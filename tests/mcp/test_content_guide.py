"""Fallback guide reads and discovered input contracts remain usable before Jira login."""

from hashlib import sha256

import anyio
from jsonschema import Draft202012Validator
from mcp import Client


def test_guide_tool_matches_resource_manifest_and_never_accesses_jira(build_env, monkeypatch):
    env = build_env()

    def unavailable():
        raise AssertionError("Guide lookup must work before Jira client/session initialization")

    monkeypatch.setattr(env.runtime, "get_client", unavailable)

    async def scenario():
        async with Client(env.runtime.server) as client:
            catalog = {tool.name: tool for tool in (await client.list_tools()).tools}
            tool = catalog["get_content_guide"]
            assert tool.annotations.read_only_hint and not tool.annotations.destructive_hint
            validator = Draft202012Validator(tool.output_schema)
            index = await client.call_tool("get_content_guide", {})
            assert not index.is_error
            validator.validate(index.structured_content)
            for entry in index.structured_content["data"]["skills"]:
                name = entry["frontmatter"]["name"]
                prefix = f"skill://{name}/"
                for expected in entry["resources"]:
                    result = await client.call_tool("get_content_guide", {
                        "skill": name, "path": expected["uri"].removeprefix(prefix)})
                    assert not result.is_error, result
                    validator.validate(result.structured_content)
                    doc = result.structured_content["data"]
                    raw = doc["text"].encode("utf-8")
                    assert doc["uri"] == expected["uri"]
                    assert doc["size"] == expected["size"] == len(raw)
                    assert doc["digest"] == expected["digest"] == "sha256:" + sha256(raw).hexdigest()
                    resource = await client.read_resource(doc["uri"])
                    assert resource.contents[0].text == doc["text"]
            for arguments in ({"skill": "unknown"}, {"skill": "ltm-task-authoring", "path": "../../config/jira.yml"},
                              {"skill": "ltm-task-authoring", "path": "https://example.com/"}):
                result = await client.call_tool("get_content_guide", arguments)
                assert result.is_error
            assert env.calls == []

    anyio.run(scenario)


def test_authoring_input_schemas_explain_formats_without_changing_optional_fields(build_env):
    env = build_env()

    async def scenario():
        async with Client(env.runtime.server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            for name, model in (("create_task", "CreateInput"), ("create_epic", "EpicInput"),
                                ("update_fields", "FieldsInput")):
                body = tools[name].input_schema["$defs"][model]
                description = body["properties"]["descriptionHtml"]
                assert "Editor HTML" in description["description"]
                assert "descriptionHtml" not in body.get("required", [])
                assert any(row.get("type") == "null" for row in description["anyOf"])
            for name in ("add_comment", "edit_comment", "update_description"):
                field = tools[name].input_schema["properties"]["html"]
                assert field["type"] == "string" and "not Markdown" in field["description"]
            assert "Markdown" in tools["bulk_validate"].description
            assert "get_content_guide" in tools["bulk_create"].description or "bulk authoring guide" in tools["bulk_create"].description

    anyio.run(scenario)
