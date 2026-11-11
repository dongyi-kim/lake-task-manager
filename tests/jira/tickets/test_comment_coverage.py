"""Dialog coverage finds edited old comments beyond the short list, without per-card reads."""
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.auth.base import PRIO_BACKGROUND, upstream_priority
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient
from app.services.ticket_activity import ticket_activity
from app.infra.cache_policy import CachePolicy, read_scope


@pytest.fixture
def comments_client(monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    client = JiraClient(get_settings(), Cache(":memory:"))
    client.env = "prod"  # Exercise authenticated media rewriting with only the fake provider below.
    stamp = lambda days: datetime.fromtimestamp(clock[0] - days * 86400, timezone.utc).isoformat()
    rows = [{"id": str(index), "created": stamp(10 + index), "updated": stamp(10 + index),
             "body": f"Comment {index}", "author": {"name": "test.ui01"}} for index in range(1, 251)]
    rows[-1].update(updated=stamp(0.01), renderedBody=(
        '<p>Edited old comment<img src="' + client.s.jira_base + '/secure/attachment/1/picture.png"></p>'))
    calls, fault = [], [None]
    def get_json(path, params=None):
        assert path == "/rest/api/2/issue/DL-9007/comment"
        start, size = params.get("startAt", 0), params["maxResults"]
        calls.append((start, size, upstream_priority()))
        if fault[0] == "network" and start:
            raise ConnectionError("offline")
        page = rows[start:start + size]
        total = len(rows)
        if fault[0] == "duplicate" and start:
            page = rows[:size]
        if fault[0] == "changed_total" and start:
            total += 1
        return {"startAt": start, "total": total, "comments": page}
    client._provider = SimpleNamespace(get_json=get_json, close=lambda: None)
    client._provider_built = True
    yield client, rows, calls, fault, clock
    client.close()
    client.cache.close()


def test_dialog_pages_all_comments_in_background_and_reuses_complete_snapshot(comments_client):
    client, _, calls, _, _ = comments_client
    short = client._issue_comments("DL-9007", 100)
    assert len(short) == 20 and not any(row["id"] == "250" for row in short)
    assert client.comment_coverage("DL-9007", short) == {"complete": False, "total": 250}
    client.cache.set(f"myself:{client.env}", {"id": "test.ui01"}, 600)
    full = client.issue_comments_complete("DL-9007")
    assert full["complete"] and full["returned"] == full["total"] == 250
    assert calls[1:] == [(0, 100, PRIO_BACKGROUND), (100, 100, PRIO_BACKGROUND), (200, 100, PRIO_BACKGROUND)]
    edited = next(row for row in full["comments"] if row["id"] == "250")
    assert edited["mine"] is True
    assert all("mine" not in row for row in client.cache.get_stale(f"comments:{client.env}:DL-9007:full")["comments"])
    assert '/api/img?u=' in edited["html"]
    assert client.issue_comments_complete("DL-9007") == full
    assert len(calls) == 4
    # New per-card metadata inspects this already-loaded full snapshot without fetching Jira.
    known = ticket_activity(client, client.s, client.cache, ["DL-9007"])["tickets"]["DL-9007"]
    assert known["latestCommentAt"] == int(datetime.fromisoformat(edited["updated"]).timestamp() * 1000)
    assert len(calls) == 4
    # Evidence/MCP callers retain their original bounded contract.
    evidence = client.comment_snapshot("DL-9007")
    assert evidence["returned"] == 200 and not evidence["complete"]
    assert evidence["incompleteReason"] == "cap_exceeded"
    assert all(size == 50 for _, size, _ in calls[4:])


def test_full_snapshot_source_age_is_first_page_start_not_last_page_finish(comments_client):
    client, _, calls, _, clock = comments_client
    original = client._provider.get_json
    def advancing(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] += 2
        return result
    client._provider.get_json = advancing
    started = clock[0]
    first = client.issue_comments_complete("DL-9007")
    assert clock[0] == started + 6
    assert first["fetchedAt"] == client.cache.metadata(f"comments:{client.env}:DL-9007:full")["fetchedAt"] == started
    # A detail update during pagination requires a new complete read, even though the
    # previous snapshot finished after that update. It must not become a cache hit.
    next_snapshot = client.issue_comments_complete("DL-9007", fresh_after=int((started + 3) * 1000))
    assert next_snapshot["complete"] and next_snapshot["fetchedAt"] == started + 6
    assert len(calls) == 6


def test_short_complete_list_proves_coverage_without_an_extra_request(comments_client):
    client, rows, calls, _, _ = comments_client
    del rows[3:]
    short = client._issue_comments("DL-9007", 100)
    assert client.comment_coverage("DL-9007", short) == {"complete": True, "total": 3}
    assert client.issue_comments_complete("DL-9007")["comments"] == short
    assert len(calls) == 1
    # Truncating the caller's visible list cannot inherit the full-list proof.
    assert not client.comment_coverage("DL-9007", short[:1])["complete"]


@pytest.mark.parametrize("mode", ["live", "hot", "warm", "cool"])
def test_explicit_full_comments_accepts_actual_paginated_upstream_proof(comments_client, mode):
    client, _, calls, _, clock = comments_client
    client.cache.configure(CachePolicy())
    with read_scope(cache_mode=mode) as sources:
        result = client.issue_comments_complete("DL-9007")
    assert result["complete"] and result["returned"] == 250
    assert len(calls) == 3
    assert sources[-1]["compliant"] and sources[-1]["upstreamValidated"]
    assert result["fetchedAt"] == clock[0]


@pytest.mark.parametrize("mode,age,requests", [("live", 0, 1), ("hot", 601, 1),
    ("warm", 7200, 0), ("warm", 86401, 1), ("cool", 172800, 0)])
def test_explicit_complete_short_cache_hit_checks_both_age_and_coverage(comments_client, mode, age, requests):
    client, rows, calls, _, clock = comments_client
    client.cache.configure(CachePolicy())
    del rows[3:]
    client._issue_comments("DL-9007", 100)
    before = len(calls)
    original = clock[0]
    clock[0] += age
    with read_scope(cache_mode=mode) as sources:
        result = client.issue_comments_complete("DL-9007")
    assert result["complete"] and len(calls) - before == requests
    assert result["fetchedAt"] == (clock[0] if requests else original)
    assert all(row.get("compliant") for row in sources)


def test_explicit_short_list_without_matching_coverage_fetches_full_snapshot(comments_client):
    client, rows, calls, _, _ = comments_client
    client.cache.configure(CachePolicy())
    del rows[3:]
    client._issue_comments("DL-9007", 100)
    client.cache.forget(f"comments:{client.env}:DL-9007:coverage")
    with read_scope(cache_mode="warm"):
        assert client.issue_comments_complete("DL-9007")["complete"]
    assert len(calls) == 2


@pytest.mark.parametrize("mode", ["live", "hot"])
def test_explicit_complete_comments_preserves_auth_failure_without_cache_fallback(comments_client, mode):
    from app.auth.base import SessionExpired
    client, rows, _, _, clock = comments_client
    client.cache.configure(CachePolicy())
    del rows[3:]
    client._issue_comments("DL-9007", 100)
    clock[0] += 601
    def expired(*_args, **_kwargs):
        raise SessionExpired("expired")
    client._provider.get_json = expired
    with read_scope(cache_mode=mode), pytest.raises(SessionExpired):
        client.issue_comments_complete("DL-9007")


@pytest.mark.parametrize("mode", ["live", "hot", "warm", "cool"])
def test_explicit_preview_is_complete_for_its_requested_window_not_entire_thread(comments_client, mode):
    client, _, calls, _, _ = comments_client
    client.cache.configure(CachePolicy())
    with read_scope(cache_mode=mode) as sources:
        preview = client._issue_comments("DL-9007", 100)
        coverage = client.comment_coverage("DL-9007", preview)
    assert len(preview) == 20 and len(calls) == 1
    assert not coverage["complete"]
    assert sources[-1]["compliant"] and sources[-1]["upstreamValidated"]


def test_ui_comment_permission_loss_removes_complete_fallback(comments_client):
    from app.auth.base import PermissionDenied
    client, _, _, _, _ = comments_client
    client.cache.configure(CachePolicy())
    client.issue_comments_complete("DL-9007")
    def denied(*_args, **_kwargs):
        raise PermissionDenied("denied")
    client._provider.get_json = denied
    with read_scope(cache_mode="live"), pytest.raises(PermissionDenied):
        client.issue_comments_complete("DL-9007")
    assert client.cache.get_alive(f"comments:{client.env}:DL-9007:full") is None


@pytest.mark.parametrize("page", [
    {"comments": [], "total": None, "startAt": 0},
    {"comments": [], "total": 3, "startAt": 0},
    {"comments": [], "total": 0, "startAt": 1},
    {"errorMessages": ["temporarily unavailable"]},
])
def test_explicit_preview_rejects_unknown_or_incomplete_requested_page(comments_client, page):
    client, _, _, _, _ = comments_client
    client._provider.get_json = lambda *_args, **_kwargs: page
    with read_scope(cache_mode="live"), pytest.raises(RuntimeError):
        client._issue_comments("DL-9007", 100)
    assert client.cache.get(f"comments:{client.env}:DL-9007") is None


def test_live_full_comments_retry_intervening_mutation_before_claiming_complete(comments_client):
    client, _, calls, _, clock = comments_client
    client.cache.configure(CachePolicy())
    original = client._provider.get_json
    changed = []
    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs["params"]["startAt"] == 100 and not changed:
            changed.append(1)
            clock[0] += 10
            client.cache.invalidate(f"comments:{client.env}:DL-9007")
        return result
    client._provider.get_json = racing
    with read_scope(cache_mode="live") as sources:
        result = client.issue_comments_complete("DL-9007")
    assert result["complete"] and result["fetchedAt"] == clock[0]
    assert len(calls) == 6
    assert sources[-1]["compliant"] and sources[-1]["upstreamValidated"]


@pytest.mark.parametrize("failure", ["network", "duplicate", "changed_total"])
def test_partial_refresh_keeps_old_complete_data_and_mutation_expires_both_caches(comments_client, failure):
    client, _, calls, fault, clock = comments_client
    client._issue_comments("DL-9007", 100)
    first = client.issue_comments_complete("DL-9007")
    cache_key = f"comments:{client.env}:DL-9007:full"
    previous = client.cache.get_stale_at(cache_key)
    fault[0] = failure
    clock[0] += 2
    failed = client.issue_comments_complete("DL-9007", fresh_after=int(clock[0] * 1000) - 1000)
    assert not failed["complete"] and failed["partial"] and failed["fetchedAt"] is None
    assert client.cache.get_stale_at(cache_key) == previous
    assert first["fetchedAt"] == previous[1]
    # Each malformed pagination terminates on the next page, rather than looping.
    assert len(calls) == 6
    client._invalidate_ticket_content("DL-9007", comments=True)
    for suffix in ("", ":coverage", ":full"):
        assert client.cache.get_stale(f"comments:{client.env}:DL-9007{suffix}") is None


@pytest.mark.parametrize("when", ["while_loading", "after_publish"])
def test_full_snapshot_never_borrows_newer_cached_coverage(comments_client, when):
    client, _, _, _, clock = comments_client
    started = clock[0]
    cache_key = f"comments:{client.env}:DL-9007:full"
    newer = {"comments": [{"id": "new-snapshot"}], "complete": True}
    def prime_newer():
        clock[0] += 20
        client.cache.invalidate(cache_key)
        client.cache.set(cache_key, newer, 600)
    if when == "while_loading":
        original = client._provider.get_json
        def racing(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs["params"].get("startAt") == 200:
                prime_newer()
            return result
        client._provider.get_json = racing
    else:
        client.cache.on_upstream_ok = prime_newer
    returned = client.issue_comments_complete("DL-9007")
    assert len(returned["comments"]) == 250
    assert client.cache.get(cache_key) == newer
    if when == "while_loading":
        assert returned["partial"] and not returned["complete"] and returned["fetchedAt"] is None
    else:
        assert returned["complete"] and returned["fetchedAt"] == started
    assert client.cache.metadata(cache_key)["fetchedAt"] == started + 20
