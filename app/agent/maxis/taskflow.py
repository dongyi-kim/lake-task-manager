"""Local continuation owner for a remotely orchestrated, read-only Task flow.

Ports are trusted application adapters, not HTTP request parameters. In particular
``verify_release`` must verify remote budget enforcement and immutable nested releases
before any dispatch. No production adapter is implicitly enabled by this module.
The coordinator never receives a Jira writer or an approval-token issuer.
"""

from __future__ import annotations

import threading
import time
import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable, Protocol

from app.agent.maxis.context_guard import validate_pack_safety
from app.agent.maxis.contracts import CompactQueryPlanV1, EvidencePackV1, WorkDraftV1
from app.agent.maxis.response_guard import ResponseGuard
from app.agent.maxis.taskflow_contracts import (
    TaskFlowRequestV1, TaskFlowResultV1, evidence_digest,
)


class TaskFlowError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass
class ExecutionBudget:
    """One shared ledger for every nested dispatch and additional read round."""

    deadline: float
    clock: Callable[[], float] = field(repr=False)
    model_remaining: int = 8
    tool_remaining: int = 12
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def check(self) -> None:
        if self.clock() >= self.deadline:
            raise TaskFlowError("budget_exhausted")

    def reserve(self, *, models: int = 0, tools: int = 0) -> None:
        """Adapters reserve before execution, including parser/model retries."""
        if type(models) is not int or type(tools) is not int or min(models, tools) < 0:
            raise ValueError("invalid budget reservation")
        with self._lock:
            self.check()
            if models > self.model_remaining or tools > self.tool_remaining:
                raise TaskFlowError("budget_exhausted")
            self.model_remaining -= models
            self.tool_remaining -= tools


class TaskFlowPorts(Protocol):
    """Authority stays in adapters; model output cannot choose scope or identity."""

    def authorize(self, request: TaskFlowRequestV1) -> None: ...
    def verify_release(self, request: TaskFlowRequestV1) -> None: ...
    def dispatch(self, request: TaskFlowRequestV1,
                 budget: ExecutionBudget) -> TaskFlowResultV1: ...
    def read(self, request: TaskFlowRequestV1, plan: CompactQueryPlanV1,
             budget: ExecutionBudget) -> EvidencePackV1: ...
    def validate_draft(self, request: TaskFlowRequestV1,
                       result: TaskFlowResultV1) -> None: ...


@dataclass
class _Run:
    initial_sha256: str
    principal: str
    current: TaskFlowRequestV1 | None
    binding: TaskFlowResultV1
    budget: ExecutionBudget
    created_at: float
    cancelled: bool = False
    running: bool = True
    result: TaskFlowResultV1 | None = None


@dataclass(frozen=True)
class _Tombstone:
    initial_sha256: str
    principal: str
    reason: str


