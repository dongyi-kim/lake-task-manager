"""Completed upstream JQL can publish known child memberships with canonical rows."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from app.auth.base import QueryStateChanged
from app.domain import mytasks
from app.infra.cache import Cache, CacheReadPolicyError
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
    # Expected display state must use actual dated cache rows, not certify unobserved
    # supplied dictionaries. Remove that setup before measuring the cold publication.
    client.cache.set_many_if_fence(
        ((f"issueL:{client.env}:{row['key']}", row) for row in rows), 600, client.cache.fence(),
        fetched_at=clock[0], upstream_validated=False)
    expected = _model(client, rows)
    client.cache.forget_prefixes(("childkeys:", "issueL:"))
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


def _aged_parent(upstream, has_children):
    """An eligible older projection and a changed actual issue endpoint, with no child cache."""
    client, clock, _rows, _calls = upstream
    old = _row(1)
    old["fields"]["subtasks"] = [{"key": "DL-11"}] if has_children else []
    current = _row(1)
    current["fields"]["subtasks"] = [{"key": "DL-12"}]
    fetched = clock[0] - 650  # Outside HOT, inside both WARM and COLD.
    parent_key = f"issueL:{client.env}:DL-1"
    client.cache.set_if_fence(parent_key, old, 600, client.cache.fence(),
                              fetched_at=fetched, upstream_validated=False)
    source = client.cache.metadata(parent_key)
    records = {"DL-1": current, "DL-11": _row(11), "DL-12": _row(12)}
    for key in ("DL-11", "DL-12"):
        records[key]["fields"]["issuetype"] = {"name": "Sub-Task"}
    requests = []

    def issue_endpoint(path, params=None):
        requests.append(path)
        assert path.startswith("/rest/api/2/issue/"), path
        assert params and "fields" in params
        return deepcopy(records[path.rsplit("/", 1)[1]])

    client._provider.get_json = issue_endpoint
    return parent_key, source, old, current, records, requests


@pytest.mark.parametrize("mode", ["warm", "cold"])
@pytest.mark.parametrize("has_children", [False, True])
def test_supplied_eligible_parent_cannot_publish_newer_membership_without_upstream_proof(
        upstream, mode, has_children):
    client, clock, _rows, _calls = upstream
    parent_key, original, old, _current, _records, requests = _aged_parent(upstream, has_children)
    membership_key = client._direct_child_cache_key("DL-1")
    with read_scope(cache_mode=mode):
        supplied = client.get_issue_light("DL-1")
        assert supplied == old and requests == []
        children = client.direct_child_keys("DL-1", parent_issue=supplied)
        source = client.cache.metadata(membership_key)
        assert source is not None
        if requests:
            # An independent upstream read is a valid alternative to reusing captured proof.
            assert requests == ["/rest/api/2/issue/DL-1"]
            assert children == ["DL-12"]
            assert source["fetchedAt"] == clock[0] and source["upstreamValidated"]
        else:
            assert children == (["DL-11"] if has_children else [])
            assert source["fetchedAt"] == original["fetchedAt"]
            assert source["upstreamValidated"] is False
            assert client.cache.metadata(parent_key)["rowIdentity"] == original["rowIdentity"]


@pytest.mark.parametrize("follow_mode", ["hot", "live"])
@pytest.mark.parametrize("has_children", [False, True])
def test_hot_or_live_after_aged_supplied_parent_observes_changed_children(
        upstream, follow_mode, has_children):
    client, clock, _rows, _calls = upstream
    _parent_key, _source, _old, _current, _records, requests = _aged_parent(upstream, has_children)
    with read_scope(cache_mode="warm"):
        supplied = client.get_issue_light("DL-1")
        client.direct_child_keys("DL-1", parent_issue=supplied)
    initial_reads = len(requests)
    assert initial_reads in (0, 1)
    with read_scope(cache_mode=follow_mode):
        assert client.direct_child_keys("DL-1") == ["DL-12"]
        source = client.cache.metadata(client._direct_child_cache_key("DL-1"))
        assert source["fetchedAt"] == clock[0]
        new_read = follow_mode == "live" or not initial_reads
        assert source["upstreamValidated"] is bool(new_read)
    assert requests == ["/rest/api/2/issue/DL-1"] * (initial_reads + int(new_read))


@pytest.mark.parametrize("has_children", [False, True])
def test_epic_tree_warm_parent_cannot_make_old_membership_hot(upstream, has_children):
    client, clock, _rows, _calls = upstream
    _parent_key, original, _old, _current, records, requests = _aged_parent(upstream, has_children)
    client.cache.set(client._direct_child_cache_key("DL-900"), ["DL-1"], 600)
    client.cache.set_if_fence(f"issueL:{client.env}:DL-11", records["DL-11"], 600,
                              client.cache.fence(), fetched_at=original["fetchedAt"],
                              upstream_validated=False)
    with read_scope(cache_mode="warm"):
        first = client._build_epic_tree("DL-900")
        assert [node["key"] for node in first] == ["DL-1"]
    with read_scope(cache_mode="hot"):
        tree = client._build_epic_tree("DL-900")
        assert [node["key"] for node in tree] == ["DL-1"]
        assert [node["key"] for node in tree[0]["children"]] == ["DL-12"]
        assert client.cache.metadata(client._direct_child_cache_key("DL-1"))["fetchedAt"] == clock[0]
    # Regardless of whether the warm producer independently refreshed, each required issue
    # is fetched once. An old false-empty list must not suppress the actual new child.
    assert requests == ["/rest/api/2/issue/DL-1", "/rest/api/2/issue/DL-12"]


@pytest.mark.parametrize("change", ["same_time_identity", "newer_identity", "invalidation", "unproven_bytes"])
def test_supplied_parent_cannot_borrow_proof_from_other_bytes_or_retired_identity(upstream, change):
    client, clock, _rows, _calls = upstream
    parent_key, original, _old, current, _records, requests = _aged_parent(upstream, True)
    membership_key = client._direct_child_cache_key("DL-1")
    with read_scope(cache_mode="warm"):
        supplied = client.get_issue_light("DL-1")
        if change in {"same_time_identity", "newer_identity"}:
            fetched = original["fetchedAt"] if change == "same_time_identity" else clock[0]
            client.cache.set_if_fence(parent_key, current, 600, client.cache.fence(),
                                      fetched_at=fetched, upstream_validated=False)
            assert client.cache.metadata(parent_key)["rowIdentity"] != original["rowIdentity"]
        elif change == "invalidation":
            client.cache.invalidate_keys([parent_key])
        else:
            supplied["fields"]["subtasks"] = [{"key": "DL-99"}]  # No source contains these bytes.
        try:
            children = client.direct_child_keys("DL-1", parent_issue=supplied)
        except (CacheReadPolicyError, QueryStateChanged):
            assert client.cache.get_stale_at(membership_key) is None
            return
        source = client.cache.metadata(membership_key)
        if requests:
            assert requests == ["/rest/api/2/issue/DL-1"]
            assert children == ["DL-12"]
            assert source["fetchedAt"] == clock[0] and source["upstreamValidated"]
        elif change == "unproven_bytes":
            assert children == ["DL-11"]  # The actual unchanged cached projection is usable.
            assert source["fetchedAt"] == original["fetchedAt"] and not source["upstreamValidated"]
        else:
            assert change != "invalidation"
            assert children == ["DL-12"]
            expected_age = original["fetchedAt"] if change == "same_time_identity" else clock[0]
            assert source["fetchedAt"] == expected_age and not source["upstreamValidated"]
