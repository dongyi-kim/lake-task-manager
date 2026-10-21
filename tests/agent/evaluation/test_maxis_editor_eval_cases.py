"""Synthetic component inputs cannot be replaced with caller/live application data."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone

import pytest

from app.mock.world import World
from tools import agent_compose_eval as native
from tools import maxis_editor_eval_cases as E


def test_cases_reuse_native_inputs_schema_and_review_contract(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("live world configuration must not be read")

    monkeypatch.setattr(World, "__init__", forbidden)
    E._case_snapshots.cache_clear()
    cases = E.load_cases()
    assert [case["case_id"] for case in cases] == list(E.CASE_IDS)
    assert len(cases) == 5
    original = {row[0]: row for row in native.CASES}
    for case in cases:
        E.validate_case_spec(case)
        assert case["input"] == original[case["case_id"]][2]
        assert case["instruction"] == case["input"]["prompt"]
        assert case["seed_html"] == case["input"].get("seed_html", "")
        assert case["payload_schema"]["properties"]["mode"]["const"] == case["input"]["kind"]
        assert case["payload_schema"]["additionalProperties"] is False
        assert case["review"]["case_spec"] == native.CASE_REVIEW_SPECS[case["case_id"]]
        assert case["review"]["suite_elements"] == native.SUITE_REVIEW_ELEMENTS
        assert case["source_battery_version"] == native.BATTERY_VERSION
        assert case["provenance"]["synthetic_only"] is True
        assert case["comparable_to_source_suite"] is False
        json.dumps(case, allow_nan=False)


@pytest.mark.parametrize("field,value", [
    ("instruction", "caller supplied instruction"),
    ("seed_html", "caller supplied seed"),
    ("role_id", "work_architect"),
    ("evidence", []),
    ("payload_schema", {}),
    ("expected", {}),
])
def test_mutated_case_rejected_even_if_caller_rehashes(field, value):
    case = E.load_case("CMP3")
    case[field] = value
    case["provenance"]["case_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="repository-owned"):
        E.validate_case_spec(case)
    assert E.load_case("CMP3")[field] != value


@pytest.mark.parametrize("case_id", ["CMP1", "CMP4", "CMP5", "CMP7", "cmp3", "../CMP3", ""])
def test_only_explicit_generation_cases_can_be_loaded(case_id):
    with pytest.raises(ValueError, match="allowlist"):
        E.load_case(case_id)


def test_evidence_preserves_source_conflict_and_ambiguous_seed():
    case = E.load_case("CMP3")
    evidence = {row["source_id"]: row for row in case["evidence"]}
    family = json.loads(evidence["fixture.ticket.lineage-family"]["content"])
    current = next(row for row in family if row["key"] == "DL-9095")
    assert current["statusName"] == "In Progress"
    assert current["parentKey"] == "DL-9090"
    assert current["description"] == ""
    assert "다운스트림 2홉 조회: 완료" in evidence["fixture.document.lineage"]["content"]
    assert "성능 측정(2홉 100 노드 기준)" in evidence["fixture.document.lineage"]["content"]
    assert case["seed_html"] == (
        "<p>오늘 리니지 뷰어 성능 측정을 돌렸는데, p95 가 생각보다</p>"
    )
    assert len(evidence) == 3
    assert len(json.dumps(case["evidence"], ensure_ascii=False).encode()) < 4000
    for row in evidence.values():
        assert len(row["content"]) <= 1500
        assert row["content_sha256"] == hashlib.sha256(row["content"].encode()).hexdigest()
        assert row["canonical_id"].startswith("ltm:synthetic:editor:v1:")


def test_mutating_returned_data_does_not_change_later_loads():
    first = E.load_case("CMP3")
    original = copy.deepcopy(first)
    first["input"]["prompt"] = "changed"
    first["evidence"][0]["content"] = "changed"
    first["review"]["suite_elements"].clear()
    assert E.load_case("CMP3") == original


def test_source_change_is_detected_before_later_load_or_check(monkeypatch):
    E.load_case("CMP3")
    hashes = E._source_hashes()
    hashes["app/mock/world.py"] = "0" * 64
    monkeypatch.setattr(E, "_source_hashes", lambda: hashes)
    with pytest.raises(ValueError, match="source changed"):
        E.load_case("CMP3")
    with pytest.raises(ValueError, match="source changed"):
        E.check_output("CMP3", {"ok": True, "html": "<p>text</p>"})


def test_source_change_between_import_and_first_load_is_rejected(monkeypatch):
    E._case_snapshots.cache_clear()
    hashes = E._source_hashes()
    hashes["tools/agent_compose_eval.py"] = "0" * 64
    with monkeypatch.context() as patch:
        patch.setattr(E, "_source_hashes", lambda: hashes)
        with pytest.raises(ValueError, match="source changed after import"):
            E.load_case("CMP3")
    E.load_case("CMP3")


def test_native_seed_checker_rejects_invented_direction():
    seed = E.load_case("CMP3")["seed_html"]
    good = E.check_output("CMP3", {"ok": True, "html": seed + "<p>측정 방향 확인 필요</p>"})
    bad = E.check_output("CMP3", {"ok": True, "html": seed.replace("생각보다", "생각보다 높음")})
    assert good["sourceCasePass"] and good["automaticPass"]
    assert good["semanticCorrectnessAssessed"] is False
    assert not bad["sourceCasePass"]


def test_renderer_failure_is_distinct_from_native_case_check():
    result = {"ok": True, "html": "<h3>배경</h3><p>[~skcc.x1402]</p><h3>완료 조건</h3>"}
    check = E.check_output("CMP2", result)
    assert check["sourceCasePass"]
    assert "final renderer raw mention token" in check["rendererFlaws"]
    assert not check["automaticPass"]


def test_projection_preserves_native_input_and_replaces_only_known_people():
    from app.agent.maxis.context_guard import assert_safe_outbound_text

    case = E.load_case("CMP3")
    original = copy.deepcopy(case)
    first = E.project_case(case)
    second = E.project_case(case)
    assert first == second
    assert case == original
    assert first["input"] == case["input"]  # this native seed contains no identity
    assert len(first["person_map"]) == 3
    assert set(first["person_map"].values()) == {
        "skcc.x1402", "skcc.x1450", "skcc.x1315",
    }
    wire = {key: value for key, value in first.items() if key != "person_map"}
    serialized = json.dumps(wire, ensure_ascii=False)
    assert_safe_outbound_text(serialized)
    assert "skcc." not in serialized
    for marker in first["person_map"]:
        assert marker in serialized
    for row in first["evidence"]:
        assert row["content_sha256"] == hashlib.sha256(row["content"].encode()).hexdigest()
    first["evidence"][0]["content"] = "caller replacement"
    first["person_map"].clear()
    assert E.project_case(case) == second


def test_projection_rejects_arbitrary_text_before_pseudonymization():
    case = E.load_case("CMP6")
    case["input"]["prompt"] = "caller supplied identity"
    with pytest.raises(ValueError, match="snapshot"):
        E.project_case(case)


@pytest.mark.parametrize("case_id", E.CASE_IDS)
def test_projected_pack_passes_production_safety_and_wire_budget(case_id):
    from app.agent.maxis.context_guard import validate_pack_safety
    from app.agent.maxis.contracts import (
        EvidencePackV1, EvidenceScopeV1, EvidenceSourceV1, canonical_payload_schema,
    )

    case = E.load_case(case_id)
    wire = E.project_case(case)
    observed = datetime(2026, 9, 14, tzinfo=timezone.utc)
    sources = [EvidenceSourceV1(**row, retrieved_at=observed, updated_at=observed)
               for row in wire["evidence"]]
    intent = json.dumps(wire["input"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sources.append(EvidenceSourceV1(
        source_id="eval.editor.intent", kind="ltm", canonical_id="ltm:user-intent:editor-eval",
        title="Synthetic author request and existing seed", scope="synthetic-evaluation",
        content=intent, content_sha256=hashlib.sha256(intent.encode()).hexdigest(),
        retrieved_at=observed, updated_at=observed,
    ))
    schema, digest = canonical_payload_schema(case["payload_schema"])
    pack = EvidencePackV1(
        request_id="eval_test", use_case="editor_author", query=wire["instruction"],
        pseudonymous_user="ltm_1234567890123456", requested_at=observed,
        scope=EvidenceScopeV1(),
        sources=tuple(sources), freshness="fresh", partial=False,
        flags=("synthetic_evaluation",), knowledge_manifest_sha256="a" * 64,
        payload_schema_json=schema, payload_schema_sha256=digest,
    )
    validate_pack_safety(pack)
