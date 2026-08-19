"""Auditor — 사용자에게 보이기 전에 초안을 **스스로 검열**한다(Self-Check 3종).

여기서 걸러야 할 것은 두 종류이고, 성격이 완전히 다르다.

  · **기계가 판정할 수 있는 것** — 없는 부모 키, 허용되지 않은 타입, 빠진 필수값.
    이건 LLM 에게 묻지 않는다. `domain/bulk.validate_bulk` 가 화면의 Bulk 생성과 **같은 규칙**으로
    판정한다. 규칙이 두 벌이 되면 반드시 갈라지고, 그때 더 관대한 쪽이 사고를 낸다.
  · **판단이 필요한 것** — 근거 없는 서술, 요청과 어긋난 분해, 과잉 분해.
    이건 규칙으로 못 잡는다. 그래서 모델에게 **세 가지를 각각** 자문하게 한다.

    ① 근거 있는가 — 초안에 적힌 티켓 키·사람·날짜가 조사에서 실제로 나온 것인가
    ② 규칙에 맞는가 — 티켓 작성 규칙(SP·Epic Link·컴포넌트)을 어기지 않았나
    ③ 요청에 답하는가 — 사용자가 부탁한 일을 실제로 담고 있나

**기계 판정이 항상 이긴다.** 모델이 "문제없다"고 해도 `validate_bulk` 가 막으면 막힌 것이다.
반대로 모델이 문제를 찾으면 그건 사람에게 보여 준다 — 기계가 못 보는 종류라서다.
"""

from __future__ import annotations

import json
import re as _re
from html import unescape

from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.agents.work_architect import (
    _authoritative_explicit_due, _explicit_due_instruction_status,
    _can_parent_subtask,
    _current_request_boundary_text, _global_exact_due_for_roots,
    _delegates_existing_epic_choice, _explicit_parent_epic,
    _explicit_hierarchical_ordinal_contract,
    _evidence_obligation_errors, _verified_evidence_obligations,
    _expected_due_dates_by_root, _expected_parent_epics_by_root,
    _separate_typed_meeting_scalars,
    as_bulk_items, draft_full_text,
)
from app.agent.prompts.roles import SYSTEM_AUDITOR
from app.agent.workflow.anchors import (
    is_ordinal, outcome_authority_terms, requested_outcome_contract,
    required_user_anchors,
    scoped_continuation_decisions, validate_draft_outcome_contract,
    validate_scoped_outcome_bindings,
)
from app.agent.workflow.continuation import (
    is_top_level_parent_choice,
    jira_keys,
    parse_assignee_decision,
)
from app.agent.workflow.effect_contract import (
    UPDATE_EFFECT_ACTIONS as _UPDATE_EFFECT_ACTIONS,
    WRITE_ACTIONS as _WRITE_ACTIONS,
    UserFieldLock,
    capture_user_field_locks,
    continuation_action,
    current_work_failed,
    defect_signature_set,
    final_effect,
    finding_signature_key,
    payload_digest,
    parse_defect_signature_set,
    project_final_authority_state,
    recurrent_finding_signature_keys,
    typed_audit_findings,
    validate_requested_effect_contract,
)
from app.agent.workflow.meeting_context import (
    NO_MEETING_ASSIGNMENT_REF,
    is_meeting_request,
    meeting_assignment_bindings,
    meeting_assignment_source_mapping,
    meeting_owner_records,
    meeting_requester_instructors,
    resolved_people,
)
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import (
    AgentState, Node, last_user_text, note, request_text,
    verified_parent_epic_candidates,
)
from app.agent.workflow.typed_fast_path import (
    advance_typed_repair_budget,
    evaluate_typed_fast_path,
    make_typed_check_result,
    parse_typed_check_result,
    typed_fast_path_note,
)


AUDITOR_MACHINE_AUTHORITY = "auditor.machine-check.v1"
_MACHINE_RESULT_KEY = "_auditor_machine_check_result"

SCHEMA = {
    "type": "object",
    "properties": {
        "grounded": {"type": "boolean",
                     "description": "Whether every ticket key, person, date, and claim is grounded in evidence."},
        "rule_compliant": {"type": "boolean", "description": "Whether the draft follows ticket rules."},
        "answers_request": {"type": "boolean", "description": "Whether the draft covers the user's request."},
        "problems": {
            "type": "array", "maxItems": 6,
            "items": {"type": "object", "properties": {
                "index": {"type": "integer", "description": "Zero-based item index, or -1 for the whole draft."},
                "check": {"type": "string", "enum": ["grounded", "rule", "request"]},
                "finding_kind": {
                    "type": "string",
                    "enum": ["field_mismatch", "missing_requirement", "contradiction",
                             "policy_violation", "request_coverage"],
                    "description": "Machine-readable defect relation, independent of prose wording.",
                },
                "field": {
                    "type": "string", "maxLength": 80,
                    "description": "Affected payload field; use assignee for an owner identity claim.",
                },
                "expected": {
                    "type": "string", "maxLength": 160,
                    "description": "Exact expected field value; empty when not a field comparison.",
                },
                "actual": {
                    "type": "string", "maxLength": 160,
                    "description": "Exact observed field value; empty when not a field comparison.",
                },
                "message": {"type": "string", "maxLength": 220,
                            "description": "One Korean sentence describing what is wrong and why."},
                "fix": {"type": "string", "maxLength": 220,
                        "description": "A precise Korean repair instruction."}}},
            "description": "Blocking semantic problems only; empty when none. Never invent a defect.",
        },
        "summary": {"type": "string", "maxLength": 280,
                    "description": "One or two Korean sentences visible to the user."},
    },
    "required": ["grounded", "rule_compliant", "answers_request", "problems"],
}


_KEY = _re.compile(r"(?<![A-Z0-9-])([A-Z][A-Z0-9]{1,9}-\d+)(?!\d)", _re.I)


def _locked_field_errors(state: AgentState,
                         locks: tuple[UserFieldLock, ...]) -> list[dict]:
    items = [row for row in ((state.get("draft") or {}).get("items") or [])
             if isinstance(row, dict)]
    errors = []
    for lock in locks:
        row = items[lock.index] if 0 <= lock.index < len(items) else None
        if row is not None and lock.child_index is not None:
            children = [child for child in (row.get("children") or []) if isinstance(child, dict)]
            row = children[lock.child_index] if 0 <= lock.child_index < len(children) else None
        actual = str((row or {}).get(lock.field) or "")
        if row is not None and actual == lock.value:
            continue
        error = {
            "index": lock.index,
            "field": lock.field,
            "message": ("사용자 지정 미할당이 추천 병합 뒤 담당자로 바뀌었다"
                        if not lock.value else
                        f"사용자 지정 담당자 {lock.value}가 추천 병합 뒤 {actual or '비어 있음'}으로 바뀌었다"),
            "source": "final_authority",
        }
        if lock.child_index is not None:
            error["child_index"] = lock.child_index
        errors.append(error)
    return errors


_TOP_LEVEL_PARENT = "__TOP_LEVEL__"


_is_top_level_parent_value = is_top_level_parent_choice


def _typed_parent(state: AgentState) -> str:
    """Read one exact parent from the typed authority, not from conversation history."""
    contract = state.get("continuation_contract") or {}
    if not isinstance(contract, dict) or contract.get("version") != "continuation.v1":
        return ""
    for decision in reversed(contract.get("decisions") or []):
        if not isinstance(decision, dict):
            continue
        # A scoped ``parent:<outcome>`` decision belongs only to the matching outcome. The
        # existing machine check maps those per-outcome clauses through opaque outcome_refs;
        # treating the last scoped value as a global parent would corrupt every sibling.
        field = str(decision.get("field") or "").strip().casefold()
        if field not in {"parent", "epic"}:
            continue
        if _is_top_level_parent_value(str(decision.get("value") or "")):
            return _TOP_LEVEL_PARENT
        keys = jira_keys(decision.get("value"))
        if len(keys) == 1:
            return keys[0]
    root = str(contract.get("root_request") or "")
    matches = []
    key = r"([A-Z][A-Z0-9]{1,9}-\d+)"
    for pattern in (
        rf"(?:Epic|에픽|parent|상위)(?:은|는|이|가|을|를|으로|로|:)?\s*{key}",
        rf"{key}\s*(?:Epic|에픽)?\s*(?:아래|밑에?|하위|상위)",
    ):
        matches.extend(match.group(1).upper() for match in _re.finditer(pattern, root, _re.I))
    values = list(dict.fromkeys(matches))
    return values[0] if len(values) == 1 else ""


def _typed_parent_errors(state: AgentState) -> list[dict]:
    expected = _typed_parent(state)
    if not expected:
        return []
    errors = []
    for index, row in enumerate((state.get("draft") or {}).get("items") or []):
        if not isinstance(row, dict):
            continue
        actual = str(row.get("parent") or row.get("epic") or "").strip().upper()
        if expected == _TOP_LEVEL_PARENT and actual:
            errors.append({
                "index": index, "field": "parent", "source": "final_authority",
                "message": f"typed 사용자는 최상위 Task를 지정했으나 최종 payload parent는 {actual}",
            })
        elif expected != _TOP_LEVEL_PARENT and actual != expected:
            errors.append({
                "index": index, "field": "parent", "source": "final_authority",
                "message": f"typed 사용자 지정 parent는 {expected}이나 최종 payload는 {actual or '비어 있음'}",
            })
    return errors


def _typed_assignee_errors(state: AgentState) -> list[dict]:
    """Validate one unscoped exact owner decision across all final roots."""
    contract = state.get("continuation_contract") or {}
    if not isinstance(contract, dict) or contract.get("version") != "continuation.v1":
        return []
    decision = None
    for row in contract.get("decisions") or []:
        if not isinstance(row, dict):
            continue
        field = str(row.get("field") or "").strip().casefold()
        if ":" not in field and field in {"assignee", "owner"}:
            decision = row
    if not decision:
        return []
    value = str(decision.get("value") or "").strip()
    kind, parsed = parse_assignee_decision(value)
    unassigned = kind == "unassigned"
    user_id = parsed if kind == "user_id" else ""
    named = kind == "display_name"
    errors = []
    for index, row in enumerate((state.get("draft") or {}).get("items") or []):
        if not isinstance(row, dict):
            continue
        actual = str(row.get("assignee") or "").strip().casefold()
        source = str(row.get("assignee_source") or "")
        if unassigned and actual:
            message = f"typed 사용자는 미할당을 지정했으나 최종 payload 담당자는 {actual}"
        elif user_id and actual != user_id:
            message = (f"typed 사용자 지정 담당자는 {user_id}이나 최종 payload는 "
                       f"{actual or '비어 있음'}")
        elif named and (not actual or source != "user"):
            message = "이름으로 지정한 담당자가 검증된 Jira 계정으로 최종 고정되지 않았다"
        else:
            continue
        errors.append({
            "index": index, "field": "assignee", "source": "final_authority",
            "message": message,
        })
    return errors


