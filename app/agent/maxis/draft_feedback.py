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
    "knowledge_curator", "result_integrator",
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


def with_native_feedback(payload: dict, role_id: str) -> dict:
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
    row = RoleContextV1(
        context_id="rctx_" + hashlib.sha256(f"{pack.request_id}:{role_id}:{_NAME}".encode()).hexdigest()[:20],
        name=_NAME, content_json=_CONTENT,
        content_sha256=hashlib.sha256(_CONTENT.encode()).hexdigest(),
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
