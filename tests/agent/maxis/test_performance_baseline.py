import json
from pathlib import Path
import pytest

from tools import maxis_performance_baseline as runner


def test_help_missing_command_and_unknown_option_do_not_run(monkeypatch):
    monkeypatch.setattr(runner, "diagnostics", lambda: pytest.fail("must not run"))
    for args in (["--help"], [], ["--bogus"], ["diagnose", "--live"]):
        with pytest.raises(SystemExit):
            runner.main(args)


def test_replay_rejects_paths_outside_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="inside"):
        runner.replay_editor(str(tmp_path / "private.json"))


def test_replay_does_not_copy_raw_response(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    source = tmp_path / ".cache/agent-evaluation/existing/raw.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({
        "metadata": {"battery": {"name": "maxis-editor-component", "selectedCaseIds": ["CMP2"]}},
        "cases": [{"caseId": "CMP2", "latencyMs": 10, "responseReceived": True,
                   "response": {"status": "ok", "private": "raw-secret-must-not-copy"}}],
    }))
    before = source.read_bytes()
    result = runner.replay_editor(str(source))
    assert "raw-secret-must-not-copy" not in json.dumps(result)
    assert result["measurements"]["usableCompletions"] is None
    assert source.read_bytes() == before


def test_cli_uses_reserved_new_artifact_and_records_identity(tmp_path, monkeypatch):
    target = tmp_path / ".cache/agent-evaluation/test-run/diagnostic.json"
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "_source_hashes", lambda: {"fixture": "a" * 64})
    monkeypatch.setattr(runner, "raw_result_path", lambda *args: target)
    monkeypatch.setattr(runner, "diagnostics", lambda: {"scope": "offline-test", "qualificationEligible": False})
    monkeypatch.setattr(runner, "git_snapshot", lambda: {"commit": "fixture", "dirtyTrackedFiles": True})
    assert runner.main(["--run-group", "test-run", "diagnose"]) == 0
    result = json.loads(target.read_text())
    assert result["runKind"] == "offline-diagnostic"
    assert result["diagnosticToolSnapshot"]["dirtyTrackedFiles"] is True
    assert runner.main(["--run-group", "test-run", "diagnose"]) == 2
    assert json.loads(target.read_text()) == result


def test_invalid_group_cannot_escape_cache():
    with pytest.raises(SystemExit):
        runner.main(["--run-group", "../private", "diagnose"])


def test_actual_offline_diagnostic_covers_all_cases_and_reports_observed_loss():
    result = runner.diagnostics()
    assert result["attempts"] == result["buildSuccessCount"] == 11
    overflow = next(row for row in result["cases"] if row["caseId"] == "role-overflow")
    assert "interpretation" in overflow["missingRequiredContextKeys"]
    assert overflow["requiredContextComplete"] is False
    missing = next(row for row in result["cases"] if row["caseId"] == "role-missing")
    assert missing["missingRequiredContextKeys"] == ["evidence", "knowledge_brief", "situation"]
    assert missing["requiredContextComplete"] is False
    assert result["requiredContextCompleteCount"] == 9
    assert result["runtimeLatencyAssessed"] is False
    assert result["qualificationEligible"] is False


def test_resolved_link_target_is_checked_before_any_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    lexical = tmp_path / ".cache/agent-evaluation/linked/result.json"
    external = tmp_path / "private/result.json"
    real_resolve = Path.resolve
    # Models Windows junction resolution without requiring symlink privileges.
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **kw:
                        external if self == lexical else real_resolve(self, *a, **kw))
    monkeypatch.setattr(runner, "raw_result_path", lambda *args: lexical)
    monkeypatch.setattr(runner, "reserve_raw_result_path", lambda *args: pytest.fail("must not reserve"))
    assert runner.main(["diagnose"]) == 2
    with pytest.raises(ValueError, match="inside"):
        runner.replay_editor(str(lexical))


@pytest.mark.parametrize("code,expected", [
    ("invalid_payload_schema", "invalid_payload_schema"),
    ("context_too_large", "context_too_large"),
    ("private-secret-error", "guarded_input_rejected"),
])
def test_guard_failures_retain_all_attempts_and_only_constant_codes(monkeypatch, code, expected):
    from app.agent.maxis.context_guard import ContextGuardError
    from tools import maxis_role_eval_cases as fixtures
    build = fixtures.build_fixture_payload

    def fail_one(case_id):
        if case_id == "role-work":
            raise ContextGuardError(code, "private-path-data")
        return build(case_id)

    monkeypatch.setattr(fixtures, "build_fixture_payload", fail_one)
    result = runner.diagnostics()
    assert result["attempts"] == 11 and result["buildSuccessCount"] == 10
    failed = next(row for row in result["cases"] if row["caseId"] == "role-work")
    assert failed["errorCode"] == expected
    assert "private" not in json.dumps(result)


def test_cli_failure_is_retained_without_reusing_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    target = tmp_path / ".cache/agent-evaluation/failed/result.json"
    monkeypatch.setattr(runner, "raw_result_path", lambda *args: target)
    monkeypatch.setattr(runner, "diagnostics", lambda: (_ for _ in ()).throw(ValueError("private details")))
    assert runner.main(["diagnose"]) == 2
    failure = json.loads(target.read_text())
    assert failure["complete"] is False
    assert "private" not in json.dumps(failure)
    assert runner.main(["diagnose"]) == 2
    assert json.loads(target.read_text()) == failure
