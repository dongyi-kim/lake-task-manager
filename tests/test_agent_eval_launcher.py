"""Manual real-provider batteries require an explicit, network-authorized launcher."""

from __future__ import annotations

import os

import pytest

from tools import agent_eval_launcher as L
from tools.agent_eval_isolation import (
    EFFECTIVE_PROVIDER_IDENTITY_ENV,
    EvaluationPreflightError,
    NETWORK_PREFLIGHT_MARKER,
)
from tools.agent_scenario_eval import parse_scenario_args, validate_eval_argv


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
