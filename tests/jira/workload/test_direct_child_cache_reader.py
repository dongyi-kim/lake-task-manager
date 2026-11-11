"""Warm hierarchy reads batch memberships without changing scalar fallback semantics."""
import time

import pytest

from app.auth.base import UpstreamUnavailable
from app.infra.cache import Cache, CacheReadPolicyError
from app.infra.cache_policy import CachePolicy, read_scope
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


def row(key, children=(), kind="Task"):
    return {"key": key, "fields": {"summary": key, "issuetype": {"name": kind},
        "status": {"name": "Open", "statusCategory": {"key": "new"}},
        "subtasks": [{"key": child} for child in children]}}


@pytest.fixture
def hierarchy(tmp_path, monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    cache = Cache(str(tmp_path / "children.sqlite3"))
    cache.configure(CachePolicy())
    client = JiraClient(get_settings(), cache)
    monkeypatch.setattr(client.provider, "get_json", lambda *a, **k: pytest.fail("Unexpected upstream read"))
    def seed(key, children, ttl=600):
        ck = client._direct_child_cache_key(key)
        cache.set_if_fence(ck, children, ttl, cache.fence())
        return ck, cache.metadata(ck)
    try:
        yield client, clock, seed
    finally:
        client.close()
        cache.close()


def test_real_warm_epic_tree_batches_parent_memberships_and_preserves_tree_proof(hierarchy):
    client, clock, seed = hierarchy
    roots = [f"DL-{i}" for i in range(1, 129)]
    seed("DL-900", roots)
    originals = {}
    for i, key in enumerate(roots, 1):
        children = [] if i % 2 else [f"DL-{1000 + i}"]
        ck, source = seed(key, children)
        originals[ck] = source
        # Cached [] must remain authoritative over different supplied subtasks.
        client.cache.set(f"issueL:{client.env}:{key}", row(key, ["DL-UNUSED"]), 600)
        for child in children:
            client.cache.set(f"issueL:{client.env}:{child}", row(child, kind="Sub-Task"), 600)
    clock[0] += 20
    sql = []
    client.cache._conn.set_trace_callback(sql.append)
    with read_scope() as sources:
        tree = client._build_epic_tree("DL-900")
    client.cache._conn.set_trace_callback(None)
    assert [node["key"] for node in tree] == roots
    assert [node["summary"] for node in tree] == roots
    assert [[child["key"] for child in node["children"]] for node in tree] == [
        [] if i % 2 else [f"DL-{1000 + i}"] for i in range(1, 129)]
    # Epic membership, parent issues, all parent memberships, child issues.
    assert sql.count("COMMIT") == 4
    membership_sources = [source for source in sources if source["kind"] == "childkeys"]
    assert len(membership_sources) == 129
    for ck, original in originals.items():
        source = client.cache.metadata(ck)
        assert (source["rowIdentity"], source["fetchedAt"]) == (original["rowIdentity"], original["fetchedAt"])
        assert client.cache._conn.execute("SELECT accessed_at FROM cache_access WHERE key=?", (ck,)).fetchone()[0] == clock[0]
    assert not client.cache._conn.in_transaction


@pytest.mark.parametrize("mode,age", [(None, 20), ("hot", 20), ("warm", 650), ("live", 20)])
def test_reader_preserves_eligible_proof_empty_membership_and_scalar_limit_normalization(hierarchy, mode, age):
    client, clock, seed = hierarchy
    with read_scope(cache_mode=mode):
        ck, original = seed("DL-1", ["DL-11", "DL-12"])
        seed("DL-2", [])
        clock[0] += age
        with read_scope() as observed:
            read = client._direct_child_keys_reader([" dl-1 ", "DL-2", "", "DL-1"])
            assert read(" dl-1 ", limit=1) == ["DL-11"]
            assert read("DL-1", limit=-1) == []
            assert read("DL-1", limit="2") == ["DL-11", "DL-12"]
            assert read("DL-2", parent_issue=row("DL-2", ["DL-WRONG"])) == []
            assert read("") == []
        assert len(observed) == 2
        current = client.cache.metadata(ck)
        assert (current["rowIdentity"], current["fetchedAt"]) == (original["rowIdentity"], original["fetchedAt"])
        assert observed[0]["upstreamValidated"] == original["upstreamValidated"]


@pytest.mark.parametrize("raises", [False, True])
def test_scalar_fallback_discards_later_hits_even_if_it_raises(hierarchy, monkeypatch, raises):
    client, clock, seed = hierarchy
    seed("DL-2", ["DL-OLD"], ttl=5)
    # The replacement membership needs a dated parent source; caller bytes alone are not proof.
    client.cache.set_if_fence(f"issueL:{client.env}:DL-2", row("DL-2", ["DL-NEW"]), 600,
                              client.cache.fence(), fetched_at=clock[0], upstream_validated=False)
    scalar, called = client.direct_child_keys, []
    def fallback(key, **kwargs):
        called.append(key)
        if key == "DL-1":
            clock[0] += 10
            if raises:
                raise UpstreamUnavailable("unknown membership")
            return ["DL-11"]
        return scalar(key, **kwargs)
    monkeypatch.setattr(client, "direct_child_keys", fallback)
    read = client._direct_child_keys_reader(["DL-1", "DL-2"])
    if raises:
        with pytest.raises(UpstreamUnavailable):
            read("DL-1")
    else:
        assert read("DL-1") == ["DL-11"]
    assert read("DL-2", parent_issue=row("DL-2", ["DL-NEW"])) == ["DL-NEW"]
    assert called == ["DL-1", "DL-2"]


def test_mutation_after_capture_rechecks_membership(hierarchy):
    client, _, seed = hierarchy
    ck, _ = seed("DL-1", ["DL-OLD"])
    read = client._direct_child_keys_reader(["DL-1"])
    client.cache.invalidate_keys([ck])
    seed("DL-1", ["DL-NEW"])
    assert read("DL-1") == ["DL-NEW"]


def test_new_live_scope_does_not_certify_retained_membership_from_supplied_parent(hierarchy, monkeypatch):
    client, _, seed = hierarchy
    ck, original = seed("DL-1", ["DL-OLD"])
    requests = []
    def unavailable(path, **kwargs):
        requests.append(path)
        raise UpstreamUnavailable("independent parent validation unavailable")
    monkeypatch.setattr(client.provider, "get_json", unavailable)
    with read_scope(cache_mode="live"):
        read = client._direct_child_keys_reader(["DL-1"])
        with pytest.raises(CacheReadPolicyError):
            read("DL-1", parent_issue=row("DL-1", ["DL-NEW"]))
    assert requests == []
    source = client.cache.metadata(ck)
    assert (source["rowIdentity"], source["fetchedAt"]) == (original["rowIdentity"], original["fetchedAt"])


def test_batch_probe_failure_delegates_without_hiding_scalar_failure(hierarchy, monkeypatch):
    client, _, _ = hierarchy
    monkeypatch.setattr(client.cache, "get_many", lambda *a, **k: (_ for _ in ()).throw(ValueError("cache probe")))
    monkeypatch.setattr(client, "direct_child_keys", lambda *a, **k: (_ for _ in ()).throw(UpstreamUnavailable("down")))
    read = client._direct_child_keys_reader(["DL-1"])
    with pytest.raises(UpstreamUnavailable, match="down"):
        read("DL-1")
