"""Real MCP/Mock Jira transitions, including optional completion notes and Jira requirements."""

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import Client


@pytest.mark.parametrize("comment_html", [None, "<p> &nbsp;<br></p><p>\u200b</p>",
                                         "<p>검증한 완료 기록</p>"],
                         ids=["omitted", "empty-editor", "completion-note"])
def test_completion_requirements_are_discoverable_and_retry_does_not_repeat_write(build_env, comment_html):
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
            assert "Recommended for Done" in input_model["properties"]["commentHtml"]["description"]

            async def call(name, arguments):
                result = await mcp.call_tool(name, arguments)
                Draft202012Validator(catalog[name].output_schema).validate(result.structured_content)
                assert not result.is_error, result.structured_content
                return result.structured_content["data"]

            key = (await call("create_task", {"body": {"type": "Task", "summary": "Completion screen",
                    "clientMutationId": "transition:create:001"}}))["key"]
            options = (await call("list_options", {"kind": "transitions", "key": key}))["options"]
            done = next(option for option in options if option["toCategory"] == "done")
            assert done["commentRequired"] is False
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
            if comment_html is not None:
                body["commentHtml"] = comment_html
            for invalid in ({"timeSpent": "2h"}, {"worklog": "2h"}, {"hours": -1}):
                invalid_result = await mcp.call_tool("transition_ticket", {"key": key, "body": {**body, **invalid}})
                assert invalid_result.is_error
            assert not any(method == "post" and target == path for method, target, _ in env.calls)
            for _ in range(2):
                await call("transition_ticket", {"key": key, "body": body})
            assert sum(method == "post" and target == path for method, target, _ in env.calls) == 1
            completed = await call("get_ticket", {"key": key})
            assert completed["statusCategory"] == "done"
            assert completed["timeSpentSeconds"] == 5400
            comments = (await call("get_comments", {"key": key}))["comments"]
            has_note = "검증한 완료 기록" in (comment_html or "")
            assert len(comments) == int(has_note)
            assert sum("검증한 완료 기록" in comment["html"] for comment in comments) == int(has_note)
            completion_post = next(payload for method, target, payload in env.calls
                                   if method == "post" and target == path)
            assert ("comment" in completion_post.get("update", {})) == has_note
            assert sum(row["seconds"] for row in env.store.issues[key]["worklog"]) == 5400
            denied = await mcp.call_tool("update_fields", {"key": key, "body": {
                "summary": "Rename while done", "clientMutationId": "transition:denied-rename:001"}})
            assert denied.is_error
            error = denied.structured_content["error"]
            assert (error["code"], error["reason"], error["status"]) == ("conflict", "ticket_completed", 409)
            assert error["nextAction"] == {"tool": "list_options", "arguments": {
                "kind": "transitions", "key": key}}
            Draft202012Validator(catalog["update_fields"].output_schema).validate(denied.structured_content)
            assert not any(method == "put" for method, _target, _body in env.calls)
            await call("add_comment", {"key": key, "html": "<p>완료 후 추가 확인</p>",
                                      "client_mutation_id": "transition:done-comment:001"})
            assert (await call("get_ticket", {"key": key, "fresh": True}))["statusCategory"] == "done"
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
