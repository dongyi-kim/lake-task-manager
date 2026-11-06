"""Owner-thread completion must be normalized before durable receipt replay."""

from threading import Event

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import Client

from app.auth.base import MutationOutcomeUnknown, PendingUpstreamOperation, UpstreamUnavailable
from app.jira.mutation_recovery import Reconciliation, UNKNOWN
from tests.mcp.test_confluence_commands import ConfluenceFixture, commands


def defer_reply(provider, method, monkeypatch):
    done, original = Event(), getattr(provider, method)
    def pending(*args, **kwargs):
        raw = original(*args, **kwargs)
        error = UpstreamUnavailable("caller deadline elapsed; owner thread still finishing")
        error.pending_operation = PendingUpstreamOperation(done, [raw, None])
        raise error
    monkeypatch.setattr(provider, method, pending)
    return done


@pytest.mark.parametrize("operation,method", [
    ("create_page", "post_json"), ("update_page", "put_json"),
    ("create_comment", "post_json"), ("update_comment", "put_json"),
    ("upload_attachment", "post_multipart"), ("delete_comment", "delete"),
    ("delete_attachment", "delete"), ("add_labels", "post_json"), ("remove_label", "delete"),
])
def test_pending_http_success_uses_command_result_and_replays_once(commands, monkeypatch, operation, method):
    command, upstream = commands
    upstream.items["20"] = {"id": "20", "type": "comment", "container": {"id": "10"},
                            "space": {"key": "DL"}, "version": {"number": 1}}
    upstream.items["21"] = {"id": "21", "type": "attachment", "container": {"id": "10"},
                            "space": {"key": "DL"}, "version": {"number": 1}, "title": "old.png"}
    common = {"mutation_id": "pending-" + operation}
    run = {
        "create_page": lambda: command.create_page(space="DL", title="new", body="<p>new</p>", **common),
        "update_page": lambda: command.update_page(page_id="10", expected_version=1, title="new", **common),
        "create_comment": lambda: command.create_comment(page_id="10", body="<p>new</p>", **common),
        "update_comment": lambda: command.update_comment(page_id="10", comment_id="20", expected_version=1, body="<p>new</p>", **common),
        "upload_attachment": lambda: command.upload_attachment(page_id="10", filename="new.png", data=b"new", **common),
        "delete_comment": lambda: command.delete_child(page_id="10", child_id="20", kind="comment", expected_version=1, **common),
        "delete_attachment": lambda: command.delete_child(page_id="10", child_id="21", kind="attachment", expected_version=1, **common),
        "add_labels": lambda: command.update_labels(page_id="10", labels=["new"], **common),
        "remove_label": lambda: command.update_labels(page_id="10", labels=["new"], remove=True, **common),
    }[operation]
    done = defer_reply(upstream, method, monkeypatch)
    with pytest.raises(MutationOutcomeUnknown):
        run()
    with pytest.raises(MutationOutcomeUnknown):
        run()
    assert len(upstream.writes) == 1
    done.set()
    result = run()
    assert result["status"] == "success" and result["operation"] == operation
    assert "body" not in result and "results" not in result
    assert run() == result and len(upstream.writes) == 1


def test_unregistered_normalizer_falls_back_to_reconciliation_not_raw_receipt(commands, monkeypatch):
    command, upstream = commands
    done = defer_reply(upstream, "post_json", monkeypatch)
    def run():
        return command.execute(kind="custom", mutation_id="pending-custom", payload={"one": 1},
            write=lambda: command.request("POST", "/rest/api/content", {"type": "page", "title": "raw",
                "version": {"number": 1}}), reconcile=lambda receipt: Reconciliation(UNKNOWN))
    with pytest.raises(MutationOutcomeUnknown):
        run()
    done.set()
    with pytest.raises(MutationOutcomeUnknown):
        run()
    receipt = command._receipts().receipt("confluence.custom", "pending-custom")
    assert receipt["state"] == "pending" and receipt["result"] is None
    assert len(upstream.writes) == 1


def test_sdk_pending_success_and_receipt_replay_validate_output_schema(build_env, monkeypatch):
    env, upstream = build_env(), ConfluenceFixture()
    env.runtime.settings.search_confluence_spaces = ["DL"]
    env.runtime.settings.confluence_base = "https://docs.test/wiki"
    env.client._provider = upstream
    done = defer_reply(upstream, "post_json", monkeypatch)
    arguments = {"space": "DL", "title": "SDK pending", "body": "<p>once</p>",
                 "client_mutation_id": "sdk-pending-001"}
    async def scenario():
        async with Client(env.runtime.server) as sdk:
            tool = next(tool for tool in (await sdk.list_tools()).tools if tool.name == "create_confluence_page")
            first = await sdk.call_tool(tool.name, arguments)
            assert first.is_error and first.structured_content["error"]["code"] == "outcome_unknown"
            done.set()
            for _ in range(2):
                result = await sdk.call_tool(tool.name, arguments)
                Draft202012Validator(tool.output_schema).validate(result.structured_content)
                assert not result.is_error, result.structured_content
                assert result.structured_content["data"]["operation"] == "create_page"
            assert len(upstream.writes) == 1
    anyio.run(scenario)
