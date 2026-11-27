from types import SimpleNamespace

import pytest

from app.auth.base import SessionExpired, UpstreamBusy
from app.infra.cache import Cache
from app.infra.resource_pressure import ResourceBusy
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


@pytest.fixture
def client():
    cache = Cache(":memory:")
    client = JiraClient(get_settings(), cache)
    client._provider_built = True
    client._provider = SimpleNamespace(close=lambda: None)
    yield client
    client.close()
    cache.close()


@pytest.mark.parametrize("failure", [SessionExpired("expired"), UpstreamBusy("busy"), ConnectionError("offline")])
def test_changelog_failure_does_not_publish_empty_success(client, failure):
    def fail(*args, **kwargs):
        raise failure
    client._provider.get_json = fail
    with pytest.raises(type(failure)):
        client._changelog("DL-1")
    assert client.cache.get_stale(f"changelog:{client.env}:DL-1") is None
    client._provider.get_json = lambda *a, **kw: {"fields": {}, "changelog": {"histories": []}}
    assert client._changelog("DL-1")["histories"] == []


def test_timeline_limits_materialized_cache_but_preserves_newest_events(client, monkeypatch):
    histories = [{"created": f"2026-11-01T{index:06d}", "items": [{
        "field": "status", "fromString": "old", "toString": str(index)}]} for index in range(5000)]
    monkeypatch.setattr(client, "_changelog", lambda key: {"histories": histories})
    monkeypatch.setattr(client, "_status_cats", lambda: {})
    monkeypatch.setattr(client, "_issue_comments", lambda *a, **kw: [])
    result = client.ticket_timeline("DL-1", include_children=False)
    assert len(result) == 40 and result[0]["to"] == "4999"
    stored = client.cache.get(f"timeline:{client.env}:DL-1:self:v2")
    assert len(stored) <= client.TIMELINE_CACHE_LIMIT
    assert len(client.ticket_timeline("DL-1", limit=1000, include_children=False)) == 1000


def test_oversized_display_changelog_is_not_copied_into_cache(client, monkeypatch):
    monkeypatch.setattr(client, "DISPLAY_CHANGELOG_MAX_BYTES", 1000)
    client._provider.get_json = lambda *a, **kw: {"fields": {}, "changelog": {
        "histories": [{"items": [{"field": "description", "fromString": "x" * 2000}]}]}}
    with pytest.raises(UpstreamBusy):
        client._changelog("DL-1")
    assert client.cache.get_stale(f"changelog:{client.env}:DL-1") is None


def test_legacy_empty_changelog_cache_does_not_hide_auth_failure(client):
    client.cache.set(f"changelog:{client.env}:DL-1", {}, 600)
    def expired(*args, **kwargs):
        raise SessionExpired("expired")
    client._provider.get_json = expired
    with pytest.raises(SessionExpired):
        client._changelog("DL-1")


def comments_provider(client, rows):
    calls = []
    def get_json(path, params=None):
        start, size = params.get("startAt", 0), params["maxResults"]
        calls.append(start)
        return {"startAt": start, "total": len(rows), "comments": rows[start:start + size]}
    client._provider.get_json = get_json
    return calls


def comment(index, body="small"):
    return {"id": str(index), "body": body, "created": "2026-11-01T00:00:00Z"}


def test_complete_ui_collection_stops_at_cap_without_caching_false_coverage(client, monkeypatch):
    monkeypatch.setattr(client, "UI_COMMENT_CAP", 3)
    calls = comments_provider(client, [comment(i) for i in range(10)])
    result = client.issue_comments_complete("DL-1")
    assert not result["complete"] and result["partial"]
    assert result["incompleteReason"] == "cap_exceeded" and result["returned"] == 3
    assert result["fetchedAt"] is None and calls == [0]
    assert client.cache.get_stale(f"comments:{client.env}:DL-1:full") is None


def test_oversized_comment_is_refused_before_html_projection_and_can_be_skipped_explicitly(client, monkeypatch):
    monkeypatch.setattr(client, "UI_COMMENT_MAX_ROW_BYTES", 1000)
    rows = [comment(0, "x" * 2000), comment(1)]
    comments_provider(client, rows)
    # The projection helper owns the source-size guard. Observe the expensive
    # parser boundary rather than replacing that guard with a failing stub.
    with monkeypatch.context() as guard:
        guard.setattr("app.jira.jira_client.sanitize_html", lambda *_a, **_kw: pytest.fail("oversized HTML sanitized"))
        guard.setattr("app.content.wikihtml.wiki_to_html", lambda *_a, **_kw: pytest.fail("oversized wiki parsed"))
        result = client.issue_comments_page("DL-1", max_results=1)
    assert not result["complete"] and not result["pageComplete"]
    assert result["comments"][0]["bodyTooLarge"] and result["comments"][0]["html"] == ""
    assert result["skipped"][0]["id"] == "0"
    assert result["hasMore"] and result["nextStartAt"] == 1
    next_page = client.issue_comments_page("DL-1", start_at=1, max_results=1)
    assert next_page["comments"][0]["id"] == "1" and not next_page["hasMore"]
    assert not next_page["complete"] and next_page["pageComplete"]


