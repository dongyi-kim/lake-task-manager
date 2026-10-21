"""Real Session/QueryRunner/ContextGuard, in-process jira820, no model/socket/write."""
from copy import deepcopy
from contextlib import contextmanager
import json
import threading
from types import SimpleNamespace

import pytest

from app.agent import approval
from app.agent.maxis.contracts import (
    CompactQueryPlanV1, DraftQuestionV1, QueryIntentV1, RemoteRoleResultV1,
)
from app.agent.maxis.taskflow import TaskFlowError
from app.agent.maxis.taskflow_contracts import TaskFlowPinV1, TaskFlowResultV1
from app.agent.maxis.taskflow_session import TaskFlowPreviewHost, TaskFlowPreviewRuntime
from app.agent.tools import _ctx
from app.agent.workflow import session
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient


class Remote:
    """Only external authority/release/transport seams are replaced."""
    def __init__(self):
        self.calls = []
        self.on_dispatch = None
        self.denied = False

    def authorize(self, request):
        if self.denied:
            raise TaskFlowError("access_denied")

    def verify_release(self, request):
        pass  # test double, never installed by application composition

    def dispatch(self, request, budget):
        budget.reserve(models=1)
        self.calls.append(request)
        if self.on_dispatch:
            result = self.on_dispatch(request)
            if result is not None:
                return result
        data = dict(request_id=request.request_id, revision=request.revision,
                    pin=request.pin, evidence_sha256=request.evidence_sha256)
        if request.revision == 0:
            return TaskFlowResultV1(**data, status="needs_evidence",
                reason="insufficient_evidence", query_plan=CompactQueryPlanV1(reads=(
                    QueryIntentV1(source="jira", where="key = DL-9001", exhaustive=True),)))
        return TaskFlowResultV1(**data, status="needs_input", reason="input_required",
            questions=(DraftQuestionV1(question="점검 범위는 무엇인가요?", kind="text",
                       required_input=True, why_required="업무 범위 확인 필요"),))

    def validate_draft(self, request, result):
        raise AssertionError("This fixture must not produce a draft")


@pytest.fixture
def harness(monkeypatch):
    settings = deepcopy(get_settings())
    assert settings.jira_env == "mock"
    cache = Cache(":memory:")
    _ctx.bind(JiraClient(settings, cache), settings)
    remote = Remote()
    pin = TaskFlowPinV1(profile_fingerprint="fixture-profile", workflow_release="wf-1",
                       workflow_sha256="a" * 64, research_release="research-1",
                       research_sha256="b" * 64, knowledge_manifest_sha256="c" * 64)
    host = TaskFlowPreviewHost(TaskFlowPreviewRuntime(pin, remote))
    state = {"thread_id": "preview-thread", "request_text": "진척 점검 초안",
             "runtime_profile": "prod", "runtime_profile_fingerprint": "fixture-profile",
             "knowledge_manifest_sha256": "c" * 64,
             "draft": {"items": []}, "approval_token": "", "comment_token": ""}
    revision = ["checkpoint-1"]
    graph = SimpleNamespace(get_state=lambda config: SimpleNamespace(
        values=state, next=(), config={"configurable": {"checkpoint_id": revision[0]}}))
    monkeypatch.setattr(session, "get_graph", lambda: graph)
    monkeypatch.setattr(session, "_taskflow_previews", host)
    monkeypatch.setattr(session._cfg, "callbacks", lambda **kwargs: [])
    host.begin_turn("preview-thread", state["request_text"], host.admit("preview-thread"))
    before_approvals = deepcopy(approval._pending)
    yield SimpleNamespace(host=host, remote=remote, state=state, revision=revision,
                          settings=settings, graph=graph)
    assert approval._pending == before_approvals
    host.cancel("preview-thread")
    session._STOP.discard("preview-thread")
    _ctx.bind()
    cache.close()


def test_session_reads_actual_scoped_mock_and_only_keeps_preview(harness):
    before = deepcopy(harness.state)
    result = session.taskflow_preview("preview-thread")
    assert result.status == "needs_input" and result.revision == 1
    first, second = harness.remote.calls
    assert first.request_id == second.request_id
    assert first.evidence_sha256 != second.evidence_sha256
    assert any(source.kind == "jira" and source.canonical_id == "DL-9001"
               for source in second.evidence.sources)
    assert second.evidence.scope.projects == first.evidence.scope.projects
    assert harness.state == before
    public = session.snapshot("preview-thread")
    assert public["taskflowPreview"] == {
        "mode": "preview", "status": "needs_input", "reason": "input_required",
        "revision": 1, "qualityStatus": "not_evaluated", "approvalRequired": True,
    }
    assert "pending" not in public and "draft_items" not in public
    assert session.taskflow_preview("preview-thread") == result
    assert len(harness.remote.calls) == 2


