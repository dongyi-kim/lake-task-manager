"""SDK bulk edits use real Jira fixtures, shared validation and durable per-row receipts."""

import json

import anyio
from jsonschema import Draft202012Validator
from mcp import Client
import pytest

from app.auth.base import SessionExpired, UpstreamError, UpstreamUnavailable


async def create(mcp, index):
    result = await mcp.call_tool("create_task", {"body": {
        "type": "Task", "summary": f"before {index}", "clientMutationId": f"bulkedit:create:{index}"}})
    assert not result.is_error, result.structured_content
    return result.structured_content["data"]["key"]


def puts(env):
    return [row for row in env.calls if row[0] == "put"]


def test_partial_batch_replays_successes_and_only_retries_failed_edits(build_env, monkeypatch):
    env = build_env()
    original = env.provider.put_json
    attempts = []
    def reject_once(path, json_body=None, **kwargs):
        attempts.append(path)
        if json_body["fields"].get("summary") == "retry" and attempts.count(path) == 1:
            raise UpstreamError(400, path, "fixture validation rejected")
        return original(path, json_body, **kwargs)
    monkeypatch.setattr(env.provider, "put_json", reject_once)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            keys = [await create(mcp, i) for i in range(3)]
            for key in keys:
                env.client.get_issue(key)  # Prime the same cache that UI readers consume.
            generation = env.client._jql_generation()
            args = {"items": [{"key": keys[0], "fields": {"summary": "first", "labels": ["keep"]}},
                {"key": keys[1], "fields": {"summary": "retry"}},
                {"key": keys[2], "fields": {"descriptionHtml": "<p>new body</p>", "assignee": "u02"}}],
                "client_mutation_id": "bulkedit:partial:001"}
            first = await mcp.call_tool("bulk_update", args)
            payload = first.structured_content
            assert first.is_error and payload["partial"] is True
            assert [row["index"] for row in payload["data"]["updated"]] == [0, 2]
            failed = payload["data"]["failed"][0]
            assert (failed["index"], failed["key"], failed["status"]) == (1, keys[1], 400)
            assert failed["action"] == "check_inputs"
            assert env.client._jql_generation() == generation + 1
            assert env.client.get_issue(keys[0])["fields"]["summary"] == "first"
            recovered = await mcp.call_tool("bulk_update", args)
            assert not recovered.is_error, recovered.structured_content
            assert [row["index"] for row in recovered.structured_content["data"]["updated"]] == [0, 1, 2]
            count = len(attempts)
            assert not (await mcp.call_tool("bulk_update", args)).is_error
            assert len(attempts) == count == 4
            assert env.client._jql_generation() == generation + 2
            changed = {**args, "items": list(reversed(args["items"]))}
            assert (await mcp.call_tool("bulk_update", changed)).is_error
            assert len(attempts) == count
            assert env.store.issues[keys[0]]["labels"] == ["keep"]
            assert env.store.issues[keys[2]]["assignee"] == "u02"
            assert "new body" in env.client.get_issue(keys[2])["fields"]["description"]
    anyio.run(scenario)


def test_completed_and_permission_denied_rows_are_actionable_and_never_written(build_env, monkeypatch):
    env = build_env()
    original = env.client.get_issue_light
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            keys = [await create(mcp, i) for i in range(3)]
            def completed(key, **kwargs):
                row = original(key, **kwargs)
                if key == keys[0]:
                    row = {**row, "fields": {**row["fields"], "status": {
                        "name": "Resolved", "statusCategory": {"key": "done"}}}}
                return row
            monkeypatch.setattr(env.client, "get_issue_light", completed)
            result = await mcp.call_tool("bulk_update", {"items": [
                {"key": keys[0], "fields": {"summary": "blocked by Done"}},
                {"key": keys[1], "fields": {"reporter": "u02"}},
                {"key": keys[2], "fields": {"summary": "allowed"}}],
                "client_mutation_id": "bulkedit:permissions:001"})
            assert result.is_error and result.structured_content["partial"]
            assert result.structured_content["error"]["retryable"] is False
            failures = result.structured_content["data"]["failed"]
            assert [(row["index"], row["status"], row["action"]) for row in failures] == [
                (0, 409, "reopen_or_check_conflict"), (1, 403, "check_permissions")]
            assert [path for _, path, _ in puts(env)] == [f"/rest/api/2/issue/{keys[2]}"]
            assert not any(path.endswith("/transitions") and method == "post" for method, path, _ in env.calls)
    anyio.run(scenario)


