"""Non-managers can create without modifying Reporter or owning the parent."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jira820 import make_app
from jira820.config import Config

from app.auth.base import PermissionDenied, UpstreamError
from app.auth.inprocess import InProcessProvider
from app.infra.cache import Cache
from app.jira.jira_client import JiraClient
from app.mock.permissions import PermissionStore, install_permission_route
from app.routes.ticket_commands import (
    AssigneeBody, BulkBody, BulkUpdateBody, ChildBody, EpicBody, FieldsBody, TaskBody,
    build_ticket_command_router,
)


@pytest.fixture
def setup():
    config = Config(project_key="DL", current_user="u01", latency_ms=0)
    store = PermissionStore(config)
    store.permissions["MODIFY_REPORTER"] = False
    app = make_app(store=store)
    install_permission_route(app)
    transport = TestClient(app)
    provider = object.__new__(InProcessProvider)
    provider._client = transport
    provider._serial = lambda fn, priority=None: fn()
    settings = SimpleNamespace(jira_env="mock", project_key="DL", jira_base="",
                               sp_field_id=config.sp_field,
                               epic_link_field_id=config.epic_link_field,
                               epic_name_field_id=config.epic_name_field, cache_ttl_seconds=600)
    client = JiraClient(settings, Cache(":memory:"))
    client._provider, client._provider_built = provider, True
    # Cache dependency tests cover invalidation; retain the real serializer/provider/API here.
    client._invalidate_ticket = lambda *_args, **_kwargs: None
    client._invalidate_people_views = lambda: None
    client._record_mutation = lambda event: event
    client.cascade_suggestion = lambda *_args, **_kwargs: None
    requests = []
    original_write = provider._write

    def write(method, path, body, params, **kwargs):
        requests.append((method, path, body))
        return original_write(method, path, body, params, **kwargs)

    provider._write = write

    def reject_edit(key):
        raise HTTPException(403, "parent is owned by another user")

    router = build_ticket_command_router(
        get_client=lambda: client, settings=settings, may_edit=lambda key: False,
        require_edit=reject_edit, session_user=lambda: {"id": "u01"},
    )
    endpoint = lambda path: next(route.endpoint for route in router.routes if route.path == path)
    epic = next(k for k, issue in store.issues.items() if issue["type"] == "Epic")
    task = next(k for k, issue in store.issues.items()
                if issue["type"] == "Task" and issue["statusCategory"] != "done")
    yield SimpleNamespace(client=client, store=store, endpoint=endpoint, requests=requests,
                          transport=transport, epic=epic, task=task)
    transport.close()


@pytest.mark.parametrize("kind", ["task", "epic-task", "subtask", "epic", "bulk"])
def test_creation_needs_no_reporter_permission_or_parent_edit_permission(setup, kind):
    s = setup
    if kind == "task":
        response = s.endpoint("/api/task")(TaskBody(type="Task", summary="new", reporter="u02"))
    elif kind in ("epic-task", "subtask"):
        epic = kind == "epic-task"
        response = s.endpoint("/api/ticket/{key}/child")(
            s.epic if epic else s.task,
            ChildBody(type="Task" if epic else s.store.config.subtask_type, summary="new", parentIsEpic=epic),
        )
    elif kind == "epic":
        response = s.endpoint("/api/epic")(EpicBody(summary="new", reporter=None))
    else:
        body = BulkBody(mode="task", items=[{"summary": "new", "type": "Task", "epic": s.epic,
                                             "reporter": "u02"}])
        assert json.loads(s.endpoint("/api/bulk/validate")(body).body)["ok"]
        response = s.endpoint("/api/bulk/create")(body)
    result = json.loads(response.body)
    assert response.status_code == 200 and result["ok"], result
    key = result["created"][0]["key"] if kind == "bulk" else result["key"]
    assert s.store.issues[key]["reporter"] == "u01"
    assert len(s.requests) == 1
    method, path, body = s.requests[0]
    assert (method, path) == ("post", "/rest/api/2/issue")
    assert "reporter" not in body["fields"]


def test_parent_menu_offers_creation_without_granting_parent_edit(setup):
    result = json.loads(setup.endpoint("/api/ticket/{key}/menu")(setup.epic).body)
    assert result["mayEdit"] is False
    assert "Task" in result["childTypes"]
    with pytest.raises(HTTPException):
        setup.endpoint("/api/ticket/{key}/assignee")(setup.epic, AssigneeBody(assignee="u01"))
    body = BulkUpdateBody(items=[{"key": setup.task, "changes": {"summary": "blocked"}}])
    assert not json.loads(setup.endpoint("/api/bulk/update/validate")(body).body)["ok"]
    assert setup.requests == []


def test_jira_create_denial_is_preserved_and_never_retried(setup):
    setup.store.permissions["CREATE_ISSUES"] = False
    before = len(setup.store.issues)
    with pytest.raises(UpstreamError) as caught:
        setup.endpoint("/api/task")(TaskBody(type="Task", summary="denied"))
    assert caught.value.status == 403
    assert len(setup.store.issues) == before
    assert len(setup.requests) == 1


def test_reporter_edit_requires_actual_jira_permission_and_metadata(setup, monkeypatch):
    s = setup
    assert "reporter" not in s.client.editmeta(s.task)
    response = s.endpoint("/api/ticket/{key}/fields")(s.task, FieldsBody(reporter="u02"))
    assert response.status_code == 403 and not s.requests
    with pytest.raises(PermissionDenied):
        s.client.update_fields(s.task, {"reporter": {"name": "u02"}})

    s.store.permissions["MODIFY_REPORTER"] = True
    assert "reporter" in s.client.editmeta(s.task)
    s.client.update_fields(s.task, {"reporter": {"name": "u02"}})
    assert s.store.issues[s.task]["reporter"] == "u02"

    s.store.permissions["MODIFY_REPORTER"] = False
    with pytest.raises(PermissionDenied):
        s.client.update_fields(s.task, {"reporter": {"name": "u01"}})
    s.store.permissions["MODIFY_REPORTER"] = True

    # Permission alone is insufficient if the field is absent from the workflow's edit screen.
    get_json = s.client.provider.get_json
    def no_reporter_meta(path, **kwargs):
        result = get_json(path, **kwargs)
        if path.endswith("/editmeta"):
            result["fields"].pop("reporter", None)
        return result
    monkeypatch.setattr(s.client.provider, "get_json", no_reporter_meta)
    with pytest.raises(PermissionDenied):
        s.client.update_fields(s.task, {"reporter": None})
    assert len(s.requests) == 1


def test_mock_jira_rejects_explicit_reporter_but_defaults_omitted_reporter(setup):
    fields = {"project": {"key": "DL"}, "issuetype": {"name": "Task"}, "summary": "new"}
    for value in (None, {"name": "u01"}, {"name": "u02"}):
        response = setup.transport.post("/rest/api/2/issue", json={"fields": {**fields, "reporter": value}})
        assert response.status_code == 400 and "reporter" in response.json()["errors"]
    response = setup.transport.post("/rest/api/2/issue", json={"fields": fields})
    assert response.status_code == 201
    assert setup.store.issues[response.json()["key"]]["reporter"] == "u01"
