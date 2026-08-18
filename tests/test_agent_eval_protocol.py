"""Agent 평가 방법이 보고서마다 바뀌지 않도록 versioned 계약을 검증한다."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from tools import agent_eval_protocol as E
from tools.agent_eval_review_specs import review_specs


ROOT = Path(__file__).resolve().parents[1]
CODEX_EVALUATOR = {
    "agentFamily": "codex",
    "agentModel": "test-codex-model",
    "directRawOutputReview": True,
    "ltmLlmUsedAsJudge": False,
}


def specialized_contract(case_ids=("A",)):
    """Return a small but concrete suite/case review contract for protocol tests."""
    suite_elements = [{
        "id": "suite_evidence_path",
        "dimension": "factual_grounding",
        "question": "필요한 조회 경로와 실제 실행 증거가 일치하는가",
        "majorWhen": "필수 조회를 실행하지 않았거나 실행했다고 허위 표기",
        "evidenceSources": ["evaluationEvidence.queryPlan", "evaluationEvidence.queryResults"],
        "expected": {"requiredEvidence": ["queryPlan", "queryResults"]},
    }]
    case_specs = {
        case_id: {
            "goal": f"{case_id}의 고유 목표를 검증",
            "elements": [{
                "id": f"{case_id.lower()}_expected_result",
                "dimension": "request_fulfillment",
                "question": f"{case_id}에 요구된 구체 결과가 출력에 있는가",
                "majorWhen": "요구된 구체 결과를 누락하거나 반대로 답변",
                "evidenceSources": ["output.reply"],
                "expected": {"requiredText": case_id},
            }],
        }
        for case_id in case_ids
    }
    return suite_elements, case_specs


def specialized_case(case_id="A"):
    suite_elements, case_specs = specialized_contract((case_id,))
    registry = E.normalize_specialized_review_specs(
        [(case_id, "입력")], suite_elements, case_specs,
    )
    return E.specialized_review_spec_for_case(registry, case_id)


def specialized_results(case_id="A", *, status="pass"):
    return {
        "suite_evidence_path": {"status": status, "evidence": "raw 실행 증거 확인"},
        f"{case_id.lower()}_expected_result": {
            "status": "pass",
            "evidence": "실제 답변에서 고유 결과 확인",
        },
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
        "specializedReviewSpec": specialized_case(case_id),
        "specializedReviewResults": specialized_results(case_id),
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
    assert protocol["protocolVersion"] == "3.0.0"
    assert protocol["rubricVersion"] == "2.0.0"
    assert protocol["qualification"]["requiresSameBenchmarkKey"] is True
    assert "requiresSameComparabilityKey" not in protocol["qualification"]
    assert set(protocol["humanRubric"]["scoreAnchors"]) == {"1", "2", "3", "4", "5"}
    assert protocol["qualitativeEvaluation"]["allowedAgentFamilies"] == ["codex", "claude"]
    assert "generic-no-defect-claims-are-invalid" in \
        protocol["qualitativeEvaluation"]["checklistEvidencePolicy"]
    assert "자동 checker와 사람 판정 불일치" in protocol["requiredReportSections"]
    assert "배터리·case 특수 검토요소" in protocol["requiredReportSections"]
    assert protocol["specializedReview"]["requiredForEveryBattery"] is True
    assert protocol["isolation"]["cachePolicy"] == \
        "process-private-sqlite-and-cold-cache-each-case"
    assert "world-and-provider-store-sha256" in \
        protocol["isolation"]["worldMutationPolicy"]
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

    def helper_one(value):
        return value > 0

    def helper_two(value):
        return value >= 0

    first = [("CASE1", "설명", ["입력"], passes_one)]
    same = [("CASE1", "설명", ["입력"], passes_one)]
    changed_input = [("CASE1", "설명", ["다른 입력"], passes_one)]
    changed_checker = [("CASE1", "설명", ["입력"], passes_two)]
    suite_elements, case_specs = specialized_contract(("CASE1",))
    registry = E.normalize_specialized_review_specs(first, suite_elements, case_specs)
    changed_registry = json.loads(json.dumps(registry, ensure_ascii=False))
    changed_registry["cases"]["CASE1"]["elements"][0]["expected"] = {
        "requiredText": "다른 기대값",
    }
    assert E.battery_manifest_sha256(first) == E.battery_manifest_sha256(same)
    assert E.battery_manifest_sha256(first) != E.battery_manifest_sha256(changed_input)
    assert E.battery_manifest_sha256(first) != E.battery_manifest_sha256(changed_checker)
    assert E.battery_manifest_sha256(first, registry) != E.battery_manifest_sha256(
        first, changed_registry,
    )
    with_helper = E.battery_manifest_sha256(
        first, registry, checker_dependencies=(helper_one,),
    )
    assert with_helper == E.battery_manifest_sha256(
        first, registry, checker_dependencies=(helper_one,),
    )
    assert with_helper != E.battery_manifest_sha256(
        first, registry, checker_dependencies=(helper_two,),
    )


def test_create_battery_manifest_fingerprints_shared_checker_dependencies():
    import tools.agent_create_suite as create
    import tools.agent_eval_fact_relations as facts

    registry = E.normalize_specialized_review_specs(
        create.CASES, create.SUITE_REVIEW_ELEMENTS, create.CASE_REVIEW_SPECS,
    )
    lambda_only = E.battery_manifest_sha256(create.CASES, registry)
    complete = E.battery_manifest_sha256(
        create.CASES, registry,
        checker_dependencies=create.CREATE_CHECKER_DEPENDENCIES,
    )

    assert create.BATTERY_VERSION == "6.0.0"
    assert create.CREATE_CASE_CONTRACT_FLAW_CHECKERS["STARR1"] is \
        create._starr1_contract_flaws
    for dependency in (
        *facts.FACT_RELATION_DEPENDENCIES,
        create._draft_descriptions,
        create._STARR1_FACT_CONTRACTS,
        create._starr1_contract_flaws,
        create.CREATE_CASE_CONTRACT_FLAW_CHECKERS,
        create._case_specific_contract_flaws,
        create._STRUCTURED_FAILURE_RE,
        create._turn_execution_flaws,
        create.automatic_contract_flaws,
    ):
        assert any(item is dependency for item in create.CREATE_CHECKER_DEPENDENCIES)
    assert complete != lambda_only
    false_pass_dependencies = {
        *(id(dependency) for dependency in facts.FACT_RELATION_DEPENDENCIES),
        id(create._draft_descriptions),
        id(create._STARR1_FACT_CONTRACTS),
        id(create._starr1_contract_flaws),
        id(create.CREATE_CASE_CONTRACT_FLAW_CHECKERS),
        id(create._case_specific_contract_flaws),
        id(create._STRUCTURED_FAILURE_RE),
        id(create._turn_execution_flaws),
        id(create.automatic_contract_flaws),
    }
    without_false_pass_checks = tuple(
        dependency for dependency in create.CREATE_CHECKER_DEPENDENCIES
        if id(dependency) not in false_pass_dependencies
    )
    assert complete != E.battery_manifest_sha256(
        create.CASES, registry,
        checker_dependencies=without_false_pass_checks,
    )
    assert complete == E.battery_manifest_sha256(
        create.CASES, registry,
        checker_dependencies=create.CREATE_CHECKER_DEPENDENCIES,
    )


def test_human_score_uses_fixed_dimensions_weights_and_caps():
    scores = {
        "request_fulfillment": 4.5,
        "factual_grounding": 4.0,
        "contract_actionability": 3.5,
        "safety_uncertainty": 5.0,
        "communication_rendering": 4.0,
    }
    checklist = checklist_for_scores(scores)
    spec = specialized_case()
    results = specialized_results()
    normal = E.score_human_case(
        scores,
        checklist_results=checklist,
        specialized_review_spec=spec,
        specialized_review_results=results,
    )
    assert normal["rawScore"] == 4.2 and normal["caseScore"] == 4.2

    fabricated = E.score_human_case(
        scores, ["fabricated_fact_or_entity"],
        checklist_results=checklist,
        specialized_review_spec=spec,
        specialized_review_results=results,
    )
    assert fabricated["rawScore"] == 4.2
    assert fabricated["appliedCap"] == 2.0 and fabricated["caseScore"] == 2.0

    with pytest.raises(ValueError, match="increments"):
        E.score_human_case(
            {**scores, "request_fulfillment": 4.2},
            checklist_results=checklist,
            specialized_review_spec=spec,
            specialized_review_results=results,
        )


def test_checklist_requires_every_item_evidence_and_enforces_score_ceiling():
    scores = {item["id"]: 5.0 for item in E.load_protocol()["humanRubric"]["dimensions"]}
    checklist = checklist_for_scores(scores)
    spec = specialized_case()
    results = specialized_results()
    first_dimension = next(iter(checklist))
    first_item = next(iter(checklist[first_dimension]))

    missing = {key: dict(value) for key, value in checklist.items()}
    missing[first_dimension].pop(first_item)
    with pytest.raises(ValueError, match="checklist mismatch"):
        E.score_human_case(
            scores,
            checklist_results=missing,
            specialized_review_spec=spec,
            specialized_review_results=results,
        )

    no_evidence = checklist_for_scores(scores)
    no_evidence[first_dimension][first_item]["evidence"] = ""
    with pytest.raises(ValueError, match="evidence is required"):
        E.score_human_case(
            scores,
            checklist_results=no_evidence,
            specialized_review_spec=spec,
            specialized_review_results=results,
        )

    one_major = checklist_for_scores(scores)
    one_major[first_dimension][first_item] = {"status": "major", "evidence": "핵심 누락"}
    with pytest.raises(ValueError, match="exceeds combined checklist ceiling 3.5"):
        E.score_human_case(
            scores,
            checklist_results=one_major,
            specialized_review_spec=spec,
            specialized_review_results=results,
        )


def test_specialized_review_requires_every_element_evidence_and_caps_mapped_axis():
    scores = {item["id"]: 5.0 for item in E.load_protocol()["humanRubric"]["dimensions"]}
    checklist = checklist_for_scores(scores)
    spec = specialized_case()
    results = specialized_results()

    missing = dict(results)
    missing.pop("suite_evidence_path")
    with pytest.raises(ValueError, match="specialized review result mismatch"):
        E.score_human_case(
            scores,
            checklist_results=checklist,
            specialized_review_spec=spec,
            specialized_review_results=missing,
        )

    no_evidence = specialized_results()
    no_evidence["suite_evidence_path"]["evidence"] = ""
    with pytest.raises(ValueError, match="evidence is required"):
        E.score_human_case(
            scores,
            checklist_results=checklist,
            specialized_review_spec=spec,
            specialized_review_results=no_evidence,
        )

    major = specialized_results(status="major")
    with pytest.raises(ValueError, match="factual_grounding.*ceiling 3.5"):
        E.score_human_case(
            scores,
            checklist_results=checklist,
            specialized_review_spec=spec,
            specialized_review_results=major,
        )

    two_minor_scores = dict(scores)
    two_minor_scores["factual_grounding"] = 4.5
    common_minor = checklist_for_scores(two_minor_scores)
    with pytest.raises(ValueError, match="factual_grounding.*ceiling 4.0"):
        E.score_human_case(
            two_minor_scores,
            checklist_results=common_minor,
            specialized_review_spec=spec,
            specialized_review_results=specialized_results(status="minor"),
        )


def test_specialized_contract_rejects_missing_suite_or_case_coverage():
    with pytest.raises(ValueError, match="at least one suite element"):
        E.normalize_specialized_review_specs(
            [("A", "입력")], [], specialized_contract(("A",))[1],
        )
    suite_elements, case_specs = specialized_contract(("A",))
    with pytest.raises(ValueError, match="case mismatch"):
        E.normalize_specialized_review_specs(
            [("A", "입력"), ("B", "입력")], suite_elements, case_specs,
        )


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
    assert set(result["specializedReviewResultCounts"]) == {"conversation", "create"}
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
    monkeypatch.delenv("LTM_EVAL_PROCESS_ISOLATION", raising=False)
    cases = [("A", "첫째"), ("B", "둘째")]
    suite_elements, case_specs = specialized_contract(("A", "B"))
    metadata = E.build_run_metadata(
        suite="example",
        battery_version="1.0.0",
        cases=cases,
        selected_case_ids=["A"],
        model="gpt-4o",
        simple_model="gpt-4o-mini",
        prompt_version="candidate-v1",
        suite_review_elements=suite_elements,
        case_review_specs=case_specs,
    )
    assert metadata["protocolVersion"] == "3.0.0"
    assert metadata["battery"]["batteryVersion"] == "1.0.0"
    assert len(metadata["battery"]["batteryManifestSha256"]) == 64
    assert len(metadata["battery"]["specializedReviewSpecSha256"]) == 64
    assert len(metadata["run"]["dataManifestSha256"]) == 64
    assert len(metadata["run"]["candidateTreeSha256"]) == 64
    assert len(metadata["run"]["runtimePackageSetSha256"]) == 64
    assert len(metadata["benchmarkKey"]) == 64
    assert len(metadata["candidateKey"]) == 64
    assert len(metadata["comparabilityKey"]) == 64
    assert metadata["run"]["structuredOutputBackend"] == "instructor"
    assert metadata["run"]["structuredBackendVersion"]
    assert metadata["run"]["structuredBackendFallbackPolicy"] == "allow"
    assert metadata["run"]["runKind"] == "exploratory"
    assert metadata["qualificationEligible"] is False
    assert "selected cases are not the full battery" in metadata["qualificationIneligibilityReasons"]
    assert "evaluation process isolation is not recorded" in \
        metadata["qualificationIneligibilityReasons"]
    assert "provider endpoint identity is not recorded" in \
        metadata["qualificationIneligibilityReasons"]


def test_comparability_key_changes_with_execution_policy(monkeypatch):
    monkeypatch.setenv("LTM_EVAL_REPETITIONS", "5")
    monkeypatch.setenv("LTM_EVAL_PROCESS_ISOLATION", "separate-process-private-cache")
    monkeypatch.setenv("LTM_EVAL_EFFECTIVE_PROVIDER_IDENTITY_SHA256", "a" * 64)
    monkeypatch.setenv("LTM_AGENT_STRUCTURED_OUTPUT_FALLBACK", "forbid")
    suite_elements, case_specs = specialized_contract(("A",))
    common = dict(
        suite="example", battery_version="1.0.0", cases=[("A", "첫째")],
        selected_case_ids=["A"], model="gpt-4o", simple_model="gpt-4o-mini",
        prompt_version="candidate-v1",
        suite_review_elements=suite_elements, case_review_specs=case_specs,
    )
    first = E.build_run_metadata(**common)["comparabilityKey"]
    monkeypatch.setenv("LTM_EVAL_CACHE_POLICY", "cold-cache-each-attempt")
    second = E.build_run_metadata(**common)["comparabilityKey"]
    assert first != second
    monkeypatch.setenv("LTM_EVAL_PROCESS_ISOLATION", "shared-process")
    third = E.build_run_metadata(**common)["comparabilityKey"]
    assert second != third


def test_benchmark_and_candidate_keys_separate_workload_from_runtime_candidate(monkeypatch):
    monkeypatch.setenv("LTM_EVAL_REPETITIONS", "5")
    monkeypatch.setenv("LTM_EVAL_PROCESS_ISOLATION", "separate-process-private-cache")
    monkeypatch.setattr(E, "git_snapshot", lambda: {
        "commit": "abc123", "dirtyTrackedFiles": False,
        "gitStatusAvailable": True,
        "candidateTreeSha256": "a" * 64,
    })
    suite_elements, case_specs = specialized_contract(("A",))
    common = dict(
        suite="example", battery_version="1.0.0", cases=[("A", "first")],
        selected_case_ids=["A"], model="model-a", simple_model="model-a-small",
        prompt_version="candidate-v1", suite_review_elements=suite_elements,
        case_review_specs=case_specs,
    )

    first = E.build_run_metadata(**common)
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("LTM_AGENT_STRUCTURED_OUTPUT_BACKEND", "legacy")
    monkeypatch.setenv("LTM_EVAL_EFFECTIVE_PROVIDER_IDENTITY_SHA256", "b" * 64)
    second = E.build_run_metadata(**{
        **common, "model": "model-b", "simple_model": "model-b-small",
        "prompt_version": "candidate-v2",
    })

    assert first["benchmarkKey"] == second["benchmarkKey"]
    assert first["candidateKey"] != second["candidateKey"]
    assert first["comparabilityKey"] != second["comparabilityKey"]
    assert first["run"]["providerEndpointIdentitySha256"] != \
        second["run"]["providerEndpointIdentitySha256"]
    assert "endpoint-a" not in json.dumps(first, ensure_ascii=False)
    assert second["run"]["structuredOutputBackend"] == "legacy"

    monkeypatch.setenv("LTM_EVAL_CACHE_POLICY", "cold-cache-each-attempt")
    third = E.build_run_metadata(**common)
    assert third["benchmarkKey"] != first["benchmarkKey"]


def test_self_attested_or_invalid_provider_identity_is_not_qualification_metadata(monkeypatch):
    monkeypatch.setenv("LTM_EVAL_PROVIDER_ENDPOINT_ID", "operator-label")
    monkeypatch.setenv("LTM_EVAL_EFFECTIVE_PROVIDER_IDENTITY_SHA256", "not-a-digest")
    suite_elements, case_specs = specialized_contract(("A",))
    metadata = E.build_run_metadata(
        suite="example", battery_version="1.0.0", cases=[("A", "first")],
        selected_case_ids=["A"], model="model-a", simple_model="model-a-small",
        prompt_version="candidate-v1", suite_review_elements=suite_elements,
        case_review_specs=case_specs,
    )

    assert metadata["run"]["providerEndpointIdentitySha256"] == "unrecorded"
    assert "provider endpoint identity is not recorded" in \
        metadata["qualificationIneligibilityReasons"]


def test_git_status_failure_cannot_be_recorded_as_a_clean_candidate(monkeypatch):
    def checked(*args):
        if args[:2] == ("rev-parse", "HEAD"):
            return True, "abc123"
        return False, ""

    monkeypatch.setattr(E, "_git_checked", checked)
    snapshot = E.git_snapshot()
    assert snapshot["commit"] == "abc123"
    assert snapshot["gitStatusAvailable"] is False

    monkeypatch.setattr(E, "git_snapshot", lambda: snapshot)
    suite_elements, case_specs = specialized_contract(("A",))
    metadata = E.build_run_metadata(
        suite="example", battery_version="1.0.0", cases=[("A", "first")],
        selected_case_ids=["A"], model="model-a", simple_model="model-a-small",
        prompt_version="candidate-v1", suite_review_elements=suite_elements,
        case_review_specs=case_specs,
    )
    assert metadata["run"]["gitStatusAvailable"] is False
    assert metadata["run"]["candidateDirty"] is True
    assert "git working-tree status is unavailable" in \
        metadata["qualificationIneligibilityReasons"]


def test_harness_tree_changes_the_benchmark_not_the_candidate(monkeypatch):
    monkeypatch.setattr(E, "git_snapshot", lambda: {
        "commit": "abc123", "gitStatusAvailable": True,
        "dirtyTrackedFiles": False, "dirtyUntrackedFiles": False,
        "candidateTreeSha256": "a" * 64,
    })
    monkeypatch.setattr(E, "evaluation_harness_tree_sha256", lambda: "1" * 64)
    suite_elements, case_specs = specialized_contract(("A",))
    common = dict(
        suite="example", battery_version="1.0.0", cases=[("A", "first")],
        selected_case_ids=["A"], model="model-a", simple_model="model-a-small",
        prompt_version="candidate-v1", suite_review_elements=suite_elements,
        case_review_specs=case_specs,
    )
    first = E.build_run_metadata(**common)
    monkeypatch.setattr(E, "evaluation_harness_tree_sha256", lambda: "2" * 64)
    second = E.build_run_metadata(**common)
    assert first["benchmarkKey"] != second["benchmarkKey"]
    assert first["candidateKey"] == second["candidateKey"]


def test_harness_fingerprint_covers_the_user_view_capture_runner():
    assert E.ROOT / "tools" / "agent_user_review.py" in E.EVALUATION_HARNESS_INPUTS


def test_report_block_contains_versioned_criteria_and_validates(monkeypatch):
    monkeypatch.setenv("LTM_EVAL_RUN_GROUP_ID", "group-1")
    suite_elements, case_specs = specialized_contract(("S1",))
    metadata = E.build_run_metadata(
        suite="conversation",
        battery_version="1.0.0",
        cases=[("S1", ["질문"])],
        selected_case_ids=["S1"],
        model="gpt-4o",
        simple_model="gpt-4o-mini",
        prompt_version="candidate-v1",
        suite_review_elements=suite_elements,
        case_review_specs=case_specs,
    )
    block = E.render_report_standard_block([metadata])
    assert E.validate_report(block) == []
    assert "complete-run-no-substitution" in block
    assert "arithmetic-mean-of-all-case-attempt-scores-across-suites" in block
    assert "evaluatorAgentFamily" in block
    assert "ltmLlmUsedAsJudge | `false`" in block
    assert "processIsolation" in block
    assert "evaluationHarnessTreeSha256" in block
    assert "### Candidate identity" in block
    assert "## 실행 격리" in block
    assert "process-private-sqlite-and-cold-cache-each-case" in block
    assert "공통 anchor" in block
    assert "축별 checklist" in block
    assert "majorWhen" in block
    assert "배터리·case 특수 검토요소" in block
    assert "specializedReviewSpecSha256" in block
    assert "suite_evidence_path" in block
    assert "s1_expected_result" in block
    assert 'expected: `{"requiredText":"S1"}`' in block
    assert "evaluationEvidence.queryPlan" in block
    assert "majorWhen:" in block
    for dimension in E.load_protocol()["humanRubric"]["dimensions"]:
        assert dimension["label"] in block
        for item in dimension["checklist"]:
            assert f"`{item['id']}`" in block
    assert E.validate_report(block.replace("protocolVersion", "protocol"))


def test_report_renders_every_distinct_candidate_identity(monkeypatch):
    suite_elements, case_specs = specialized_contract(("A",))
    common = dict(
        suite="example", battery_version="1.0.0", cases=[("A", "first")],
        selected_case_ids=["A"], prompt_version="candidate-v1",
        suite_review_elements=suite_elements, case_review_specs=case_specs,
    )
    monkeypatch.setenv("LTM_EVAL_RUN_GROUP_ID", "paired-candidate-a")
    monkeypatch.setenv("LTM_EVAL_CANDIDATE_ORDER_INDEX", "a-first")
    first = E.build_run_metadata(
        **common, model="model-a", simple_model="simple-a",
    )
    monkeypatch.setenv("LTM_EVAL_RUN_GROUP_ID", "paired-candidate-b")
    monkeypatch.setenv("LTM_EVAL_CANDIDATE_ORDER_INDEX", "b-second")
    second = E.build_run_metadata(
        **{**common, "suite": "example-b"}, model="model-b", simple_model="simple-b",
    )
    block = E.render_report_standard_block([first, second])
    assert "model-a" in block and "model-b" in block
    assert "simple-a" in block and "simple-b" in block
    assert "paired-candidate-a" in block and "paired-candidate-b" in block
    assert "a-first" in block and "b-second" in block
    assert first["candidateKey"] in block and second["candidateKey"] in block
    assert E.validate_report(block) == []


def test_historical_base_report_keeps_its_declared_v1_contract():
    report = (ROOT / "research/agent-improvement/evaluations/2026-08-15-base-rubric-1.2-full.md")
    text = report.read_text(encoding="utf-8")
    assert "protocolVersion | `1.0.0`" in text
    assert "3.72" in text
    assert E.validate_report(text) == []


def test_all_primary_batteries_emit_versioned_metadata():
    expected = {
        "tools/agent_lang_ab.py": ('suite="conversation"', "5.0.0"),
        "tools/agent_compose_eval.py": ('suite="editor"', "4.0.0"),
        "tools/agent_create_suite.py": ('suite="create"', "6.0.0"),
        "tools/agent_meeting_eval.py": ('suite="meeting"', "4.0.0"),
        "tools/agent_context_change_eval.py": ('suite="ctx-chg"', "3.0.0"),
    }
    for relative, (suite_marker, battery_version) in expected.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert f'BATTERY_VERSION = "{battery_version}"' in text, relative
        assert ("build_run_metadata(" in text or "run_scenario_suite(" in text), relative
        assert suite_marker in text, relative
        assert "suite_review_elements=SUITE_REVIEW_ELEMENTS" in text, relative
        assert "case_review_specs=CASE_REVIEW_SPECS" in text, relative
        if "run_scenario_suite(" not in text:
            assert '"evaluation"' in text, relative


def test_primary_battery_manifests_fingerprint_shared_automatic_contracts():
    from tools import agent_lang_ab as conversation
    from tools import agent_compose_eval as editor
    from tools import agent_context_change_eval as context
    from tools import agent_create_suite as create
    from tools import agent_eval_request_fields as request_fields
    from tools import agent_meeting_eval as meeting
    from tools import agent_scenario_eval as scenario
    from tools.agent_eval_contracts import (
        AUTOMATIC_CONTRACT_DEPENDENCIES,
        EDITOR_RENDERER_CONTRACT_DEPENDENCIES,
        automatic_contract_flaws,
        editor_renderer_contract_flaws,
    )
    from tools.agent_eval_claims import EVALUATION_CLAIM_CONTRACT_DEPENDENCIES

    assert automatic_contract_flaws in AUTOMATIC_CONTRACT_DEPENDENCIES
    assert editor_renderer_contract_flaws in EDITOR_RENDERER_CONTRACT_DEPENDENCIES
    assert all(dependency in create.CREATE_CHECKER_DEPENDENCIES
               for dependency in AUTOMATIC_CONTRACT_DEPENDENCIES)
    assert all(dependency in editor.EDITOR_CHECKER_DEPENDENCIES
               for dependency in EDITOR_RENDERER_CONTRACT_DEPENDENCIES)
    assert all(dependency in conversation.CONVERSATION_CHECKER_DEPENDENCIES
               for dependency in EVALUATION_CLAIM_CONTRACT_DEPENDENCIES)
    assert conversation._measurement_contract_flaws in \
        conversation.CONVERSATION_CHECKER_DEPENDENCIES
    assert conversation._scenario_contract_flaws in \
        conversation.CONVERSATION_CHECKER_DEPENDENCIES
    assert context.CONTEXT_CHECKER_DEPENDENCIES
    assert meeting.MEETING_CHECKER_DEPENDENCIES
    assert all(dependency in context.CONTEXT_CHECKER_DEPENDENCIES
               for dependency in request_fields.REQUEST_FIELD_DEPENDENCIES)
    assert context._context_case_checker in context.CONTEXT_CHECKER_DEPENDENCIES
    assert meeting._map_outcome_rows in meeting.MEETING_CHECKER_DEPENDENCIES

    runner_source = (ROOT / "tools/agent_scenario_eval.py").read_text(encoding="utf-8")
    conversation_source = (ROOT / "tools/agent_lang_ab.py").read_text(encoding="utf-8")
    assert "checker_dependencies=(*AUTOMATIC_CONTRACT_DEPENDENCIES" in runner_source
    assert "automatic_flaws = automatic_contract_flaws(outputs)" in runner_source
    assert "*AUTOMATIC_CONTRACT_DEPENDENCIES" in conversation_source
    assert "checker_dependencies=CONVERSATION_CHECKER_DEPENDENCIES" in conversation_source
    assert scenario.automatic_contract_flaws is automatic_contract_flaws

    registry = E.normalize_specialized_review_specs(
        conversation.SCENARIOS, conversation.SUITE_REVIEW_ELEMENTS,
        conversation.CASE_REVIEW_SPECS,
    )
    complete = E.battery_manifest_sha256(
        conversation.SCENARIOS, registry,
        checker_dependencies=conversation.CONVERSATION_CHECKER_DEPENDENCIES,
    )
    without_claim_gates = tuple(
        dependency for dependency in conversation.CONVERSATION_CHECKER_DEPENDENCIES
        if all(dependency is not claim_dependency
               for claim_dependency in EVALUATION_CLAIM_CONTRACT_DEPENDENCIES)
    )
    assert complete != E.battery_manifest_sha256(
        conversation.SCENARIOS, registry,
        checker_dependencies=without_claim_gates,
    )
    assert complete == E.battery_manifest_sha256(
        conversation.SCENARIOS, registry,
        checker_dependencies=conversation.CONVERSATION_CHECKER_DEPENDENCIES,
    )


def _assigned_case_ids(relative, assignment_name):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == assignment_name
                   for target in node.targets):
            continue
        return [
            str(item.elts[0].value)
            for item in node.value.elts
            if isinstance(item, ast.Tuple)
            and item.elts
            and isinstance(item.elts[0], ast.Constant)
        ]
    raise AssertionError(f"{assignment_name} not found in {relative}")


def test_every_primary_case_has_an_exact_specialized_review_contract():
    declarations = {
        "conversation": ("tools/agent_lang_ab.py", "SCENARIOS"),
        "editor": ("tools/agent_compose_eval.py", "CASES"),
        "create": ("tools/agent_create_suite.py", "CASES"),
        "meeting": ("tools/agent_meeting_eval.py", "CASES"),
        "ctx-chg": ("tools/agent_context_change_eval.py", "CASES"),
    }
    for suite, (relative, assignment) in declarations.items():
        case_ids = _assigned_case_ids(relative, assignment)
        suite_elements, case_specs = review_specs(suite)
        assert set(case_specs) == set(case_ids), suite
        registry = E.normalize_specialized_review_specs(
            [(case_id, "input") for case_id in case_ids], suite_elements, case_specs,
        )
        assert set(registry["cases"]) == set(case_ids)
        assert all(registry["cases"][case_id]["elements"] for case_id in case_ids)


def test_history_and_external_research_cases_name_concrete_review_evidence():
    _, conversation = review_specs("conversation")
    history = {item["id"]: item for item in conversation["S3-이력"]["elements"]}
    expected_tickets = {
        "DL-9041", "DL-9042", "DL-9043", "DL-9044",
        "DL-9045", "DL-9046", "DL-9047", "DL-9062",
    }
    assert set(history["s3_history_ticket_coverage"]["expected"]["requiredTicketKeys"]) == \
        expected_tickets
    assert len(history["s3_history_event_sequence"]["expected"]["requiredMilestones"]) == 8

    research = {item["id"]: item for item in conversation["S7-내외부조사"]["elements"]}
    internal_expected = research["s7_internal_research_coverage"]["expected"]
    assert set(internal_expected["requiredSourceClasses"]) == {
        "jira", "confluence-or-comment",
    }
    assert internal_expected["requiredTicketKeys"] == ["DL-7001"]
    assert internal_expected["requiredDocumentTitles"] == [
        "[Lake] Iceberg Puffin NDV 적용 검토 노트",
    ]
    assert research["s7_external_research_coverage"]["expected"]["externalQueryTermsAny"] == [
        "Apache Iceberg", "Puffin", "NDV statistics",
    ]
    assert "DL-" in research["s7_external_research_coverage"]["expected"][
        "forbiddenExternalTokens"
    ]
    assert research["s7_internal_external_separation"]["expected"]["requiredSections"] == [
        "내부 근거", "외부 근거", "판단", "확인 필요",
    ]

    evidence_quality = {
        item["id"]: item for item in conversation["S8-복합근거품질"]["elements"]
    }
    assert evidence_quality["s8_source_results"]["expected"]["requiredSourceClasses"] == [
        "jira-ticket", "jira-comment", "confluence", "official-web",
    ]
    confidence = evidence_quality["s8_source_confidence_and_fitness"]["expected"]
    assert set(confidence["confidenceFactors"]) == {
        "authority", "directness", "recency", "corroboration",
    }
    renderer = evidence_quality["s8_visual_rendering"]
    assert "manualUi.desktopScreenshot" in renderer["evidenceSources"]
    assert renderer["expected"]["requiredViewports"] == [
        "desktop", "narrow-with-agent-side-panel",
    ]
    source_index = evidence_quality["s8_single_source_index"]["expected"]
    assert source_index["citationClusters"] == \
        "multiple sources at one location use [4][5][10]"
    assert source_index["everyCitationBracketHyperlinked"] is True


def test_meeting_and_context_change_cases_have_concrete_review_evidence():
    meeting_suite, meeting = review_specs("meeting")
    suite_ids = {item["id"] for item in meeting_suite}
    assert "meeting_identity_normalization" in suite_ids
    assert "meeting_research_then_interview" in suite_ids
    assert "meeting_heterogeneous_note_reconstruction" in suite_ids
    assert "meeting_actor_role_separation" in suite_ids
    mtg5 = {item["id"]: item for item in meeting["MTG5"]["elements"]}
    expected = mtg5["mtg5_research_gap_interview"]["expected"]
    assert expected["requiredCandidates"] == ["skcc.x1103", "skcc.x1327"]
    assert expected["unresolvedTerm"] == "PSR"
    assert expected["noDraftBeforeChoice"] is True
    assert meeting["MTG7"]["elements"][0]["expected"]["assignments"] == [
        "skcc.i2011", "skcc.x1402", "unassigned",
    ]
    assert meeting["MTG9"]["elements"][0]["expected"]["forbiddenFields"] == [
        "priority", "components", "labels",
    ]

    context_suite, context = review_specs("ctx-chg")
    assert {item["id"] for item in context_suite} >= {
        "ctx_latest_request_precedence", "ctx_relevant_memory_selection",
        "ctx_pending_replacement",
    }
    ctx3 = {item["id"]: item for item in context["CTX3"]["elements"]}
    assert ctx3["ctx3_superseded_writes"]["expected"]["exactChanges"] == {
        "summary": "[Catalog] Puffin NDV 결과 템플릿 정리",
    }


def test_rubric_scores_question_judgment_not_the_presence_of_questions():
    protocol = E.load_protocol()
    assert protocol["rubricVersion"] == "2.0.0"
    safety = next(d for d in protocol["humanRubric"]["dimensions"]
                  if d["id"] == "safety_uncertainty")
    checklist = {item["id"]: item for item in safety["checklist"]}
    assert "required_input_interview" in checklist
    assert "question_economy" in checklist
    assert "알아서" in checklist["required_input_interview"]["question"]
    assert "내부 조회" in checklist["question_economy"]["question"]


def test_create_battery_covers_required_and_delegated_question_boundaries():
    text = (ROOT / "tools/agent_create_suite.py").read_text(encoding="utf-8")
    for case_id in ("ASKD1", "ASKD2", "ASKD3", "ASKD4", "AMB1"):
        assert f'("{case_id}"' in text
    assert "필수정보 충족 시 바로 초안" in text
    assert '"이 구조로 진행한다"' not in text, (
        "`알아서`로 위임한 STR2가 불필요한 구조 재확인을 정답으로 강제하면 안 된다")
    for token in ("Lake 배치 적재 테이블 중 신규 등록 30개", "배치 이름", "상위 Task",
                  "30분에서 45분", "bool(its[0].get(\"epic\") or its[0].get(\"parent\"))"):
        assert token in text


def test_create_v5_review_contract_names_field_source_and_auditor_false_positives():
    suite, create = review_specs("create")
    suite_by_id = {item["id"]: item for item in suite}
    request_expected = suite_by_id["create_request_to_payload"]["expected"]
    assert "YYYY-MM-DD" in request_expected["exactDueRule"]
    assert "bare 차" in request_expected["ordinalRule"]
    interview_expected = suite_by_id["create_interview_boundary"]["expected"]
    assert interview_expected["requiredQuestionContract"] == [
        "required_input=true", "concrete why_required",
    ]

    starr = {item["id"]: item for item in create["STARR1"]["elements"]}
    assert set(starr) == {
        "starr1_cross_output", "starr1_internal_validation_state",
        "starr1_direct_source_relevance", "starr1_auditor_field_fidelity",
    }
    assert starr["starr1_internal_validation_state"]["expected"][
        "requiredTicketKeys"
    ] == ["DL-9200", "DL-9201", "DL-9202"]
    assert "docs/README.md" in starr["starr1_direct_source_relevance"][
        "expected"
    ]["forbiddenDirectSources"]
    assert starr["starr1_auditor_field_fidelity"]["expected"][
        "auditorFalsePassForbidden"
    ] is True


def test_editor_battery_rejects_seed_loss_and_reference_renderer_contradictions():
    text = (ROOT / "tools/agent_compose_eval.py").read_text(encoding="utf-8")
    contract_text = (ROOT / "tools/agent_eval_contracts.py").read_text(encoding="utf-8")
    assert "_seed_preserved" in text
    assert "seed in _txt" in text
    assert "_editor_contract_flaws" in text
    assert "editor_renderer_contract_flaws(result)" in text
    assert "ticket marker 안에 이미 렌더된 anchor를 이중 삽입" in contract_text
    assert "resolved ticket을 미확인으로 경고" in contract_text
    for defect in ("pseudo ticket", "raw mention", "raw Markdown", "bare URL"):
        assert defect in contract_text


def test_primary_battery_metrics_have_one_canonical_schema():
    metrics = E.quantitative_metrics(
        attempts=2, duration_seconds=3.26, calls=4, prompt_tokens=5,
        completion_tokens=6, total_tokens=11, cached_tokens=2, cost_usd=0.1234567,
    )
    assert metrics == {
        "attempts": 2, "durationSeconds": 3.3, "calls": 4,
        "promptTokens": 5, "completionTokens": 6, "totalTokens": 11,
        "cachedTokens": 2, "costUsd": 0.123457,
    }
    for relative in ("tools/agent_lang_ab.py", "tools/agent_compose_eval.py",
                     "tools/agent_create_suite.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "quantitative_metrics(" in text, relative
        assert '"metrics"' in text, relative


def _fast_path_specs():
    return {
        "result.structure_tree.v1": {
            "authority": "work_architect.structure_stage",
            "ownerNode": "result_integrator", "savedCalls": 1,
        },
        "research.single_bounded_query": {
            "authority": "request-plan.v1+query-plan.v1+query-results.v1",
            "ownerNode": "research_analyst",
            "savedCalls": 1,
        },
    }


def _fast_path_event(phase, *, path="result.structure_tree.v1", eligible=True,
                     scope="a" * 24):
    spec = _fast_path_specs()[path]
    return {
        "contract": "typed-fast-path-event.v1", "phase": phase,
        "pathId": path, "authority": spec["authority"], "eligible": eligible,
        "estimatedSavedCalls": spec["savedCalls"] if eligible else 0,
        "scopeId": scope,
    }


def test_typed_fast_path_metrics_separate_opportunity_commit_and_verified_savings():
    usage = {
        "calls": 1,
        "callsDetail": [{"node": "research_analyst", "fastPathScopeId": "b" * 24}],
        "fastPathEvents": [
            _fast_path_event("evaluated", scope="a" * 24),
            _fast_path_event("committed", scope="a" * 24),
            _fast_path_event("evaluated", path="research.single_bounded_query",
                             eligible=False, scope="b" * 24),
            _fast_path_event("evaluated", scope="c" * 24),
        ],
    }

    metrics = E.typed_fast_path_metrics([usage], path_specs=_fast_path_specs())

    assert metrics == {
        "contract": "typed-fast-path-metrics.v1",
        "opportunities": 3,
        "hits": 2,
        "misses": 1,
        "committed": 1,
        "eligibleNotCommitted": 1,
        "estimatedSavedCalls": 1,
        "verifiedSavedCalls": 1,
        "parityFailures": 0,
        "byPath": {
            "research.single_bounded_query": {
                "opportunities": 1, "hits": 0, "misses": 1, "committed": 0,
                "eligibleNotCommitted": 0, "estimatedSavedCalls": 0,
                "verifiedSavedCalls": 0, "parityFailures": 0,
            },
            "result.structure_tree.v1": {
                "opportunities": 2, "hits": 2, "misses": 0, "committed": 1,
                "eligibleNotCommitted": 1, "estimatedSavedCalls": 1,
                "verifiedSavedCalls": 1, "parityFailures": 0,
            },
        },
    }
    assert metrics["opportunities"] == metrics["hits"] + metrics["misses"]


def test_typed_fast_path_metrics_fail_closed_on_scope_calls_and_event_parity():
    usage = {
        "calls": 2,
        "callsDetail": [
            {"fastPathScopeId": "a" * 24},
            {"fastPathScopeId": "z" * 24},
        ],
        "fastPathInvalidEvents": 1,
        "fastPathEvents": [
            _fast_path_event("evaluated", scope="a" * 24),
            _fast_path_event("committed", scope="a" * 24),
            _fast_path_event("committed", scope="a" * 24),
            _fast_path_event("committed", scope="b" * 24),
        ],
    }

    metrics = E.typed_fast_path_metrics([usage], path_specs=_fast_path_specs())

    assert metrics["opportunities"] == 0
    assert metrics["committed"] == 0
    assert metrics["estimatedSavedCalls"] == 0
    assert metrics["verifiedSavedCalls"] == 0
    assert metrics["parityFailures"] >= 4
    assert metrics["byPath"]["result.structure_tree.v1"]["parityFailures"] >= 3


def test_typed_fast_path_metrics_do_not_verify_commit_with_actual_scoped_call():
    usage = {
        "calls": 1,
        "callsDetail": [{"fastPathScopeId": "a" * 24}],
        "fastPathEvents": [
            _fast_path_event("evaluated"),
            _fast_path_event("committed"),
        ],
    }

    metrics = E.typed_fast_path_metrics([usage], path_specs=_fast_path_specs())

    assert metrics["committed"] == 1
    assert metrics["estimatedSavedCalls"] == 1
    assert metrics["verifiedSavedCalls"] == 0
    assert metrics["parityFailures"] == 1


def test_typed_fast_path_metrics_do_not_verify_when_any_call_scope_is_unknown():
    metrics = E.typed_fast_path_metrics([{
        "calls": 1,
        "callsDetail": [{}],
        "fastPathEvents": [
            _fast_path_event("evaluated"),
            _fast_path_event("committed"),
        ],
    }], path_specs=_fast_path_specs())

    assert metrics["estimatedSavedCalls"] == 1
    assert metrics["verifiedSavedCalls"] == 0
    assert metrics["parityFailures"] == 1


def test_typed_fast_path_metrics_do_not_verify_valid_pair_beside_rejected_event():
    metrics = E.typed_fast_path_metrics([{
        "calls": 0,
        "callsDetail": [],
        "fastPathInvalidEvents": 1,
        "fastPathEvents": [
            _fast_path_event("evaluated"),
            _fast_path_event("committed"),
        ],
    }], path_specs=_fast_path_specs())

    assert metrics["committed"] == 1
    assert metrics["estimatedSavedCalls"] == 1
    assert metrics["verifiedSavedCalls"] == 0
    assert metrics["parityFailures"] == 1


def test_typed_fast_path_metrics_require_calls_detail_cardinality_for_verification():
    usage = {
        "calls": 1,
        "callsDetail": [],
        "fastPathEvents": [
            _fast_path_event("evaluated"),
            _fast_path_event("committed"),
        ],
    }

    metrics = E.typed_fast_path_metrics([usage], path_specs=_fast_path_specs())

    assert metrics["estimatedSavedCalls"] == 1
    assert metrics["verifiedSavedCalls"] == 0
    assert metrics["parityFailures"] >= 1


def test_typed_fast_path_metrics_accept_meter_zero_call_snapshot_without_detail_rows():
    metrics = E.typed_fast_path_metrics([{
        "calls": 0,
        "fastPathEvents": [
            _fast_path_event("evaluated"),
            _fast_path_event("committed"),
        ],
    }], path_specs=_fast_path_specs())

    assert metrics["estimatedSavedCalls"] == 1
    assert metrics["verifiedSavedCalls"] == 1
    assert metrics["parityFailures"] == 0


def test_typed_fast_path_metrics_reject_invalid_falsy_shapes_and_duplicate_evaluation():
    valid = [_fast_path_event("evaluated"), _fast_path_event("committed")]
    for usage in (
        {"calls": 0, "callsDetail": [], "fastPathEvents": {}},
        {"calls": 0, "callsDetail": {}, "fastPathEvents": valid},
        {"callsDetail": [], "fastPathEvents": valid},
    ):
        metrics = E.typed_fast_path_metrics([usage], path_specs=_fast_path_specs())
        assert metrics["verifiedSavedCalls"] == 0
        assert metrics["parityFailures"] >= 1

    duplicate = E.typed_fast_path_metrics([{
        "calls": 0, "callsDetail": [],
        "fastPathEvents": [
            _fast_path_event("evaluated"), _fast_path_event("evaluated"),
            _fast_path_event("committed"),
        ],
    }], path_specs=_fast_path_specs())
    assert duplicate["opportunities"] == 0
    assert duplicate["hits"] == 0
    assert duplicate["committed"] == 0
    assert duplicate["verifiedSavedCalls"] == 0
    assert duplicate["parityFailures"] >= 1


def test_typed_fast_path_metrics_have_stable_zero_schema_without_events():
    metrics = E.typed_fast_path_metrics(
        [{"calls": 0}], path_specs=_fast_path_specs(),
    )

    assert metrics == {
        "contract": "typed-fast-path-metrics.v1",
        "opportunities": 0, "hits": 0, "misses": 0, "committed": 0,
        "eligibleNotCommitted": 0, "estimatedSavedCalls": 0,
        "verifiedSavedCalls": 0, "parityFailures": 0, "byPath": {},
    }


def test_conversation_summary_preserves_safe_usage_and_aggregates_by_path():
    from tools import agent_lang_ab as suite

    usage = {
        "calls": 0,
        "callsDetail": [],
        "fastPathEvents": [
            _fast_path_event("evaluated"),
            _fast_path_event("committed"),
        ],
    }
    rows = [{"시나리오": "S", "턴": [{
        "usage": usage, "초": 0.1, "총토큰": 0, "프롬프트토큰": 0,
        "완성토큰": 0, "캐시토큰": 0, "LLM호출": 0, "비용USD": 0,
        "검사": {},
    }]}]

    _totals, metrics = suite._summarize(rows)

    assert metrics["typedFastPath"]["verifiedSavedCalls"] == 1
    assert rows[0]["턴"][0]["usage"] == usage


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
        "전용 SQLite cache",
    ):
        assert token in guide
    for dimension in protocol["humanRubric"]["dimensions"]:
        assert dimension["label"] in guide
        for item in dimension["checklist"]:
            assert f"`{item['id']}`" in guide


def test_raw_result_path_is_always_under_the_ignored_evaluation_cache(tmp_path):
    metadata = {
        "run": {"runGroupId": "focused:2026-08-15", "repeatIndex": 2},
        "battery": {"batteryVersion": "2.0.0"},
    }
    path = E.raw_result_path("create", metadata)
    cache_root = (ROOT / ".cache" / "agent-evaluation").resolve()
    assert path.resolve().is_relative_to(cache_root)
    assert path.name == "create-b2.0.0-r02.json"
    with pytest.raises(ValueError, match=r"\.cache/agent-evaluation"):
        E.raw_result_path("create", metadata, requested=tmp_path / "raw.json")


def test_primary_batteries_always_write_raw_results_through_the_cache_helper():
    for relative in ("tools/agent_lang_ab.py", "tools/agent_compose_eval.py",
                     "tools/agent_create_suite.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "raw_result_path(" in text, relative
        assert "write_raw_result(" in text, relative


def test_evaluation_rules_require_a_tracked_compact_markdown_report():
    guide = (ROOT / "app/agent/EVALUATION.md").read_text(encoding="utf-8")
    agent_guide = (ROOT / "app/agent/AGENT.md").read_text(encoding="utf-8")
    archive = (ROOT / "research/agent-improvement/README.md").read_text(encoding="utf-8")
    for text in (guide, agent_guide, archive):
        assert ".cache/agent-evaluation/" in text
        assert "research/agent-improvement/evaluations/" in text
    for token in ("candidate commit", "protocolVersion", "rubricVersion",
                  "batteryVersion", "batteryManifestSha256", "specializedReviewSpecSha256"):
        assert token in guide
