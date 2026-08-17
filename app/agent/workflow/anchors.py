"""Deterministic user-authored anchors shared by semantic and draft boundaries.

Models may paraphrase prose, but identifiers, product/technology names, and ordinals are
not prose.  Losing one can leave a syntactically valid payload that describes a different
piece of work.  This module deliberately extracts only high-precision Latin tokens and
Korean numeric ordinals; broad natural-language entity recognition remains the semantic
model's job.
"""

from __future__ import annotations

import hashlib
import json
import re

from app.agent.workflow.state import AgentState, last_user_text, request_text


_TOKEN = re.compile(r"(?<![0-9A-Za-z_.-])([A-Za-z][A-Za-z0-9_.-]{1,79})(?![0-9A-Za-z_.-])")
_ORDINAL = re.compile(r"(?<!\d)(\d{1,3}\s*차)(?![0-9A-Za-z가-힣])")
_STOP = {
    "a", "an", "and", "api", "all", "any", "as", "at", "by", "create", "data",
    "due", "epic", "feature", "for", "from", "http", "https", "improvement", "in",
    "jira", "module", "new", "of", "on", "or", "please", "story", "sub-task",
    "subtask", "task", "test", "the", "this", "ticket", "to", "update", "use",
    "with", "www", "bug", "catalog", "dataops", "etl", "runtime", "workbench",
    # Common English request prose. Lowercase technical nouns remain eligible because
    # casing is not consistently preserved in real ticket text, but ordinary directions
    # and discourse words must never become immutable title anchors.
    "add", "already", "also", "always", "analyze", "build", "can", "cannot",
    "change", "check", "choose", "compare", "could", "currently", "describe",
    "existing", "explain", "fail", "failed", "failing", "fails", "find", "frequently",
    "how", "implement", "investigate", "just", "list", "make", "may", "might",
    "need", "needed", "needs", "often", "recently", "remove", "report", "review",
    "search", "select", "should", "show", "sometimes", "summarize", "than", "then",
    "usually", "verify", "want", "wants", "what", "when", "where", "which", "who",
    "why", "would",
}

_MAX_REQUESTED_OUTCOMES = 6
_MAX_REQUESTED_OUTCOME_CHARS = 512
_WRITE_KINDS = {"ticket", "comment", "write", "plan"}


def _anchor_text(state: AgentState, *, include_latest: bool) -> str:
    original = request_text(state)
    latest = last_user_text(state) if include_latest else ""
    return original + (("\n" + latest) if latest and latest != original else "")


def required_user_anchors(state: AgentState, *, include_latest: bool = True) -> list[str]:
    """Return ordered, exact user tokens that must survive semantic projection.

    Ticket keys, dates, priorities, and user ids already have typed fields and are excluded
    here.  The result is capped so a pasted log cannot become a second prompt payload.
    """
    text = _anchor_text(state, include_latest=include_latest)
    candidates: list[tuple[int, str]] = []
    for match in _TOKEN.finditer(text):
        value = match.group(1).strip(".,;:()[]{}")
        folded = value.casefold()
        if not value or folded in _STOP:
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*-\d+", value):  # Jira key
            continue
        if re.fullmatch(r"(?:skcc\.)?[a-z]{1,3}\d{2,8}", value, re.I):
            continue
        if re.fullmatch(r"P[0-4](?:-[A-Za-z]+)?", value, re.I):
            continue
        # Product and database names are frequently entered in lowercase in tickets, so
        # lowercase alone cannot disqualify a token (``starrocks puffin ndv`` is a real
        # observed form). Common request prose is filtered by `_STOP` above; structured,
        # cased, acronym, and versioned forms remain intrinsically distinctive.
        distinctive = (len(value) >= 3 or value[0].isupper()
                       or any(ch.isupper() for ch in value[1:])
                       or value.isupper() or any(ch in value for ch in "_.-")
                       or any(ch.isdigit() for ch in value))
        if distinctive:
            candidates.append((match.start(), value))
    for match in _ORDINAL.finditer(text):
        candidates.append((match.start(), re.sub(r"\s+", "", match.group(1))))

    ordered, seen = [], set()
    for _start, value in sorted(candidates, key=lambda row: row[0]):
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
        if len(ordered) >= 12:
            break
    return ordered


def format_anchor_contract(state: AgentState, *, include_latest: bool = True) -> str:
    """Compact prompt sidecar; contains no user prose beyond the extracted tokens."""
    anchors = required_user_anchors(state, include_latest=include_latest)
    if not anchors:
        return ""
    return "Required user anchors (copy verbatim when relevant): " + ", ".join(
        f"`{value}`" for value in anchors
    )


