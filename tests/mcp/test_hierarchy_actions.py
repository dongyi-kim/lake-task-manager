"""SDK + native form engine + Jira REST fixtures; no unsupported REST PUT conversion shortcut."""

from urllib.parse import parse_qs, urlsplit

import anyio
from jira820.serialize import iid
from jsonschema import Draft202012Validator
from mcp import Client
import pytest

from app.auth.base import PermissionDenied, SessionExpired, UpstreamError, UpstreamUnavailable


class WizardFixture:
    """Minimal rendered Jira forms; final POST mutates jira820's canonical store, then REST verifies it."""
    def __init__(self, env, monkeypatch):
        self.env, self.values, self.calls = env, {}, []
        self.lost, self.no_commit, self.required, self.denied = False, False, "", False
        env.runtime.settings.jira_base = "https://jira.fixture.test/jira"
        original = env.provider.get_json
        def get(path, *args, **kwargs):
            if path.startswith("/rest/api/2/issuetype/"):
                name = env.store._type_by_id(path.rsplit("/", 1)[-1])
                return env.store.serializer.issuetype_obj(name) if name else {}
            return original(path, *args, **kwargs)
        monkeypatch.setattr(env.provider, "get_json", get)
        monkeypatch.setattr(env.provider, "web_form", self.web_form)
        monkeypatch.setattr(env.client, "_reprime", lambda *args, **kwargs: None)

    def form(self, action, controls="", conversion=True):
        hidden = f'<input type="hidden" name="id" value="{iid(self.key)}">'
        hidden += '<input type="hidden" name="atl_token" value="fixture-secret">'
        if conversion:
            hidden += '<input type="hidden" name="guid" value="fixture-guid">'
        return {"status": 200, "location": "", "html":
                f'<form method="post" action="{action}">{hidden}{controls}</form>'}

    def web_form(self, path, fields=None, *, may_commit=False):
        self.calls.append((path, fields, may_commit))
        if self.denied:
            raise PermissionDenied(path, "fixture Edit/Move Issues permission denied")
        alias = urlsplit(path).path.rsplit("/", 1)[-1]
        if fields is None and "id" in parse_qs(urlsplit(path).query):
            identifier = parse_qs(urlsplit(path).query)["id"][0]
            self.key = next(key for key in self.env.store.issues if iid(key) == identifier)
        if alias.startswith("MoveSubTask"):
            if "ChooseOperation!" in alias:
                return self.form("MoveSubTaskChooseOperation.jspa", '<input type="radio" name="operation" '
                                 'value="move.subtask.parent.operation.name">', False)
            if alias == "MoveSubTaskChooseOperation.jspa":
                return {"status": 303, "html": "", "location": f"MoveSubTaskParent!default.jspa?id={iid(self.key)}"}
            if "!default" in alias:
                return self.form("MoveSubTaskParent.jspa", '<input name="parentIssue" required>', False)
        elif alias in ("ConvertIssue.jspa", "ConvertSubTask.jspa"):
            self.values = {}
            self.prefix = alias[:-5]
            name = "Sub-task" if self.prefix == "ConvertIssue" else "Task"
            type_id = self.env.store.serializer.type_map[name]
            controls = f'<select name="issuetype" required><option value="{type_id}">{name}</option></select>'
            if self.prefix == "ConvertIssue":
                controls += '<input name="parentIssueKey" required>'
            return self.form(self.prefix + "SetIssueType.jspa", controls)
        elif "SetIssueType" in alias:
            self.values.update(dict(fields))
            controls = f'<input name="{self.required}" required>' if self.required else ""
            return self.form(self.prefix + "UpdateFields.jspa", controls)
        elif "UpdateFields" in alias:
            self.values.update(dict(fields))
            return self.form(self.prefix + "Convert.jspa")
        assert may_commit, "Unexpected non-final form route"
        if not self.no_commit:
            row = self.env.store.issues[self.key]
            old_parent = row.get("parentKey")
            new_parent = dict(fields).get("parentIssue") if alias.startswith("MoveSubTask") else self.values.get("parentIssueKey")
            if old_parent:
                self.env.store.issues[old_parent]["subtasks"].remove(self.key)
            if not alias.startswith("MoveSubTask"):
                row["type"] = self.env.store._type_by_id(self.values["issuetype"])
            row["parentKey"] = new_parent
            if new_parent:
                self.env.store.issues[new_parent]["subtasks"].append(self.key)
            self.env.store.reindex()
        if self.lost:
            raise UpstreamUnavailable("fixture final response lost after commit")
        return {"status": 302, "html": "", "location": f"/jira/browse/{self.key}"}

    @property
    def commits(self):
        return sum(call[2] for call in self.calls)


