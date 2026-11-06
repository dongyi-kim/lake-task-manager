"""Composer writes retain MCP command semantics and reject cross-origin writes."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.auth.base import SessionExpired
from app.routes.confluence_authoring import build_confluence_authoring_router
from app.services.confluence_commands import ConfluenceCommands


@pytest.fixture()
def authoring_api(monkeypatch):
    from app.routes import confluence_authoring as module
    client = SimpleNamespace(s=SimpleNamespace(app_port=4457, search_confluence_spaces=["DL"]))
    app = FastAPI()
    app.include_router(build_confluence_authoring_router(get_client=lambda: client))
    calls = []
    monkeypatch.setattr(module.reads, "raw_read", lambda *args: calls.append("preflight") or {})
    monkeypatch.setattr(ConfluenceCommands, "create_page", lambda _, **kwargs: calls.append(kwargs) or {"id": "42"})
    headers = {"Origin": "http://127.0.0.1:4457", "X-LTM-Confluence": "1"}
    with TestClient(app, base_url="http://127.0.0.1:4457", client=("127.0.0.1", 1234), headers=headers) as http:
        yield http, calls, module


PAYLOAD = {"space": "DL", "title": "New page", "body": "<h1>Title</h1>", "mutation_id": "create-page-001"}


def test_create_preserves_logical_id_and_performs_service_preflight(authoring_api):
    http, calls, _ = authoring_api
    assert http.post("/api/confluence/pages", json=PAYLOAD).json() == {"id": "42"}
    assert calls[0] == "preflight" and calls[1] == {**PAYLOAD, "parent_id": None}


@pytest.mark.parametrize("headers", [
    {"Origin": "https://outside.example"}, {"Origin": "null"}, {"Origin": ""},
    {"Host": "outside.example:4457"}, {"X-LTM-Confluence": ""},
])
def test_foreign_writes_never_reach_confluence(authoring_api, headers):
    http, calls, _ = authoring_api
    response = http.post("/api/confluence/pages", json=PAYLOAD, headers=headers)
    assert response.status_code == 403 and not calls


def test_auth_and_space_failures_prevent_creation(authoring_api, monkeypatch):
    http, calls, module = authoring_api
    assert http.post("/api/confluence/pages", json={**PAYLOAD, "space": "OTHER"}).status_code == 403
    assert not calls
    monkeypatch.setattr(module.reads, "raw_read", lambda *args: (_ for _ in ()).throw(SessionExpired()))
    response = http.post("/api/confluence/pages", json=PAYLOAD)
    assert response.status_code == 401 and response.json()["service"] == "confluence"
    assert response.json()["needLogin"] and not calls


def test_upload_passes_binary_and_mutation_id_to_shared_commands(authoring_api, monkeypatch):
    http, calls, _ = authoring_api
    monkeypatch.setattr(ConfluenceCommands, "upload_attachment", lambda _, **kwargs: calls.append(kwargs) or {"id": "43"})
    assert http.post("/api/confluence/pages/42/attachments", data={"mutation_id": "file-request-001"},
                     files={"file": ("report.txt", b"content", "text/plain")}).json() == {"id": "43"}
    assert calls == [{"page_id": "42", "filename": "report.txt", "data": b"content", "content_type": "text/plain", "mutation_id": "file-request-001"}]


def test_mentions_require_verified_confluence_identifier(authoring_api, monkeypatch):
    http, _, _ = authoring_api
    monkeypatch.setattr(ConfluenceCommands, "request", lambda *a, **k: {"username": "alice", "userKey": "different-id"})
    assert http.post("/api/confluence/mention-users", json={"usernames": ["alice", "alice"]}).json() == {"alice": "different-id"}
    assert http.post("/api/confluence/mention-users", json={"usernames": ["bob"]}).status_code == 400
