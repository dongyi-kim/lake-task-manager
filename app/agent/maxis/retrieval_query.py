"""Bounded domain vocabulary for policy retrieval on known production Role I/O.

The original request remains the exact prefix. Appended terms identify applicable
authoring policy; they provide no task fact, citation, answer, or write authority.
Callers must still run the normal context safety and remote retrieval/ACL guards.
"""

from __future__ import annotations

from functools import lru_cache
from types import MappingProxyType
from typing import Any

from app.agent.maxis.context_guard import ContextGuardError, MAX_QUERY_UTF8_BYTES
from app.agent.maxis.contracts import EditorDraftV1, canonical_payload_schema


# Generic vocabulary present in knowledge/07-ticket-body-guide.md and
# knowledge/08-editor-and-rendering.md. No corpus or benchmark file is read at runtime.
_EDITOR_POLICY_SUFFIX = "\n\nPolicy search terms: HTML TipTap 본문"

# Only generic vocabulary in Role-authorized policy documents, never a ticket,
# benchmark answer, corpus body, or permission. Unknown/specialized schemas stay
# unchanged until reviewed; this is not a new Role/schema authority registry.
_POLICY_TERMS = MappingProxyType({
    "query_specialist": "Confluence changelog 검색",
    "work_architect": "Epic Sub-Task 완료 조건",
    "auditor": "Story Point Epic Link",
    "portfolio_analyst": "진척률 SP statusCategory",
})
_POLICY_SUFFIX_PREFIX = "\n\nPolicy search terms: "


def select_policy_query(query: str, role_id: str) -> str:
    """Select only an exact, bounded policy suffix; never edit model/user input.

    This pure unit is also embedded in Core/PMO's Dify Policy Query node. It is
    retrieval optimization, not an authority boundary: Role ACL and both guards
    still validate the original request. Control and unknown queries are unchanged.
    """
    if not isinstance(query, str) or not isinstance(role_id, str):
        return query
    terms = _POLICY_TERMS.get(role_id)
    if terms is None:
        return query
    suffix = _POLICY_SUFFIX_PREFIX + terms
    if not query.endswith(suffix) or not query[:-len(suffix)].strip():
        return query
    try:
        if len(query.encode("utf-8")) > MAX_QUERY_UTF8_BYTES:
            return query
    except UnicodeEncodeError:
        return query
    return terms


@lru_cache(maxsize=4)
def _policy_payload_schemas(role_id: str) -> frozenset[str]:
    if role_id == "query_specialist":
        from app.agent.workflow.contracts import role_output_schema
        schemas = (role_output_schema(role_id),)
    elif role_id == "work_architect":
        from app.agent.workflow.work_architect.contracts import (
            SCHEMA, CREATE_SCHEMA, UPDATE_SCHEMA, COMMENT_SCHEMA,
        )
        schemas = (SCHEMA, CREATE_SCHEMA, UPDATE_SCHEMA, COMMENT_SCHEMA)
    elif role_id == "auditor":
        from app.agent.workflow.agents.auditor import SCHEMA
        schemas = (SCHEMA,)
    elif role_id == "portfolio_analyst":
        from app.agent.workflow.agents.portfolio_analyst import SCHEMA
        schemas = (SCHEMA,)
    else:
        return frozenset()
    return frozenset(canonical_payload_schema(schema)[0] for schema in schemas)


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
    """Append policy vocabulary only for an exact supported Role wire schema.

    Annotation-free wire schemas and the runtime's mode-specific schemas are
    equivalent inputs. A contract-name lookalike, another Role, or a control-only
    schema is unchanged. Malformed schemas are left to the normal payload validator;
    this helper cannot turn them into an approved schema. Query byte overflow raises
    ContextGuardError instead of silently removing any original user intent.
    """

    _check_query(query)
    if role_id != "editor_author" and role_id not in _POLICY_TERMS:
        return query
    try:
        canonical_schema, _ = canonical_payload_schema(payload_schema)
    except (TypeError, ValueError):
        return query
    accepted = (_editor_payload_schemas() if role_id == "editor_author"
                else _policy_payload_schemas(role_id))
    if canonical_schema not in accepted:
        return query
    suffix = (_EDITOR_POLICY_SUFFIX if role_id == "editor_author"
              else _POLICY_SUFFIX_PREFIX + _POLICY_TERMS[role_id])
    if query.endswith(suffix):
        return query
    enriched = query + suffix
    _check_query(enriched)
    return enriched


__all__ = ["enrich_role_query"]
