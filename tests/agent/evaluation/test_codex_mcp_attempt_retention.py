"""Post-model infrastructure faults must retain already observed paid attempts."""
import json
import threading
from types import SimpleNamespace

import pytest

from tools.agent_eval_isolation import NETWORK_PREFLIGHT_MARKER
from tools.codex_mcp_eval import cli, runner


def paid():
    return {"ok": True, "reply": "retained answer", "durationSeconds": 2,
            "usage": {"promptTokens": 100, "completionTokens": 20, "totalTokens": 120}, "events": []}


class Fixture:
    catalog_sha256, url, token = "catalog", "http://fixture/mcp", "secret"
    settings = SimpleNamespace(search_jira_projects=["DL"], search_confluence_spaces=["DL"])
    def __init__(self, *args, close_error=False, **kwargs):
        self.close_error = close_error
        self.tool_calls = [{"name": "get_ticket", "isError": False}]
        self.upstream_calls = [{"service": "jira", "status": 200, "durationSeconds": 0.1, "responseBytes": 10}]
        self.protocol_calls = [{"server": "ltm", "method": "tools/call", "id": 1}]
    def __enter__(self):
        return self
    def __exit__(self, *_):
        if self.close_error:
            raise RuntimeError("fixture close failed")
    def ground_truth(self):
        return {"source": "private"}
    def summary(self):
        return {"worldUnchanged": True, "providerStoreUnchanged": True}
    def assert_conformance(self):
        return {"cacheWarmed": False}


@pytest.mark.parametrize("fault", ["checker", "oracle-write", "events-write", "close"])
def test_post_model_failure_keeps_output_costs_and_checkpoint(tmp_path, monkeypatch, fault):
    monkeypatch.setenv(NETWORK_PREFLIGHT_MARKER, "1")
    original_write = runner.write_raw_result
    def write(path, payload):
        if str(path).endswith({"oracle-write": "oracle.json", "events-write": "events.json"}.get(fault, "never")):
            raise ValueError("artifact serialization failed")
        return original_write(path, payload)
    monkeypatch.setattr(runner, "write_raw_result", write)
    def check(*args):
        if fault == "checker":
            raise RuntimeError("checker failed")
        return {"pass": True, "failures": []}
    options = SimpleNamespace(date="2026-11-10", model="stub", effort="high", timeout=30)
    battery = SimpleNamespace(SYSTEM_CONTEXT="", OUTPUT_SCHEMA={}, check=check)
    case = {"id": "retention", "prompts": ["request"]}
    record = runner._run_case(case, options, "stub", tmp_path, "catalog", lambda *a, **k: paid(),
        lambda *a, **k: Fixture(close_error=fault == "close"), battery, artifact_root=tmp_path)
    assert not record["automaticPass"] and record["infrastructureError"]
    assert record["output"]["usage"]["totalTokens"] == 120
    assert record["output"]["reply"] == "retained answer"
    assert record["durationSeconds"] == 2
    assert len(record["upstreamCalls"]) == len(record["protocolCalls"]) == 1
    checkpoint = json.loads((tmp_path / "retention" / "model-attempt.json").read_text(encoding="utf-8"))
    assert checkpoint["output"]["usage"]["totalTokens"] == 120
    assert len(checkpoint["upstreamCalls"]) == 1


def test_recovery_second_phase_setup_failure_keeps_first_phase(tmp_path, monkeypatch):
    from tools.codex_mcp_eval import recovery_runner
    monkeypatch.setenv(NETWORK_PREFLIGHT_MARKER, "1")
    def phase(directory, index, options, binary, cli_run, files_root, controller, revision, fixture_factory):
        if index:
            raise RuntimeError("second fixture could not start")
        controller._trigger("test boundary")
        return {"output": {**paid(), "intentionalInterruption": True},
                "mcpToolCalls": [{"name": "search_issues"}], "fileCalls": [], "skillReads": [],
                "upstreamCalls": [{"service": "jira"}], "protocolCalls": [], "persistedCalls": [],
                "isolation": {"worldUnchanged": True, "providerStoreUnchanged": True}}, {}
    monkeypatch.setattr(recovery_runner, "_phase", phase)
    options = SimpleNamespace(recovery_mode="restarted", recovery_scenario="between-pages")
    battery = SimpleNamespace(OUTPUT_SCHEMA={})
    record = recovery_runner.run_recovery_case({"id": "WORK-R02-boundary"}, options, "stub", tmp_path,
                                               None, battery, artifact_root=tmp_path)
    assert record["infrastructureError"] and not record["automaticPass"]
    assert record["phases"][0]["output"]["usage"]["totalTokens"] == 120
    assert len(record["upstreamCalls"]) == 1
    metrics, _ = runner.model_metrics([record])
    assert metrics["totalTokens"] == 120


