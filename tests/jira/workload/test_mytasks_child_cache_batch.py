"""A cumulative Task model reads cached parent memberships in one durable batch."""
from datetime import date

import pytest

from app.auth.base import UpstreamUnavailable
from app.domain import mytasks
from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, read_scope
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


def _row(key, subtasks=()):
    return {"key": key, "fields": {"summary": key,
        "issuetype": {"name": "Task", "subtask": False},
        "assignee": {"name": "me"}, "reporter": {"name": "me"},
        "status": {"name": "Open", "statusCategory": {"key": "new"}},
        "subtasks": [{"key": child} for child in subtasks]}}


@pytest.fixture
def parents(tmp_path, monkeypatch):
    clock = [100000.0]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    cache = Cache(tmp_path / "shared.sqlite3")
    # Open the second connection before any reads to expose an uncommitted read-owned write.
    writer = Cache(tmp_path / "shared.sqlite3")
    writer._conn.execute("PRAGMA busy_timeout=50")
    cache.configure(CachePolicy())
    client = JiraClient(get_settings(), cache)
    monkeypatch.setattr(client, "current_user", lambda: {"id": "me", "name": "Me"})
    monkeypatch.setattr(client, "s_today", lambda: date(2026, 11, 10))
    rows = [_row("DL-1"), _row("DL-2", ["DL-102"]), _row("DL-3"), _row("DL-4")]
    memberships = {"DL-1": ["DL-101"], "DL-2": [], "DL-3": [], "DL-4": ["DL-104"]}
    try:
        yield client, writer, clock, rows, memberships
    finally:
        client.close()
        cache.close()
        writer.close()


def _build(client, rows):
    return mytasks.build_my_tasks(mytasks._TaskRowsClient(client, rows, cache_only=True),
                                 user="me", defer_children=True, create_sync=False)


@pytest.mark.parametrize("mode,age", [(None, 120), ("hot", 120), ("warm", 650), ("live", 120)])
def test_warm_parent_batch_keeps_snapshot_and_proof_with_one_commit(parents, mode, age):
    client, writer, clock, rows, memberships = parents
    cache = client.cache
    with read_scope(cache_mode=mode):
        for key, children in memberships.items():
            cache.set_if_fence(client._direct_child_cache_key(key), children, 600, cache.fence())
        original = {key: cache.metadata(client._direct_child_cache_key(key)) for key in memberships}
        expected = _build(client, rows)
        clock[0] += age  # Real timestamps may differ; this must not depend on equal-time dedup.
        statements = []
        cache._conn.set_trace_callback(statements.append)
        try:
            with read_scope() as observed:
                actual = _build(client, rows)
        finally:
            cache._conn.set_trace_callback(None)
        assert actual == expected
        groups = {group["key"]: group for group in actual["groups"]}
        assert groups["DL-1"]["kidsTotal"] == 1 and groups["DL-1"]["childrenPending"]
        assert groups["DL-2"]["standalone"]  # Cached [] wins over supplied row's different children.
        assert actual["counts"]["total"] == 2
        sources = [source for source in observed if source["kind"] == "childkeys"]
        assert len(sources) == len(memberships)
        assert {source["rowIdentity"] for source in sources} == {
            source["rowIdentity"] for source in original.values()}
        assert all(source["fetchedAt"] == 100000.0 for source in sources)
        proof_by_identity = {source["rowIdentity"]: source["upstreamValidated"] for source in original.values()}
        assert all(source["upstreamValidated"] == proof_by_identity[source["rowIdentity"]] for source in sources)
        assert not cache._conn.in_transaction
        writer.set("user:after-batch", {"ok": True}, 600)
        recency = dict(writer._conn.execute("SELECT key,accessed_at FROM cache_access WHERE key LIKE 'childkeys:%'"))
        assert all(recency[client._direct_child_cache_key(key)] == clock[0] for key in memberships)
        assert sum(statement == "COMMIT" for statement in statements) == 1