def requested_outcome_contract(state: AgentState) -> dict:
    """Return a bounded, stable contract for user-requested write outcomes.

    The Request Architect already expresses each user-visible outcome as an atomic typed
    task.  Downstream semantic models must not rediscover the requested action from a long
    evidence bundle: doing so allowed an implementation method found in research to replace
    the user's requested result.  This contract therefore copies the planner's instruction
    verbatim and gives it an opaque id.  It deliberately performs no verb classification.

    The normal RequestPlan schema is capped at six tasks and 280 characters per instruction.
    The defensive limits below keep hand-built/legacy state bounded too; truncation is made
    explicit so validation can fail closed instead of silently losing an outcome.
    """
    tasks = (state.get("request_plan") or {}).get("tasks") or []
    candidates: list[dict] = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        kind = str(task.get("kind") or "").strip().casefold()
        if task.get("write_intent") is not True and kind not in _WRITE_KINDS:
            continue
        instruction = " ".join(str(task.get("instruction") or "").split())
        if not instruction:
            continue
        source_task_id = " ".join(str(task.get("id") or f"task-{index + 1}").split())[:80]
        truncated = len(instruction) > _MAX_REQUESTED_OUTCOME_CHARS
        bounded_instruction = instruction[:_MAX_REQUESTED_OUTCOME_CHARS]
        digest_source = f"{source_task_id}\0{bounded_instruction}".encode("utf-8")
        candidates.append({
            "id": "outcome:" + hashlib.sha256(digest_source).hexdigest()[:12],
            "source_task_id": source_task_id,
            "instruction": bounded_instruction,
            **({"truncated": True} if truncated else {}),
        })

    if not candidates:
        return {}
    outcomes = candidates[:_MAX_REQUESTED_OUTCOMES]
    omitted_count = max(0, len(candidates) - len(outcomes))
    identity = {"outcomes": outcomes, "omitted_count": omitted_count}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return {
        "id": "requested-outcome:" + hashlib.sha256(encoded).hexdigest()[:16],
        "outcomes": outcomes,
        **({"omitted_count": omitted_count} if omitted_count else {}),
    }


def format_requested_outcome_contract(state: AgentState) -> str:
    """Serialize the authoritative outcome sidecar without surrounding user context."""
    contract = requested_outcome_contract(state)
    if not contract:
        return ""
    return ("Requested outcome contract (authoritative; preserve ids and instructions "
            "verbatim): "
            + json.dumps(contract, ensure_ascii=False, separators=(",", ":")))


def validate_draft_outcome_contract(state: AgentState, draft: dict) -> list[dict]:
    """Validate typed outcome bindings without trying to judge natural-language actions."""
    contract = requested_outcome_contract(state)
    items = [row for row in (draft.get("items") or []) if isinstance(row, dict)]
    if not contract or not items:
        return []

    errors: list[dict] = []
    if contract.get("omitted_count"):
        errors.append({
            "index": -1, "field": "outcome_contract",
            "message": "outcome contract exceeds its bounded capacity; split the request plan",
        })
    if any(row.get("truncated") for row in contract.get("outcomes") or []):
        errors.append({
            "index": -1, "field": "outcome_contract",
            "message": "outcome contract contains a truncated instruction; rebuild the request plan",
        })
    if str(draft.get("outcome_contract_id") or "") != str(contract.get("id") or ""):
        errors.append({
            "index": -1, "field": "outcome_contract_id",
            "message": "outcome contract id does not match the authoritative request plan",
        })

    allowed = {str(row.get("id") or "") for row in contract.get("outcomes") or []}
    used: set[str] = set()
    for index, item in enumerate(items):
        refs = [str(value or "") for value in (item.get("outcome_refs") or []) if str(value or "")]
        unknown = [value for value in refs if value not in allowed]
        if not refs:
            errors.append({
                "index": index, "field": "outcome_refs",
                "message": "outcome contract binding is missing from this draft item",
            })
        elif unknown:
            errors.append({
                "index": index, "field": "outcome_refs",
                "message": "outcome contract contains unknown binding ids: " + ", ".join(unknown[:3]),
            })
        parent_refs = [value for value in refs if value in allowed]
        used.update(parent_refs)
        for child_index, child in enumerate(item.get("children") or []):
            if not isinstance(child, dict):
                continue
            child_refs = [str(value or "") for value in (child.get("outcome_refs") or [])
                          if str(value or "")]
            child_unknown = [value for value in child_refs if value not in allowed]
            if child_unknown:
                errors.append({
                    "index": index, "field": f"children[{child_index}].outcome_refs",
                    "message": "outcome contract contains unknown child binding ids: "
                               + ", ".join(child_unknown[:3]),
                })
            applicable = ([value for value in child_refs if value in allowed]
                          if child_refs else parent_refs)
            if not applicable:
                errors.append({
                    "index": index, "field": f"children[{child_index}].outcome_refs",
                    "message": "outcome contract binding is missing from this child and its parent",
                })
            used.update(applicable)
    missing = sorted(allowed - used)
    if missing:
        errors.append({
            "index": -1, "field": "outcome_refs",
            "message": "outcome contract outcomes are not represented in the draft: "
                       + ", ".join(missing[:6]),
        })
    return errors


def is_ordinal(value: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}차", str(value or "")))
