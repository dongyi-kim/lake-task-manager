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
_ORDINAL = re.compile(
    r"(?<!\d)(\d{1,3}\s*차)"
    r"(?=$|[\s,.;:!?/()\[\]{}]|(?:와|과|및|부터|까지|로|를|는|은|의)(?=\s|$|[,.;:!?]))"
)
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
_OUTCOME_TERM_STOP = {
    "task", "story", "bug", "feature", "improvement", "ticket", "module",
    "티켓", "작업", "업무", "추가", "진행", "수행", "작성", "적용", "개선",
    "조정", "최적화", "구현", "개발", "검증",
}


def outcome_authority_terms(text: str) -> set[str]:
    """Return product-neutral terms used only to compare authority boundaries.

    This is deliberately smaller than Work's drafting heuristics: product, component, and
    technology names are material identity here and must never be placed on a local stoplist.
    The function does not infer intent; it only supports bounded set comparisons between the
    original request, planner projection, finding, and visible draft.
    """
    clean = re.sub(r"^\s*\[[^\]]+\]\s*", "", str(text or ""))
    words = re.findall(r"[A-Za-z0-9_.-]{2,}|[가-힣]{2,}", clean.casefold())
    return {
        word for word in words
        if word not in _OUTCOME_TERM_STOP and not word.isdigit()
    }


def _anchors_from_text(text: str) -> list[str]:
    """Extract high-precision anchors from one request boundary, without precedence."""
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
    return ordered


def required_user_anchors(state: AgentState, *, include_latest: bool = True) -> list[str]:
    """Return exact user tokens with field-aware current-turn precedence.

    Technical names accumulate across the frozen request and current user turn. Ordinals
    are scope fields rather than independent nouns: if the latest turn explicitly says
    ``2차``, it supersedes frozen ``1차`` instead of forcing both into every title. Ticket
    keys, dates, priorities, and user ids already have typed fields and are excluded here.
    The result is capped so a pasted log cannot become a second prompt payload.
    """
    original = request_text(state)
    latest = last_user_text(state) if include_latest else ""
    original_anchors = _anchors_from_text(original)
    latest_anchors = (_anchors_from_text(latest)
                      if latest and latest != original else [])
    latest_ordinals = [value for value in latest_anchors
                       if _ORDINAL.fullmatch(value)]

    # Reserve capacity for authoritative latest ordinals. Technical anchors remain a union,
    # while frozen ordinals survive only when the current turn did not replace that field.
    ordinal_values = (latest_ordinals if latest_ordinals else [
        value for value in original_anchors + latest_anchors if _ORDINAL.fullmatch(value)
    ])
    technical_values = [
        value for value in original_anchors + latest_anchors
        if not _ORDINAL.fullmatch(value)
    ]
    ordered, seen = [], set()
    for value in technical_values[:max(0, 12 - len(ordinal_values[:12]))] \
            + ordinal_values[:12]:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered[:12]


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

    The Request Architect expresses each user-visible outcome as an atomic typed task and
    pins a single write outcome to the current user request.  Downstream semantic models must
    not rediscover the requested action from a long evidence bundle: doing so allowed an
    implementation method found in research to replace the user's requested result.  This
    contract therefore copies the bounded instruction and gives it an opaque id.  The id and
    user-authored action/object/explicit constraints are authority; planner-authored examples,
    assumptions, and delegated implementation choices are not independent user requirements.
    It deliberately performs no verb classification.

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


_SCOPED_DECISION_FAMILIES = {
    "target": "target", "table": "target", "entity": "target",
    "document": "target", "ticket": "target",
    "parent": "parent", "epic": "parent",
    "assignee": "assignee", "owner": "assignee",
}


