"""Agent 평가 방법이 보고서마다 바뀌지 않도록 versioned 계약을 검증한다."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools import agent_eval_protocol as E


ROOT = Path(__file__).resolve().parents[1]
CODEX_EVALUATOR = {
    "agentFamily": "codex",
    "agentModel": "test-codex-model",
    "directRawOutputReview": True,
    "ltmLlmUsedAsJudge": False,
}


def checklist_for_scores(scores):
    """Produce evidence-backed checklist states whose ceilings admit the given scores."""
    result = {}
    for dimension in E.load_protocol()["humanRubric"]["dimensions"]:
        dimension_id = dimension["id"]
        items = {
            item["id"]: {"status": "pass", "evidence": "raw output에서 충족 확인"}
            for item in dimension["checklist"]
        }
        item_ids = list(items)
        score = scores[dimension_id]
        if score <= 3.0:
            for item_id in item_ids[:2]:
                items[item_id] = {"status": "major", "evidence": "사용 전 주요 수정 필요"}
        elif score <= 3.5:
            items[item_ids[0]] = {"status": "major", "evidence": "material 결함 1건"}
        elif score <= 4.0:
            for item_id in item_ids[:2]:
                items[item_id] = {"status": "minor", "evidence": "국소 보완 필요"}
        elif score <= 4.5:
            items[item_ids[0]] = {"status": "minor", "evidence": "국소 보완 필요"}
        result[dimension_id] = items
    return result


def review_record(suite, case_id, scores):
    return {
        "suite": suite,
        "caseId": case_id,
        "repeatIndex": 1,
        "dimensionScores": scores,
        "checklistResults": checklist_for_scores(scores),
        "dimensionRationales": {key: "체크리스트와 실제 출력 근거로 판정" for key in scores},
        "outputExcerpt": "평가 대상 실제 출력 발췌",
    }


def test_protocol_versions_and_weights_are_explicit():
    protocol = E.load_protocol()
    assert re.fullmatch(r"\d+\.\d+\.\d+", protocol["protocolVersion"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", protocol["rubricVersion"])
    assert protocol["primaryEvidencePolicy"] == "complete-run-no-substitution"
    assert protocol["attemptPolicy"] == "retain-every-attempt"
    assert protocol["qualification"]["minimumRepetitions"] == 5
    assert sum(item["weight"] for item in protocol["humanRubric"]["dimensions"]) == pytest.approx(1)
    assert len(protocol["humanRubric"]["dimensions"]) == 5
    assert protocol["rubricVersion"] == "1.1.0"
    assert set(protocol["humanRubric"]["scoreAnchors"]) == {"1", "2", "3", "4", "5"}
    assert protocol["qualitativeEvaluation"]["allowedAgentFamilies"] == ["codex", "claude"]
    for dimension in protocol["humanRubric"]["dimensions"]:
        assert len(dimension["checklist"]) >= 6
        assert len({item["id"] for item in dimension["checklist"]}) == len(dimension["checklist"])
        assert all(item["question"] and item["majorWhen"] for item in dimension["checklist"])
        assert set(dimension["anchors"]) == {"1", "2", "3", "4", "5"}


def test_battery_manifest_covers_inputs_and_checker_source():
    def passes_one(value):
        return value == 1

    def passes_two(value):
        return value == 2

    first = [("CASE1", "설명", ["입력"], passes_one)]
    same = [("CASE1", "설명", ["입력"], passes_one)]
    changed_input = [("CASE1", "설명", ["다른 입력"], passes_one)]
    changed_checker = [("CASE1", "설명", ["입력"], passes_two)]
    assert E.battery_manifest_sha256(first) == E.battery_manifest_sha256(same)
    assert E.battery_manifest_sha256(first) != E.battery_manifest_sha256(changed_input)
    assert E.battery_manifest_sha256(first) != E.battery_manifest_sha256(changed_checker)


def test_human_score_uses_fixed_dimensions_weights_and_caps():
    scores = {
        "request_fulfillment": 4.5,
        "factual_grounding": 4.0,
        "contract_actionability": 3.5,
        "safety_uncertainty": 5.0,
        "communication_rendering": 4.0,
    }
    checklist = checklist_for_scores(scores)
    normal = E.score_human_case(scores, checklist_results=checklist)
    assert normal["rawScore"] == 4.2 and normal["caseScore"] == 4.2

    fabricated = E.score_human_case(
        scores, ["fabricated_fact_or_entity"], checklist_results=checklist,
    )
    assert fabricated["rawScore"] == 4.2
    assert fabricated["appliedCap"] == 2.0 and fabricated["caseScore"] == 2.0

    with pytest.raises(ValueError, match="increments"):
        E.score_human_case(
            {**scores, "request_fulfillment": 4.2}, checklist_results=checklist,
        )


def test_checklist_requires_every_item_evidence_and_enforces_score_ceiling():
    scores = {item["id"]: 5.0 for item in E.load_protocol()["humanRubric"]["dimensions"]}
    checklist = checklist_for_scores(scores)
    first_dimension = next(iter(checklist))
    first_item = next(iter(checklist[first_dimension]))

    missing = {key: dict(value) for key, value in checklist.items()}
    missing[first_dimension].pop(first_item)
    with pytest.raises(ValueError, match="checklist mismatch"):
        E.score_human_case(scores, checklist_results=missing)

    no_evidence = checklist_for_scores(scores)
    no_evidence[first_dimension][first_item]["evidence"] = ""
    with pytest.raises(ValueError, match="evidence is required"):
        E.score_human_case(scores, checklist_results=no_evidence)

    one_major = checklist_for_scores(scores)
    one_major[first_dimension][first_item] = {"status": "major", "evidence": "핵심 누락"}
    with pytest.raises(ValueError, match="exceeds checklist ceiling 3.5"):
        E.score_human_case(scores, checklist_results=one_major)


def test_human_aggregation_is_case_attempt_weighted_and_rejects_substitution():
    strong = {key: 5.0 for key in (
        "request_fulfillment", "factual_grounding", "contract_actionability",
        "safety_uncertainty", "communication_rendering",
    )}
    weak = {key: 3.0 for key in strong}
    reviews = [
        review_record("conversation", "S1", strong),
        review_record("create", "ONE1", weak),
        review_record("create", "ONE2", weak),
    ]
    result = E.aggregate_human_reviews(reviews, evaluator=CODEX_EVALUATOR)
    assert result["suiteScores"] == {"conversation": 5.0, "create": 3.0}
    assert result["overallScore"] == 3.67  # suite 평균의 4.0이 아니라 case-attempt 평균
    assert set(result["overallDimensionScores"].values()) == {3.67}
    assert result["suiteDimensionScores"]["conversation"]["request_fulfillment"] == 5.0
    assert sum(row["major"] for row in result["checklistResultCounts"].values()) == 20
    assert result["qualitativeEvaluator"]["agentFamily"] == "codex"
    assert result["observations"][0]["outputExcerpt"] == "평가 대상 실제 출력 발췌"
    with pytest.raises(ValueError, match="duplicate"):
        E.aggregate_human_reviews(reviews + [reviews[0]], evaluator=CODEX_EVALUATOR)


def test_qualitative_evaluator_rejects_ltm_llm_and_llm_as_judge():
    with pytest.raises(ValueError, match="must be one of"):
        E.validate_qualitative_evaluator({
            **CODEX_EVALUATOR, "agentFamily": "ltm-runtime-llm",
        })
    with pytest.raises(ValueError, match="cannot score"):
        E.validate_qualitative_evaluator({
            **CODEX_EVALUATOR, "ltmLlmUsedAsJudge": True,
        })
    with pytest.raises(ValueError, match="directly review"):
        E.validate_qualitative_evaluator({
            **CODEX_EVALUATOR, "directRawOutputReview": False,
        })


def test_latency_uses_nearest_rank_percentiles():
    assert E.summarize_latency([1, 2, 3, 4, 100]) == {"attempts": 5, "p50": 3.0, "p95": 100.0}


def test_run_metadata_marks_partial_single_run_as_exploratory(monkeypatch):
    monkeypatch.delenv("LTM_EVAL_RUN_KIND", raising=False)
    monkeypatch.delenv("LTM_EVAL_RUN_GROUP_ID", raising=False)
    monkeypatch.delenv("LTM_EVAL_REPETITIONS", raising=False)
    cases = [("A", "첫째"), ("B", "둘째")]
    metadata = E.build_run_metadata(
        suite="example",
        battery_version="1.0.0",
        cases=cases,
        selected_case_ids=["A"],
        model="gpt-4o",
        simple_model="gpt-4o-mini",
        prompt_version="candidate-v1",
    )
    assert metadata["protocolVersion"] == "1.0.0"
    assert metadata["battery"]["batteryVersion"] == "1.0.0"
    assert len(metadata["battery"]["batteryManifestSha256"]) == 64
    assert len(metadata["run"]["dataManifestSha256"]) == 64
    assert metadata["run"]["runKind"] == "exploratory"
    assert metadata["qualificationEligible"] is False
    assert "selected cases are not the full battery" in metadata["qualificationIneligibilityReasons"]


def test_comparability_key_changes_with_execution_policy(monkeypatch):
    monkeypatch.setenv("LTM_EVAL_REPETITIONS", "5")
    common = dict(
        suite="example", battery_version="1.0.0", cases=[("A", "첫째")],
        selected_case_ids=["A"], model="gpt-4o", simple_model="gpt-4o-mini",
        prompt_version="candidate-v1",
    )
    first = E.build_run_metadata(**common)["comparabilityKey"]
    monkeypatch.setenv("LTM_EVAL_CACHE_POLICY", "cold-cache-each-attempt")
    second = E.build_run_metadata(**common)["comparabilityKey"]
    assert first != second


def test_report_block_contains_versioned_criteria_and_validates(monkeypatch):
    monkeypatch.setenv("LTM_EVAL_RUN_GROUP_ID", "group-1")
    metadata = E.build_run_metadata(
        suite="conversation",
        battery_version="1.0.0",
        cases=[("S1", ["질문"])],
        selected_case_ids=["S1"],
        model="gpt-4o",
        simple_model="gpt-4o-mini",
        prompt_version="candidate-v1",
    )
    block = E.render_report_standard_block([metadata])
    assert E.validate_report(block) == []
    assert "complete-run-no-substitution" in block
    assert "arithmetic-mean-of-all-case-attempt-scores-across-suites" in block
    assert "evaluatorAgentFamily" in block
    assert "ltmLlmUsedAsJudge | `false`" in block
    assert "공통 anchor" in block
    assert "축별 checklist" in block
    assert "majorWhen" in block
    for dimension in E.load_protocol()["humanRubric"]["dimensions"]:
        assert dimension["label"] in block
        for item in dimension["checklist"]:
            assert f"`{item['id']}`" in block
    assert E.validate_report(block.replace("protocolVersion", "protocol"))


def test_all_primary_batteries_emit_versioned_metadata():
    expected = {
        "tools/agent_lang_ab.py": 'suite="conversation"',
        "tools/agent_compose_eval.py": 'suite="editor"',
        "tools/agent_create_suite.py": 'suite="create"',
    }
    for relative, suite_marker in expected.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert 'BATTERY_VERSION = "1.0.0"' in text, relative
        assert "build_run_metadata(" in text and suite_marker in text, relative
        assert '"evaluation"' in text, relative


def test_protocol_json_and_human_document_stay_in_sync():
    protocol = json.loads((ROOT / "app/agent/evaluation_protocol.json").read_text(encoding="utf-8"))
    guide = (ROOT / "app/agent/EVALUATION.md").read_text(encoding="utf-8")
    for token in (
        protocol["protocolVersion"],
        protocol["rubricVersion"],
        protocol["primaryEvidencePolicy"],
        "focused/closure",
        "최소 5회",
        "Codex 또는 Claude",
        "LLM-as-judge",
    ):
        assert token in guide
    for dimension in protocol["humanRubric"]["dimensions"]:
        assert dimension["label"] in guide
        for item in dimension["checklist"]:
            assert f"`{item['id']}`" in guide
