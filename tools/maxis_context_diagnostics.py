"""Offline structural delivery observations; never emit context values or identifiers."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from app.agent.maxis import backend
from app.agent.maxis.context_guard import (
    MAX_CONTEXT_PACK_UTF8_BYTES, MAX_QUERY_UTF8_BYTES,
    MAX_ROLE_CONTEXT_UTF8_BYTES, canonical_evidence_pack_json,
)
from app.agent.maxis.contracts import EvidencePackV1
from app.agent.workflow.role_manifest import ROLE_SPECS


_SAFE_FLAGS = frozenset({
    "synthetic_only", "source_limit", "source_truncated", "total_truncated",
    "partial_source", "stale_source", "fact_source_omitted", "metric_source_omitted",
    "role_context_limit", "partial_role_context", "stale_role_context",
})


def _encoded_value(role: str, key: str, state: dict) -> str:
    value = backend._json_value(state[key])
    if role == "editor_author" and key == "seed_html":
        value = backend._outbound_content_projection(value)
    return backend._canonical_json(value)


def _missing_observation(state: dict, key: str, encoded: str) -> bool:
    """Mirror the projection's provenance check, independently of its size/count flags."""
    present = bool(str(state.get("correlation_started_at") or "").strip())
    ledger = state.get("role_context_carryover")
    carried = ledger.get(key) if isinstance(ledger, dict) else None
    if isinstance(carried, dict) and carried.get("sha256") == hashlib.sha256(encoded.encode()).hexdigest():
        try:
            observed = datetime.fromisoformat(str(carried.get("observed_at") or "").strip().replace("Z", "+00:00"))
            if observed.tzinfo is not None and observed.utcoffset() is not None:
                present = True
        except ValueError:
            present = False
    return not present


def describe_context(role_id: str, state: dict, pack: EvidencePackV1) -> dict:
    """Compare declared, projected and delivered keys without assessing requiredness.

    Lists follow manifest priority. Budget-removal attribution requires the guard's
    role_context_limit flag; unmatched removals stay unexplained. Limits describe the
    current production defaults, not a custom guard policy. No state or pack is changed.
    """
    if role_id not in ROLE_SPECS or pack.use_case != role_id:
        raise ValueError("role mismatch or unknown role")
    declared = list(ROLE_SPECS[role_id].remote_context_keys)
    rows, allowed_set = backend._role_context(role_id, state)
    allowed = [key for key in declared if key in allowed_set]
    eligible = [key for key in allowed if state.get(key) not in (None, "", [], {})]
    projected = {row["name"]: row for row in rows if row["name"] in allowed}
    final = {row.name: row for row in pack.role_context if row.name in allowed}
    encoded = {key: _encoded_value(role_id, key, state) for key in projected}
    per_field = {
        key for key, value in encoded.items()
        if len(value) > 8192 or len(value.encode("utf-8")) > MAX_ROLE_CONTEXT_UTF8_BYTES
    }
    flags = sorted(set(pack.flags) & _SAFE_FLAGS)
    removed = set(projected) - set(final)
    synthetic = "synthetic_only" in flags
    budget_removed = removed if not synthetic and "role_context_limit" in flags else set()
    budget_omitted = set()
    for key, row in final.items():
        value = json.loads(row.content_json)
        if (key in projected and isinstance(value, dict)
                and set(value) == {"omitted", "originalUtf8Bytes"}
                and value["omitted"] is True
                and type(value["originalUtf8Bytes"]) is int
                and value != projected[key]["value"]):
            budget_omitted.add(key)
    groups = {
        "declaredKeys": declared, "allowedKeys": allowed, "eligibleKeys": eligible,
        "projectedKeys": projected, "finalKeys": final,
        "missingValueKeys": set(allowed) - set(eligible),
        "countLimitOmittedKeys": set(eligible) - set(projected),
        "perFieldSizeOmittedKeys": per_field,
        "packBudgetOmittedKeys": budget_omitted, "packBudgetRemovedKeys": budget_removed,
        "syntheticReplacementKeys": removed if synthetic else set(),
        "unexplainedRemovedKeys": removed - budget_removed if not synthetic else set(),
        "partialKeys": {key for key, row in final.items() if row.partial},
        "freshnessMissingKeys": {key for key, value in encoded.items() if _missing_observation(state, key, value)},
    }
    return {
        "roleId": role_id,
        **{label: [key for key in declared if key in keys] for label, keys in groups.items()},
        "counts": {label: len(groups[f"{label}Keys"]) for label in ("declared", "allowed", "eligible", "projected", "final")},
        "limits": {"projectionCount": 8, "projectionChars": 8192,
                   "fieldUtf8Bytes": MAX_ROLE_CONTEXT_UTF8_BYTES,
                   "packUtf8Bytes": MAX_CONTEXT_PACK_UTF8_BYTES,
                   "queryUtf8Bytes": MAX_QUERY_UTF8_BYTES},
        "packUtf8Bytes": len(canonical_evidence_pack_json(pack).encode("utf-8")),
        "queryUtf8Bytes": len(pack.query.encode("utf-8")),
        "sourceCount": len(pack.sources),
        "truncatedSourceCount": sum(source.truncated for source in pack.sources),
        "freshness": pack.freshness if pack.freshness in {"fresh", "mixed", "stale", "empty"} else "unknown",
        "partial": bool(pack.partial), "flags": flags,
    }
