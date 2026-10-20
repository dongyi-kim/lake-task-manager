"""The manual MAXIS adapter must never disguise local or interrupted execution."""
import json
import sys
from types import SimpleNamespace

import pytest

from tools import agent_maxis_eval as runner


def test_handoff_refuses_before_profile_resolution(monkeypatch):
    from tools.agent_eval_isolation import EvaluationPreflightError
    monkeypatch.delenv("LTM_EVAL_NETWORK_PREFLIGHTED", raising=False)
    monkeypatch.setattr(runner, "_pin", lambda *_: pytest.fail("profile resolved before handoff"))
    with pytest.raises(EvaluationPreflightError, match="network-authorization"):
        runner.verify_handoff("custom")


def test_actual_saved_editor_must_be_maxis(monkeypatch):
    from app.agent.maxis import backend, profiles
    monkeypatch.setattr(profiles, "active_profile_name", lambda: "custom")
    monkeypatch.setattr(backend, "thread_runtime_pin",
                        lambda **_: {"runtime_role_routes": {"editor_author": "shadow"}})
    with pytest.raises(ValueError, match="Editor"):
        runner._pin("custom")


def test_failure_on_earlier_turn_is_not_overwritten_by_success():
    assert runner.technical_failures(
        [{"ok": False, "error": "failed"}, {"ok": True, "reply": "later"}], editor=False)
    assert not runner.technical_failures(
        [{"ok": False, "needsInfo": True, "error": "대상을 알려주세요."}], editor=True)
    assert runner.technical_failures([{"ok": True, "html": ""}], editor=True)


def test_later_failure_keeps_its_actual_turn_index():
    failures = runner.technical_failures(
        [{"ok": True, "reply": "first"}, {"ok": False, "reply": "failed", "error": "contract"}],
        editor=False)
    assert len(failures) == 1 and failures[0].startswith("turn[1]")


def test_interruption_preserves_previous_turn_and_snapshot(monkeypatch, tmp_path):
    from app.agent.workflow import session
    from tools import agent_eval_isolation as isolation, agent_eval_protocol as protocol
    monkeypatch.setattr(runner, "verify_handoff", lambda *_: {})
    monkeypatch.setattr(isolation, "configure_process_isolation", lambda *_: None)
    monkeypatch.setattr(isolation, "begin_case", lambda *_a, **_kw: {})
    monkeypatch.setattr(protocol, "build_run_metadata", lambda **_: {})
    target = tmp_path / "raw.json"
    monkeypatch.setattr(protocol, "raw_result_path", lambda *_: target)
    suite = SimpleNamespace(
        SCENARIOS=[("test", ["first", "second"])], BATTERY_VERSION="1",
        PROMPT_VERSION="1", SUITE_REVIEW_ELEMENTS=[], CASE_REVIEW_SPECS={})
    monkeypatch.setitem(sys.modules, "tools.agent_lang_ab", suite)
    calls = []

    def ask(text, **_):
        calls.append(text)
        if text == "second":
            raise KeyboardInterrupt()
        return {"ok": True, "reply": "first paid response", "thread_id": "thread"}

    monkeypatch.setattr(session, "ask", ask)
    monkeypatch.setattr(session, "evaluation_snapshot", lambda _: {"sources": ["synthetic"]})
    assert runner.run(["conversation", "--profile", "custom"]) == 3
    raw = json.loads(target.read_text(encoding="utf-8"))
    turns = raw["cases"][0]["turns"]
    assert turns[0]["output"]["reply"] == "first paid response"
    assert turns[0]["output"]["evaluationEvidence"] == {"sources": ["synthetic"]}
    assert turns[1]["errorType"] == "KeyboardInterrupt"
    assert raw["cases"][0]["technicalFailures"]
    assert not raw["complete"]
