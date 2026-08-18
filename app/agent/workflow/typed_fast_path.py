"""Registered, typed authority contracts for deterministic LLM bypasses and repairs."""

from __future__ import annotations

from types import MappingProxyType
from typing import Annotated, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from app.agent.workflow.state import AgentState, note


TYPED_FAST_PATH_CONTRACT = "typed-fast-path.v1"
TYPED_CHECK_RESULT_CONTRACT = "typed-check-result.v1"
TYPED_REPAIR_BUDGET_CONTRACT = "typed-repair-budget.v1"

FastPathId = Literal[
    "auditor.machine_negative.v1",
    "portfolio.intermediate.v1",
    "request.question_answer_receipt.v1",
    "research.single_bounded_query",
    "result.execution_receipt.v1",
    "result.structure_tree.v1",
    "work.exact_single_ticket_update",
]
FastPathAuthority = Literal[
    "auditor.machine-check.v1",
    "portfolio_analyst.raw_tool_snapshot",
    "session.question-answer-receipt.v1",
    "request-plan.v1+query-plan.v1+query-results.v1",
    "request-plan.requested-effects.v1+continuation.v1",
    "action-executor.approved-dispatch.v1",
    "work_architect.structure_stage",
]
TypedCheckAuthority = Literal["auditor.machine-check.v1"]
RepairLane = Literal["semantic", "machine"]
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonNegativeInt = Annotated[int, Field(ge=0)]
SavedCallCount = Annotated[int, Field(ge=1, le=8)]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TypedFastPathSpec(_StrictFrozenModel):
    path_id: FastPathId
    authority: FastPathAuthority
    required_checks: frozenset[str]
    saved_calls: SavedCallCount = 1


_SPECS = (
    TypedFastPathSpec(
        path_id="auditor.machine_negative.v1",
        authority="auditor.machine-check.v1",
        required_checks=frozenset({
            "structured_result", "validation_complete", "negative_verdict",
            "structured_blockers", "semantic_obligations_absent",
        }),
    ),
    TypedFastPathSpec(
        path_id="portfolio.intermediate.v1",
        authority="portfolio_analyst.raw_tool_snapshot",
        required_checks=frozenset({
            "typed_material", "all_material_complete", "legacy_material_absent",
            "requested_targets_complete", "non_jql_request",
        }),
    ),
    TypedFastPathSpec(
        path_id="request.question_answer_receipt.v1",
        authority="session.question-answer-receipt.v1",
        required_checks=frozenset({
            "typed_projection", "current_plan_binding",
            "current_continuation_binding", "complete_answer_set",
            "eligible_field_projectors", "continuation_turn",
            "verified_work_context",
        }),
    ),
    TypedFastPathSpec(
        path_id="research.single_bounded_query",
        authority="request-plan.v1+query-plan.v1+query-results.v1",
        required_checks=frozenset({
            "ask_intent", "one_query_outcome", "query_plan_complete",
            "exact_result_binding", "ledger_result_shape", "bounded_without_omission",
        }),
    ),
    TypedFastPathSpec(
        path_id="result.execution_receipt.v1",
        authority="action-executor.approved-dispatch.v1",
        required_checks=frozenset({
            "typed_receipt", "current_thread", "current_capability",
            "exact_approval", "exact_outcomes", "safe_renderable",
        }),
    ),
    TypedFastPathSpec(
        path_id="result.structure_tree.v1",
        authority="work_architect.structure_stage",
        required_checks=frozenset({
            "tree", "stage_authority", "tree_seal", "render_safe",
        }),
    ),
    TypedFastPathSpec(
        path_id="work.exact_single_ticket_update",
        authority="request-plan.requested-effects.v1+continuation.v1",
        required_checks=frozenset({
            "typed_update_contract", "single_target", "single_outcome",
            "typed_effect_mapping", "supported_request_surface", "supported_scalar_set",
            "current_turn_boundary", "requested_effects_sealed",
        }),
    ),
)
TYPED_FAST_PATH_SPECS = MappingProxyType({spec.path_id: spec for spec in _SPECS})


class TypedFastPathDecision(_StrictFrozenModel):
    """Immutable measurement receipt derived only from a registered specification."""

    path_id: FastPathId
    authority: FastPathAuthority
    complete: bool
    missing: tuple[str, ...]
    saved_calls: NonNegativeInt

    def as_dict(self) -> dict:
        return {
            "contract": TYPED_FAST_PATH_CONTRACT,
            "id": self.path_id,
            "complete": self.complete,
            "authority": self.authority,
            "savedCalls": self.saved_calls,
            "missing": list(self.missing),
        }


class TypedCheckFinding(_StrictFrozenModel):
    """One bounded deterministic finding; unknown prose-shaped fields are rejected."""

    index: int | None = -1
    child_index: int | None = None
    item_id: str | None = None
    field: str | None = None
    source: str | None = None
    obligation_kind: str | None = None
    finding_kind: str | None = None
    check: str | None = None
    expected: str | None = None
    actual: str | None = None
    evidence: list[str] = Field(default_factory=list)
    fix: str | None = None
    message: str = Field(min_length=1, max_length=500)


class TypedCheckResult(_StrictFrozenModel):
    """Payload-bound output of a registered deterministic validator."""

    contract: Literal["typed-check-result.v1"] = TYPED_CHECK_RESULT_CONTRACT
    authority: TypedCheckAuthority
    payload_digest: Sha256Digest
    complete: bool
    ok: bool
    errors: list[TypedCheckFinding] = Field(default_factory=list)
    warnings: list[TypedCheckFinding] = Field(default_factory=list)
    text: str = Field(default="", max_length=12000)

    @model_validator(mode="after")
    def _consistent_verdict(self):
        if self.ok and (not self.complete or self.errors):
            raise ValueError("an incomplete or erroneous typed check cannot be ok")
        return self

    def as_dict(self) -> dict:
        return self.model_dump(mode="python")


