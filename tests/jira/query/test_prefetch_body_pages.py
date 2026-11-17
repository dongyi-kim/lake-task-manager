"""Full-ticket prefetch exhausts small physical body pages without scalar fan-out."""
import re
import time
from types import SimpleNamespace

import pytest

from app.auth.base import UpstreamBusy, UpstreamUnavailable
from app.infra.resource_pressure import ResourceBusy, resource_pressure
from app.infra.cache import Cache
from app.infra.cache_policy import read_scope
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


@pytest.fixture
def paged_client(monkeypatch):
    cache = Cache(":memory:")
    client = JiraClient(get_settings(), cache)
    clock, calls = [time.time()], []
    monkeypatch.setattr("app.infra.cache.time.time", lambda: clock[0])
    state = {"cap": 10, "failAt": None, "paths": [], "busyAfterFirst": False, "busy": False}

    def get(path, params=None):
        state["paths"].append(path)
        assert path == "/rest/api/2/search", "full prefetch must never fall back to scalar GETs"
        params = dict(params)
        calls.append(params)
        start = params["startAt"]
        if start == state["failAt"]:
            raise UpstreamUnavailable("second page unavailable")
        keys = sorted(set(re.findall(r"DL-\d+", params["jql"])))
        size = min(params["maxResults"], state["cap"])
        rows = []
        for key in keys[start:start + size]:
            fields = {field: None for field in params["fields"].split(",")}
            fields.update(summary=key, description="complete body " + key)
            rows.append({"key": key, "fields": fields})
        clock[0] += 2
        if state["busyAfterFirst"]:
            state["busy"] = True
        return {"startAt": start, "maxResults": size, "total": len(keys), "issues": rows}

    provider = SimpleNamespace(get_json=get, supports_parallel=False, close=lambda: None)
    client._provider, client._provider_built = provider, True
    try:
        yield client, calls, clock, state
    finally:
        client.close()
        cache.close()


@pytest.mark.parametrize("cap,offsets", [(10, [0, 10, 20]), (3, [0, 3, 6, 9, 12, 15, 18, 21])])
def test_full_key_batch_exhausts_actual_pages_without_per_issue_gets(paged_client, cap, offsets):
    client, calls, _, state = paged_client
    state["cap"] = cap
    keys = [f"DL-{i:02}" for i in range(1, 24)]
    result = client.issues_by_keys_result(list(reversed(keys)), light=False, raise_retryable=True)
    assert result["complete"] and result["missing"] == []
    assert [issue["key"] for issue in result["issues"]] == list(reversed(keys))
    assert all(issue["fields"]["description"] == "complete body " + issue["key"] for issue in result["issues"])
    assert [call["startAt"] for call in calls] == offsets
    assert all(call["maxResults"] <= 10 for call in calls)


def test_warm_body_page_republication_preserves_age_and_cannot_claim_new_validation(paged_client):
    client, calls, clock, _ = paged_client
    keys = ["DL-01", "DL-02"]
    original = clock[0]
    with read_scope(cache_mode="hot"):
        client.prefetch_issues(keys, light=False)
    canonical = [f"issue:{client.env}:{key}" for key in keys]
    assert all(client.cache.metadata(key)["fetchedAt"] == original for key in canonical)
    client.cache.invalidate_keys(canonical)
    clock[0] += 90
    with read_scope(cache_mode="hot"):
        client.prefetch_issues(keys, light=False)
        sources = [client.cache.metadata(key) for key in canonical]
        assert all(source["fetchedAt"] == original for source in sources)
        assert all(source["upstreamValidated"] is False for source in sources)
    assert len(calls) == 1


def test_failed_later_page_keeps_successful_rows_and_never_caches_absent_keys(paged_client):
    client, calls, _, state = paged_client
    keys = [f"DL-{i:02}" for i in range(1, 8)]
    state.update(cap=3, failAt=3)
    with pytest.raises(UpstreamUnavailable):
        client.prefetch_issues(keys, light=False)
    assert [call["startAt"] for call in calls] == [0, 3]
    assert all(client.cache.get(f"issue:{client.env}:{key}") is not None for key in keys[:3])
    assert all(client.cache.get(f"issue:{client.env}:{key}") is None for key in keys[3:])


@pytest.mark.parametrize("error", [UpstreamBusy("queue busy"), ResourceBusy("memory pressure")])
def test_busy_prefetch_keeps_completed_rows_and_reports_missing_without_scalar_fanout(paged_client, monkeypatch, error):
    client, _, _, state = paged_client
    state.update(cap=3, busyAfterFirst=True)

    def pressure():
        if state["busy"]:
            raise error

    monkeypatch.setattr(resource_pressure, "check", pressure)
    keys = [f"DL-{i:02}" for i in range(1, 8)]
    result = client.issues_by_keys_result(keys, light=False)
    assert result["complete"] is False
    assert [row["key"] for row in result["issues"]] == keys[:3]
    assert [row["key"] for row in result["missing"]] == keys[3:]
    assert all(row["kind"] == "transport" and row["retryable"] is True for row in result["missing"])
    assert state["paths"] == ["/rest/api/2/search"]
    with pytest.raises(type(error)):
        client.issues_by_keys_result(keys, light=False, raise_retryable=True)
