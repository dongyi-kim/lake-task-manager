"""Backend benchmark records real transport and refuses ambiguous/reused artifacts."""
from pathlib import Path
import json

import pytest

from tools import jql_cache_benchmark as benchmark


REPO = Path(__file__).resolve().parents[3]


def test_output_reservation_rejects_repository_paths_and_existing_attempts(tmp_path):
    with pytest.raises(ValueError):
        benchmark.reserve_attempt(REPO, REPO / ".cache" / "forbidden-benchmark")
    target = tmp_path / "attempt"
    benchmark.reserve_attempt(REPO, target)
    (target / "prior.txt").write_text("preserved", encoding="utf-8")
    with pytest.raises(FileExistsError):
        benchmark.reserve_attempt(REPO, target)
    assert (target / "prior.txt").read_text(encoding="utf-8") == "preserved"


def test_identity_records_actual_commit_dirty_tree_and_dependency_provenance():
    identity = benchmark.repository_identity(REPO)
    assert len(identity["commit"]) == 40
    assert isinstance(identity["dirty"], bool)
    assert len(identity["workingTreeSha256"]) == 64
    assert identity["checkout"] == str(REPO)
    assert "requirements.txt" in identity["requirementsSha256"]


def test_backend_smoke_preserves_priming_cost_parity_and_unknown_model_usage(tmp_path):
    target = tmp_path / "raw"
    result = benchmark.run(REPO, 0, 1, 1, arm="F+A+B", output_dir=target,
                           dependency_label="test-pinned-overlay", journeys=["bulk"])
    assert result["runKind"] == "single-revision-backend"
    assert result["arm"] == "F+A+B" and result["world"]["counts"]["total"] == 10000
    assert result["modelUsage"] is None and result["mcpCalls"] == 0
    assert result["qualificationEligible"] is False
    assert result["summary"]["allCorrect"] is True
    records = [json.loads(line) for line in (target / "trials.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1 and records[0]["journey"] == "bulk"
    phases = records[0]["phases"]
    assert phases[0]["cacheState"] == "empty" and phases[1]["cacheState"] == "valid"
    assert phases[0]["result"]["total"] == phases[1]["result"]["total"] == 2245
    assert phases[0]["jiraRestAttempts"] >= 23
    assert phases[1]["jiraRestAttempts"] == 0
    assert records[0]["jiraRestAttempts"] == sum(phase["jiraRestAttempts"] for phase in phases)
    assert records[0]["backgroundSettlement"]["complete"] is True
    assert records[0]["worldUnchanged"] is True
    assert not Path(result["scratchPath"]).exists()
    assert (target / "result.json").exists()


def test_raw_failure_remains_reviewable_when_a_journey_raises(tmp_path):
    from tools import jql_benchmark_runtime as runtime
    target = tmp_path / "failure"
    benchmark.reserve_attempt(REPO, target)
    with pytest.raises(RuntimeError, match="injected"):
        with runtime.RawAttempt(target, {"arm": "F"}) as attempt:
            attempt.record({"journey": "bulk", "ok": False, "jiraRestAttempts": 2})
            raise RuntimeError("injected")
    assert json.loads((target / "manifest.json").read_text())["arm"] == "F"
    assert json.loads((target / "result.json").read_text())["error"]["type"] == "RuntimeError"
    assert json.loads((target / "trials.jsonl").read_text())["jiraRestAttempts"] == 2


def test_preflight_world_failure_is_retained_before_any_business_request(tmp_path, monkeypatch):
    from tools.codex_mcp_eval import etl
    def fail(*_args, **_kwargs):
        raise RuntimeError("world preflight failed")
    monkeypatch.setattr(etl, "build_etl_world", fail)
    target = tmp_path / "preflight"
    with pytest.raises(RuntimeError, match="world preflight"):
        benchmark.run(REPO, 0, 1, 0, arm="F", output_dir=target,
                      dependency_label="test-overlay", journeys=["bulk"])
    assert json.loads((target / "manifest.json").read_text())["arm"] == "F"
    assert json.loads((target / "result.json").read_text())["error"]["type"] == "RuntimeError"
    assert (target / "trials.jsonl").read_text() == ""


@pytest.fixture(scope="module")
def year_bundle():
    from tools.codex_mcp_eval.etl import build_etl_world
    return build_etl_world()


@pytest.mark.parametrize("journey", ["people", "whole_split", "projection", "navigation",
                                     "age_invalidation", "scheduling", "restart"])
def test_journey_contract_runs_on_real_year_transport_without_mcp(tmp_path, year_bundle, journey):
    from tools.jql_benchmark_runtime import BackendTrial
    from tools.jql_benchmark_journeys import run_common
    trial = BackendTrial(tmp_path, year_bundle)
    with trial:
        run_common(trial, journey, 1)
    record = trial.result(journey, 0)
    assert record["ok"] is True, [(phase["name"], phase.get("error")) for phase in record["phases"]]
    assert record["worldUnchanged"] is True
    assert record["mcpCalls"] == 0 and record["modelUsage"] is None
    if journey == "restart":
        assert record["failedDispatches"] == 1
        assert record["phases"][0]["upstreamCalls"][-1]["status"] == 503
        assert record["phases"][-1]["jiraRestAttempts"] == 0
