"""Deterministic final-effect projection shared by review, approval and rendering.

This module intentionally contains no semantic review and no side effects.  It projects the
typed continuation authority over mutable workflow containers and describes the one effect that
could be staged.  Keeping this boundary outside ``Auditor`` prevents the approval contract from
becoming another agent-specific rule pile.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.agent.workflow.contracts import RequestedUpdateEffect
from app.agent.workflow.state import AgentState, Node


WRITE_ACTIONS = {"create", "comment", "update", "mixed"}
PENDING_RATIONALE_CONTRACT = "pending-rationale.v1"
UPDATE_EFFECT_ACTIONS = {
    "transition_ticket", "link_tickets", "update_ticket", "update_tickets",
}
UPDATE_SCALAR_FIELDS = frozenset({
    "assignee", "duedate", "priority", "summary",
})


@dataclass(frozen=True)
class FinalEffect:
    """Small immutable description of the only mutation at the final boundary."""

    kind: str
    actions: tuple[str, ...] = ()
    target_count: int = 0

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "actions": list(self.actions),
            "target_count": self.target_count,
        }


@dataclass(frozen=True)
class UserFieldLock:
    """One exact user-owned field captured before mutable assignment merging."""

    index: int
    child_index: int | None
    field: str
    value: str


@dataclass(frozen=True, order=True)
class RequestedEffect:
    """One immutable user-requested field mutation."""

    target: str
    field: str
    value: str

    def as_dict(self) -> dict:
        return {"target": self.target, "field": self.field, "value": self.value}


@dataclass(frozen=True)
class PendingDecision:
    """Typed payload facts used to rebuild one user-visible approval rationale."""

    kind: str
    targets: tuple[str, ...] = ()
    details: tuple[str, ...] = ()


_KEY = re.compile(r"(?<![A-Z0-9-])([A-Z][A-Z0-9]{1,9}-\d+)(?![A-Z0-9-])", re.I)
_DATE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_PRIORITY = re.compile(r"(?<![0-9A-Za-z])P([0-4])(?:-[A-Za-z]+)?(?![0-9A-Za-z])", re.I)
_ACCOUNT = re.compile(
    r"(?<![0-9A-Za-z_.-])((?:[A-Za-z][A-Za-z0-9_-]*\.)+[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)"
    r"(?![0-9A-Za-z_.-])",
)
_PRIORITY_NAMES = {
    "0": "P0-Blocker", "1": "P1-Critical", "2": "P2-Major",
    "3": "P3-Minor", "4": "P4-Trivial",
}
_AMBIGUOUS_UPDATE = re.compile(
    r"(?:아니라|아닌|말고|잘못|하지\s*마|취소|제외|"
    r"\bnot\b|\binstead\b|\bexcept\b|\bcancel\b)", re.I,
)

_FIELD_LABELS = {
    "summary": "제목",
    "description": "본문",
    "assignee": "담당자",
    "priority": "우선순위",
    "duedate": "마감",
    "labels": "라벨",
    "components": "컴포넌트",
}
_FIELD_ORDER = {
    field: index for index, field in enumerate(
        ("summary", "description", "assignee", "priority", "duedate",
         "labels", "components")
    )
}


def _canonical_value(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        values = sorted(str(row).strip() for row in value if str(row).strip())
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    return str(value if value is not None else "").strip()


def _display_value(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        text = ", ".join(str(row).strip() for row in value if str(row).strip())
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    else:
        text = str(value if value is not None else "").strip()
    return text if len(text) <= 160 else text[:157].rstrip() + "..."


def derive_pending_decision(
        *, draft: dict | None = None,
        change_plan: dict | None = None) -> PendingDecision:
    """Derive one immutable rationale decision from the current actionable container.

    This is deliberately payload-only.  In particular, ``draft.rationale`` and
    ``change_plan.why`` are never inputs, because they are the fields this projection is
    replacing and may describe a superseded revision.
    """
    plan = change_plan if isinstance(change_plan, dict) else {}
    targets = tuple(dict.fromkeys(
        str(value or "").strip().upper()
        for value in [*(plan.get("keys") or []), plan.get("key")]
        if str(value or "").strip()
    ))
    changes = plan.get("changes") if isinstance(plan.get("changes"), dict) else {}
    comments = [
        row for row in (plan.get("comments") or [])
        if isinstance(row, dict) and str(row.get("body") or "").strip()
    ]
    has_comment = bool(str(plan.get("comment") or "").strip())
    comment_count = len(comments) or (len(targets) if has_comment and plan.get("keys")
                                      else int(has_comment))
    transition = plan.get("transition") if isinstance(plan.get("transition"), dict) else {}
    link = plan.get("link") if isinstance(plan.get("link"), dict) else {}
    if targets and (changes or comment_count or transition or link):
        details = [f"{_FIELD_LABELS.get(str(field), field)}: "
                   f"{_display_value(value) or '비움'}"
                   for field, value in sorted(
                       changes.items(),
                       key=lambda row: (_FIELD_ORDER.get(str(row[0]), len(_FIELD_ORDER)),
                                        str(row[0])),
                   )]
        transition_name = str(transition.get("name") or transition.get("to")
                              or transition.get("id") or "").strip()
        other = str(link.get("other") or "").strip().upper()
        link_text = " ".join(filter(None, (
            str(link.get("relation") or "Relates").strip(), other,
        ))) if other else ""
        details.extend(value for value in (
            f"상태: {transition_name}" if transition_name else "",
            f"링크: {link_text}" if link_text else "",
            f"댓글 {comment_count}건" if comment_count else "",
        ) if value)
        kind = "comment" if comment_count and not changes and not transition_name \
            and not link_text else "update"
        if kind == "comment":
            details.append("필드·상태 변경 없음")
        return PendingDecision(kind=kind, targets=targets, details=tuple(details))

    current_draft = draft if isinstance(draft, dict) else {}
    roots = [row for row in (current_draft.get("items") or []) if isinstance(row, dict)]
    if not roots:
        return PendingDecision(kind="none")
    counts: dict[str, int] = {}
    parents: list[str] = []
    due_values: list[str] = []
    priority_values: list[str] = []
    top_level_count = 0
    child_count = 0
    for item in roots:
        issue_type = str(item.get("type") or item.get("issue_type") or "Task").strip()
        counts[issue_type] = counts.get(issue_type, 0) + 1
        parent = str(item.get("parent") or item.get("epic") or "").strip().upper()
        if parent:
            parents.append(parent)
        else:
            top_level_count += 1
        due = str(item.get("duedate") or "").strip()
        priority = str(item.get("priority") or "").strip()
        if due:
            due_values.append(due)
        if priority:
            priority_values.append(priority)
        child_count += len([child for child in (item.get("children") or [])
                           if isinstance(child, dict)])
    if child_count:
        counts["Sub-Task"] = counts.get("Sub-Task", 0) + child_count
    details = [f"{issue_type} {count}건" for issue_type, count in counts.items() if count]
    details.extend(value for value in (
        ("상위: " + ", ".join(dict.fromkeys(parents))) if parents else "",
        f"최상위 {top_level_count}건" if top_level_count else "",
        ("마감: " + ", ".join(dict.fromkeys(due_values))) if due_values else "",
        ("우선순위: " + ", ".join(dict.fromkeys(priority_values)))
        if priority_values else "",
        ("구조: " + str(current_draft.get("structure") or "").strip())
        if str(current_draft.get("structure") or "").strip() else "",
    ) if value)
    return PendingDecision(kind="create", details=tuple(details))


def project_pending_rationale(
        *, draft: dict | None = None,
        change_plan: dict | None = None) -> str:
    """Render the current typed decision without retaining superseded free-form prose."""
    decision = derive_pending_decision(draft=draft, change_plan=change_plan)
    if decision.kind == "none":
        return ""
    if decision.kind in {"update", "comment"}:
        target = ", ".join(decision.targets)
        label = "댓글 초안" if decision.kind == "comment" else "변경 초안"
        return f"{target} {label}" + (
            f" — {', '.join(decision.details)}" if decision.details else ""
        )
    return "생성 초안" + (
        f" — {', '.join(decision.details)}" if decision.details else ""
    )


def _literal_requested_values(text: str) -> dict[str, str]:
    """Parse only exact scalar leaves; semantic intent remains upstream.

    This is deliberately a small field codec, not a case classifier. It accepts values only
    when the corresponding field is named in the same bounded instruction.
    """
    source = " ".join(str(text or "").split())
    values: dict[str, str] = {}
    priorities = list(_PRIORITY.finditer(source))
    if len(priorities) == 1 and re.search(
            r"우선순위|(?<![A-Za-z])priority(?![A-Za-z])", source, re.I):
        values["priority"] = _PRIORITY_NAMES[priorities[0].group(1)]
    dates = list(_DATE.finditer(source))
    if len(dates) == 1 and re.search(r"마감|기한|due(?:date)?|deadline", source, re.I):
        values["duedate"] = dates[0].group(1)
    if re.search(r"담당|배정|할당|assignee|owner", source, re.I):
        accounts = list(_ACCOUNT.finditer(source))
        unassigned = list(re.finditer(
            r"미\s*할당|담당(?:자)?\s*(?:없음|없이)|unassigned", source, re.I,
        ))
        if len(accounts) == 1 and not unassigned:
            values["assignee"] = accounts[0].group(1).casefold()
        elif len(unassigned) == 1 and not accounts:
            values["assignee"] = ""
    if re.search(r"상위|부모|parent|Epic|에픽|아래", source, re.I):
        parent = _KEY.search(source)
        if parent:
            values["parent"] = parent.group(1).upper()
        elif re.search(r"최상위|top[- ]?level|parent\s*(?:none|없음)", source, re.I):
            values["parent"] = ""
    summaries = list(re.finditer(
        r"(?:제목|summary|title)(?:만|을|를)?\s*(?:은|는|:)?\s*"
        r"['\"“‘]([^'\"”’\n]{2,240})['\"”’]",
        source, re.I,
    ))
    if len(summaries) == 1:
        values["summary"] = summaries[0].group(1).strip()
    return values


def _current_message_digest(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _canonical_update_literal(field: str, literal: str) -> str:
    """Canonicalize one exact value span without interpreting update intent."""
    value = str(literal or "").strip()
    if field == "priority":
        match = _PRIORITY.fullmatch(value)
        return _PRIORITY_NAMES[match.group(1)] if match else ""
    if field == "duedate":
        return value if _DATE.fullmatch(value) else ""
    if field == "summary":
        return value
    return ""


def issue_requested_update_effects(value, allowed_targets, current_text: str) -> list[dict]:
    """Ground model-proposed rows to one unambiguous current-turn literal mapping.

    The model proposes the semantic destination, but runtime issues the persisted envelope:
    every row must reproduce an exact raw value span and the complete unique scalar mapping.
    The server-owned digest prevents a valid-looking row from being replayed on another turn.
    """
    if not isinstance(value, list) or not value or len(value) > 3:
        return []
    text = str(current_text or "")
    targets = {
        str(target or "").strip().upper() for target in allowed_targets or []
        if _KEY.fullmatch(str(target or "").strip())
    }
    if not targets or _AMBIGUOUS_UPDATE.search(text):
        return []
    digest = _current_message_digest(text)
    rows: list[RequestedUpdateEffect] = []
    try:
        for raw in value:
            if (not isinstance(raw, dict) or "source_digest" in raw
                    or set(raw) != {"target", "field", "value", "literal"}):
                return []
            row = RequestedUpdateEffect.model_validate({**raw, "source_digest": digest})
            if (row.literal not in text
                    or _canonical_update_literal(row.field, row.literal) != row.value):
                return []
            rows.append(row)
    except Exception:
        return []
    identities = [(row.target, row.field) for row in rows]
    decoded = {
        field: value for field, value in _literal_requested_values(text).items()
        if field in {"priority", "duedate", "summary"}
    }
    proposed = {row.field: row.value for row in rows}
    if (len(identities) != len(set(identities)) or {row.target for row in rows} != targets
            or not decoded or proposed != decoded):
        return []
    return [row.model_dump() for row in rows]


def _requested_effect_digest(effects: list[dict]) -> str:
    encoded = json.dumps(
        effects, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _effect_snapshot(effects: list[RequestedEffect]) -> dict:
    rows = [effect.as_dict() for effect in sorted(set(effects))]
    return {
        "contract": "requested-effects.v1",
        "effects": rows,
        "digest": _requested_effect_digest(rows),
    }


def _create_requested_effects(state: AgentState, draft: dict) -> list[RequestedEffect]:
    from app.agent.workflow.anchors import (
        requested_outcome_contract,
        scoped_continuation_decisions,
        work_item_id,
    )

    contract = requested_outcome_contract(state)
    contract_id = str(contract.get("id") or "")
    outcomes = {
        str(row.get("id") or ""): row
        for row in (contract.get("outcomes") or []) if str(row.get("id") or "")
    }
    scoped = scoped_continuation_decisions(state)
    roots = [row for row in (draft.get("items") or []) if isinstance(row, dict)]
    effects: list[RequestedEffect] = []
    global_values = (_literal_requested_values(
        str((state.get("continuation_contract") or {}).get("root_request") or ""))
        if len(outcomes) == 1 else {})
    refinement = state.get("request_refinement") or {}
    if isinstance(refinement, dict) and str(refinement.get("duedate") or "").strip():
        global_values["duedate"] = str(refinement["duedate"]).strip()
    resolved_slots = [
        row for row in (draft.get("resolved_slots") or [])
        if isinstance(row, dict) and row.get("contract") == "resolved-slot.v1"
        and row.get("field") == "parent" and row.get("status") == "resolved"
    ]
    for item in roots:
        refs = list(dict.fromkeys(
            str(value or "").strip() for value in (item.get("outcome_refs") or [])
            if str(value or "").strip() in outcomes
        ))
        if len(refs) != 1:
            continue
        outcome_ref = refs[0]
        target = str(item.get("item_id") or work_item_id(contract_id, outcome_ref))
        values = dict(global_values)
        values.update(_literal_requested_values(
            str(outcomes[outcome_ref].get("instruction") or "")))
        decisions = scoped.get(outcome_ref) or {}
        parent = decisions.get("parent") or {}
        if parent:
            literal = str(parent.get("value") or "")
            keys = [match.group(1).upper() for match in _KEY.finditer(literal)]
            values["parent"] = (keys[0] if len(set(keys)) == 1 else ""
                                if re.search(r"최상위|top[- ]?level", literal, re.I)
                                else values.get("parent", ""))
        assignment = decisions.get("assignee") or {}
        if assignment:
            literal = str(assignment.get("value") or "")
            account = _ACCOUNT.search(literal)
            if account:
                values["assignee"] = account.group(1).casefold()
            elif re.search(r"미\s*할당|unassigned", literal, re.I):
                values["assignee"] = ""
        item_id = str(item.get("item_id") or "")
        resolved_parent = next((
            row for row in resolved_slots
            if ((item_id and str(row.get("item_id") or "") == item_id)
                or str(row.get("outcome_id") or "") == outcome_ref)
        ), None)
        if resolved_parent is not None:
            values["parent"] = str(resolved_parent.get("value") or "").strip().upper()
        for field, value in values.items():
            effects.append(RequestedEffect(target, field, _canonical_value(value)))
    return effects


def requested_update_effect_authority(
        state: AgentState) -> tuple[list[RequestedEffect], str]:
    """Verify the runtime-issued target→field→value envelope against its source turn."""
    plan = state.get("request_plan") or {}
    if not isinstance(plan, dict) or "requested_effects" not in plan:
        return [], "missing"
    raw_rows = plan.get("requested_effects")
    if not isinstance(raw_rows, list) or not raw_rows or len(raw_rows) > 3:
        return [], "missing" if raw_rows == [] else "invalid_typed_effects"
    contract = state.get("continuation_contract") or {}
    if (not isinstance(contract, dict) or contract.get("version") != "continuation.v1"
            or contract.get("action") not in {"update", "mixed"}):
        return [], "invalid_typed_effects"
    targets = {
        str(value or "").strip().upper() for value in contract.get("target_keys") or []
        if _KEY.fullmatch(str(value or "").strip())
    }
    source = str(contract.get("root_request") or "")
    if not source or _AMBIGUOUS_UPDATE.search(source):
        return [], "unverified_current_literal"
    try:
        typed = [RequestedUpdateEffect.model_validate(row) for row in raw_rows]
    except Exception:
        return [], "invalid_typed_effects"
    identities = [(row.target, row.field) for row in typed]
    if (not targets or len(identities) != len(set(identities))
            or {row.target for row in typed} != targets):
        return [], "invalid_typed_effects"
    digest = _current_message_digest(source)
    decoded = {
        field: value for field, value in _literal_requested_values(source).items()
        if field in {"priority", "duedate", "summary"}
    }
    proposed = {row.field: row.value for row in typed}
    if (not decoded or proposed != decoded
            or any(row.source_digest != digest or row.literal not in source
                   or _canonical_update_literal(row.field, row.literal) != row.value
                   for row in typed)):
        return [], "unverified_current_literal"
    return [RequestedEffect(row.target, row.field, row.value) for row in typed], ""


def _legacy_update_scalar_authority(
        state: AgentState) -> tuple[list[str], dict[str, str], str]:
    """Compatibility codec for semantic updates; never grants fast-path authority."""
    contract = state.get("continuation_contract") or {}
    if (not isinstance(contract, dict)
            or contract.get("version") != "continuation.v1"
            or contract.get("action") not in {"update", "mixed"}):
        return [], {}, ""
    text = str(contract.get("root_request") or "")
    targets = list(dict.fromkeys(
        str(value or "").strip().upper()
        for value in (contract.get("target_keys") or [])
        if _KEY.fullmatch(str(value or "").strip())
    ))
    return targets, _literal_requested_values(text), text


def materialize_requested_update_effects(state: AgentState, plan: dict | None) -> dict:
    """Compile RequestPlan effects, with a non-fast legacy codec for old checkpoints."""
    typed, typed_error = requested_update_effect_authority(state)
    raw_typed = isinstance(state.get("request_plan"), dict) \
        and "requested_effects" in state["request_plan"]
    if raw_typed and typed_error not in {"", "missing"}:
        materialized = dict(plan or {})
        materialized.pop("effect_contract", None)
        materialized.pop("requested_effects", None)
        materialized["requested_effects_error"] = {
            "contract": "requested-effects-error.v1", "kind": typed_error,
        }
        return materialized
    if typed:
        targets = list(dict.fromkeys(effect.target for effect in typed))
        per_target = {
            target: {effect.field: effect.value for effect in typed if effect.target == target}
            for target in targets
        }
        if len(targets) > 1 and len({_canonical_value(value)
                                     for value in per_target.values()}) != 1:
            materialized = dict(plan or {})
            materialized.pop("effect_contract", None)
            materialized.pop("requested_effects", None)
            materialized["requested_effects_error"] = {
                "contract": "requested-effects-error.v1",
                "kind": "per_target_plan_required", "targets": targets,
            }
            return materialized
        values = per_target[targets[0]]
    else:
        targets, decoded, legacy_text = _legacy_update_scalar_authority(state)
        values = {field: value for field, value in decoded.items()
                  if field in UPDATE_SCALAR_FIELDS}
        if len(targets) > 1 and not values:
            candidate_fields = []
            if (_PRIORITY.search(legacy_text)
                    and re.search(r"우선순위|\bpriority\b", legacy_text, re.I)):
                candidate_fields.append("priority")
            if (_DATE.search(legacy_text)
                    and re.search(r"마감|기한|due(?:date)?|deadline", legacy_text, re.I)):
                candidate_fields.append("duedate")
            if candidate_fields:
                materialized = dict(plan or {})
                materialized["requested_effects_error"] = {
                    "contract": "requested-effects-error.v1",
                    "kind": "per_target_mapping_required", "targets": targets,
                    "fields": candidate_fields,
                }
                return materialized
    if not targets or not values:
        return plan if isinstance(plan, dict) else {}

    # The current continuation envelope owns the target set but does not yet carry a
    # target→field→value mapping.  Applying one scalar parse to every target would turn
    # ``A due X, B due Y`` into a Cartesian update using X.  Until Request Architect emits
    # typed per-target effects, multi-target scalar requests must remain fail-closed.
    if len(targets) > 1 and not typed:
        materialized = dict(plan or {})
        materialized.pop("effect_contract", None)
        materialized.pop("requested_effects", None)
        materialized["requested_effects_error"] = {
            "contract": "requested-effects-error.v1",
            "kind": "per_target_mapping_required",
            "targets": targets,
            "fields": sorted(values),
        }
        return materialized

    materialized = dict(plan or {})
    materialized.pop("requested_effects_error", None)
    if len(targets) == 1:
        materialized["key"] = targets[0]
        materialized.pop("keys", None)
    else:
        materialized["keys"] = targets
        materialized.pop("key", None)
    changes = dict(materialized.get("changes") or {})
    for field, value in values.items():
        changes[field] = value
    materialized["changes"] = changes
    materialized.setdefault("comment", "")
    materialized.setdefault("why", "typed requested scalar fields")
    snapshot = _effect_snapshot(typed or [
        RequestedEffect(target, field, _canonical_value(value))
        for target in targets for field, value in values.items()])
    materialized["effect_contract"] = "requested-effects.v1"
    materialized["requested_effects"] = snapshot
    return materialized


def _update_requested_effects(state: AgentState, plan: dict) -> list[RequestedEffect]:
    typed, _typed_error = requested_update_effect_authority(state)
    if typed:
        targets = list(dict.fromkeys(effect.target for effect in typed))
        values = {(effect.target, effect.field): effect.value for effect in typed}
        text = str((state.get("continuation_contract") or {}).get("root_request") or "")
    else:
        targets, decoded, text = _legacy_update_scalar_authority(state)
        if len(targets) > 1 and decoded:
            return []
        values = {(target, field): value for target in targets
                  for field, value in decoded.items()}
    changes = plan.get("changes") if isinstance(plan.get("changes"), dict) else {}
    # Exact list/body values may already have been compiled by Work even when their literal
    # grammar is not scalar. Seal only fields explicitly named by the immutable request.
    aliases = {
        "labels": r"라벨|labels?", "components": r"컴포넌트|components?",
        "description": r"본문|description", "summary": r"제목|summary|title",
    }
    for field, pattern in aliases.items():
        if field in changes and re.search(pattern, text, re.I):
            for target in targets:
                values.setdefault((target, field), _canonical_value(changes[field]))
    if not targets:
        targets = [
            str(value or "").strip().upper()
            for value in [*(plan.get("keys") or []), plan.get("key")]
            if _KEY.fullmatch(str(value or "").strip())
        ]
    return [RequestedEffect(target, field, _canonical_value(value))
            for (target, field), value in values.items()]


def derive_requested_effect_contract(
        state: AgentState, *, draft: dict | None = None,
        change_plan: dict | None = None) -> dict:
    """Derive the exact immutable effects from typed outcomes and scalar literals."""
    draft = draft if isinstance(draft, dict) else (state.get("draft") or {})
    plan = change_plan if isinstance(change_plan, dict) else (state.get("change_plan") or {})
    action = continuation_action(state)
    effects = (_create_requested_effects(state, draft)
               if action == "create" else _update_requested_effects(state, plan)
               if action in {"update", "mixed"} else [])
    return _effect_snapshot(effects)


def seal_requested_effect_contract(
        state: AgentState, *, draft: dict | None = None,
        change_plan: dict | None = None, force_refresh: bool = False) -> dict:
    """Seal effects at Work's output boundary and preserve the prior seal on repair."""
    target_draft = draft if isinstance(draft, dict) else (state.get("draft") or {})
    target_plan = (change_plan if isinstance(change_plan, dict)
                   else (state.get("change_plan") or {}))
    action = continuation_action(state)
    container = target_draft if action == "create" else target_plan
    if not isinstance(container, dict):
        return {}
    effect_error = container.get("requested_effects_error")
    if (isinstance(effect_error, dict)
            and effect_error.get("contract") == "requested-effects-error.v1"):
        container.pop("effect_contract", None)
        container.pop("requested_effects", None)
        return {}
    prior_container = ((state.get("draft") or {}) if action == "create"
                       else (state.get("change_plan") or {}))
    prior = prior_container.get("requested_effects") if isinstance(prior_container, dict) else None
    current = container.get("requested_effects") if isinstance(container, dict) else None
    if (not force_refresh and isinstance(current, dict)
            and current.get("contract") == "requested-effects.v1"
            and current.get("digest") == _requested_effect_digest(current.get("effects") or [])):
        # The materializer already decoded the frozen request. Reuse the canonical snapshot
        # instead of parsing the same authority again at Work's seal boundary.
        snapshot = json.loads(json.dumps(current, ensure_ascii=False))
    elif (not force_refresh and isinstance(prior, dict)
            and prior.get("contract") == "requested-effects.v1"
            and prior.get("digest") == _requested_effect_digest(prior.get("effects") or [])):
        snapshot = json.loads(json.dumps(prior, ensure_ascii=False))
    else:
        snapshot = derive_requested_effect_contract(
            state, draft=target_draft, change_plan=target_plan,
        )
    if snapshot.get("effects"):
        container["effect_contract"] = "requested-effects.v1"
        container["requested_effects"] = snapshot
    else:
        container.pop("effect_contract", None)
        container.pop("requested_effects", None)
    return snapshot