def test_default_composition_cannot_dispatch_or_grant_capabilities(harness, monkeypatch):
    monkeypatch.setattr(session, "_taskflow_previews", TaskFlowPreviewHost())
    with pytest.raises(TaskFlowError, match="deployment_unverified"):
        session.taskflow_preview("preview-thread")
    assert not harness.remote.calls


@pytest.mark.parametrize("field,value", [
    ("runtime_profile", "dev"), ("runtime_profile_fingerprint", "another-profile"),
    ("knowledge_manifest_sha256", "d" * 64),
])
def test_profile_and_knowledge_cannot_rebind(harness, field, value):
    harness.state[field] = value
    with pytest.raises(TaskFlowError, match="deployment_unverified"):
        session.taskflow_preview("preview-thread")
    assert not harness.remote.calls


def test_unverified_release_stops_before_remote_dispatch(harness):
    def denied(request):
        raise TaskFlowError("deployment_unverified")
    harness.remote.verify_release = denied
    with pytest.raises(TaskFlowError, match="deployment_unverified"):
        session.taskflow_preview("preview-thread")
    assert not harness.remote.calls and not harness.host._previews


def test_fresh_actor_and_scope_revocation_hide_cached_preview(harness, monkeypatch):
    session.taskflow_preview("preview-thread")
    monkeypatch.setattr(approval, "current_actor_fresh", lambda: {"id": "other-user"})
    assert "taskflowPreview" not in session.snapshot("preview-thread")
    assert not harness.host._previews


def test_scope_change_during_dispatch_prevents_local_acquisition(harness):
    harness.remote.on_dispatch = lambda request: setattr(
        harness.settings, "search_jira_projects", [])
    with pytest.raises(TaskFlowError, match="access_or_release_revoked"):
        session.taskflow_preview("preview-thread")
    assert len(harness.remote.calls) == 1
    assert not harness.host._previews


def test_checkpoint_change_during_dispatch_discards_late_result(harness):
    harness.remote.on_dispatch = lambda request: harness.revision.__setitem__(0, "new")
    with pytest.raises(TaskFlowError, match="access_or_release_revoked"):
        session.taskflow_preview("preview-thread")
    assert len(harness.remote.calls) == 1 and not harness.host._previews


def test_existing_stop_discards_preview_and_late_result(harness):
    harness.remote.on_dispatch = lambda request: session.request_stop("preview-thread")
    result = session.taskflow_preview("preview-thread")
    assert result.status == "blocked" and result.reason == "cancelled"
    assert result.draft_result is None and not harness.host._previews


@pytest.mark.parametrize("entrypoint", ["ask", "stream"])
def test_actual_new_turn_cancels_inflight_preview_before_waiting_for_lock(
        harness, monkeypatch, entrypoint):
    from app.agent.maxis import backend
    monkeypatch.setattr(backend, "prepare_thread_runtime", lambda *a, **k: {})
    monkeypatch.setattr(session, "_guard", lambda text: None)
    dispatched, release, cancelled, graph_entered = (threading.Event() for _ in range(4))
    errors, previews, replies = [], [], []
    original_cancel = harness.host.cancel

    def cancel(tid):
        original_cancel(tid)
        if threading.current_thread().name == "new-turn":
            cancelled.set()
    monkeypatch.setattr(harness.host, "cancel", cancel)

    def dispatch(request):
        dispatched.set()
        assert release.wait(5), "test did not release fake remote dispatch"
    harness.remote.on_dispatch = dispatch

    def invoke(initial, config):
        graph_entered.set()
        harness.state.clear()
        harness.state.update(initial, reply="새 요청 처리")
        harness.revision[0] = "checkpoint-2"
        return harness.state
    harness.graph.invoke = invoke
    harness.graph.stream = lambda initial, config, **kwargs: (invoke(initial, config), iter(()))[1]

    def capture(target, operation):
        try:
            target.append(operation())
        except Exception as exc:
            errors.append(exc)
    old_turn = threading.Thread(target=lambda: capture(
        previews, lambda: session.taskflow_preview("preview-thread")), name="old-preview")
    operation = (lambda: session.ask("새 요청", "preview-thread")) if entrypoint == "ask" else (
        lambda: list(session.stream("새 요청", "preview-thread")))
    new_turn = threading.Thread(target=lambda: capture(replies, operation), name="new-turn")
    old_turn.start()
    try:
        assert dispatched.wait(3)
        new_turn.start()
        assert cancelled.wait(2), "new request waited for old remote execution before cancelling"
        assert not graph_entered.is_set()  # checkpoint admission remains serialized
        assert not harness.host._previews
        assert "taskflowPreview" not in session.snapshot("preview-thread")
    finally:
        release.set()
        old_turn.join(5)
        if new_turn.ident is not None:
            new_turn.join(5)
    assert not old_turn.is_alive() and not new_turn.is_alive() and not errors
    assert previews[0].reason == "cancelled" and previews[0].draft_result is None
    assert len(harness.remote.calls) == 1  # no read or second old dispatch after admission
    assert graph_entered.is_set() and harness.revision[0] == "checkpoint-2"
    assert replies and not harness.host._previews


