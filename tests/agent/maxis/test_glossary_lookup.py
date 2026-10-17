"""Offline lookup contracts use invented terms only, never private definitions."""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import socket

import pytest


@pytest.fixture
def lookup_api(monkeypatch):
    return importlib.import_module("app.agent.maxis.glossary_lookup")


def document():
    return {
        "schemaVersion": "1.0.0", "glossaryId": "synthetic-glossary", "version": "1.0.0",
        "classification": "public", "asOf": "2026-10-08",
        "sources": [{
            "id": "source-one", "repository": "https://example.invalid/never-fetch",
            "revision": "a" * 40, "path": "invented.md", "section": "Terms",
            "observedAt": "2026-10-07", "classification": "public",
        }],
        "terms": [{
            "id": "alpha", "name": "Evidence", "aliases": ["Proof", " Alpha proof "],
            "scope": "Example records", "definition": "An invented support record.",
            "status": "documented", "sourceRefs": ["source-one"],
            "ownerModule": "example", "notEquivalentTo": [], "caveats": [],
        }],
    }


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def snapshot(api, value=None):
    return api.prepare_snapshot(encode(document() if value is None else value))


def add_term(value, **changes):
    term = copy.deepcopy(value["terms"][0])
    term.update({"id": "beta", "name": "Alternative", "scope": "Example metrics"} | changes)
    value["terms"].append(term)
    return term


@pytest.mark.parametrize("label", ["Evidence", "Proof", "  ＰＲＯＯＦ  ", "alpha\tPROOF"])
def test_exact_normalized_name_or_alias_preserves_definition_and_provenance(lookup_api, label):
    value = document()
    result = lookup_api.lookup(snapshot(lookup_api, value), label)
    assert result["status"] == "matched" and result["totalCandidates"] == 1
    assert result["truncated"] is False
    assert result["candidates"] == [{"term": value["terms"][0], "sources": value["sources"]}]
    assert result["snapshot"]["classification"] == "public"
    assert result["snapshot"]["asOf"] == value["asOf"]
    assert len(result["snapshot"]["contentSha256"]) == 64


def test_canonical_name_does_not_win_over_another_terms_alias(lookup_api):
    value = document()
    add_term(value, aliases=["EVIDENCE"])
    result = lookup_api.lookup(snapshot(lookup_api, value), "Evidence", max_candidates=1)
    assert result["status"] == "ambiguous" and result["totalCandidates"] == 2
    assert result["truncated"] is True
    assert [row["term"]["id"] for row in result["candidates"]] == ["alpha"]


def test_multiple_labels_of_same_term_are_one_candidate(lookup_api):
    value = document()
    value["terms"][0]["aliases"].extend([" EVIDENCE ", "ｅｖｉｄｅｎｃｅ"])
    result = lookup_api.lookup(snapshot(lookup_api, value), "Evidence")
    assert result["status"] == "matched" and result["totalCandidates"] == 1


def test_explicit_scope_is_whole_normalized_equality_without_fallback(lookup_api):
    value = document()
    add_term(value)
    prepared = snapshot(lookup_api, value)
    result = lookup_api.lookup(prepared, "Proof", scope="  EXAMPLE\tMETRICS ")
    assert result["status"] == "matched" and result["candidates"][0]["term"]["id"] == "beta"
    assert lookup_api.lookup(prepared, "Proof", scope="Example")["status"] == "not_found"
    assert lookup_api.lookup(prepared, "Proof", scope="wrong scope")["status"] == "not_found"


def test_same_scope_can_remain_ambiguous_and_input_order_does_not_select_winner(lookup_api):
    value = document()
    add_term(value, scope="Example records")
    first = lookup_api.lookup(snapshot(lookup_api, value), "Proof", scope="Example records")
    value["terms"].reverse()
    second = lookup_api.lookup(snapshot(lookup_api, value), "Proof", scope="Example records")
    assert first["status"] == second["status"] == "ambiguous"
    assert first["candidates"] == second["candidates"]


def test_collisions_after_validator_warning_limit_are_still_counted(lookup_api):
    value = document()
    for index in range(110):
        term = copy.deepcopy(value["terms"][0])
        term.update(id=f"term-{index:03d}", name=f"Term {index}")
        value["terms"].append(term)
    result = lookup_api.lookup(snapshot(lookup_api, value), "Proof", max_candidates=2)
    assert result["status"] == "ambiguous" and result["totalCandidates"] == 111
    assert result["truncated"] is True and len(result["candidates"]) == 2


@pytest.mark.parametrize("label", ["evid", "Evidence?", "An Evidence record", "record", "alpha", "example"])
def test_no_fuzzy_substring_id_owner_or_semantic_expansion(lookup_api, label):
    assert lookup_api.lookup(snapshot(lookup_api), label)["status"] == "not_found"


def test_not_equivalent_relationship_does_not_expand_lookup(lookup_api):
    value = document()
    add_term(value, aliases=[])
    value["terms"][0]["notEquivalentTo"] = ["beta"]
    result = lookup_api.lookup(snapshot(lookup_api, value), "Evidence")
    assert result["status"] == "matched" and result["totalCandidates"] == 1
    assert result["candidates"][0]["term"]["notEquivalentTo"] == ["beta"]


@pytest.mark.parametrize("status", ["documented", "proposed", "historical", "unknown"])
def test_record_status_caveats_and_internal_classification_are_not_promoted(lookup_api, status):
    value = document()
    value["classification"] = value["sources"][0]["classification"] = "internal"
    value["terms"][0].update(status=status, caveats=["Meaning requires explicit confirmation."])
    result = lookup_api.lookup(snapshot(lookup_api, value), "Proof")
    assert result["status"] == "matched"
    assert result["snapshot"]["classification"] == "internal"
    assert result["candidates"][0] == {"term": value["terms"][0], "sources": value["sources"]}


