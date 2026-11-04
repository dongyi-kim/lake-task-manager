"""Real MCP/Mock Jira transitions, including completion policy absent from Jira screen metadata."""

import anyio
from jsonschema import Draft202012Validator
from mcp import Client


def test_completion_requirements_are_discoverable_and_retry_does_not_repeat_write(build_env):
    env = build_env()
    original_get = env.provider.get_json

    def omit_comment_metadata(path, *args, **kwargs):
        result = original_get(path, *args, **kwargs)
        if path.endswith("/transitions"):
            for transition in result["transitions"]:
                transition.get("fields", {}).pop("comment", None)
        return result

    env.provider.get_json = omit_comment_metadata

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            catalog = {tool.name: tool for tool in (await mcp.list_tools()).tools}
            input_model = catalog["transition_ticket"].input_schema["$defs"]["TransitionInput"]
            assert "id" in input_model["required"]
            assert "Done" in input_model["properties"]["commentHtml"]["description"]

            async def call(name, arguments):
                result = await mcp.call_tool(name, arguments)
                Draft202012Validator(catalog[name].output_schema).validate(result.structured_content)
                assert not result.is_error, result.structured_content
                return result.structured_content["data"]

            key = (await call("create_task", {"body": {"type": "Task", "summary": "Completion screen",
                    "clientMutationId": "transition:create:001"}}))["key"]
            options = (await call("list_options", {"kind": "transitions", "key": key}))["options"]
            done = next(option for option in options if option["toCategory"] == "done")
            assert done["commentRequired"] is True
            assert all(field["system"] != "comment" for field in done["fields"]["fields"])
            fields = {field["system"] or field["id"]: field for field in done["fields"]["fields"]}
            body = {"id": done["id"], "clientMutationId": "transition:complete:001"}
            if "assignee" in fields:
                body["assignee"] = "u01"
            if "resolution" in fields:
                body["resolution"] = next(item["name"] for item in fields["resolution"]["allowedValues"]
                                          if item["name"].lower() == "done")
            assert fields["worklog"]["required"] is True
            assert fields["worklog"]["inputFields"] == ["body.days", "body.hours", "body.minutes"]
            path = f"/rest/api/2/issue/{key}/transitions"
            missing_time = await mcp.call_tool("transition_ticket", {"key": key, "body": body})
            assert missing_time.is_error and "worklog" in missing_time.structured_content["error"]["message"]
            body.update(hours=1, minutes=30)
            rejected = await mcp.call_tool("transition_ticket", {"key": key, "body": body})
            assert rejected.is_error and "코멘트" in rejected.structured_content["error"]["message"]
            assert not any(method == "post" and target == path for method, target, _ in env.calls)
            body["commentHtml"] = "<p>검증한 완료 기록</p>"
            for invalid in ({"timeSpent": "2h"}, {"worklog": "2h"}, {"hours": -1}):
                invalid_result = await mcp.call_tool("transition_ticket", {"key": key, "body": {**body, **invalid}})
                assert invalid_result.is_error
            assert not any(method == "post" and target == path for method, target, _ in env.calls)
            for _ in range(2):
                await call("transition_ticket", {"key": key, "body": body})
            assert sum(method == "post" and target == path for method, target, _ in env.calls) == 1
            assert (await call("get_ticket", {"key": key}))["statusCategory"] == "done"
            comments = (await call("get_comments", {"key": key}))["comments"]
            assert sum("검증한 완료 기록" in comment["html"] for comment in comments) == 1
            assert sum(row["seconds"] for row in env.store.issues[key]["worklog"]) == 5400
            denied = await mcp.call_tool("update_fields", {"key": key, "body": {
                "summary": "Rename while done", "clientMutationId": "transition:denied-rename:001"}})
            assert denied.is_error
            available = (await call("list_options", {"kind": "transitions", "key": key}))["options"]
            reopen = next(option for option in available if option["to"].lower() == "reopened")
            await call("transition_ticket", {"key": key, "body": {
                "id": reopen["id"], "clientMutationId": "transition:reopen:001"}})
            await call("update_fields", {"key": key, "body": {
                "summary": "Renamed after reopening", "clientMutationId": "transition:rename:001"}})
            assert (await call("get_ticket", {"key": key}))["summary"] == "Renamed after reopening"
            posts = [payload for method, target, payload in env.calls if method == "post" and target == path]
            assert "worklog" not in posts[-1].get("update", {})

    anyio.run(scenario)