@pytest.mark.parametrize("newer_action", ["ask", "stream", "stop"])
def test_newer_admission_or_stop_cannot_rebind_older_preparation(
        harness, monkeypatch, newer_action):
    from app.agent.maxis import backend
    runtime = {key: harness.state[key] for key in (
        "runtime_profile", "runtime_profile_fingerprint", "knowledge_manifest_sha256")}
    monkeypatch.setattr(backend, "prepare_thread_runtime", lambda *a, **k: runtime)
    monkeypatch.setattr(session, "_guard", lambda text: None)
    preparing, release, queued, admitted, preview_done = (threading.Event() for _ in range(5))
    outcomes = {}
    original_lock, original_admit = session.question_turn_lock, harness.host.admit

    def initial(tid, text, *args):
        if text == "old request":
            preparing.set()
            assert release.wait(5)
        return {"thread_id": tid, "messages": [], "request_text": text}
    monkeypatch.setattr(session, "_initial", initial)

    def admit(tid):
        token = original_admit(tid)
        if threading.current_thread().name == "newer-turn":
            admitted.set()
        return token
    monkeypatch.setattr(harness.host, "admit", admit)

    @contextmanager
    def scheduled_lock(tid):
        name = threading.current_thread().name
        if name == "queued-preview":
            queued.set()
        if name == "newer-turn":
            assert preview_done.wait(5)  # deterministically exercise a queued stale consumer
        with original_lock(tid):
            yield
    monkeypatch.setattr(session, "question_turn_lock", scheduled_lock)

    def invoke(state, config):
        harness.state.clear()
        harness.state.update(state, reply="새 요청 처리")
        harness.revision[0] = state["request_text"]
        return harness.state
    harness.graph.invoke = invoke
    harness.graph.stream = lambda state, config, **kwargs: (invoke(state, config), iter(()))[1]

    def worker(name, operation):
        try:
            outcomes[name] = operation()
        except Exception as exc:
            outcomes[name] = exc
        finally:
            if name == "preview":
                preview_done.set()
    old = threading.Thread(target=lambda: worker("old", lambda: session.ask(
        "old request", "preview-thread")), name="older-turn")
    preview = threading.Thread(target=lambda: worker("preview", lambda: session.taskflow_preview(
        "preview-thread")), name="queued-preview")
    newer = threading.Thread(target=lambda: worker("new", lambda: session.ask(
        "new request", "preview-thread") if newer_action == "ask" else list(session.stream(
            "new request", "preview-thread"))), name="newer-turn")
    old.start()
    try:
        assert preparing.wait(3)
        preview.start()
        assert queued.wait(3)
        if newer_action == "stop":
            session.request_stop("preview-thread")
        else:
            newer.start()
            assert admitted.wait(3)
    finally:
        release.set()
        for thread in (old, preview, newer):
            if thread.ident is not None:
                thread.join(5)
    assert all(not thread.is_alive() for thread in (old, preview, newer))
    assert isinstance(outcomes["preview"], TaskFlowError)
    assert outcomes["preview"].code == "access_denied"
    assert not harness.remote.calls and not harness.host._previews
    assert not isinstance(outcomes["old"], Exception)
    if newer_action != "stop":
        assert not isinstance(outcomes["new"], Exception)
        assert harness.host._turns["preview-thread"][2] == harness.host._admissions["preview-thread"][0]


def test_new_turn_boundary_cancels_preview(harness, monkeypatch):
    session.taskflow_preview("preview-thread")
    from app.agent.maxis import backend
    monkeypatch.setattr(backend, "prepare_thread_runtime", lambda *a, **k: {})
    monkeypatch.setattr(session, "_initial", lambda *a: {})
    monkeypatch.setattr(session, "_turn_start_patch", lambda *a: {})
    session._prepare_turn(harness.graph, thread_id="preview-thread", text="새 요청",
                          user_role="", user_id="",
                          taskflow_admission=harness.host.admit("preview-thread"))
    assert not harness.host._previews


def test_retention_expires_preview_content_on_session_access(harness):
    now = [0.0]
    harness.host.clock = lambda: now[0]
    harness.host.begin_turn("preview-thread", harness.state["request_text"],
                           harness.host.admit("preview-thread"))
    session.taskflow_preview("preview-thread")
    now[0] = 1800
    assert "taskflowPreview" not in session.snapshot("preview-thread")
    assert not harness.host._previews