@pytest.mark.parametrize("fault", ["preflight", "lost_response"])
def test_failure_before_write_can_resume_but_ambiguous_commit_never_repeats(build_env, monkeypatch, fault):
    env = build_env()
    original_put, original_meta = env.provider.put_json, env.client.editmeta
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            key = await create(mcp, 0)
            args = {"items": [{"key": key, "fields": {"summary": "after"}}],
                    "client_mutation_id": "bulkedit:failure:001"}
            def unavailable(*args, **kwargs):
                raise UpstreamUnavailable("fixture metadata unavailable")
            def response_lost(*args, **kwargs):
                original_put(*args, **kwargs)
                raise UpstreamUnavailable("fixture response lost after commit")
            monkeypatch.setattr(env.client, "editmeta", unavailable if fault == "preflight" else original_meta)
            monkeypatch.setattr(env.provider, "put_json", response_lost if fault == "lost_response" else original_put)
            first = await mcp.call_tool("bulk_update", args)
            assert first.is_error
            failure = first.structured_content["data"]["failed"][0]
            assert failure["code"] == ("upstream_unavailable" if fault == "preflight" else "outcome_unknown")
            assert len(puts(env)) == (0 if fault == "preflight" else 1)
            monkeypatch.setattr(env.client, "editmeta", original_meta)
            monkeypatch.setattr(env.provider, "put_json", original_put)
            for _ in range(2):
                retry = await mcp.call_tool("bulk_update", args)
                assert retry.is_error == (fault == "lost_response")
                if retry.is_error:
                    assert retry.structured_content["data"]["failed"][0]["repeatWrite"] is False
            assert len(puts(env)) == 1 and env.store.issues[key]["summary"] == "after"
    anyio.run(scenario)


def test_bulk_schema_scope_duplicates_and_unknown_fields_reject_before_any_write(build_env):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            key = await create(mcp, 0)
            catalog = {tool.name: tool for tool in (await mcp.list_tools()).tools}
            tool = catalog["bulk_update"]
            assert tool.input_schema["properties"]["items"]["maxItems"] == 10
            assert tool.input_schema["$defs"]["FieldChanges"]["additionalProperties"] is False
            row = {"key": key, "fields": {"summary": "after"}}
            for rows in ([], [row] * 11, [row, row], [row, {**row, "key": "OUTSIDE-1"}],
                         [{"key": key, "fields": {"epic": "OUTSIDE-2"}}],
                         [{**row, "fields": {"parent": "DL-1"}}], [{**row, "fields": {}}],
                         [{**row, "fields": {"summary": None}}]):
                result = await mcp.call_tool("bulk_update", {
                    "items": rows, "client_mutation_id": "bulkedit:invalid:001"})
                assert result.is_error
            assert puts(env) == [] and env.store.issues[key]["summary"] == "before 0"
            good = await mcp.call_tool("bulk_update", {"items": [row], "client_mutation_id": "bulkedit:valid:001"})
            Draft202012Validator(tool.output_schema).validate(good.structured_content)
            assert not good.is_error
    anyio.run(scenario)


