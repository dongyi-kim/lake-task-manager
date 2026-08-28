"""Current-request authority boundaries for Work Architect policies."""

from __future__ import annotations

from app.agent.workflow.continuation import authoritative_decision_values
from app.agent.workflow.state import last_user_text, request_text


_CONTINUATION_ACTIONS = {"read", "create", "comment", "update", "mixed", "respond"}


def typed_continuation_contract(state: dict) -> dict:
    """Return only a bounded v1 contract already validated at the Session boundary."""
    contract = (state or {}).get("continuation_contract") or {}
    if (not isinstance(contract, dict)
            or contract.get("version") != "continuation.v1"
            or contract.get("action") not in _CONTINUATION_ACTIONS):
        return {}
    return contract


def current_request_boundary_text(state) -> str:
    """Return only the frozen request and its explicit current continuation."""
    contract = typed_continuation_contract(state)
    if state.get("turn_continuation") and contract:
        rows = [str(contract.get("root_request") or "").strip()]
        rows.extend(authoritative_decision_values(contract))
        return "\n".join(dict.fromkeys(row for row in rows if row))

    frozen = request_text(state).strip()
    latest = last_user_text(state).strip()
    if state.get("turn_continuation"):
        rows = [frozen] if frozen else []
        if latest and latest != frozen:
            rows.append(latest)
        return "\n".join(rows)
    return latest or frozen


def meeting_request_boundary_text(state) -> str:
    """Return meeting text only inside its explicit continuation boundary."""
    current = current_request_boundary_text(state)
    if not state.get("turn_continuation"):
        return current
    try:
        from app.agent.workflow.meeting_context import meeting_request_text
        original = str(meeting_request_text(state) or "").strip()
    except Exception:
        original = ""
    return original or current


__all__ = [
    "current_request_boundary_text",
    "meeting_request_boundary_text",
    "typed_continuation_contract",
]