def test_ui_collection_checks_pressure_between_comments(client, monkeypatch):
    comments_provider(client, [comment(i) for i in range(4)])
    checks = [0]
    def pressure():
        checks[0] += 1
        if checks[0] >= 3:
            raise ResourceBusy("pressure")
    monkeypatch.setattr("app.jira.jira_client.resource_pressure.check", pressure)
    result = client.comment_snapshot("DL-1", _for_ui=True)
    assert not result["complete"] and result["incompleteReason"] == "resource_pressure"
    assert result["returned"] < 4


def test_ui_collection_has_cooperative_time_budget(client, monkeypatch):
    clock = [0]
    monkeypatch.setattr("app.jira.jira_client.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(client, "UI_COMMENT_SECONDS", 1)
    comments_provider(client, [comment(i) for i in range(4)])
    project = client._project_comment_row
    def slow(row, **kwargs):
        result = project(row, **kwargs)
        clock[0] += 2
        return result
    monkeypatch.setattr(client, "_project_comment_row", slow)
    result = client.comment_snapshot("DL-1", _for_ui=True)
    assert not result["complete"] and result["incompleteReason"] == "time_budget"
    assert result["returned"] <= 1


def test_ui_comment_byte_budget_is_shared_across_rows_and_page_continuation(client, monkeypatch):
    monkeypatch.setattr(client, "UI_COMMENT_MAX_BYTES", 3000)
    comments_provider(client, [comment(i, "x" * 2000) for i in range(3)])
    result = client.comment_snapshot("DL-1", _for_ui=True)
    assert not result["complete"] and result["incompleteReason"] == "byte_budget"
    assert result["returned"] == 1
    page = client.issue_comments_page("DL-1", max_results=3)
    assert not page["pageComplete"] and page["incompleteReason"] == "byte_budget"
    assert page["returned"] == 1 and page["nextStartAt"] == 1


def test_rich_comment_images_are_lazy_only_in_ui_projection(client):
    raw = {**comment(1), "renderedBody": '<p><img src="/picture.png"></p>'}
    assert 'loading="lazy"' in client._project_comment_row(raw)["html"]
    assert 'decoding="async"' in client._project_comment_row(raw)["html"]
    assert 'loading="lazy"' not in client._project_comment_row(raw, proxy_media=False)["html"]


def test_huge_description_is_omitted_before_view_transform(client, monkeypatch):
    monkeypatch.setattr(client, "UI_DESCRIPTION_MAX_BYTES", 1000)
    raw = {"key": "DL-1", "fields": {"summary": "Large", "description": "x" * 2000},
           "renderedFields": {"description": "<p>" + "x" * 2000 + "</p>"}}
    monkeypatch.setattr(client, "_get_issue_view", lambda *a, **kw: raw)
    view = client.ticket_view("DL-1")
    assert view["descriptionTooLarge"] and view["descriptionHtml"] == ""
    assert view["descriptionSections"] == [] and view["descriptionUrl"].endswith("/browse/DL-1")
    assert len(raw["fields"]["description"]) == 2000  # Shared source is not modified.


def test_description_read_images_are_lazy_without_changing_editor_html(client, monkeypatch):
    raw = {"key": "DL-1", "fields": {"summary": "Image", "description": "image"},
           "renderedFields": {"description": '<p><img src="/picture.png"></p>'}}
    monkeypatch.setattr(client, "_get_issue_view", lambda *a, **kw: raw)
    view = client.ticket_view("DL-1")
    assert 'loading="lazy"' in view["descriptionHtml"]
    assert 'loading="lazy"' not in view["descriptionEditHtml"]


@pytest.mark.parametrize("fresh", [False, True])
def test_oversized_view_source_bypasses_both_raw_cache_copies(client, monkeypatch, fresh):
    monkeypatch.setattr(client, "UI_ISSUE_SOURCE_MAX_BYTES", 1000)
    monkeypatch.setattr(client, "UI_DESCRIPTION_MAX_BYTES", 1000)
    raw = {"key": "DL-1", "fields": {"summary": "Large", "description": "x" * 2000}}
    client._provider.get_json = lambda *a, **kw: raw
    assert client._get_issue_view("DL-1", fresh=fresh) is raw
    view = client.ticket_view("DL-1", fresh=fresh)
    assert view["descriptionTooLarge"]
    assert client.cache.get_stale(f"issue:{client.env}:DL-1") is None
    assert client.cache.get_stale(f"issueview:{client.env}:DL-1") is None
    assert len(raw["fields"]["description"]) == 2000
