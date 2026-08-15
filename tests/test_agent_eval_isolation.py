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
    "LTM_EVAL_CACHE_DB_PATH",
)


@pytest.fixture(autouse=True)
def restore_evaluation_environment():
    before = {key: os.environ.get(key) for key in _ENV_KEYS}
    yield
    for key, value in before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


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
        assert "begin_case(" in text, relative
        assert "finish_case(" in text, relative

    conversation = (ROOT / "tools/agent_lang_ab.py").read_text(encoding="utf-8")
    assert conversation.index('configure_process_isolation("conversation")') < \
        conversation.index("from app.agent.workflow import session")
    for relative in ("tools/agent_compose_eval.py", "tools/agent_create_suite.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        prepare = text.index("def _prepare_runtime")
        configure = text.index("configure_process_isolation(", prepare)
        runtime_import = text.index("from app.agent", prepare)
        assert prepare < configure < runtime_import, relative

    shared = (ROOT / "tools/agent_scenario_eval.py").read_text(encoding="utf-8")
    assert "configure_process_isolation(suite)" in shared
    assert shared.index("configure_process_isolation(suite)") < \
        shared.index("from app.agent.workflow import session")
    assert "begin_case(" in shared and "finish_case(" in shared
    for relative in ("tools/agent_meeting_eval.py", "tools/agent_context_change_eval.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "run_scenario_suite(" in text
