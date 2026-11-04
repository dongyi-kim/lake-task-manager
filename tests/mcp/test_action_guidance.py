"""Action restrictions remain structured, discoverable, and non-mutating on denial."""

import anyio
from jsonschema import Draft202012Validator
from mcp import Client


def test_edit_restriction_preserves_status_and_links_to_actual_metadata(build_env):
    env = build_env()  # This user can create but cannot modify Reporter.

    async def scenario():
        async with Client(env.runtime.server) as mcp:
            catalog = {tool.name: tool for tool in (await mcp.list_tools()).tools}
            created = await mcp.call_tool("create_task", {"body": {
                "type": "Task", "summary": "permissions", "clientMutationId": "actions:create:001"}})
            assert not created.is_error
            key = created.structured_content["data"]["key"]
            args = {"key": key, "body": {"reporter": "u02", "clientMutationId": "actions:reporter:001"}}
            for _ in range(2):
                denied = await mcp.call_tool("update_fields", args)
                assert denied.is_error
                Draft202012Validator(catalog["update_fields"].output_schema).validate(denied.structured_content)
                error = denied.structured_content["error"]
                assert (error["code"], error["reason"], error["status"]) == (
                    "permission_denied", "field_not_editable", 403)
                assert error["nextAction"] == {"tool": "list_options", "arguments": {
                    "kind": "edit_fields", "key": key}}
                options = await mcp.call_tool(error["nextAction"]["tool"], error["nextAction"]["arguments"])
                assert not options.is_error
                assert "reporter" not in options.structured_content["data"]["options"]
            assert not any(method in ("put", "delete") for method, _path, _body in env.calls)
            assert env.store.issues[key]["reporter"] == "u01"

            guide = await mcp.call_tool("get_content_guide", {
                "skill": "ltm-task-authoring", "path": "references/ticket-actions.md"})
            assert not guide.is_error
            assert "JRASERVER-27893" in guide.structured_content["data"]["text"]
            assert "JRASERVER-68763" in guide.structured_content["data"]["text"]

    anyio.run(scenario)
