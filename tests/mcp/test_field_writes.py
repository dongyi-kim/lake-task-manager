"""Real SDK field updates and read-before-write failures against disposable Jira fixtures."""

import anyio
from mcp import Client
import pytest

from app.auth.base import UpstreamBusy, UpstreamError, UpstreamUnavailable


@pytest.mark.parametrize("operation", ["delete_ticket", "delete_document"])
def test_delete_permission_read_failure_can_retry_without_duplicate_delete(build_env, monkeypatch, operation):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            created = await mcp.call_tool("create_task", {"body": {
                "type": "Task", "summary": "delete preflight", "clientMutationId": "deleteguard:create:001"}})
            assert not created.is_error
            key = created.structured_content["data"]["key"]
            args = {"key": key, "client_mutation_id": "deleteguard:delete:001"}
            if operation == "delete_document":
                doc = await mcp.call_tool("add_document", {"key": key, "url": "https://example.test/fixture",
                    "client_mutation_id": "deleteguard:document:001"})
                assert not doc.is_error
                args["link_id"] = str(doc.structured_content["data"]["id"])
            original = env.runtime.commands.require_edit
            def unavailable(_key):
                raise UpstreamUnavailable("fixture permission lookup unavailable")
            monkeypatch.setattr(env.runtime.commands, "require_edit", unavailable)
            failed = await mcp.call_tool(operation, args)
            assert failed.is_error and failed.structured_content["error"]["writeExecuted"] is False
            assert not any(method == "delete" for method, _path, _body in env.calls)
            monkeypatch.setattr(env.runtime.commands, "require_edit", original)
            for _ in range(2):
                recovered = await mcp.call_tool(operation, args)
                assert not recovered.is_error, recovered.structured_content
            assert sum(method == "delete" for method, _path, _body in env.calls) == 1
    anyio.run(scenario)


def test_sdk_assignee_due_date_clear_preserves_unrelated_fields(build_env):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            async def call(name, arguments):
                result = await mcp.call_tool(name, arguments)
                assert not result.is_error, result.structured_content or result.content
                return result.structured_content["data"]
            key = (await call("create_task", {"body": {
                "type": "Task", "summary": "before", "clientMutationId": "fields:create:0001"}}))["key"]
            patch = {"assignee": "u02",
                     "duedate": "2027-01-02", "clientMutationId": "fields:set:0001"}
            await call("get_ticket", {"key": key})
            await call("update_fields", {"key": key, "body": patch})
            view = await call("get_ticket", {"key": key})
            assert view["key"] == key and view["summary"] == "before"
            stored = env.store.issues[key]
            assert stored["assignee"] == "u02" and stored["due"].isoformat() == "2027-01-02"
            await call("update_fields", {"key": key, "body": {
                "assignee": "", "duedate": "",
                "clientMutationId": "fields:clear:0001"}})
            stored = env.store.issues[key]
            assert stored["summary"] == "before"
            assert not stored["assignee"] and not stored["due"]
    anyio.run(scenario)


def test_reporter_last_permission_read_failure_does_not_leave_uncertain_receipt(build_env, monkeypatch):
    env = build_env()
    env.store.permissions["MODIFY_REPORTER"] = True
    original = env.provider.get_json
    metadata_reads = []
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            created = await mcp.call_tool("create_task", {"body": {
                "type": "Task", "summary": "reporter", "clientMutationId": "reporter:create:0001"}})
            key = created.structured_content["data"]["key"]
            def late_failure(path, *args, **kwargs):
                if path.endswith("/editmeta"):
                    metadata_reads.append(path)
                    if len(metadata_reads) == 2:
                        raise UpstreamUnavailable("fixture final reporter permission read failed")
                return original(path, *args, **kwargs)
            monkeypatch.setattr(env.provider, "get_json", late_failure)
            args = {"key": key, "body": {"reporter": "u02", "clientMutationId": "reporter:edit:0001"}}
            before = sum(method == "put" for method, _path, _body in env.calls)
            failed = await mcp.call_tool("update_fields", args)
            assert failed.is_error and failed.structured_content["error"]["writeExecuted"] is False
            assert len(metadata_reads) == 2 and env.store.issues[key]["reporter"] == "u01"
            assert sum(method == "put" for method, _path, _body in env.calls) == before
            for _ in range(2):
                recovered = await mcp.call_tool("update_fields", args)
                assert not recovered.is_error, recovered.structured_content
            assert env.store.issues[key]["reporter"] == "u02"
            assert sum(method == "put" for method, _path, _body in env.calls) == before + 1
    anyio.run(scenario)


