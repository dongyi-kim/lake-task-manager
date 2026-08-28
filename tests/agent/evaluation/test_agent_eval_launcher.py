"""Manual real-provider batteries require an explicit, network-authorized launcher."""

from __future__ import annotations

import ast
import os

import pytest

from tools import agent_eval_launcher as L
from tools.agent_eval_isolation import (
    EFFECTIVE_PROVIDER_IDENTITY_ENV,
    EvaluationPreflightError,
    NETWORK_PREFLIGHT_MARKER,
)
from tools.agent_scenario_eval import parse_scenario_args, validate_eval_argv
from tools import agent_scenario_eval as scenario


@pytest.fixture(autouse=True)
def clean_handoff_marker(monkeypatch):
    monkeypatch.delenv(NETWORK_PREFLIGHT_MARKER, raising=False)
    monkeypatch.delenv(EFFECTIVE_PROVIDER_IDENTITY_ENV, raising=False)
    monkeypatch.delenv("LTM_AGENT_STRUCTURED_OUTPUT_FALLBACK", raising=False)
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai")
    monkeypatch.setenv("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini")


@pytest.mark.parametrize(
    "argv",
    (["--help"], ["-h"], ["--unknown"], ["--out"], ["--out="], ["--out", "--bad"]),
)
def test_direct_runner_control_options_fail_closed_before_execution(argv):
    with pytest.raises(SystemExit, match="evaluation not started|unsupported evaluation option"):
        validate_eval_argv(argv)


def test_documented_output_option_still_parses():
    assert parse_scenario_args(
        ["gpt-4o", "MTG1", "--out", ".cache/result.json"],
    ) == ("gpt-4o", {"MTG1"}, ".cache/result.json")


def test_launcher_help_and_unknown_runner_are_network_free(monkeypatch, capsys):
    monkeypatch.setattr(
        L, "_authorize_network",
        lambda *_args: (_ for _ in ()).throw(AssertionError("network must not be touched")),
    )
    assert L.main(["--help"]) == 0
    assert "usage:" in capsys.readouterr().out
    assert L.main(["does-not-exist"]) == 2
    assert "unknown evaluation runner" in capsys.readouterr().err


def test_launcher_sets_handoff_only_after_endpoint_preflight(monkeypatch):
    events = []

    def authorize(model, simple_model, timeout):
        assert NETWORK_PREFLIGHT_MARKER not in os.environ
        events.append(("authorize", model, simple_model, timeout))
        return "a" * 64

    def run(script, argv):
        assert os.environ[NETWORK_PREFLIGHT_MARKER] == "1"
        assert os.environ["LTM_AGENT_STRUCTURED_OUTPUT_FALLBACK"] == "forbid"
        assert os.environ[EFFECTIVE_PROVIDER_IDENTITY_ENV] == "a" * 64
        events.append(("run", script, list(argv)))
        return 17

    monkeypatch.setattr(L, "_authorize_network", authorize)
    monkeypatch.setattr(L, "_run_runner", run)
    assert L.main(["meeting", "gpt-4o", "MTG1"]) == 17
    assert events == [
        ("authorize", "gpt-4o", "gpt-4o-mini", 10.0),
        ("run", "agent_meeting_eval.py", ["gpt-4o", "MTG1"]),
    ]
    assert NETWORK_PREFLIGHT_MARKER not in os.environ
    assert EFFECTIVE_PROVIDER_IDENTITY_ENV not in os.environ
    assert "LTM_AGENT_STRUCTURED_OUTPUT_FALLBACK" not in os.environ


def test_failed_launcher_preflight_never_sets_marker_or_starts_runner(monkeypatch, capsys):
    monkeypatch.setattr(
        L, "_authorize_network",
        lambda *_args: (_ for _ in ()).throw(
            EvaluationPreflightError("connection", "openai_compat")
        ),
    )
    monkeypatch.setattr(
        L, "_run_runner",
        lambda *_args: (_ for _ in ()).throw(AssertionError("runner must not start")),
    )
    assert L.main(["create", "gpt-4o-mini", "STARR1"]) == 3
    assert NETWORK_PREFLIGHT_MARKER not in os.environ
    assert EFFECTIVE_PROVIDER_IDENTITY_ENV not in os.environ
    assert "LTM_AGENT_STRUCTURED_OUTPUT_FALLBACK" not in os.environ
    assert "no cases started" in capsys.readouterr().err


