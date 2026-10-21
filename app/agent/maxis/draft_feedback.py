"""Bounded, locally authored validation feedback for one native regeneration.

Never copy remote exception prose or rejected model text into the next request.
Sources, their hashes/timestamps, user intent, runtime binding and schema stay
unchanged. Feedback is non-citable role context, not permission or factual evidence.
"""
import hashlib
import json

from app.agent.maxis.contracts import EvidencePackV1, RoleContextV1
from app.agent.maxis.context_guard import canonical_evidence_pack_json, validate_pack_safety

_ROLES = frozenset({
    "query_specialist", "request_architect", "work_architect",
    "knowledge_curator", "result_integrator", "auditor",
})
_NAME = "native_draft_feedback"
_CONTENT = json.dumps({
    "attempt": 2,
    "previous_result": "rejected_native_draft",
    "validation": "Strict JSON, unique keys, native schema and exact source ID validation did not pass.",
    "request": "Generate a fresh complete native draft. Do not repeat root keys, add schema fields, "
               "cite role-context IDs, or label user-intent/proposed work as external facts. "
               "For Query, read source values must belong to task.allowed_sources when present; "
               "record unsupported requested sources in uncertainty, never as executed reads. "
               "For fact selections, use that source's fact_paths and resolve inside content, not /content. "
               "No rejected candidate has been accepted.",
}, sort_keys=True, separators=(",", ":"))
_REASONS = {
    "numeric_grounding": "A narrative number was absent from the evidence. All claim kinds, "
        "including inference and recommendation, must preserve cited numeric values. "
        "Do not invent counts, dates or measurements. For a plan, name its sources/actions "
        "without adding an unsupported count. Keep requested factual numbers only when "
        "supported; otherwise state that they could not be verified. Do not relabel a fact "
        "or substitute a different number to evade validation.",
    "json_syntax": "The previous output was not parseable JSON. Start a fresh object. "
        "Close every nested array/object and then root. missing_inputs belongs only at root. "
        "Do not append corrections or a second JSON object.",
    "json_structure": "The previous JSON violated unique-key, object, size or depth limits. "
        "Emit one bounded object with unique keys at every level; no NaN, Infinity or code fence.",
    "schema": "The previous JSON did not match the native schema. Check required fields, "
        "value types and object ownership. Do not add optional fields without a need.",
    "source_id": "A cited source ID was not in context.sources. Copy the entire exact source_id; "
        "never shorten a hash, invent an ID, or use a role-context ID.",
    "fact_selection": "An original fact selection was invalid. Resolve source_path inside "
        "that source's content, respecting the role's record/length restrictions. "
        "A concept term or reference identifier must select a string, not a record object.",
}
_SCHEMA_HELP = {
    "request_architect": "Root keys are contract,status,payload,missing_inputs. "
        "payload has research_readiness and every required array, even when empty.",
    "work_architect": "Root keys are contract,status,payload,missing_inputs. "
        "interpretation,structure,structure_why,rationale belong to payload, not items. "
        "Keep item metadata separate from wrapped narrative leaves.",
    "knowledge_curator": "A fact leaf is exactly {kind,source_id,source_path}: NO value/source_ids. "
        "An inference/recommendation leaf is exactly {value,kind,source_ids}. "
        "Do not mix these shapes. Optional concept term and reference ref may be omitted.",
    "result_integrator": "Facts have kind,source_id,source_path only; other segments have "
        "value,kind,source_ids. Root uses segments, not payload.",
    "auditor": "Root keys are contract,status,payload,missing_inputs. Completed negative audits use "
        "status=ok and pass|fail axes. Every narrative field and problem.check uses value,kind,source_ids; "
        "check.value is grounded|rule|request. index is a strict integer. Preserve adverse findings; "
        "do not turn fail into pass or cite user intent as an external fact.",
}


def with_native_feedback(payload: dict, role_id: str, *, reason: str = "") -> dict:
    """Add fixed feedback if it fits; never displace/truncate original context."""
    if role_id not in _ROLES:
        return payload
    pack = EvidencePackV1.model_validate_json(
        json.dumps(payload["evidencePack"], ensure_ascii=False), strict=True,
    )
    if pack.use_case != role_id or payload.get("roleId") != role_id:
        raise ValueError("native feedback role mismatch")
    validate_pack_safety(pack)
    if ("synthetic_only" in pack.flags or len(pack.role_context) >= 8
            or any(row.name == _NAME for row in pack.role_context)):
        return payload
    content = _CONTENT
    if reason in _REASONS:
        content = json.dumps({**json.loads(_CONTENT), "reason": reason,
            "validation": _REASONS[reason],
            "role_contract": _SCHEMA_HELP.get(role_id, "Use only native Query schema fields.")},
            sort_keys=True, separators=(",", ":"))
    row = RoleContextV1(
        context_id="rctx_" + hashlib.sha256(f"{pack.request_id}:{role_id}:{_NAME}".encode()).hexdigest()[:20],
        name=_NAME, content_json=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        observed_at=pack.requested_at, partial=False,
    )
    # Do not update requested_at, freshness, source observation times or flags.
    if not (pack.sources or pack.role_context):
        return payload
    candidate = pack.model_copy(update={"role_context": (*pack.role_context, row)})
    candidate = EvidencePackV1.model_validate_json(canonical_evidence_pack_json(candidate), strict=True)
    try:
        validate_pack_safety(candidate)
    except ValueError:
        # A full input may be regenerated unchanged; it is never shortened for this hint.
        return payload
    return {**payload, "evidencePack": candidate.model_dump(mode="json")}
