"""Hydration preserves existing projection identity while publishing actual new copies."""
import re
import time

import pytest

from app.auth.base import QueryStateChanged
from app.infra.cache import Cache
from app.infra.cache_policy import read_scope, reduce_read_metadata
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


@pytest.fixture
def hydration(tmp_path, monkeypatch):
    client = JiraClient(get_settings(), Cache(str(tmp_path / "hydration.sqlite3")))
    clock = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    fields = client._issue_fields(light=True)
    generation, projection = client._jql_generation(), client._projection_signature(fields, True)
    calls, publications = [], []
    fetch_delay = [0]

    def row(key):
        values = dict.fromkeys(fields.split(","))
        values.update(summary=key, issuetype={"name": "Task", "subtask": False},
                      assignee={"name": "a"}, reporter={"name": "b"},
                      status={"name": "Open", "statusCategory": {"key": "new"}})
        return {"key": key, "fields": values}

    def request(path, params=None, **kwargs):
        assert path == "/rest/api/2/search"
        calls.append(dict(params))
        keys = re.findall(r"DL-\d+", params["jql"])
        clock[0] += fetch_delay[0]
        return {"startAt": 0, "total": len(keys), "maxResults": 100,
                "issues": [row(key) for key in keys]}

    store = client._store_jql_rows
    def publish(rows, *args, **kwargs):
        rows = list(rows)
        publications.append({"keys": [item["key"] for item in rows], **kwargs})
        return store(rows, *args, **kwargs)

    monkeypatch.setattr(client.provider, "get_json", request)
    monkeypatch.setattr(client, "_store_jql_rows", publish)
    def target(key):
        return client._jql_row_key(generation, projection, key)
    def seed(key, kind, age):
        cache_key = target(key) if kind == "jqlrow" else f"{kind}:{client.env}:{key}"
        client.cache.set_if_fence(cache_key, row(key), 600, client.cache.fence(), fetched_at=clock[0] - age)
        return client.cache.metadata(cache_key)
    def hydrate(keys):
        return client._hydrate_jql_leaf_rows(keys, fields, True, generation, projection)
    try:
        yield client, clock, calls, publications, row, target, seed, hydrate, fetch_delay
    finally:
        client.close()
        client.cache.close()


def test_complete_projection_keeps_persistent_identity_age_and_read_recency(hydration):
    client, clock, calls, publications, row, target, seed, hydrate, _ = hydration
    before = seed("DL-1", "jqlrow", 100)
    clock[0] += 20
    with read_scope() as sources:
        assert hydrate(["DL-1"]) == [row("DL-1")]
    after = client.cache.metadata(target("DL-1"))
    assert after["rowIdentity"] == before["rowIdentity"]
    assert after["fetchedAt"] == before["fetchedAt"]
    assert reduce_read_metadata(sources)["fetchedAt"] == before["fetchedAt"]
    accessed = client.cache._conn.execute("SELECT accessed_at FROM cache_access WHERE key=?", (target("DL-1"),)).fetchone()[0]
    assert accessed == clock[0]
    assert publications == [] and calls == []


def test_mixed_sources_publish_only_canonical_copies_and_fetched_rows_at_original_ages(hydration):
    client, clock, calls, publications, row, target, seed, hydrate, delay = hydration
    exact = seed("DL-1", "jqlrow", 150)
    full = seed("DL-2", "issue", 90)
    light = seed("DL-3", "issueL", 30)
    fetched = clock[0]
    delay[0] = 5
    with read_scope(cache_mode="cool") as sources:
        assert hydrate(["DL-1", "DL-2", "DL-3", "DL-4"]) == [row(f"DL-{i}") for i in range(1, 5)]
        metadata = {key: client.cache.metadata(target(key)) for key in ("DL-1", "DL-2", "DL-3", "DL-4")}
    assert metadata["DL-1"]["rowIdentity"] == exact["rowIdentity"]
    assert {key: value["fetchedAt"] for key, value in metadata.items()} == {
        "DL-1": exact["fetchedAt"], "DL-2": full["fetchedAt"], "DL-3": light["fetchedAt"], "DL-4": fetched}
    assert {key for batch in publications for key in batch["keys"]} == {"DL-2", "DL-3", "DL-4"}
    assert len(calls) == 1 and "DL-4" in calls[0]["jql"]
    assert metadata["DL-4"]["upstreamValidated"] is True
    assert not any(metadata[key]["upstreamValidated"] for key in ("DL-1", "DL-2", "DL-3"))
    assert reduce_read_metadata(sources)["fetchedAt"] == exact["fetchedAt"]


def test_live_requires_upstream_proof_but_does_not_replace_same_request_validated_projection(hydration):
    client, _, calls, publications, row, target, seed, hydrate, _ = hydration
    before = seed("DL-1", "jqlrow", 100)
    with read_scope(cache_mode="live"):
        assert hydrate(["DL-1"]) == [row("DL-1")]
        validated = client.cache.metadata(target("DL-1"))
        assert validated["rowIdentity"] != before["rowIdentity"] and validated["upstreamValidated"]
        publications.clear()
        assert hydrate(["DL-1"]) == [row("DL-1")]
        assert client.cache.metadata(target("DL-1"))["rowIdentity"] == validated["rowIdentity"]
        assert publications == [] and len(calls) == 1
    with read_scope(cache_mode="live"):
        assert hydrate(["DL-1"]) == [row("DL-1")]
        assert client.cache.metadata(target("DL-1"))["rowIdentity"] != validated["rowIdentity"]
    assert len(calls) == 2


def test_expired_projection_is_fetched_and_republished(hydration):
    client, clock, calls, publications, row, target, seed, hydrate, _ = hydration
    before = seed("DL-1", "jqlrow", 10000)
    with read_scope(cache_mode="hot"):
        assert hydrate(["DL-1"]) == [row("DL-1")]
    after = client.cache.metadata(target("DL-1"))
    assert after["rowIdentity"] != before["rowIdentity"] and after["fetchedAt"] == clock[0]
    assert len(calls) == len(publications) == 1


@pytest.mark.parametrize("change", ["fence", "generation"])
def test_complete_projection_cannot_escape_fence_or_generation_validation(hydration, monkeypatch, change):
    client, _, calls, publications, _, _, seed, hydrate, _ = hydration
    seed("DL-1", "jqlrow", 100)
    resolve = client._resolve_cached_jql_rows
    def invalidate(*args, **kwargs):
        result = resolve(*args, **kwargs)
        if change == "fence":
            client.cache.invalidate("unrelated:")
        else:
            client.cache.bump_epoch(client._jql_epoch_namespace())
        return result
    monkeypatch.setattr(client, "_resolve_cached_jql_rows", invalidate)
    with pytest.raises(QueryStateChanged):
        hydrate(["DL-1"])
    assert publications == [] and calls == []
