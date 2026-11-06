"""HTTP browser reads share the Confluence service and isolate service failures."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.auth.base import SessionExpired, UpstreamError, UpstreamUnavailable
from app.routes.confluence import build_confluence_router, document_html, _media_path


def test_native_callout_style_and_attachment_link_survive_sanitizing():
    rendered = document_html('<div class="confluence-information-macro confluence-information-macro-tip"><p>Tip</p></div>'
        '<a href="/wiki/download/attachments/12/report.txt">Report</a>', "https://docs.example/wiki", "12")
    assert 'class="callout callout-tip"' in rendered
    assert '/api/confluence/pages/12/file?path=' in rendered
    assert "report.txt" in rendered


@pytest.fixture()
def browser_api(monkeypatch):
    from app.routes import confluence
    client = SimpleNamespace(s=SimpleNamespace(confluence_base="https://docs.example/wiki", search_confluence_spaces=["ETL"]))
    monkeypatch.setattr(confluence.service, "head", lambda *a, **k: {"id": "12", "space": {"key": "ETL"}})
    app = FastAPI()
    app.include_router(build_confluence_router(get_client=lambda: client))
    return TestClient(app), confluence.service, client


def test_local_config_does_not_wait_for_authentication(browser_api, monkeypatch):
    http, service, client = browser_api
    monkeypatch.setattr(service, "list_spaces", lambda *a, **k: (_ for _ in ()).throw(SessionExpired()))
    assert http.get("/api/confluence/config").json() == {"base": "https://docs.example/wiki", "spaces": ["ETL"]}
    response = http.get("/api/confluence/spaces")
    assert response.status_code == 401
    assert response.json()["service"] == "confluence"
    assert response.json()["needLogin"] is True


@pytest.mark.parametrize("error,status", [(UpstreamError(403, "content"), 403),
                                         (UpstreamError(404, "content"), 404),
                                         (UpstreamUnavailable(), 503)])
def test_service_errors_do_not_reach_jira_handlers(browser_api, monkeypatch, error, status):
    http, service, _ = browser_api
    monkeypatch.setattr(service, "head", lambda *a, **k: (_ for _ in ()).throw(error))
    monkeypatch.setattr(service, "check_revision", lambda *a, **k: service.head(*a, **k))
    response = http.get("/api/confluence/pages/12/revision?known_version=2")
    assert response.status_code == status
    assert response.json()["service"] == "confluence"


def test_pinned_view_and_revision_check_delegate_to_shared_service(browser_api, monkeypatch):
    http, service, client = browser_api
    calls = []
    def body(received, cid, version=None, representation="storage"):
        calls.append((received, cid, version, representation))
        return {"id": cid, "title": "문서", "version": {"number": version}, "space": {"key": "ETL"},
                "body": {representation: {"value": '<h1>내용</h1><script>alert(1)</script>'}}}
    monkeypatch.setattr(service, "body", body)
    monkeypatch.setattr(service, "check_revision", lambda c, p, v: {"pageId": p, "latestPublishedVersion": 4, "isLatest": v == 4})
    response = http.get("/api/confluence/pages/12?version=3").json()
    assert calls == [(client, "12", 3, "view"), (client, "12", 3, "storage")]
    assert response["html"] == "<h1>내용</h1>"
    assert "body" not in response
    assert http.get("/api/confluence/pages/12/revision?known_version=3").json()["isLatest"] is False


def test_full_text_search_is_space_scoped_and_paginated(browser_api, monkeypatch):
    http, service, _ = browser_api
    calls = []
    monkeypatch.setattr(service, "search", lambda c, q, s, l: calls.append((q, s, l)) or {"results": [], "start": s})
    http.get('/api/confluence/search', params={"q": '적재 "표"', "space": "ETL", "start": 30})
    assert calls == [('type = page AND text ~ "적재 \\"표\\"" AND space = "ETL" ORDER BY lastmodified DESC', 30, 30)]


def test_tree_index_reads_cached_metadata_without_text_query_or_bodies(browser_api, monkeypatch):
    http, service, _ = browser_api
    calls = []
    def search(client, query, start, limit):
        calls.append((query, start, limit))
        return {"results": [{"content": {"id": "22", "title": "Data Lake",
            "space": {"key": "ETL"}, "ancestors": [{"id": "12", "title": "Root"}],
            "body": {"storage": {"value": "not part of tree"}}}}],
            "start": start, "size": 1, "_links": {"next": "?start=31"}}
    monkeypatch.setattr(service, "search", search)
    result = http.get('/api/confluence/tree', params={"space": "ETL", "start": 30}).json()
    assert calls == [('type = page AND space = "ETL" ORDER BY title ASC', 30, 100)]
    assert result["results"] == [{"id": "22", "title": "Data Lake", "space": {"key": "ETL"},
                                   "ancestors": [{"id": "12", "title": "Root"}]}]
    assert result["start"] == 30 and result["_links"]["next"] == "?start=31"
    assert http.get('/api/confluence/tree?space=OTHER').status_code == 403
    assert len(calls) == 1


def test_scope_rejects_other_spaces_and_latest_external_move(browser_api, monkeypatch):
    http, service, client = browser_api
    state = {"space": "OTHER"}
    monkeypatch.setattr(service, "head", lambda *a, **k: {"id": "12", "space": {"key": state["space"]}})
    monkeypatch.setattr(service, "body", lambda *a, **k: pytest.fail("outside Space must not fetch body"))
    assert http.get("/api/confluence/pages/12").status_code == 403
    assert http.get("/api/confluence/pages?space=OTHER").status_code == 403
    assert http.get("/api/confluence/search?space=OTHER&q=secret").status_code == 403
    state["space"] = "ETL"
    def check(*args):
        state["space"] = "OTHER"  # fresh metadata discovers an external move
        return {"latestPublishedVersion": 4, "isLatest": False}
    monkeypatch.setattr(service, "check_revision", check)
    response = http.get("/api/confluence/pages/12/revision?known_version=2")
    assert response.status_code == 403 and "latestPublishedVersion" not in response.json()
    client.s.search_confluence_spaces = []
    assert http.get("/api/confluence/spaces").status_code == 422


def test_space_filter_preserves_upstream_pagination(browser_api, monkeypatch):
    http, service, _ = browser_api
    monkeypatch.setattr(service, "list_spaces", lambda *a, **k: {"results": [{"key": "OTHER"}],
        "size": 1, "start": 0, "_links": {"next": "/rest/api/space?start=1"}})
    result = http.get("/api/confluence/spaces").json()
    assert result["results"] == [] and result["size"] == 1 and result["_links"]["next"]


def test_html_security_and_configured_host_media():
    base = "https://docs.example/wiki"
    html = document_html('<p onclick="evil()">안전</p><img src="https://evil.example/tracker">'
                         '<img src="/wiki/download/attachments/12/image.png" onerror="evil()">'
                         '<a href="javascript:evil()">bad</a><input type="checkbox">', base, "12")
    assert "onclick" not in html and "onerror" not in html and "javascript" not in html
    assert 'src="https://evil.example/tracker"' in html
    assert 'referrerpolicy="no-referrer"' in html
    assert '/api/confluence/pages/12/image?path=' in html
    assert 'disabled="disabled"' in html
    assert _media_path(base, "12", "/wiki/download/attachments/13/image.png") is None
    assert _media_path(base, "12", "/wiki/download/attachments/12/%2e%2e/secret") is None
    assert _media_path(base, "12", "https://evil.example/wiki/download/attachments/12/image.png") is None


def test_reader_uses_head_path_when_body_does_not_expand_ancestors(browser_api, monkeypatch):
    http, service, client = browser_api
    ancestors = [{"id": "1", "title": "Root"}, {"id": "2", "title": "Guides"}]
    monkeypatch.setattr(service, "head", lambda *a, **k: {"id": "12", "space": {"key": "ETL"}, "ancestors": ancestors})
    monkeypatch.setattr(service, "body", lambda *a, **k: {"id": "12", "space": {"key": "ETL"},
        "version": {"number": 1}, "body": {"view": {"value": "<h2>Body</h2>"}, "storage": {"value": "<h2>Body</h2>"}}})
    response = http.get("/api/confluence/pages/12")
    assert response.status_code == 200 and response.json()["ancestors"] == ancestors