class TaskFlowCoordinator:
    """Process-local, like the existing Session checkpoint lifetime.

    A duplicate while running returns None (in progress), never starts another run.
    Request IDs are scoped to a single pseudonymous principal; collisions are errors.
    Content expires on access/sweep after 30 minutes. Content-free replay tombstones
    survive until process restart; a separate bounded ID capacity fails closed.
    The host may call prune() while idle; this is not a background retention service.
    A reply to needs_input is a new request with freshly acquired evidence, not a
    continuation that resets the old budget or reuses a previous approval.
    """

    def __init__(self, ports: TaskFlowPorts, *, clock: Callable[[], float] = time.monotonic,
                 max_runs: int = 128, max_request_ids: int = 4096):
        if (type(max_runs) is not int or max_runs < 1
                or type(max_request_ids) is not int or max_request_ids < max_runs):
            raise ValueError("invalid run capacity")
        self._ports = ports
        self._clock = clock
        self._max_runs = max_runs
        self._max_request_ids = max_request_ids
        self._runs: dict[str, _Run] = {}
        self._tombstones: dict[str, _Tombstone] = {}
        self._lock = threading.RLock()

    def _check(self, run: _Run) -> TaskFlowRequestV1:
        with self._lock:
            if run.cancelled or run.current is None:
                raise TaskFlowError("cancelled")
            request = run.current
        run.budget.check()
        self._ports.authorize(request)
        self._ports.verify_release(request)
        run.budget.check()
        with self._lock:
            if run.cancelled:
                raise TaskFlowError("cancelled")
        return request

    @staticmethod
    def _stopped(run: _Run, code: str) -> TaskFlowResultV1:
        allowed = {"budget_exhausted", "access_denied", "release_changed",
                   "cancelled", "incomplete_coverage", "deployment_unverified"}
        return run.binding.model_copy(update={
            "reason": code if code in allowed else "invalid_response"})

    def _purge(self, run: _Run, reason: str) -> None:
        """Caller owns lock. Drop stored source/output content, retain replay guard."""
        self._tombstones[run.binding.request_id] = _Tombstone(
            run.initial_sha256, run.principal, reason)
        self._runs.pop(run.binding.request_id, None)
        run.cancelled, run.current, run.result = True, None, None

    def prune(self) -> None:
        with self._lock:
            for run in list(self._runs.values()):
                if self._clock() - run.created_at >= 1800:
                    self._purge(run, "request_expired")

    def run(self, request: TaskFlowRequestV1) -> TaskFlowResultV1 | None:
        started = self._clock()
        # Revalidation also rejects unsafe model_construct()/model_copy() inputs.
        request = TaskFlowRequestV1.model_validate_json(request.model_dump_json())
        if request.revision != 0:
            raise TaskFlowError("initial_revision_required")
        validate_pack_safety(request.evidence)
        initial_sha256 = hashlib.sha256(json.dumps(request.model_dump(mode="json"),
            sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        with self._lock:
            self.prune()
            tombstone = self._tombstones.get(request.request_id)
            if tombstone:
                if tombstone.initial_sha256 != initial_sha256:
                    raise TaskFlowError("request_id_collision")
                raise TaskFlowError(tombstone.reason)
            previous = self._runs.get(request.request_id)
            if previous:
                if previous.initial_sha256 != initial_sha256:
                    raise TaskFlowError("request_id_collision")
                return self._snapshot(previous)
            if len(self._runs) >= self._max_runs:
                raise TaskFlowError("session_capacity_reached")
            if len(self._runs) + len(self._tombstones) >= self._max_request_ids:
                raise TaskFlowError("request_id_capacity_reached")
            self._ports.authorize(request)
            binding = TaskFlowResultV1(request_id=request.request_id, revision=0,
                pin=request.pin, evidence_sha256=request.evidence_sha256,
                status="blocked", reason="cancelled")
            run = _Run(initial_sha256, request.evidence.pseudonymous_user, request,
                       binding, ExecutionBudget(started + 60, self._clock), started)
            self._runs[request.request_id] = run
        try:
            result = self._advance(run)
        except TaskFlowError as exc:
            result = self._stopped(run, exc.code)
        except Exception:
            # Never put raw transport errors, prompts, or rejected text in recovery.
            result = self._stopped(run, "invalid_response")
        with self._lock:
            if run.cancelled:
                return self._stopped(run, "cancelled")
            run.result, run.running = result, False
            return self._snapshot(run, completing=True)

    def _advance(self, run: _Run) -> TaskFlowResultV1:
        for _ in range(3):
            request = self._check(run)
            result = self._ports.dispatch(request, run.budget)
            result = TaskFlowResultV1.model_validate_json(result.model_dump_json())
            self._check(run)
            if (result.request_id != request.request_id
                    or result.revision != request.revision
                    or result.pin != request.pin
                    or result.evidence_sha256 != request.evidence_sha256):
                raise TaskFlowError("invalid_response")
            if result.status == "completed":
                pack = request.evidence
                if (pack.partial or pack.freshness != "fresh"
                        or any(s.partial or s.truncated for s in pack.sources)):
                    raise TaskFlowError("incomplete_coverage")
                ResponseGuard().validate_remote_role(
                    result.draft_result.model_dump_json(),
                    expected_role_id="work_architect", payload_model=WorkDraftV1,
                    evidence=pack,
                    expected_knowledge_manifest_sha256=request.pin.knowledge_manifest_sha256,
                )
                self._ports.validate_draft(request, result)
                self._check(run)
                return result
            if result.status != "needs_evidence":
                for question in result.questions:
                    ResponseGuard().validate(
                        question.model_dump_json(), type(question), request.evidence,
                    )
                return result
            if request.revision >= 2:
                raise TaskFlowError("budget_exhausted")
            self._check(run)
            assert result.query_plan is not None  # enforced by versioned schema
            ResponseGuard().validate(
                result.query_plan.model_dump_json(), CompactQueryPlanV1, request.evidence,
            )
            run.budget.reserve(tools=len(result.query_plan.reads))
            pack = self._ports.read(request, result.query_plan, run.budget)
            self._check(run)
            pack = EvidencePackV1.model_validate_json(pack.model_dump_json())
            validate_pack_safety(pack)
            old = request.evidence
            if (pack.scope != old.scope or pack.pseudonymous_user != old.pseudonymous_user
                    or pack.query != old.query or pack.use_case != old.use_case
                    or pack.payload_schema_sha256 != old.payload_schema_sha256):
                raise TaskFlowError("access_denied")
            with self._lock:
                if run.cancelled:
                    raise TaskFlowError("cancelled")
                run.current = TaskFlowRequestV1(
                    request_id=request.request_id, revision=request.revision + 1,
                    pin=request.pin, evidence=pack, evidence_sha256=evidence_digest(pack),
                )
                run.binding = run.binding.model_copy(update={
                    "revision": run.current.revision,
                    "evidence_sha256": run.current.evidence_sha256})
        raise TaskFlowError("budget_exhausted")

    def _snapshot(self, run: _Run, *, completing: bool = False) -> TaskFlowResultV1 | None:
        # Caller owns the lock through authorization and copying. A concurrent
        # cancel cannot return a cached draft after cancellation has completed.
        if self._clock() - run.created_at >= 1800:
            self._purge(run, "request_expired")
            raise TaskFlowError("request_expired")
        if run.cancelled or run.current is None:
            raise TaskFlowError("cancelled")
        try:
            self._ports.authorize(run.current)
            self._ports.verify_release(run.current)
        except Exception:
            self._purge(run, "access_or_release_revoked")
            raise TaskFlowError("access_or_release_revoked") from None
        if self._clock() - run.created_at >= 1800:
            self._purge(run, "request_expired")
            raise TaskFlowError("request_expired")
        if completing:
            try:
                run.budget.check()
            except TaskFlowError:
                run.result = self._stopped(run, "budget_exhausted")
        return run.result.model_copy(deep=True) if run.result else None

    def snapshot(self, request_id: str, principal: str) -> TaskFlowResultV1 | None:
        with self._lock:
            run = self._owned(request_id, principal)
            return self._snapshot(run)

    def cancel(self, request_id: str, principal: str) -> None:
        with self._lock:
            run = self._owned(request_id, principal)
            self._purge(run, "cancelled")
        # Local rejection is guaranteed. Stopping already dispatched remote work is
        # an adapter/server capability, not something this flag claims to guarantee.

    def _owned(self, request_id: str, principal: str) -> _Run:
        self.prune()
        tombstone = self._tombstones.get(request_id)
        if tombstone and tombstone.principal == principal:
            raise TaskFlowError(tombstone.reason)
        run = self._runs.get(request_id)
        if run is None or run.principal != principal:
            raise TaskFlowError("unknown_request")
        return run
