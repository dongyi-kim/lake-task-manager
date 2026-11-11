"""Only a completed, current stream can prove a reusable whole-query membership."""
import time

import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import read_scope
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


QUERY = "key = DL-9012 OR key = DL-9013"


@pytest.fixture
def client():
    value = JiraClient(get_settings(), Cache(":memory:"))
    yield value
    value.close()
    value.cache.close()


def whole_key(client):
    return client._jql_leaf_key(client._compile_jql(QUERY).whole_query)[2]


def test_warm_stream_union_keeps_original_collection_age(client, monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    for key in ("DL-9012", "DL-9013"):
        client.search_issues_page("key = " + key, strategy="full")
    collected = clock[0]
    clock[0] += 650
    calls, upstream = [], client.provider.get_json

    def request(path, **kwargs):
        calls.append(path)
        return upstream(path, **kwargs)

    monkeypatch.setattr(client.provider, "get_json", request)
    with read_scope(cache_mode="warm"):
        chunks = list(client.iter_search_issue_chunks(QUERY, strategy="full"))
        source = client.cache.metadata(whole_key(client))
    assert len(chunks[-1]["combined"]) == 2
    assert source is not None and source["fetchedAt"] == collected
    assert not source["upstreamValidated"] and calls == []
    with read_scope(cache_mode="hot"):
        assert client.cache.get(whole_key(client)) is None


def test_live_stream_union_reuses_its_proof_only_in_the_same_request(client, monkeypatch):
    with read_scope(cache_mode="live"):
        chunks = list(client.iter_search_issue_chunks(QUERY, strategy="full"))
        source = client.cache.metadata(whole_key(client))
        assert source is not None and source["upstreamValidated"]
        monkeypatch.setattr(client.provider, "get_json", lambda *a, **k: pytest.fail("Validated union is already complete"))
        page = client.search_issues_page(QUERY, strategy="off")
        assert page["issues"] == chunks[-1]["combined"]
    with read_scope(cache_mode="live"):
        assert client.cache.get(whole_key(client)) is None


def test_closed_stream_does_not_publish_a_whole_union(client):
    stream = client.iter_search_issue_chunks(QUERY, strategy="full")
    assert next(stream)["issues"]
    stream.close()
    assert client.cache.get_with_metadata(whole_key(client)) is None


def test_failed_stream_does_not_publish_a_whole_union(client, monkeypatch):
    upstream = client.provider.get_json

    def request(path, **kwargs):
        if path == "/rest/api/2/search" and "key = DL-9013" in kwargs["params"]["jql"]:
            raise ConnectionError("second membership unavailable")
        return upstream(path, **kwargs)

    monkeypatch.setattr(client.provider, "get_json", request)
    chunks = list(client.iter_search_issue_chunks(QUERY, strategy="full"))
    assert any(chunk.get("error") for chunk in chunks)
    assert client.cache.get_with_metadata(whole_key(client)) is None


def test_mutation_after_last_stream_yield_prevents_union_publication(client):
    stream = client.iter_search_issue_chunks(QUERY, strategy="full")
    next(stream)
    assert len(next(stream)["combined"]) == 2
    client.cache.bump_epoch(client._jql_epoch_namespace())
    assert list(stream) == []
    assert client.cache.get_with_metadata(whole_key(client)) is None


def test_expired_policy_after_last_stream_yield_prevents_union_publication(client, monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    with read_scope(cache_mode="hot"):
        stream = client.iter_search_issue_chunks(QUERY, strategy="full")
        next(stream)
        next(stream)
        clock[0] += 601
        assert list(stream) == []
        assert client.cache.get_with_metadata(whole_key(client)) is None