def _actual_effect_value(state: AgentState, effect: dict) -> tuple[bool, str]:
    target = str(effect.get("target") or "")
    field = str(effect.get("field") or "")
    view = project_final_authority_state(state)
    if continuation_action(view) == "create":
        item = next((row for row in ((view.get("draft") or {}).get("items") or [])
                     if isinstance(row, dict) and str(row.get("item_id") or "") == target), None)
        if not item:
            return False, ""
        if field == "parent":
            value = item.get("parent") or item.get("epic") or ""
        else:
            value = item.get(field)
        requested_value = _canonical_value(effect.get("value"))
        present = (
            field in item
            or (field == "parent" and ("parent" in item or "epic" in item or value == ""))
            or (field == "assignee" and requested_value == "" and not item.get("assignee"))
        )
        return present, _canonical_value(value)
    plan = view.get("change_plan") or {}
    targets = {
        str(value or "").strip().upper()
        for value in [*(plan.get("keys") or []), plan.get("key")]
        if str(value or "").strip()
    }
    changes = plan.get("changes") if isinstance(plan.get("changes"), dict) else {}
    return target in targets and field in changes, _canonical_value(changes.get(field))


def validate_requested_effect_contract(state: AgentState) -> list[dict]:
    """Require every immutable tuple at the final projected payload boundary."""
    view = project_final_authority_state(state)
    action = continuation_action(view)
    container = ((view.get("draft") or {}) if action == "create"
                 else (view.get("change_plan") or {}))
    effect_error = container.get("requested_effects_error") if isinstance(container, dict) else None
    if (isinstance(effect_error, dict)
            and effect_error.get("contract") == "requested-effects-error.v1"):
        return [{
            "index": -1, "field": "requested_effects", "source": "final_authority",
            "expected": "typed target-field-value mapping",
            "actual": str(effect_error.get("kind") or "unresolved"),
            "evidence": ["continuation target set has multiple scalar destinations"],
            "message": "다중 대상 scalar 변경의 target별 값을 확정할 수 없다",
        }]
    typed, typed_error = requested_update_effect_authority(view)
    raw_typed = isinstance(view.get("request_plan"), dict) \
        and "requested_effects" in view["request_plan"]
    if raw_typed and typed_error not in {"", "missing"}:
        return [{
            "index": -1, "field": "requested_effects", "source": "final_authority",
            "expected": "valid RequestPlan target-field-value mapping",
            "actual": typed_error, "evidence": ["typed RequestPlan requested_effects"],
            "message": "RequestPlan의 exact requested effects가 유효하지 않다",
        }]
    targets, decoded, _text = _legacy_update_scalar_authority(view)
    if not typed and len(targets) > 1 \
            and any(field in UPDATE_SCALAR_FIELDS for field in decoded):
        return [{
            "index": -1, "field": "requested_effects", "source": "final_authority",
            "expected": "typed target-field-value mapping",
            "actual": "per_target_mapping_required",
            "evidence": ["multi-target scalar request without typed effect mapping"],
            "message": "다중 대상 scalar 변경의 target별 값을 확정할 수 없다",
        }]
    snapshot = container.get("requested_effects") if isinstance(container, dict) else None
    opted_in = (isinstance(container, dict)
                and container.get("effect_contract") == "requested-effects.v1")
    authoritative = derive_requested_effect_contract(
        view, draft=view.get("draft") or {}, change_plan=view.get("change_plan") or {},
    )
    errors: list[dict] = []
    request_plan = view.get("request_plan") or {}
    typed_write_request = bool(
        isinstance(request_plan, dict)
        and any(isinstance(row, dict) and row.get("write_intent") is True
                for row in (request_plan.get("tasks") or []))
    )
    if not opted_in and not snapshot:
        if authoritative.get("effects") and typed_write_request:
            return [{
                "index": -1, "field": "requested_effects", "source": "final_authority",
                "expected": authoritative.get("digest") or "requested-effects.v1 seal",
                "actual": "missing",
                "evidence": ["typed RequestPlan write outcome"],
                "message": "immutable requested-effect seal is missing from the final payload",
            }]
        return errors
    if not authoritative.get("effects") and not snapshot:
        return errors
    if (not isinstance(snapshot, dict)
            or snapshot.get("contract") != "requested-effects.v1"
            or snapshot.get("digest") != _requested_effect_digest(snapshot.get("effects") or [])):
        return [{
            "index": -1, "field": "requested_effects", "source": "final_authority",
            "expected": authoritative.get("digest") or "valid requested-effects.v1 seal",
            "actual": str((snapshot or {}).get("digest") or "missing"),
            "evidence": ["typed request effect contract"],
            "message": "immutable requested-effect seal is missing or invalid",
        }]
    sealed_rows = snapshot.get("effects") or []
    authoritative_rows = authoritative.get("effects") or []
    if authoritative_rows != sealed_rows:
        errors.append({
            "index": -1, "field": "requested_effects", "source": "final_authority",
            "expected": _canonical_value(authoritative_rows),
            "actual": _canonical_value(sealed_rows),
            "evidence": ["typed request outcome/target/field/value"],
            "message": "sealed requested effects differ from the authoritative request",
        })
    for effect in sealed_rows:
        present, actual = _actual_effect_value(view, effect)
        expected = _canonical_value(effect.get("value"))
        if present and actual == expected:
            continue
        errors.append({
            "index": -1, "field": "requested_effects", "source": "final_authority",
            "expected": f"{effect.get('target')}:{effect.get('field')}={expected}",
            "actual": actual if present else "missing",
            "evidence": ["immutable requested effect"],
            "message": (f"requested effect {effect.get('field')} for {effect.get('target')} "
                        "is missing or changed in the final payload"),
        })
    return errors


