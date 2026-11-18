import time
from types import SimpleNamespace

import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import read_scope
from app.infra.settings import get_settings
from app.jira.body_search import ticket_field_context
from app.jira.jira_client import JiraClient


@pytest.fixture
def client():
    cache = Cache(":memory:")
    client = JiraClient(get_settings(), cache)
    client.env = "mock"
    client._provider = SimpleNamespace(get_json=lambda *a, **kw: pytest.fail("unexpected REST"), close=lambda: None)
    client._provider_built = True
    yield client
    client.close()
    cache.close()


@pytest.mark.parametrize("detail", ["summary", "read", "edit", "full"])
def test_explicit_description_off_uses_only_bounded_metadata(client, monkeypatch, detail):
    calls = []
    def search(_client, _query, **options):
        calls.append(options)
        return {"issues": [{"key": "DL-1", "fields": {"summary": "small", "updated": "v1"}}]}
    monkeypatch.setattr("app.jira.body_search.search_body_page", search)
    monkeypatch.setattr(client, "_get_issue_view", lambda *a, **kw: pytest.fail("canonical body decode"))
    view = client.ticket_view("DL-1", detail=detail, include_description=False)
    assert view["summary"] == "small"
    assert not any(key.startswith("description") for key in view)
    assert not {"description", "comment", "attachment", "issuelinks"}.intersection(calls[0]["fields"].split(","))


def test_summary_explicit_description_on_and_raw_comments_preserve_all_rows(client, monkeypatch):
    rows = [{"id": str(i), "body": "unmodified"} for i in range(25)]
    def search(_client, _query, **options):
        assert {"description", "comment"}.issubset(options["fields"].split(","))
        return {"issues": [{"key": "DL-1", "fields": {"summary": "s", "description": "body", "comment": {
            "comments": rows, "total": 100, "startAt": 0}}}]}
    monkeypatch.setattr("app.jira.body_search.search_body_page", search)
    view = client.ticket_view("DL-1", detail="summary", include_description=True, include_comments=True)
    assert "body" in view["descriptionHtml"] and "descriptionEditHtml" not in view
    assert view["comments"] is rows and len(view["comments"]) == 25
    assert view["commentsRequested"] and view["commentsMissingKeys"] == []
    assert not view["commentsCoverage"]["complete"]


def seed_comments(client, count=25, *, total=None, fetched_at=None):
    rows = [{"id": str(i), "body": f"comment {i}", "created": f"2026-11-{i+1:02}T00:00:00+0000"} for i in range(count)]
    value = {"comments": rows, "total": count if total is None else total, "startAt": 0}
    ticket_field_context(client).caches["comment"].store(
        [{"key": "DL-1", "fields": {"updated": "v1", "comment": value}}], fetched_at or time.time())
    return rows


def test_complete_raw_cache_bridges_preview_dialog_and_evidence_without_rest(client):
    from app.jira.evidence_service import _comments_page
    started = time.time() - 4
    rows = seed_comments(client, fetched_at=started)
    with read_scope() as sources:
        short = client._issue_comments("DL-1", 100)
        full = client.issue_comments_complete("DL-1")
        evidence = _comments_page(client, "DL-1", 20, 10)
    assert len(short) == 20 and short[0]["id"] == "24"
    assert full["complete"] and full["returned"] == 25 and full["fetchedAt"] == started
    assert [row["id"] for row in evidence["items"]] == [row["id"] for row in rows[20:]]
    assert not evidence["hasMore"] and evidence["sourceComplete"]
    assert sources and all(row["fetchedAt"] == started for row in sources)


def test_partial_raw_cache_does_not_replace_complete_comment_reader(client):
    seed_comments(client, 2, total=100)
    with pytest.raises(BaseException, match="unexpected REST"):
        client._issue_comments("DL-1")


def test_single_ticket_cold_combined_warm_zero_and_independent_missing_field(client, monkeypatch):
    calls = []
    raw_fields = {"summary": "s", "updated": "v1", "description": "body", "comment": {
        "comments": [{"id": "1", "body": "raw", "created": "2026-11-18T00:00:00+0000"}], "startAt": 0, "total": 1}}
    def fetch(path, params=None, **kwargs):
        assert path == "/rest/api/2/search"
        requested = params["fields"].split(",")
        calls.append(requested)
        return {"issues": [{"key": "DL-1", "fields": {
            k: v for k, v in raw_fields.items() if k in requested}}], "total": 1,
            "startAt": 0, "maxResults": params["maxResults"]}
    client._provider.get_json = fetch
    first = client.ticket_view("DL-1", include_description=True)
    assert first["descriptionRequested"] and first["descriptionMissingKeys"] == []
    assert len(calls) == 1 and "description" in calls[0] and "comment" not in calls[0]
    second = client.ticket_view("DL-1", include_description=True, include_comments=True)
    assert len(calls) == 2 and "comment" in calls[1] and "description" not in calls[1]
    assert second["commentsCoverage"]["complete"]
    again = client.ticket_view("DL-1", include_description=True, include_comments=True)
    assert again == second and len(calls) == 2