def test_authentication_recovery_reuses_confirmed_rows_and_batch_identity(build_env, monkeypatch):
    env = build_env()
    original = env.provider.put_json
    attempts = []
    def expired_once(path, json_body=None, **kwargs):
        attempts.append(path)
        if json_body["fields"]["summary"] == "recover" and attempts.count(path) == 1:
            raise SessionExpired("fixture expired before write")
        return original(path, json_body, **kwargs)
    monkeypatch.setattr(env.provider, "put_json", expired_once)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            keys = [await create(mcp, i) for i in range(2)]
            result = await mcp.call_tool("bulk_update", {"items": [
                {"key": keys[0], "fields": {"summary": "first"}},
                {"key": keys[1], "fields": {"summary": "recover"}}],
                "client_mutation_id": "bulkedit:auth:001"})
            assert not result.is_error, result.structured_content
            assert len(attempts) == 3 and len(puts(env)) == 2
    anyio.run(scenario)


def test_worst_case_batch_errors_have_bounded_structured_output(build_env, monkeypatch):
    env = build_env()
    env.runtime.settings.search_jira_projects = ["D" * 60]
    original_error = env.runtime._error
    def guided_error(exc):
        return {**original_error(exc), "reason": "field_not_editable", "nextAction": {
            "tool": "list_options", "arguments": {"kind": "edit_fields", "key": "D" * 60 + "-0"}}}
    monkeypatch.setattr(env.runtime, "_error", guided_error)
    def rejected(key, body):
        raise UpstreamError(400, "/fixture", "한글 오류 상세" * 3000)
    monkeypatch.setattr(env.runtime.commands, "update_fields", rejected)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("bulk_update", {"items": [
                {"key": "D" * 60 + "-" + str(i), "fields": {"summary": "after"}} for i in range(10)],
                "client_mutation_id": "bulkedit:bounded:001"})
            payload = result.structured_content
            assert result.is_error and len(payload["data"]["failed"]) == 10
            assert all(row["messageTruncated"] for row in payload["data"]["failed"])
            assert all(row["reason"] == "field_not_editable" and row["nextAction"]["tool"] == "list_options"
                       for row in payload["data"]["failed"])
            assert len(json.dumps(payload, ensure_ascii=False).encode()) < 6144
    anyio.run(scenario)


def test_partial_batch_preserves_receipts_without_an_account_gate(build_env, monkeypatch):
    env = build_env()
    original_put, original_get = env.provider.put_json, env.provider.get_json
    def deny(path, json_body=None, **kwargs):
        if json_body["fields"]["summary"] == "later":
            raise UpstreamError(400, path, "fixture rejection")
        return original_put(path, json_body, **kwargs)
    monkeypatch.setattr(env.provider, "put_json", deny)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            keys = [await create(mcp, i) for i in range(2)]
            args = {"items": [{"key": keys[0], "fields": {"summary": "first"}},
                              {"key": keys[1], "fields": {"summary": "later"}}],
                    "client_mutation_id": "bulkedit:actor:001"}
            assert (await mcp.call_tool("bulk_update", args)).structured_content["partial"] is True
            def changed_actor(path, *args, **kwargs):
                return {"name": "u02", "key": "u02"} if path == "/rest/api/2/myself" else original_get(path, *args, **kwargs)
            monkeypatch.setattr(env.provider, "get_json", changed_actor)
            monkeypatch.setattr(env.provider, "put_json", original_put)
            result = await mcp.call_tool("bulk_update", args)
            assert not result.is_error, result.structured_content
            data = result.structured_content["data"]
            assert [row["key"] for row in data["updated"]] == keys
            assert data["failed"] == []
            assert len(puts(env)) == 2 and env.store.issues[keys[1]]["summary"] == "later"
            # Confirmed first row is replayed locally; only the rejected second row is written.
            assert not any(path == "/rest/api/2/myself" for method, path, _ in env.calls)
    anyio.run(scenario)


