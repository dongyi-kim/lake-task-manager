"""Pure typed authority for runtime-resolved execution slots.

This module contains no language parsing, retrieval or product API calls.  It consumes the
validated continuation envelope produced at the existing Pydantic/LangGraph boundary and
emits a small ``ResolvedSlot`` contract that Work and final-effect projection can share.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.agent.workflow.contracts import ResolvedSlot


@dataclass(frozen=True)
class ParentSelectionAuthority:
    """Validated request to resolve an existing parent from bounded runtime material."""

    decision_digest: str
    outcome_ids: tuple[str, ...] = ()
    required: bool = True


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parent_selection_authority(state: dict) -> ParentSelectionAuthority | None:
    """Return current typed ``select_existing`` authority, never inferred from prose.

    ``request_refinement`` is a per-turn execution overlay and the matching continuation
    decision is its durable typed provenance. Requiring both prevents a stale/manual value in
    either container from triggering retrieval or a write on its own.
    """
    contract = (state or {}).get("continuation_contract") or {}
    refinement = (state or {}).get("request_refinement") or {}
    if (not isinstance(contract, dict)
            or contract.get("version") != "continuation.v1"
            or contract.get("action") not in {"create", "mixed"}
            or not isinstance(refinement, dict)
            or refinement.get("parent") != "select_existing"):
        return None

    matching: list[dict] = []
    for raw in contract.get("decisions") or []:
        if not isinstance(raw, dict):
            continue
        field = str(raw.get("field") or "").strip().casefold()
        if (field in {"parent", "epic"}
                and raw.get("value") == "select_existing"
                and raw.get("source") in {"explicit_refinement", "interview_answer"}):
            matching.append({
                "field": field,
                "value": "select_existing",
                "source": str(raw.get("source")),
            })
    if not matching:
        return None

    outcome_ids = tuple(dict.fromkeys(
        str(value or "").strip()
        for value in (contract.get("outcome_ids") or [])
        if str(value or "").strip()
    ))
    material = {
        "version": "parent-selection.v1",
        "action": str(contract.get("action")),
        "outcome_ids": list(outcome_ids),
        "decision": matching[-1],
    }
    # An explicit instruction to select and connect an existing parent is an immutable
    # requested effect. Unlike an optional placement preference, zero compatible candidates
    # therefore cannot silently downgrade it to a top-level item.
    return ParentSelectionAuthority(
        decision_digest=_digest(material), outcome_ids=outcome_ids, required=True,
    )


def resolve_parent_slot(
        authority: ParentSelectionAuthority, *, outcome_id: str = "",
        item_id: str = "", selected: dict | str | None = None) -> ResolvedSlot:
    """Resolve one parent slot from a caller-supplied verified compatible candidate."""
    key = (str(selected.get("key") or "").strip().upper()
           if isinstance(selected, dict) else str(selected or "").strip().upper())
    if key:
        return ResolvedSlot(
            field="parent", outcome_id=str(outcome_id or ""), item_id=str(item_id or ""),
            request="select_existing", required=authority.required, status="resolved",
            value=key, resolution="verified_candidate",
            provenance="materialized_parent_candidates", evidence=[key],
            decision_digest=authority.decision_digest,
        )
    if not authority.required:
        return ResolvedSlot(
            field="parent", outcome_id=str(outcome_id or ""), item_id=str(item_id or ""),
            request="select_existing", required=False, status="resolved", value="",
            resolution="top_level", provenance="explicit_safe_fallback", evidence=[],
            decision_digest=authority.decision_digest,
        )
    return ResolvedSlot(
        field="parent", outcome_id=str(outcome_id or ""), item_id=str(item_id or ""),
        request="select_existing", required=True, status="unresolved", value="",
        resolution="unresolved", provenance="materialized_parent_candidates", evidence=[],
        decision_digest=authority.decision_digest,
    )


def bind_resolved_slot_item_ids(draft: dict) -> list[dict]:
    """Bind slot outcome ids to sealed Work ids without using mutable titles."""
    if not isinstance(draft, dict):
        return []
    by_outcome: dict[str, list[str]] = {}
    for item in draft.get("items") or []:
        if not isinstance(item, dict):
            continue
        refs = list(dict.fromkeys(
            str(value or "").strip() for value in (item.get("outcome_refs") or [])
            if str(value or "").strip()
        ))
        item_id = str(item.get("item_id") or "").strip()
        if len(refs) == 1 and item_id:
            by_outcome.setdefault(refs[0], []).append(item_id)

    bound: list[dict] = []
    for raw in draft.get("resolved_slots") or []:
        try:
            slot = ResolvedSlot.model_validate(raw)
        except Exception:
            continue
        candidates = list(dict.fromkeys(by_outcome.get(slot.outcome_id, [])))
        # An outcome-level slot can bind a root only when that root is unique. Multiple
        # roots serving the same outcome need item-scoped authority; list order is not it.
        item_id = slot.item_id or (candidates[0] if len(candidates) == 1 else "")
        bound.append(slot.model_copy(update={"item_id": item_id}).model_dump())
    if bound:
        draft["resolved_slots"] = bound
    elif "resolved_slots" in draft:
        draft["resolved_slots"] = []
    return bound


__all__ = [
    "ParentSelectionAuthority", "bind_resolved_slot_item_ids",
    "parent_selection_authority", "resolve_parent_slot",
]