def _scoped_decision_errors(state: AgentState) -> list[dict]:
    """Validate per-outcome user fields against their one exact final root."""
    draft = state.get("draft") or {}
    errors = validate_scoped_outcome_bindings(state, draft)
    scoped = scoped_continuation_decisions(state)
    if not scoped:
        return errors
    contract = requested_outcome_contract(state)
    aliases: dict[str, str] = {}
    for outcome in contract.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        opaque = str(outcome.get("id") or "").strip()
        source_task_id = str(outcome.get("source_task_id") or "").strip()
        if opaque:
            aliases[opaque.casefold()] = opaque
        if opaque and source_task_id:
            aliases[source_task_id.casefold()] = opaque

    roots = [row for row in (draft.get("items") or []) if isinstance(row, dict)]
    bound: dict[str, list[tuple[int, dict]]] = {ref: [] for ref in scoped}
    for index, row in enumerate(roots):
        raw_refs = [str(value or "").strip() for value in (row.get("outcome_refs") or [])
                    if str(value or "").strip()]
        refs = [aliases.get(value.casefold(), "") for value in raw_refs]
        refs = [value for value in refs if value]
        if len(refs) == 1 and refs[0] in bound:
            bound[refs[0]].append((index, row))

    for outcome_ref, decisions in scoped.items():
        matches = bound.get(outcome_ref) or []
        if len(matches) != 1:
            continue
        index, row = matches[0]
        parent = decisions.get("parent") or {}
        expected_keys = jira_keys(parent.get("value"))
        top_level = bool(parent and _is_top_level_parent_value(
            str(parent.get("value") or "")))
        if top_level:
            actual = str(row.get("parent") or row.get("epic") or "").strip().upper()
            if actual:
                errors.append({
                    "index": index, "field": "parent",
                    "message": (f"outcome {outcome_ref} is explicitly top-level, but final "
                                f"payload has parent {actual}"),
                })
        elif parent and len(expected_keys) != 1:
            errors.append({
                "index": index, "field": "parent",
                "message": f"outcome {outcome_ref} scoped parent is not one exact Jira key",
            })
        elif len(expected_keys) == 1:
            expected = expected_keys[0]
            actual = str(row.get("parent") or row.get("epic") or "").strip().upper()
            if actual != expected:
                errors.append({
                    "index": index, "field": "parent",
                    "message": (f"outcome {outcome_ref} parent is {expected}, but final "
                                f"payload has {actual or '비어 있음'}"),
                })

        assignment = decisions.get("assignee") or {}
        value = str(assignment.get("value") or "").strip()
        if not value:
            continue
        kind, parsed = parse_assignee_decision(value)
        unassigned = kind == "unassigned"
        user_id = parsed if kind == "user_id" else ""
        actual = str(row.get("assignee") or "").strip().casefold()
        if unassigned and actual:
            errors.append({
                "index": index, "field": "assignee",
                "message": (f"outcome {outcome_ref} is explicitly unassigned, but final "
                            f"payload assigns {actual}"),
            })
        elif user_id and actual != user_id:
            errors.append({
                "index": index, "field": "assignee",
                "message": (f"outcome {outcome_ref} assignee is {user_id}, but final "
                            f"payload has {actual or '비어 있음'}"),
            })
        elif kind == "display_name":
            source = str(row.get("assignee_source") or "")
            if not actual or source != "user":
                errors.append({
                    "index": index, "field": "assignee",
                    "message": (f"outcome {outcome_ref} named assignee was not resolved to "
                                "one verified Jira account"),
                })
        elif kind == "unknown":
            errors.append({
                "index": index, "field": "assignee",
                "message": f"outcome {outcome_ref} assignee directive is not a verified identity",
            })
    return errors


def _typed_change_target_errors(state: AgentState) -> list[dict]:
    """Seal the executable change targets to the typed continuation envelope.

    Approval fingerprints prove that a payload was not modified after staging; they do not
    prove that the payload targets the ticket the user named.  Compare the complete primary
    target set here, including the other side of an explicit link and per-row comments, before
    any capability can be minted.
    """
    contract = state.get("continuation_contract") or {}
    if not isinstance(contract, dict) or contract.get("version") != "continuation.v1":
        return []
    if str(contract.get("action") or "") not in {"comment", "update", "mixed"}:
        return []
    expected = {
        str(value or "").strip().upper()
        for value in (contract.get("target_keys") or [])
        if _KEY.fullmatch(str(value or "").strip())
    }
    plan = state.get("change_plan") or {}
    singular_key = str(plan.get("key") or "").strip().upper()
    raw_bulk_keys = [str(value or "").strip() for value in (plan.get("keys") or [])]
    bulk_keys = [value.upper() for value in raw_bulk_keys]
    actual = {
        str(value or "").strip().upper()
        for value in [*bulk_keys, singular_key]
        if _KEY.fullmatch(str(value or "").strip())
    }
    other = str((plan.get("link") or {}).get("other") or "").strip().upper()
    if _KEY.fullmatch(other):
        actual.add(other)
    for row in plan.get("comments") or []:
        key = str((row or {}).get("key") or "").strip().upper() if isinstance(row, dict) else ""
        if _KEY.fullmatch(key):
            actual.add(key)
    errors = []
    valid_bulk_keys = [value for value in bulk_keys if _KEY.fullmatch(value)]
    if raw_bulk_keys and (
            len(valid_bulk_keys) != len(raw_bulk_keys)
            or len(set(valid_bulk_keys)) != len(valid_bulk_keys)):
        errors.append({
            "index": -1, "field": "target", "source": "final_authority",
            "message": "bulk target key는 유효한 Jira key의 중복 없는 목록이어야 한다",
        })
    if singular_key and bulk_keys:
        errors.append({
            "index": -1, "field": "target", "source": "final_authority",
            "message": "change_plan에 singular key와 bulk keys가 함께 있어 실행 대상이 모호하다",
        })
    if expected != actual or not expected:
        errors.append({
            "index": -1, "field": "target", "source": "final_authority",
            "message": (
                "typed 변경 대상과 최종 실행 대상이 다르다 "
                f"(요청: {', '.join(sorted(expected)) or '없음'}; "
                f"payload: {', '.join(sorted(actual)) or '없음'})"
            ),
        })

    primary_keys = {
        str(value or "").strip().upper()
        for value in (plan.get("keys") or [])
        if _KEY.fullmatch(str(value or "").strip())
    }
    raw_previews = [row for row in (plan.get("comments") or []) if isinstance(row, dict)]
    previews = [row for row in raw_previews
                if _KEY.fullmatch(str(row.get("key") or "").strip())
                and str(row.get("body") or "").strip()]
    if raw_previews and len(previews) != len(raw_previews):
        errors.append({
            "index": -1, "field": "comment_targets", "source": "final_authority",
            "message": "댓글 미리보기에는 유효한 Jira key와 비어 있지 않은 본문만 허용된다",
        })
    if not primary_keys and raw_previews:
        errors.append({
            "index": -1, "field": "comment_targets", "source": "final_authority",
            "message": "단건 변경의 댓글은 comments 배열이 아니라 exact key의 comment로 확정해야 한다",
        })
    if primary_keys and previews:
        preview_keys = [str(row.get("key") or "").strip().upper() for row in previews]
        explicit_comment_targets = _explicit_named_comment_targets(state)
        expected_comment_targets = explicit_comment_targets or primary_keys
        if (set(preview_keys) != expected_comment_targets
                or not set(preview_keys) <= primary_keys
                or len(preview_keys) != len(expected_comment_targets)
                or len(set(preview_keys)) != len(preview_keys)):
            errors.append({
                "index": -1, "field": "comment_targets", "source": "final_authority",
                "message": ("일괄 변경의 댓글 미리보기 대상이 명시된 댓글 대상과 일치하지 않는다 "
                            f"(expected comments: {', '.join(sorted(expected_comment_targets))}; "
                            f"comments: {', '.join(preview_keys) or '없음'})"),
            })
    return errors


def _explicit_named_comment_targets(state: AgentState) -> set[str]:
    """Return Jira keys explicitly bound to the comment effect in current authority text."""
    contract = state.get("continuation_contract") or {}
    text = (str(contract.get("root_request") or "")
            if isinstance(contract, dict) and contract.get("version") == "continuation.v1"
            else _current_request_boundary_text(state))
    return {
        match.group(1).upper() for match in _re.finditer(
            r"(?<![A-Z0-9-])([A-Z][A-Z0-9]{1,9}-\d+)(?![A-Z0-9-])"
            r"\s*(?:에|에는|에만|에게)\s*[^.!?\n]{0,48}(?:댓글|코멘트)",
            text, _re.I,
        )
    }


def _change_shape_errors(state: AgentState) -> list[dict]:
    """Reject change containers that encode multiple competing primary mutations."""
    plan = state.get("change_plan") or {}
    if not isinstance(plan, dict) or not plan:
        return []
    primary = [
        name for name, present in (
            ("transition", bool((plan.get("transition") or {}).get("id"))),
            ("link", bool((plan.get("link") or {}).get("other"))),
            ("fields", bool(plan.get("changes") or {})),
        ) if present
    ]
    errors = []
    raw_keys = [str(value or "").strip().upper() for value in (plan.get("keys") or [])]
    if plan.get("key") and raw_keys:
        errors.append({
            "index": -1, "field": "target", "source": "final_authority",
            "message": "change_plan은 singular key와 bulk keys를 동시에 가질 수 없다",
        })
    valid_keys = [value for value in raw_keys if _KEY.fullmatch(value)]
    if raw_keys and (len(valid_keys) != len(raw_keys)
                     or len(set(valid_keys)) != len(valid_keys)):
        errors.append({
            "index": -1, "field": "target", "source": "final_authority",
            "message": "bulk target은 유효한 Jira key의 중복 없는 목록이어야 한다",
        })
    raw_comments = [row for row in (plan.get("comments") or []) if isinstance(row, dict)]
    if raw_comments:
        comment_keys = [str(row.get("key") or "").strip().upper() for row in raw_comments]
        valid_comments = [row for row in raw_comments
                          if _KEY.fullmatch(str(row.get("key") or "").strip())
                          and str(row.get("body") or "").strip()]
        if (len(valid_comments) != len(raw_comments)
                or len(set(comment_keys)) != len(comment_keys)):
            errors.append({
                "index": -1, "field": "comment_targets", "source": "final_authority",
                "message": "comments는 유효한 Jira key별 비어 있지 않은 본문을 한 번씩만 가져야 한다",
            })
        if not raw_keys:
            errors.append({
                "index": -1, "field": "comment_targets", "source": "final_authority",
                "message": "단건 댓글은 comments 배열이 아니라 top-level comment로 정규화해야 한다",
            })
    if len(primary) > 1:
        errors.append({
            "index": -1, "field": "effect", "source": "final_authority",
            "message": ("change_plan에 서로 다른 primary mutation이 함께 있어 하나가 "
                        f"누락될 수 있다: {', '.join(primary)}"),
        })
    if (plan.get("keys") or []) and any(name in primary for name in ("transition", "link")):
        errors.append({
            "index": -1, "field": "effect", "source": "final_authority",
            "message": "bulk keys에는 singular transition/link mutation을 함께 실행할 수 없다",
        })
    return errors