def _scoped_decision_resolution(state: AgentState) -> tuple[dict, list[dict], dict[str, str]]:
    """Bridge ``field:<request task id>`` decisions to opaque draft outcome refs.

    RequestPlan task ids are stable across an interview and intentionally human-readable to
    the question producer. Draft bindings use hashed ids so a semantic projector cannot
    reinterpret them. This is the sole deterministic bridge between those two namespaces.
    """
    continuation = state.get("continuation_contract") or {}
    if (not isinstance(continuation, dict)
            or continuation.get("version") != "continuation.v1"):
        return {}, [], {}
    contract = requested_outcome_contract(state)
    outcomes = [row for row in (contract.get("outcomes") or []) if isinstance(row, dict)]
    if not outcomes:
        # Legacy/direct states have no RequestPlan namespace to bridge. Their unscoped
        # deterministic guards still apply; do not guess an opaque outcome identity.
        return {}, [], {}
    aliases: dict[str, str] = {}
    for row in outcomes:
        opaque = str(row.get("id") or "").strip()
        task_id = str(row.get("source_task_id") or "").strip()
        if opaque:
            aliases[opaque.casefold()] = opaque
        if opaque and task_id:
            aliases[task_id.casefold()] = opaque

    resolved: dict[str, dict[str, dict]] = {}
    issues: list[dict] = []
    seen: dict[tuple[str, str], dict] = {}
    allowed_task_ids = {
        str(value or "").strip().casefold()
        for value in (continuation.get("outcome_ids") or []) if str(value or "").strip()
    }
    for decision in continuation.get("decisions") or []:
        if not isinstance(decision, dict):
            continue
        raw_field = str(decision.get("field") or "").strip()
        if ":" not in raw_field:
            continue
        raw_family, raw_scope = raw_field.split(":", 1)
        family = _SCOPED_DECISION_FAMILIES.get(raw_family.strip().casefold())
        scope = raw_scope.strip()
        if not family or not scope:
            continue
        opaque = aliases.get(scope.casefold(), "")
        # ``outcome_ids`` is also checked so a stale task id cannot be silently rebound to
        # a new RequestPlan that happens to share some unrelated draft artifact.
        task_scope_known = scope.casefold() in allowed_task_ids
        opaque_scope_known = scope.casefold() in {
            str(row.get("id") or "").casefold() for row in outcomes
        }
        if not opaque or not (task_scope_known or opaque_scope_known):
            issues.append({
                "index": -1, "field": "outcome_refs",
                "message": f"scoped continuation decision has unknown outcome: {raw_field}",
            })
            continue
        normalized = {
            "field": raw_field,
            "value": str(decision.get("value") or "").strip(),
            "source": str(decision.get("source") or ""),
            "outcome_ref": opaque,
            "source_task_id": str(next((row.get("source_task_id") for row in outcomes
                                         if str(row.get("id") or "") == opaque), "") or ""),
        }
        identity = (opaque, family)
        prior = seen.get(identity)
        if prior and prior.get("value") != normalized["value"]:
            issues.append({
                "index": -1, "field": family,
                "message": (f"outcome {opaque} has conflicting scoped {family} decisions: "
                            f"{prior.get('field')} / {raw_field}"),
            })
        seen[identity] = normalized
        resolved.setdefault(opaque, {})[family] = normalized
    return resolved, issues, aliases


def scoped_continuation_decisions(state: AgentState) -> dict[str, dict[str, dict]]:
    """Return user decisions keyed by the opaque ``outcome_ref`` used in the draft."""
    resolved, _issues, _aliases = _scoped_decision_resolution(state)
    return resolved


