"""Hierarchy discovery shares caches without inheriting a Done ticket's long membership TTL."""

from copy import deepcopy
import json
import time

import pytest

from app.auth.base import PermissionDenied, SessionExpired, UpstreamUnavailable
from app.infra.cache_policy import CachePolicy
from app.jira.hierarchy_service import get_descendants


def _age_issue(cache, key, seconds):
    with cache._lock:
        cache._conn.execute("UPDATE cache SET fetched_at=? WHERE key=?", (time.time() - seconds, key))
        cache._conn.commit()


def _old_done_parent(env, monkeypatch):
    # Keep background jobs from rewriting the cache snapshot arranged below.
    monkeypatch.setattr(env.client, "_reprime", lambda *_args, **_kwargs: None)
    root = env.client.create_child(None, "Task", "Done parent")['key']
    done = next(row for row in env.client.transitions(root) if row["toCategory"] == "done")
    env.client.do_transition(root, done["id"], assignee="u01", resolution="Done",
                             time_spent="1m", comment="Completed parent fixture")
    previous = deepcopy(env.client.get_issue_light(root))
    child = env.client.create_child(root, "Sub-task", "Added outside the cached parent")['key']
    env.cache.invalidate()
    env.cache.configure(CachePolicy())
    env.cache.set(f"issueL:{env.client.env}:{root}", previous, 600)
    _age_issue(env.cache, f"issueL:{env.client.env}:{root}", 7200)
    return root, child


def test_cold_task_batches_one_hundred_children_and_warm_read_has_no_requests(build_env):
    env = build_env()
    root = env.client.create_child(None, "Task", "Large Task")['key']
    children = {env.client.create_child(root, "Sub-task", f"Child {n}")['key'] for n in range(100)}
    env.cache.invalidate()
    env.calls.clear()
    result = get_descendants(env.client, root, projects=["DL"], max_results=100)
    rows, page = list(result["issues"]), result
    while page["hasMore"]:
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) <= 8192
        page = get_descendants(env.client, root, projects=["DL"], start_at=page["nextStartAt"])
        rows.extend(page["issues"])
    assert result["complete"] and result["total"] == 101
    assert len(rows) == 101 and {row["key"] for row in rows} == {root, *children}
    assert all("subtasks" not in row["issue"]["fields"] for row in result["issues"])
    paths = [row[1] for row in env.calls if row[0] == "get"]
    assert paths.count("/rest/api/2/search") == 2
    assert len([path for path in paths if path.startswith("/rest/api/2/issue/")]) <= 2
    before = len(env.calls)
    get_descendants(env.client, root, projects=["DL"])
    assert len(env.calls) == before


@pytest.mark.parametrize("late_full_refresh", [False, True], ids=["aged", "concurrent-full-refresh"])
def test_done_parent_refreshes_membership_after_ten_minutes_not_twenty_four_hours(
        build_env, monkeypatch, late_full_refresh):
    env = build_env()
    root, child = _old_done_parent(env, monkeypatch)
    if late_full_refresh:
        fresh_parent = env.provider.get_json(f"/rest/api/2/issue/{root}")
        assert [row["key"] for row in fresh_parent["fields"]["subtasks"]] == [child]
        original = env.client.get_issue_light

        def read(key, *, strict=False):
            raw = original(key, strict=strict)
            if key == root and strict and not raw["fields"]["subtasks"]:
                # A background full read publishes after this request selected the old light row.
                env.cache.set(f"issue:{env.client.env}:{root}", fresh_parent, 600)
            return raw

        monkeypatch.setattr(env.client, "get_issue_light", read)
    env.calls.clear()
    result = get_descendants(env.client, root, projects=["DL"])
    assert result["complete"] and {row["key"] for row in result["issues"]} == {root, child}
    assert any(row[0] == "get" and row[1] == f"/rest/api/2/issue/{root}" for row in env.calls)
    assert env.client.direct_child_keys(root) == [child]
    before = len(env.calls)
    assert get_descendants(env.client, root, projects=["DL"])["complete"]
    assert len(env.calls) == before


def test_membership_keeps_original_parent_source_age(build_env):
    env = build_env()
    root = env.client.create_child(None, "Task", "Source age")['key']
    raw = env.client.get_issue_light(root)
    env.cache.invalidate()
    env.cache.set(f"issueL:{env.client.env}:{root}", raw, 600)
    _age_issue(env.cache, f"issueL:{env.client.env}:{root}", 300)
    source = env.cache.metadata(f"issueL:{env.client.env}:{root}")["fetchedAt"]
    assert get_descendants(env.client, root, projects=["DL"])["complete"]
    assert env.cache.metadata(env.client._direct_child_cache_key(root))["fetchedAt"] == source


