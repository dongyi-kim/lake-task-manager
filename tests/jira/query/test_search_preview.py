"""Search previews are bounded prefixes, never exhaustive membership proofs."""
import time

import pytest

from app.auth.base import QueryStateChanged
from app.infra.cache import Cache
from app.infra.cache_policy import read_scope, reduce_read_metadata
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient, MutationEvent
from app.domain import search
from types import SimpleNamespace


@pytest.fixture
def client():
    value = JiraClient(get_settings(), Cache(":memory:"))
    yield value
    value.cache._conn.close()


QUERY = 'project = DL AND text ~ "ETL" ORDER BY updated DESC'
FIELDS = "summary,status,updated"


def row(key, updated="2026-11-01T00:00:00+09:00"):
    return {"key": key, "fields": {"summary": key, "status": {"name": "Open"}, "updated": updated}}


def provider(client, monkeypatch, *, total=7755, cap=2):
    calls = []
    def fetch(path, params=None):
        calls.append((path, dict(params)))
        rows = [row("DL-1"), row("DL-2"), row("DL-3")][:min(cap, params["maxResults"])]
        return {"issues": rows, "total": total, "maxResults": cap}
    monkeypatch.setattr(client.provider, "get_json", fetch)
    return calls


def test_preview_fetches_one_bounded_page_and_never_publishes_full_membership(client, monkeypatch):
    calls = provider(client, monkeypatch)
    result = client.search_issues_preview(QUERY, limit=3, fields=FIELDS)
    assert [item["key"] for item in result["issues"]] == ["DL-1", "DL-2"]
    assert result["total"] == 7755 and result["hasMore"]
    assert result.get("snapshotId") is None
    assert len(calls) == 1 and calls[0][1]["maxResults"] == 3
    assert calls[0][1]["jql"].endswith("ORDER BY updated DESC, key ASC")
    assert client.cache.get(client._jql_leaf_key(client._compile_jql(QUERY).whole_query)[2]) is None


def test_preview_preserves_unknown_total_without_exhaustive_refill(client, monkeypatch):
    calls = provider(client, monkeypatch, total=None)
    result = client.search_issues_preview(QUERY, limit=3, fields=FIELDS)
    assert result["total"] is None and result["hasMore"] and len(calls) == 1