def _explicit_link_and_comment_errors(state: AgentState) -> list[dict]:
    """Bind directional link/comment effects to the literal typed request.

    ``target_keys`` is intentionally an unordered identity set.  That is insufficient for a
    directional relation or for deciding which endpoint receives a comment, so recover only
    high-precision literal clauses from the immutable root request and reject mismatches.
    """
    plan = state.get("change_plan") or {}
    if not isinstance(plan, dict) or not plan:
        return []
    contract = state.get("continuation_contract") or {}
    text = (str(contract.get("root_request") or "").strip()
            if isinstance(contract, dict) and contract.get("version") == "continuation.v1"
            else _current_request_boundary_text(state))
    if not text:
        return []
    errors: list[dict] = []
    link = plan.get("link") if isinstance(plan.get("link"), dict) else {}
    if link.get("other") and _re.search(r"링크|연결|\b(?:blocks?|relates?)\b|막는\s*관계", text, _re.I):
        actual_key = str(plan.get("key") or "").strip().upper()
        actual_other = str(link.get("other") or "").strip().upper()
        actual_relation = str(link.get("relation") or "Relates").strip().casefold()
        block = _re.search(
            r"(?<![A-Z0-9-])([A-Z][A-Z0-9]{1,9}-\d+)(?![A-Z0-9-])\s*(?:이|가)\s*"
            r"(?<![A-Z0-9-])([A-Z][A-Z0-9]{1,9}-\d+)(?![A-Z0-9-])\s*(?:을|를)"
            r"[^.!?\n]{0,40}(?:막|block)", text, _re.I,
        )
        if not block:
            block = _re.search(
                r"(?<![A-Z0-9-])([A-Z][A-Z0-9]{1,9}-\d+)(?![A-Z0-9-])"
                r"\s+(?:blocks?|막(?:는|는다)?)\s+"
                r"(?<![A-Z0-9-])([A-Z][A-Z0-9]{1,9}-\d+)(?![A-Z0-9-])",
                text, _re.I,
            )
        if block:
            expected_key, expected_other = (block.group(1).upper(), block.group(2).upper())
            if ((actual_key, actual_other) != (expected_key, expected_other)
                    or actual_relation != "blocks"):
                errors.append({
                    "index": -1, "field": "link", "source": "final_authority",
                    "message": (f"명시된 방향 관계는 {expected_key} Blocks {expected_other}이나 "
                                f"payload는 {actual_key or '없음'} "
                                f"{str(link.get('relation') or 'Relates')} {actual_other or '없음'}이다"),
                })
        else:
            keys = list(dict.fromkeys(match.group(1).upper() for match in _KEY.finditer(text)))
            if len(keys) == 2 and ({actual_key, actual_other} != set(keys)
                                   or actual_relation != "relates"):
                errors.append({
                    "index": -1, "field": "link", "source": "final_authority",
                    "message": "명시된 두 Jira endpoint의 일반 연결(Relates)과 payload가 다르다",
                })

    has_comment = bool(str(plan.get("comment") or "").strip() or plan.get("comments"))
    if has_comment:
        named_comment_targets = sorted(_explicit_named_comment_targets(state))
        if len(named_comment_targets) == 1:
            if plan.get("keys"):
                actual_comment_targets = {
                    str(row.get("key") or "").strip().upper()
                    for row in (plan.get("comments") or []) if isinstance(row, dict)
                } or {str(value or "").strip().upper() for value in plan.get("keys") or []}
            else:
                actual_comment_targets = {str(plan.get("key") or "").strip().upper()}
            if actual_comment_targets != set(named_comment_targets):
                errors.append({
                    "index": -1, "field": "comment_targets", "source": "final_authority",
                    "message": (f"명시적 댓글 대상은 {named_comment_targets[0]}이나 payload 대상은 "
                                f"{', '.join(sorted(actual_comment_targets)) or '없음'}이다"),
                })
    return errors


def _cardinality_errors(state: AgentState) -> list[dict]:
    """Enforce only explicit single-Sub-Task cardinality; decomposition otherwise remains semantic."""
    contract = state.get("continuation_contract") or {}
    if not isinstance(contract, dict) or contract.get("version") != "continuation.v1":
        return []
    texts = [str(contract.get("root_request") or "")]
    allowed_ids = {str(value or "") for value in (contract.get("outcome_ids") or [])}
    for task in (state.get("request_plan") or {}).get("tasks") or []:
        if isinstance(task, dict) and (not allowed_ids or str(task.get("id") or "") in allowed_ids):
            texts.append(str(task.get("instruction") or ""))
    material = "\n".join(texts)
    subtask_term = r"(?:Sub[ -]?Task|서브\s*태스크|서브테스크|하위\s*(?:태스크|티켓|작업))"
    explicit_single = bool(_re.search(
        rf"{subtask_term}.{{0,40}}"
        r"(?:하나|한\s*(?:개|건)|1\s*(?:개|건)|\bsingle\b|\bone\b)|"
        rf"(?:하나|한\s*(?:개|건)|1\s*(?:개|건)|\bsingle\b|\bone\b).{{0,40}}"
        rf"{subtask_term}",
        material, _re.I,
    ))
    # ``A와 B에 각각 한 개씩`` is a per-target distribution, not one total issue.
    distributive = bool(_re.search(
        rf"(?:각각.{{0,40}}{subtask_term}.{{0,24}}(?:하나|한\s*(?:개|건)|1\s*(?:개|건))|"
        rf"{subtask_term}.{{0,40}}(?:하나|한\s*(?:개|건)|1\s*(?:개|건))\s*씩)",
        material, _re.I,
    ))
    if distributive:
        explicit_single = False
    if not explicit_single:
        return []
    draft = state.get("draft") or {}
    roots = [row for row in (draft.get("items") or []) if isinstance(row, dict)]
    children = [child for row in roots for child in (row.get("children") or [])
                if isinstance(child, dict)]
    total = len(roots) + len(children)
    if total != 1 or len(roots) != 1 or children:
        return [{
            "index": -1, "field": "cardinality", "source": "final_authority",
            "message": (f"사용자가 Sub-Task 1건을 지정했으나 최종 생성 이슈는 "
                        f"root {len(roots)}건 + child {len(children)}건"),
        }]
    row = roots[0]
    issue_type = str(row.get("type") or row.get("issue_type") or "").strip()
    normalized_type = issue_type.casefold().replace("-", "").replace(" ", "")
    if normalized_type != "subtask" or str(draft.get("mode") or "").casefold() != "subtask":
        return [{
            "index": 0, "field": "cardinality", "source": "final_authority",
            "message": ("정확히 1건 요청은 wrapper Task가 아니라 mode=subtask의 "
                        "Sub-Task 1건이어야 한다"),
        }]
    parent = str(row.get("parent") or "").strip().upper()
    if not parent or not _can_parent_subtask(parent):
        return [{
            "index": 0, "field": "cardinality", "source": "final_authority",
            "message": (f"정확히 1건의 Sub-Task 부모는 실재하는 Task-tier여야 하나 "
                        f"{parent or '비어 있음'}은 유효하지 않다"),
        }]
    return []


def _meeting_assignment_authority(state: AgentState) -> dict:
    """Project only source-backed meeting owners into a bounded typed authority."""
    if not is_meeting_request(state):
        return {
            "bindings": [], "identity_map": {}, "source_records": [],
            "canonical_people": {},
        }
    records = meeting_owner_records(state)
    people = resolved_people(state) if records else {}
    items = [row for row in ((state.get("draft") or {}).get("items") or [])
             if isinstance(row, dict)]
    bindings = meeting_assignment_bindings(items, records, people)
    canonical_ids = {
        spelling.casefold(): owner_id.casefold()
        for label, owner_id in people.items()
        for spelling in (str(label or "").strip(), str(owner_id or "").strip())
        if spelling and str(owner_id or "").strip()
    }
    relevant_ids = {
        canonical_ids.get(str(row.get("owner") or "").strip().casefold(), "")
        for row in records if str(row.get("owner") or "").strip()
    }
    relevant_ids.discard("")
    if records and is_meeting_request(state):
        relevant_ids.update(
            str(value or "").strip().casefold()
            for value in meeting_requester_instructors(state)
            if str(value or "").strip()
        )
    identity_map = {
        label: owner_id
        for label, owner_id in sorted(people.items(), key=lambda row: row[0].casefold())
        if owner_id.casefold() in relevant_ids
    }
    return {
        "bindings": bindings,
        "identity_map": identity_map,
        "source_records": records,
        "canonical_people": people,
    }


def _meeting_assignment_errors(state: AgentState) -> list[dict]:
    """Reject a final user-owned owner/due that differs from meeting source authority."""
    authority = _meeting_assignment_authority(state)
    records = authority["source_records"]
    if not records:
        return []
    items = [row for row in ((state.get("draft") or {}).get("items") or [])
             if isinstance(row, dict)]
    bindings = {row["item_id"]: row for row in authority["bindings"]}
    errors: list[dict] = meeting_assignment_source_mapping(items, records)[1]
    separate_authority = _separate_typed_meeting_scalars(state, items)
    unresolved_identity_indexes = {
        row.get("index") for row in errors
        if isinstance(row.get("index"), int) and row.get("index") >= 0
    }
    for index, item in enumerate(items):
        if index in unresolved_identity_indexes:
            continue
        if (str(item.get("meeting_assignment_ref") or "").strip()
                == NO_MEETING_ASSIGNMENT_REF):
            # A no-source sentinel rejects model/recommender scalars. Only a separately
            # typed current-request field can populate this root.
            expected = separate_authority.get(index) or {}
            expected_owner = str(expected.get("assignee") or "").strip()
            expected_source = str(expected.get("assignee_source") or "").strip()
            actual_owner = str(item.get("assignee") or "").strip()
            actual_source = str(item.get("assignee_source") or "").strip()
            if (actual_owner.casefold(), actual_source) != (
                    expected_owner.casefold(), expected_source):
                errors.append({
                    "index": index, "field": "assignee", "source": "meeting_assignment",
                    "expected": expected_owner or "empty", "actual": actual_owner or "empty",
                    "evidence": [NO_MEETING_ASSIGNMENT_REF],
                    "message": "no-source meeting root에 별도 typed 권위 없는 담당자가 남아 있다",
                })
            expected_due = str(expected.get("duedate") or "").strip()
            actual_due = str(item.get("duedate") or "").strip()
            if actual_due != expected_due:
                errors.append({
                    "index": index, "field": "duedate", "source": "meeting_assignment",
                    "expected": expected_due or "empty", "actual": actual_due or "empty",
                    "evidence": [NO_MEETING_ASSIGNMENT_REF],
                    "message": "no-source meeting root에 별도 typed 권위 없는 기한이 남아 있다",
                })
            continue
        source = str(item.get("assignee_source") or "").strip()
        if source not in {"user", "user_unassigned"}:
            continue
        item_id = str(item.get("item_id") or "").strip()
        if not item_id:
            errors.append({
                "index": index, "field": "item_id", "source": "meeting_assignment",
                "expected": "stable authored item identity", "actual": "missing",
                "evidence": ["source-backed meeting assignment"],
                "message": "회의 담당 결정을 검증할 stable item_id가 최종 payload에 없다",
            })
            continue
        binding = bindings.get(item_id)
        if not binding:
            errors.append({
                "index": index, "field": "assignee", "source": "meeting_assignment",
                "expected": "one canonical source assignment", "actual": (
                    str(item.get("assignee") or "").strip() or "unassigned"
                ),
                "evidence": ["bounded meeting assignment records"],
                "message": "사용자 지정 담당자를 회의 source assignment 하나와 연결할 수 없다",
            })
            continue
        expected_owner = str(binding.get("owner_id") or "").strip()
        actual_owner = str(item.get("assignee") or "").strip()
        expected_source = "user" if expected_owner else "user_unassigned"
        evidence = [json.dumps(binding.get("source_evidence") or {}, ensure_ascii=False,
                               sort_keys=True)]
        if source != expected_source or actual_owner.casefold() != expected_owner.casefold():
            errors.append({
                "index": index, "item_id": item_id, "field": "assignee",
                "source": "meeting_assignment", "expected": expected_owner or "unassigned",
                "actual": actual_owner or "unassigned", "evidence": evidence,
                "message": (f"회의 source 담당자는 {expected_owner or '미할당'}이나 "
                            f"최종 payload는 {actual_owner or '미할당'}"),
            })
        expected_due = str(binding.get("due") or "").strip()
        actual_due = str(item.get("duedate") or "").strip()
        if actual_due != expected_due:
            errors.append({
                "index": index, "item_id": item_id, "field": "duedate",
                "source": "meeting_assignment", "expected": expected_due or "empty",
                "actual": actual_due or "missing", "evidence": evidence,
                "message": (f"회의 source 기한은 {expected_due}이나 최종 payload는 "
                            f"{actual_due or '비어 있음'}"),
            })
    return errors