@pytest.mark.parametrize("error", [UpstreamUnavailable("offline"), SessionExpired("expired")])
def test_done_membership_refresh_never_caches_failed_empty_and_preserves_auth(build_env, monkeypatch, error):
    env = build_env()
    root, _ = _old_done_parent(env, monkeypatch)
    original = env.provider.get_json
    def get(path, *args, **kwargs):
        if path == f"/rest/api/2/issue/{root}":
            raise error
        return original(path, *args, **kwargs)
    monkeypatch.setattr(env.provider, "get_json", get)
    if isinstance(error, SessionExpired):
        with pytest.raises(SessionExpired):
            get_descendants(env.client, root, projects=["DL"])
    else:
        result = get_descendants(env.client, root, projects=["DL"])
        assert result["partial"] and result["total"] == 1
        assert result["missing"] == [{"key": root, "parentKey": None, "kind": "transport", "stage": "children"}]
    assert env.cache.get(env.client._direct_child_cache_key(root)) is None


def test_write_during_parent_refresh_fences_both_issue_and_membership(build_env, monkeypatch):
    env = build_env()
    root, _ = _old_done_parent(env, monkeypatch)
    original = env.provider.get_json
    def get(path, *args, **kwargs):
        response = original(path, *args, **kwargs)
        if path == f"/rest/api/2/issue/{root}":
            env.cache.invalidate(f"issueL:{env.client.env}:{root}")
        return response
    monkeypatch.setattr(env.provider, "get_json", get)
    get_descendants(env.client, root, projects=["DL"])
    assert env.cache.get(f"issueL:{env.client.env}:{root}") is None
    assert env.cache.get(env.client._direct_child_cache_key(root)) is None


def test_epic_query_is_scoped_before_search_and_never_uses_unscoped_agile(build_env, monkeypatch):
    env = build_env()
    root = env.client.create_child(None, "Epic", "Scoped Epic")['key']
    queries = []
    def search(query, **kwargs):
        queries.append(query)
        return []
    monkeypatch.setattr(env.client, "_search", search)
    monkeypatch.setattr(env.client, "_fetch_agile_epic_children", lambda *_: pytest.fail("unscoped Agile query"))
    result = get_descendants(env.client, root, projects=["DL"])
    assert result["complete"] and result["total"] == 1
    assert queries == [f'project in ("DL") AND "Epic Link" = {root}']


def test_failed_child_ledger_has_independent_pagination(build_env, monkeypatch):
    env = build_env()
    root = env.client.create_child(None, "Task", "Missing children")['key']
    children = [f"DL-{100000 + n}" for n in range(7)]
    env.cache.set(env.client._direct_child_cache_key(root), children, 600)
    original = env.client.get_issue_light
    monkeypatch.setattr(env.client, "prefetch_issues", lambda *_args, **_kwargs: None)
    def issue(key, **kwargs):
        if key in children:
            raise PermissionDenied("/issue/" + key)
        return original(key, **kwargs)
    monkeypatch.setattr(env.client, "get_issue_light", issue)
    first = get_descendants(env.client, root, projects=["DL"], max_results=3)
    second = get_descendants(env.client, root, projects=["DL"], max_results=3, missing_start_at=3)
    assert first["partial"] and first["total"] == 1 and not first["hasMore"]
    assert first["missingTotal"] == 7 and first["missingHasMore"] and first["nextMissingStartAt"] == 3
    assert [row["key"] for row in first["missing"] + second["missing"]] == children[:6]
    final = get_descendants(env.client, root, projects=["DL"], max_results=3, missing_start_at=6)
    assert final["missingTotal"] == 7 and not final["missingHasMore"] and final["nextMissingStartAt"] is None
    assert final["missing"][0]["key"] == children[6]


def test_oversized_hierarchy_projection_keeps_identity_and_explicit_details_pointer(build_env):
    env = build_env()
    root = env.client.create_child(None, "Task", "Oversized projection")['key']
    raw = env.client.get_issue_light(root)
    raw["fields"]["summary"] = "큰 제목" * 9000
    env.cache.invalidate()
    env.cache.set(f"issueL:{env.client.env}:{root}", raw, 600)
    page = get_descendants(env.client, root, projects=["DL"])
    assert page["complete"] and not page["hasMore"] and page["returned"] == 1
    assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) <= 8192
    row = page["issues"][0]
    assert row["key"] == root and row["projectionOmissions"] == ["issue.fields"]
    assert row["details"] == {"tool": "get_ticket", "arguments": {"key": root}}