@pytest.mark.parametrize("change", ["mutation", "session", "live", "fresh_after"])
def test_comment_bridge_rejects_unusable_or_stale_source(client, change):
    from app.jira.comment_snapshot import cached_complete_comments
    started = time.time() - 5
    seed_comments(client, 1, fetched_at=started)
    if change == "mutation":
        client.advance_jql_generation()
    elif change == "session":
        client._provider_generation += 1
    with read_scope(cache_mode="live" if change == "live" else None) as sources:
        result = cached_complete_comments(client, "DL-1", fresh_after=(started+1)*1000 if change == "fresh_after" else None)
    assert result is None and sources == []


@pytest.mark.parametrize("detail", ["read", "edit"])
def test_explicit_rich_wiki_description_preserves_render_and_editor_structure(client, monkeypatch, detail):
    wiki = "h2. Heading\n||A||B||\n|one|two|\n[~alice]"
    monkeypatch.setattr(client, "_description_fmt", lambda: "wiki")
    monkeypatch.setattr("app.jira.body_search.search_body_page", lambda *a, **kw: {
        "issues": [{"key": "DL-1", "fields": {"description": wiki}}]})
    view = client.ticket_view("DL-1", include_description=True, detail=detail)
    html = view["descriptionEditHtml" if detail == "edit" else "descriptionHtml"]
    assert "<h2>Heading</h2>" in html and "<table" in html
    assert ('data-id="alice"' if detail == "edit" else 'class="user-hover"') in html


@pytest.mark.parametrize("format,body,expected", [("wiki", "*bold*", "<strong>bold</strong>"),
    ("html", '<p><strong>bold</strong></p><script>bad()</script>', "<strong>bold</strong>")])
def test_raw_comments_bridge_renders_supported_source_formats(client, monkeypatch, format, body, expected):
    monkeypatch.setattr(client, "_comment_fmt", lambda: format)
    context = ticket_field_context(client)
    context.caches["comment"].store([{"key": "DL-1", "fields": {"updated": "v1", "comment": {
        "comments": [{"id": "1", "body": body, "created": "2026-11-18T00:00:00+0000"}],
        "startAt": 0, "total": 1}}}], time.time())
    html = client._issue_comments("DL-1")[0]["html"]
    assert expected in html and "<script" not in html


def test_html_config_plain_comment_preserves_line_breaks_and_literal_angles(client, monkeypatch):
    monkeypatch.setattr(client, "_comment_fmt", lambda: "html")
    row = client._project_comment_row({"id": "1", "body": "a < 3\nnext > 2"})
    assert row["html"] == "a &lt; 3<br>next &gt; 2"


def test_wiki_filename_images_fetch_only_bounded_attachment_metadata_and_keep_warm_cache(client):
    calls = []
    def fetch(path, params=None):
        assert path == "/rest/api/2/search"
        requested = params["fields"].split(",")
        calls.append(requested)
        values = {"summary": "s", "updated": "v1", "description": "!diagram.png!", "attachment": [
            {"filename": "diagram.png", "content": "https://jira.example/secure/attachment/123/diagram.png"}]}
        return {"issues": [{"key": "DL-1", "fields": {k:v for k,v in values.items() if k in requested}}],
                "total": 1, "startAt": 0, "maxResults": params["maxResults"]}
    client._provider.get_json = fetch
    view = client.ticket_view("DL-1", detail="edit", include_description=True)
    from urllib.parse import unquote
    assert "/secure/attachment/123/diagram.png" in unquote(view["descriptionEditHtml"])
    assert len(calls) == 2 and set(calls[1]) == {"key", "updated", "attachment"}
    assert client.ticket_view("DL-1", detail="edit", include_description=True) == view
    assert len(calls) == 2


def test_legacy_physical_fetch_seeds_shared_description_at_original_source_age(client, monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr("app.jira.jira_client.time.time", lambda: clock[0])
    original = clock[0]
    def fetch(path, params=None):
        assert path == "/rest/api/2/issue/DL-1"
        clock[0] += 3
        return {"key": "DL-1", "fields": {"summary": "legacy", "description": "body", "updated": "v1"}}
    client._provider.get_json = fetch
    assert client.ticket_view("DL-1")["summary"] == "legacy"
    with read_scope() as sources:
        values = ticket_field_context(client).caches["description"].read_latest(["DL-1"], 1024)
    assert values == {"DL-1": "body"}
    assert sources and all(row["fetchedAt"] == original for row in sources)