def seed(env, kind="Task", parent=None):
    fields = {"issuetype": {"name": kind}, "summary": "hierarchy fixture"}
    if parent:
        fields["parent"] = {"key": parent}
    return env.store.create_issue(fields)["key"]


def test_sdk_conversion_reparent_reverse_replay_and_cache_invalidation(build_env, monkeypatch):
    env = build_env()
    wizard = WizardFixture(env, monkeypatch)
    key, parent, other = [seed(env) for _ in range(3)]
    epics = [seed(env, "Epic") for _ in range(2)]
    for target, epic in zip((parent, other), epics):
        env.store.issues[target]["epicKey"] = epic
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
            assert "preview_hash" in tools["change_ticket_hierarchy"].input_schema["required"]
            async def call(name, arguments):
                result = await mcp.call_tool(name, arguments)
                Draft202012Validator(tools[name].output_schema).validate(result.structured_content)
                assert not result.is_error, result.structured_content
                return result.structured_content["data"]
            body = {"action": "convert_to_subtask", "parent_key": parent}
            options = await call("get_hierarchy_options", {"key": key, "body": body})
            assert not options["ready"] and options["inputs"][0]["id"] == "issuetype"
            assert "fixture-secret" not in str(options) and "fixture-guid" not in str(options)
            assert (await mcp.call_tool("change_ticket_hierarchy", {"key": key, "body": body,
                "client_mutation_id": "hierarchy:missing:001"})).is_error
            body["issue_type_id"] = options["inputs"][0]["options"][0]["id"]
            prepared = await call("get_hierarchy_options", {"key": key, "body": body})
            assert prepared["ready"]
            assert wizard.commits == 0
            env.client.get_issue(key)
            generation = env.client._jql_generation()
            args = {"key": key, "body": body, "client_mutation_id": "hierarchy:convert:001",
                    "preview_hash": prepared["previewHash"]}
            for _ in range(2):
                assert (await call("change_ticket_hierarchy", args))["parentKey"] == parent
            changed = {**args, "body": {**body, "parent_key": other}}
            assert (await mcp.call_tool("change_ticket_hierarchy", changed)).is_error
            assert wizard.commits == 1 and env.client._jql_generation() == generation + 1
            assert env.client.get_issue(key)["fields"]["issuetype"]["subtask"] is True
            assert key in [row["key"] for row in env.client.ticket_children(parent)]
            assert not env.client.ticket_children(other)
            for prefix in ("workload", "wbs_build", "vit_build"):
                env.cache.set(f"{prefix}:{env.client.env}:fixture", {"stale": True}, 600)
            for epic in epics:
                env.cache.set(f"epic_children:{env.client.env}:{epic}", {"stale": True}, 600)
            body = {"action": "change_parent", "parent_key": other}
            prepared = await call("get_hierarchy_options", {"key": key, "body": body})
            moved = await call("change_ticket_hierarchy", {"key": key, "body": body,
                "preview_hash": prepared["previewHash"], "client_mutation_id": "hierarchy:parent:001"})
            assert moved["previousParentKey"] == parent and moved["parentKey"] == other
            assert all(env.cache.get(f"{prefix}:{env.client.env}:fixture") is None for prefix in ("workload", "wbs_build", "vit_build"))
            assert all(env.cache.get(f"epic_children:{env.client.env}:{epic}") is None for epic in epics)
            assert not env.client.ticket_children(parent)
            assert key in [row["key"] for row in env.client.ticket_children(other)]
            body = {"action": "convert_to_task"}
            options = await call("get_hierarchy_options", {"key": key, "body": body})
            body["issue_type_id"] = options["inputs"][0]["options"][0]["id"]
            prepared = await call("get_hierarchy_options", {"key": key, "body": body})
            converted = await call("change_ticket_hierarchy", {"key": key, "body": body,
                "preview_hash": prepared["previewHash"], "client_mutation_id": "hierarchy:reverse:001"})
            assert converted["parentKey"] is None and converted["previousParentKey"] == other
            assert not env.client.get_issue(key)["fields"]["issuetype"]["subtask"]
            assert not env.client.ticket_children(other) and wizard.commits == 3
    anyio.run(scenario)