def test_old_checkpoint_artifacts_are_not_exported(harness):
    harness.state["query_results"] = [{"source": "jira", "result": {"secret": "old"}}]
    session.taskflow_preview("preview-thread")
    assert all(source.kind == "ltm" for source in harness.remote.calls[0].evidence.sources)


def test_completed_preview_does_not_stage_or_adopt_a_draft(harness):
    validated = []
    harness.remote.validate_draft = lambda req, result: validated.append(req.request_id)
    def completed(req):
        source = req.evidence.sources[0]
        envelope = RemoteRoleResultV1.model_validate_json(json.dumps({
            "role_id": "work_architect", "status": "ok", "workflow_version": "wf-1",
            "knowledge_version": "kb-1", "knowledge_manifest_sha256": "c" * 64,
            "knowledge_proof": "LTM_RELEASE_SENTINEL_V1=test",
            "payload": {"contract": "work-draft.v1", "mode": "task",
                "structure": "single_task", "items": [{"summary": "진척 점검 초안",
                    "tier": "task", "issue_type": "Task", "scope_in": ["진척 점검"],
                    "dod": ["초안"], "source_ids": [source.source_id]}]},
            "claims": [{"claim_id": "claim-1", "kind": "inference",
                        "text": source.content, "citation_ids": ["cite-1"]}],
            "citations": [{"citation_id": "cite-1", "source_id": source.source_id}],
            "payload_claims": [{"path": path, "claim_ids": ["claim-1"]} for path in
                ["/items/0/summary", "/items/0/scope_in/0", "/items/0/dod/0"]],
        }))
        return TaskFlowResultV1(request_id=req.request_id, revision=req.revision, pin=req.pin,
            evidence_sha256=req.evidence_sha256, status="completed", draft_result=envelope)
    harness.remote.on_dispatch = completed
    before = deepcopy(harness.state)
    result = session.taskflow_preview("preview-thread")
    assert result.status == "completed" and validated
    public = session.snapshot("preview-thread")
    assert public["taskflowPreview"]["status"] == "completed"
    assert "pending" not in public and "draft_items" not in public
    assert "knowledge_proof" not in json.dumps(public)
    assert harness.state == before


def test_poll_before_coordinator_registration_does_not_cancel(harness, monkeypatch):
    from app.agent.maxis.taskflow import TaskFlowCoordinator
    original = TaskFlowCoordinator.run
    statuses = []
    def run(coordinator, request):
        statuses.append(session.snapshot("preview-thread")["taskflowPreview"]["status"])
        return original(coordinator, request)
    monkeypatch.setattr(TaskFlowCoordinator, "run", run)
    assert session.taskflow_preview("preview-thread").status == "needs_input"
    assert statuses == ["running"]


def test_old_registration_failure_cannot_erase_new_admission(harness, monkeypatch):
    from app.agent.maxis.taskflow import TaskFlowCoordinator
    registered, release = threading.Event(), threading.Event()
    original, failures = TaskFlowCoordinator.run, []

    def paused(coordinator, request):
        registered.set()
        assert release.wait(5)
        return original(coordinator, request)
    monkeypatch.setattr(TaskFlowCoordinator, "run", paused)

    def old_preview():
        try:
            session.taskflow_preview("preview-thread")
        except Exception as exc:
            failures.append(exc)
    old = threading.Thread(target=old_preview)
    old.start()
    try:
        assert registered.wait(3)
        newer = harness.host.admit("preview-thread")
    finally:
        release.set()
        old.join(5)
    assert not old.is_alive() and len(failures) == 1
    assert isinstance(failures[0], TaskFlowError) and failures[0].code == "cancelled"
    assert harness.host._admissions["preview-thread"][0] == newer
    harness.state["request_text"] = "새 점검 요청"
    harness.revision[0] = "checkpoint-2"
    harness.host.begin_turn("preview-thread", harness.state["request_text"], newer)
    assert session.taskflow_preview("preview-thread").status == "needs_input"
    assert len(harness.remote.calls) == 2  # old registration never dispatched


@pytest.mark.parametrize("change", ["thread", "request", "actor", "legacy"])
def test_preview_requires_current_actor_and_exact_submitted_turn(harness, monkeypatch, change):
    if change == "thread":
        harness.state["thread_id"] = "another-thread"
    elif change == "request":
        harness.state["request_text"] = "이전 요청의 다른 내용"
    elif change == "actor":
        monkeypatch.setattr(approval, "current_actor_fresh", lambda: {"id": "other-user"})
    else:
        harness.host._turns.clear()
    with pytest.raises(TaskFlowError, match="unknown_request|access_denied"):
        session.taskflow_preview("preview-thread")
    assert not harness.remote.calls
