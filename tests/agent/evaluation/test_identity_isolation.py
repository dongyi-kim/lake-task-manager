"""Identity sources and scope must not inherit the machine's workspace roster."""
import copy
import json
from types import SimpleNamespace

import anyio
import pytest
from mcp import Client

from app.infra import settings
from tools.codex_mcp_eval import fixture as base
from tools.codex_mcp_eval.identity_fixture import ACTOR_ID, IDENTITY_PLAN, IDENTITY_ROSTER, IdentityFixture


def test_identity_overrides_host_scope_roster_and_plan_before_loading(tmp_path, monkeypatch):
    host_plan = {"project_key": "HOST", "modules": ["HOST_PRIVATE"], "epics": {"HOST-1": "HOST_SECRET"}, "wbs": []}
    host_people = {"HOST_PRIVATE": ["host.secret.employee"]}
    for module in (settings, base):
        monkeypatch.setattr(module, "load_plan", lambda: copy.deepcopy(host_plan))
        monkeypatch.setattr(module, "load_people", lambda: copy.deepcopy(host_people))
    host_cache = {"data": host_people, "dir": None}
    monkeypatch.setattr(settings, "_PEOPLE_CACHE", host_cache)
    module_dir_alias = settings.module_dir  # Same import pattern as person_context.
    host_directory = module_dir_alias()
    assert host_directory["byUser"] == {"host.secret.employee": ["HOST_PRIVATE"]}
    source = settings.get_settings()
    monkeypatch.setattr(source, "search_jira_projects", [])
    monkeypatch.setattr(source, "search_confluence_spaces", [])
    monkeypatch.setattr(source, "managers", ["host.secret.manager"])
    with IdentityFixture(tmp_path / "isolated", port=0, today="2026-11-04") as env:
        assert env.settings.search_jira_projects == ["DL"] and env.settings.search_confluence_spaces == []
        assert env.world.plan == IDENTITY_PLAN and env.world.people == IDENTITY_ROSTER
        assert settings.load_people() == IDENTITY_ROSTER and settings.load_plan() == IDENTITY_PLAN
        directory = module_dir_alias()
        assert directory["people"] == IDENTITY_ROSTER and directory["modules"] == ["TEST"]
        assert set(directory["byUser"]) == {user.lower() for user in IDENTITY_ROSTER["TEST"]}
        assert settings.modules_of(ACTOR_ID) == ["TEST"]
        assert settings.modules_of("host.secret.employee") == []
        assert env.runtime.scoped_people() == IDENTITY_ROSTER
        assert set(env.store.users) == set(IDENTITY_ROSTER["TEST"]) and len(env.store.users) == 8
        assert env.store.config.current_user == ACTOR_ID
        assert env.assert_conformance()["currentUserId"] == ACTOR_ID

        async def scenario():
            async with Client(env.runtime.server) as client:
                me = await client.call_tool("get_user", {})
                assert not me.is_error and me.structured_content["data"]["id"] == ACTOR_ID
                for kind in ("wbs", "vit", "workload"):
                    result = await client.call_tool("get_dashboard", {"kind": kind})
                    assert not result.is_error, result.structured_content
                    assert "HOST" not in json.dumps(result.structured_content)
                    assert "host.secret" not in json.dumps(result.structured_content)
                    data = result.structured_content["data"]
                    if kind == "wbs":
                        assert data["wbs"] == [] and data["epics"] == {}
                    if kind == "workload":
                        assert {row["id"] for module in data["modules"] for row in module["people"]} == set(IDENTITY_ROSTER["TEST"])
        anyio.run(scenario)
        assert env.summary()["worldUnchanged"] and env.summary()["providerStoreUnchanged"]
    assert settings.load_people() == host_people and base.load_people() == host_people
    assert settings._PEOPLE_CACHE is host_cache and module_dir_alias() is host_directory
    assert settings.load_plan() == host_plan and base.load_plan() == host_plan
    assert source.search_jira_projects == [] and source.managers == ["host.secret.manager"]


def test_default_query_scope_still_uses_host_scopes_and_requires_both(tmp_path):
    env = base.EvaluationFixture(tmp_path / "unused", port=0)
    configured = SimpleNamespace(search_jira_projects=["JIRA"], search_confluence_spaces=["DOCS"])
    env.configure_scope(configured)
    assert vars(configured) == {"search_jira_projects": ["JIRA"], "search_confluence_spaces": ["DOCS"]}
    for projects, spaces in (([], []), (["JIRA"], []), ([], ["DOCS"])):
        with pytest.raises(ValueError, match="Jira projects and Confluence spaces"):
            env.configure_scope(SimpleNamespace(search_jira_projects=projects, search_confluence_spaces=spaces))
