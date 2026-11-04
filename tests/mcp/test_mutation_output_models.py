"""Output contracts validate real command results without changing receipt payloads."""

import pytest
from pydantic import TypeAdapter, ValidationError

from app.auth.base import UpstreamError
from app.local_mcp.authentication import Authentication
from app.local_mcp.schemas.mutations import DATA_MODELS
from app.local_mcp.write_tools import CreateInput, EpicInput, FieldsInput
from app.services.ticket_models import BulkBody


def validate(name, data):
    adapter = TypeAdapter(DATA_MODELS[name])
    validated = adapter.validate_python(data)
    assert adapter.dump_python(validated, mode="json", exclude_unset=True) == data
    return data


def test_real_create_comment_attachment_link_and_replay_results(build_env):
    env = build_env()
    commands = env.runtime.commands
    body = CreateInput(type="Task", summary="output contract", clientMutationId="schema:create:0001")
    created = validate("create_task", commands.create(body))
    key = created["key"]
    writes = len(env.calls)
    assert validate("create_task", commands.create(body)) == created
    assert not any(method == "post" for method, _, _ in env.calls[writes:])
    assert validate("update_fields", commands.update_fields(key, FieldsInput(
        clientMutationId="schema:fields:0001")))["note"] == "변경 없음"
    comment = validate("add_comment", commands.comment(key, "<p>원본</p>", mutation_id="schema:comment:0001"))
    cid = str(comment["id"])
    validate("edit_comment", commands.comment(key, "<p>수정</p>", comment_id=cid,
                                               mutation_id="schema:comment-edit:0001"))
    attachment = validate("add_attachment", commands.attachment(
        key, "contract.txt", b"contract bytes", "text/plain", "schema:attachment:0001"))
    validate("delete_attachment", commands.delete_attachment(key, attachment["id"], "schema:attachment-delete:0001"))
    document = validate("add_document", commands.document(
        key, "https://example.test/contracts", "계약", "schema:document:0001"))
    validate("delete_document", commands.delete_document(key, str(document["id"]), "schema:document-delete:0001"))
    validate("delete_comment", commands.comment(key, comment_id=cid, delete=True,
                                                 mutation_id="schema:comment-delete:0001"))
    validate("delete_ticket", commands.delete_ticket(key, "schema:ticket-delete:0001"))


def test_epic_created_with_failed_followup_links_has_a_distinct_nested_result(build_env):
    env = build_env()
    result = validate("create_epic", env.runtime.commands.create_epic(EpicInput(
        summary="partial followup", taskKeys=["DL-99999999"], clientMutationId="schema:epic:0001")))
    assert result["ok"] and result["key"]
    assert result["link"]["ok"] is False
    assert result["link"]["failed"][0]["key"] == "DL-99999999"


def test_bulk_invalid_values_and_partial_write_keep_original_evidence(build_env, monkeypatch):
    env = build_env()
    commands = env.runtime.commands
    invalid = [{"summary": 3, "type": "Task", "epic": None}]
    result = validate("bulk_validate", commands.bulk_validate("task", invalid))
    assert not result["ok"] and result["items"][0]["summary"] == 3
    empty = validate("bulk_validate", commands.bulk_validate("task", []))
    assert "items" not in empty
    rejected = validate("bulk_create", commands.bulk_create(BulkBody(
        mode="task", items=invalid, clientMutationId="schema:bulk-invalid:0001")))
    assert not rejected["ok"] and rejected["created"] == rejected["failed"] == []

    original = env.provider.post_json
    def reject_one(path, json_body=None, **kwargs):
        if path == "/rest/api/2/issue" and json_body["fields"]["summary"] == "reject":
            raise UpstreamError(400, path, "fixture rejects this write")
        return original(path, json_body, **kwargs)
    monkeypatch.setattr(env.provider, "post_json", reject_one)
    partial = validate("bulk_create", commands.bulk_create(BulkBody(mode="task", items=[
        {"summary": "confirmed", "type": "Task", "epic": None},
        {"summary": "reject", "type": "Task", "epic": None},
    ], clientMutationId="schema:bulk-partial:0001")))
    assert not partial["ok"]
    assert partial["created"][0]["index"] == 0 and partial["failed"][0]["index"] == 1


def test_provider_success_without_comment_or_remote_link_body_is_valid(build_env, monkeypatch):
    env = build_env()
    commands = env.runtime.commands
    key = commands.create(CreateInput(type="Task", summary="204", clientMutationId="schema:204:0001"))["key"]
    monkeypatch.setattr(env.provider, "put_json", lambda *args, **kwargs: {})
    assert validate("edit_comment", commands.comment(key, "changed", comment_id="123",
        mutation_id="schema:204-edit:0001")) == {}
    monkeypatch.setattr(env.provider, "post_json", lambda *args, **kwargs: {})
    assert validate("add_document", commands.document(key, "https://example.test/empty", "",
        "schema:204-document:0001")) == {"ok": True}


@pytest.mark.parametrize("state", [{"ok": True}, {"needLogin": True}, {"pending": True}, {}])
def test_actual_auth_state_branches_preserve_boolean_status(state):
    for service in ("jira", "confluence"):
        result = Authentication.state(service, state)
        validate("get_auth_status", result)


def test_interactive_login_progress_and_poll_instruction_are_not_authenticated(build_env, monkeypatch):
    env = build_env()
    monkeypatch.setattr(env.client, "env", "prod")
    monkeypatch.setattr(env.client, "proactive_auth_probe", lambda **kwargs: {"needLogin": True})
    env.runtime.authentication.request_login = lambda service: {"started": True, "pending": True}
    result = validate("authenticate", env.runtime.authentication.authenticate("jira", True))
    assert result["state"] == "login_in_progress" and not result["authenticated"]
    assert result["nextAction"]["tool"] == "get_auth_status"


@pytest.mark.parametrize("name,data", [
    ("create_task", {"ok": True}),
    ("add_comment", {"body": "no confirmed ID"}),
    ("add_attachment", {"id": 123, "filename": "a", "path": "/a"}),
    ("bulk_create", {"ok": False, "created": [], "failed": [{"index": "1", "summary": "s", "error": "e"}], "warnings": []}),
])
def test_schema_rejects_missing_identity_or_wrong_field_type(name, data):
    with pytest.raises(ValidationError):
        TypeAdapter(DATA_MODELS[name]).validate_python(data)
