"""Manual DevTool run over real MCP SDK and independent native Jira form fixture."""

import json
import asyncio
import threading
import time

import pytest
import anyio

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"

from app.services.dev_hierarchy_probe import HierarchyProbe, ProbeError
from app.auth.base import SessionExpired
from app.auth.web_forms import FormTransportUnavailable
from tests.mcp.test_hierarchy_actions import WizardFixture


@pytest.mark.parametrize("response,diagnostic", [
    ({"status": 404, "html": "private server response", "location": ""},
     {"reason": "http_response", "httpStatus": 404}),
    ({"status": 200, "html": '<form action="https://private.example/?token=secret"><input value="private"></form>', "location": ""},
     {"reason": "form_not_identified", "httpStatus": 200, "formCount": 1, "supportedFormCount": 0}),
    (FormTransportUnavailable(timeout=True), {"reason": "transport_timeout"}),
])
async def test_native_form_failure_evidence_survives_sdk_journal_and_copy(build_env, monkeypatch, tmp_path, response, diagnostic):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    def form(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(env.provider, "web_form", form)
    run_id = "probe:form:0001"
    await start(probe, run_id, "Task", "Sub-task")
    result = await settled(probe, run_id)
    assert result["status"] == "failed"
    assert result["steps"][3]["error"]["formDiagnostic"] == diagnostic
    assert result["error"]["formDiagnostic"] == diagnostic
    restored = await HierarchyProbe(env.runtime, probe.state_dir).get(run_id)
    assert restored["steps"][3]["error"]["formDiagnostic"] == diagnostic
    assert len(creates(env)) == 3 and wizard.commits == 0
    assert "private" not in json.dumps(result) and "secret" not in json.dumps(result)


async def settled(probe, run_id):
    deadline = time.monotonic() + 15
    while probe._active and time.monotonic() < deadline:
        await anyio.sleep(.01)
    assert probe._active is None, "background probe did not settle"
    return await probe.get(run_id)


async def start(probe, run_id, task_type="Task", subtask_type="Sub-task"):
    scope = (await probe.options())["scope"]
    return await probe.start(run_id, task_type, subtask_type, scope)


def setup_probe(build_env, monkeypatch, tmp_path):
    env = build_env()
    wizard = WizardFixture(env, monkeypatch)
    return env, wizard, HierarchyProbe(env.runtime, tmp_path / "probe-journal")


def creates(env):
    return [call for call in env.calls if call[:2] == ("post", "/rest/api/2/issue")]


async def test_sdk_probe_creates_only_three_owned_tickets_and_roundtrips(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    options = await probe.options()
    assert options["taskType"] == "Task" and options["subtaskType"] == "Sub-task"
    started = await start(probe, "probe:roundtrip:0001", "Task", "Sub-task")
    assert started["scope"] == options["scope"]
    result = await settled(probe, started["runId"])
    assert result["status"] == "passed", result
    assert len(creates(env)) == 3 and wizard.commits == 3
    assert len(result["createdTickets"]) == 3
    assert all(step["status"] == "passed" for step in result["steps"])
    assert result["steps"][-1]["parentKey"] is None
    assert all(row["url"].startswith("https://jira.fixture.test/jira/browse/") for row in result["createdTickets"])
    public = json.dumps(result)
    assert "_actor" not in public and "preview_hash" not in public and "fixture-secret" not in public
    journal = probe._path(started["runId"])
    assert json.loads(journal.read_text(encoding="utf-8"))["_calls"]["convert_to_subtask"][1]["preview_hash"]
    env.cache.invalidate()
    restarted = HierarchyProbe(env.runtime, probe.state_dir)
    assert (await start(restarted, started["runId"], "Task", "Sub-task"))["status"] == "passed"
    assert len(creates(env)) == 3 and wizard.commits == 3


async def test_lost_final_response_resumes_exact_payload_without_duplicate_writes(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    wizard.lost = True
    await start(probe, "probe:unknown:0001", "Task", "Sub-task")
    first = await settled(probe, "probe:unknown:0001")
    assert first["status"] == "blocked" and first["error"]["outcomeUnknown"], first
    assert len(creates(env)) == 3 and wizard.commits == 1
    planned = json.loads(probe._path(first["runId"]).read_text(encoding="utf-8"))["_calls"]["convert_to_subtask"]
    wizard.lost = False
    restarted = HierarchyProbe(env.runtime, probe.state_dir)
    await start(restarted, first["runId"], "Task", "Sub-task")
    final = await settled(restarted, first["runId"])
    assert final["status"] == "passed", final
    assert len(creates(env)) == 3 and wizard.commits == 3
    assert json.loads(restarted._path(first["runId"]).read_text(encoding="utf-8"))["_calls"]["convert_to_subtask"] == planned


@pytest.mark.parametrize("fault,code", [("permission", "permission_denied"), ("required", "required_input"), ("done", "conflict")])
async def test_failure_stops_after_owned_creation_without_later_native_writes(build_env, monkeypatch, tmp_path, fault, code):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    if fault == "permission":
        wizard.denied = True
    elif fault == "required":
        wizard.required = "customfield_required"
    else:
        original = probe._plan
        async def plan(mcp, run, step, index):
            if index == 3:
                env.store.issues[run["steps"][2]["key"]].update(statusCategory="done", statusName="Done")
            return await original(mcp, run, step, index)
        monkeypatch.setattr(probe, "_plan", plan)
    await start(probe, "probe:failure:0001", "Task", "Sub-task")
    result = await settled(probe, "probe:failure:0001")
    assert result["error"]["code"] == code, result
    assert len(result["createdTickets"]) == 3 and wizard.commits == 0
    assert result["steps"][-1]["status"] == "pending"
    if fault == "required":
        assert result["steps"][3]["inputs"][0]["id"] == "customfield_required"


async def test_required_create_metadata_blocks_before_any_ticket(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    original = env.client.create_fields
    monkeypatch.setattr(env.client, "create_fields", lambda *args, **kwargs: {
        **original(*args, **kwargs), "customfield_private": {"name": "필수 항목", "required": True, "schema": {"type": "string"}}})
    await start(probe, "probe:metadata:0001", "Task", "Sub-task")
    result = await settled(probe, "probe:metadata:0001")
    assert result["status"] == "blocked" and result["error"]["code"] == "required_input"
    assert not creates(env) and wizard.commits == 0


async def test_server_change_before_next_write_stops_and_never_creates_as_replacement(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    original = env.provider.post_json
    def post(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == "/rest/api/2/issue":
            env.client.s.jira_base = "https://other-jira.test"
        return result
    monkeypatch.setattr(env.provider, "post_json", post)
    await start(probe, "probe:identity:0001", "Task", "Sub-task")
    with pytest.raises(ProbeError, match="서버"):
        await settled(probe, "probe:identity:0001")
    # The public journal read also rejects the changed server, while the saved run records
    # why it stopped before the second write.
    assert probe._load("probe:identity:0001")["error"]["code"] == "scope_changed"
    assert len(creates(env)) == 1 and wizard.commits == 0


async def test_one_active_run_and_journal_failure_prevent_new_writes(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = probe._requirements
    async def held(mcp, run):
        entered.set()
        await anyio.to_thread.run_sync(lambda: release.wait(10))
        return await original(mcp, run)
    monkeypatch.setattr(probe, "_requirements", held)
    await start(probe, "probe:active:0001", "Task", "Sub-task")
    assert await anyio.to_thread.run_sync(lambda: entered.wait(5))
    try:
        assert (await start(probe, "probe:active:0001", "Task", "Sub-task"))["status"] == "running"
        with pytest.raises(ProbeError):
            await start(probe, "probe:active:0002", "Task", "Sub-task")
    finally:
        release.set()
    assert (await settled(probe, "probe:active:0001"))["status"] == "passed"
    monkeypatch.setattr(probe, "_save", lambda run: (_ for _ in ()).throw(ProbeError("journal_unavailable")))
    with pytest.raises(ProbeError):
        await start(probe, "probe:save:0001", "Task", "Sub-task")
    assert len(creates(env)) == 3 and wizard.commits == 3


async def test_sdk_response_loss_after_create_replays_receipt_and_hides_raw_exception(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    original = probe._call
    lost = [False]
    async def call(mcp, name, arguments):
        result = await original(mcp, name, arguments)
        if name == "create_task" and not lost[0]:
            lost[0] = True
            raise TimeoutError("<html>private-token must not escape</html>")
        return result
    monkeypatch.setattr(probe, "_call", call)
    await start(probe, "probe:sdk-loss:0001", "Task", "Sub-task")
    first = await settled(probe, "probe:sdk-loss:0001")
    assert first["error"]["outcomeUnknown"] and len(creates(env)) == 1
    assert "private-token" not in probe._path(first["runId"]).read_text(encoding="utf-8")
    await start(probe, first["runId"], "Task", "Sub-task")
    final = await settled(probe, first["runId"])
    assert final["status"] == "passed" and len(creates(env)) == 3 and wizard.commits == 3


async def test_old_journal_cannot_recreate_tickets_after_receipt_expiry(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    wizard.lost = True
    await start(probe, "probe:expired:0001", "Task", "Sub-task")
    first = await settled(probe, "probe:expired:0001")
    run = probe._load(first["runId"])
    run["_createdAt"] = time.time() - 8 * 24 * 3600
    probe._save(run)
    # Simulate normal receipt expiry while the private journal remains.
    env.cache._conn.execute("DELETE FROM mutation_receipt")
    env.cache._conn.commit()
    restarted = HierarchyProbe(env.runtime, probe.state_dir)
    await start(restarted, first["runId"], "Task", "Sub-task")
    result = await settled(restarted, first["runId"])
    assert result["status"] == "blocked" and result["error"]["outcomeUnknown"]
    assert len(creates(env)) == 3 and wizard.commits == 1


async def test_options_scope_must_match_before_starting_any_write(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    scope = (await probe.options())["scope"]
    env.client.s.jira_base = "https://another-jira.test"
    with pytest.raises(ProbeError, match="서버"):
        await probe.start("probe:scope:0001", "Task", "Sub-task", scope)
    assert not creates(env) and not list(probe.state_dir.glob("*.json"))


async def test_optional_cached_identity_does_not_change_local_probe_scope(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    scope = (await probe.options())["scope"]
    assert env.client.mutation_actor_identity()["aliases"] == []
    env.client.current_user()  # An ordinary business-data request warms optional creator evidence.
    assert env.client.mutation_actor_identity()["aliases"]
    assert (await probe.options())["scope"] == scope
    await probe.start("probe:identity-cache:0001", "Task", "Sub-task", scope)
    result = await settled(probe, "probe:identity-cache:0001")
    assert result["status"] == "passed" and len(creates(env)) == 3 and wizard.commits == 3
    env.cache.forget(f"myself:{env.client.env}")
    assert (await probe.get("probe:identity-cache:0001"))["status"] == "passed"


@pytest.mark.parametrize("changed", [True, False])
async def test_sdk_auth_retry_uses_local_scope_instead_of_an_account_gate(build_env, monkeypatch, tmp_path, changed):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    original_get, original_post = env.provider.get_json, env.provider.post_json
    rejected, recovered = [False], [False]
    def post(path, body=None, **kwargs):
        if path == "/rest/api/2/issue" and not rejected[0]:
            rejected[0] = True
            raise SessionExpired("fixture expired before write")
        return original_post(path, body, **kwargs)
    def get(path, *args, **kwargs):
        if path == "/rest/api/2/myself" and recovered[0] and changed:
            return {"name": "replacement", "key": "replacement"}
        return original_get(path, *args, **kwargs)
    def check(service="jira", *, force=False):
        if force:
            recovered[0] = True
        return {"authenticated": True}
    monkeypatch.setattr(env.provider, "post_json", post)
    monkeypatch.setattr(env.provider, "get_json", get)
    monkeypatch.setattr(env.runtime.authentication, "check", check)
    await start(probe, "probe:auth-retry:0001", "Task", "Sub-task")
    result = await settled(probe, "probe:auth-retry:0001")
    assert recovered[0]
    assert result["status"] == "passed" and len(creates(env)) == 3 and wizard.commits == 3
    # A definite pre-write 401 may retry after recovery, without a separate /myself gate.
    assert not any(path == "/rest/api/2/myself" for _method, path, _body in env.calls)


async def test_translated_native_type_labels_match_authoritative_ids(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    original = wizard.form
    def form(*args, **kwargs):
        result = original(*args, **kwargs)
        result["html"] = result["html"].replace(">Sub-task</option>", ">하위 작업</option>").replace(">Task</option>", ">작업</option>")
        return result
    monkeypatch.setattr(wizard, "form", form)
    await start(probe, "probe:translated:0001", "Task", "Sub-task")
    assert (await settled(probe, "probe:translated:0001"))["status"] == "passed"
    assert wizard.commits == 3


async def test_lifespan_cancels_owned_job_and_preserves_resumable_journal(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    entered = asyncio.Event()
    async def held(mcp, run):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(probe, "_requirements", held)
    await start(probe, "probe:shutdown:0001")
    await entered.wait()
    await probe.close()
    assert probe._task.done() and probe._active is None and not creates(env)
    assert (await probe.get("probe:shutdown:0001"))["status"] == "blocked"
    restarted = HierarchyProbe(env.runtime, probe.state_dir)
    await start(restarted, "probe:shutdown:0001")
    assert (await settled(restarted, "probe:shutdown:0001"))["status"] == "passed"
    assert len(creates(env)) == 3 and wizard.commits == 3


async def test_response_caps_preserve_unknown_state_and_keys(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    async def types():
        return [{"id": str(i), "name": str(i) + "가" * 100, "subtask": True} for i in range(100)]
    monkeypatch.setattr(probe, "_project_types", types)
    with pytest.raises(ProbeError) as error:
        await probe.options()
    assert error.value.status == 413 and not creates(env)
    result = probe._public({"runId": "probe:cap:0001", "status": "blocked",
        "steps": [{"name": "convert_to_subtask", "inputs": ["large" * 2000], "changes": []}],
        "createdTickets": [{"key": "DL-123", "role": "create_subject"}],
        "error": {"outcomeUnknown": True, "retryable": True}})
    assert result["detailsOmitted"] and "inputs" not in result["steps"][0]
    assert result["error"]["outcomeUnknown"] and result["createdTickets"][0]["key"] == "DL-123"
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 8192


async def test_post_create_journal_failure_replay_keeps_three_unique_ticket_rows(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    original, failed = probe._save, [False]
    def save(run):
        if run["steps"][0]["status"] == "passed" and not failed[0]:
            failed[0] = True
            raise ProbeError("journal_unavailable")
        return original(run)
    monkeypatch.setattr(probe, "_save", save)
    await start(probe, "probe:save-after-create:0001")
    first = await settled(probe, "probe:save-after-create:0001")
    assert first["error"]["outcomeUnknown"] and len(creates(env)) == 1
    restarted = HierarchyProbe(env.runtime, probe.state_dir)
    await start(restarted, first["runId"])
    result = await settled(restarted, first["runId"])
    assert result["status"] == "passed" and len(creates(env)) == 3
    assert len(result["createdTickets"]) == len({row["key"] for row in result["createdTickets"]}) == 3


@pytest.mark.parametrize("fault", ["removed_field", "security"])
async def test_probe_never_auto_accepts_field_loss_or_security_change(build_env, monkeypatch, tmp_path, fault):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    if fault == "removed_field":
        original = wizard.form
        def form(action, *args, **kwargs):
            result = original(action, *args, **kwargs)
            if action.endswith("Convert.jspa"):
                result["html"] += '<table><tr><td>Custom field</td><td><span class="status-inactive">value</span></td><td></td></tr></table>'
            return result
        monkeypatch.setattr(wizard, "form", form)
    else:
        original = env.provider.get_json
        def get(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            key = path.rsplit("/", 1)[-1]
            if path.startswith("/rest/api/2/issue/") and key in env.store.issues:
                subject = "create_subject" in env.store.issues[key].get("summary", "")
                result["fields"]["security"] = {"id": "1" if subject else "2"}
            return result
        monkeypatch.setattr(env.provider, "get_json", get)
    await start(probe, "probe:unsafe-preview:0001")
    result = await settled(probe, "probe:unsafe-preview:0001")
    assert result["status"] == "blocked" and result["error"]["code"] == "unsafe_preview"
    assert len(creates(env)) == 3 and wizard.commits == 0
    assert all(step["status"] == "pending" for step in result["steps"][4:])


async def test_optional_create_fields_absent_from_screen_are_not_sent(build_env, monkeypatch, tmp_path):
    env, wizard, probe = setup_probe(build_env, monkeypatch, tmp_path)
    original = env.client.create_fields
    monkeypatch.setattr(env.client, "create_fields", lambda *args, **kwargs: {
        key: value for key, value in original(*args, **kwargs).items() if key not in {"labels", "description"}})
    await start(probe, "probe:optional-fields:0001")
    assert (await settled(probe, "probe:optional-fields:0001"))["status"] == "passed"
    assert all(not {"labels", "description"} & row[2]["fields"].keys() for row in creates(env))
