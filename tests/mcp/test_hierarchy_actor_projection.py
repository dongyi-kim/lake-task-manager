"""Hierarchy actor identities stay useful without copying full Jira profiles."""
from copy import deepcopy
import json

import anyio
from mcp import Client
import pytest

from app.infra.cache_policy import read_scope
from app.jira.hierarchy_service import get_descendants


@pytest.mark.parametrize(("profile", "expected"), [
    ({"accountId": "acct-1", "name": "login-1", "key": "user-1", "displayName": "이름🙂",
      "self": "/user/1", "timeZone": "Asia/Seoul", "avatarUrls": {"48x48": "avatar"},
      "emailAddress": "not-an-id@example.test", "active": True, "extra": {"private": 1}},
     {"accountId": "acct-1", "name": "login-1", "key": "user-1", "displayName": "이름🙂",
      "self": "/user/1", "timeZone": "Asia/Seoul"}),
    ({"accountId": "cloud-only", "avatarUrls": {}}, {"accountId": "cloud-only"}),
    ({"name": "dc-login", "emailAddress": "private@example.test"}, {"name": "dc-login"}),
    ({"key": "legacy-key", "active": False}, {"key": "legacy-key"}),
    ({"displayName": "동명이인🙂", "extra": 1}, {"displayName": "동명이인🙂"}),
    ({"emailAddress": "unknown@example.test"}, {}),
    ({}, {}), (None, None), ("unknown-shape", "unknown-shape"),
])
def test_supplied_actor_variants_do_not_leak_profiles_or_mutate_source(build_env, monkeypatch, profile, expected):
    env = build_env()
    monkeypatch.setattr(env.client, "_reprime", lambda *_a, **_k: None)
    root = env.client.create_child(None, "Task", "Actor projection")["key"]
    raw = env.client.get_issue_light(root)
    raw["fields"].update(assignee=deepcopy(profile), reporter={"name": "other", "key": "other-key",
        "displayName": "동명이인🙂", "avatarUrls": {"48x48": "other-avatar"}})
    original = deepcopy(raw)
    env.cache.invalidate()
    cache_key = f"issueL:{env.client.env}:{root}"
    env.cache.set(cache_key, raw, 600)
    stamp = env.cache.metadata(cache_key)["fetchedAt"]
    observed_sources, read = [], env.client.get_issue_light

    def observed(key, **kwargs):
        value = read(key, **kwargs)
        observed_sources.append(value)
        return value
    monkeypatch.setattr(env.client, "get_issue_light", observed)
    before = len(env.calls)
    with read_scope(cache_mode="hot"):
        page = get_descendants(env.client, root, projects=["DL"])
    row, = page["issues"]
    assert "assignee" in row["issue"]["fields"]
    assert row["issue"]["fields"]["assignee"] == expected
    assert row["issue"]["fields"]["reporter"] == {
        "name": "other", "key": "other-key", "displayName": "동명이인🙂"}
    assert page["complete"] and page["total"] == 1 and not page["missing"]
    assert observed_sources == [original]
    assert env.cache.get(cache_key) == original and env.cache.metadata(cache_key)["fetchedAt"] == stamp
    assert len(env.calls) == before
    # A consumer can edit its actor dictionary without changing the read source.
    row["issue"]["fields"]["reporter"]["displayName"] = "edited result"
    assert observed_sources == [original] and env.cache.get(cache_key) == original


def test_missing_actor_fields_stay_absent(build_env, monkeypatch):
    env = build_env()
    monkeypatch.setattr(env.client, "_reprime", lambda *_a, **_k: None)
    root = env.client.create_child(None, "Task", "Absent actors")["key"]
    raw = env.client.get_issue_light(root)
    for role in ("assignee", "reporter"):
        raw["fields"].pop(role, None)
    env.cache.invalidate()
    env.cache.set(f"issueL:{env.client.env}:{root}", raw, 600)
    with read_scope(cache_mode="hot"):
        page = get_descendants(env.client, root, projects=["DL"])
    assert not {"assignee", "reporter"} & page["issues"][0]["issue"]["fields"].keys()


