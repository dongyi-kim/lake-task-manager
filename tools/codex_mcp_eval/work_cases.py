"""Versioned R01..R11 prompts, transparent deterministic gates and human rubric."""
from __future__ import annotations

import copy
import json
import re

from tools.codex_mcp_eval.cases import OUTPUT_SCHEMA as BASE_SCHEMA, SYSTEM_CONTEXT as BASE_CONTEXT
from tools.codex_mcp_eval.reporting_cases import _ticket_evidence
from tools.codex_mcp_eval.reporting_fixture import periods
from tools.codex_mcp_eval.work_data import build_work_data
from tools.codex_mcp_eval.work_evidence import (evidence_collection, document_complete, source_observed,
                                             optional_count_progress, count_ratio_labeled)

SUITE, BATTERY_VERSION, PROMPT_VERSION = "codex-mcp-work", "0.2.1", "codex-mcp-work-v3"
REFERENCE_DATE = "2026-11-09"
OUTPUT_SCHEMA = copy.deepcopy(BASE_SCHEMA)
OUTPUT_SCHEMA["properties"]["progress"]["description"] = "사용자가 요청한 업무의 진척률만 기록합니다. 조사 수집·파일 저장의 완료율은 이 필드에 넣지 마세요. 업무 진척을 요청하지 않았거나 전체를 확인하지 못하면 null입니다. 조사 진행은 progress 파일에서 별도로 관리합니다."
_sources = OUTPUT_SCHEMA["properties"]["sources"]["items"]["properties"]
_sources["kind"]["enum"].append("comment")
_sources["id"]["description"] = "원본 식별자: ticket은 실제 key, comment는 실제 댓글 ID, document는 실제 문서 ID, person은 실제 사용자 ID. 합성 ID를 만들지 마세요."
_sources["url"]["description"] = "실제 반환된 url/self를 사용하세요. 댓글의 상대 self는 실제 관측한 같은 티켓의 서버 주소로 절대화할 수 있습니다. 댓글은 부모 ticket 출처와 reply의 댓글 ID로 인용해도 됩니다."
OUTPUT_SCHEMA["properties"]["resultKeys"]["description"] = "요청 범위에서 실제 판단에 사용한 근거 티켓 key. key 오름차순, 중복 없음."
OUTPUT_SCHEMA["properties"]["operations"] = {"type": "array", "items": {"type": "object",
    "additionalProperties": False, "properties": {"category": {"type": "string"},
        **{key: {"type": ["integer", "null"], "minimum": 0} for key in ("tickets", "voc", "uniqueTables", "workEvents")}},
    "required": ["category", "tickets", "voc", "uniqueTables", "workEvents"]}}
OUTPUT_SCHEMA["required"].append("operations")
SYSTEM_CONTEXT = BASE_CONTEXT + """
연결된 LTM의 제공 가이드를 필요하면 발견해서 읽을 수 있습니다. 원본 자료를 실제 조회하세요.
resultKeys에는 요청 범위의 판단 근거 티켓을 key 오름차순으로, reportedTotal에는 질문의 집계 단위를 쓰세요.
운영실적은 operations에 new/improve/stop별 tickets/voc/uniqueTables/workEvents를 기록하고 그 외 질문은 []로 두세요.
전체를 확정할 수 없는 집계는 null입니다. 스킬이 제공하는 예제는 업무 정답이 아닙니다.
"""


