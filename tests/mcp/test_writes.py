import base64
import json

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.auth.base import UpstreamUnavailable


def test_sdk_writes_share_ui_commands_receipts_and_cache_invalidation(build_env):
    env = build_env()
    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                                           base_url="http://127.0.0.1:4457",
                                           headers={"Authorization": "Bearer " + env.runtime.credentials.token()}) as http:
                async with Client(streamable_http_client(env.runtime.url, http_client=http)) as mcp:
                    async def call(name, arguments):
                        result = await mcp.call_tool(name, arguments)
                        assert not result.is_error, result.structured_content or result.content
                        return result.structured_content["data"]
                    body = {"type": "Task", "summary": "MCP created", "clientMutationId": "mcp:create:0001"}
                    created = await call("create_task", {"body": body})
                    key = created["key"]
                    before = sum(method == "post" and path == "/rest/api/2/issue" for method, path, _ in env.calls)
                    assert (await call("create_task", {"body": body}))["key"] == key
                    assert sum(method == "post" and path == "/rest/api/2/issue" for method, path, _ in env.calls) == before
                    assert env.store.issues[key]["reporter"] == "u01"
                    assert (await http.get(f"/api/ticket/{key}")).json()["summary"] == "MCP created"
                    query = {"jql": f"key = {key}"}
                    assert (await call("search_issues", query))["total"] == 1
                    await call("update_fields", {"key": key, "body": {
                        "summary": "MCP edited", "descriptionHtml": "<p>본문 수정</p>",
                        "clientMutationId": "mcp:fields:0001"}})
                    assert (await http.get(f"/api/ticket/{key}")).json()["summary"] == "MCP edited"
                    assert (await call("search_issues", query))["issues"][0]["fields"]["summary"] == "MCP edited"
                    denied = await mcp.call_tool("update_fields", {"key": key,
                        "body": {"reporter": "u02", "clientMutationId": "mcp:reporter:0001"}})
                    assert denied.is_error and env.store.issues[key]["reporter"] == "u01"

                    comment = await call("add_comment", {"key": key, "html": "<p>댓글 추가</p>",
                                                          "client_mutation_id": "mcp:comment:0001"})
                    cid = str(comment["id"])
                    await call("get_comments", {"key": key})  # prime the UI's comments cache
                    await call("edit_comment", {"key": key, "comment_id": cid, "html": "<p>댓글 수정</p>",
                                                 "client_mutation_id": "mcp:comment-edit:0001"})
                    assert "댓글 수정" in json.dumps(await call("get_comments", {"key": key}), ensure_ascii=False)
                    transitions = (await call("list_options", {"kind": "transitions", "key": key}))["options"]
                    transition = next(t for t in transitions if t.get("toCategory") == "inprogress")
                    await call("transition_ticket", {"key": key, "body": {"id": transition["id"],
                        "targetStatusId": transition.get("toId", ""), "clientMutationId": "mcp:transition:0001"}})
                    assert (await http.get(f"/api/ticket/{key}")).json()["statusCategory"] == "inprogress"
                    subtype = (await call("list_options", {"kind": "child_types", "key": key}))["options"][0]
                    child = await call("create_subtask", {"parent_key": key, "body": {
                        "type": subtype, "summary": "MCP child", "clientMutationId": "mcp:child:0001"}})
                    assert child["key"] in json.dumps((await http.get(f"/api/ticket/{key}/children")).json())
                    epic = await call("create_epic", {"body": {"summary": "MCP Epic", "clientMutationId": "mcp:epic:0001"}})
                    assert (await call("get_epic", {"key": epic["key"]}))["epic"]

                    attachment = await call("add_attachment", {"key": key, "filename": "fixture.txt",
                        "base64_data": base64.b64encode(b"fixture bytes").decode(), "client_mutation_id": "mcp:attachment:0001"})
                    assert attachment["id"] in json.dumps(await call("get_attachments", {"key": key}))
                    wrong_owner = await mcp.call_tool("delete_attachment", {
                        "key": child["key"], "attachment_id": attachment["id"],
                        "client_mutation_id": "mcp:wrong-attachment:0001"})
                    assert wrong_owner.is_error
                    assert attachment["id"] in json.dumps(await call("get_attachments", {"key": key}))
                    await call("delete_attachment", {"key": key, "attachment_id": attachment["id"],
                                                     "client_mutation_id": "mcp:attachment-delete:0001"})
                    await call("add_issue_link", {"key": key, "other_key": epic["key"],
                                                   "client_mutation_id": "mcp:link:0001"})
                    link_id = str(env.store.issues[key]["links"][-1]["id"])
                    wrong_link = await mcp.call_tool("delete_issue_link", {
                        "key": key, "other_key": child["key"], "link_id": link_id,
                        "client_mutation_id": "mcp:wrong-link:0001"})
                    assert wrong_link.is_error
                    await call("delete_issue_link", {"key": key, "other_key": epic["key"], "link_id": link_id,
                                                      "client_mutation_id": "mcp:link-delete:0001"})
                    doc = await call("add_document", {"key": key, "url": "https://example.com/doc", "title": "fixture",
                                                      "client_mutation_id": "mcp:document:0001"})
                    await call("delete_document", {"key": key, "link_id": str(doc["id"]),
                                                    "client_mutation_id": "mcp:document-delete:0001"})
                    await call("delete_comment", {"key": key, "comment_id": cid,
                                                   "client_mutation_id": "mcp:comment-delete:0001"})
                    assert cid not in {str(c["id"]) for c in (await call("get_comments", {"key": key}))["comments"]}
                    for number, target in enumerate((child["key"], key, epic["key"])):
                        await call("delete_ticket", {"key": target, "client_mutation_id": f"mcp:delete:{number:04}"})
                    assert key not in env.store.issues
                    assert (await call("search_issues", query))["total"] == 0
    anyio.run(scenario)


def test_object_ids_cannot_address_arbitrary_rest_paths(build_env):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            for name, fields in (
                ("edit_comment", {"comment_id": "../../../user", "html": "x"}),
                ("delete_comment", {"comment_id": "1?other=2"}),
                ("delete_attachment", {"attachment_id": "../1"}),
                ("delete_document", {"link_id": "/rest/api/2/user"}),
                ("delete_issue_link", {"link_id": "1#fragment", "other_key": "DL-2"}),
            ):
                result = await mcp.call_tool(name, {
                    "key": "DL-1", "client_mutation_id": "mcp:invalid-id:0001", **fields})
                assert result.is_error and result.structured_content["error"]["code"] == "invalid_request"
            assert env.calls == []
    anyio.run(scenario)


def test_response_lost_field_edit_is_never_repeated(build_env):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            created = await mcp.call_tool("create_task", {"body": {
                "type": "Task", "summary": "receipt", "clientMutationId": "mcp:uncertain-create:0001"}})
            assert not created.is_error, created
            key = created.structured_content["data"]["key"]
            original = env.provider.put_json
            writes = []
            def lost(path, payload, *args, **kwargs):
                writes.append(path)
                original(path, payload, *args, **kwargs)
                raise UpstreamUnavailable("response lost after commit")
            env.provider.put_json = lost
            args = {"key": key, "body": {"summary": "committed", "clientMutationId": "mcp:uncertain-edit:0001"}}
            for _ in range(2):
                result = await mcp.call_tool("update_fields", args)
                assert result.is_error and result.structured_content["error"]["code"] == "outcome_unknown"
                assert result.structured_content["error"]["repeatWrite"] is False
            assert len(writes) == 1 and env.store.issues[key]["summary"] == "committed"
    anyio.run(scenario)