def test_sdk_projection_precedes_utf8_budget_and_preserves_fields_parents_and_cursors(build_env, monkeypatch):
    env = build_env()
    monkeypatch.setattr(env.client, "_reprime", lambda *_a, **_k: None)
    epic = env.client.create_child(None, "Epic", "Outside traversal")["key"]
    task = env.client.create_child(epic, "Task", "Task")["key"]
    children = [env.client.create_child(task, "Sub-task", f"Child {n}")["key"] for n in range(4)]
    keys = [task, *children]
    sources = {key: env.client.get_issue_light(key) for key in [epic, *keys]}
    for index, key in enumerate(keys):
        fields = sources[key]["fields"]
        fields.update(summary="한🙂" * (80 + index), labels=["범위🙂"],
            assignee={"accountId": f"a{index}", "displayName": "사용자🙂" * 90,
                      "avatarUrls": {"48x48": "https://example.test/" + "avatar" * 2000}},
            reporter={"name": f"r{index}", "displayName": "동명이인🙂" * 90,
                      "emailAddress": "private@example.test", "active": True})
        fields[env.client.s.sp_field_id] = 0 if index == 0 else None
        fields[env.client.s.epic_name_field_id] = "원본 이름🙂"
    env.cache.invalidate()
    for key, raw in sources.items():
        env.cache.set(f"issueL:{env.client.env}:{key}", raw, 600)
    originals = deepcopy(sources)

    async def collect():
        rows, offsets, page_count = [], [], 0
        async with Client(env.runtime.server) as mcp:
            offset = 0
            while True:
                response = await mcp.call_tool("get_descendants", {"root_key": task,
                    "start_at": offset, "max_results": 100, "cache_mode": "hot"})
                assert not response.is_error
                page = response.structured_content["data"]
                assert len(json.dumps(page, ensure_ascii=False, default=str).encode()) <= 8192
                assert page["startAt"] == offset and page["total"] == len(keys)
                assert page["complete"] and not page["partial"] and page["missingTotal"] == 0
                assert not page["missingHasMore"] and page["nextMissingStartAt"] is None
                rows.extend(page["issues"]); offsets.append(offset); page_count += 1
                assert all("projectionOmissions" not in row for row in page["issues"])
                if not page["hasMore"]:
                    assert page["nextStartAt"] is None
                    break
                assert page["nextStartAt"] == offset + page["returned"] > offset
                offset = page["nextStartAt"]
            # A SubTask-root has an outer null traversal parent but keeps its actual parent.
            sub = await mcp.call_tool("get_descendants", {"root_key": children[0], "cache_mode": "hot"})
        assert page_count > 1 and offsets == sorted(set(offsets))
        return rows, sub.structured_content["data"]["issues"][0]

    rows, sub = anyio.run(collect)
    assert [row["key"] for row in rows] == keys
    for row in rows:
        fields, source = row["issue"]["fields"], originals[row["key"]]["fields"]
        assert fields["assignee"] == {"accountId": source["assignee"]["accountId"],
                                      "displayName": source["assignee"]["displayName"]}
        assert fields["reporter"] == {"name": source["reporter"]["name"],
                                      "displayName": source["reporter"]["displayName"]}
        for name, value in fields.items():
            if name not in {"assignee", "reporter"}:
                assert value == source[name]
        assert fields[env.client.s.sp_field_id] == (0 if row["key"] == task else None)
        assert row["epicKey"] == epic
        assert row["parentKey"] == (None if row["key"] == task else task)
        assert row["depth"] == (0 if row["key"] == task else 1)
    assert sub["parentKey"] is None and sub["issue"]["fields"]["parent"]["key"] == task
    assert sub["epicKey"] == epic
    assert sources == originals
    assert all(env.cache.get(f"issueL:{env.client.env}:{key}") == raw for key, raw in originals.items())