def test_same_fixture_phase_snapshots_use_offsets_and_do_not_mutate(tmp_path):
    from tools.codex_mcp_eval.attempts import snapshot_attempt, combine_phase_attempts
    fixture = Fixture()
    first = {"modelAttempted": True}
    snapshot_attempt(first, fixture, paid())
    fixture.tool_calls.append({"name": "get_comments"})
    fixture.upstream_calls.append({"service": "jira", "status": 500})
    second = {"modelAttempted": True}
    snapshot_attempt(second, fixture, paid(), offsets={"tool_calls": 1, "upstream_calls": 1, "protocol_calls": 1})
    record = {}
    combine_phase_attempts(record, [first, second])
    assert [call["name"] for call in record["output"]["mcpToolCalls"]] == ["get_ticket", "get_comments"]
    assert len(record["upstreamCalls"]) == 2
    assert record["output"]["usage"]["totalTokens"] == 240
    fixture.upstream_calls.clear()
    assert len(first["upstreamCalls"]) == 1


def test_launch_retains_case_costs_when_primary_result_writer_fails(tmp_path, monkeypatch):
    original_write = runner.write_raw_result
    def write(path, payload):
        if path.name.startswith("codex-mcp-") and "persistence-failure" not in path.name:
            raise ValueError("primary result serialization failed")
        return original_write(path, payload)
    monkeypatch.setattr(runner, "write_raw_result", write)
    status = runner.launch(["--run-group", "retained", "--date", "2026-11-10", "--cases", "MCP-J1",
                            "--codex", "stub", "--output-root", str(tmp_path)],
        cli_run=lambda *args, **kwargs: paid(), fixture_factory=Fixture,
        identity_fn=lambda *args, **kwargs: {"runtime": "test", "version": "1"})
    assert status == 1
    result = json.loads(next((tmp_path / "retained").glob("*-persistence-failure.json")).read_text(encoding="utf-8"))
    assert result["infrastructureError"] == "result_write_failed"
    assert result["metrics"]["totalTokens"] == 120
    assert result["cumulativeModelMetrics"]["totalTokens"] == 240
    assert result["summary"]["jiraApi"]["requests"] == 1


def test_cli_schema_processing_failure_keeps_completed_usage(tmp_path):
    events = [{"type": "item.completed", "item": {"type": "agent_message", "text": '{"reply":"answer"}'}},
              {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}]
    result = cli.run("stub", "request", tmp_path / "workspace", schema={"type": "invalid"},
        process_run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="\n".join(map(json.dumps, events)), stderr=""))
    assert not result["ok"] and result["error"] == "codex_postprocess_failed"
    assert result["usage"]["totalTokens"] == 120
    assert result["events"]


@pytest.mark.parametrize("fault", ["artifact", "close"])
def test_recovery_paid_second_phase_failure_retains_both_phases(tmp_path, monkeypatch, fault):
    from tools.codex_mcp_eval import recovery_runner
    monkeypatch.setenv(NETWORK_PREFLIGHT_MARKER, "1")
    original_write = recovery_runner.write_raw_result
    def write(path, payload):
        if fault == "artifact" and path.name == "phase-1-oracle.json":
            raise ValueError("serialization failure")
        return original_write(path, payload)
    monkeypatch.setattr(recovery_runner, "write_raw_result", write)
    class RecoveryFixture(Fixture):
        files_url, skill_reads = "http://fixture/files", []
        def __init__(self, path, *args, controller=None, **kwargs):
            super().__init__(close_error=fault == "close" and path.name == "fixture-1")
            self.files = SimpleNamespace(calls=[], list_files=lambda: {"files": []})
            if controller:
                controller._trigger("test interruption")
        def ground_truth(self):
            return {"cases": {"WORK-R02-boundary": {"keys": []}}}
    options = SimpleNamespace(recovery_mode="restarted", recovery_scenario="between-pages", date="2026-11-10",
                              model="stub", effort="high", timeout=30, skill_transport="default")
    record = recovery_runner.run_recovery_case({"id": "WORK-R02-boundary"}, options, "stub", tmp_path,
        lambda *a, **k: {**paid(), "intentionalInterruption": bool(k.get("stop_event"))},
        SimpleNamespace(OUTPUT_SCHEMA={}), artifact_root=tmp_path, fixture_factory=RecoveryFixture)
    assert record["infrastructureError"] and not record["automaticPass"]
    assert len(record["phases"]) == len(record["upstreamCalls"]) == len(record["protocolCalls"]) == 2
    assert record["output"]["usage"]["totalTokens"] == 240
    assert runner.model_metrics([record])[0]["totalTokens"] == 240
    checkpoint = json.loads((tmp_path / "WORK-R02-boundary" / "phase-1-attempt.json").read_text(encoding="utf-8"))
    assert checkpoint["output"]["usage"]["totalTokens"] == 120


