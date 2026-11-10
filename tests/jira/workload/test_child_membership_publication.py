"""Completed upstream JQL can publish known child memberships with canonical rows."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from app.auth.base import QueryStateChanged
from app.domain import mytasks
from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, read_scope
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


def _row(number):
    return {"key": f"DL-{number}", "fields": {"summary": f"Task {number}",
        "issuetype": {"name": "Task"}, "subtasks": [],
        "assignee": {"name": "me"}, "reporter": {"name": "me"},
        "status": {"name": "Open", "statusCategory": {"key": "new"}}}}


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    clock, rows, calls = [100000.0], [_row(number) for number in range(1, 41)], []
    cache = Cache(tmp_path / "cache.sqlite3")
    cache.configure(CachePolicy())
    client = JiraClient(get_settings(), cache)
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    monkeypatch.setattr(client, "current_user", lambda: {"id": "me", "name": "Me"})
    monkeypatch.setattr(client, "s_today", lambda: date(2026, 11, 10))

    def get_json(path, params):
        assert path == "/rest/api/2/search"
        calls.append(params["startAt"])
        clock[0] += 5
        start = params["startAt"]
        return {"issues": deepcopy(rows[start:start + 20]), "total": len(rows), "maxResults": 20}

    client._provider = SimpleNamespace(get_json=get_json, supports_parallel=False)
    client._provider_built = True
    try:
        yield client, clock, rows, calls
    finally:
        client.close()
        cache.close()


def _fetch(client):
    return client._fetch_exhaustive_jql("project = DL ORDER BY key ASC",
                                      client._issue_fields(light=True), True, 1000)


def _model(client, rows):
    return mytasks.build_my_tasks(mytasks._TaskRowsClient(client, rows, cache_only=True),
                                 user="me", defer_children=True, create_sync=False)


def test_cold_membership_publication_is_one_commit_with_same_complete_model(upstream):
    client, clock, rows, calls = upstream
    expected = _model(client, rows)
    client.cache.forget_prefixes(("childkeys:",))
    started = clock[0]
    statements = []
    client.cache._conn.set_trace_callback(statements.append)
    try:
        with read_scope(cache_mode="live"):
            assert _fetch(client) == rows
            memberships = [client._direct_child_cache_key(row["key"]) for row in rows]
            assert all(client.cache.get_stale_at(key) == ([], started) for key in memberships)
            assert sum(statement == "COMMIT" for statement in statements) == 1
            assert all(client.cache.metadata(key)["upstreamValidated"] for key in memberships)
            with read_scope() as observed:
                actual = _model(client, rows)
            assert actual == expected
            sources = [source for source in observed if source["kind"] == "childkeys"]
            assert len(sources) == len(rows)
            assert all(source["fetchedAt"] == started and source["upstreamValidated"] for source in sources)
            assert sum(statement == "COMMIT" for statement in statements) == 2
    finally:
        client.cache._conn.set_trace_callback(None)
    assert calls == [0, 20]
    assert not client.cache._conn.in_transaction


@pytest.mark.parametrize("invalid", [
    {}, {"issuetype": {"name": "Epic"}}, {"issuetype": {}}, {"issuetype": None},
    {"subtasks": None}, {"subtasks": [{}]}, {"subtasks": [None]},
    {"subtasks": [{"key": "   "}]}, {"subtasks": [{"key": 123}]},
])
def test_unknown_or_malformed_membership_is_not_primed(upstream, invalid):
    client, _clock, rows, _calls = upstream
    rows[:] = [_row(1), _row(2)]
    rows[0]["fields"].update(invalid)
    if not invalid:
        rows[0]["fields"].pop("subtasks")
    _fetch(client)
    assert client.cache.metadata(client._direct_child_cache_key("DL-1")) is None
    assert client.cache.get_stale_at(client._direct_child_cache_key("DL-2"))[0] == []


def test_valid_subtasks_are_ordered_deduplicated_and_share_collection_start(upstream):
    client, _clock, rows, _calls = upstream
    rows[:] = [_row(1)]
    rows[0]["fields"]["subtasks"] = [{"key": " dl-5 "}, {"key": "DL-4"}, {"key": "DL-5"}]
    _fetch(client)
    assert client.cache.get_stale_at(client._direct_child_cache_key("DL-1")) == (["DL-5", "DL-4"], 100000)


def test_invalidation_during_paging_prevents_both_issue_and_membership_publication(upstream):
    client, _clock, rows, calls = upstream
    original = client.provider.get_json

    def invalidated(path, params):
        result = original(path, params)
        if params["startAt"]:
            client.cache.invalidate("mutation-marker:")
        return result

    client._provider.get_json = invalidated
    with read_scope(cache_mode="live"):
        # This shared F fixture owns the no-publication invariant. B additionally rejects
        # the invalidated page (its strict typed rejection has a separate page-level test).
        try:
            fetched = _fetch(client)
        except QueryStateChanged:
            pass
        else:
            assert fetched == rows
    assert calls == [0, 20]
    assert client.cache.entries_by_prefix("issueL:", retained=True) == {}
    assert client.cache.entries_by_prefix("childkeys:", retained=True) == {}
    assert not client.cache._conn.in_transaction


def test_cached_projection_copy_does_not_prime_membership_as_fresh(upstream):
    client, clock, rows, calls = upstream
    row = rows[0]
    fields = client._issue_fields(light=True)
    client.cache.set_if_fence(f"issueL:{client.env}:{row['key']}", row, 600,
                              client.cache.fence(), fetched_at=clock[0] - 650, upstream_validated=False)
    with read_scope(cache_mode="warm"):
        assert client._hydrate_jql_leaf_rows([row["key"]], fields, True, client._jql_generation(),
                                             client._projection_signature(fields, True)) == [row]
    assert calls == []
    assert client.cache.metadata(client._direct_child_cache_key(row["key"])) is None


@pytest.mark.parametrize("connection", ["same", "other"])
@pytest.mark.parametrize("delta", [0, 5])
@pytest.mark.parametrize("partial_parent", [False, True])
def test_delayed_upstream_result_preserves_equal_or_newer_membership(upstream, connection, delta, partial_parent):
    client, clock, rows, _calls = upstream
    rows[:] = [_row(1)]
    rows[0]["fields"]["subtasks"] = [{"key": "DL-OLD"}]
    path = client.cache._conn.execute("PRAGMA database_list").fetchone()[2]
    other = Cache(path)  # Open before the upstream call; it may publish while that call waits.
    owner = client.cache if connection == "same" else other
    original = client.provider.get_json
    key = client._direct_child_cache_key("DL-1")
    newer = []

    def completed_after_replacement(path, params):
        result = original(path, params)
        owner.set_if_fence(key, ["DL-NEW"], 600, owner.fence(), fetched_at=100000 + delta)
        newer.append(client.cache.metadata(key))
        return result

    client._provider.get_json = completed_after_replacement
    try:
        with read_scope(cache_mode="live"):
            _fetch(client)
            assert client.cache.get_stale_at(key) == (["DL-NEW"], 100000 + delta)
            source = client.cache.metadata(key)
            assert source["rowIdentity"] == newer[0]["rowIdentity"]
            assert source["fetchedAt"] == newer[0]["fetchedAt"]
            assert source["upstreamValidated"] == newer[0]["upstreamValidated"]
            assert source["upstreamValidated"] == (connection == "same")
            supplied = deepcopy(rows)
            if partial_parent:
                supplied[0]["fields"].pop("subtasks")
            model = _model(client, supplied)
            assert client.cache.get_stale_at(key) == (["DL-NEW"], 100000 + delta)
            after_model = client.cache.metadata(key)
            assert after_model["rowIdentity"] == newer[0]["rowIdentity"]
            assert after_model["upstreamValidated"] == newer[0]["upstreamValidated"]
            if connection == "other":
                assert any(error["key"] == "DL-1" for error in model.get("loadErrors", []))
                group = next(group for group in model["groups"] if group["key"] == "DL-1")
                assert group["childrenPending"] and not group["standalone"]
        assert not client.cache._conn.in_transaction
    finally:
        other.close()


def test_identical_upstream_at_same_timestamp_revalidates_without_replacing_membership(upstream):
    client, clock, rows, _calls = upstream
    rows[:] = [_row(1)]
    with read_scope(cache_mode="live"):
        _fetch(client)
        first = _model(client, rows)
        original = client.cache.metadata(client._direct_child_cache_key("DL-1"))
    assert first["groups"][0]["standalone"]
    clock[0] = 100000  # A legitimate second upstream read at the same logical clock.
    with read_scope(cache_mode="live"):
        _fetch(client)
        repeated = _model(client, rows)
        source = client.cache.metadata(client._direct_child_cache_key("DL-1"))
        assert source["rowIdentity"] == original["rowIdentity"]
        assert source["fetchedAt"] == original["fetchedAt"]
        assert source["upstreamValidated"]
    assert repeated == first
    assert not repeated.get("loadErrors")


def test_live_supplied_epic_keeps_independent_search_fallback(upstream):
    client, _clock, rows, calls = upstream
    rows[:] = [_row(2)]
    epic = _row(1)
    epic["fields"].update(issuetype={"name": "Epic"}, subtasks=[{"key": "DL-BOGUS"}])
    key = client._direct_child_cache_key("DL-1")
    client.cache.set(key, ["DL-STALE"], 600)
    with read_scope(cache_mode="live"):
        assert client.direct_child_keys("DL-1", parent_issue=epic) == ["DL-2"]
        assert client.cache.metadata(key)["upstreamValidated"]
    assert calls == [0]