def test_preview_uses_shared_age_policy_without_restamping(client, monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    calls = provider(client, monkeypatch)
    with read_scope(cache_mode="hot") as sources:
        client.search_issues_preview(QUERY, limit=3, fields=FIELDS)
        original = reduce_read_metadata(sources)["fetchedAt"]
    clock[0] += 2 * 86400
    with read_scope(cache_mode="cool") as sources:
        client.search_issues_preview(QUERY, limit=3, fields=FIELDS)
        assert reduce_read_metadata(sources)["fetchedAt"] == original
    assert len(calls) == 1
    for mode in ("hot", "live", "live"):
        with read_scope(cache_mode=mode) as sources:
            client.search_issues_preview(QUERY, limit=3, fields=FIELDS)
            assert reduce_read_metadata(sources)["compliant"]
    assert len(calls) == 4


@pytest.mark.parametrize("prime", ["whole", "leaves"])
def test_preview_reuses_proven_membership_across_decomposition_without_fetch(client, monkeypatch, prime):
    query = "key = DL-1 OR key = DL-2 ORDER BY updated DESC"
    compiled = client._compile_jql(query)
    stamp = time.time() - 90
    rows = [row("DL-1", "2026-10-01T00:00:00+09:00"), row("DL-2")]
    leaves = [(compiled.whole_query, ["DL-1", "DL-2"])] if prime == "whole" else list(zip(compiled.leaves, [["DL-1"], ["DL-2"]]))
    for leaf, keys in leaves:
        client.cache.set_if_fence(client._jql_leaf_key(leaf)[2], keys, 600, client.cache.fence(), fetched_at=stamp)
    projection = client._projection_signature(FIELDS, True)
    client._store_jql_rows(rows, client._jql_generation(), projection, FIELDS, fetched_at=stamp)
    def unexpected(*args, **kwargs):
        pytest.fail("Complete fresh membership and rows need no Jira read")
    monkeypatch.setattr(client.provider, "get_json", unexpected)
    with read_scope(cache_mode="hot") as sources:
        result = client.search_issues_preview(query, limit=1, fields=FIELDS)
        assert reduce_read_metadata(sources)["fetchedAt"] == stamp
    assert [item["key"] for item in result["issues"]] == ["DL-2"]
    assert result["total"] == 2 and result["hasMore"]


@pytest.mark.parametrize("missing", ["membership", "row", "sort_field"])
def test_incomplete_coverage_falls_back_to_one_preview_not_hydration(client, monkeypatch, missing):
    compiled = client._compile_jql(QUERY)
    if missing != "membership":
        client.cache.set(client._jql_leaf_key(compiled.whole_query)[2], ["DL-1"], 600)
    if missing != "row":
        value = row("DL-1")
        if missing == "sort_field":
            value["fields"].pop("updated")
        client._store_jql_rows([value], client._jql_generation(), client._projection_signature(FIELDS, True), FIELDS)
    calls = provider(client, monkeypatch)
    client.search_issues_preview(QUERY, limit=3, fields=FIELDS)
    assert len(calls) == 1 and 'text ~ "ETL"' in calls[0][1]["jql"]


def test_preview_cache_separates_limit_projection_order_and_mutation(client, monkeypatch):
    calls = provider(client, monkeypatch)
    for fields, limit, query in [(FIELDS, 1, QUERY), (FIELDS, 2, QUERY),
            ("summary,updated", 2, QUERY), (FIELDS, 2, QUERY.replace("DESC", "ASC"))]:
        client.search_issues_preview(query, limit=limit, fields=fields)
    assert len(calls) == 4
    client._record_mutation(MutationEvent("fields", "DL-1", changed_fields=("summary",)))
    client.search_issues_preview(QUERY, limit=1, fields=FIELDS)
    assert len(calls) == 5


def test_preview_mutation_during_fetch_cannot_return_stale_success(client, monkeypatch):
    def fetch(*args, **kwargs):
        client.cache.invalidate()
        return {"issues": [row("DL-1")], "total": 1}
    monkeypatch.setattr(client.provider, "get_json", fetch)
    with read_scope(cache_mode="live"):
        with pytest.raises(QueryStateChanged):
            client.search_issues_preview(QUERY, limit=3, fields=FIELDS)


@pytest.mark.parametrize("query,exact_keys", [("ETL", []), ("1", ["DL-1", "OPS-1"])])
def test_workspace_preview_and_batched_exact_keys_preserve_priority_and_scope(query, exact_keys):
    settings = SimpleNamespace(search_jira_projects=["DL", "OPS"], epic_link_field_id="customfield_1", jira_base="https://jira.test")
    def issue(key):
        value = row(key)
        value["fields"]["project"] = {"key": key.split("-")[0]}
        return value
    class SearchClient:
        def __init__(self):
            self.previews, self.exact = [], []
        def search_issues_page(self, jql, **kwargs):
            assert 'text ~' not in jql, "A discovery limit must not materialize the whole query"
            self.exact.append(jql)
            return {"issues": [issue(key) for key in reversed(exact_keys)]}
        def search_issues_preview(self, jql, **kwargs):
            self.previews.append((jql, kwargs))
            return {"issues": [issue("DL-1"), issue("DL-2"), issue("PRIVATE-1")]}
        def epic_metadata_title_map(self, *args, **kwargs):
            return {}
    value = SearchClient()
    result = search._search_jira(value, settings, query, "all", 3)
    assert len(value.previews) == 1 and value.previews[0][1]["limit"] == 3
    assert len(value.exact) == bool(exact_keys)
    assert [item["key"] for item in result["items"]] == (exact_keys + ["DL-2"] if exact_keys else ["DL-1", "DL-2"])
    assert all(item["exact"] for item in result["items"][:len(exact_keys)])
