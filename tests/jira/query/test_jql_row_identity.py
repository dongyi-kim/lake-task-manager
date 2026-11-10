"""A compatibility list cannot reuse another request's same-timestamp live proof."""
from copy import deepcopy

from app.infra.cache import Cache
from app.infra.cache_policy import read_scope
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


def test_live_issue_revalidates_after_same_time_legacy_list_replacement(monkeypatch):
    cache = Cache(":memory:")
    client = JiraClient(get_settings(), cache)
    try:
        key = "DL-9012"
        actual = deepcopy(client.get_issue(key))
        alternate = deepcopy(actual)
        alternate["fields"]["summary"] = "UNVALIDATED LIST COPY"
        monkeypatch.setattr("app.infra.cache.time.time", lambda: 100000.0)
        cache.invalidate()
        cache.set("mt:token-repro", [alternate], 600)
        original = client.provider.get_json
        calls = []
        def record(path, *args, **kwargs):
            calls.append(path)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(client.provider, "get_json", record)
        with read_scope(cache_mode="live") as sources:
            assert client.get_issue(key)["fields"]["summary"] == actual["fields"]["summary"]
            assert len(calls) == 1
            with read_scope(cache_mode="warm"):
                copied = client._legacy_search("key = DL-9012", cache_key="mt:token-repro", light=False)
            assert copied[0]["fields"]["summary"] == "UNVALIDATED LIST COPY"
            assert len(calls) == 1
            assert client.get_issue(key)["fields"]["summary"] == actual["fields"]["summary"]
            assert len(calls) == 2
            assert sources[-1]["compliant"] and sources[-1]["upstreamValidated"]
            assert sources[-1]["fetchedAt"] == 100000
    finally:
        client.close()
        cache.close()