def payload_digest(state: AgentState) -> str:
    """Digest only the actionable draft/change payload reviewed by Auditor."""
    view = project_final_authority_state(state)
    payload = {
        "draft": view.get("draft") or {},
        "change_plan": view.get("change_plan") or {},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def typed_audit_findings(state: AgentState, rows) -> list[dict]:
    """Project legacy machine/model rows onto stable item and payload identities."""
    items = [row for row in ((state.get("draft") or {}).get("items") or [])
             if isinstance(row, dict)]
    digest = payload_digest(state)
    findings: list[dict] = []
    seen = set()
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        index = raw.get("index") if isinstance(raw.get("index"), int) else -1
        item = items[index] if 0 <= index < len(items) else {}
        item_id = str(raw.get("item_id") or item.get("item_id") or "global")
        field = str(raw.get("field") or raw.get("check") or "semantic")
        child_index = raw.get("child_index")
        path = (f"draft.items[item_id={item_id}]"
                + (f".children[{child_index}]" if isinstance(child_index, int) else "")
                + f".{field}" if item_id != "global" else f"workflow.{field}")
        expected = str(raw.get("expected") or raw.get("fix") or "contract satisfied")
        actual = str(raw.get("actual") or raw.get("message") or "contract violation")
        evidence = [str(value) for value in (raw.get("evidence") or []) if str(value)]
        if not evidence:
            evidence = [str(raw.get("source") or raw.get("check") or "auditor")]
        finding = {
            "item_id": item_id,
            "field_path": path,
            "expected": expected,
            "actual": actual,
            "evidence": evidence,
            "payload_digest": digest,
        }
        identity = (item_id, path, expected, actual)
        if identity not in seen:
            seen.add(identity)
            findings.append(finding)
    return findings


def defect_signature(findings: list[dict]) -> str:
    """Stable semantic signature; payload digest is intentionally excluded."""
    material = [{key: row.get(key) for key in (
        "item_id", "field_path", "expected", "actual",
    )} for row in findings if isinstance(row, dict)]
    if not material:
        return ""
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return "defect:" + hashlib.sha256(encoded).hexdigest()[:20]


def finding_signature_key(finding: dict) -> str:
    """Return one authority-aware semantic defect identity independent of payload revision."""
    if not isinstance(finding, dict):
        return ""
    material = {
        key: _canonical_value(finding.get(key))
        for key in ("item_id", "field_path", "expected", "actual", "authority")
    }
    if not any(material.values()):
        return ""
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def defect_signature_set(findings: list[dict]) -> str:
    """Encode a reversible set of per-finding identities for subset recurrence checks."""
    keys = sorted({key for key in (
        finding_signature_key(row) for row in findings or []
    ) if key})
    return "defects.v2:" + ",".join(keys) if keys else ""


def parse_defect_signature_set(value: str) -> frozenset[str]:
    """Decode only a well-formed v2 set; legacy aggregate hashes are intentionally opaque."""
    prefix = "defects.v2:"
    raw = str(value or "")
    if not raw.startswith(prefix):
        return frozenset()
    keys = raw[len(prefix):].split(",") if raw[len(prefix):] else []
    if any(not re.fullmatch(r"[0-9a-f]{20}", key) for key in keys):
        return frozenset()
    return frozenset(keys)


def recurrent_finding_signature_keys(
        findings: list[dict], prior_signature: str) -> frozenset[str]:
    """Return persistent individual defects even when sibling findings were repaired."""
    current = parse_defect_signature_set(defect_signature_set(findings))
    prior = parse_defect_signature_set(prior_signature)
    return current & prior


def continuation_action(state: AgentState) -> str:
    """Return only a validated typed continuation action; never infer from old messages."""
    contract = state.get("continuation_contract") or {}
    if not isinstance(contract, dict) or contract.get("version") != "continuation.v1":
        return ""
    action = str(contract.get("action") or "")
    return action if action in {
        "read", "create", "comment", "update", "mixed", "respond",
    } else ""


def current_work_failed(state: AgentState) -> bool:
    """Whether the current Work call ended at its structured transport boundary."""
    return str(state.get("error") or "").startswith(f"[{Node.WORK_ARCHITECT}]")


def project_final_authority_state(state: AgentState) -> dict:
    """Remove stale effect families that the typed action does not authorize."""
    projected = dict(state)
    action = continuation_action(state)
    if action == "comment":
        plan = dict(state.get("change_plan") or {})
        plan["changes"] = {}
        plan.pop("transition", None)
        plan.pop("link", None)
        if plan:
            plan["why"] = "댓글 전용 요청 — 필드·상태 변경 없음"
        projected["change_plan"] = plan
        projected["draft"] = {}
    elif action == "update":
        projected["draft"] = {}
    elif action == "create":
        projected["change_plan"] = {}
    elif action in {"read", "respond"}:
        projected["draft"] = {}
        projected["change_plan"] = {}
    return projected


def final_effect(state: AgentState) -> FinalEffect:
    """Classify the projected approval effect without constructing or staging payloads."""
    view = project_final_authority_state(state)
    draft = view.get("draft") or {}
    plan = view.get("change_plan") or {}
    items = [row for row in (draft.get("items") or []) if isinstance(row, dict)]
    targets = list(dict.fromkeys(
        str(key).strip().upper()
        for key in (plan.get("keys") or [plan.get("key")])
        if str(key or "").strip()
    ))
    actions: list[str] = []
    if items:
        actions.append("create_epic" if (draft.get("mode") or "task") == "epic"
                       else "create_tickets")
    if targets and (plan.get("transition") or {}).get("id"):
        actions.append("transition_ticket")
    elif targets and (plan.get("link") or {}).get("other"):
        actions.append("link_tickets")
    elif targets and (plan.get("changes") or {}):
        actions.append("update_tickets" if plan.get("keys") else "update_ticket")
    comments = [row for row in (plan.get("comments") or [])
                if isinstance(row, dict) and str(row.get("key") or "").strip()
                and str(row.get("body") or "").strip()]
    comment = str(plan.get("comment") or "").strip()
    # A transition carries its comment atomically. Other primary mutations use a separately
    # bound capability, but remain one reviewed update effect.
    if targets and (comments or comment) and not (plan.get("transition") or {}).get("id"):
        actions.append("add_ticket_comments" if plan.get("keys") else "add_ticket_comment")

    creates = any(action.startswith("create_") for action in actions)
    updates = any(action in UPDATE_EFFECT_ACTIONS for action in actions)
    comments_only = any(action.startswith("add_ticket_comment") for action in actions)
    if creates and (updates or comments_only):
        kind = "conflict"
    elif creates:
        kind = "create"
    elif updates:
        kind = "update"
    elif comments_only:
        kind = "comment"
    else:
        kind = "none"
    effect_targets = list(targets)
    other = str((plan.get("link") or {}).get("other") or "").strip().upper()
    if other and other not in effect_targets:
        effect_targets.append(other)
    return FinalEffect(
        kind=kind,
        actions=tuple(actions),
        target_count=(len(items) if kind == "create" else len(effect_targets)),
    )


def capture_user_field_locks(draft: dict) -> tuple[UserFieldLock, ...]:
    """Capture assigned/unassigned user decisions before recommendation merging."""
    locks: list[UserFieldLock] = []
    for index, item in enumerate((draft or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("assignee_source") or "")
        if source in {"user", "user_unassigned"}:
            locks.append(UserFieldLock(
                index, None, "assignee",
                str(item.get("assignee") or "") if source == "user" else "",
            ))
        for child_index, child in enumerate(item.get("children") or []):
            if not isinstance(child, dict):
                continue
            source = str(child.get("assignee_source") or "")
            if source in {"user", "user_unassigned"}:
                locks.append(UserFieldLock(
                    index, child_index, "assignee",
                    str(child.get("assignee") or "") if source == "user" else "",
                ))
    return tuple(locks)


__all__ = [
    "FinalEffect", "RequestedEffect", "UPDATE_EFFECT_ACTIONS", "UserFieldLock",
    "WRITE_ACTIONS",
    "capture_user_field_locks", "continuation_action", "current_work_failed",
    "defect_signature", "defect_signature_set", "derive_requested_effect_contract",
    "final_effect", "finding_signature_key",
    "issue_requested_update_effects", "materialize_requested_update_effects",
    "payload_digest", "project_final_authority_state", "requested_update_effect_authority",
    "seal_requested_effect_contract",
    "parse_defect_signature_set", "recurrent_finding_signature_keys",
    "typed_audit_findings", "validate_requested_effect_contract",
]
