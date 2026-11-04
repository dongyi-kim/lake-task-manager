"""Generic evidence: real comment pages, exact history and shared hierarchy caches."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from types import SimpleNamespace
import time

import pytest

from app.auth.base import PermissionDenied, SessionExpired
from app.infra.cache import Cache
from app.jira.evidence_service import get_evidence_page


@pytest.fixture
def evidence_client():
    cache = Cache(":memory:")
    client = SimpleNamespace(cache=cache, env="mock", s=SimpleNamespace(cache_ttl_seconds=600),
                             provider=SimpleNamespace(), _looks_anonymous=lambda _: False)
    yield client
    cache.close()


def history(identity="1", author=None):
    return {"id": identity, "created": "2026-11-01T00:00:00.123+0900",
            "author": author or {"name": "u02", "displayName": "Full Person (TEAM)"},
            "items": [{"field": "status", "fieldId": "status", "from": "3", "fromString": "Active",
                       "to": "5", "toString": "Resolved"}]}


def test_comments_real_rest_pages_have_no_ui_or_snapshot_cap(build_env):
    env = build_env()
    key = env.client.create_child(None, "Task", "evidence pages")["key"]
    for number in range(205):
        env.provider.post_json(f"/rest/api/2/issue/{key}/comment", json_body={"body": f"Evidence {number}"})
    # Existing UI deliberately shows only 20. Generic evidence must keep paging past 200.
    assert len(env.client.issue_comments(key, limit=1000)) == 20
    rows, start = [], 0
    while True:
        page = get_evidence_page(env.client, key, "comments", start, 73)
        assert page["complete"] and page["total"] == 205
        rows.extend(page["items"])
        if not page["hasMore"]:
            break
        assert page["nextStartAt"] > start
        start = page["nextStartAt"]
    assert len(rows) == len({row["id"] for row in rows}) == 205
    assert {row["body"] for row in rows} == {f"Evidence {number}" for number in range(205)}
    assert all(row["created"] and row["authorId"] and row["author"]["displayName"] for row in rows)
    before = len(env.calls)
    get_evidence_page(env.client, key, "comments", 0, 73)
    assert len(env.calls) == before
    env.client.add_comment(key, "Added through the UI command path")
    assert get_evidence_page(env.client, key, "comments", 0, 73)["total"] == 206


def test_history_preserves_exact_source_values_and_pages_one_shared_snapshot(evidence_client):
    client, calls = evidence_client, []
    original = [history(), history("2", {"displayName": "Display Only"})]
    def fetch(path, params):
        calls.append((path, params))
        return {"changelog": {"startAt": 0, "total": 2, "histories": deepcopy(original)}}
    client.provider.get_json = fetch
    first = get_evidence_page(client, "dl-12", "history", 0, 1)
    second = get_evidence_page(client, "DL-12", "history", 1, 1)
    assert len(calls) == 1
    assert first["complete"] and first["hasMore"] and first["nextStartAt"] == 1
    assert first["items"][0]["created"] == original[0]["created"]
    assert first["items"][0]["items"] == original[0]["items"]
    assert first["items"][0]["authorDisplayName"] == "Full Person (TEAM)"
    assert second["items"][0]["authorId"] is None
    assert second["availableTotal"] == second["total"] == 2 and not second["hasMore"]
    client.cache.invalidate("changelog:mock:DL-12")
    get_evidence_page(client, "DL-12", "history")
    assert len(calls) == 2


@pytest.mark.parametrize("metadata", [{"startAt": 0, "total": 50}, {}, {"startAt": 20, "total": 21}])
def test_partial_history_never_claims_coverage_or_becomes_cached(evidence_client, metadata):
    client, calls = evidence_client, []
    def fetch(*args, **kwargs):
        calls.append(1)
        return {"changelog": {**metadata, "histories": [history()]}}
    client.provider.get_json = fetch
    for _ in range(2):
        result = get_evidence_page(client, "DL-12", "history")
        assert result["partial"] and not result["sourceComplete"]
        assert result["availableTotal"] == 1 and not result["hasMore"]
        assert result["incompleteReason"] == "changelog_expansion_incomplete"
    assert len(calls) == 2


@pytest.mark.parametrize("payload", [
    {"startAt": 0, "total": 3, "comments": []},
    {"startAt": 1, "total": 1, "comments": [{"id": "1"}]},
    {"comments": [{"id": "1"}]},
])
def test_unproven_comment_pages_are_partial_and_not_cached(evidence_client, payload):
    client, calls = evidence_client, []
    def fetch(*args, **kwargs):
        calls.append(1)
        return deepcopy(payload)
    client.provider.get_json = fetch
    for _ in range(2):
        result = get_evidence_page(client, "DL-12")
        assert result["partial"] and result["nextStartAt"] is None
    assert len(calls) == 2


@pytest.mark.parametrize("error", [SessionExpired("expired"), PermissionDenied("/issue/DL-12")])
def test_evidence_auth_and_permission_are_not_cached_empty(evidence_client, error):
    def fail(*args, **kwargs):
        raise error
    evidence_client.provider.get_json = fail
    with pytest.raises(type(error)):
        get_evidence_page(evidence_client, "DL-12")
    assert evidence_client.cache.get("comments:mock:DL-12:evidence:v1:0:100") is None


def test_write_fence_stops_inflight_evidence_from_repopulating_stale_cache(evidence_client):
    client, entered, release = evidence_client, Event(), Event()
    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {"startAt": 0, "total": 1, "comments": [{"id": "1", "body": "old"}]}
    client.provider.get_json = fetch
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(get_evidence_page, client, "DL-12")
        assert entered.wait(5)
        client.cache.invalidate("comments:mock:DL-12")
        release.set()
        assert future.result()["items"][0]["body"] == "old"
    assert client.cache.get("comments:mock:DL-12:evidence:v1:0:100") is None


def test_successful_edit_delete_and_field_writes_invalidate_evidence(build_env):
    env = build_env()
    key = env.client.create_child(None, "Task", "Before")["key"]
    env.client.add_comment(key, "Before comment")
    first = get_evidence_page(env.client, key)
    comment_id = first["items"][0]["id"]
    env.client.update_comment(key, comment_id, "Edited comment")
    assert get_evidence_page(env.client, key)["items"][0]["body"] == "Edited comment"
    env.client.delete_comment(key, comment_id)
    assert get_evidence_page(env.client, key)["total"] == 0
    previous = get_evidence_page(env.client, key, "history")
    env.client.update_fields(key, {"summary": "After"})
    assert env.cache.get(f"changelog:{env.client.env}:{key}:evidence:v1") is None
    # The stock jira820 fixture records transition history, but not summary edit history.
    get_evidence_page(env.client, key, "history")
    transition = next(item for item in env.client.transitions(key) if item["toCategory"] == "inprogress")
    env.client.do_transition(key, transition["id"])
    current = get_evidence_page(env.client, key, "history")
    assert current["total"] > previous["total"]
    assert any(change.get("toString") == transition["to"]
               for row in current["items"] for change in row["items"])


def test_incomplete_refresh_preserves_previous_source_age_and_recovers(evidence_client):
    client, rows = evidence_client, [history()]
    client.provider.get_json = lambda *args, **kwargs: {"changelog": {
        "startAt": 0, "total": 1, "histories": deepcopy(rows)}}
    assert get_evidence_page(client, "DL-12", "history")["complete"]
    cache_key = "changelog:mock:DL-12:evidence:v1"
    old_time = time.time() - 700
    with client.cache._lock:
        client.cache._conn.execute("UPDATE cache SET fetched_at=? WHERE key=?", (old_time, cache_key))
        client.cache._conn.commit()
    rows.clear()  # source claims one row but temporarily returns none
    assert get_evidence_page(client, "DL-12", "history")["partial"]
    previous, fetched_at = client.cache.get_stale_at(cache_key)
    assert fetched_at == old_time and len(previous["items"]) == 1
    rows.append(history("2"))
    assert get_evidence_page(client, "DL-12", "history")["items"][0]["id"] == "2"
