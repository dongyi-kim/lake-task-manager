"""Comment editing reuses the UI's editor HTML instead of the display preview."""

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import Client


@pytest.mark.parametrize("comment_format", ["wiki", "html"])
def test_comment_source_round_trip_preserves_existing_mention(build_env, comment_format):
    env = build_env()
    env.runtime.settings.comment_format = comment_format

    async def scenario():
        async with Client(env.runtime.server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            source_tool = tools["get_comment_source"]
            assert source_tool.annotations.read_only_hint
            validator = Draft202012Validator(source_tool.output_schema)

            async def call(name, arguments):
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result.structured_content
                return result.structured_content

            created = await call("create_task", {"body": {
                "type": "Task", "summary": "Comment edit source", "clientMutationId": "source:create:0001"}})
            key = created["data"]["key"]
            comment = await call("add_comment", {
                "key": key, "client_mutation_id": "source:add:0001",
                "html": '<p><span data-type="mention" data-id="u02">@Reviewer</span> '
                        '<strong>Original finding</strong> and <em>unchanged context</em></p>',
            })
            cid = str(comment["data"]["id"])
            arguments = {"key": key, "comment_id": cid}
            first = await call("get_comment_source", arguments)
            validator.validate(first)
            editor_html = first["data"]["html"]
            assert first["data"]["id"] == cid
            assert 'data-type="mention"' in editor_html and 'data-id="u02"' in editor_html
            assert "<strong>Original finding</strong>" in editor_html
            assert "<em>unchanged context</em>" in editor_html

            await call("edit_comment", {**arguments, "client_mutation_id": "source:edit:0001",
                                        "html": editor_html.replace("Original finding", "Updated finding")})
            after = await call("get_comment_source", arguments)
            validator.validate(after)
            assert after["data"]["id"] == cid
            assert after["data"]["html"] == editor_html.replace("Original finding", "Updated finding")
            stored = env.store.issues[key]["comments"]
            assert len(stored) == 1 and str(stored[0]["id"]) == cid
            if comment_format == "wiki":
                assert "[~u02]" in stored[0]["body"]
            else:
                assert 'class="user-hover"' in stored[0]["body"]
                assert "/secure/ViewProfile.jspa?name=u02" in stored[0]["body"]

    anyio.run(scenario)


def test_comment_source_rejects_invalid_or_unavailable_source(build_env):
    env = build_env()

    async def scenario():
        async with Client(env.runtime.server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            validator = Draft202012Validator(tools["get_comment_source"].output_schema)
            for key, cid, code in (
                ("DL-1", "999999999999", "not_found"),
                ("DL-1", "../comments", "invalid_request"),
                ("OUTSIDE-1", "1", "permission_denied"),
            ):
                before = len(env.calls)
                response = await client.call_tool("get_comment_source", {"key": key, "comment_id": cid})
                assert response.is_error
                payload = response.structured_content
                validator.validate(payload)
                assert payload["error"]["code"] == code and "data" not in payload
                if code != "not_found":
                    assert len(env.calls) == before

    anyio.run(scenario)
