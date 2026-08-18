"""Deterministic final-effect projection shared by review, approval and rendering.

This module intentionally contains no semantic review and no side effects.  It projects the
typed continuation authority over mutable workflow containers and describes the one effect that
could be staged.  Keeping this boundary outside ``Auditor`` prevents the approval contract from
becoming another agent-specific rule pile.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.workflow.state import AgentState, Node


WRITE_ACTIONS = {"create", "comment", "update", "mixed"}
UPDATE_EFFECT_ACTIONS = {
    "transition_ticket", "link_tickets", "update_ticket", "update_tickets",
}


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
    "FinalEffect", "UPDATE_EFFECT_ACTIONS", "UserFieldLock", "WRITE_ACTIONS",
    "capture_user_field_locks", "continuation_action", "current_work_failed",
    "final_effect", "project_final_authority_state",
]
