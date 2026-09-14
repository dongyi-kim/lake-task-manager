"""Production query enrichment preserves editor intent and other Role contracts."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.agent.maxis.context_guard import ContextGuardError, MAX_QUERY_UTF8_BYTES
from app.agent.maxis.contracts import EditorDraftV1, canonical_payload_schema
from app.agent.maxis.retrieval_query import enrich_role_query


def _editor_schema(mode=None):
    schema = EditorDraftV1.model_json_schema()
    if mode is not None:
        schema["properties"]["mode"] = {"type": "string", "const": mode}
    return schema


@pytest.mark.parametrize("mode", (None, "description", "comment"))
@pytest.mark.parametrize("wire_schema", (False, True))
def test_editor_enrichment_keeps_the_exact_query_prefix_and_opaque_references(
    mode, wire_schema,
):
    query = "  [[person:opaque-ref]] 검토 요청\n초안의 요청과 상태를 그대로 보존.  "
    schema = _editor_schema(mode)
    if wire_schema:
        schema = canonical_payload_schema(schema)[0]

    enriched = enrich_role_query(query, "editor_author", schema)

    assert enriched.startswith(query)
    assert enriched != query
    assert len(enriched.encode("utf-8")) <= MAX_QUERY_UTF8_BYTES
    assert "HTML" in enriched[len(query):]
    assert "TipTap" in enriched[len(query):]
    assert "Description" in enriched[len(query):]
    assert enrich_role_query(enriched, "editor_author", schema) == enriched


def test_search_terms_are_present_in_the_governed_editor_corpus():
    root = Path(__file__).resolve().parents[3]
    corpus = "\n".join(
        (root / "knowledge" / name).read_text(encoding="utf-8")
        for name in ("07-ticket-body-guide.md", "08-editor-and-rendering.md")
    )

    for term in ("HTML", "TipTap", "Description"):
        assert term in corpus


@pytest.mark.parametrize("role_id", (
    "request_architect", "query_specialist", "knowledge_curator", "work_architect",
    "portfolio_analyst", "result_integrator", "auditor", "unknown_role",
))
def test_other_roles_are_byte_for_byte_unchanged(role_id):
    query = "기존 요청의 원문\n  유지"
    assert enrich_role_query(query, role_id, _editor_schema()) == query


@pytest.mark.parametrize("change", (
    "contract_only", "wrong_mode", "extra_field", "missing_field", "weaker_bounds",
    "open_properties", "missing_contract", "annotation_only_contract",
))
def test_similar_or_malformed_editor_schemas_do_not_get_enrichment(change):
    schema = _editor_schema()
    if change == "contract_only":
        schema = {"type": "object", "properties": {
            "contract": {"const": "editor-draft.v1"},
        }}
    elif change == "wrong_mode":
        schema["properties"]["mode"] = {"type": "string", "const": "other"}
    elif change == "extra_field":
        schema["properties"]["other"] = {"type": "string"}
    elif change == "missing_field":
        del schema["properties"]["content_html"]
    elif change == "weaker_bounds":
        del schema["properties"]["content_html"]["maxLength"]
    elif change == "open_properties":
        schema["additionalProperties"] = True
    elif change == "missing_contract":
        del schema["properties"]["contract"]
    elif change == "annotation_only_contract":
        del schema["properties"]["contract"]["const"]

    query = "원문 요청"
    assert enrich_role_query(query, "editor_author", schema) == query


@pytest.mark.parametrize("schema", (
    None, [], "not-json", '{"type":"object","type":"object"}',
    {"type": "object", "$ref": "https://invalid.example/schema"},
    {"type": "object", "properties": {"status": {"const": "retrieved"}},
     "required": ["status"], "additionalProperties": False},
))
def test_invalid_and_control_schemas_leave_query_unchanged(schema):
    query = "관련 없는 주제의 기존 검색"
    assert enrich_role_query(query, "editor_author", schema) == query


def test_editor_enrichment_never_mutates_the_payload_schema():
    schema = _editor_schema("comment")
    before = deepcopy(schema)

    enrich_role_query("검토 요청", "editor_author", schema)

    assert schema == before


@pytest.mark.parametrize("character", ("a", "한", "🙂"))
def test_enrichment_enforces_the_exact_utf8_limit_without_truncating(character):
    schema = _editor_schema("description")
    suffix = enrich_role_query("x", "editor_author", schema)[1:]
    available = MAX_QUERY_UTF8_BYTES - len(suffix.encode("utf-8"))
    repeats, remainder = divmod(available, len(character.encode("utf-8")))
    query = character * repeats + "a" * remainder

    enriched = enrich_role_query(query, "editor_author", schema)
    assert enriched.startswith(query)
    assert len(enriched.encode("utf-8")) == MAX_QUERY_UTF8_BYTES
    with pytest.raises(ContextGuardError, match="query_too_large"):
        enrich_role_query(query + "a", "editor_author", schema)


def test_original_oversize_query_is_rejected_even_when_role_is_not_enriched():
    with pytest.raises(ContextGuardError, match="query_too_large"):
        enrich_role_query("a" * (MAX_QUERY_UTF8_BYTES + 1), "auditor", {})


@pytest.mark.parametrize("query", (None, "", " \n\t", "\ud800"))
def test_invalid_query_fails_with_a_content_free_error(query):
    with pytest.raises(ContextGuardError):
        enrich_role_query(query, "editor_author", _editor_schema())


def test_existing_knowledge_benchmark_queries_remain_unchanged():
    from app.agent.maxis.knowledge_eval import build_synthetic_pack, load_suite

    suite = load_suite()
    for case in suite.cases:
        pack = build_synthetic_pack(
            case, suite_sha256=suite.sha256, knowledge_manifest_sha256="a" * 64,
        )
        assert enrich_role_query(pack.query, case.role_id, pack.payload_schema()) == pack.query