def test_all_modern_runner_headers_point_to_authorized_launcher():
    for script in L.RUNNERS.values():
        text = (L.ROOT / "tools" / script[0]).read_text(encoding="utf-8")
        assert "agent_eval_launcher.py" in text, script[0]
        assert "validate_eval_argv(" in text or "parse_scenario_args(" in text, script[0]


def test_user_review_is_an_authorized_versioned_capture_not_an_llm_judge(monkeypatch):
    definition = L.RUNNERS.get("user-review")
    assert definition == ("agent_user_review.py", "gpt-4o-mini")

    path = L.ROOT / "tools" / "agent_user_review.py"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported_modules = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name.startswith("app.agent") for name in imported_modules)
    assert "run_scenario_suite" in text
    assert "build_run_metadata" not in text  # shared runner owns the metadata authority
    assert "get_llm(" not in text
    assert "with_structured_output" not in text
    assert "research/agent-improvement/reports" not in text
    assert "direct-raw-output-review" in text
    assert not (L.ROOT / "tools" / "agent_quality_read.py").exists()

    from tools import agent_user_review as user_review

    case_ids = {case[0] for case in user_review.CASES}
    assert case_ids == {f"F{index}" for index in range(1, 9)}
    assert case_ids == set(user_review.CASE_REVIEW_SPECS)
    assert user_review.BATTERY_VERSION == "1.0.0"

    captured = {}
    monkeypatch.setattr(
        user_review,
        "run_scenario_suite",
        lambda **kwargs: captured.update(kwargs),
    )
    assert user_review.main(["F3"]) == 0
    assert captured["suite"] == "user-review"
    assert captured["selected"] == {"F3"}
    assert captured["battery_version"] == "1.0.0"


def test_perf_is_an_authorized_versioned_streaming_suite(monkeypatch):
    assert L.RUNNERS.get("perf") == ("agent_perf.py", "gpt-4o-mini")
    text = (L.ROOT / "tools" / "agent_perf.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    top_level_imports = {
        node.module or ""
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    }
    assert "run_scenario_suite" in text
    assert "streaming=True" in text
    assert not any(name.startswith("app.agent") for name in top_level_imports)
    assert "session.stream" not in text

    from tools import agent_perf as perf

    assert {case[0] for case in perf.CASES} == set(perf.CASE_REVIEW_SPECS)
    captured = {}
    monkeypatch.setattr(perf, "run_scenario_suite", lambda **kwargs: captured.update(kwargs))
    assert perf.main(["P4"]) == 0
    assert captured["suite"] == "perf"
    assert captured["selected"] == {"P4"}
    assert captured["streaming"] is True


def test_shared_streaming_turn_captures_ttft_and_normalizes_final_event(monkeypatch):
    class FakeSession:
        @staticmethod
        def stream(_prompt, *, thread_id):
            assert thread_id == "prior-thread"
            yield {"type": "start", "thread_id": "current-thread"}
            yield {"type": "token", "text": "첫"}
            yield {
                "type": "final",
                "thread_id": "current-thread",
                "ok": True,
                "reply": "완료",
                "usage": {"calls": 1, "totalTokens": 12},
            }

    ticks = iter((10.0, 10.25))
    monkeypatch.setattr(scenario.time, "perf_counter", lambda: next(ticks))
    output, thread_id, ttft = scenario.invoke_scenario_turn(
        FakeSession(), "질문", "prior-thread", streaming=True,
    )
    assert output == {
        "thread_id": "current-thread",
        "ok": True,
        "reply": "완료",
        "usage": {"calls": 1, "totalTokens": 12},
    }
    assert thread_id == "current-thread"
    assert ttft == 0.25


def test_shared_streaming_turn_requires_a_final_event():
    class IncompleteSession:
        @staticmethod
        def stream(_prompt, *, thread_id):
            yield {"type": "start", "thread_id": thread_id}
            yield {"type": "token", "text": "중간"}

    with pytest.raises(RuntimeError, match="without a final event"):
        scenario.invoke_scenario_turn(IncompleteSession(), "질문", "thread", streaming=True)
