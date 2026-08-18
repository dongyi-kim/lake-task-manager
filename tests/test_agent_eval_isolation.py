"""Manual batteries must not share cache, conversation state, or mutable mock data."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools import agent_eval_isolation as I
from tools import agent_eval_protocol as E


ROOT = Path(__file__).resolve().parents[1]
_ENV_KEYS = (
    "CACHE_DB_PATH", "LTM_EVAL_CACHE_POLICY", "LTM_EVAL_PROCESS_ISOLATION",
    "LTM_EVAL_CACHE_DB_PATH", "LTM_EVAL_PREFLIGHT_TIMEOUT", "LAKE_AGENT_PROVIDER",
    "LTM_EVAL_NETWORK_PREFLIGHTED",
)


@pytest.fixture(autouse=True)
def restore_evaluation_environment():
    before = {key: os.environ.get(key) for key in _ENV_KEYS}
    # Isolation mechanics are deterministic unit tests. Real-provider behavior is exercised
    # below with an injected, network-free preflight probe.
    os.environ["LAKE_AGENT_PROVIDER"] = "fake"
    I._PREFLIGHT.update(suite="", identity="", passed=False)
    yield
    I._PREFLIGHT.update(suite="", identity="", passed=False)
    for key, value in before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.mark.parametrize("provider", ("openai", "openai_compat", "aoai"))
def test_real_provider_preflight_runs_once_before_all_cases(provider, monkeypatch):
    calls = []
    monkeypatch.setenv("LTM_EVAL_NETWORK_PREFLIGHTED", "1")
    monkeypatch.setattr(I, "_provider_context", lambda: (provider, "route-a"))
    I.configure_process_isolation("preflight-once")

    def probe(actual_provider, timeout):
        calls.append((actual_provider, timeout))

    assert I.preflight_evaluation_provider(probe=probe) == {
        "provider": provider, "status": "passed",
    }
    assert I.preflight_evaluation_provider(probe=probe) == {
        "provider": provider, "status": "cached",
    }
    assert calls == [(provider, 10.0)]


def test_fake_provider_skips_preflight_without_calling_a_probe(monkeypatch):
    monkeypatch.setattr(I, "_provider_context", lambda: ("fake", "fake"))
    I.configure_process_isolation("fake-suite")

    def unexpected_probe(*_args):
        raise AssertionError("fake provider must remain network-free")

    assert I.preflight_evaluation_provider(probe=unexpected_probe) == {
        "provider": "fake", "status": "skipped",
    }


@pytest.mark.parametrize(
    ("failure", "category"),
    (
        (ConnectionError("https://192.0.2.1 secret-token refused"), "connection"),
        (PermissionError("401 invalid api key secret-token"), "auth"),
        (TimeoutError("https://192.0.2.1 timed out secret-token"), "timeout"),
        (RuntimeError(""), "empty"),
    ),
)
def test_real_provider_preflight_is_fail_closed_and_redacted(failure, category, monkeypatch):
    monkeypatch.setenv("LTM_EVAL_NETWORK_PREFLIGHTED", "1")
    monkeypatch.setattr(I, "_provider_context", lambda: ("openai_compat", "route-a"))
    I.configure_process_isolation("failed-preflight")

    def fail(*_args):
        raise failure

    with pytest.raises(I.EvaluationPreflightError) as caught:
        I.preflight_evaluation_provider(probe=fail)
    message = str(caught.value)
    assert f"category={category}" in message
    assert "no cases started" in message
    assert "192.0.2.1" not in message
    assert "secret-token" not in message


def test_reconfiguring_or_changing_route_requires_a_new_preflight(monkeypatch):
    monkeypatch.setenv("LTM_EVAL_NETWORK_PREFLIGHTED", "1")
    route = {"identity": "route-a"}
    calls = []
    monkeypatch.setattr(
        I, "_provider_context", lambda: ("openai", route["identity"]),
    )
    I.configure_process_isolation("first")
    probe = lambda provider, timeout: calls.append((provider, timeout))
    I.preflight_evaluation_provider(probe=probe)
    route["identity"] = "route-b"
    I.preflight_evaluation_provider(probe=probe)
    I.configure_process_isolation("second")
    I.preflight_evaluation_provider(probe=probe)
    assert len(calls) == 3


def test_begin_case_cannot_enter_graph_when_preflight_fails(monkeypatch):
    I.configure_process_isolation("direct-run")

    def blocked():
        raise I.EvaluationPreflightError("connection", "openai_compat")

    monkeypatch.setattr(I, "preflight_evaluation_provider", blocked)
    with pytest.raises(I.EvaluationPreflightError, match="no cases started"):
        I.begin_case("must-not-start")


def test_direct_real_runner_without_launcher_marker_opens_no_socket(monkeypatch):
    calls = []
    monkeypatch.setattr(I, "_provider_context", lambda: ("openai_compat", "route-a"))
    monkeypatch.setattr(
        I, "_probe_real_provider", lambda *_args: calls.append("network"),
    )
    monkeypatch.delenv("LTM_EVAL_NETWORK_PREFLIGHTED", raising=False)
    I.configure_process_isolation("untrusted-direct-run")
    with pytest.raises(I.EvaluationPreflightError, match="network-authorization"):
        I.preflight_evaluation_provider()
    assert calls == []


def test_default_preflight_prefers_models_and_does_not_spend_a_chat_call(monkeypatch):
    from app.agent import config

    monkeypatch.setattr(config, "list_models", lambda **_kwargs: {
        "chat": ["model"], "simple": ["model"], "error": "",
    })
    monkeypatch.setattr(
        I, "_tiny_chat_probe",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("chat must not be called")),
    )
    I._probe_real_provider("openai", 3.0)


def test_default_preflight_falls_back_to_bounded_chat_when_models_is_unsupported(monkeypatch):
    from app.agent import config

    class Definition:
        provider = "openai_compat"
        base_url = "http://example.invalid/v1"
        model = "same-model"

    calls = []
    monkeypatch.setattr(config, "list_models", lambda **_kwargs: {"error": "404 not found"})
    monkeypatch.setattr(config, "chat_definition", lambda *_args, **_kwargs: Definition())
    monkeypatch.setattr(
        I, "_tiny_chat_probe",
        lambda **kwargs: calls.append(kwargs),
    )
    I._probe_real_provider("openai_compat", 7.0)
    assert calls == [{"tier": "complex", "timeout": 7.0}]


def test_default_preflight_never_falls_back_on_connection_failure(monkeypatch):
    from app.agent import config

    monkeypatch.setattr(
        config, "list_models", lambda **_kwargs: {"error": "Connection refused by endpoint"},
    )
    monkeypatch.setattr(
        I, "_tiny_chat_probe",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must fail before chat")),
    )
    with pytest.raises(RuntimeError, match="Connection refused"):
        I._probe_real_provider("openai_compat", 3.0)


def test_process_cache_is_private_and_stays_under_ignored_evaluation_root(monkeypatch):
    monkeypatch.delenv("LTM_EVAL_CACHE_DB_PATH", raising=False)
    path = I.configure_process_isolation("conversation")
    assert path.parent == (ROOT / ".cache/agent-evaluation/runtime-cache").resolve()
    assert path.name.startswith("conversation-") and path.suffix == ".sqlite3"
    assert os.environ["CACHE_DB_PATH"] == str(path)
    assert os.environ["LTM_EVAL_CACHE_POLICY"] == "cold-private-cache-each-case"
    assert os.environ["LTM_EVAL_PROCESS_ISOLATION"] == "separate-process-private-cache"

    monkeypatch.setenv("LTM_EVAL_CACHE_DB_PATH", str(ROOT / "shared.sqlite3"))
    with pytest.raises(ValueError, match=r"\.cache/agent-evaluation"):
        I.configure_process_isolation("conversation")
    monkeypatch.delenv("CACHE_DB_PATH", raising=False)
    monkeypatch.delenv("LTM_EVAL_CACHE_POLICY", raising=False)
    monkeypatch.delenv("LTM_EVAL_PROCESS_ISOLATION", raising=False)


def test_case_reset_clears_cache_and_restores_a_mutated_world():
    I.configure_process_isolation("isolation-test")
    from app.agent.tools import _ctx
    from app.mock.world import get_world

    clean = I.begin_case("clean")
    assert _ctx.client().cache.always_revalidate == ()
    assert _ctx.client().cache.revalidator is None
    assert clean["backgroundRevalidation"] is False
    assert I.finish_case(clean)["worldUnchanged"] is True

    _ctx.client().cache.set("eval-isolation:sentinel", {"stale": True}, 3600)
    _ctx.client().cache.add_snapshot("ticket", "DL-7001", {"progress": 99})
    _ctx.client().cache.touch_recent("/browse/DL-7001", "jira", "stale ticket")
    mutated = I.begin_case("mutated")
    assert _ctx.client().cache.get("eval-isolation:sentinel") is None
    assert _ctx.client().cache.recent_snapshots("ticket", "DL-7001") == []
    assert _ctx.client().cache.recent_items() == []
    original = get_world().issues["DL-7001"]["summary"]
    get_world().issues["DL-7001"]["summary"] = "overwritten by prior battery"
    with pytest.raises(RuntimeError, match="mutated mock data"):
        I.finish_case(mutated)

    recovered = I.begin_case("recovered")
    assert get_world().issues["DL-7001"]["summary"] == original
    assert I.finish_case(recovered)["worldUnchanged"] is True

    provider_mutated = I.begin_case("provider-mutated")
    store = _ctx.client().provider._client.app.state.store
    store.issues["DL-99999"] = {**store.issues["DL-7001"], "key": "DL-99999"}
    with pytest.raises(RuntimeError, match="provider store"):
        I.finish_case(provider_mutated)
    provider_recovered = I.begin_case("provider-recovered")
    assert "DL-99999" not in _ctx.client().provider._client.app.state.store.issues
    assert I.finish_case(provider_recovered)["providerStoreUnchanged"] is True


def test_suite_raw_paths_do_not_overwrite_each_other(monkeypatch):
    monkeypatch.setenv("LTM_EVAL_RUN_GROUP_ID", "isolation-group")
    metadata = {
        "run": {"runGroupId": "isolation-group", "repeatIndex": 1},
        "battery": {"batteryVersion": "2.0.0"},
    }
    conversation = E.raw_result_path("conversation", metadata)
    editor = E.raw_result_path("editor", metadata)
    create = E.raw_result_path("create", metadata)
    meeting = E.raw_result_path("meeting", metadata)
    context = E.raw_result_path("ctx-chg", metadata)
    paths = (conversation, editor, create, meeting, context)
    assert len(set(paths)) == 5
    assert all(path.parent.name == "isolation-group" for path in paths)


def test_raw_attempt_path_is_reserved_and_cannot_be_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "RAW_RESULT_ROOT", tmp_path)
    metadata = {
        "run": {"runGroupId": "same-attempt", "repeatIndex": 1},
        "battery": {"batteryVersion": "2.0.0"},
    }
    path = E.raw_result_path("conversation", metadata)
    assert E.reserve_raw_result_path(path) == path
    assert path.with_suffix(path.suffix + ".claim").read_text(encoding="utf-8").startswith("pid=")
    with pytest.raises(FileExistsError, match="already reserved"):
        E.reserve_raw_result_path(path)
    E.write_raw_result(path, {"attempt": 1})
    with pytest.raises(FileExistsError, match="already exists"):
        E.reserve_raw_result_path(path)


def test_all_harnesses_declare_case_reset_and_world_verification():
    for relative in (
        "tools/agent_lang_ab.py", "tools/agent_compose_eval.py", "tools/agent_create_suite.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "configure_process_isolation(" in text, relative
        assert "preflight_evaluation_provider(" in text, relative
        assert "begin_case(" in text, relative
        assert "finish_case(" in text, relative

    for relative in (
        "tools/agent_lang_ab.py", "tools/agent_compose_eval.py", "tools/agent_create_suite.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        prepare = text.index("def _prepare_runtime")
        configure = text.index("configure_process_isolation(", prepare)
        preflight = text.index("preflight_evaluation_provider()", prepare)
        runtime_import = text.index("from app.agent", prepare)
        assert prepare < configure < preflight < runtime_import, relative

    conversation = (ROOT / "tools/agent_lang_ab.py").read_text(encoding="utf-8")
    run = conversation.index("def run():")
    assert run < conversation.index("_prepare_runtime()", run) < \
        conversation.index("reserve_raw_result_path(", run)

    shared = (ROOT / "tools/agent_scenario_eval.py").read_text(encoding="utf-8")
    assert "configure_process_isolation(suite)" in shared
    assert shared.index("configure_process_isolation(suite)") < \
        shared.index("from app.agent.workflow import session")
    assert shared.index("configure_model_routing(model, simple_model)") < \
        shared.index("preflight_evaluation_provider()") < \
        shared.index("from app.agent.workflow import session")
    assert "begin_case(" in shared and "finish_case(" in shared
    for relative in ("tools/agent_meeting_eval.py", "tools/agent_context_change_eval.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "run_scenario_suite(" in text
