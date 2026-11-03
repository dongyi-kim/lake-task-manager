import anyio
import httpx2
import pytest
from datetime import datetime, timedelta, timezone
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.auth.base import UpstreamError, UpstreamUnavailable


def test_bulk_uses_ui_validation_keeps_order_relationships_and_retries_only_failed_rows(build_env, monkeypatch):
    env = build_env()
    original = env.provider.post_json
    attempts = []
    def once_rejected(path, json_body=None, **kwargs):
        if path == "/rest/api/2/issue":
            summary = json_body["fields"]["summary"]
            attempts.append(summary)
            if summary == "permanently rejected" or (summary == "temporarily rejected" and attempts.count(summary) == 1):
                raise UpstreamError(400, path, "fixture validation rejection before write")
        return original(path, json_body, **kwargs)
    monkeypatch.setattr(env.provider, "post_json", once_rejected)
    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                                           base_url="http://127.0.0.1:4457",
                                           headers={"Authorization": "Bearer " + env.runtime.credentials.token()}) as http:
                async with Client(streamable_http_client(env.runtime.url, http_client=http)) as mcp:
                    async def call(name, arguments):
                        result = await mcp.call_tool(name, arguments)
                        assert not result.is_error, result.structured_content
                        return result.structured_content["data"]
                    invalid = [{"summary": "missing Epic intent", "type": "Task"}]
                    mcp_validation = await call("bulk_validate", {"mode": "task", "items": invalid})
                    ui_validation = (await http.post("/api/bulk/validate", json={"mode": "task", "items": invalid})).json()
                    assert mcp_validation == ui_validation and not mcp_validation["ok"]
                    invalid_result = await mcp.call_tool("bulk_create", {"mode": "task", "items": invalid,
                                                                         "client_mutation_id": "bulk:invalid:0001"})
                    assert invalid_result.is_error
                    invalid_payload = invalid_result.structured_content
                    assert invalid_payload["ok"] is False and invalid_payload["partial"] is False
                    assert invalid_payload["error"]["code"] == "validation_failed"
                    assert invalid_payload["error"]["retryable"] is False
                    assert invalid_payload["data"]["created"] == []
                    ui_invalid = await http.post("/api/bulk/create", json={"mode": "task", "items": invalid})
                    assert ui_invalid.status_code == 400 and ui_invalid.json() == invalid_payload["data"]
                    assert attempts == []
                    all_failed = await mcp.call_tool("bulk_create", {"mode": "task", "items": [
                        {"summary": "permanently rejected", "type": "Task", "epic": None}],
                        "client_mutation_id": "bulk:rejected:0001"})
                    assert all_failed.is_error and all_failed.structured_content["ok"] is False
                    assert all_failed.structured_content["partial"] is False
                    assert all_failed.structured_content["error"]["code"] == "write_failed"
                    assert all_failed.structured_content["data"]["created"] == []
                    assert all_failed.structured_content["data"]["failed"][0]["index"] == 0
                    epic = await call("create_epic", {"body": {"summary": "bulk Epic", "clientMutationId": "bulk:epic:0001"}})
                    items = [{"summary": "first", "type": "Task", "epic": epic["key"], "description": "**bulk body**"},
                             {"summary": "temporarily rejected", "type": "Task", "epic": None},
                             {"summary": "third", "type": "Task", "epic": epic["key"]}]
                    args = {"mode": "task", "items": items, "client_mutation_id": "bulk:tasks:0001"}
                    partial_result = await mcp.call_tool("bulk_create", args)
                    assert partial_result.is_error and partial_result.structured_content["ok"] is False
                    assert partial_result.structured_content["partial"] is True
                    assert partial_result.structured_content["error"]["code"] == "partial_failure"
                    assert partial_result.structured_content["error"]["retryWithSameMutationId"] is True
                    first = partial_result.structured_content["data"]
                    assert not first["ok"] and [row["index"] for row in first["created"]] == [0, 2]
                    assert [row["index"] for row in first["failed"]] == [1]
                    confirmed = {row["index"]: row["key"] for row in first["created"]}
                    second = await call("bulk_create", args)
                    assert second["ok"] and [row["index"] for row in second["created"]] == [0, 1, 2]
                    assert all(row["key"] == confirmed[row["index"]] for row in second["created"] if row["index"] in confirmed)
                    before = len(attempts)
                    assert (await call("bulk_create", args))["created"] == second["created"]
                    assert len(attempts) == before
                    assert attempts.count("first") == attempts.count("third") == 1
                    assert attempts.count("temporarily rejected") == 2
                    parent = second["created"][0]["key"]
                    raw = env.client.get_issue(parent)["fields"]
                    assert raw[env.runtime.settings.epic_link_field_id] == epic["key"]
                    assert "bulk body" in (await http.get(f"/api/ticket/{parent}")).json()["descriptionHtml"]
                    subtype = (await call("list_options", {"kind": "child_types", "key": parent}))["options"][0]
                    children = [{"summary": "child zero", "type": subtype, "parent": parent},
                                {"summary": "child one", "type": subtype, "parent": second["created"][2]["key"]}]
                    child_args = {"mode": "subtask", "items": children, "client_mutation_id": "bulk:children:0001"}
                    created = await call("bulk_create", child_args)
                    assert created["ok"]
                    for row in created["created"]:
                        child_raw = env.client.get_issue(row["key"])["fields"]
                        assert child_raw["parent"]["key"] == children[row["index"]]["parent"]
                    before = len(attempts)
                    changed = [dict(item) for item in items]
                    changed[0]["summary"] = "different batch payload"
                    rejected = await mcp.call_tool("bulk_create", {**args, "items": changed})
                    assert rejected.is_error and len(attempts) == before
    anyio.run(scenario)