class TypedRepairBudget(_StrictFrozenModel):
    """Independent semantic/machine repair counts plus a total loop ceiling."""

    contract: Literal["typed-repair-budget.v1"] = TYPED_REPAIR_BUDGET_CONTRACT
    semantic: NonNegativeInt
    machine: NonNegativeInt
    total: NonNegativeInt

    @model_validator(mode="after")
    def _total_matches_lanes(self):
        if self.total != self.semantic + self.machine:
            raise ValueError("typed repair total must equal semantic + machine")
        return self

    def as_dict(self) -> dict:
        return self.model_dump(mode="python")

    def advance(self, lane: RepairLane) -> "TypedRepairBudget":
        return TypedRepairBudget(
            semantic=self.semantic + int(lane == "semantic"),
            machine=self.machine + int(lane == "machine"),
            total=self.total + 1,
        )


_CHECK_RESULT_ADAPTER = TypeAdapter(TypedCheckResult)
_REPAIR_BUDGET_ADAPTER = TypeAdapter(TypedRepairBudget)
_CHECKS_ADAPTER = TypeAdapter(dict[str, bool])
_LANE_ADAPTER = TypeAdapter(RepairLane)
_COUNT_ADAPTER = TypeAdapter(NonNegativeInt)


def evaluate_typed_fast_path(
    path_id: str,
    *,
    checks: Mapping[str, bool],
) -> TypedFastPathDecision:
    """Evaluate the exact registered check set; callers cannot mint authority or savings."""
    spec = TYPED_FAST_PATH_SPECS.get(path_id)
    if spec is None:
        raise ValueError(f"unregistered typed fast path: {path_id!r}")
    try:
        normalized = _CHECKS_ADAPTER.validate_python(checks, strict=True)
    except ValidationError as exc:
        raise ValueError("typed fast path checks must be strict booleans") from exc
    if frozenset(normalized) != spec.required_checks:
        raise ValueError("typed fast path check set does not match its registered specification")
    missing = tuple(sorted(name for name, passed in normalized.items() if not passed))
    complete = not missing
    return TypedFastPathDecision(
        path_id=spec.path_id,
        authority=spec.authority,
        complete=complete,
        missing=missing,
        saved_calls=spec.saved_calls if complete else 0,
    )


def make_typed_check_result(
    *,
    authority: TypedCheckAuthority,
    payload_digest: str,
    complete: bool,
    ok: bool,
    errors=(),
    warnings=(),
    text: str = "",
) -> TypedCheckResult:
    """Build one strict common validator result using the registered authority literal."""
    return _CHECK_RESULT_ADAPTER.validate_python({
        "authority": authority,
        "payload_digest": payload_digest,
        "complete": complete,
        "ok": ok,
        "errors": list(errors or []),
        "warnings": list(warnings or []),
        "text": text,
    }, strict=True)


def parse_typed_check_result(
    value,
    *,
    authority: TypedCheckAuthority,
    payload_digest: str,
) -> TypedCheckResult | None:
    """Consume only an exact registered authority bound to the expected SHA-256 payload."""
    try:
        result = _CHECK_RESULT_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        return None
    if result.authority != authority or result.payload_digest != payload_digest:
        return None
    return result


def typed_repair_budget(state: Mapping) -> TypedRepairBudget | None:
    """Read the sidecar, projecting legacy ``revisions`` only when it is absent."""
    try:
        legacy = _COUNT_ADAPTER.validate_python(state.get("revisions") or 0, strict=True)
    except ValidationError:
        return None
    raw = state.get("repair_budget")
    if raw is None:
        return TypedRepairBudget(semantic=legacy, machine=0, total=legacy)
    try:
        budget = _REPAIR_BUDGET_ADAPTER.validate_python(raw, strict=True)
    except ValidationError:
        return None
    return budget if budget.semantic == legacy else None


def zero_typed_repair_budget() -> dict:
    """Return the canonical per-turn reset value for the repair sidecar."""
    return TypedRepairBudget(semantic=0, machine=0, total=0).as_dict()


def advance_typed_repair_budget(
    state: Mapping,
    lane: RepairLane,
) -> TypedRepairBudget | None:
    """Advance exactly one registered repair lane; malformed persisted state stays invalid."""
    budget = typed_repair_budget(state)
    try:
        checked_lane = _LANE_ADAPTER.validate_python(lane, strict=True)
    except ValidationError:
        return None
    return budget.advance(checked_lane) if budget else None


def typed_repair_retry_allowed(
    state: Mapping,
    lane: RepairLane,
    *,
    semantic_limit: int,
    machine_limit: int,
    total_limit: int,
) -> bool:
    """Fail closed unless the current lane and total counts are below strict limits."""
    budget = typed_repair_budget(state)
    try:
        checked_lane = _LANE_ADAPTER.validate_python(lane, strict=True)
        limits = tuple(_COUNT_ADAPTER.validate_python(value, strict=True)
                       for value in (semantic_limit, machine_limit, total_limit))
    except ValidationError:
        return False
    if budget is None:
        return False
    lane_count = budget.semantic if checked_lane == "semantic" else budget.machine
    lane_limit = limits[0] if checked_lane == "semantic" else limits[1]
    return lane_count < lane_limit and budget.total < limits[2]


def typed_fast_path_note(
    state: AgentState,
    node: str,
    text: str,
    decision: TypedFastPathDecision,
) -> list[dict]:
    """Return one normal trace row with a machine-readable fast-path sidecar."""
    row = note(state, node, text)[0]
    row["fastPath"] = decision.as_dict()
    return [row]