def _dedupe_errors(rows) -> list[dict]:
    result, seen = [], set()
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        identity = (row.get("index"), row.get("child_index"), row.get("field"),
                    str(row.get("message") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _typed_findings(state: AgentState, errors: list[dict],
                    problems: list[dict]) -> list[dict]:
    """Give every machine/model finding an order-independent authority-aware identity."""
    findings: list[dict] = []
    seen: set[str] = set()
    for rows, default_authority, accepts_typed_source in (
            (errors, "machine", True),
            (problems, "semantic_auditor", False)):
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            projected = typed_audit_findings(state, [raw])
            if not projected:
                continue
            finding = dict(projected[0])
            finding["authority"] = (
                str(raw.get("source") or default_authority)
                if accepts_typed_source else default_authority
            )
            key = finding_signature_key(finding)
            finding["finding_signature"] = f"finding:{key}"
            if finding["finding_signature"] in seen:
                continue
            seen.add(finding["finding_signature"])
            findings.append(finding)
    return findings


def _typed_review_contract(state: AgentState, review: dict) -> dict:
    """Attach stable finding identity and detect a repeated repair defect."""
    sealed = dict(review or {})
    errors = [row for row in (sealed.get("errors") or []) if isinstance(row, dict)]
    problems = [row for row in (sealed.get("problems") or []) if isinstance(row, dict)]
    findings = _typed_findings(state, errors, problems)
    signature = defect_signature_set(findings)
    action = continuation_action(state)
    container = ((state.get("draft") or {}) if action == "create"
                 else (state.get("change_plan") or {}))
    attempt = container.get("repair_attempt") if isinstance(container, dict) else {}
    prior_signature = str((attempt or {}).get("defect_signature") or "")
    current_keys = parse_defect_signature_set(signature)
    repeated_keys = recurrent_finding_signature_keys(findings, prior_signature)
    for finding in findings:
        finding["repeated"] = finding_signature_key(finding) in repeated_keys
    sealed["findings"] = findings
    sealed["payload_digest"] = payload_digest(state)
    sealed["defect_signature"] = signature
    sealed["repeated_defect"] = bool(current_keys and current_keys == repeated_keys)
    sealed["finding_contract"] = "audit-finding.v2"
    return sealed


def final_authority_review(state: AgentState, *,
                           locks: tuple[UserFieldLock, ...] = (),
                           require_effect: bool = False) -> dict:
    """Re-seal review against the final projected effect after every mutable merge."""
    view = project_final_authority_state(state)
    effect = final_effect(view)
    action = continuation_action(view)
    previous = dict(view.get("review") or {})
    errors = [dict(row) for row in (previous.get("errors") or [])
              if isinstance(row, dict) and row.get("source") != "final_authority"]
    warnings = [dict(row) for row in (previous.get("warnings") or []) if isinstance(row, dict)]
    problems = [dict(row) for row in (previous.get("problems") or []) if isinstance(row, dict)]

    if current_work_failed(view):
        errors.append({
            "index": -1, "field": "effect", "source": "final_authority",
            "message": "현재 Work Architect structured output이 실패해 실행 effect가 확정되지 않았다",
        })
    expected = {
        "create": {"create"}, "comment": {"comment"}, "update": {"update"},
        "read": {"none"}, "respond": {"none"},
    }.get(action)
    plan = view.get("change_plan") or {}
    transition_comment = bool(
        (plan.get("transition") or {}).get("id") and str(plan.get("comment") or "").strip()
    )
    if action == "mixed" and not (
        effect.kind == "update"
        and any(name in _UPDATE_EFFECT_ACTIONS for name in effect.actions)
        and (any(name.startswith("add_ticket_comment") for name in effect.actions)
             or transition_comment)
    ):
        # The existing executor intentionally binds update+comment to two fingerprints on one
        # approval card.  No equivalent atomic contract exists for create+change, or for a
        # ``mixed`` request whose final payload silently lost one side.  Those cases must be
        # split or clarified rather than approving a partial outcome.
        errors.append({
            "index": -1, "field": "effect", "source": "final_authority",
            "message": "mixed 요청의 최종 effect가 지원되는 update+comment 쌍이 아니어서 분할 확인이 필요하다",
        })
    if expected is not None and effect.kind not in expected:
        errors.append({
            "index": -1, "field": "effect", "source": "final_authority",
            "message": f"typed action은 {action}이나 최종 effect는 {effect.kind}",
        })
    if effect.kind == "conflict":
        errors.append({
            "index": -1, "field": "effect", "source": "final_authority",
            "message": "하나의 최종 승인 경계에 create와 change effect가 함께 남아 있다",
        })
    if require_effect and action in _WRITE_ACTIONS and effect.kind == "none" \
            and not view.get("questions"):
        errors.append({
            "index": -1, "field": "effect", "source": "final_authority",
            "message": f"{action} 요청에 승인 가능한 최종 effect가 없다",
        })

    if effect.kind == "create":
        if previous.get("ok") is not True:
            errors.append({
                "index": -1, "field": "review", "source": "final_authority",
                "message": "create effect에는 명시적인 pre-merge review.ok=true가 필요하다",
            })
        auto = _safe_machine_check(view)
        errors.extend({**dict(row), "source": "final_authority"}
                      for row in (auto.get("errors") or []) if isinstance(row, dict))
        warnings = _dedupe_errors([*warnings, *(auto.get("warnings") or [])])
        errors.extend(_locked_field_errors(view, locks))
        errors.extend(_typed_parent_errors(view))
        errors.extend(_typed_assignee_errors(view))
        errors.extend(_scoped_decision_errors(view))
        errors.extend(_cardinality_errors(view))
    elif effect.kind in {"comment", "update"} and previous.get("ok") is False:
        # Change flows do not run the semantic Auditor, so an absent verdict is accepted only
        # because this function is their deterministic machine-only review. An explicit red
        # verdict from this/current state remains red and cannot be promoted by omission.
        errors.append({
            "index": -1, "field": "review", "source": "final_authority",
            "message": "명시적으로 실패한 기존 review를 최종 change effect가 승격할 수 없다",
        })
    if effect.kind in {"comment", "update"}:
        errors.extend(_change_shape_errors(view))
        errors.extend(_typed_change_target_errors(view))
        errors.extend(_explicit_link_and_comment_errors(view))
    errors.extend(validate_requested_effect_contract(view))

    errors = _dedupe_errors(errors)
    ok = not errors and not problems and (previous.get("ok") is True
                                          if effect.kind == "create" else True)
    label = {"create": "생성", "comment": "댓글", "update": "변경",
             "none": "무효", "conflict": "충돌"}.get(effect.kind, effect.kind)
    summary = (f"최종 {label} effect 검증 통과"
               + (" — 필드·상태 변경 없음" if effect.kind == "comment" else "")
               if ok else f"최종 {label} effect 검증 보류 — 오류 {len(errors)}건")
    checks = dict(previous.get("checks") or {})
    checks["final_authority"] = ok
    return _typed_review_contract(view, {
        **previous,
        "ok": ok,
        "checks": checks,
        "errors": errors,
        "problems": problems,
        "warnings": warnings,
        "summary": summary,
        "final_authority": effect.as_dict(),
        "approval_contract": "deterministic_final_effect.v1",
    })


def _safe_machine_check(state: AgentState) -> dict:
    """Convert an unexpected validator crash into typed, incomplete, fail-closed evidence."""
    try:
        return _machine_check(state)
    except Exception as exc:
        return make_typed_check_result(
            authority=AUDITOR_MACHINE_AUTHORITY,
            payload_digest=payload_digest(state),
            complete=False,
            ok=False,
            errors=[{
                "index": -1,
                "field": "validation",
                "source": "machine",
                "message": f"기계 검증을 완료하지 못했다: {str(exc)[:160]}",
            }],
            warnings=[],
            text=f"검증을 수행하지 못했다: {str(exc)[:200]}",
        ).as_dict()


def _machine_result(state: AgentState) -> dict:
    """Reuse one common payload-bound check result within a semantic Auditor run."""
    cached = parse_typed_check_result(
        state.get(_MACHINE_RESULT_KEY),
        authority=AUDITOR_MACHINE_AUTHORITY,
        payload_digest=payload_digest(state),
    )
    return cached.as_dict() if cached else _safe_machine_check(state)


def _semantic_obligations_absent(state: AgentState) -> bool:
    """Fail closed when the semantic-obligation extractor is unavailable."""
    try:
        return not _verified_evidence_obligations(state)
    except Exception:
        return False


def _machine_negative_decision(state: AgentState, result: dict):
    """Bypass semantics only for a completed validator with concrete blocking rows."""
    checked = parse_typed_check_result(
        result,
        authority=AUDITOR_MACHINE_AUTHORITY,
        payload_digest=payload_digest(state),
    )
    return evaluate_typed_fast_path(
        "auditor.machine_negative.v1",
        checks={
            "structured_result": checked is not None,
            "validation_complete": bool(checked and checked.complete),
            "negative_verdict": bool(checked and checked.ok is False),
            "structured_blockers": bool(checked and checked.errors),
            # Presence/state checks can be machine-authored, but producer/artifact/consumer
            # role contradictions remain semantic and must retain the Auditor judgment pass.
            "semantic_obligations_absent": _semantic_obligations_absent(state),
        },
    )


class Auditor(StructuredAgent):
    name = Node.AUDITOR

    def node(self):
        base_run = super().node()

        def run(state):
            auto = _safe_machine_check(state)
            machine_negative = _machine_negative_decision(state, auto)
            if machine_negative.complete:
                repair_budget = advance_typed_repair_budget(state, "machine")
                review = _typed_review_contract(state, {
                    "ok": False,
                    "checks": {"machine_valid": False},
                    "repair_lane": "machine",
                    "problems": [],
                    "errors": [dict(row) for row in auto["errors"]],
                    "warnings": [dict(row) for row in (auto.get("warnings") or [])
                                 if isinstance(row, dict)],
                    "summary": f"기계 검증 보류 — 오류 {len(auto['errors'])}건",
                })
                return {
                    "review": review,
                    # This repair is driven entirely by typed machine findings. Preserve the
                    # semantic Auditor budget for the repaired payload's judgment pass.
                    "revisions": state.get("revisions") or 0,
                    **({"repair_budget": repair_budget.as_dict()}
                       if repair_budget is not None else {}),
                    "trace": typed_fast_path_note(
                        state, self.name, "보류(확정 기계 오류 · 의미 검열 호출 생략)",
                        machine_negative,
                    ),
                }
            # A machine-valid draft is not proof of request coverage.  The former compact
            # positive shortcut could approve a structurally valid draft that performed the
            # opposite action, so only concrete negative findings bypass semantic judgment.
            return base_run({**state, _MACHINE_RESULT_KEY: auto})

        return run

    def system(self, state):
        return persona(state, SYSTEM_AUDITOR, role_id=self.name)

    def task(self, state):
        auto = _machine_result(state)
        rules = _rules_for(state)
        grounding = _audit_grounding_contract(state)
        ev = "\n".join(f"- {e.get('key','')} {e.get('title','')}"
                       for e in (state.get("evidence") or []))
        # 담당자 제안은 여기 없다 — PeopleAdvisor 와 병렬로 돌기 때문. 근거 없는 배정은
        # merge_assignments 의 코드 가드가 걸러내므로 검열 대상에서 뺀다.
        data = wrap_data(
            data_block("Deterministic Validation Results (Authoritative)", auto["text"]),
            data_block("Authoritative Request and Draft State Contract",
                       json.dumps(grounding, ensure_ascii=False, default=str)),
            data_block("Applicable Authoring Rules", rules),
            data_block("Tickets Present in Verified Research", ev))
        return f"""\
# Task

Audit the complete ticket draft before it is shown to the user.

## Constraints

- Do not repeat defects already found by deterministic validation. Inspect only semantic problems code cannot decide.
- Treat the authoritative request/draft state contract as facts. A populated field is not missing.
- `parent_action=select_existing` means select/link an existing Epic, never create a new Epic.
- Assignment is validated separately; an empty assignee is not an audit defect.
- If reporting an assignment identity mismatch, set `finding_kind=field_mismatch`,
  `field=assignee`, and copy the exact expected/actual values. A display name and account id
  mapped to the same identity in `bounded_identity_map` are equal and must not be reported.
- Put only execution-blocking policy, grounding, or request-coverage failures in `problems`. Editorial suggestions for a better sentence, title, or DoD are not blocking.
- Preserve a Task/Sub-Task structure explicitly supplied or previously approved by the user.
- A Task-tier `Bug` is valid with the Korean sections `재현 경로`, `기대 동작`, and `실제 동작`; do not require generic Task background or DoD as well.
- A title need not end in a verb. An intentional top-level Task or Story without an Epic is valid.
- Reuse of one verified reference across multiple payload items is not blocking when it supports each item.
- Treat `evidence_obligations` as authoritative execution constraints. A completed result is a reusable baseline, not work to repeat; an unconfirmed dependency must remain unconfirmed; an approval or rollout gate must appear in scope and DoD; and existing validation work must be reused rather than duplicated. Preserve producer, artifact, and consumer roles exactly—a consumer under verification is not evidence that the consumer can generate the artifact. Omission or role/state reversal is a blocking `grounded` problem.
- Treat `requested_outcome_contract` as authoritative for the user-authored action, object, and explicit constraints. A singleton outcome may be served by several root items; when `outcome_groups` marks collective coverage, compare the required result with the union of those items and require each member to be a relevant contribution, rather than demanding that every title repeat the whole compound request. For an individually bound outcome, compare it with that item's title, scope, and DoD. A legacy planner instruction can contain examples or implementation choices that are absent from `Original User Request`; when the user delegated those choices, they are runtime-owned and cannot become missing user requirements. Evidence may refine implementation method or constraints, but omission or replacement of the user's action/object—including an opposite action—or an explicit acceptance/safety constraint is a blocking `request` problem. Never repair it by inventing intent.
- Audit every child in the authoritative contract too. `applicable_outcome_refs` is explicit when the child maps to another requested outcome and otherwise inherited from its parent. A legitimate design, implementation, validation, or rollout stage need not repeat the parent's action verb; block only a child that replaces/reverses the applicable requested result or introduces an unrelated deliverable.
- Write `message`, `fix`, and `summary` in Korean.

## Original User Request

The draft must preserve this subject; subject drift is a blocking request-coverage problem.

{_current_request_boundary_text(state)}

## Complete Draft Under Audit

{draft_full_text(state.get('draft')) or '(no draft)'}{data}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        auto = _machine_result(state)
        raw_problems = [p for p in (out.get("problems") or [])
                        if isinstance(p, dict) and p.get("message")]
        disproved_checks = {
            str(problem.get("check") or "")
            for problem in raw_problems
            if (_canonical_identity_false_finding(state, problem)
                or _runtime_owned_planner_detail_finding(state, problem))
        }
        problems, advice = _partition_model_problems(state, raw_problems)
        # A schema-valid projection can still lose the model's problem array while retaining
        # a negative axis boolean. Treating the empty array as authoritative would turn an
        # explicit semantic failure into review.ok=true. Preserve every negative axis as a
        # concrete blocking problem; if a corresponding problem survived projection, do not
        # duplicate it.
        synthetic = {
            "grounded": ("grounded", "근거 충족 여부를 확인하지 못했다",
                         "티켓·사람·날짜·주장을 검증된 근거와 다시 대조하라"),
            "rule_compliant": ("rule", "티켓 규칙 준수 여부를 확인하지 못했다",
                               "타입·계층·필수 필드 규칙을 다시 검증하라"),
            "answers_request": ("request", "사용자 요청 충족 여부를 확인하지 못했다",
                                "원 요청의 산출물·행동·대상을 초안과 다시 대조하라"),
        }
        for axis, (check, message, fix) in synthetic.items():
            if (out.get(axis) is False and check not in disproved_checks
                    and not any(p.get("check") == check for p in problems)):
                problems.append({"index": -1, "check": check,
                                 "message": message, "fix": fix})
        # boolean과 problems가 서로 어긋나는 모델 출력이 있다. 실행 차단은 구체적인
        # problem으로 설명 가능해야 하므로, 해당 축의 blocking problem 유무를 기준으로
        # 정규화한다. 기계 오류는 아래 auto["ok"]가 별도로 이긴다.
        checks = {
            "grounded": not any(p.get("check") == "grounded" for p in problems),
            "rule_compliant": not any(p.get("check") == "rule" for p in problems),
            "answers_request": not any(p.get("check") == "request" for p in problems),
        }
        # 완료 조건(DoD) 누락은 **한 번은 되돌려 보낸다** — 언제 끝난 것인지 못 박지 않은
        # 티켓은 나중에 아무도 닫지 못한다(실측: 배경·작업 범위만 쓰고 승인 카드까지 갔다).
        # 재작성 한도는 그래프가 쥐고 있으므로 무한 왕복은 나지 않는다.
        if (state.get("revisions") or 0) < 1:
            for w in auto["warnings"]:
                if ("완료 조건" in str(w.get("message") or "")
                        or "Bug 필수 섹션" in str(w.get("message") or "")):
                    problems.append({"index": w.get("index", -1),
                                     "message": w["message"],
                                     "fix": ("Bug 본문에 재현 경로·기대 동작·실제 동작을 "
                                             "모두 적어라" if "Bug 필수" in w["message"] else
                                             "본문에 '완료 조건 (DoD)' 섹션을 넣고 "
                                             "검증 가능한 불릿 2~5개를 적어라")})
        # 기계 판정이 이긴다 — 모델이 "문제없다"고 해도 validate_bulk 가 막으면 막힌 것이다.
        ok = auto["ok"] and all(checks.values()) and not problems
        advisory_warnings = [{"index": p.get("index", -1),
                              "message": "품질 참고(비차단): " + str(p.get("message") or "")}
                             for p in advice]
        summary = _normalize_delegated_parent_summary(
            state, str(out.get("summary") or "")
        )
        if ok and advice:
            summary = (f"정책·근거 검증 통과. 편집 제안 {len(advice)}건은 "
                       "비차단 참고로 남겼다.")
        elif ok and raw_problems and not problems:
            summary = "권위 상태와 모순된 모델 지적을 제외하고 정책·근거 검증 통과."
        review = _typed_review_contract(state, {
            "ok": ok, "checks": checks, "repair_lane": "semantic",
            "problems": problems,
            "errors": auto["errors"],
            "warnings": auto["warnings"] + advisory_warnings,
            "summary": summary,
        })
        failed = [k for k, v in checks.items() if not v]
        repair_budget = advance_typed_repair_budget(state, "semantic")
        return {"review": review,
                "revisions": (state.get("revisions") or 0) + 1,
                **({"repair_budget": repair_budget.as_dict()}
                   if repair_budget is not None else {}),
                "trace": note(state, self.name,
                              "통과" if ok else
                              f"보류 — 자동 {len(auto['errors'])}건 · 판단 {len(problems)}건"
                              + (f" ({', '.join(failed)})" if failed else ""))}


def _canonical_identity_false_finding(state: AgentState, problem: dict) -> bool:
    """Reject any assignee finding disproved by the canonical assignment binding.

    The model's ``expected`` and ``actual`` strings are both allegations.  Once a stable
    work item is uniquely bound to a source assignment and the authored payload matches that
    binding, neither string may override the machine authority.  A genuinely wrong payload
    remains blocking through ``_meeting_assignment_errors`` below.
    """
    if (problem.get("finding_kind") != "field_mismatch"
            or problem.get("field") != "assignee"):
        return False
    if not str(problem.get("expected") or "").strip() \
            or not str(problem.get("actual") or "").strip():
        return False
    authority = _meeting_assignment_authority(state)
    bindings = authority["bindings"]
    if not bindings:
        return False
    index = problem.get("index", -1)
    items = [row for row in ((state.get("draft") or {}).get("items") or [])
             if isinstance(row, dict)]
    if not isinstance(index, int) or not 0 <= index < len(items):
        return False
    item = items[index]
    if str(item.get("assignee_source") or "") not in {
            "user", "user_unassigned"}:
        return False
    item_id = str(item.get("item_id") or "")
    binding = next((row for row in bindings if row["item_id"] == item_id), None)
    if not binding or any(
            row.get("index") == index and row.get("field") == "assignee"
            for row in _meeting_assignment_errors(state)):
        return False
    authored_id = str(item.get("assignee") or "").strip().casefold()
    bound_id = str(binding.get("owner_id") or "").strip().casefold()
    return authored_id == bound_id


def _request_plan_terms(state: AgentState) -> set[str]:
    """Return bounded semantic terms authored by RequestArchitect, never evidence prose."""
    plan = state.get("request_plan") or {}
    values: list[str] = []
    for task in (plan.get("tasks") or [])[:6]:
        if not isinstance(task, dict):
            continue
        values.append(str(task.get("instruction") or ""))
        values.extend(str(row or "") for row in (task.get("completion_criteria") or [])[:3])
    values.extend(str(row or "") for row in (plan.get("assumptions") or [])[:5])
    return outcome_authority_terms("\n".join(values))


def _runtime_owned_planner_detail_finding(state: AgentState, problem: dict) -> bool:
    """Identify a typed request finding whose requirement exists only in the plan.

    RequestArchitect is a projector, not a second user.  An implementation preference
    introduced solely by that projector may be selected by Work and shown on the approval
    card; it cannot suspend the workflow whether or not the user used a particular delegation
    phrase. This guard deliberately fails closed unless the finding supplies a typed
    expected/actual relation, and the complete draft visibly preserves the original request's
    subject.
    User-authored acceptance/safety terms therefore remain blocking,
    as do rule, grounding, hierarchy, identity, and vague-target findings.
    """
    if (str(problem.get("check") or "") != "request"
            or str(problem.get("finding_kind") or "") not in {
                "request_coverage", "missing_requirement",
            }):
        return False

    request = _current_request_boundary_text(state)
    expected = str(problem.get("expected") or "").strip()
    actual = str(problem.get("actual") or "").strip()
    if not request or not expected or not actual:
        return False

    request_terms = outcome_authority_terms(request)
    expected_terms = outcome_authority_terms(expected)
    actual_terms = outcome_authority_terms(actual)
    planner_terms = _request_plan_terms(state)
    draft_terms = outcome_authority_terms(draft_full_text(state.get("draft")))
    if not all((request_terms, expected_terms, actual_terms, planner_terms, draft_terms)):
        return False

    # The finding must describe the visible draft truthfully enough to be scoped to the
    # otherwise-complete outcome, not an unrelated or opposite artifact.
    if len(actual_terms & draft_terms) < min(2, len(actual_terms)):
        return False
    if len(request_terms & draft_terms) < min(2, len(request_terms)):
        return False

    planner_aligned = expected_terms & planner_terms
    planner_only = planner_aligned - request_terms
    # Two terms plus majority dominance avoids reclassifying a one-word field omission or a
    # user-authored requirement merely because the planner paraphrased one adjacent word.
    return (len(planner_only) >= 2
            and len(planner_only) * 2 > len(planner_aligned)
            and len(expected_terms & request_terms) < len(planner_only))


def _partition_model_problems(state: AgentState, problems: list) -> tuple[list, list]:
    """정책 차단과 편집 조언을 나눈다.

    LLM Auditor가 실제 Jira/LTM 제약이 아닌 문체 취향을 `problems`로 올리면 불필요한
    WorkArchitect 왕복이 생기고, 한도 뒤에는 정상 초안도 `review.ok=false`로 남는다. 아래는
    관찰된 취향성 오판만 좁게 비차단으로 내린다. 근거·부모 계층·요청 누락은 건드리지 않는다.
    """
    draft = state.get("draft") or {}
    items = [i for i in (draft.get("items") or []) if isinstance(i, dict)]
    has_bug = any(str(i.get("type") or "").strip().lower() == "bug" for i in items)
    req = _current_request_boundary_text(state)
    explicit_shape = (draft.get("structure_source") == "user_specified"
                      or bool(state.get("structure_ok"))
                      or "이 구조로 진행" in req
                      or any(w in req for w in ("단계별 Sub-Task", "사람 나눠서")))
    blocking, advice, seen = [], [], set()
    for problem in problems:
        if _canonical_identity_false_finding(state, problem):
            continue
        if _runtime_owned_planner_detail_finding(state, problem):
            advice.append(problem)
            continue
        if _unverified_delegated_parent_claim(state, problem):
            # A model cannot turn absence from the bounded candidate ledger into proof that
            # a Jira key does not exist, nor recommend a search hit whose Epic detail was not
            # opened. Deterministic validation emits the actionable parent error below.
            continue
        if _problem_contradicts_authoritative_state(state, problem):
            continue
        msg = str(problem.get("message") or "")
        raw_index = problem.get("index", -1)
        fingerprint = (str(problem.get("check") or ""),
                       int(raw_index) if isinstance(raw_index, int) else -1,
                       " ".join(msg.casefold().split()))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        advisory = False
        if has_bug and any(w in msg for w in ("배경", "완료 조건", "DoD")):
            advisory = True
        elif any(w in msg for w in ("담당자", "사번", "사용자")) and any(
                w in msg for w in ("존재하지", "확인되지", "실재하지", "찾을 수 없")):
            # 담당 사용자 실재 여부는 merge_assignments 뒤 코드가 bulk lookup으로 확정한다.
            advisory = True
        elif explicit_shape and any(w in msg for w in ("과잉 분해", "불필요하게 나뉘")):
            advisory = True
        elif "참고 섹션" in msg and "중복" in msg:
            advisory = True
        elif "Sub-Task" in msg and "부모" in msg and "중복" in msg:
            advisory = True
        elif any(w in msg for w in (
                "동사로 끝", "제목이 명확하지", "제목이 구체적이지",
                "완료 조건이 명확하지", "완료 조건이 구체적이지", "판정 가능하지",
                "Epic 배치가 명시되지", "Epic 배치가 누락")):
            advisory = True
        (advice if advisory else blocking).append(problem)
    return blocking, advice


def _unverified_delegated_parent_claim(state: AgentState, problem: dict) -> bool:
    """Reject model-only parent existence claims and unverified replacement keys."""
    if not _delegates_existing_epic_choice(state):
        return False
    text = " ".join(str(problem.get(key) or "") for key in ("message", "fix"))
    folded = text.casefold()
    parent_language = any(
        token in folded for token in (
            "parent", "상위", "부모", "연결", "배치", "아래", "하위",
        )
    )
    candidates = {
        str(row.get("key") or "").strip().upper()
        for row in verified_parent_epic_candidates(state)
    }
    mentioned = {
        match.group(1).upper()
        for match in _re.finditer(
            r"(?<![A-Z0-9])([A-Z][A-Z0-9]*-\d+)(?![A-Z0-9])", text, _re.I,
        )
    }
    unsupported_key = bool(mentioned - candidates)
    unsupported_existence = any(token in folded for token in (
        "존재하지", "실재하지", "찾을 수 없", "검색 결과에", "확인되지 않",
    ))
    # A terse summary can omit the word Epic while retaining the same unsupported
    # ``DL-x does not exist; use DL-y`` assertion. Ticket keys plus an existence claim are
    # enough to identify that model-only parent conclusion inside a delegated-parent audit.
    return (parent_language and unsupported_key) or (unsupported_existence and bool(mentioned))


def _normalize_delegated_parent_summary(state: AgentState, summary: str) -> str:
    """Replace epistemically invalid model wording with the candidate-ledger contract."""
    text = str(summary or "")
    if not _unverified_delegated_parent_claim(
            state, {"message": text, "fix": ""}):
        return text
    candidates = [
        str(row.get("key") or "").strip().upper()
        for row in verified_parent_epic_candidates(state)
        if str(row.get("key") or "").strip()
    ]
    if candidates:
        return (
            "상위 Epic 자동 선택은 현재 조회에서 상세 확인된 기존 Epic 후보("
            + ", ".join(candidates) + ")만 사용할 수 있다."
        )
    return (
        "현재 조회에서 상세 확인된 기존 Epic 후보가 없어 parent를 비우거나 "
        "후보 조회를 갱신해야 한다."
    )


def _request_parent_action(state: AgentState) -> str:
    """Classify only explicit Epic relationship language; never infer from a plan."""
    said = _current_request_boundary_text(state)
    # RequestArchitect already owns the exact distinction, including the important
    # "choose one; create only if none exists" fallback. Reuse it so audit cannot silently
    # reinterpret a fallback-create request as selection-only.
    try:
        from app.agent.workflow.agents.request_architect import (
            _EPIC_CREATION, _FALLBACK_CREATION, _selection_is_not_creation,
        )
        selection_only = _selection_is_not_creation(said)
        create = bool(_EPIC_CREATION.search(said) or _FALLBACK_CREATION.search(said))
    except Exception:
        selection_only = bool(_re.search(
            r"(?:Epic|에픽)[^.!?\n]{0,32}(?:골라|선택|찾아|정해|붙여|연결)",
            said, _re.I,
        ))
        create = bool(_re.search(
            r"(?:Epic|에픽)[^.!?\n]{0,24}(?:생성|만들)|"
            r"(?:생성|만들)[^.!?\n]{0,24}(?:Epic|에픽)", said, _re.I,
        ))
    if selection_only:
        return "select_existing"
    if create:
        return "create_new"
    if _re.search(r"\b[A-Z][A-Z0-9]*-\d+\b", said, _re.I) \
            and _re.search(r"Epic|에픽|상위|아래|밑에", said, _re.I):
        return "use_explicit_existing"
    return "unspecified"


def _draft_asserts_new_epic_creation(draft: dict) -> bool:
    """Detect an authored new-Epic action anywhere in the pending draft.

    ``mode=task`` only proves the payload's current root type; it does not prove that the
    title/body/rationale obeys a select-existing request.  In particular, treating that
    typed mode as compliance caused the Auditor to discard a correct finding about prose
    that promised a new Epic.  Inspect all authored draft text and children before allowing
    any request-intent contradiction filter to suppress a finding.
    """
    values = [str((draft or {}).get("rationale") or "")]

    def collect(item: dict) -> None:
        values.extend(str(item.get(key) or "") for key in ("summary", "description"))
        for child in item.get("children") or []:
            if isinstance(child, dict):
                collect(child)

    for item in (draft or {}).get("items") or []:
        if isinstance(item, dict):
            collect(item)
    text = "\n".join(values)
    # HTML block boundaries and sentence boundaries keep a nearby negation from masking a
    # different positive statement elsewhere in the draft.
    segments = _re.split(r"(?:</(?:p|li|h[1-6])>|[.!?\n])", text, flags=_re.I)
    creation = _re.compile(
        r"(?:새(?:로운)?\s*)?(?:Epic|에픽).{0,36}(?:생성|만들|create|make)"
        r"|(?:생성|만들|create|make).{0,36}(?:새(?:로운)?\s*)?(?:Epic|에픽)",
        _re.I,
    )
    negated = _re.compile(
        r"(?:생성|만들)(?:지\s*않|지\s*말|지\s*않기로|\s*안\s*|\s*금지|\s*제외|\s*보류)"
        r"|(?:do\s+not|don't|not|never|without)\s+(?:create|make)",
        _re.I,
    )
    return any(creation.search(segment) and not negated.search(segment)
               for segment in segments)


def _audit_grounding_contract(state: AgentState) -> dict:
    """Minimal typed facts that semantic audit may not reinterpret."""
    draft = state.get("draft") or {}
    rows = []

    def compact_body(value: str) -> str:
        plain = unescape(_re.sub(r"<[^>]+>", " ", str(value or "")))
        return " ".join(plain.split())[:700]

    for index, item in enumerate(draft.get("items") or []):
        if not isinstance(item, dict):
            continue
        parent_refs = [str(value) for value in (item.get("outcome_refs") or [])
                       if str(value)]
        children = []
        for child_index, child in enumerate(item.get("children") or []):
            if not isinstance(child, dict):
                continue
            explicit_refs = [str(value) for value in (child.get("outcome_refs") or [])
                             if str(value)]
            children.append({
                "index": child_index,
                "type": str(child.get("type") or child.get("issue_type") or "Sub-Task"),
                "summary": str(child.get("summary") or ""),
                "scope_and_dod": compact_body(child.get("description") or ""),
                "outcome_refs": explicit_refs,
                "applicable_outcome_refs": explicit_refs or parent_refs,
                "outcome_binding_source": ("explicit" if explicit_refs
                                           else "inherited_from_parent"),
            })
        rows.append({
            "index": index,
            "item_id": str(item.get("item_id") or ""),
            "type": str(item.get("type") or item.get("issue_type") or ""),
            "summary": str(item.get("summary") or ""),
            "epic": str(item.get("epic") or ""),
            "parent": str(item.get("parent") or ""),
            "duedate": str(item.get("duedate") or ""),
            "assignee": str(item.get("assignee") or ""),
            "assignee_source": str(item.get("assignee_source") or ""),
            "scope_and_dod": compact_body(item.get("description") or ""),
            "outcome_refs": parent_refs,
            "child_count": len(children),
            "children": children,
        })
    grouped: dict[str, dict] = {}
    for row in rows:
        for outcome_ref in row.get("outcome_refs") or []:
            group = grouped.setdefault(outcome_ref, {
                "outcome_ref": outcome_ref, "item_ids": [], "indexes": [],
                "coverage": "collective",
            })
            group["item_ids"].append(row["item_id"])
            group["indexes"].append(row["index"])
    outcome_groups = [group for group in grouped.values() if len(group["indexes"]) > 1]

    typed_epic = (str(draft.get("mode") or "task").casefold() == "epic"
                  or any(row["type"].casefold() == "epic" for row in rows))
    textual_epic = _draft_asserts_new_epic_creation(draft)
    meeting_authority = _meeting_assignment_authority(state)
    return {
        "parent_action": _request_parent_action(state),
        "draft_mode": str(draft.get("mode") or "task"),
        "draft_creates_epic": typed_epic or textual_epic,
        "draft_asserts_new_epic_creation": textual_epic,
        "requested_outcome_contract": requested_outcome_contract(state),
        "draft_outcome_contract_id": str(draft.get("outcome_contract_id") or ""),
        "outcome_groups": outcome_groups,
        "evidence_obligations": (draft.get("evidence_obligations")
                                 or _verified_evidence_obligations(state)),
        "meeting_assignment_bindings": meeting_authority["bindings"],
        "bounded_identity_map": meeting_authority["identity_map"],
        "items": rows,
    }


def _problem_contradicts_authoritative_state(state: AgentState, problem: dict) -> bool:
    """Drop a semantic finding only when request *and complete draft* disprove it.

    Request intent is an audit rule, never evidence that the draft complied with the rule.
    """
    facts = _audit_grounding_contract(state)
    text = " ".join(str(problem.get(key) or "") for key in ("message", "fix"))
    folded = " ".join(text.casefold().split())
    if (facts["parent_action"] == "select_existing"
            and not facts["draft_creates_epic"]
            and ("epic" in folded or "에픽" in folded)
            and any(word in folded for word in ("생성", "만들", "create"))):
        return True

    rows = facts["items"]
    index = problem.get("index", -1)
    has_exact_scope = isinstance(index, int) and 0 <= index < len(rows)
    scoped = [rows[index]] if has_exact_scope else rows

    def populated(predicate) -> bool:
        """A global missing claim is disproved only when every root has the field."""
        values = [bool(predicate(row)) for row in scoped]
        return bool(values) and (values[0] if has_exact_scope else all(values))

    missing_claim = any(word in folded for word in (
        "없", "누락", "비어", "명시되어 있지", "설정되지", "not present", "missing",
    ))
    if missing_claim and any(word in folded for word in ("마감", "기한", "due")) \
            and populated(lambda row: row.get("duedate")):
        return True
    if missing_claim and ("epic" in folded or "에픽" in folded or "상위" in folded) \
            and populated(lambda row: row.get("epic") or row.get("parent")):
        return True
    if missing_claim and any(word in folded for word in ("제목", "summary", "요약")) \
            and populated(lambda row: row.get("summary")):
        return True
    return False


_ISSUE_TYPE_TOKEN = _re.compile(
    r"(?<![A-Za-z가-힣])(Bug|버그|Story|스토리|Feature|피처|Improvement|임프로브먼트)"
    r"(?=$|[^A-Za-z가-힣]|(?:를|을|로|은|는|와|과)(?=\s|[,.;:!?]|$))", _re.I,
)
_ISSUE_TYPE_CANONICAL = {
    "bug": "Bug", "버그": "Bug", "story": "Story", "스토리": "Story",
    "feature": "Feature", "피처": "Feature",
    "improvement": "Improvement", "임프로브먼트": "Improvement",
}
_CREATE_ACTION = _re.compile(r"만들|생성|등록|올려", _re.I)


def _explicit_issue_type_mentions(text: str) -> list[dict]:
    """Return issue types explicitly participating in a create instruction.

    A lone type is authoritative only when its suffix directly forms a create phrase. In a
    multi-type list (``Bug 1건과 Story 1건 만들어``), every type token in that create clause
    is retained so the caller can map them per root instead of applying the first globally.
    """
    source = str(text or "")
    all_mentions = [{
        "type": _ISSUE_TYPE_CANONICAL[match.group(1).casefold()],
        "start": match.start(), "end": match.end(),
    } for match in _ISSUE_TYPE_TOKEN.finditer(source)]
    if not all_mentions:
        return []
    unique = {row["type"] for row in all_mentions}
    if len(unique) > 1 and _CREATE_ACTION.search(source):
        return all_mentions
    direct = []
    for row in all_mentions:
        tail = source[row["end"]:row["end"] + 40]
        if _re.match(
            r"\s*(?:를|을|로)?\s*(?:\d+\s*건|한\s*건|두\s*건|하나)?\s*"
            r"(?:만\s*)?(?:만들|생성|등록|올려)", tail, _re.I,
        ):
            direct.append(row)
    return direct


def _type_subject_terms(value: str) -> set[str]:
    stop = {
        "건과", "건와", "그리고", "각각", "만들고", "만들어", "만들어줘",
        "생성", "생성해", "등록", "올려", "작업", "티켓", "task",
        "bug", "story", "feature", "improvement",
    }
    return {token.casefold() for token in _re.findall(
        r"[A-Za-z][A-Za-z0-9_.-]{1,}|[가-힣]{2,}", str(value or ""),
    ) if token.casefold() not in stop and not token.isdigit()}


def _visible_multi_type_mapping(material: str, mentions: list[dict],
                                roots: list[dict]) -> dict[int, str]:
    """Map postfixed type clauses to visible root summaries only on a literal bijection."""
    if len(mentions) != len(roots) or len(mentions) < 2:
        return {}
    subjects = []
    prior_end = 0
    for mention in mentions:
        subjects.append(_type_subject_terms(material[prior_end:mention["start"]]))
        prior_end = mention["end"]
    root_terms = [_type_subject_terms(str(row.get("summary") or "")) for row in roots]
    mapping: dict[int, str] = {}
    used: set[int] = set()
    for index, terms in enumerate(subjects):
        siblings = set().union(*(other for pos, other in enumerate(subjects) if pos != index))
        distinctive = terms - siblings
        if not distinctive:
            return {}
        candidates = [root_index for root_index, values in enumerate(root_terms)
                      if distinctive <= values]
        if len(candidates) != 1 or candidates[0] in used:
            return {}
        used.add(candidates[0])
        mapping[candidates[0]] = mentions[index]["type"]
    return mapping if len(mapping) == len(roots) else {}


def _expected_issue_types_by_root(state: AgentState, roots: list[dict]) -> dict[int, str]:
    """Resolve exact issue types without turning a multi-type request into a global type."""
    material = _current_request_boundary_text(state)
    mentions = _explicit_issue_type_mentions(material)
    unique = {row["type"] for row in mentions}
    if len(unique) == 1:
        expected = next(iter(unique))
        return {index: expected for index in range(len(roots))}
    if len(unique) < 2:
        return {}

    contract = requested_outcome_contract(state)
    outcome_types = {}
    for outcome in contract.get("outcomes") or []:
        values = {row["type"] for row in _explicit_issue_type_mentions(
            str(outcome.get("instruction") or ""))}
        if len(values) == 1:
            outcome_types[str(outcome.get("id") or "")] = next(iter(values))
    mapped = {}
    for index, root in enumerate(roots):
        values = {outcome_types[ref] for ref in (
            str(value) for value in (root.get("outcome_refs") or [])
        ) if ref in outcome_types}
        if len(values) == 1:
            mapped[index] = next(iter(values))
    if len(mapped) == len(roots):
        return mapped
    # Outcome ids may be unavailable in legacy/direct drafts. Fall back only when visible
    # root subjects establish the same one-to-one literal mapping.
    return _visible_multi_type_mapping(material, mentions, roots)


def _deterministic_request_field_errors(state: AgentState, roots: list[dict]) -> list[dict]:
    """Check exact user-owned fields that semantic review must never reinterpret."""
    errors: list[dict] = []

    ordinals = [value for value in required_user_anchors(state) if is_ordinal(value)]
    if len(roots) == 1 and len(ordinals) == 1:
        expected = ordinals[0]
        expected_number = _re.match(r"(\d+)", expected).group(1)
        rows = [roots[0], *[
            child for child in (roots[0].get("children") or []) if isinstance(child, dict)
        ]]
        bare = _re.compile(
            r"(?<![0-9A-Za-z가-힣_])차(?=\s|[—–\-:·,.;!?()\[\]{}]|$)", _re.I)
        for index, row in enumerate(rows):
            visible = unescape(_re.sub(
                r"<[^>]+>", " ",
                f"{row.get('summary') or ''} {row.get('description') or ''}",
            ))
            explicit_numbers = set(_re.findall(r"(?<!\d)(\d{1,3})\s*차", visible))
            conflicts = sorted(number for number in explicit_numbers
                               if number != expected_number)
            if bare.search(visible):
                errors.append({
                    "index": index,
                    "field": "ordinal",
                    "message": (f"사용자 지정 범위 {expected}에서 숫자 없는 bare '차'가 "
                                "root/child 표시에 사용됐다"),
                })
            elif conflicts:
                rendered = ", ".join(f"{number}차" for number in conflicts)
                errors.append({
                    "index": index,
                    "field": "ordinal",
                    "message": (f"사용자 지정 범위 {expected}와 충돌하는 {rendered}가 "
                                "root/child 표시에 사용됐다"),
                })
            elif index == 0 and expected_number not in explicit_numbers:
                errors.append({
                    "index": 0,
                    "field": "ordinal",
                    "message": f"root 표시에 사용자 지정 범위 {expected}가 누락됐다",
                })

    hierarchy_ordinals = _explicit_hierarchical_ordinal_contract(state)
    if len(roots) == 1 and hierarchy_ordinals:
        hierarchy_rows = [("root", 0, roots[0], hierarchy_ordinals["root"])]
        hierarchy_rows.extend(
            ("child", index, child, hierarchy_ordinals["child"])
            for index, child in enumerate((roots[0].get("children") or []), start=1)
            if isinstance(child, dict)
        )
        bare = _re.compile(
            r"(?<![0-9A-Za-z가-힣_])차(?=\s|[—–\-:·,.;!?()\[\]{}]|$)", _re.I)
        for tier, index, row, expected in hierarchy_rows:
            # The summary is the issue's visible phase ownership label. Descriptions may
            # legitimately mention both phases as background/dependencies, so they are not
            # used to infer ownership here.
            summary = unescape(_re.sub(r"<[^>]+>", " ", str(row.get("summary") or "")))
            explicit = {
                f"{int(number)}차"
                for number in _re.findall(r"(?<!\d)(\d{1,3})\s*차", summary)
            }
            conflicts = sorted(value for value in explicit if value != expected)
            if bare.search(summary):
                message = f"{tier} 표시에 숫자 없는 bare '차'가 사용됐다"
            elif expected not in explicit or conflicts:
                actual = ", ".join(sorted(explicit)) or "비어 있음"
                message = (f"{tier} 표시는 사용자 지정 범위 {expected}여야 하나 "
                           f"초안은 {actual}")
            else:
                continue
            errors.append({"index": index, "field": "ordinal", "message": message})

    for index, expected_type in _expected_issue_types_by_root(state, roots).items():
        row = roots[index]
        actual = str(row.get("type") or row.get("issue_type") or "").strip()
        if actual.casefold() != expected_type.casefold():
            errors.append({
                "index": index, "field": "type",
                "message": f"사용자가 {expected_type}를 지정했으나 초안 타입은 {actual or '비어 있음'}",
            })

    explicit_parents = _expected_parent_epics_by_root(state, roots)
    explicit_parent = _explicit_parent_epic(state)
    if explicit_parents:
        for index, expected_parent in explicit_parents.items():
            row = roots[index]
            actual = str(row.get("epic") or row.get("parent") or "").strip().upper()
            if actual != expected_parent.upper():
                errors.append({
                    "index": index, "field": "parent",
                    "message": (f"해당 요청 결과의 상위 Epic은 {expected_parent}이나 "
                                f"초안은 {actual or '비어 있음'}"),
                })
    elif explicit_parent:
        for index, row in enumerate(roots):
            actual = str(row.get("epic") or row.get("parent") or "").strip().upper()
            if actual != explicit_parent.upper():
                errors.append({
                    "index": index, "field": "parent",
                    "message": f"사용자 지정 상위 Epic은 {explicit_parent}이나 초안은 {actual or '비어 있음'}",
                })
    elif _delegates_existing_epic_choice(state) and "materialized_ticket_sources" in state:
        candidates = {
            str(row.get("key") or "").strip().upper()
            for row in verified_parent_epic_candidates(state)
            if str(row.get("key") or "").strip()
        }
        for index, row in enumerate(roots):
            actual = str(row.get("epic") or row.get("parent") or "").strip().upper()
            invalid = (actual not in candidates if candidates else bool(actual))
            if invalid:
                errors.append({
                    "index": index, "field": "parent",
                    "message": (
                        f"초안 parent {actual}은 현재 조회에서 상세 확인된 기존 Epic "
                        f"후보({', '.join(sorted(candidates))})에 포함되지 않는다"
                        if candidates else
                        f"현재 조회에서 상세 확인된 기존 Epic 후보가 없어 초안 parent "
                        f"{actual}의 연결 근거를 확인할 수 없다"
                    ),
                })
    return errors


def _machine_check_output(
    state: AgentState,
    *,
    complete: bool,
    ok: bool,
    errors,
    warnings,
    text: str,
) -> dict:
    return make_typed_check_result(
        authority=AUDITOR_MACHINE_AUTHORITY,
        payload_digest=payload_digest(state),
        complete=complete,
        ok=ok,
        errors=errors,
        warnings=warnings,
        text=text,
    ).as_dict()


def _machine_check(state: AgentState) -> dict:
    """`domain/bulk.validate_bulk` — 화면의 Bulk 생성과 **같은 규칙**. LLM 을 거치지 않는다."""
    draft = state.get("draft") or {}
    items = as_bulk_items(draft)
    contract_errors = validate_draft_outcome_contract(state, draft)
    due_errors = []
    roots = [item for item in (draft.get("items") or []) if isinstance(item, dict)]
    field_errors = _deterministic_request_field_errors(state, roots)
    obligation_errors = _evidence_obligation_errors(state, draft)
    assignment_errors = _meeting_assignment_errors(state)
    per_outcome_due = _expected_due_dates_by_root(state, roots)
    due_status, due_literal = _explicit_due_instruction_status(state)
    expected_due = _authoritative_explicit_due(state)
    if per_outcome_due:
        for index, expected in per_outcome_due.items():
            actual_due = str(roots[index].get("duedate") or "").strip()
            if actual_due != expected:
                due_errors.append({
                    "index": index, "field": "duedate",
                    "message": (f"해당 요청 결과의 마감일은 {expected}이나 초안은 "
                                f"{actual_due or '비어 있음'}"),
                })
    elif due_status in {"invalid", "ambiguous"}:
        due_errors.append({
            "index": 0 if roots else -1,
            "field": "duedate",
            "message": (
                (f"사용자 지정 마감일 {due_literal}은 유효하지 않다"
                 if due_status == "invalid" and due_literal
                 else "사용자가 서로 다른 마감일을 지정해 하나로 확정할 수 없다")
                + " — 유효한 단일 날짜를 확인하기 전에는 승인할 수 없다"
            ),
        })
    elif due_status == "clear" and any(str(row.get("duedate") or "").strip() for row in roots):
        due_errors.append({
            "index": 0, "field": "duedate",
            "message": "사용자가 마감일 제거를 요청했으나 초안에 날짜가 남아 있다",
        })
    elif expected_due and (len(roots) == 1
                           or _global_exact_due_for_roots(state, len(roots))):
        for index, root in enumerate(roots):
            actual_due = str(root.get("duedate") or "").strip()
            if actual_due == expected_due:
                continue
            due_errors.append({
                "index": index,
                "field": "duedate",
                "message": (f"사용자 지정 마감일은 {expected_due}이나 초안은 "
                            f"{actual_due or '비어 있음'} — exact date를 그대로 보존해야 한다"),
            })
    if not items:
        return _machine_check_output(
            state, complete=True, ok=False,
            errors=(contract_errors + due_errors + field_errors
                    + obligation_errors + assignment_errors),
            warnings=[], text="초안이 비어 있다.",
        )
    # Epic 은 Bulk 규칙(validate_bulk)의 대상이 아니다 — 요약만 확인하고 통과.
    # (Epic Link·타입·SP 규칙은 전부 자식 티켓 이야기다.)
    if (draft.get("mode") or "task") == "epic":
        ok = bool((items[0].get("summary") or "").strip())
        errors = ([] if ok else [{"index": 0, "field": "summary",
                                  "message": "Epic 요약이 비었다"}]) \
            + contract_errors + due_errors + field_errors + obligation_errors \
            + assignment_errors
        return _machine_check_output(
            state, complete=True, ok=ok and not errors, errors=errors, warnings=[],
            text="Epic 초안 — 기계 검증 대상 아님(요약 확인만).",
        )
    try:
        from app.agent.tools._ctx import client
        from app.domain.bulk import validate_bulk
        r = validate_bulk(draft.get("mode") or "task", items, client().bulk_lookup())
    except Exception as e:
        return _machine_check_output(
            state, complete=False, ok=False,
            errors=([{"index": -1, "field": "validation", "message": str(e)[:200]}]
                    + contract_errors + due_errors + field_errors + obligation_errors
                    + assignment_errors),
            warnings=[], text=f"검증을 수행하지 못했다: {str(e)[:200]}",
        )
    warnings = list(r.get("warnings") or [])
    # ★ 본문 접지 — 챗 답변에만 걸던 grounding 을 **티켓 본문에도** 건다. 없는 키·틀린
    #   제목이 티켓에 박제되면 동적 RAG 가 그 날조를 다음 조사에서 다시 수확한다(실측:
    #   본문의 날조는 어떤 검사도 안 거치고 통과했다). 실패는 경고로 — 판단은 사람이.
    try:
        from app.agent.workflow import grounding
        body = " ".join(str(i.get("description") or "") + " " + str(i.get("summary") or "")
                        for i in items)
        g = grounding.check(body)
        if not g.get("ok"):
            for k in (g.get("fake_keys") or [])[:5]:
                warnings.append({"index": -1, "message": f"본문의 {k} 는 실재하지 않는 티켓이다"})
            for k, t in list((g.get("wrong_titles") or {}).items())[:3]:
                warnings.append({"index": -1, "message": f"본문의 {k} 제목이 실제와 다르다: {t}"})
    except Exception:
        pass
    # ★ 본문 골격 — 완료 조건(DoD)이 없는 티켓은 "언제 끝난 것인지" 아무도 모른다.
    #   knowledge/07 이 정한 4섹션 중 이것만 유독 잘 빠진다(실측: 배경·작업 범위만 쓰고
    #   DoD 없이 승인 카드까지 갔다). 경고로 올려 재작성 루프가 채우게 한다.
    for i, it in enumerate(items):
        desc = str(it.get("description") or "")
        if not desc.strip():
            continue
        if str(it.get("type") or "").strip().lower() == "bug":
            missing = [name for name in ("재현 경로", "기대 동작", "실제 동작")
                       if name not in desc]
            if missing:
                warnings.append({"index": i, "message":
                                 "Bug 필수 섹션이 없다: " + ", ".join(missing)})
            continue
        if not _re.search(r"완료\s*조건|DoD|Definition of Done", desc, _re.I):
            warnings.append({"index": i, "message":
                             "완료 조건(DoD)이 없다 — 무엇을 만족하면 끝인지 적어야 한다"})

    errors = (list(r.get("errors") or []) + contract_errors + due_errors
              + field_errors + obligation_errors + assignment_errors)
    lines = [f"- [{e.get('index')}] {e.get('field')}: {e.get('message')}"
             for e in errors]
    lines += [f"- (경고) [{w.get('index')}] {w.get('message')}" for w in warnings]
    return _machine_check_output(
        state, complete=True, ok=bool(r.get("ok")) and not errors,
        errors=errors, warnings=warnings,
        text="\n".join(lines) if lines else "통과 — 형식·실값 오류 없음",
    )


def _rules_for(state: AgentState) -> str:
    """초안에 관련된 규칙만 끌어온다. 규칙 전문을 프롬프트에 붓지 않는다(정적 RAG)."""
    try:
        from app.agent.retrieval import static_index
        q = "티켓 작성 규칙 " + " ".join(
            str(i.get("type") or "") for i in (state.get("draft") or {}).get("items") or [])
        return "\n\n".join(h["text"] for h in static_index.search(q, k=3))
    except Exception:
        return ""
