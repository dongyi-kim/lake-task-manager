"""Ticket output contracts against actual mock services and partial provider variants."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from app.auth.base import PermissionDenied, UpstreamUnavailable
from app.jira.evidence_service import get_evidence_page
from app.jira.hierarchy_service import _bounded_hierarchy_page, get_descendants
from app.local_mcp.evidence_tools import register_evidence
from app.local_mcp.hierarchy_tools import register_hierarchy
from app.local_mcp.read_tools import register_reads
from app.local_mcp.schemas.tickets import DATA_MODELS, EvidenceChunk, WorkloadStatistics


def assert_contract(name, value):
    adapter = TypeAdapter(DATA_MODELS[name])
    result = adapter.validate_python(value)
    Draft202012Validator(adapter.json_schema()).validate(value)
    assert adapter.dump_python(result, mode="json", by_alias=True, exclude_unset=True) == value
    return result


def test_all_ticket_reads_accept_real_service_outputs_and_preserve_fields(build_env):
    env, functions = build_env(), {}
    register = lambda fn, **kwargs: functions.setdefault(fn.__name__, fn)
    for register_group in (register_reads, register_evidence, register_hierarchy):
        register_group(register, env.runtime)
    epic = env.client.create_epic("Contract Epic")["key"]
    task = env.client.create_child(epic, "Task", "Contract Task", assignee="u01")["key"]
    child = env.client.create_child(task, "Sub-task", "Contract SubTask")["key"]
    comment = env.client.add_comment(task, "Original comment")
    env.client.upload_attachment(task, "contract.txt", b"attachment bytes", "text/plain")
    env.client.add_remote_link(task, "https://example.com/document", "Contract document")
    arguments = {
        "search_issues": {"jql": f"key = {task}"}, "get_ticket": {"key": task}, "get_tickets": {"keys": [task, child]},
        "get_comments": {"key": task}, "get_history": {"key": task},
        "get_comment_source": {"key": task, "comment_id": str(comment["id"])},
        "get_children": {"key": task}, "get_epic": {"key": epic},
        "search_users": {"query": "u01"}, "get_user": {"user_id": "u01"},
        "list_options": {"kind": "task_types"}, "get_workload": {"user_id": "u01"},
        "get_attachments": {"key": task}, "get_documents": {"key": task},
        "get_evidence_page": {"key": task}, "get_descendants": {"root_key": epic},
    }
    assert set(arguments) == set(DATA_MODELS)
    for name, args in arguments.items():
        payload = functions[name](**args)
        try:
            assert_contract(name, payload)
        except Exception as exc:
            raise AssertionError(f"Invalid {name} output: {payload!r}") from exc
    assert functions["get_children"](task)["children"][0]["key"] == child
    assert functions["get_attachments"](task)["attachments"]
    assert functions["get_documents"](task)["documents"]
    assert_contract("get_user", functions["get_user"]())
    assert_contract("get_user", None)
    for kind in ("child_types", "edit_fields", "transitions", "priorities", "components", "epics", "link_types"):
        assert_contract("list_options", functions["list_options"](kind, key=task))
    assert_contract("get_evidence_page", functions["get_evidence_page"](task, kind="history"))


def evidence_client(payload):
    return SimpleNamespace(env="mock", s=SimpleNamespace(cache_ttl_seconds=600),
        cache=SimpleNamespace(get_or_set_strict=lambda key, ttl, producer: (producer(), False)),
        provider=SimpleNamespace(get_json=lambda *args, **kwargs: deepcopy(payload)),
        _looks_anonymous=lambda _: False)


@pytest.mark.parametrize("kind,payload", [
    ("comments", {"comments": [{"id": 1, "body": "sparse identity", "custom": [None, 3]}]}),
    ("comments", {"startAt": "invalid provider metadata", "total": 4, "comments": []}),
    ("comments", {"startAt": 0, "total": 1, "comments": [{"id": "1", "body": "한글" * 10000}]}),
    ("history", {"changelog": {"startAt": 0, "total": 5, "histories": [
        {"id": "1", "items": [{"field": "status", "from": "1", "to": "3", "custom": True}]}]}}),
    ("history", {"changelog": {"startAt": 0, "total": 1, "histories": [
        {"id": "1", "items": [{"field": "description", "toString": "long" * 10000}]}]}}),
])
def test_partial_evidence_and_large_record_shapes_are_lossless(kind, payload):
    page = get_evidence_page(evidence_client(payload), "DL-1", kind)
    result = assert_contract("get_evidence_page", page)
    if page["items"] and "recordJson" in page["items"][0]:
        assert isinstance(result.items[0], EvidenceChunk)
        assert result.items[0].nextRecordOffset > 0
    else:
        assert result.partial


def test_provider_extensions_and_sparse_display_cache_are_preserved():
    raw = {"issues": [{"key": "DL-1", "fields": {"customfield_123": {"value": [True, None, 7]}},
                       "providerExtension": "unchanged"}], "startAt": 0, "maxResults": 50,
           "total": None, "returned": 1, "hasMore": False, "nextStartAt": None}
    assert_contract("search_issues", raw)
    assert_contract("get_epic", {"epic": {"key": "DL-1", "title": "Cached name"}, "tree": []})
    assert_contract("list_options", {"options": {"customfield_123": {
        "id": "customfield_123", "name": "Custom option", "type": "option", "required": False,
        "operations": ["set"], "schema": {"type": "option"},
        "allowedValues": [{"id": "7", "name": "x", "providerExtension": True}]}}})


def test_options_schema_describes_edit_fields_transition_screens_and_directions():
    definitions = TypeAdapter(DATA_MODELS["list_options"]).json_schema()["$defs"]
    expected = {
        "EditField": {"id", "name", "type", "required", "operations", "allowedValues"},
        "TransitionOption": {"id", "name", "to", "toId", "toCategory", "hasScreen", "fields"},
        "TransitionScreen": {"fields", "unsupported"},
        "ScreenField": {"id", "name", "type", "system", "required", "allowedValues"},
        "EpicOption": {"key", "name", "summary"},
        "LinkTypeOption": {"name", "inward", "outward"},
    }
    for model, fields in expected.items():
        assert fields <= definitions[model]["properties"].keys()
        assert fields <= set(definitions[model]["required"])
    pointers = TypeAdapter(DATA_MODELS["get_descendants"]).json_schema()["$defs"]
    assert {"tool", "arguments"} <= pointers["TicketDetails"]["properties"].keys()
    assert pointers["TicketArguments"]["properties"]["key"]["type"] == "string"
    workload = TypeAdapter(DATA_MODELS["get_workload"]).json_schema()["$defs"]
    assert "epicResolution" in workload["WorkloadTicket"]["properties"]
    assert "retryable" in workload["WorkloadStatistics"]["properties"]
    assert {"parentKey", "issueKeys", "kind", "retryable", "message", "bucket"} <= (
        workload["MissingParent"]["properties"].keys())


def test_hierarchy_partial_ledger_and_large_issue_details_pointer(build_env, monkeypatch):
    env = build_env()
    parent = env.client.create_child(None, "Task", "Hierarchy root")["key"]
    child = env.client.create_child(parent, "Sub-task", "Restricted child")["key"]
    original = env.client.get_issue_light
    def read(key, **kwargs):
        if key == child:
            raise PermissionDenied("restricted child")
        return original(key, **kwargs)
    monkeypatch.setattr(env.client, "get_issue_light", read)
    partial = get_descendants(env.client, parent, projects=["DL"])
    result = assert_contract("get_descendants", partial)
    assert result.partial and result.missingTotal == 1
    assert result.missing[0].key == child and result.missing[0].kind == "permission"
    partial["issues"][0]["issue"]["fields"]["labels"] = ["large" * 10000]
    bounded = _bounded_hierarchy_page(partial)
    parsed = assert_contract("get_descendants", bounded)
    assert parsed.issues[0].projectionOmissions == ["issue.fields"]
    assert parsed.issues[0].details.tool == "get_ticket"
    assert parsed.issues[0].details.arguments.key == parent


def test_workload_parent_failure_keeps_typed_coverage_on_statistics_and_tickets(build_env, monkeypatch):
    env = build_env()
    parent = env.client.create_child(None, "Task", "Unavailable parent")["key"]
    child = env.client.create_child(parent, "Sub-task", "Retained subtask", assignee="u01")["key"]
    original = env.client.issues_by_keys_result

    def read(keys, **kwargs):
        if parent in keys:
            return {"issues": [], "missing": [{"key": parent, "kind": "permission", "error": "denied"}]}
        return original(keys, **kwargs)

    monkeypatch.setattr(env.client, "issues_by_keys_result", read)
    payload = {"statistics": env.client.workload_person("u01"),
               "tickets": env.client.workload_tickets("u01")}
    result = assert_contract("get_workload", payload)
    assert result.statistics.partial
    assert any(row.parentKey == parent and child in row.issueKeys for row in result.statistics.missingParents)
    ticket = next(row for row in result.tickets.open if row.key == child)
    assert not ticket.epicResolution.complete and ticket.epicResolution.kind == "permission"
    assert not ticket.epicResolution.retryable


def test_workload_error_bucket_is_valid_without_normal_window_metadata(build_env, monkeypatch):
    env = build_env()
    def unavailable(*args, **kwargs):
        raise UpstreamUnavailable("network unavailable")
    monkeypatch.setattr(env.client, "_wl_counts", unavailable)
    data = env.client.workload_person("u01")
    parsed = WorkloadStatistics.model_validate(data)
    assert parsed.error and parsed.errorKind == "network"
    assert "doneDays" not in data and "assignedWindow" not in data


def test_attachment_without_content_keeps_other_valid_attachments(build_env, monkeypatch):
    env = build_env()
    monkeypatch.setattr(env.client, "get_issue", lambda _key: {"fields": {"attachment": [
        {"id": "1", "filename": "missing-content.txt", "size": 1, "mimeType": "text/plain"},
        {"id": "2", "filename": "available.txt", "size": 2, "mimeType": "text/plain",
         "content": "/secure/attachment/2/available.txt"},
    ]}})
    result = assert_contract("get_attachments", {"attachments": env.client.ticket_attachments("DL-1")})
    assert len(result.attachments) == 2
    assert result.attachments[0].id == "1" and result.attachments[0].url is None
    assert result.attachments[1].id == "2" and result.attachments[1].url


def test_legacy_document_cache_rows_are_returned_without_refetch_or_invented_metadata(build_env):
    env, functions = build_env(), {}
    register_reads(lambda fn, **_options: functions.setdefault(fn.__name__, fn), env.runtime)
    rows = [{"title": "Old reference", "url": "https://example.com/old"},
            {"title": "Existing link", "url": "https://example.com/link", "linkId": "1"}]
    env.cache.set(f"documents:{env.client.env}:DL-1", rows, 600)
    before = len(env.calls)
    payload = functions["get_documents"]("DL-1")
    assert_contract("get_documents", payload)
    assert payload["documents"] == rows and len(env.calls) == before


def test_legacy_comment_cache_rows_keep_missing_identity_fields_absent(build_env):
    env, functions = build_env(), {}
    register_reads(lambda fn, **_options: functions.setdefault(fn.__name__, fn), env.runtime)
    env.client.current_user()
    row = {"date": "2026-01-01", "author": "Legacy author", "html": "<p>old comment</p>"}
    env.cache.set(f"comments:{env.client.env}:DL-1", [row], 600)
    before = len(env.calls)
    payload = functions["get_comments"]("DL-1")
    assert_contract("get_comments", payload)
    assert payload["comments"] == [{**row, "mine": False}]
    assert len(env.calls) == before


@pytest.mark.parametrize("name,value", [
    ("get_user", {"id": 123, "name": "Person"}),
    ("get_comments", {"comments": [], "returnedCount": "0", "limit": 10, "sourceLimit": 20, "mayHaveMore": False}),
    ("get_children", {"children": [{"key": "DL-1"}]}),
    ("get_attachments", {"attachments": [{"id": "1", "filename": "file", "size": "10", "mime": "text/plain",
        "created": None, "author": None, "url": "/api/file", "thumb": None, "isImage": False}]}),
])
def test_known_nested_fields_cannot_silently_coerce_or_disappear(name, value):
    with pytest.raises(ValidationError):
        TypeAdapter(DATA_MODELS[name]).validate_python(value)


def test_evidence_chunk_requires_digest_and_real_numeric_offsets():
    data = {"id": "1", "sourceIndex": 0, "recordJson": "partial", "recordOffset": 0,
            "nextRecordOffset": 7, "totalCharacters": 30, "recordSha256": "abc",
            "recordFormat": "jira-evidence-v2"}
    EvidenceChunk.model_validate(data)
    with pytest.raises(ValidationError):
        EvidenceChunk.model_validate({**data, "recordOffset": "0"})
    with pytest.raises(ValidationError):
        EvidenceChunk.model_validate({**data, "recordFormat": "future"})
    del data["recordSha256"]
    with pytest.raises(ValidationError):
        EvidenceChunk.model_validate(data)