def test_total_write_failure_exposes_paid_payload_to_caller(tmp_path):
    from tools.codex_mcp_eval.attempts import persist_result, ResultPersistenceError
    payload = {"output": paid()}
    def unwritable(*args):
        raise OSError("disk unavailable")
    with pytest.raises(ResultPersistenceError) as error:
        persist_result(tmp_path / "result.json", payload, writer=unwritable)
    assert error.value.payload["output"]["usage"]["totalTokens"] == 120
    assert error.value.payload["infrastructureError"] == "result_write_failed"


class TailFixture(Fixture):
    """An admitted handler finishes after its cancelled model process returns."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tool_calls, self.upstream_calls, self.protocol_calls = [], [], []
        self.release = threading.Event()
        def pending():
            self.release.wait()
            self.tool_calls.append({"name": "get_ticket", "isError": False})
            self.upstream_calls.append({"service": "jira", "status": 200, "responseBytes": 10,
                                        "durationSeconds": 0.1})
            self.protocol_calls.append({"server": "ltm", "method": "tools/call", "id": 1})
        self.worker = threading.Thread(target=pending)
        self.worker.start()
    def __exit__(self, *_):
        self.release.set()
        self.worker.join(timeout=2)
        assert not self.worker.is_alive()
    def settle_observers(self, timeout_seconds=5):
        return {"complete": not self.worker.is_alive(), "timedOut": self.worker.is_alive(),
                "active": {"handler": int(self.worker.is_alive())}, "waitSeconds": 0}


def test_cancelled_model_merges_teardown_tail_and_preserves_early_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv(NETWORK_PREFLIGHT_MARKER, "1")
    options = SimpleNamespace(date="2026-11-10", model="stub", effort="high", timeout=30)
    battery = SimpleNamespace(SYSTEM_CONTEXT="", OUTPUT_SCHEMA={},
                              check=lambda *a: {"pass": False, "failures": ["model_timeout"]})
    record = runner._run_case({"id": "tail", "prompts": ["request"]}, options, "stub", tmp_path, "catalog",
        lambda *a, **k: {**paid(), "ok": False, "error": "codex_timeout"}, TailFixture, battery, artifact_root=tmp_path)
    assert len(record["upstreamCalls"]) == len(record["protocolCalls"]) == 1
    assert record["settlement"]["complete"] is True
    early = json.loads((tmp_path / "tail" / "model-attempt.json").read_text(encoding="utf-8"))
    assert early["upstreamCalls"] == []
    final = json.loads((tmp_path / "tail" / "model-attempt-final.json").read_text(encoding="utf-8"))
    assert len(final["upstreamCalls"]) == 1


def test_recovery_phase_merges_paid_tail_after_teardown(tmp_path):
    from tools.codex_mcp_eval import recovery_runner
    class RecoveryTail(TailFixture):
        files_url, skill_reads = "http://fixture/files", []
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.files = SimpleNamespace(calls=[], list_files=lambda: {"files": []})
        def ground_truth(self):
            return {"cases": {"WORK-R02-boundary": {"keys": []}}}
    options = SimpleNamespace(date="2026-11-10", model="stub", effort="high", timeout=30,
                              skill_transport="default", work_output_schema={})
    record, _ = recovery_runner._phase(tmp_path, 0, options, "stub",
        lambda *a, **k: {**paid(), "ok": False, "error": "codex_timeout"}, tmp_path, None, 1, RecoveryTail)
    assert len(record["upstreamCalls"]) == len(record["protocolCalls"]) == 1
    assert record["settlement"]["complete"] is True