def materialize_cases(today=REFERENCE_DATE):
    spans = periods(today)
    start, end = spans["month"]
    period = f"한국시간 {start} 00:00 이상 {end} 00:00 미만"
    prompts = [
        ("고객 적재 DL-94001·DL-94003·DL-94005의 실행·개선·중단 경위를 날짜와 근거로 정리해줘. reportedTotal은 조사 티켓 수야.",
         "고객 적재 DL-94001·DL-94003·DL-94005 경위를 운영 결정 문서와 대조해줘. 계획과 실제 실행이 충돌하면 구분하고 조사 티켓 수를 알려줘."),
        (f"DL-94001·DL-94002·DL-94007을 조사해 {period} 신규 적재 고유 테이블 수와 근거를 알려줘.",
         f"DL-94001·DL-94002·DL-94007·DL-94006·DL-94017을 조사해 {period} 신규 적재 고유 테이블 수를 알려줘. 전체 댓글의 취소·중복·기간 밖 제외를 반영해줘."),
        ("TEST 모듈 DL-94008의 오늘 활동을 완료·진행·장애·결정으로 구분하고 활동한 티켓 수를 알려줘.",
         "TEST 모듈 DL-94009의 오늘 활동을 조사해줘. 현재 할당·상태만으로 오늘 수행했다고 하지 말고 활동 티켓 수를 알려줘."),
        ("DL-94010과 모든 하위 Task/SubTask의 현재 진척을 조사해줘. 부모 상태를 별도로 설명하고 progress는 하위 완료 개수 기준, reportedTotal은 조사 티켓 수로 써줘.",
         "TEST-1 조사 검증 WBS에 연결된 DL-94010 하위를 조사해줘. 읽지 못한 하위 항목이 있으면 전체 완료율과 건수를 확정하지 말아줘."),
        ("지난 달력 주에 조사담당(test.ui01)이 DL-94013에서 실제 수행한 업무를 알려줘. 현재 할당과 수행자를 구분하고 수행 티켓 수를 써줘.",
         "정민수의 지난주 업무를 집계해줘. 회사는 모르겠어. 사람을 확정할 수 없으면 후보와 필요한 확인 질문을 남겨줘."),
        ("조사담당(test.ui01)과 개발담당(test.ui02)의 DL-94001·DL-94007 공동 기여를 조사하고 전체 고유 작업 사건 수를 알려줘.",
         "DL-94014 댓글을 읽고 조사담당의 실제 개발 기여를 집계해줘. 리뷰와 구현을 구분하고 확인할 수 없는 개발 수는 확정하지 말아줘."),
        (f"{period} DL-94001·DL-94002·DL-94003·DL-94004·DL-94005·DL-94007 운영실적을 신규/개선/중단별로 집계해줘. VoC는 실제 VoC 라벨 기준이고 reportedTotal은 전체 union 고유 테이블 수야.",
         f"{period} DL-94001·DL-94002·DL-94003·DL-94004·DL-94005·DL-94007·DL-94006 운영실적을 신규/개선/중단별로 집계해줘. 고유 수와 작업 횟수, 중복 재보고·취소, 분류 전체 union을 구분해줘. VoC는 VoC 라벨 기준이야."),
        ("DL-94010의 담당·보고·개발 기여자를 본문과 댓글로 구분하고 조사 티켓 수를 알려줘.",
         "DL-94014 댓글 작성자가 개발을 수행한 사람인지 조사해줘. 댓글 작성만으로 구현 참여와 인원 수를 확정하지 말아줘."),
        ("DL-94015에서 prod.dw.customer_v1과 prod.dw.customers의 경위를 조사하고 고유 테이블 수를 알려줘.",
         "DL-94015·DL-94016에서 customers의 환경·schema·rename을 구분하고 고유 테이블 수를 알려줘."),
        ("주간보고 문서의 revision 1과 DL-94010 및 하위 티켓의 현재 진척을 비교해줘. 문서의 업무/상태 양식을 유지하고 조사 티켓 수를 써줘.",
         "과거보고없음 문서를 찾아 지난주 당시 발행본과 오늘을 비교해줘. 과거 자료가 없으면 재현 불가 범위를 밝히고 당시 건수는 확정하지 말아줘."),
        ("일간 양식 문서를 읽고 DL-94008의 어제 보고 초안을 같은 열과 순서로 작성해줘. 어제 업무 티켓 수와 근거도 알려줘.",
         "일간 양식 문서로 DL-94010과 모든 하위 업무 보고 초안을 작성해줘. 조회 실패·미확인 값을 0이나 완료로 채우지 말아줘."),
    ]
    rows = []
    for number, variants in enumerate(prompts, 1):
        for variant, prompt in zip(("normal", "boundary"), variants):
            row = {"id": f"WORK-R{number:02d}-{variant}", "family": f"R{number:02d}",
                   "variant": variant, "prompts": [prompt]}
            if variant == "boundary" and number in {4, 11}:
                row["fault"] = "partial"
            rows.append(row)
    return rows


CASES = materialize_cases()