@pytest.mark.parametrize("reconciled", [False, True], ids=["unproven-time", "proven-created-after-attempt"])
def test_bulk_response_lost_keeps_per_item_receipt_and_never_duplicates(build_env, monkeypatch, reconciled):
    env = build_env()
    original = env.provider.post_json
    writes = []
    def response_lost(path, json_body=None, **kwargs):
        result = original(path, json_body, **kwargs)
        if path == "/rest/api/2/issue":
            writes.append(json_body["fields"]["summary"])
            if writes[-1] == "lost response":
                # jira820 defaults to 09:00 UTC on its local fixture date. That may
                # fall before or after the real receipt depending on wall clock/TZ.
                # Model explicit evidence on both sides of attemptedAt instead.
                created = datetime.now(timezone.utc) + timedelta(seconds=2 if reconciled else -60)
                env.store.issues[result["key"]].update(
                    created=created.date(), tcreated=created.strftime("%H:%M:%S"))
                raise UpstreamUnavailable("response lost after successful fixture POST")
        return result
    monkeypatch.setattr(env.provider, "post_json", response_lost)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            args = {"mode": "task", "items": [{"summary": "lost response", "type": "Task", "epic": None},
                                                {"summary": "confirmed", "type": "Task", "epic": None}],
                    "client_mutation_id": "bulk:uncertain:0001"}
            initial = await mcp.call_tool("bulk_create", args)
            assert initial.is_error and initial.structured_content["ok"] is False
            assert initial.structured_content["partial"] is True
            assert initial.structured_content["error"]["outcomeUnknown"] is True
            assert initial.structured_content["error"]["repeatWrite"] is False
            data = initial.structured_content["data"]
            assert data["failed"][0]["code"] == "outcome_unknown"
            assert data["failed"][0]["repeatWrite"] is False
            monkeypatch.setattr(env.provider, "post_json", original)
            repeated = await mcp.call_tool("bulk_create", args)
            repeated_data = repeated.structured_content["data"]
            if reconciled:
                assert not repeated.is_error and repeated_data["ok"]
                assert [row["index"] for row in repeated_data["created"]] == [0, 1]
                assert repeated_data["failed"] == []
                assert repeated_data["created"][1] == data["created"][0]
            else:
                assert repeated.is_error and repeated.structured_content["partial"] is True
                # A matching candidate older than attemptedAt is not proof of this
                # write. Keep uncertainty and the confirmed sibling without a POST.
                assert repeated_data["created"] == data["created"]
                assert repeated_data["failed"][0]["code"] == "outcome_unknown"
                assert repeated_data["failed"][0]["clientMutationId"] == data["failed"][0]["clientMutationId"]
            assert writes == ["lost response", "confirmed"]
            assert len([row for row in env.calls if row[:2] == ("post", "/rest/api/2/issue")]) == 2
            assert sum(row["summary"] == "lost response" for row in env.store.issues.values()) == 1
    anyio.run(scenario)


def test_sdk_labels_components_edit_clear_and_vit_registration_preserves_other_labels(build_env):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            async def call(name, arguments):
                result = await mcp.call_tool(name, arguments)
                assert not result.is_error, result.structured_content
                return result.structured_content["data"]
            task = await call("create_task", {"body": {"type": "Task", "summary": "VIT root",
                "clientMutationId": "vit:task:0001"}})
            key = task["key"]
            component = (await call("list_options", {"kind": "components"}))["options"][0]["name"]
            await call("update_fields", {"key": key, "body": {"labels": ["keep-this"], "components": [component],
                "clientMutationId": "vit:fields:0001"}})
            fields = env.client.get_issue(key)["fields"]
            assert fields["labels"] == ["keep-this"] and [row["name"] for row in fields["components"]] == [component]
            await call("set_vit_tracking", {"key": key, "tracked": True, "client_mutation_id": "vit:track:0001"})
            assert set(env.client.get_issue(key)["fields"]["labels"]) == {"keep-this", "PMO_VIT"}
            overview = await call("get_vit_overview", {})
            assert key in {row["key"] for row in overview["visibleRoots"]}
            subtype = (await call("list_options", {"kind": "child_types", "key": key}))["options"][0]
            child = await call("create_subtask", {"parent_key": key, "body": {"type": subtype, "summary": "nested VIT",
                "labels": ["PMO_VIT"], "clientMutationId": "vit:child:0001"}})
            nested = await call("get_vit_overview", {})
            assert {"key": child["key"], "hiddenBy": [key]} in nested["skipped"]
            await call("set_vit_tracking", {"key": key, "tracked": False, "client_mutation_id": "vit:untrack:0001"})
            assert env.client.get_issue(key)["fields"]["labels"] == ["keep-this"]
            await call("update_fields", {"key": key, "body": {"labels": [], "components": [],
                "clientMutationId": "vit:clear:0001"}})
            cleared = env.client.get_issue(key)["fields"]
            assert cleared["labels"] == [] and cleared["components"] == []
    anyio.run(scenario)
