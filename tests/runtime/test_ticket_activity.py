"""Local read state reuses known Jira timestamps without fetching or renewing cache data."""
from datetime import datetime, timezone
from types import SimpleNamespace

import anyio
import httpx2
import pytest
from fastapi import FastAPI

from app.infra.cache import Cache
from app.infra.cache_policy import CacheHeadersMiddleware, revalidating
from app.jira.jira_client import JiraClient
from app.routes.resources import build_resource_router
from app.services.ticket_activity import RECENT_WINDOW_MS, mark_ticket_viewed, ticket_activity


NOW = 1793847600000


def iso(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr("app.services.ticket_activity.time.time", lambda: NOW / 1000)
    cache = Cache(":memory:")
    # These APIs must work with expired auth, an offline provider and no current user alike.
    class LocalClient:
        env = "prod"

        def __getattr__(self, name):
            raise AssertionError(f"activity must not access Jira/auth: {name}")

    settings = SimpleNamespace(jira_base="https://jira.example", project_key="DL", app_port=4457)
    yield LocalClient(), settings, cache
    cache.close()


def test_metadata_uses_all_known_projections_and_comment_edits_without_revalidation(context):
    client, settings, cache = context
    for kind, offset in (("issue", 4000), ("issueL", 3000), ("issueview", 2000)):
        cache.set(f"{kind}:prod:DL-1", {"fields": {"updated": iso(NOW - offset)}}, 600)
    cache.set("comments:prod:DL-1", [{"date": iso(NOW - RECENT_WINDOW_MS - 1),
                                     "updated": iso(NOW - 1000)}], 600)
    before = cache.get_stale_at("issue:prod:DL-1")[1]
    cache.revalidator = lambda *_a: pytest.fail("metadata may not start revalidation")
    cache.always_revalidate = ("issue:", "comments:")
    result = ticket_activity(client, settings, cache, ["dl-1", "DL-1", "DL-2"])
    assert result["observedAt"] == NOW
    assert result["tickets"] == {
        "DL-1": {"lastViewedAt": None, "latestRelevantAt": NOW - 1000, "latestCommentAt": NOW - 1000},
        "DL-2": {"lastViewedAt": None, "latestRelevantAt": None, "latestCommentAt": None},
    }
    assert cache.get_stale_at("issue:prod:DL-1")[1] == before


@pytest.mark.parametrize("timestamp,expected", [
    (iso(NOW), NOW), (iso(NOW - RECENT_WINDOW_MS), NOW - RECENT_WINDOW_MS),
    (iso(NOW - RECENT_WINDOW_MS - 1), None), (iso(NOW + 1), None),
    ("not-a-date", None), ("2026-11-05", None), (None, None),
])
def test_recent_boundary_and_invalid_timestamps(context, timestamp, expected):
    client, settings, cache = context
    cache.set("issueL:prod:DL-1", {"fields": {"updated": timestamp}}, 600)
    assert ticket_activity(client, settings, cache, ["DL-1"])["tickets"]["DL-1"]["latestRelevantAt"] == expected


def test_embedded_comments_and_dead_payloads(context):
    client, settings, cache = context
    cache.set("issue:prod:DL-1", {"fields": {"comment": {"comments": [
        {"created": iso(NOW - 2000), "updated": iso(NOW - 1000)}, None]}}}, 600)
    assert ticket_activity(client, settings, cache, ["DL-1"])["tickets"]["DL-1"]["latestCommentAt"] == NOW - 1000
    cache._conn.execute("UPDATE cache SET fetched_at=?", (NOW / 1000 - cache.dead_ttl - 1,))
    assert ticket_activity(client, settings, cache, ["DL-1"])["tickets"]["DL-1"]["latestRelevantAt"] is None


def test_read_watermark_survives_restart_clear_history_invalidation_and_late_ack(context, tmp_path):
    client, settings, _ = context
    path = tmp_path / "read-state.sqlite"
    with_cache = Cache(path)
    mark_ticket_viewed(client, settings, with_cache, "DL-1", NOW - 3000)
    assert mark_ticket_viewed(client, settings, with_cache, "dl-1", NOW - 4000)["lastViewedAt"] == NOW - 3000
    with_cache.forget_recent()
    with_cache.invalidate()
    with_cache.close()
    reopened = Cache(path)
    try:
        row = ticket_activity(client, settings, reopened, ["DL-1"])["tickets"]["DL-1"]
        assert row["lastViewedAt"] == NOW - 3000
        for alternative in (SimpleNamespace(**{**vars(settings), "jira_base": "https://other.example"}),
                            SimpleNamespace(**{**vars(settings), "project_key": "OTHER"})):
            assert ticket_activity(client, alternative, reopened, ["DL-1"])["tickets"]["DL-1"]["lastViewedAt"] is None
        assert ticket_activity(SimpleNamespace(env="local"), settings, reopened, ["DL-1"])["tickets"]["DL-1"]["lastViewedAt"] is None
    finally:
        reopened.close()


def test_comment_mutation_preserves_read_baseline_and_new_known_edit_remains_unread(context):
    client, settings, cache = context
    mark_ticket_viewed(client, settings, cache, "DL-1", NOW - 3000)
    cache.set("issue:prod:DL-1", {"fields": {"updated": iso(NOW - 3000)}}, 600)
    cache.set("comments:prod:DL-1", [{"date": iso(NOW - 3000)}], 600)
    JiraClient._invalidate_ticket_content(SimpleNamespace(cache=cache, env="prod"), "DL-1", comments=True)
    cache.set("comments:prod:DL-1", [{"date": iso(NOW - 3000), "updated": iso(NOW - 1000)}], 600)
    row = ticket_activity(client, settings, cache, ["DL-1"])["tickets"]["DL-1"]
    assert row["lastViewedAt"] == NOW - 3000 < row["latestCommentAt"] == NOW - 1000


def test_http_local_boundary_validation_and_two_clients_share_read_state(context):
    client, settings, cache = context
    app = FastAPI()
    app.include_router(build_resource_router(get_client=lambda: client, cache=cache, settings=settings))

    async def scenario():
        transport = httpx2.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1:4457") as first:
            headers = {"X-LTM-Local": "1"}
            body = {"key": "DL-1", "viewedThrough": NOW - 1000}
            for path, payload in (("activity", {"keys": ["DL-1"]}), ("viewed", body)):
                url = f"/api/tickets/{path}"
                assert (await first.post(url, json=payload)).status_code == 403
                assert (await first.post(url, json=payload, headers={**headers, "Origin": "https://evil.example"})).status_code == 403
            for invalid in (True, "123", -1, NOW + 1):
                assert (await first.post("/api/tickets/viewed", json={**body, "viewedThrough": invalid}, headers=headers)).status_code == 422
            assert (await first.post("/api/tickets/activity", json={"keys": ["../issue"]}, headers=headers)).status_code == 422
            assert (await first.post("/api/tickets/activity", json={"keys": ["DL-1"] * 501}, headers=headers)).status_code == 422
            assert (await first.post("/api/tickets/viewed", json=body, headers=headers)).status_code == 200
        async with httpx2.AsyncClient(transport=transport, base_url="http://localhost:4457") as second:
            response = await second.post("/api/tickets/activity", json={"keys": ["dl-1"]}, headers=headers)
            assert response.headers["Cache-Control"] == "no-store"
            assert response.json()["tickets"]["DL-1"]["lastViewedAt"] == NOW - 1000
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app, client=("10.0.0.2", 1)),
                                     base_url="http://127.0.0.1:4457") as remote:
            assert (await remote.post("/api/tickets/viewed", json=body, headers=headers)).status_code == 403
    anyio.run(scenario)


