"""Output contracts exercise production builders and an isolated in-process Jira world."""

from datetime import date

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from app.auth.base import PermissionDenied, UpstreamUnavailable
from app.infra import settings as config
from app.local_mcp.admin_tools import register_admin
from app.local_mcp.schemas.workspace import DATA_MODELS
from app.local_mcp.vit_tools import register_vit
from app.local_mcp.workspace_tools import register_workspace
from app.services.workspace_config import WbsTask, WbsTaskChanges


def validate(name, payload):
    adapter = TypeAdapter(DATA_MODELS[name])
    value = adapter.validate_python(payload, strict=True)
    Draft202012Validator(adapter.json_schema()).validate(payload)
    # The contract must neither lose optional upstream metadata nor manufacture fields.
    assert value.model_dump(mode="json", by_alias=True, exclude_unset=True) == payload
    return payload


@pytest.fixture
def workspace(build_env, monkeypatch, tmp_path):
    env = build_env()
    directory = tmp_path / "workspace-config"
    directory.mkdir()
    plan = {"project_key": "DL", "wbs": [{"module": "Workbench", "tasks": [
        {"id": "existing", "name": "Existing plan", "start": "2026-01-01",
         "end": "2026-12-01", "epics": [{"ticket": "DL-101", "weight": 2}]}]}]}
    (directory / "wbs_config.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
    (directory / "people.yaml").write_text("Workbench: [u01]\n", encoding="utf-8")
    (directory / "module-aliases.yaml").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", directory)
    config.reload_people()
    env.runtime.workspace_config.config_dir = directory
    env.runtime.require_manager = lambda: None
    env.runtime.workspace_config.require_manager = lambda: env.runtime.require_manager()
    env.runtime.require_module_access = lambda _module: None
    env.runtime.scoped_people = config.load_people
    env.tools = {}
    for register in (register_workspace, register_vit, register_admin):
        register(lambda fn, **_options: env.tools.setdefault(fn.__name__, fn), env.runtime)
    yield env
    config.reload_people()


def test_workspace_reads_preserve_real_documents_relationships_and_chunks(workspace):
    env, tools = workspace, workspace.tools
    env.runtime.settings.search_confluence_spaces = ["DL"]
    env.runtime.settings.confluence_base = "http://confluence.test"
    env.store.confluence = {"u01": [{"title": "Schema guide", "space": "DL",
        "body": "<p>actual document</p>", "date": date.today()}]}
    page_id = str(env.store.confluence_pages()[0]["id"])
    key = env.client.create_child(None, "Task", "Schema ticket")["key"]
    related = env.client.create_child(None, "Task", "Schema related ticket")["key"]
    env.client.add_issue_link(key, related, env.client.link_types()[0]["name"])
    env.client.add_comment(key, "actual comment")
    attachment = env.client.upload_attachment(key, "test.txt", b"actual bytes", "text/plain")[0]

    for name, args in [
        ("search_workspace", {"query": "Schema", "sources": ["jira", "confluence"]}),
        ("search_confluence_documents", {"query": "Schema"}),
        ("read_confluence_document", {"page": page_id}),
        ("read_confluence_document", {"page": page_id, "format": "storage", "max_chars": 4}),
        ("read_attachment", {"key": key, "attachment_id": str(attachment["id"]), "max_bytes": 3}),
        ("read_attachment", {"key": key, "attachment_id": str(attachment["id"]), "offset": 3}),
        ("get_ticket_context", {"key": key}),
        ("get_field_history", {"key": key}),
        ("get_activity", {"user_id": "u01"}),
    ]:
        validate(name, tools[name](**args))
    context = tools["get_ticket_context"](key)
    assert any(row["key"] is None and row["virtual"] for row in context["ancestors"])
    assert any(row["key"] == related and row["via"] == "link" and row["rel"] for row in context["related"])
    exact = validate("search_workspace", tools["search_workspace"](key, ["jira"]))
    assert exact["jira"]["items"][0]["exact"] is True
    assert tools["read_attachment"](key, str(attachment["id"]))["hasMore"] is False


def test_search_and_context_fields_are_present_in_data_schemas():
    search = TypeAdapter(DATA_MODELS["search_workspace"]).json_schema()["$defs"]["SearchItem"]["properties"]
    assert {"status", "statusCategory", "issuetype", "project", "assignee", "assigneeId",
            "epicKey", "epicName", "updated", "exact", "path"} <= search.keys()
    context = TypeAdapter(DATA_MODELS["get_ticket_context"]).json_schema()["$defs"]["TicketReference"]["properties"]
    assert {"rel", "via"} <= context.keys()
    dashboard = TypeAdapter(DATA_MODELS["get_dashboard"]).json_schema()["$defs"]["WorkloadPerson"]["properties"]
    assert {"partial", "missingParents", "retryable", "errorKind"} <= dashboard.keys()


def test_document_builder_preserves_numeric_string_total(workspace, monkeypatch):
    workspace.runtime.settings.search_confluence_spaces = ["DL"]
    monkeypatch.setattr(workspace.client, "confluence_search", lambda *_args, **_kwargs: {
        "results": [{"content": {"id": "1", "title": "page", "space": {"key": "DL"}}}],
        "totalSize": "2"})
    result = validate("search_confluence_documents", workspace.tools["search_confluence_documents"]())
    assert result["total"] == "2" and result["hasMore"] and result["nextStartAt"] == 1


@pytest.mark.parametrize("name", ["Legacy plan", 2026, 2.5, True])
def test_legacy_wbs_scalars_survive_read_roster_write_and_dashboard(workspace, name):
    path = workspace.runtime.workspace_config.config_dir / "wbs_config.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["wbs"][0]["tasks"][0]["id"] = 123
    raw["wbs"][0]["tasks"][0]["name"] = name
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    current = validate("get_workspace_config", workspace.tools["get_workspace_config"]())
    assert current["tasks"][0]["id"] == 123
    assert current["tasks"][0]["name"] == name
    changed = validate("set_module_people", workspace.tools["set_module_people"](
        "Other", [], current["revision"]))
    assert changed["tasks"][0]["id"] == 123
    assert changed["tasks"][0]["name"] == name
    dashboard = validate("get_dashboard", workspace.tools["get_dashboard"]("wbs"))
    assert dashboard["wbs"][0]["id"] == 123
    assert dashboard["wbs"][0]["name"] == name
    assert dashboard["rollup"]["modules"][0]["wbsIds"] == [123]


@pytest.mark.parametrize("kind,module", [
    ("wbs", ""), ("vit", ""), ("vit", "Workbench"),
    ("workload", ""), ("workload", "Workbench"),
])
def test_all_dashboard_variants_with_actual_nested_data(workspace, kind, module):
    env = workspace
    root = env.client.create_child(None, "Epic", "Tracked schema root", labels=["PMO_VIT"],
                                   components=["Workbench"])["key"]
    child = env.client.create_child(root, "Task", "Tracked child", components=["Workbench"])["key"]
    env.client.add_comment(root, "root discussion")
    # The local WBS mapping supplies weighted Epics; leave lookup of its Epic progress real.
    before = env.runtime.workspace_config.read()
    env.runtime.workspace_config.update_task(before["tasks"][0]["id"],
        {"epics": [{"key": root, "weight": 2}]}, before["revision"])
    result = validate("get_dashboard", env.tools["get_dashboard"](kind, module))
    if kind == "wbs":
        assert result["wbs"][0]["epics"][0]["epicKey"] == root
    if kind == "vit":
        detail = validate("get_vit_detail", env.tools["get_vit_detail"](root))
        assert detail["tree"][0]["key"] == child and detail["comments"]
        validate("get_vit_detail", env.tools["get_vit_detail"]("DL-999999"))
    validate("get_epic_progress", env.tools["get_epic_progress"](root))


def test_vit_registration_hidden_roots_and_field_history(workspace):
    env, tools = workspace, workspace.tools
    root = env.client.create_child(None, "Task", "Tracked parent")["key"]
    child = env.client.create_child(root, "Sub-task", "Tracked subtask", labels=["PMO_VIT"])["key"]
    validate("set_vit_tracking", tools["set_vit_tracking"](root, True, "schema:tracking:0001"))
    result = validate("get_vit_overview", tools["get_vit_overview"]())
    assert {"key": child, "hiddenBy": [root]} in result["skipped"]
    transition = next(row for row in env.client.transitions(root) if row["toCategory"] == "inprogress")
    env.client.do_transition(root, transition["id"])
    history = validate("get_field_history", tools["get_field_history"](root))
    assert any(row["field"] == "status" for row in history["history"])


def test_seven_configuration_operations_share_full_revisioned_contract(workspace):
    tools = workspace.tools
    current = validate("get_workspace_config", tools["get_workspace_config"]())
    task = WbsTask(id="new", module="Workbench", name="New plan", start="2026-02-01",
                   end="2026-10-01", epics=[{"key": "DL-101", "weight": 1}])
    actions = [
        ("create_wbs_task", {"task": task}),
        ("update_wbs_task", {"task_id": "new", "changes": WbsTaskChanges(name="Changed plan")}),
        ("set_module_people", {"module": "Other", "user_ids": []}),
        ("rename_people_module", {"module": "Other", "new_name": "Renamed"}),
        ("delete_people_module", {"module": "Renamed"}),
        ("delete_wbs_task", {"task_id": "new"}),
    ]
    for name, args in actions:
        previous = current["revision"]
        current = validate(name, tools[name](**args, expected_revision=previous))
        assert current["revision"] != previous
        assert "Workbench" in current["people"] and current["tasks"]


def test_partial_sources_empty_dashboards_and_extensions_remain_valid(workspace, monkeypatch):
    env, tools = workspace, workspace.tools
    with monkeypatch.context() as patch:
        def denied(_user):
            raise PermissionDenied("source unavailable to this user")
        patch.setattr(env.client, "_fetch_confluence", denied)
        result = validate("get_activity", tools["get_activity"]("u01"))
        assert result["partial"] and result["sourceErrors"][0]["kind"] == "permission"
    with monkeypatch.context() as patch:
        def unavailable(*_args):
            raise UpstreamUnavailable("network offline")
        patch.setattr(env.client, "_wl_counts", unavailable)
        result = validate("get_dashboard", tools["get_dashboard"]("workload", "Workbench"))
        assert result["people"][0]["errorKind"] == "network"
    current = tools["get_workspace_config"]()
    for task in current["tasks"]:
        current = tools["delete_wbs_task"](task["id"], current["revision"])
    empty = validate("get_dashboard", tools["get_dashboard"]("wbs"))
    assert empty["timeline"] == {"start": None, "end": None}
    source_error = {"items": [], "error": "expired", "needLogin": True,
                    "authentication": {"code": "authentication_required", "service": "jira"}}
    search = {"query": "q", "scope": "scoped", "jira": source_error,
              "confluence": {"items": []}, "bitbucket": {"items": []}, "extra": {"keep": True}}
    validate("search_workspace", search)


@pytest.mark.parametrize("name,payload", [
    ("get_field_history", {"history": [], "limit": "100", "mayHaveMore": False}),
    ("get_vit_detail", {"key": "DL-1", "tree": [], "comments": [{"author": "name"}]}),
    ("get_dashboard", {"module": "Workbench", "people": []}),
    ("get_workspace_config", {"revision": "r", "projectKey": "DL", "tasks": [], "people": {"m": [42]}}),
])
def test_known_contract_fields_are_required_and_not_coerced(name, payload):
    with pytest.raises(ValidationError):
        TypeAdapter(DATA_MODELS[name]).validate_python(payload, strict=True)
