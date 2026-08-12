"""Jira ticket tier별 field/action 계약.

LLM은 이 규칙을 추론하지 않는다. prompt는 사람과 model이 읽는 설명이고, 이 module은
validator와 write boundary가 집행하는 source of truth다. Jira instance마다 달라지는 실제
issue type, field, transition은 createmeta/editmeta/transitions와 반드시 교차 검증한다.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

TIER_EPIC = "epic"
TIER_TASK = "task"
TIER_SUBTASK = "subtask"
TIERS = (TIER_EPIC, TIER_TASK, TIER_SUBTASK)

# Agent write tools가 실제로 받는 field. Jira REST의 모든 field 목록이 아니다.
CREATE_FIELDS = {
    TIER_EPIC: frozenset({"summary", "epic_name", "description", "components",
                          "priority", "duedate", "assignee"}),
    TIER_TASK: frozenset({"summary", "type", "epic", "description", "components",
                          "labels", "priority", "duedate", "assignee"}),
    TIER_SUBTASK: frozenset({"summary", "type", "parent", "description", "components",
                             "labels", "priority", "duedate", "assignee"}),
}
EDITABLE_FIELDS = frozenset({"assignee", "duedate", "priority", "summary", "labels",
                             "components", "description"})

# read는 검색/상세 조회를 함께 뜻한다. create_child는 parent 쪽 capability다.
_COMMON_ACTIONS = frozenset({"create", "read", "update_fields", "comment", "transition",
                             "link", "attach_document"})
ACTIONS = {
    TIER_EPIC: _COMMON_ACTIONS | {"create_child"},
    TIER_TASK: _COMMON_ACTIONS | {"create_child"},
    TIER_SUBTASK: _COMMON_ACTIONS,
}

_SUBTASK_NAMES = {"subtask", "sub-task", "하위작업", "하위 작업", "서브태스크"}


def issue_tier(issue_type: str = "", is_subtask: bool | None = None) -> str:
    """Jira metadata를 tier로 정규화한다. `subtask` flag가 type 표시명보다 우선한다."""
    name = str(issue_type or "").strip()
    norm = re.sub(r"[\s_-]+", "", name).casefold()
    if is_subtask is True:
        return TIER_SUBTASK
    if norm == "epic":
        return TIER_EPIC
    if is_subtask is False:
        return TIER_TASK
    if name.casefold() in _SUBTASK_NAMES or norm in {"subtask", "하위작업", "서브태스크"}:
        return TIER_SUBTASK
    # Task/Improvement/Feature/New Feature/Bug/Story와 project 고유 일반 type은 Task tier다.
    return TIER_TASK


def can_parent(parent_tier: str, child_tier: str) -> bool:
    """허용 계층은 Epic → Task-tier → Sub-Task뿐이다."""
    return ((parent_tier == TIER_EPIC and child_tier == TIER_TASK)
            or (parent_tier == TIER_TASK and child_tier == TIER_SUBTASK))


def status_category(ticket: Mapping | None) -> str:
    """badge/brief/raw issue에서 `statusCategory`를 내부 소문자 값으로 읽는다."""
    if not isinstance(ticket, Mapping):
        return ""
    direct = ticket.get("statusCategory") or ticket.get("statusCat")
    if direct:
        return str(direct).strip().lower()
    if ticket.get("done") is True:
        return "done"
    fields = ticket.get("fields") or {}
    status = fields.get("status") or {}
    category = status.get("statusCategory") or {}
    return str(category.get("key") or "").strip().lower()


def is_done(ticket: Mapping | None) -> bool:
    return status_category(ticket) == "done"


def field_update_error(ticket: Mapping | None, fields: Iterable[str] = ()) -> str:
    """field update의 상태/contract 오류. 빈 문자열이면 이 층에서는 허용한다.

    Jira editmeta 권한·실제 허용 field 검사는 별도로 수행한다. Done은 editmeta가 우연히
    비어 있는지와 무관하게 명시적으로 막는다.
    """
    bad = sorted({str(f) for f in fields if str(f) not in EDITABLE_FIELDS})
    if bad:
        return "Agent가 바꿀 수 없는 field입니다: " + ", ".join(bad)
    if is_done(ticket):
        return ("완료된 티켓(statusCategory=done)의 속성은 바꿀 수 없습니다. "
                "먼저 실제 가능한 Reopened 전이를 별도로 승인·실행한 뒤, 새 승인으로 "
                "속성을 변경하세요. 완료 티켓에도 댓글은 남길 수 있습니다.")
    return ""


def reopen_transition(transitions: Iterable[Mapping] | None) -> Mapping | None:
    """현재 Jira가 실제 제공한 전이 중 Reopened/open 전이를 고른다. 이름을 만들지 않는다."""
    rows = [t for t in (transitions or []) if isinstance(t, Mapping)]
    named = re.compile(r"re[ -]?open|재열|다시\s*열", re.I)
    return next((t for t in rows
                 if named.search(str(t.get("name") or "") + " " + str(t.get("to") or ""))
                 and str(t.get("toCategory") or "").lower() != "done"), None)


def action_error(tier: str, action: str, ticket: Mapping | None = None,
                 child_tier: str = "") -> str:
    """tier/status에서 action이 구조적으로 가능한지 설명한다."""
    if tier not in TIERS:
        return f"알 수 없는 ticket tier입니다: {tier}"
    if action not in ACTIONS[tier]:
        return f"{tier} tier에서는 '{action}' 행동을 지원하지 않습니다."
    if action == "update_fields":
        return field_update_error(ticket)
    if action == "create_child" and child_tier and not can_parent(tier, child_tier):
        return f"{tier} 아래에 {child_tier} tier를 만들 수 없습니다."
    return ""


__all__ = ["ACTIONS", "CREATE_FIELDS", "EDITABLE_FIELDS", "TIERS", "TIER_EPIC",
           "TIER_TASK", "TIER_SUBTASK", "action_error", "can_parent", "field_update_error",
           "is_done", "issue_tier", "reopen_transition", "status_category"]