def validate_scoped_outcome_bindings(state: AgentState, draft: dict) -> list[dict]:
    """Fail closed when a scoped decision has no unique root outcome binding."""
    resolved, issues, aliases = _scoped_decision_resolution(state)
    if not resolved and not issues:
        return []
    errors = list(issues)
    roots = [row for row in (draft.get("items") or []) if isinstance(row, dict)]
    occurrences: dict[str, list[int]] = {ref: [] for ref in resolved}
    complete_scope = set(resolved) == {
        str(row.get("id") or "")
        for row in (requested_outcome_contract(state).get("outcomes") or [])
        if str(row.get("id") or "")
    }
    for index, row in enumerate(roots):
        raw_refs = [str(value or "").strip() for value in (row.get("outcome_refs") or [])
                    if str(value or "").strip()]
        canonical = [aliases.get(value.casefold(), "") for value in raw_refs]
        canonical = [value for value in canonical if value]
        if len(canonical) != len(set(canonical)):
            errors.append({
                "index": index, "field": "outcome_refs",
                "message": "draft item repeats the same scoped outcome_ref",
            })
        scoped = [value for value in canonical if value in resolved]
        if complete_scope and (len(canonical) != 1 or canonical[0] not in resolved):
            errors.append({
                "index": index, "field": "outcome_refs",
                "message": "every root needs one unique scoped outcome_ref",
            })
        if len(scoped) > 1:
            errors.append({
                "index": index, "field": "outcome_refs",
                "message": "one draft item ambiguously binds multiple scoped outcomes",
            })
        for value in set(scoped):
            occurrences[value].append(index)
    for outcome_ref, indexes in occurrences.items():
        if len(indexes) == 1:
            continue
        errors.append({
            "index": indexes[0] if indexes else -1,
            "field": "outcome_refs",
            "message": (f"scoped outcome {outcome_ref} must bind exactly one draft root; "
                        f"found {len(indexes)}"),
        })
    return errors


def format_requested_outcome_contract(state: AgentState) -> str:
    """Serialize the authoritative outcome sidecar without surrounding user context."""
    contract = requested_outcome_contract(state)
    if not contract:
        return ""
    return ("Requested outcome contract (ids authoritative; preserve user-authored action, "
            "object, and explicit constraints): "
            + json.dumps(contract, ensure_ascii=False, separators=(",", ":")))


def single_outcome_binding(state: AgentState) -> tuple[str, str] | None:
    """Return the one safe runtime-owned outcome binding, if it is unambiguous.

    A single, complete Request Architect outcome has no semantic mapping choice: every root
    artifact serves that same requested result and child stages inherit it.  Multiple,
    truncated, or overflowed outcomes still require model mapping plus Auditor validation.
    """
    contract = requested_outcome_contract(state)
    outcomes = contract.get("outcomes") or []
    if (len(outcomes) != 1 or contract.get("omitted_count")
            or outcomes[0].get("truncated")):
        return None
    return str(contract.get("id") or ""), str(outcomes[0].get("id") or "")


def bind_single_outcome_contract(state: AgentState, payload: dict) -> bool:
    """Attach an unambiguous one-outcome binding without asking the model to copy ids."""
    binding = single_outcome_binding(state)
    items = [row for row in (payload.get("items") or []) if isinstance(row, dict)]
    if not binding or not items:
        return False
    contract_id, outcome_id = binding
    if not contract_id or not outcome_id:
        return False
    payload["outcome_contract_id"] = contract_id
    for item in items:
        item["outcome_refs"] = [outcome_id]
        # Child stages inherit their only possible mapping from the parent.  Removing a
        # model-authored child copy avoids redundant ids while preserving validator meaning.
        for child in (item.get("children") or []):
            if isinstance(child, dict):
                child.pop("outcome_refs", None)
    return True


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
    errors.extend(validate_work_item_identities(state, draft))
    return errors


def work_item_id(contract_id: str, outcome_ref: str, root_slot: str = "") -> str:
    """Return one opaque identity inside a requested-outcome payload.

    One requested outcome can legitimately decompose into several root tickets. ``root_slot``
    is a server-stamped position inside that immutable draft payload, not a title matcher or a
    second semantic outcome.  A singleton keeps the historical v1 value for checkpoint and
    receipt compatibility.  Titles and descriptions never participate in either identity.
    """
    material = (f"{str(contract_id or '').strip()}\0{str(outcome_ref or '').strip()}"
                + (f"\0{str(root_slot or '').strip()}" if str(root_slot or '').strip() else ""))
    return "work-item:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _root_identity_slots(draft: dict, allowed: set[str]) -> list[tuple[dict, list[str], str]]:
    """Project roots to typed refs plus a deterministic within-payload occurrence slot."""
    roots = [row for row in (draft.get("items") or []) if isinstance(row, dict)]
    refs_by_root = [list(dict.fromkeys(
        str(value or "").strip() for value in (row.get("outcome_refs") or [])
        if str(value or "").strip() in allowed
    )) for row in roots]
    totals: dict[str, int] = {}
    for refs in refs_by_root:
        if len(refs) == 1:
            totals[refs[0]] = totals.get(refs[0], 0) + 1
    seen: dict[str, int] = {}
    projected = []
    for row, refs in zip(roots, refs_by_root):
        slot = ""
        if len(refs) == 1 and totals.get(refs[0], 0) > 1:
            seen[refs[0]] = seen.get(refs[0], 0) + 1
            slot = f"root:{seen[refs[0]]}"
        projected.append((row, refs, slot))
    return projected


