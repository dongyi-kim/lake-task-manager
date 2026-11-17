"""Every Jira search transport respects body size and current memory admission."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from types import SimpleNamespace

import pytest

from app.infra.cache import Cache
from app.infra.resource_pressure import ResourceBusy, resource_pressure
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient
from app.jira.jql import JqlUnsupported


def client(get_json):
    value = JiraClient(get_settings(), Cache(":memory:"))
    value._provider = SimpleNamespace(get_json=get_json, supports_parallel=False)
    value._provider_built = True
    return value


@pytest.mark.parametrize("fields,requested,expected", [
    ("summary", 100, 100), ("summary,description", 100, 10),
    ("*all", 100, 10), ("*navigable", 100, 10), ("description", 3, 3),
])
def test_physical_body_pages_are_small_without_changing_metadata_pages(fields, requested, expected):
    calls = []

    def transport(_path, params):
        calls.append(params)
        return {"issues": [], "total": 0}

    c = client(transport)
    try:
        c._query_page("project = P", fields, max_results=requested)
        assert [call["maxResults"] for call in calls] == [expected]
    finally:
        c.cache.close()


@pytest.mark.parametrize("bad_field_retry", [False, True])
def test_unsupported_full_search_advances_by_actual_server_rows(monkeypatch, bad_field_retry):
    calls, count = [], 23

    def transport(_path, params):
        calls.append(dict(params))
        if bad_field_retry and len(calls) == 1:
            return {"errorMessages": ["bad field"]}
        start, size = params["startAt"], min(params["maxResults"], 3)
        return {"startAt": start, "maxResults": size, "total": count,
                "issues": [{"key": f"P-{index}", "fields": {"summary": str(index), "description": "whole body"}}
                           for index in range(start, min(count, start + size))]}

    c = client(transport)
    monkeypatch.setattr(c, "_note_bad_field", lambda _: True)
    def unsupported(*args, **kwargs):
        raise JqlUnsupported("fixture Jira-only grammar")
    monkeypatch.setattr(c, "_compile_jql", unsupported)
    try:
        rows = c._search('status WAS "Resolved"', light=False, max_results=None)
        assert [row["key"] for row in rows] == [f"P-{index}" for index in range(23)]
        offsets = [call["startAt"] for call in calls]
        assert offsets == ([0] if bad_field_retry else []) + [0, 3, 6, 9, 12, 15, 18, 21]
        assert all(call["maxResults"] == 10 for call in calls)
    finally:
        c.cache.close()


def test_memory_is_rechecked_after_waiting_for_physical_page_admission(monkeypatch):
    calls, busy = [], False

    def check():
        if busy:
            raise ResourceBusy("fixture memory pressure")

    c = client(lambda path, params: calls.append(path) or {"issues": [], "total": 0})
    monkeypatch.setattr(resource_pressure, "check", check)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with c._query_scheduler.page_scope(parallel_capable=False):
                pending = pool.submit(c._query_page, "project = P", "summary")
                with c._query_scheduler._condition:
                    assert c._query_scheduler._condition.wait_for(lambda: len(c._query_scheduler._waiting) == 1, 2)
                busy = True
            with pytest.raises(ResourceBusy):
                pending.result(2)
        assert calls == []
        busy = False
        assert c._query_page("project = P", "summary")["issues"] == []
        assert calls == ["/rest/api/2/search"]
    finally:
        c.cache.close()


def test_exhaustive_body_limit_fails_before_canonical_cache_publication():
    calls = []

    def transport(_path, params):
        calls.append(params["startAt"])
        return {"startAt": params["startAt"], "total": 3, "maxResults": params["maxResults"],
                "issues": [{"key": f"P-{i}", "fields": {"summary": str(i), "description": "x" * 200}}
                           for i in range(3)]}

    c = client(transport)
    c.JQL_BODY_AGGREGATE_LIMIT = 500
    try:
        with pytest.raises(ValueError, match="Description query exceeds"):
            c._fetch_exhaustive_jql("project = P", "summary,description", False, 100)
        assert calls == [0]
        assert c.cache.get(f"issue:{c.env}:P-0") is None
    finally:
        c.cache.close()


def test_separately_bounded_body_groups_cannot_publish_oversized_union():
    calls = []

    def transport(_path, params):
        calls.append(params["jql"])
        name = "one" if '"one"' in params["jql"] else "two"
        return {"startAt": 0, "total": 1, "maxResults": params["maxResults"], "issues": [{
            "key": "P-1" if name == "one" else "P-2", "fields": {
                "summary": name, "description": "x" * 400, "assignee": {"name": name},
                "reporter": None, "status": {"statusCategory": {"key": "new"}}}}]}

    c = client(transport)
    c.JQL_BODY_AGGREGATE_LIMIT = 800
    try:
        with pytest.raises(ValueError, match="Description query exceeds"):
            c._search('assignee = "one" OR assignee = "two"', light=False, strategy="full")
        assert len(calls) == 2
        assert not c.cache._conn.execute("SELECT key FROM cache WHERE key LIKE 'jqlsnap:%'").fetchall()
    finally:
        c.cache.close()


def test_body_projection_uses_new_budget_namespace_while_metadata_identity_stays_compatible():
    c = client(lambda *_args, **_kwargs: {"issues": [], "total": 0})
    try:
        context = c._jql_user_context()
        legacy = lambda fields: hashlib.sha256(f"{context}\n0\n{fields}".encode()).hexdigest()[:20]
        assert c._projection_signature("summary", False) == legacy("summary")
        assert c._projection_signature("description,summary", False) != legacy("description,summary")
    finally:
        c.cache.close()