@pytest.mark.parametrize("kwargs", [
    {"label": ""}, {"label": " \t"}, {"label": None}, {"label": ["SECRET"]},
    {"label": "S" * 121}, {"label": "\ud800"}, {"scope": False}, {"scope": ""},
    {"scope": "S" * 241}, {"max_candidates": True}, {"max_candidates": 0},
    {"max_candidates": 65}, {"max_bytes": False}, {"max_bytes": 255},
    {"max_bytes": 65537},
])
def test_invalid_request_is_safe_fixed_diagnostic(lookup_api, kwargs):
    request = {"label": "SECRET"} | kwargs
    result = lookup_api.lookup(snapshot(lookup_api), **request)
    assert result["status"] == "invalid_request" and result["candidates"] == []
    assert "SECRET" not in json.dumps(result) and "snapshot" not in result


def test_budget_is_utf8_whole_result_not_partial_definition_or_caveat(lookup_api):
    value = document()
    value["terms"][0]["definition"] = "가" * 1000
    prepared = snapshot(lookup_api, value)
    result = lookup_api.lookup(prepared, "Proof")
    exact_bytes = len(encode(result))
    assert lookup_api.lookup(prepared, "Proof", max_bytes=exact_bytes) == result
    too_small = lookup_api.lookup(prepared, "Proof", max_bytes=exact_bytes - 1)
    assert too_small["status"] == "budget_exceeded" and too_small["candidates"] == []
    assert "가" not in json.dumps(too_small, ensure_ascii=False)


def test_lookup_is_io_free_and_outputs_are_detached(lookup_api, monkeypatch):
    value = document()
    before = copy.deepcopy(value)
    prepared = snapshot(lookup_api, value)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("lookup must not access files or network")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    first = lookup_api.lookup(prepared, "Proof")
    first["candidates"][0]["term"]["definition"] = "changed"
    first["candidates"][0]["sources"].clear()
    first["snapshot"]["classification"] = "internal"
    assert lookup_api.lookup(prepared, "Proof")["candidates"][0]["term"] == value["terms"][0]
    assert value == before
    assert "invented" not in repr(prepared)


@pytest.mark.parametrize("raw", [b'{"SECRET":1,"SECRET":2}', b'{"SECRET":NaN}', b'\xff', b'{}', "SECRET"])
def test_prepare_rejects_invalid_json_and_schema_without_echoing(lookup_api, raw):
    with pytest.raises(ValueError) as caught:
        lookup_api.prepare_snapshot(raw)
    assert str(caught.value) == "invalid_glossary"


def test_prepare_byte_limit_is_enforced_before_parsing(lookup_api):
    with pytest.raises(ValueError, match="^invalid_glossary$"):
        lookup_api.prepare_snapshot(b" " * (2 * 1024 * 1024 + 1))


def test_prepare_rejects_invalid_source_references(lookup_api):
    value = document()
    value["terms"][0]["sourceRefs"] = ["missing"]
    with pytest.raises(ValueError, match="^invalid_glossary$"):
        snapshot(lookup_api, value)


def test_snapshot_requires_preparation_and_is_immutable(lookup_api):
    from dataclasses import FrozenInstanceError

    with pytest.raises(TypeError, match="^use_prepare_snapshot$"):
        lookup_api.GlossarySnapshot("SECRET", (), ())
    with pytest.raises(TypeError, match="^use_prepare_snapshot$"):
        lookup_api.GlossarySnapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot(lookup_api)._identity = "changed"
    assert lookup_api.lookup({}, "SECRET") == {
        "status": "invalid_request", "code": "invalid_snapshot", "candidates": [],
    }


def test_schema_access_failure_is_not_reported_as_bad_user_data(lookup_api, monkeypatch):
    def unavailable(_value):
        raise OSError("PRIVATE-PATH")

    monkeypatch.setattr(lookup_api, "validate_document", unavailable)
    with pytest.raises(ValueError, match="^glossary_validation_unavailable$"):
        snapshot(lookup_api)


def test_multiple_sources_follow_reference_order_without_unrelated_sources(lookup_api):
    value = document()
    second = {**value["sources"][0], "id": "source-two", "revision": "b" * 40}
    value["sources"].extend([second, {**second, "id": "unrelated"}])
    value["terms"][0]["sourceRefs"] = ["source-two", "source-one"]
    result = lookup_api.lookup(snapshot(lookup_api, value), "Evidence")
    assert result["candidates"][0]["sources"] == [second, value["sources"][0]]


def test_snapshot_identity_ignores_json_key_order_and_whitespace(lookup_api):
    value = document()
    first = snapshot(lookup_api, value)
    second = lookup_api.prepare_snapshot(json.dumps(value, indent=4).encode("utf-8"))
    assert lookup_api.lookup(first, "Evidence") == lookup_api.lookup(second, "Evidence")


def test_not_found_preserves_snapshot_identity_and_obeys_byte_budget(lookup_api):
    prepared = snapshot(lookup_api)
    found = lookup_api.lookup(prepared, "Evidence")
    missing = lookup_api.lookup(prepared, "unregistered")
    assert missing == {"status": "not_found", "totalCandidates": 0, "truncated": False,
                       "snapshot": found["snapshot"], "candidates": []}
    budget = len(encode(missing))
    assert lookup_api.lookup(prepared, "unregistered", max_bytes=budget) == missing
    assert lookup_api.lookup(prepared, "unregistered", max_bytes=budget - 1)["status"] == "budget_exceeded"
