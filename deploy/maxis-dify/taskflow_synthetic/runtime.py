"""Pure Code-node functions; all content is fabricated, fixed, and non-operational.

No caller-supplied context, URLs, documents, credentials, or real identifiers are
accepted. This is a capability fixture, not the TaskFlow production adapter.
"""
import json


FIXTURE_ID = "synthetic-research-v1"
CASE_ID = "checkpoint_research"
KNOWLEDGE_TEXT = (
    "SYN_LTM_TASK_RULE_V1 합성 검증 전용 규칙. "
    "Task 초안에는 목적, 포함 범위, 완료 조건, 근거를 구분한다. "
    "이 합성 사례의 완료 조건은 체크포인트 재시작 검증 결과를 기록하는 것이다. "
    "담당자와 일정은 제공되지 않았으므로 지정하지 않는다."
)
GLOSSARY = {
    "SYN-CHECKPOINT": {
        "source_ids": ["syn-glossary-checkpoint"],
        "text": "합성 배치가 재시작할 위치를 기록한 상태 표식. 실제 시스템의 정의가 아님.",
    },
    "SYN-LAKE": {
        "source_ids": ["syn-glossary-lake"],
        "text": "이 테스트에서만 사용하는 가상의 데이터 처리 서비스.",
    },
}
EVIDENCE = {
    "syn-task-001": "합성 요청: SYN-LAKE 배치의 SYN-CHECKPOINT 재시작 검증 Task를 조사한다. 담당자와 일정은 미제공.",
    "syn-doc-001": "합성 설계: 테스트 배치는 체크포인트 뒤의 데이터만 재처리한다. 재시작 전후의 중복 처리 여부를 검증한다.",
}


def _fixture(value):
    if type(value) is not str or value != FIXTURE_ID:
        raise ValueError("unknown synthetic fixture")


def _selector(value):
    if type(value) is not str or not value or len(value.encode("utf-8")) > 96:
        raise ValueError("invalid synthetic selector")


def _result(status, source_ids=None, text=""):
    return {"result": json.dumps({
        "contract": "synthetic-research-tool.v1", "fixture_id": FIXTURE_ID,
        "status": status, "source_ids": source_ids or [], "text": text,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))}


def glossary_lookup(fixture_id, term):
    _fixture(fixture_id)
    _selector(term)
    entry = GLOSSARY.get(term)
    return _result("found", **entry) if entry else _result("not_found")


def evidence_lookup(fixture_id, source_id):
    _fixture(fixture_id)
    _selector(source_id)
    if source_id not in EVIDENCE:
        raise ValueError("unknown synthetic source")
    return _result("found", [source_id], EVIDENCE[source_id])


def domain_query(fixture_id, query_key):
    _fixture(fixture_id)
    if type(query_key) is not str or query_key != "draft_rules":
        raise ValueError("unknown synthetic domain query")
    return {"query": "SYN_LTM_TASK_RULE_V1"}


def admit_domain(hits, dataset_id, document_id):
    if type(hits) is not list or len(hits) > 1:
        raise ValueError("invalid synthetic retrieval shape")
    if not hits:
        return _result("not_found")
    hit = hits[0]
    if type(hit) is not dict or type(hit.get("metadata")) is not dict:
        raise ValueError("invalid synthetic retrieval metadata")
    if (hit["metadata"].get("dataset_id") != dataset_id
            or hit["metadata"].get("document_id") != document_id
            or type(hit.get("content")) is not str
            or hit["content"].strip() != KNOWLEDGE_TEXT):
        raise ValueError("synthetic Knowledge identity or content mismatch")
    return _result("found", ["syn-domain-rules"], KNOWLEDGE_TEXT)


def research_query(fixture_id, case_id):
    _fixture(fixture_id)
    if type(case_id) is not str or case_id != CASE_ID:
        raise ValueError("unknown synthetic research case")
    return {"query": (
        "합성 조사 사례. glossary_lookup으로 SYN-CHECKPOINT의 뜻, "
        "evidence_lookup으로 syn-task-001의 요청, domain_retrieve의 draft_rules로 "
        "초안 작성 기준을 조회한다. 세 조회 결과를 근거와 함께 요약하고 "
        "확인되지 않은 담당자·일정을 구분한다. 실제 Task 생성이나 승인 요청은 하지 않는다."
    )}