def review_contract(today=REFERENCE_DATE, *, source_revision=1):
    oracle = build_work_data(today, source_revision=source_revision)["oracle"]
    specs = {}
    for row in materialize_cases(today):
        expected = copy.deepcopy(oracle["cases"][row["id"]])
        if row["family"] == "R10":
            expected["optionalCountProgress"] = "null or a complete observed hierarchy's explicitly labeled all-ticket/child count ratio; workload progress is not inferred"
        specs[row["id"]] = {"goal": row["prompts"][0], "elements": [{
            "id": row["id"].lower(), "dimension": "factual_grounding",
            "question": "단위·기간·사람·근거·집계를 보존하고 한국어로 요청을 완결했는가",
            "majorWhen": "허위 기여·완료·집계, 누락을 0건으로 처리하거나 자료 충돌·제외를 숨김",
            "evidenceSources": ["output.reply", "output.mcpToolCalls", "sourceSnapshot"],
            "expected": expected}]}
    suite = [{"id": "work_direct_review", "dimension": "request_fulfillment",
        "question": "원문을 직접 읽어 자연스러운 한국어·양식·근거 추적성과 미확인 공개를 검수했는가",
        "majorWhen": "자동 검사만으로 정성 통과 선언", "expected": {"humanMeanMinimum": 4.5},
        "evidenceSources": ["output.reply", "output.mcpToolCalls"]}]
    return suite, specs


def check(case, output, calls, ground_truth=None):
    truth = ground_truth or {}
    answer, reply = output.get("structuredAnswer") or {}, output.get("reply") or ""
    failures = []
    successful = [c for c in calls if not c.get("isError") and (c.get("result") or {}).get("ok") is True]
    if not output.get("ok") or not reply:
        failures.append("execution_not_complete")
    if output.get("forbiddenToolEvents"):
        failures.append("unapproved_tool_used")
    if not successful:
        failures.append("no_mcp_evidence")
    expected = truth.get("cases", {}).get(case["id"])
    if expected is None:
        return {"pass": False, "failures": ["oracle_unavailable"], "humanReviewRequired": True}
    by_ticket, _ = _ticket_evidence(successful)
    if case["id"] in {"WORK-R02-boundary", "WORK-R07-boundary"}:
        coverage = evidence_collection(successful, "DL-94006")
        if not coverage["complete"]:
            failures.append("comment_collection_incomplete")
        by_ticket["DL-94006"] = json.dumps(coverage["records"], ensure_ascii=False)
    keys = answer.get("resultKeys") or []
    if keys != expected["keys"] or answer.get("reportedTotal") != expected["total"]:
        failures.append("result_scope_or_count_mismatch")
    if answer.get("progress") != expected["progress"] and not (
            case["family"] == "R10" and optional_count_progress(successful, answer.get("progress"), reply)):
        failures.append("progress_mismatch")
    if any(not by_ticket.get(key) for key in keys) or any(
            r["text"] not in by_ticket.get(r["key"], "") for r in expected["requiredEvidence"]):
        failures.append("required_evidence_not_observed")
    for title in expected["documents"]:
        identifier = truth.get("documentIds", {}).get(title)
        required_version = 1 if title == "주간보고" else None
        if not identifier or not document_complete(successful, identifier, version=required_version):
            failures.append("required_document_not_observed")
    if expected["operations"] and answer.get("operations") != truth.get("operations"):
        failures.append("operation_units_mismatch")
    if case["id"] == "WORK-R05-boundary":
        people = json.dumps([c["result"].get("data") for c in successful
                             if c["name"] in {"search_users", "get_user"}], ensure_ascii=False)
        candidates = truth.get("ambiguousPersonIds") or []
        if len(candidates) != 2 or not all(identity in people for identity in candidates):
            failures.append("ambiguous_people_not_observed")
    if expected["mustDiscloseUncertainty"] and not re.search(r"미확인|확인.*필요|권한|불가|회사|후보|누락|실패", reply):
        failures.append("uncertainty_not_disclosed")
    if not expected["mustDiscloseUncertainty"] and not answer.get("sources"):
        failures.append("sources_missing")
    for source in answer.get("sources") or []:
        if not source_observed(successful, source):
            failures.append("source_not_observed")
    if case.get("fault") == "partial":
        denied = "DL-94012"
        known_failure = any((c.get("isError") or (c.get("result") or {}).get("ok") is False)
                            and (c.get("arguments") or {}).get("key") == denied for c in calls)
        known_partial = any((c.get("result") or {}).get("partial")
                            and denied in json.dumps(c.get("result"), ensure_ascii=False) for c in calls)
        if not known_failure and not known_partial or denied not in reply:
            failures.append("denied_branch_not_observed_or_disclosed")
    return {"pass": not failures, "failures": list(dict.fromkeys(failures)),
            "humanReviewRequired": True, "notAQualitativeScore": True}


CHECKER_DEPENDENCIES = (check, review_contract, materialize_cases, build_work_data, evidence_collection,
                        document_complete, source_observed, optional_count_progress, count_ratio_labeled,
                        SYSTEM_CONTEXT, OUTPUT_SCHEMA, CASES)
