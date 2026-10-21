"""Session-owned, read-only TaskFlow preview integration.

The installed application deliberately has no runtime composition yet. A trusted
composition must supply actual release/ACL/budget verifiers, not an enabled flag.
This module never stages an approval, mutates a graph checkpoint, or calls a writer.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import threading
import time
from typing import Callable
import uuid

from app.agent import approval
from app.agent.maxis.context_guard import ContextGuard, ContextPolicy
from app.agent.maxis.contracts import CompactQueryPlanV1, EvidencePackV1, WorkDraftV1
from app.agent.maxis.taskflow import ExecutionBudget, TaskFlowCoordinator, TaskFlowError, TaskFlowPorts
from app.agent.maxis.taskflow_contracts import TaskFlowPinV1, TaskFlowRequestV1, evidence_digest


@dataclass(frozen=True)
class TaskFlowPreviewRuntime:
    """Internal composition only: never deserialized from a request or preferences."""

    pin: TaskFlowPinV1
    ports: TaskFlowPorts


def _current_policy() -> ContextPolicy:
    from app.agent.maxis.backend import _allowed_source_hosts
    from app.agent.tools._ctx import settings

    cfg = settings()
    return ContextPolicy(
        allowed_scopes={
            "jira": tuple(getattr(cfg, "search_jira_projects", None) or ()),
            "confluence": tuple(getattr(cfg, "search_confluence_spaces", None) or ()),
            "bitbucket": tuple(getattr(cfg, "search_bitbucket_projects", None) or ()),
        },
        allowed_source_hosts=_allowed_source_hosts(cfg),
        allow_http_urls=str(getattr(cfg, "jira_env", "")).lower() != "prod",
    )


class LocalTaskFlowReader:
    """Run existing scoped QueryRunner services; export only attested guarded rows.

    Reads execute individually to avoid QueryRunner's multi-source auto-materializer
    adding hidden tool calls. Exhaustive pagination remains QueryRunner-owned. Its
    incomplete/comment coverage and truncation markers cannot become complete here.
    The coordinator reserves logical reads; this is not a network-call cancellation
    or per-page hard deadline implementation.
    """

    def __init__(self, *, thread_id: str, actor_id: str, authorize: Callable,
                 policy: ContextPolicy):
        self.thread_id, self.actor_id = thread_id, actor_id
        self.authorize, self.policy = authorize, policy

    def read(self, request: TaskFlowRequestV1, plan: CompactQueryPlanV1,
             budget: ExecutionBudget) -> EvidencePackV1:
        from app.agent.maxis.backend import (
            _PSEUDONYM_KEY, _known_identity_values, _verified_evidence_sources,
        )
        from app.agent.tools._ctx import settings
        from app.agent.workflow.agents.query_runner import QueryRunner
        from app.agent.workflow.agents.query_specialist import _compile_compact_query_plan

        self.authorize(request)
        plan = CompactQueryPlanV1.model_validate_json(plan.model_dump_json())
        if not plan.reads or any(r.source not in {"jira", "confluence", "comments"}
                                 for r in plan.reads):
            raise TaskFlowError("access_denied")
        if "synthetic_only" in request.evidence.flags:
            # A fixed-fixture DEV envelope cannot authorize local business acquisition.
            raise TaskFlowError("deployment_unverified")
        sources = [dict(row.model_dump(), partial=row.partial or row.truncated)
                   for row in request.evidence.sources]
        identity_values = set()
        for read in plan.reads:
            self.authorize(request)
            budget.check()
            compiled = _compile_compact_query_plan({
                "reads": [read.model_dump(mode="json")], "uncertainty": [],
            })
            # No messages, derived research state or target selectors can cause an
            # unrequested read. The compiler owns ids/paging; tools own search scope.
            state = {"thread_id": self.thread_id, "intent": "ask", "messages": [],
                     "query_plan": compiled, "request_text": ""}
            acquired = QueryRunner().node()(state)
            self.authorize(request)
            budget.check()
            # The existing HMAC/provenance check rejects forged or changed rows.
            sources.extend(_verified_evidence_sources(acquired))
            identity_values.update(_known_identity_values(acquired, settings()))
        old = request.evidence
        policy = replace(self.policy,
                         allowed_context_keys=tuple(row.name for row in old.role_context))
        guarded = ContextGuard(policy, _PSEUDONYM_KEY).build(
            request_id=request.request_id, use_case=old.use_case, query=old.query,
            payload_schema=old.payload_schema(), user_identity=self.actor_id,
            sources=list({row["source_id"]: row for row in sources}.values()),
            facts=[row.model_dump() for row in old.facts],
            metrics=[row.model_dump() for row in old.metrics],
            role_context=[dict(context_id=row.context_id, name=row.name,
                               value=json.loads(row.content_json), observed_at=row.observed_at,
                               partial=row.partial) for row in old.role_context],
            identity_values=tuple(sorted(identity_values)), additional_flags=old.flags,
            requested_at=datetime.now(timezone.utc),
            knowledge_manifest_sha256=request.pin.knowledge_manifest_sha256,
        )
        self.authorize(request)
        budget.check()
        # An earlier partial snapshot cannot be upgraded by adding a second source.
        if old.partial and not guarded.partial:
            guarded = guarded.model_copy(update={"partial": True})
        return guarded


class _SessionPorts:
    def __init__(self, runtime, *, thread_id, actor, revision, current_revision, policy):
        self.remote, self.actor, self.revision = runtime.ports, actor, revision
        self.current_revision, self.policy = current_revision, policy
        self.cancelled = False
        self.reader = LocalTaskFlowReader(thread_id=thread_id, actor_id=actor["id"],
                                         authorize=self.authorize, policy=policy)

    def authorize(self, request):
        from app.agent.maxis.backend import _PSEUDONYM_KEY

        if self.cancelled:
            raise TaskFlowError("cancelled")
        if self.current_revision() != self.revision:
            raise TaskFlowError("cancelled")
        current = approval.current_actor_fresh()
        # Exact stable actor ID, not a user-supplied state id or display-name alias.
        if current["id"] != self.actor["id"] or _current_policy() != self.policy:
            raise TaskFlowError("access_denied")
        if request.evidence.pseudonymous_user != ContextGuard(
                self.policy, _PSEUDONYM_KEY).pseudonym(self.actor["id"]):
            raise TaskFlowError("access_denied")
        self.remote.authorize(request)

    def verify_release(self, request):
        self.remote.verify_release(request)

    def dispatch(self, request, budget):
        return self.remote.dispatch(request, budget)

    def read(self, request, plan, budget):
        return self.reader.read(request, plan, budget)

    def validate_draft(self, request, result):
        self.remote.validate_draft(request, result)


@dataclass
class _Preview:
    revision: str
    request: TaskFlowRequestV1
    coordinator: TaskFlowCoordinator
    ports: _SessionPorts
    created_at: float


class TaskFlowPreviewHost:
    """A bounded companion to graph checkpoints, never another approval checkpoint."""

    def __init__(self, runtime: TaskFlowPreviewRuntime | None = None, *,
                 clock=time.monotonic, max_sessions: int = 128):
        if type(max_sessions) is not int or max_sessions < 1:
            raise ValueError("invalid preview capacity")
        self.runtime, self.clock, self.max_sessions = runtime, clock, max_sessions
        self._previews: dict[str, _Preview] = {}
        self._turns: dict[str, tuple[str, str, str]] = {}
        self._admissions: dict[str, tuple[str, float]] = {}
        self._generation = 0
        self._lock = threading.RLock()

    def admit(self, thread_id: str) -> str | None:
        """Invalidate older producers before waiting for the graph turn lock."""
        if self.runtime is None:
            return None
        with self._lock:
            self.prune()
            self.cancel(thread_id)
            if len(self._admissions) >= self.max_sessions:
                return None
            self._generation += 1  # process-local monotonic identity, never wall-clock reuse
            token = str(self._generation)
            self._admissions[thread_id] = (token, self.clock())
            return token

    def begin_turn(self, thread_id: str, text: str, admission: str | None):
        """Only the current admitted producer can bind actor/request eligibility."""
        if self.runtime is None:
            return
        with self._lock:
            self.prune()
            current = self._admissions.get(thread_id)
            if not admission or current is None or current[0] != admission:
                return
            try:
                actor = approval.current_actor_fresh()
            except Exception:
                return  # no preview eligibility; ordinary local chat is unaffected
            self._turns[thread_id] = (actor["id"], hashlib.sha256(
                str(text).strip().encode("utf-8")).hexdigest(), admission)

    def prune(self):
        with self._lock:
            for tid, (_, created_at) in list(self._admissions.items()):
                if self.clock() - created_at >= 1800:
                    self.cancel(tid)

    def cancel(self, thread_id: str):
        with self._lock:
            self._admissions.pop(thread_id, None)
            self._turns.pop(thread_id, None)
            entry = self._previews.pop(thread_id, None)
            if entry is not None:
                entry.ports.cancelled = True
                try:
                    entry.coordinator.cancel(entry.request.request_id,
                                             entry.request.evidence.pseudonymous_user)
                except TaskFlowError:
                    pass

    @staticmethod
    def _snapshot(entry):
        try:
            return entry.coordinator.snapshot(entry.request.request_id,
                                              entry.request.evidence.pseudonymous_user)
        except TaskFlowError as exc:
            if exc.code != "unknown_request":
                raise
            # UI polling can arrive after host registration but before coordinator
            # registration. It must not cancel a valid just-starting run.
            entry.ports.authorize(entry.request)
            entry.ports.verify_release(entry.request)
            return None

    def run(self, thread_id: str, state: dict, revision: str,
            current_revision: Callable[[], str]):
        from app.agent.maxis.backend import _context_pack

        if self.runtime is None:
            raise TaskFlowError("deployment_unverified")
        if (not thread_id or state.get("thread_id") != thread_id
                or not revision or current_revision() != revision):
            raise TaskFlowError("unknown_request")
        with self._lock:
            self.prune()
            actor = approval.current_actor_fresh()
            bound_turn = self._turns.get(thread_id)
            admission = self._admissions.get(thread_id)
            text_digest = hashlib.sha256(str(state.get("request_text") or "").strip()
                                         .encode("utf-8")).hexdigest()
            if (bound_turn is None or admission is None or bound_turn[2] != admission[0]
                    or bound_turn[:2] != (actor["id"], text_digest)):
                raise TaskFlowError("access_denied")
            previous = self._previews.get(thread_id)
            if previous and previous.revision != revision:
                self.cancel(thread_id)
                raise TaskFlowError("cancelled")
            if previous:
                return self._snapshot(previous)
            if len(self._previews) >= self.max_sessions:
                raise TaskFlowError("session_capacity_reached")
            pin = self.runtime.pin
            if (state.get("runtime_profile_fingerprint") != pin.profile_fingerprint
                    or state.get("knowledge_manifest_sha256") != pin.knowledge_manifest_sha256
                    or state.get("runtime_profile") != "prod"):
                # DEV is fixed-fixture only. No local snapshot may become DEV data.
                raise TaskFlowError("deployment_unverified")
            policy = _current_policy()
            request_id = "taskflow_" + uuid.uuid4().hex
            # A checkpoint's old connector artifacts do not prove current ACL. Start
            # with this actor-bound user intent only; acquire all other evidence anew.
            pack = _context_pack("work_architect", {
                "thread_id": thread_id, "user_id": actor["id"],
                "correlation_id": request_id, "request_text": state["request_text"],
                "runtime_profile": state["runtime_profile"],
                "knowledge_manifest_sha256": pin.knowledge_manifest_sha256,
            }, [], WorkDraftV1.model_json_schema())
            request = TaskFlowRequestV1(request_id=request_id, revision=0, pin=pin,
                                       evidence=pack, evidence_sha256=evidence_digest(pack))
            ports = _SessionPorts(self.runtime, thread_id=thread_id, actor=actor,
                                  revision=revision, current_revision=current_revision,
                                  policy=policy)
            ports.authorize(request)
            ports.verify_release(request)  # fail before keeping content or dispatching
            entry = _Preview(revision, request, TaskFlowCoordinator(ports, clock=self.clock),
                             ports, self.clock())
            self._previews[thread_id] = entry
        try:
            return entry.coordinator.run(request)
        except Exception:
            # A newer admission can invalidate this entry before coordinator.run
            # registers it. Its early authorization failure must not erase the
            # newer token while that turn waits for the graph lock.
            with self._lock:
                if self._previews.get(thread_id) is entry:
                    self.cancel(thread_id)
            raise

    def status(self, thread_id: str) -> dict:
        """Content-free UI observation; no draft, sentinel or approval capability."""
        with self._lock:
            self.prune()
            entry = self._previews.get(thread_id)
            if entry is None:
                return {}
            try:
                result = self._snapshot(entry)
            except Exception:
                self.cancel(thread_id)
                return {}
            return {"mode": "preview", "status": result.status if result else "running",
                    "reason": result.reason if result else "",
                    "revision": result.revision if result else 0,
                    "qualityStatus": "not_evaluated", "approvalRequired": True}
