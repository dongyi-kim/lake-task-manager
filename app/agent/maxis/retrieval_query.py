"""Bounded domain vocabulary for policy retrieval on known production Role I/O.

The original request remains the exact prefix. Appended terms identify applicable
authoring policy; they provide no task fact, citation, answer, or write authority.
Callers must still run the normal context safety and remote retrieval/ACL guards.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.agent.maxis.context_guard import ContextGuardError, MAX_QUERY_UTF8_BYTES
from app.agent.maxis.contracts import EditorDraftV1, canonical_payload_schema


# Generic vocabulary present in knowledge/07-ticket-body-guide.md and
# knowledge/08-editor-and-rendering.md. No corpus or benchmark file is read at runtime.
_EDITOR_POLICY_SUFFIX = "\n\nPolicy search terms: HTML TipTap Description"


@lru_cache(maxsize=1)
def _editor_payload_schemas() -> frozenset[str]:
    schema = EditorDraftV1.model_json_schema()
    accepted = {canonical_payload_schema(schema)[0]}
    # Editor Author narrows mode to the active editor in its actual runtime call.
    for mode in ("description", "comment"):
        schema["properties"]["mode"] = {"type": "string", "const": mode}
        accepted.add(canonical_payload_schema(schema)[0])
    return frozenset(accepted)


def _check_query(query: str) -> None:
    if not isinstance(query, str) or not query.strip():
        raise ContextGuardError("query_required", "query")
    try:
        size = len(query.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ContextGuardError("query_encoding_invalid", "query") from exc
    if size > MAX_QUERY_UTF8_BYTES:
        raise ContextGuardError("query_too_large", "query")


def enrich_role_query(query: str, role_id: str, payload_schema: Any) -> str:
    """Append editor policy terms only for the exact supported EditorDraftV1 schema.

    Annotation-free wire schemas and the runtime's mode-specific schemas are
    equivalent inputs. A contract-name lookalike, another Role, or a control-only
    schema is unchanged. Malformed schemas are left to the normal payload validator;
    this helper cannot turn them into an approved schema. Query byte overflow raises
    ContextGuardError instead of silently removing any original user intent.
    """

    _check_query(query)
    if role_id != "editor_author":
        return query
    try:
        canonical_schema, _ = canonical_payload_schema(payload_schema)
    except (TypeError, ValueError):
        return query
    if canonical_schema not in _editor_payload_schemas():
        return query
    if query.endswith(_EDITOR_POLICY_SUFFIX):
        return query
    enriched = query + _EDITOR_POLICY_SUFFIX
    _check_query(enriched)
    return enriched


__all__ = ["enrich_role_query"]