def seal_work_item_identities(state: AgentState, draft: dict) -> dict:
    """Attach typed payload identities without interpreting visible prose.

    A root must have exactly one outcome ref to receive an id. Several roots may serve the
    same outcome; their list slots are stamped only inside this complete payload so parallel
    assignment/audit joins cannot collide. Ambiguous/missing mappings remain unsealed and are
    rejected. Children inherit the already-sealed root identity plus their bounded slot.
    """
    if not isinstance(draft, dict):
        return draft
    contract = requested_outcome_contract(state)
    contract_id = str(contract.get("id") or "")
    allowed = {
        str(row.get("id") or "")
        for row in (contract.get("outcomes") or []) if str(row.get("id") or "")
    }
    if not contract_id or not allowed:
        return draft
    projected = _root_identity_slots(draft, allowed)
    grouped = any(slot for _item, _refs, slot in projected)
    draft["identity_contract"] = "work-item.v2" if grouped else "work-item.v1"
    for item, refs, root_slot in projected:
        if len(refs) != 1:
            item.pop("item_id", None)
            continue
        root_id = work_item_id(contract_id, refs[0], root_slot)
        item["item_id"] = root_id
        for child_index, child in enumerate(item.get("children") or []):
            if not isinstance(child, dict):
                continue
            child_refs = list(dict.fromkeys(
                str(value or "").strip() for value in (child.get("outcome_refs") or [])
                if str(value or "").strip() in allowed
            ))
            child_ref = child_refs[0] if len(child_refs) == 1 else refs[0]
            child_material = (
                f"{root_id}\0{child_ref}\0child\0{child_index}"
                if root_slot else
                f"{work_item_id(contract_id, child_ref)}\0child\0{child_index}"
            )
            child["item_id"] = (
                "work-child:" + hashlib.sha256(child_material.encode("utf-8")).hexdigest()[:16]
            )
    return draft


def validate_work_item_identities(state: AgentState, draft: dict) -> list[dict]:
    """Validate identities only after Work has opted into a typed identity contract."""
    if not isinstance(draft, dict) or (
            draft.get("identity_contract") not in {"work-item.v1", "work-item.v2"}
            and not any(isinstance(row, dict) and row.get("item_id")
                        for row in (draft.get("items") or []))):
        return []
    contract = requested_outcome_contract(state)
    contract_id = str(contract.get("id") or "")
    allowed = {
        str(row.get("id") or "")
        for row in (contract.get("outcomes") or []) if str(row.get("id") or "")
    }
    errors: list[dict] = []
    projected = _root_identity_slots(draft, allowed)
    expected_contract = ("work-item.v2"
                         if any(slot for _item, _refs, slot in projected)
                         else "work-item.v1")
    if draft.get("identity_contract") != expected_contract:
        errors.append({
            "index": -1, "field": "identity_contract",
            "message": "Work identity contract does not match outcome root cardinality",
        })
    seen: dict[str, int] = {}
    for index, (item, refs, root_slot) in enumerate(projected):
        expected = (work_item_id(contract_id, refs[0], root_slot)
                    if len(refs) == 1 else "")
        actual = str(item.get("item_id") or "")
        if not expected or actual != expected:
            errors.append({
                "index": index, "field": "item_id",
                "message": "Work item identity does not match its single typed outcome_ref",
            })
            continue
        if actual in seen:
            errors.append({
                "index": index, "field": "item_id",
                "message": (f"Work item identity is duplicated with item {seen[actual]}; "
                            "one outcome cannot be rebound by title order"),
            })
        else:
            seen[actual] = index
    return errors


def is_ordinal(value: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}차", str(value or "")))