@pytest.mark.parametrize("operation", ["delete_attachment", "delete_issue_link", "set_vit_tracking"])
def test_ownership_and_label_preflight_outages_allow_safe_same_id_retry(build_env, monkeypatch, operation):
    env = build_env()
    original = env.provider.get_json
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            async def call(name, arguments):
                result = await mcp.call_tool(name, arguments)
                assert not result.is_error, result.structured_content or result.content
                return result.structured_content["data"]
            key = (await call("create_task", {"body": {
                "type": "Task", "summary": "preflight", "clientMutationId": "readfirst:create:0001"}}))["key"]
            args = {"key": key, "client_mutation_id": "readfirst:mutation:0001"}
            if operation == "delete_attachment":
                attachment = await call("add_attachment", {"key": key, "filename": "fixture.txt",
                    "base64_data": "Zml4dHVyZQ==", "client_mutation_id": "readfirst:upload:0001"})
                args["attachment_id"] = attachment["id"]
            elif operation == "delete_issue_link":
                other = (await call("create_task", {"body": {
                    "type": "Task", "summary": "other", "clientMutationId": "readfirst:create:0002"}}))["key"]
                await call("add_issue_link", {"key": key, "other_key": other,
                    "client_mutation_id": "readfirst:link:0001"})
                args.update(other_key=other, link_id=str(env.store.issues[key]["links"][-1]["id"]))
            else:
                args["tracked"] = True
            env.client._invalidate_ticket(key)
            def unavailable(path, *a, **kw):
                if path == f"/rest/api/2/issue/{key}":
                    raise UpstreamUnavailable("fixture ownership/labels lookup failed")
                return original(path, *a, **kw)
            monkeypatch.setattr(env.provider, "get_json", unavailable)
            before = sum(method != "get" for method, _path, _body in env.calls)
            result = await mcp.call_tool(operation, args)
            assert result.is_error, result.structured_content
            assert result.structured_content["error"]["writeExecuted"] is False
            assert sum(method != "get" for method, _path, _body in env.calls) == before
            monkeypatch.setattr(env.provider, "get_json", original)
            await call(operation, args)
            await call(operation, args)
            assert sum(method != "get" for method, _path, _body in env.calls) == before + 1
    anyio.run(scenario)


@pytest.mark.parametrize("failure", ["transport", "server", "busy"])
def test_edit_metadata_outage_is_not_permission_denial_and_same_mutation_can_retry(build_env, monkeypatch, failure):
    env = build_env()
    original = env.provider.get_json
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            created = await mcp.call_tool("create_task", {"body": {
                "type": "Task", "summary": "retained", "clientMutationId": "fields:outage:create"}})
            key = created.structured_content["data"]["key"]
            def unavailable(path, *args, **kwargs):
                if path.endswith("/editmeta"):
                    if failure == "busy":
                        raise UpstreamBusy("fixture capacity exhausted")
                    if failure == "server":
                        raise UpstreamError(503, path, "fixture unavailable")
                    raise UpstreamUnavailable("fixture unavailable")
                return original(path, *args, **kwargs)
            monkeypatch.setattr(env.provider, "get_json", unavailable)
            options = await mcp.call_tool("list_options", {"kind": "edit_fields", "key": key})
            assert options.is_error, options.structured_content
            args = {"key": key, "body": {"summary": "recovered", "clientMutationId": "fields:outage:rename"}}
            before = sum(method == "put" for method, _path, _body in env.calls)
            rejected = await mcp.call_tool("update_fields", args)
            assert rejected.is_error, rejected.structured_content
            error = rejected.structured_content["error"]
            assert error["code"] == ("rate_limited" if failure == "busy" else "upstream_unavailable")
            assert error["retryable"] and error["writeExecuted"] is False
            assert error["needLogin"] is False
            assert error["retryWithSameMutationId"] and "executed" not in error
            assert sum(method == "put" for method, _path, _body in env.calls) == before
            assert env.store.issues[key]["summary"] == "retained"
            monkeypatch.setattr(env.provider, "get_json", original)
            recovered = await mcp.call_tool("update_fields", args)
            assert not recovered.is_error, recovered.structured_content
            replay = await mcp.call_tool("update_fields", args)
            assert not replay.is_error, replay.structured_content
            assert sum(method == "put" for method, _path, _body in env.calls) == before + 1
            assert env.store.issues[key]["summary"] == "recovered"
    anyio.run(scenario)
