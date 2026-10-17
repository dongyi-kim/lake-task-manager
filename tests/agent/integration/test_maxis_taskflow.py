"""Exercise the coordinator with real contracts/guards and trusted fake adapters.

No socket, model, Jira writer or production activation is involved.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import threading

import pytest
from pydantic import ValidationError

from app.agent.maxis import ContextGuard, ContextPolicy
from app.agent.maxis.contracts import (
    CompactQueryPlanV1, DraftQuestionV1, QueryIntentV1, RemoteRoleResultV1,
)
from app.agent.maxis.taskflow import ExecutionBudget, TaskFlowCoordinator, TaskFlowError
from app.agent.maxis.taskflow_contracts import (
    TaskFlowPinV1, TaskFlowRequestV1, TaskFlowResultV1, evidence_digest,
)


@pytest.fixture
def request_pack():
    now = datetime.now(timezone.utc)
    pack = ContextGuard(ContextPolicy(
        allowed_scopes={"jira": ("DL",)},
        allowed_source_hosts={"jira": ("jira.example.local",)},
    ), b"test-pseudonym-key-32-bytes-long").build(
        request_id="taskflow-test", use_case="task_draft", query="진척 점검 초안",
        payload_schema={"type": "object", "properties": {}, "additionalProperties": False},
        user_identity="fixture-person", requested_at=now,
        knowledge_manifest_sha256="a" * 64,
        sources=[dict(source_id="src-1", kind="jira", canonical_id="DL-9001",
                      title="진척 점검", scope="DL",
                      url="https://jira.example.local/browse/DL-9001",
                      content="진척 점검 현황 확인 검토 완료", retrieved_at=now)],
    )
    pin = TaskFlowPinV1(profile_fingerprint="fixture", workflow_release="wf-1",
                       workflow_sha256="b" * 64, research_release="research-1",
                       research_sha256="c" * 64, knowledge_manifest_sha256="a" * 64)
    return TaskFlowRequestV1(request_id=pack.request_id, revision=0, pin=pin,
                             evidence=pack, evidence_sha256=evidence_digest(pack))


def result(request, status="completed", **updates):
    values = dict(request_id=request.request_id, revision=request.revision,
                  pin=request.pin, evidence_sha256=request.evidence_sha256, status=status)
    if status == "completed":
        values["draft_result"] = RemoteRoleResultV1.model_validate_json(json.dumps({
            "role_id": "work_architect", "status": "ok",
            "payload": {"contract": "work-draft.v1", "mode": "task",
                        "structure": "single_task", "items": [{
                            "summary": "진척 점검", "tier": "task", "issue_type": "Task",
                            "scope_in": ["현황 확인"], "dod": ["검토 완료"],
                            "source_ids": ["src-1"]}]},
            "claims": [{"claim_id": "claim-1", "kind": "inference",
                        "text": "진척 점검 현황 확인 검토 완료", "citation_ids": ["cite-1"]}],
            "citations": [{"citation_id": "cite-1", "source_id": "src-1"}],
            "payload_claims": [{"path": p, "claim_ids": ["claim-1"]} for p in
                               ["/items/0/summary", "/items/0/scope_in/0", "/items/0/dod/0"]],
            "workflow_version": "wf-1", "knowledge_version": "kb-1",
            "knowledge_manifest_sha256": "a" * 64,
            "knowledge_proof": "LTM_RELEASE_SENTINEL_V1=test",
        }))
    elif status == "needs_evidence":
        values.update(reason="insufficient_evidence", query_plan=CompactQueryPlanV1(
            reads=(QueryIntentV1(source="jira", subject="진척"),)))
    elif status == "needs_input":
        values.update(reason="input_required", questions=(DraftQuestionV1(
            question="점검할 범위는 무엇인가요?", kind="text", required_input=True,
            why_required="업무 범위를 확정해야 초안을 작성할 수 있습니다."),))
    else:
        values["reason"] = "insufficient_evidence"
    values.update(updates)
    return TaskFlowResultV1(**values)


class Ports:
    def __init__(self, statuses=("completed",)):
        self.statuses = iter(statuses)
        self.calls, self.reads, self.auth_checks, self.validations = [], [], 0, 0
        self.revoked = False
        self.release_changed = False
        self.on_dispatch = None
        self.on_read = None

    def authorize(self, request):
        self.auth_checks += 1
        if self.revoked:
            raise TaskFlowError("access_denied")

    def verify_release(self, request):
        if self.release_changed:
            raise TaskFlowError("release_changed")

    def dispatch(self, request, budget):
        budget.reserve(models=1)
        self.calls.append(request)
        if self.on_dispatch:
            return self.on_dispatch(request, budget)
        return result(request, next(self.statuses))

    def read(self, request, plan, budget):
        self.reads.append(plan)
        if self.on_read:
            return self.on_read(request)
        return request.evidence

    def validate_draft(self, request, value):
        self.validations += 1


def test_research_read_draft_uses_one_budget_and_current_revision(request_pack):
    ports = Ports(("needs_evidence", "completed"))
    runner = TaskFlowCoordinator(ports)
    completed = runner.run(request_pack)
    assert completed.status == "completed" and completed.revision == 1
    assert [r.revision for r in ports.calls] == [0, 1]
    assert len(ports.reads) == 1 and ports.validations == 1
    assert ports.auth_checks >= 8
    assert runner.run(request_pack) == completed
    assert len(ports.calls) == 2  # completed duplicate does not redispatch


@pytest.mark.parametrize("status", ["needs_input", "blocked"])
def test_recovery_is_not_an_executable_partial_draft(request_pack, status):
    outcome = TaskFlowCoordinator(Ports((status,))).run(request_pack)
    assert outcome.status == status and outcome.draft_result is None


def test_read_round_limit_is_global(request_pack):
    ports = Ports(("needs_evidence",) * 3)
    outcome = TaskFlowCoordinator(ports).run(request_pack)
    assert outcome.reason == "budget_exhausted" and len(ports.reads) == 2


@pytest.mark.parametrize("field,value", [
    ("revision", 2), ("evidence_sha256", "d" * 64), ("request_id", "another-request"),
])
def test_out_of_order_or_rebound_response_is_discarded(request_pack, field, value):
    ports = Ports()
    ports.on_dispatch = lambda req, budget: result(req).model_copy(update={field: value})
    assert TaskFlowCoordinator(ports).run(request_pack).reason == "invalid_response"


def test_existing_grounding_guard_cannot_be_bypassed_by_adapter(request_pack):
    ports = Ports()
    def fabricated(req, budget):
        value = result(req)
        value.draft_result.payload["items"][0]["summary"] = "존재하지 않는 결정"
        return value
    ports.on_dispatch = fabricated
    assert TaskFlowCoordinator(ports).run(request_pack).reason == "invalid_response"
    assert ports.validations == 0


@pytest.mark.parametrize("changes", [{"partial": True}, {"freshness": "stale"}])
def test_incomplete_or_stale_pack_cannot_complete(request_pack, changes):
    pack = request_pack.evidence.model_copy(update=changes)
    request = request_pack.model_copy(update={"evidence": pack, "evidence_sha256": evidence_digest(pack)})
    assert TaskFlowCoordinator(Ports()).run(request).reason == "incomplete_coverage"


def test_retrieval_cannot_change_principal_or_scope(request_pack):
    ports = Ports(("needs_evidence",))
    ports.on_read = lambda req: req.evidence.model_copy(update={"pseudonymous_user": "ltm_" + "0" * 16})
    assert TaskFlowCoordinator(ports).run(request_pack).reason == "access_denied"


def test_retrieval_rechecks_revocation_before_accepting_output(request_pack):
    ports = Ports(("needs_evidence",))
    def revoke(req):
        ports.revoked = True
        return req.evidence
    ports.on_read = revoke
    with pytest.raises(TaskFlowError, match="access_or_release_revoked"):
        TaskFlowCoordinator(ports).run(request_pack)
    assert len(ports.calls) == 1


def test_cached_completion_is_not_returned_after_revocation(request_pack):
    ports = Ports()
    runner = TaskFlowCoordinator(ports)
    runner.run(request_pack)
    ports.revoked = True
    with pytest.raises(TaskFlowError, match="access_or_release_revoked"):
        runner.snapshot(request_pack.request_id, request_pack.evidence.pseudonymous_user)


def test_deadline_applies_after_remote_call_and_across_rounds(request_pack):
    now = [0.0]
    ports = Ports()
    def late(req, budget):
        now[0] = 61.0
        return result(req)
    ports.on_dispatch = late
    assert TaskFlowCoordinator(ports, clock=lambda: now[0]).run(request_pack).reason == "budget_exhausted"


def test_atomic_budget_reservation_and_negative_amounts():
    budget = ExecutionBudget(60, lambda: 0)
    budget.reserve(models=8, tools=12)
    with pytest.raises(TaskFlowError, match="budget_exhausted"):
        budget.reserve(models=1)
    for value in (-1, True, 1.5):
        with pytest.raises(ValueError):
            budget.reserve(models=value)


def test_duplicate_and_cancel_while_remote_is_running(request_pack):
    started, release = threading.Event(), threading.Event()
    ports, outcomes = Ports(), []
    def wait(req, budget):
        started.set()
        assert release.wait(3)
        return result(req)
    ports.on_dispatch = wait
    runner = TaskFlowCoordinator(ports)
    thread = threading.Thread(target=lambda: outcomes.append(runner.run(request_pack)))
    thread.start()
    try:
        assert started.wait(3)
        assert runner.run(request_pack) is None
        runner.cancel(request_pack.request_id, request_pack.evidence.pseudonymous_user)
    finally:
        release.set()
        thread.join(3)
    assert len(ports.calls) == 1 and outcomes[0].reason == "cancelled"


def test_cross_user_snapshot_and_cancel_are_denied(request_pack):
    runner = TaskFlowCoordinator(Ports())
    runner.run(request_pack)
    for action in (runner.snapshot, runner.cancel):
        with pytest.raises(TaskFlowError, match="unknown_request"):
            action(request_pack.request_id, "ltm_" + "0" * 16)


def test_external_sources_and_non_ok_payload_are_rejected(request_pack):
    with pytest.raises(ValidationError):
        result(request_pack, "needs_evidence", query_plan=CompactQueryPlanV1(
            reads=(QueryIntentV1(source="web", subject="internal identifier"),)))
    with pytest.raises(ValidationError):
        result(request_pack, "blocked", draft_result=result(request_pack).draft_result)


def test_expired_id_is_not_reexecuted(request_pack):
    now = [0.0]
    ports = Ports()
    runner = TaskFlowCoordinator(ports, clock=lambda: now[0])
    runner.run(request_pack)
    now[0] = 1800
    with pytest.raises(TaskFlowError, match="request_expired"):
        runner.run(request_pack)
    assert len(ports.calls) == 1


def test_cancel_drops_cached_draft_and_evidence(request_pack):
    runner = TaskFlowCoordinator(Ports())
    runner.run(request_pack)
    runner.cancel(request_pack.request_id, request_pack.evidence.pseudonymous_user)
    assert request_pack.request_id not in runner._runs
    with pytest.raises(TaskFlowError, match="cancelled"):
        runner.snapshot(request_pack.request_id, request_pack.evidence.pseudonymous_user)
    with pytest.raises(TaskFlowError, match="cancelled"):
        runner.run(request_pack)


def test_expiry_reclaims_content_capacity_but_preserves_replay_tombstone(request_pack):
    now = [0.0]
    ports = Ports(("completed", "completed"))
    runner = TaskFlowCoordinator(ports, max_runs=1, clock=lambda: now[0])
    runner.run(request_pack)
    now[0] = 1800.0
    pack = request_pack.evidence.model_copy(update={"request_id": "taskflow-second"})
    request = request_pack.model_copy(update={"request_id": pack.request_id,
        "evidence": pack, "evidence_sha256": evidence_digest(pack)})
    assert runner.run(request).status == "completed"
    assert request_pack.request_id not in runner._runs
    with pytest.raises(TaskFlowError, match="request_expired"):
        runner.run(request_pack)


def test_revoked_content_is_purged_even_after_authority_is_restored(request_pack):
    ports = Ports()
    runner = TaskFlowCoordinator(ports)
    runner.run(request_pack)
    ports.revoked = True
    with pytest.raises(TaskFlowError, match="access_or_release_revoked"):
        runner.snapshot(request_pack.request_id, request_pack.evidence.pseudonymous_user)
    ports.revoked = False
    assert request_pack.request_id not in runner._runs
    with pytest.raises(TaskFlowError, match="access_or_release_revoked"):
        runner.run(request_pack)


def test_completion_cannot_hide_blocking_questions(request_pack):
    ports = Ports()
    def incomplete(req, budget):
        value = result(req)
        value.draft_result.payload["questions"] = [{
            "question": "진척 점검 범위", "kind": "text", "required_input": True,
            "why_required": "현황 확인"}]
        return value
    ports.on_dispatch = incomplete
    assert TaskFlowCoordinator(ports).run(request_pack).reason == "invalid_response"


def test_inner_workflow_version_must_match_pin(request_pack):
    ports = Ports()
    ports.on_dispatch = lambda req, budget: result(req, draft_result=
        result(req).draft_result.model_copy(update={"workflow_version": "another-release"}))
    assert TaskFlowCoordinator(ports).run(request_pack).reason == "invalid_response"


def test_public_request_and_release_labels_reject_credential_shapes(request_pack):
    for field in ("profile_fingerprint", "workflow_release", "research_release"):
        values = request_pack.pin.model_dump()
        values[field] = "app-" + "a" * 32
        with pytest.raises(ValueError):
            TaskFlowPinV1.model_validate(values)


@pytest.mark.parametrize("subject", ["<script>alert(1)</script>", "app-" + "x" * 32])
def test_rejected_query_text_never_reaches_local_reader(request_pack, subject):
    ports = Ports()
    ports.on_dispatch = lambda req, budget: result(req, "needs_evidence",
        query_plan=CompactQueryPlanV1(reads=(QueryIntentV1(source="jira", subject=subject),)))
    assert TaskFlowCoordinator(ports).run(request_pack).reason == "invalid_response"
    assert not ports.reads


@pytest.mark.parametrize("auth_check", [1, 4, 5])
def test_deadline_includes_final_authorization_and_display_check(request_pack, auth_check):
    now = [0.0]
    ports = Ports()
    original = ports.authorize
    def authorize(req):
        original(req)
        if ports.auth_checks == auth_check:
            now[0] = 61.0
    ports.authorize = authorize
    assert TaskFlowCoordinator(ports, clock=lambda: now[0]).run(request_pack).reason == "budget_exhausted"
