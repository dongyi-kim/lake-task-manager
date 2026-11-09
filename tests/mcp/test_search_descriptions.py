"""Optional batched descriptions reduce real Jira requests without changing other fields."""
import anyio
from jsonschema import Draft202012Validator
from mcp import Client
import pytest


def seed_bodies(env):
    keys = []
    for number in range(3):
        response = env.provider._client.post("/rest/api/2/issue", json={"fields": {
            "project": {"key": "DL"}, "issuetype": {"name": "Task"},
            "summary": f"batch body {number}", "description": f"complete source body {number}"}})
        assert response.status_code == 201, response.text
        keys.append(response.json()["key"])
    env.calls.clear()
    return keys


def test_three_bodies_use_one_jira_search_without_per_issue_gets(build_env):
    env = build_env()
    keys = seed_bodies(env)
    async def scenario():
        async with Client(env.runtime.server) as client:
            tool = next(t for t in (await client.list_tools()).tools if t.name == "search_issues")
            assert "include_description" in tool.input_schema["properties"]
            assert "fields" not in tool.input_schema["properties"]
            args = {"jql": "key in (" + ",".join(keys) + ") ORDER BY key ASC", "include_description": True}
            response = await client.call_tool("search_issues", args)
            assert not response.is_error, response.structured_content
            Draft202012Validator(tool.output_schema).validate(response.structured_content)
            data = response.structured_content["data"]
            assert data["total"] == 3 and not data["hasMore"]
            assert data["descriptionRequested"] is True and data["descriptionMissingKeys"] == []
            assert {row["key"] for row in data["issues"]} == set(keys)
            assert {row["fields"]["description"] for row in data["issues"]} == {
                f"complete source body {number}" for number in range(3)}
            searches = [call for call in env.calls if call[1] == "/rest/api/2/search"]
            assert len(searches) == 1, env.calls
            assert not [call for call in env.calls if call[1].startswith("/rest/api/2/issue/")]
            requested = set(searches[0][2]["params"]["fields"].split(","))
            assert "description" in requested and not {"attachment", "issuelinks"} & requested
            before = len(env.calls)
            compact = await client.call_tool("search_issues", {**args, "detail": "summary"})
            assert not compact.is_error and len(env.calls) == before
            assert all("description" in row["fields"] for row in compact.structured_content["data"]["issues"])
            # Partial read projection cannot become a canonical full/light UI issue.
            assert env.cache.get("issue:mock:" + keys[0]) is None
            assert env.cache.get("issueL:mock:" + keys[0]) is None
            raw = env.client.get_issue(keys[0])
            assert "subtasks" in raw["fields"] and "issuelinks" in raw["fields"]
    anyio.run(scenario)


def test_body_defaults_off_and_on_off_snapshots_do_not_leak_or_mutate_body_cache(build_env):
    env = build_env()
    keys = seed_bodies(env)
    async def scenario():
        async with Client(env.runtime.server) as client:
            args = {"jql": "key in (" + ",".join(keys) + ") ORDER BY key ASC"}
            off = await client.call_tool("search_issues", args)
            assert not off.is_error
            assert all("description" not in row["fields"] for row in off.structured_content["data"]["issues"])
            assert all("description" not in call[2]["params"]["fields"].split(",") for call in env.calls
                       if call[1] == "/rest/api/2/search")
            on = await client.call_tool("search_issues", {**args, "include_description": True})
            assert not on.is_error
            assert off.structured_content["data"]["snapshotId"] != on.structured_content["data"]["snapshotId"]
            for detail in ("full", "summary"):
                off_again = await client.call_tool("search_issues", {**args, "include_description": False, "detail": detail})
                assert not off_again.is_error
                assert all("description" not in row["fields"] for row in off_again.structured_content["data"]["issues"])
            on_again = await client.call_tool("search_issues", {**args, "include_description": True})
            assert on_again.structured_content["data"]["issues"] == on.structured_content["data"]["issues"]
            removed_option = await client.call_tool("search_issues", {**args, "fields": ["description"]})
            assert removed_option.is_error
    anyio.run(scenario)


def test_missing_requested_body_is_disclosed_and_returned_null_remains_empty(build_env):
    env = build_env()
    keys = seed_bodies(env)
    original = env.provider.get_json
    def missing_body(path, *args, **kwargs):
        data = original(path, *args, **kwargs)
        if path == "/rest/api/2/search":
            for issue in data.get("issues", []):
                if issue["key"] == keys[0]:
                    issue["fields"].pop("description", None)
                if issue["key"] == keys[1]:
                    issue["fields"]["description"] = None
        return data
    env.provider.get_json = missing_body
    async def scenario():
        async with Client(env.runtime.server) as client:
            response = await client.call_tool("search_issues", {"jql": "key in (" + ",".join(keys) + ")",
                "include_description": True})
            assert not response.is_error, response.structured_content
            data = response.structured_content["data"]
            assert data["descriptionMissingKeys"] == [keys[0]]
            issues = {row["key"]: row for row in data["issues"]}
            assert "description" not in issues[keys[0]]["fields"]
            assert issues[keys[1]]["fields"]["description"] is None
    anyio.run(scenario)


@pytest.mark.parametrize("rendered", [None, {"description": "<p>cached body</p>", "updated": "today"}])
def test_body_off_strips_cached_raw_and_rendered_bodies_without_mutating_snapshot(build_env, rendered):
    env = build_env()
    keys = seed_bodies(env)
    original = env.provider.get_json
    def extra_cached_fields(path, *args, **kwargs):
        data = original(path, *args, **kwargs)
        if path == "/rest/api/2/search":
            for issue in data.get("issues", []):
                issue["fields"]["description"] = "cached body"
                issue["renderedFields"] = rendered
        return data
    env.provider.get_json = extra_cached_fields
    async def scenario():
        async with Client(env.runtime.server) as client:
            response = await client.call_tool("search_issues", {"jql": "key = " + keys[0]})
            assert not response.is_error, response.structured_content
            row = response.structured_content["data"]["issues"][0]
            assert "description" not in row["fields"]
            assert row.get("renderedFields") is None or "description" not in row["renderedFields"]
            original_page = env.client.search_issues_page('project in ("DL") AND (key = ' + keys[0] + ')')
            assert original_page["issues"][0]["fields"]["description"] == "cached body"
            assert original_page["issues"][0]["renderedFields"] == rendered
    anyio.run(scenario)


def test_description_pagination_reuses_projection_and_respects_configured_scope(build_env):
    env = build_env()
    keys = seed_bodies(env)
    async def scenario():
        async with Client(env.runtime.server) as client:
            args = {"jql": "key in (" + ",".join(keys) + ") ORDER BY key ASC", "max_results": 1,
                    "include_description": True}
            first = await client.call_tool("search_issues", args)
            assert not first.is_error
            data = first.structured_content["data"]
            assert data["hasMore"] and len(data["issues"]) == 1
            before = len(env.calls)
            wrong = await client.call_tool("search_issues", {**args, "snapshot_id": data["snapshotId"],
                                                            "start_at": 1, "include_description": False})
            assert wrong.is_error
            second = await client.call_tool("search_issues", {**args, "snapshot_id": data["snapshotId"], "start_at": 1})
            assert not second.is_error
            assert "description" in second.structured_content["data"]["issues"][0]["fields"]
            assert len(env.calls) == before
            env.runtime.settings.search_jira_projects = ["XX"]
            denied_scope = await client.call_tool("search_issues", args)
            assert not denied_scope.is_error
            assert denied_scope.structured_content["data"]["issues"] == []
    anyio.run(scenario)
