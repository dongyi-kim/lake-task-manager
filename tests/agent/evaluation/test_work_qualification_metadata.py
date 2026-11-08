"""Qualification must compare fixed batteries, not change identity per repeat/arm."""
import json

import pytest
from support.evaluation_tmp import external_tmp_path as tmp_path

from tools.codex_mcp_eval import cli, runner


def test_experiment_defaults_use_requested_sol_high():
    options = runner.parse_args([])
    assert (options.model, options.effort) == ("gpt-6.1-sol", "high")
    assert (cli.MODEL, cli.EFFORT) == ("gpt-6.1-sol", "high")


def test_exploratory_retry_index_remains_supported_without_new_design_flags():
    options = runner.parse_args(["--repeat-index", "2"])
    assert options.repeat_index == options.repetitions == 2
    assert options.run_kind == "exploratory"


@pytest.mark.parametrize("extra", [
    ["--run-kind", "qualification"],
    ["--run-kind", "qualification", "--repetitions", "5", "--candidate-order-index", "1", "--cases", "WORK-R01-normal"],
    ["--repetitions", "5", "--repeat-index", "6"],
    ["--repetitions", "0"],
    ["--candidate-order-index", "0"],
])
def test_invalid_qualification_design_is_rejected_before_model_start(extra):
    with pytest.raises(SystemExit):
        runner.parse_args(["--battery", "work", *extra])


def test_ab_and_repeats_share_benchmark_but_retain_candidate_identity(tmp_path, monkeypatch):
    from tools import agent_eval_protocol as protocol

    monkeypatch.setattr(protocol, "git_snapshot", lambda: {
        "commit": "a" * 40, "gitStatusAvailable": True, "dirtyTrackedFiles": [],
        "dirtyUntrackedFiles": False, "candidateTreeSha256": "b" * 64})

    from tools.codex_mcp_eval.work_fixture import WorkFixture

    def fixture(path, **kwargs):
        return WorkFixture(path, **{**kwargs, "port": 0})

    def record(case, *args, **kwargs):
        return {"id": case["id"], "durationSeconds": 1, "automaticPass": True,
                "output": {"usage": {}, "mcpToolCalls": []}}

    monkeypatch.setattr(runner, "_run_case", record)
    metadata = []
    for condition, repeat, order in [("baseline", 1, 1), ("candidate", 1, 2), ("candidate", 2, 1)]:
        group = f"{condition}-{repeat}"
        args = ["--battery", "work", "--date", "2026-11-09", "--skill-condition", condition,
                "--run-group", group, "--output-root", str(tmp_path), "--codex", "stub",
                "--run-kind", "qualification", "--repetitions", "5", "--repeat-index", str(repeat),
                "--candidate-order-index", str(order)]
        assert runner.launch(args, cli_run=lambda *a, **k: {"ok": True, "reply": "PREFLIGHT"},
                             fixture_factory=fixture,
                             identity_fn=lambda *a, **k: {"runtime": "test", "version": "1"}) == 0
        payload = json.loads(next((tmp_path / group).glob("*.json")).read_text(encoding="utf-8"))
        metadata.append(payload["evaluation"])
    assert len({row["benchmarkKey"] for row in metadata}) == 1
    assert metadata[0]["candidateKey"] != metadata[1]["candidateKey"]
    assert metadata[1]["candidateKey"] == metadata[2]["candidateKey"]
    assert all(row["qualificationEligible"] for row in metadata)
    assert [row["run"]["repeatIndex"] for row in metadata] == [1, 1, 2]
    assert [row["run"]["candidateOrderIndex"] for row in metadata] == ["1", "2", "1"]
    assert all(row["run"]["repetitions"] == 5 for row in metadata)
    assert metadata[1]["run"]["externalRuntime"]["skillCondition"] == "candidate"

    recovery_metadata = []
    for mode in ("uninterrupted", "restarted"):
        args = ["--battery", "work", "--date", "2026-11-09", "--skill-condition", "candidate",
                "--run-group", "recovery-" + mode, "--output-root", str(tmp_path), "--codex", "stub",
                "--run-kind", "qualification", "--repetitions", "5", "--candidate-order-index", "1",
                "--recovery-scenario", "between-pages", "--recovery-mode", mode]
        assert runner.launch(args, cli_run=lambda *a, **k: {"ok": True, "reply": "PREFLIGHT"},
                             fixture_factory=fixture,
                             identity_fn=lambda *a, **k: {"runtime": "test", "version": "1"}) == 0
        payload = json.loads(next((tmp_path / ("recovery-" + mode)).glob("*.json")).read_text(encoding="utf-8"))
        recovery_metadata.append(payload["evaluation"])
    assert recovery_metadata[0]["benchmarkKey"] == recovery_metadata[1]["benchmarkKey"]
    assert recovery_metadata[0]["candidateKey"] != recovery_metadata[1]["candidateKey"]
    assert recovery_metadata[0]["run"]["externalContext"]["modelInvocationTimeoutSeconds"] == 600
    assert recovery_metadata[0]["run"]["externalContext"]["preflightTimeoutSeconds"] == 90

    monkeypatch.setattr(protocol, "git_snapshot", lambda: {
        "commit": "a" * 40, "gitStatusAvailable": True, "dirtyTrackedFiles": ["changed.py"],
        "dirtyUntrackedFiles": False, "candidateTreeSha256": "b" * 64})
    with pytest.raises(RuntimeError, match="tracked working-tree changes"):
        runner.launch(args, cli_run=lambda *a, **k: pytest.fail("Invalid qualification must not spend model calls"),
                      fixture_factory=fixture,
                      identity_fn=lambda *a, **k: {"runtime": "test", "version": "1"})