@pytest.mark.parametrize("switch_at", ["between_rows", "auth_recovery"])
def test_local_batch_preserves_success_across_identity_or_auth_failure(build_env, monkeypatch, switch_at):
    env = build_env()
    original_put, original_get = env.provider.put_json, env.provider.get_json
    original_auth = env.runtime.authentication.check
    switched = False
    def user(path, *args, **kwargs):
        if switched and path == "/rest/api/2/myself":
            return {"name": "u02", "key": "u02"}
        return original_get(path, *args, **kwargs)
    def write(path, json_body=None, **kwargs):
        nonlocal switched
        if switch_at == "auth_recovery" and json_body["fields"]["summary"] == "second":
            raise SessionExpired("fixture 401 before commit")
        result = original_put(path, json_body, **kwargs)
        if switch_at == "between_rows":
            switched = True
        return result
    def authenticate(service="jira", *, force=False):
        nonlocal switched
        if force:
            switched = True
        return original_auth(service, force=force)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            keys = [await create(mcp, i) for i in range(2)]
            monkeypatch.setattr(env.provider, "get_json", user)
            monkeypatch.setattr(env.provider, "put_json", write)
            monkeypatch.setattr(env.runtime.authentication, "check", authenticate)
            args = {"items": [{"key": keys[0], "fields": {"summary": "first"}},
                              {"key": keys[1], "fields": {"summary": "second"}}],
                    "client_mutation_id": "bulkedit:mid-switch:001"}
            result = await mcp.call_tool("bulk_update", args)
            data = result.structured_content["data"]
            if switch_at == "between_rows":
                assert not result.is_error and data["failed"] == []
                assert [row["key"] for row in data["updated"]] == keys
                assert len(puts(env)) == 2
            else:
                # Repeated real Jira 401 still blocks this row; a local identity hint does not.
                assert result.is_error and result.structured_content["partial"]
                assert [row["key"] for row in data["updated"]] == [keys[0]]
                assert [(row["key"], row["code"]) for row in data["failed"]] == [(keys[1], "authentication_required")]
                assert len(puts(env)) == 1 and env.store.issues[keys[1]]["summary"] == "before 1"
            monkeypatch.setattr(env.provider, "get_json", original_get)
            monkeypatch.setattr(env.provider, "put_json", original_put)
            assert not (await mcp.call_tool("bulk_update", args)).is_error
            assert len(puts(env)) == 2  # Original first row is replayed, not written twice.
    anyio.run(scenario)


@pytest.mark.parametrize("failure_at", ["authentication_check", "replay_lookup"])
def test_auth_recovery_failure_keeps_confirmed_and_failed_row_results(build_env, monkeypatch, failure_at):
    env = build_env()
    original_put, original_auth = env.provider.put_json, env.runtime.authentication.check
    original_actor = env.client._mutation_actor_for
    recovering = False
    def write(path, json_body=None, **kwargs):
        if json_body["fields"]["summary"] == "second":
            raise SessionExpired("fixture 401 before commit")
        return original_put(path, json_body, **kwargs)
    def authenticate(service="jira", *, force=False):
        nonlocal recovering
        if force:
            recovering = True
            if failure_at == "authentication_check":
                raise UpstreamUnavailable("fixture recovery lookup offline")
        return original_auth(service, force=force)
    def actor(kind, mutation_id):
        if recovering and kind == "command-bulk-update-manifest":
            raise UpstreamUnavailable("fixture replay identity lookup offline")
        return original_actor(kind, mutation_id)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            keys = [await create(mcp, i) for i in range(2)]
            monkeypatch.setattr(env.provider, "put_json", write)
            monkeypatch.setattr(env.runtime.authentication, "check", authenticate)
            monkeypatch.setattr(env.client, "_mutation_actor_for", actor)
            result = await mcp.call_tool("bulk_update", {"items": [
                {"key": keys[0], "fields": {"summary": "first"}},
                {"key": keys[1], "fields": {"summary": "second"}}],
                "client_mutation_id": "bulkedit:recovery-fails:001"})
            payload = result.structured_content
            assert result.is_error and payload["partial"] is True
            assert [row["key"] for row in payload["data"]["updated"]] == [keys[0]]
            assert [(row["key"], row["code"]) for row in payload["data"]["failed"]] == [
                (keys[1], "authentication_required")]
            assert payload["authentication"]["jira"]["code"] == "upstream_unavailable"
            assert len(puts(env)) == 1 and env.store.issues[keys[1]]["summary"] == "before 1"
    anyio.run(scenario)