@pytest.mark.parametrize("fault", ["source_changed", "actor_changed", "epic_type", "parent_security"])
def test_changed_preview_identity_and_wrong_type_stop_before_commit(build_env, monkeypatch, fault):
    env = build_env()
    wizard = WizardFixture(env, monkeypatch)
    key, parent = seed(env), seed(env)
    levels = {key: {"id": "1", "name": "Source visibility"}, parent: {"id": "2", "name": "Parent visibility"}}
    if fault == "parent_security":
        original = env.provider.get_json
        def security(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path in {f"/rest/api/2/issue/{key}", f"/rest/api/2/issue/{parent}"}:
                result["fields"]["security"] = levels[path.rsplit("/", 1)[-1]]
            return result
        monkeypatch.setattr(env.provider, "get_json", security)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            body = {"action": "convert_to_subtask", "parent_key": parent, "issue_type_id": "7"}
            preview = await mcp.call_tool("get_hierarchy_options", {"key": key, "body": body})
            assert not preview.is_error
            env.client.mutation_actor_identity()  # Freeze the original account before replacement.
            if fault == "source_changed":
                env.store.issues[key]["tupdated"] = "15:42"
            elif fault == "actor_changed":
                original = env.provider.get_json
                monkeypatch.setattr(env.provider, "get_json", lambda path, *args, **kwargs:
                    {"name": "u02", "key": "u02"} if path == "/rest/api/2/myself" else original(path, *args, **kwargs))
            elif fault == "epic_type":
                body["issue_type_id"] = env.store.serializer.type_map["Epic"]
            else:
                assert preview.structured_content["data"]["securityLevel"] == levels[key]
                assert preview.structured_content["data"]["parentSecurityLevel"] == levels[parent]
                levels[parent] = {"id": "3", "name": "Changed parent visibility"}
            result = await mcp.call_tool("change_ticket_hierarchy", {"key": key, "body": body,
                "preview_hash": preview.structured_content["data"]["previewHash"],
                "client_mutation_id": "hierarchy:changed:001"})
            assert result.is_error and wizard.commits == 0
            assert env.store.issues[key]["type"] == "Task" and env.store.issues[key].get("parentKey") is None
    anyio.run(scenario)


@pytest.mark.parametrize("blocked", ["done", "has_children", "epic_source", "epic_parent", "subtask_parent", "cross_project", "self"])
def test_invalid_hierarchy_never_enters_jira_wizard(build_env, monkeypatch, blocked):
    env = build_env()
    wizard = WizardFixture(env, monkeypatch)
    key, parent = seed(env), seed(env)
    if blocked == "done":
        env.client.get_issue_light(key)  # A fresh display cache must not authorize this write.
        env.store.issues[key].update(statusName="Done", statusCategory="done")
    elif blocked == "has_children":
        seed(env, "Sub-task", key)
    elif blocked.startswith("epic"):
        env.store.issues[key if blocked == "epic_source" else parent]["type"] = "Epic"
    elif blocked == "subtask_parent":
        env.store.issues[parent]["type"] = "Sub-task"
    elif blocked == "cross_project":
        env.store.issues[parent]["project"] = "OTHER"
    else:
        parent = key
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("change_ticket_hierarchy", {"key": key,
                "body": {"action": "convert_to_subtask", "parent_key": parent, "issue_type_id": "7"},
                "preview_hash": "0" * 64, "client_mutation_id": "hierarchy:blocked:001"})
            assert result.is_error and wizard.calls == []
    anyio.run(scenario)


@pytest.mark.parametrize("mode", ["missing_value", "unknown_field", "permission"])
def test_form_requirements_and_permission_failures_never_commit(build_env, monkeypatch, mode):
    env = build_env()
    wizard = WizardFixture(env, monkeypatch)
    key, parent = seed(env), seed(env)
    wizard.required, wizard.denied = "customfield_42" if mode == "missing_value" else "", mode == "permission"
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            body = {"action": "convert_to_subtask", "parent_key": parent, "issue_type_id": "7"}
            if mode == "unknown_field":
                body["field_values"] = {"invented_field": "never submitted"}
            if mode == "missing_value":
                result = await mcp.call_tool("get_hierarchy_options", {"key": key, "body": body})
                assert result.structured_content["data"]["inputs"][0]["id"] == "customfield_42"
            result = await mcp.call_tool("change_ticket_hierarchy", {"key": key, "body": body,
                "preview_hash": "0" * 64, "client_mutation_id": "hierarchy:form:001"})
            assert result.is_error and wizard.commits == 0 and env.store.issues[key]["type"] == "Task"
            if mode == "permission":
                assert result.structured_content["error"]["code"] == "permission_denied"
    anyio.run(scenario)


@pytest.mark.parametrize("mode", ["lost_committed", "not_committed", "lost_custom_fields", "false_success"])
def test_final_post_response_loss_reconciles_only_proven_relation_without_another_post(build_env, monkeypatch, mode):
    env = build_env()
    wizard = WizardFixture(env, monkeypatch)
    key, parent = seed(env), seed(env)
    wizard.lost = mode != "false_success"
    wizard.no_commit = mode in {"not_committed", "false_success"}
    wizard.required = "customfield_42" if mode == "lost_custom_fields" else ""
    generation = env.client._jql_generation()
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            body = {"action": "convert_to_subtask", "parent_key": parent, "issue_type_id": "7"}
            if wizard.required:
                body["field_values"] = {wizard.required: "provided value"}
            prepared = await mcp.call_tool("get_hierarchy_options", {"key": key, "body": body})
            assert not prepared.is_error, prepared.structured_content
            args = {"key": key, "body": body, "client_mutation_id": "hierarchy:lost:001",
                    "preview_hash": prepared.structured_content["data"]["previewHash"]}
            first = await mcp.call_tool("change_ticket_hierarchy", args)
            assert first.is_error and first.structured_content["error"]["code"] == "outcome_unknown"
            for _ in range(2):
                result = await mcp.call_tool("change_ticket_hierarchy", args)
                assert result.is_error == (mode != "lost_committed"), result.structured_content
                if result.is_error:
                    assert result.structured_content["error"]["repeatWrite"] is False
                else:
                    assert result.structured_content["data"]["parentKey"] == parent
            assert wizard.commits == 1
            if wizard.no_commit:
                assert env.store.issues[key]["type"] == "Task"
                assert env.client._jql_generation() == generation
    anyio.run(scenario)


@pytest.mark.parametrize("fault", ["wizard_busy", "identity_read", "final_auth_reply", "verify_auth", "verify_malformed"])
def test_preflight_retries_and_final_login_reply_do_not_duplicate_submit(build_env, monkeypatch, fault):
    import threading
    env = build_env()
    wizard = WizardFixture(env, monkeypatch)
    key, parent = seed(env), seed(env)
    original_actor, original_form = env.client.assert_mutation_actor_identity, env.provider.web_form
    original_get = env.provider.get_json
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            body = {"action": "convert_to_subtask", "parent_key": parent, "issue_type_id": "7"}
            prepared = await mcp.call_tool("get_hierarchy_options", {"key": key, "body": body})
            args = {"key": key, "body": body, "client_mutation_id": "hierarchy:preflight:001",
                    "preview_hash": prepared.structured_content["data"]["previewHash"]}
            if fault == "wizard_busy":
                env.client._hierarchy_wizard_lock = threading.Lock()
                env.client._hierarchy_wizard_lock.acquire()
            elif fault == "identity_read":
                def unavailable(_actor):
                    raise UpstreamUnavailable("identity check failed before final POST")
                monkeypatch.setattr(env.client, "assert_mutation_actor_identity", unavailable)
            elif fault == "final_auth_reply":
                def login_reply(*args, **kwargs):
                    result = original_form(*args, **kwargs)
                    if kwargs.get("may_commit"):
                        raise SessionExpired("login page after final submission")
                    return result
                monkeypatch.setattr(env.provider, "web_form", login_reply)
            else:
                def verification_failure(path, *args, **kwargs):
                    if wizard.commits and path == f"/rest/api/2/issue/{key}":
                        if fault == "verify_auth":
                            raise UpstreamError(401, path)
                        return {"id": "1", "fields": "invalid response"}
                    return original_get(path, *args, **kwargs)
                monkeypatch.setattr(env.provider, "get_json", verification_failure)
            first = await mcp.call_tool("change_ticket_hierarchy", args)
            assert first.is_error
            error = first.structured_content["error"]
            if fault in {"final_auth_reply", "verify_auth", "verify_malformed"}:
                assert error["code"] == "outcome_unknown" and wizard.commits == 1
            else:
                assert error["writeExecuted"] is False and wizard.commits == 0
            if fault == "wizard_busy":
                env.client._hierarchy_wizard_lock.release()
            monkeypatch.setattr(env.client, "assert_mutation_actor_identity", original_actor)
            monkeypatch.setattr(env.provider, "web_form", original_form)
            monkeypatch.setattr(env.provider, "get_json", original_get)
            for _ in range(2):
                result = await mcp.call_tool("change_ticket_hierarchy", args)
                assert not result.is_error, result.structured_content
            assert wizard.commits == 1
    anyio.run(scenario)


def test_unsupported_native_form_keeps_its_error_code(build_env, monkeypatch):
    env = build_env()
    WizardFixture(env, monkeypatch)
    key, parent = seed(env), seed(env)
    monkeypatch.setattr(env.provider, "web_form", lambda *args, **kwargs: {
        "status": 200, "location": "", "html": "<p>Unknown wizard plugin</p>"})
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("get_hierarchy_options", {"key": key, "body": {
                "action": "convert_to_subtask", "parent_key": parent}})
            assert result.is_error
            assert result.structured_content["error"]["code"] == "unsupported_hierarchy_form"
    anyio.run(scenario)


@pytest.mark.parametrize("alias", ["source", "parent"])
def test_moved_issue_alias_cannot_mutate_outside_requested_project(build_env, monkeypatch, alias):
    env = build_env()
    wizard = WizardFixture(env, monkeypatch)
    key, parent = seed(env), seed(env)
    original = env.provider.get_json
    def moved(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        target = key if alias == "source" else parent
        if path == f"/rest/api/2/issue/{target}":
            result["key"] = "OUTSIDE-1"
        return result
    monkeypatch.setattr(env.provider, "get_json", moved)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("change_ticket_hierarchy", {"key": key, "body": {
                "action": "convert_to_subtask", "parent_key": parent, "issue_type_id": "7"},
                "preview_hash": "0" * 64, "client_mutation_id": "hierarchy:alias:001"})
            assert result.is_error and wizard.calls == []
    anyio.run(scenario)
