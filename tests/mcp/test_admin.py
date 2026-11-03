"""MCP and UI share manager checks, atomic config commands and live dashboard data."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import anyio
import httpx2
import pytest
import yaml
import threading
import time
from fastapi import HTTPException
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.infra import settings as config
from app.auth.base import SessionExpired, UpstreamUnavailable
from app.local_mcp.admin_tools import register_admin
from app.routes.dashboards import build_dashboard_router
from app.routes.workspace_config import build_workspace_config_router
from app.services.workspace_config import WorkspaceConfigCommands


@pytest.fixture
def workspace_env(build_env, monkeypatch, tmp_path):
    env = build_env()
    directory = tmp_path / "workspace-config"
    directory.mkdir()
    raw = {"project_key": "DL", "extension": {"keep": True}, "wbs": [
        {"module": "Workbench", "color": "keep", "tasks": [
            {"name": "Existing plan", "start": "2026-01-01", "end": "2026-12-01",
             "note": "preserve", "epics": [{"ticket": "DL-101", "weight": 2, "extra": "keep"}]}]},
        {"module": "Other", "tasks": []}]}
    (directory / "wbs_config.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    (directory / "people.yaml").write_text("Workbench: [u01]\nOther: [u02]\n", encoding="utf-8")
    (directory / "module-aliases.yaml").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", directory)
    config.reload_people()
    env.runtime.require_manager = lambda: None
    integrated = hasattr(env.runtime, "workspace_config")
    if integrated:
        env.runtime.workspace_config.config_dir = directory
        env.runtime.workspace_config.require_manager = lambda: env.runtime.require_manager()
    else:
        env.runtime.workspace_config = WorkspaceConfigCommands(
            lambda: env.client, lambda: env.runtime.require_manager(), directory)
        register_admin(env.runtime.register, env.runtime)
        env.app.include_router(build_workspace_config_router(env.runtime.workspace_config, port=4457))
    env.app.include_router(build_dashboard_router(
        client=env.client, cache=env.cache, settings=env.runtime.settings,
        scoped_people=config.load_people, require_manager=lambda: env.runtime.require_manager(),
        require_module_access=lambda _m: None, require_person_access=lambda _u: None))
    monkeypatch.setattr(env.client, "epic_progress_map", lambda _plan: {})
    env.directory = directory
    yield env
    config.reload_people()


def _task():
    return {"id": "wbs-new", "module": "Workbench", "name": "MCP registered plan",
            "start": "2026-02-01", "end": "2026-11-01",
            "epics": [{"key": "DL-101", "weight": 6}, {"key": "DL-102", "weight": 4}]}


def test_sdk_config_writes_are_visible_in_ui_and_preserve_unknown_yaml(workspace_env):
    env = workspace_env
    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                    base_url="http://127.0.0.1:4457",
                    headers={"Authorization": "Bearer " + env.runtime.credentials.token()}) as http:
                async with Client(streamable_http_client(env.runtime.url, http_client=http)) as mcp:
                    async def call(name, **arguments):
                        result = await mcp.call_tool(name, arguments)
                        assert not result.is_error, result.structured_content
                        return result.structured_content["data"]
                    state = await call("get_workspace_config")
                    original_revision = state["revision"]
                    state = await call("create_wbs_task", task=_task(), expected_revision=state["revision"])
                    assert state == (await http.get("/api/workspace/config")).json()
                    dashboard = (await http.get("/api/wbs")).json()
                    assert any(row["name"] == "MCP registered plan" for row in dashboard["wbs"])
                    # Lost-response replay changes neither revision nor already-saved contents.
                    replay = await call("create_wbs_task", task=_task(), expected_revision=original_revision)
                    assert replay == state
                    state = await call("update_wbs_task", task_id="wbs-new", changes={"name": "Edited plan"},
                                       expected_revision=state["revision"])
                    state = await call("set_module_people", module="New module", user_ids=["u01", "u03"],
                                       expected_revision=state["revision"])
                    assert config.modules_of("u01") == ["Workbench", "New module"]
                    shell = (await http.get("/api/workload/shell")).json()
                    assert next(m for m in shell["modules"] if m["module"] == "New module")["peopleCount"] == 2
                    state = await call("rename_people_module", module="New module", new_name="Renamed",
                                       expected_revision=state["revision"])
                    state = await call("delete_people_module", module="Renamed", expected_revision=state["revision"])
                    state = await call("delete_wbs_task", task_id="wbs-new", expected_revision=state["revision"])
                    assert len(state["tasks"]) == 1 and list(state["people"]) == ["Workbench", "Other"]
    anyio.run(scenario)
    raw = yaml.safe_load((env.directory / "wbs_config.yaml").read_bytes())
    assert raw["extension"] == {"keep": True} and raw["wbs"][0]["color"] == "keep"
    existing = raw["wbs"][0]["tasks"][0]
    assert existing["id"] == "Workbench-1" and existing["note"] == "preserve"
    assert existing["epics"][0]["extra"] == "keep"
    assert not [call for call in env.calls if call[0] not in {"get"}], env.calls


@pytest.mark.parametrize("patch", [
    {"name": ""}, {"start": "2026-02-30"}, {"end": "2026-01-01"},
    {"epics": []}, {"epics": [{"key": "DL-1", "weight": -1}]},
    {"epics": [{"key": "DL-1", "weight": 0}]},
    {"epics": [{"key": "DL-1", "weight": 1}, {"key": "DL-1", "weight": 2}]},
    {"epics": [{"key": "../../file", "weight": 1}]}, {"path": "arbitrary.yaml"},
])
def test_sdk_rejects_invalid_wbs_without_any_write(workspace_env, patch):
    env = workspace_env
    before = {path.name: path.read_bytes() for path in env.directory.glob("*.yaml")}
    state = env.runtime.workspace_config.read()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("create_wbs_task", {"task": {**_task(), **patch},
                                                            "expected_revision": state["revision"]})
            assert result.is_error
    anyio.run(scenario)
    assert before == {path.name: path.read_bytes() for path in env.directory.glob("*.yaml")}


def test_config_conflict_permissions_and_atomic_failure_keep_files_and_caches(workspace_env, monkeypatch):
    env = workspace_env
    commands = env.runtime.workspace_config
    state = commands.read()
    config.module_dir()  # Existing directory must be refreshed only after a successful write.
    for key in ("workload:mock:old", "mentiondef:mock", "issue:mock:DL-1"):
        env.cache.set(key, {"old": True}, 900)
    original = (env.directory / "people.yaml").read_bytes()
    replace = __import__("os").replace
    def fail_target(source, target):
        if Path(target) == env.directory / "people.yaml":
            raise OSError("simulated disk failure")
        return replace(source, target)
    monkeypatch.setattr("app.services.workspace_config.os.replace", fail_target)
    async def failed_save():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("set_module_people", {"module": "Workbench", "user_ids": ["u03"],
                                                              "expected_revision": state["revision"]})
            assert result.is_error and result.structured_content["error"]["status"] == 500
    anyio.run(failed_save)
    assert (env.directory / "people.yaml").read_bytes() == original
    assert config.modules_of("u01") == ["Workbench"]
    assert env.cache.get("workload:mock:old") and env.cache.get("mentiondef:mock")
    assert not list((env.directory / ".cache").rglob("*.tmp"))
    monkeypatch.setattr("app.services.workspace_config.os.replace", replace)
    with TestClient(env.app, base_url="http://127.0.0.1:4457", client=("127.0.0.1", 1234)) as http:
        response = http.put("/api/workspace/config/people", json={"revision": state["revision"],
            "people": {"Workbench": ["u03"], "Other": ["u02"]}}, headers={"X-LTM-Local": "1"})
        assert response.status_code == 200
        assert not env.cache.get("workload:mock:old") and not env.cache.get("mentiondef:mock")
        assert env.cache.get("issue:mock:DL-1")
        assert config.modules_of("u01") == [] and config.modules_of("u03") == ["Workbench"]
        conflict = http.put("/api/workspace/config/people", json={"revision": state["revision"], "people": {}},
                            headers={"X-LTM-Local": "1"})
        assert conflict.status_code == 409 and config.load_people() == response.json()["people"]
        no_header = http.put("/api/workspace/config/people", json={"revision": response.json()["revision"], "people": {}})
        assert no_header.status_code == 403
        cross_origin = http.get("/api/workspace/config", headers={"Origin": "http://evil.test"})
        assert cross_origin.status_code == 403
        invalid_form = http.put("/api/workspace/config/wbs", json={"revision": response.json()["revision"],
            "tasks": [{**_task(), "start": "2026-02-30"}]}, headers={"X-LTM-Local": "1"})
        assert invalid_form.status_code == 422 and isinstance(invalid_form.json()["error"], str)
        def denied():
            raise HTTPException(403, "manager only")
        env.runtime.require_manager = denied
        assert http.get("/api/workspace/config").status_code == 403
        assert http.put("/api/workspace/config/people", json={"revision": response.json()["revision"], "people": {}},
                        headers={"X-LTM-Local": "1"}).status_code == 403
        env.runtime.require_manager = lambda: None
        monkeypatch.setattr(env.client, "current_user_for_authorization", lambda: {})
        assert http.get("/api/workspace/config").status_code == 401


@pytest.mark.parametrize("users", [["u01", "U01"], [""], ["bad name"], [True]])
def test_people_validation_and_revision_poll_are_local(workspace_env, users, monkeypatch):
    env = workspace_env
    commands = env.runtime.workspace_config
    before = commands.read()
    with pytest.raises((ValueError, TypeError)):
        commands.module_people("Workbench", users, before["revision"])
    assert commands.read() == before
    monkeypatch.setattr(env.client, "current_user_for_authorization", lambda: pytest.fail("status revision must not probe Jira"))
    assert commands.revision() == before["revision"]


def test_sdk_denies_unauthenticated_or_non_manager_config_access(workspace_env, monkeypatch):
    env = workspace_env
    state = env.runtime.workspace_config.read()
    def denied():
        raise HTTPException(403, "manager only")
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            env.runtime.require_manager = denied
            for name, args in (("get_workspace_config", {}), ("set_module_people", {
                "module": "private", "user_ids": ["u01"], "expected_revision": state["revision"]})):
                result = await mcp.call_tool(name, args)
                assert result.is_error and result.structured_content["error"]["code"] == "permission_denied"
            env.runtime.require_manager = lambda: None
            monkeypatch.setattr(env.client, "current_user_for_authorization", lambda: {})
            result = await mcp.call_tool("get_workspace_config", {})
            assert result.is_error and result.structured_content["error"]["code"] == "authentication_required"
    anyio.run(scenario)
    assert env.runtime.workspace_config.revision() == state["revision"]


def test_concurrent_ui_and_mcp_revision_cannot_overwrite_each_other(workspace_env):
    commands = workspace_env.runtime.workspace_config
    state = commands.read()
    def save(user):
        try:
            return commands.save("people", state["revision"], {"Workbench": [user]})
        except ValueError as exc:
            return exc
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(save, ["u02", "u03"]))
    assert len([result for result in results if isinstance(result, dict)]) == 1
    assert [getattr(result, "status", None) for result in results if isinstance(result, ValueError)] == [409]
    assert commands.read()["people"] in ({"Workbench": ["u02"]}, {"Workbench": ["u03"]})


def test_cached_manager_cannot_authorize_known_logout_or_failed_identity_refresh(workspace_env, monkeypatch):
    env, commands = workspace_env, workspace_env.runtime.workspace_config
    state = commands.read()
    env.client._session_dead = True
    with pytest.raises(SessionExpired):
        commands.save("people", state["revision"], {})
    assert commands.revision() == state["revision"]
    env.client._session_dead = False
    key = f"myself:{env.client.env}"
    with env.cache._lock:
        env.cache._conn.execute("UPDATE cache SET fetched_at=? WHERE key=?",
                               (time.time() - env.client.USER_TTL - 1, key))
        env.cache._conn.commit()
    assert env.cache.get(key) is None and env.cache.get_alive(key) is not None
    def failed(*_args, **_kwargs):
        raise UpstreamUnavailable("identity lookup offline")
    monkeypatch.setattr(env.provider, "get_json", failed)
    with pytest.raises(ValueError) as denied:
        commands.save("people", state["revision"], {})
    assert denied.value.status == 401
    assert commands.revision() == state["revision"]
    assert not env.client._session_dead  # Transport failure is not proof of expired login.


def test_slow_authorization_does_not_hold_config_status_lock(workspace_env, monkeypatch):
    commands = workspace_env.runtime.workspace_config
    state = commands.read()
    started, release = threading.Event(), threading.Event()
    def slow_identity():
        started.set()
        assert release.wait(3)
        return {"id": "u01"}
    monkeypatch.setattr(workspace_env.client, "current_user_for_authorization", slow_identity)
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(commands.create_task, _task(), state["revision"])
        assert started.wait(1)
        try:
            assert pool.submit(commands.revision).result(timeout=1) == state["revision"]
        finally:
            release.set()
        assert len(pending.result()["tasks"]) == 2