def test_cached_empty_membership_and_per_parent_miss_failure_remain_distinct(parents, monkeypatch):
    client, _writer, clock, rows, memberships = parents
    client.cache.set(client._direct_child_cache_key("DL-2"), [], 600)
    client.cache.set(client._direct_child_cache_key("DL-3"), None, 600)
    # DL-1/DL-4 are known cached parents; only DL-3 has genuinely unavailable proof.
    client.cache.set_many_if_fence(
        ((f"issueL:{client.env}:{row['key']}", row) for row in rows
         if row["key"] in {"DL-1", "DL-4"}),
        600, client.cache.fence(), fetched_at=clock[0], upstream_validated=False)
    original = client.direct_child_keys
    called = []

    def fallback(key, **kwargs):
        called.append(key)
        if key == "DL-3":
            raise UpstreamUnavailable("unknown membership")
        return original(key, **kwargs)

    monkeypatch.setattr(client, "direct_child_keys", fallback)
    actual = _build(client, rows)
    assert {row["key"] for row in actual["loadErrors"]} == {"DL-3"}
    groups = {group["key"]: group for group in actual["groups"]}
    assert groups["DL-2"]["standalone"]
    assert groups["DL-3"]["childrenPending"]
    # The first miss discards the captured batch, so later cached parents are rechecked
    # through the scalar path. Its authoritative [] still wins over supplied subtasks.
    assert set(called) == set(memberships)


def test_new_live_scope_does_not_reuse_unvalidated_batch_entries(parents, monkeypatch):
    client, _writer, _clock, rows, memberships = parents
    for key, children in memberships.items():
        client.cache.set(client._direct_child_cache_key(key), children, 600)
    original_sources = {key: client.cache.metadata(client._direct_child_cache_key(key)) for key in memberships}
    original = client.direct_child_keys
    called = []
    requests = []

    def unavailable(path, **kwargs):
        requests.append(path)
        raise UpstreamUnavailable("independent parent validation unavailable")

    def fallback(key, **kwargs):
        called.append(key)
        return original(key, **kwargs)

    monkeypatch.setattr(client, "direct_child_keys", fallback)
    monkeypatch.setattr(client.provider, "get_json", unavailable)
    with read_scope(cache_mode="live"):
        actual = _build(client, rows)
    assert set(called) == set(memberships)
    assert requests == []  # Protected retained memberships fail closed before parent reads.
    groups = {group["key"]: group for group in actual["groups"]}
    # Merely supplying a row is not a fresh upstream proof against retained membership.
    assert {error["key"] for error in actual["loadErrors"]} == set(memberships)
    assert all(group["childrenPending"] and not group["standalone"] for group in groups.values())
    for key, children in memberships.items():
        cache_key = client._direct_child_cache_key(key)
        assert client.cache.get_stale_at(cache_key)[0] == children
        source = client.cache.metadata(cache_key)
        assert source["rowIdentity"] == original_sources[key]["rowIdentity"]
        assert source["fetchedAt"] == original_sources[key]["fetchedAt"]
        assert not source["upstreamValidated"]


def test_mutation_after_batch_read_rechecks_membership_through_existing_path(parents, monkeypatch):
    client, _writer, _clock, rows, memberships = parents
    cache = client.cache
    for key, children in memberships.items():
        cache.set(client._direct_child_cache_key(key), children, 600)
    original = cache.get_many
    changed = False

    def mutate_after_capture(keys, **kwargs):
        nonlocal changed
        captured = original(keys, **kwargs)
        if not changed and any(key.startswith("childkeys:") for key in captured):
            changed = True
            key = client._direct_child_cache_key("DL-2")
            cache.invalidate(key)
            cache.set_if_fence(key, ["DL-NEW"], 600, cache.fence())
        return captured

    monkeypatch.setattr(cache, "get_many", mutate_after_capture)
    actual = _build(client, rows)
    group = next(group for group in actual["groups"] if group["key"] == "DL-2")
    assert changed
    assert group["kidsTotal"] == 1 and group["childrenPending"]
    assert not group["standalone"]
