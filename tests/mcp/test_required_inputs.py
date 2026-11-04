"""Discoverable write contracts, with the real SDK and no external services."""

import anyio
from mcp import Client


def test_unknown_write_fields_are_rejected_before_any_jira_call(build_env):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            for tool, arguments in (
                ("create_task", {"body": {"type": "Task", "summary": "valid", "title": "typo",
                                           "clientMutationId": "schema:create:0001"}}),
                ("create_epic", {"body": {"summary": "valid", "title": "typo",
                                           "clientMutationId": "schema:epic:0001"}}),
                ("update_fields", {"key": "DL-1", "body": {"name": "silently lost rename",
                                                           "clientMutationId": "schema:rename:0001"}}),
                ("add_issue_link", {"key": "DL-1", "other_key": "DL-2", "direction": "backward",
                                    "client_mutation_id": "schema:link:0001"}),
                ("create_task", {"body": {"type": "Task", "summary": "must not publish",
                                           "clientMutationId": "schema:dry-run:0001"}, "dry_run": True}),
                ("transition_ticket", {"key": "DL-1", "body": {"id": "1",
                    "clientMutationId": "schema:time:0001"}, "timeSpent": "fixture-private-input"}),
            ):
                result = await mcp.call_tool(tool, arguments)
                assert result.is_error, tool
                assert "fixture-private-input" not in result.content[0].text
            assert env.calls == []
    anyio.run(scenario)


def test_catalog_explains_required_bodies_ids_and_bulk_shapes(build_env):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            catalog = {tool.name: tool for tool in (await mcp.list_tools()).tools}
            assert all(tool.input_schema["additionalProperties"] is False for tool in catalog.values())
            for tool, model, required in (
                ("create_task", "CreateInput", {"type", "summary", "clientMutationId"}),
                ("create_epic", "EpicInput", {"summary", "clientMutationId"}),
                ("update_fields", "FieldsInput", {"clientMutationId"}),
            ):
                body = catalog[tool].input_schema["$defs"][model]
                assert body["additionalProperties"] is False
                assert set(body["required"]) == required
                assert body["properties"]["clientMutationId"]["pattern"].startswith("^")
                assert body["properties"]["summary"]["description"]
                assert "YYYY-MM-DD" in body["properties"]["duedate"]["description"]
            assert "rename" in catalog["update_fields"].input_schema["$defs"]["FieldsInput"]["properties"]["summary"]["description"]
            fields = catalog["bulk_create"].input_schema["properties"]["items"]
            assert (fields["minItems"], fields["maxItems"]) == (1, 100)
            assert fields["items"]["required"] == ["summary", "type"]
            assert fields["items"]["anyOf"] == [{"required": ["epic"]}, {"required": ["parent"]}]
            assert "Markdown" in fields["items"]["properties"]["description"]["description"]
            draft = catalog["bulk_validate"].input_schema["properties"]["items"]["items"]
            assert "required" not in draft and "type" not in draft["properties"]["summary"]
            assert set(catalog["add_issue_link"].input_schema["properties"]["direction"]["enum"]) == {"inward", "outward"}
            identifier = catalog["delete_ticket"].input_schema["properties"]["client_mutation_id"]
            assert (identifier["minLength"], identifier["maxLength"]) == (8, 128)
    anyio.run(scenario)


def test_bulk_draft_schema_preserves_indexed_validation_and_unknown_field_warnings(build_env):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("bulk_validate", {"mode": "task", "items": [
                {"summary": 3, "unknownDraftField": "preserve warning"},
                {"summary": "valid", "type": "Task", "epic": None},
            ]})
            data = result.structured_content["data"]
            assert not data["ok"]
            assert {row["field"] for row in data["errors"] if row["index"] == 0} >= {"summary", "type", "epic"}
            assert any(row["index"] == 0 and row["field"] == "unknownDraftField" for row in data["warnings"])
            assert not any(method in ("post", "put", "delete") for method, _path, _body in env.calls)
    anyio.run(scenario)