def test_comments_source_header_excludes_older_identity_and_preserves_strict_refresh(context):
    _, settings, cache = context
    cache.set("comments:prod:DL-1", [{"id": "1"}], 600)
    cache.set_if_fence("myself:prod", {"id": "me"}, 600, cache.fence(), fetched_at=NOW / 1000 - 300)
    calls = []
    def comments(key, limit, **kwargs):
        calls.append((key, limit, kwargs, revalidating()))
        rows = cache.get("comments:prod:DL-1")
        cache.get("myself:prod")
        return rows
    client = SimpleNamespace(issue_comments=comments,
                             comment_coverage=lambda *_a: {"complete": True, "total": 1})
    app = FastAPI()
    app.include_router(build_resource_router(get_client=lambda: client, cache=cache, settings=settings))
    app.add_middleware(CacheHeadersMiddleware, cache=cache)

    async def scenario():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                     base_url="http://127.0.0.1:4457") as http:
            response = await http.get(f"/api/issue/DL-1/comments?freshAfter={NOW - 1000}&revalidate=1")
            assert response.status_code == 200
            assert float(response.headers["X-LTM-Comments-Fetched-At"]) == NOW / 1000
            assert response.headers["X-LTM-Comments-Complete"] == "1"
            assert response.headers["X-LTM-Comments-Total"] == "1"
            assert float(response.headers["X-LTM-Fetched-At"]) == NOW / 1000 - 300
            assert calls == [("DL-1", 100, {"fresh_after": NOW - 1000}, True)]
            assert (await http.get(f"/api/issue/DL-1/comments?freshAfter={NOW + 1}")).status_code == 422
            assert len(calls) == 1
    anyio.run(scenario)
