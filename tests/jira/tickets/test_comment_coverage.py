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
