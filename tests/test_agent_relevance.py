# -*- coding: utf-8 -*-
"""검색 hit를 관련 근거/유사 경험으로 과장하지 않는 후단 계약."""

from app.agent.workflow.agents.people_advisor import PeopleAdvisor
from app.agent.workflow.agents.query_specialist import (QuerySpecialist,
                                                        _dedupe_equivalent_queries,
                                                        _external_research_allowed,
                                                        _jira_query_is_only_people,
                                                        _public_external_query,
                                                        _safe_model_external_query)
from app.agent.workflow.agents.research_analyst import (ResearchAnalyst,
                                                        _prefetched_external_context,
                                                        _relevant_only)
from app.agent.workflow.relevance import (discriminating_keywords, evidence_is_relevant,
                                          matches_focus, negative_relation)


def test_negative_relation_phrases_are_not_evidence():
    assert negative_relation("현재 요청과 직접적인 관련은 없음")
    assert negative_relation("단축키 팝업과는 관련이 없음")
    assert evidence_is_relevant({"key": "DL-1", "why": "같은 단축키 UX 결정의 선행 작업"})
    assert not evidence_is_relevant({"key": "DL-2", "why": "같은 모듈이지만 현재 요청과 무관"})
    assert negative_relation("DL-9090의 구체적인 정보는 검색 결과에서 찾을 수 없었습니다")
    assert not evidence_is_relevant({"key": "DL-3", "why": "유사하지만 다른 방향성을 가짐"})


def test_a_model_written_why_cannot_make_a_generic_title_relevant():
    """관련성은 해석문이 아니라 ticket 사실 필드와 원 요청을 교차 확인한다."""
    state = {"request_text": "메타데이터 미등록 테이블 30개 등록"}
    evidence = [{"key": "DL-5431", "title": "[Catalog] 코드 리뷰 반영",
                 "why": "메타데이터 등록과 관련 있을 가능성이 있다"}]
    assert _relevant_only(state, evidence) == []
    got = ResearchAnalyst().apply(state, {"situation": "DL-5431과 유사하다",
                                    "evidence": evidence, "already_exists": True})
    assert not got["evidence"] and not got["already_exists"]
    assert "직접 일치" in got["situation"] and "DL-5431" not in got["situation"]


def test_similarity_requires_a_discriminating_request_term():
    kws = ["Workbench", "쿼리", "편집기", "단축키"]
    assert discriminating_keywords(kws) == ["쿼리", "편집기", "단축키"]
    assert matches_focus("[Workbench] 쿼리 편집기 자동완성", kws)
    assert not matches_focus("[Workbench] 동시성 이슈 해결", kws)


def test_assigner_does_not_receive_or_repeat_irrelevant_history():
    state = {
        "draft": {"items": [{"summary": "[Workbench] 단축키 도움말 추가", "type": "Task",
                              "components": ["Workbench"], "description": ""}]},
        "evidence": [{"key": "DL-5122", "title": "[Workbench] 동시성 이슈 해결",
                      "why": "같은 모듈이지만 현재 요청과 직접적인 관련은 없음"}],
        "similar_history": "",
    }
    assert "DL-5122" not in PeopleAdvisor().task(state)
    got = PeopleAdvisor().apply(state, {"assignments": [{
        "index": 0, "user": "skcc.x1402",
        "reasons": ["유사 티켓 DL-5122 담당(1건)", "현재 진행중 1건"],
        "alternates": [{"user": "skcc.x1450", "why": "유사 티켓 4건 담당"}],
    }]})
    assert got["assignments"][0]["reasons"] == ["현재 진행중 1건"]
    assert got["assignments"][0]["alternates"] == []


def test_key_centric_research_keeps_named_and_structural_evidence_only():
    state = {"request_text": "DL-9093에 회귀 테스트를 붙여줘",
             "mentioned_keys": ["DL-9093"]}
    evidence = [
        {"key": "DL-9093", "title": "렌더 컴포넌트", "why": "요청 대상"},
        {"key": "DL-9092", "title": "API", "why": "DL-9093을 차단하는 선행 작업"},
        {"key": "DL-5326", "title": "쿼리 튜닝", "why": "같은 모듈의 진행 중 작업"},
    ]
    got = ResearchAnalyst().apply(state, {"situation": "조사", "evidence": evidence})
    assert [e["key"] for e in got["evidence"]] == ["DL-9093", "DL-9092"]


def test_assigner_cannot_credit_another_users_similar_ticket(monkeypatch):
    state = {"draft": {"items": [{"summary": "[Catalog] 리니지 오류", "type": "Bug"}]},
             "similar_history": '- skcc.x1402 — 유사 1건: DL-9090 "리니지 뷰어"(Open)'}
    got = PeopleAdvisor().apply(state, {"assignments": [{
        "index": 0, "user": "skcc.x1210",
        "reasons": ["유사 티켓 DL-9090 담당(1건)", "진행중 10건"],
        "alternates": []}]})
    assert got["assignments"][0]["reasons"] == ["진행중 10건"]


def test_external_query_requires_explicit_research_or_external_technology():
    assert not _external_research_allowed({"request_text": "리니지 뷰어 성능 측정 Task 초안"})
    assert not _external_research_allowed({"request_text": "붙여넣은 VoC를 Bug 티켓으로 작성"})
    assert _external_research_allowed({"request_text": "StarRocks Puffin NDV 적용 사례 조사"})
    assert _external_research_allowed({"request_text": "쿼리 엔진 인덱스 외부 자료도 찾아줘"})
    assert not _external_research_allowed({"request_text": "hotfix 라벨로 Task 만들어줘"})

    raw = {"queries": [
        {"id": "i", "source": "jira", "where": "text ~ lineage"},
        {"id": "w", "source": "web", "query": "lineage performance"},
    ]}
    internal = QuerySpecialist().apply(
        {"request_text": "리니지 뷰어 성능 측정 초안", "messages": []}, raw)
    assert [q["source"] for q in internal["query_plan"]["queries"]] == ["jira"]
    external = QuerySpecialist().apply(
        {"request_text": "StarRocks Puffin NDV 외부 사례 조사", "messages": []}, raw)
    assert [q["source"] for q in external["query_plan"]["queries"]] == ["jira", "web"]


def test_explicit_external_research_gets_a_sanitized_web_query_even_when_model_omits_it():
    state = {"request_text": (
        "우리 프로젝트의 Iceberg Puffin NDV 적용 가능성을 내부 작업 이력과 "
        "외부 공식 자료를 함께 조사해줘. DL-7001과 skcc.x1402도 참고"),
        "messages": []}
    got = QuerySpecialist().apply(state, {"queries": [
        {"id": "internal", "source": "jira", "query": "Iceberg Puffin NDV"},
    ]})["query_plan"]["queries"]
    web = [q for q in got if q["source"] == "web"]
    assert len(web) == 1
    assert all(term in web[0]["query"] for term in ("Iceberg", "Puffin", "NDV"))
    assert "official documentation" in web[0]["query"]
    assert "DL-7001" not in web[0]["query"] and "skcc.x1402" not in web[0]["query"]
    assert "프로젝트" not in web[0]["query"]
    assert _public_external_query("DL-1 skcc.x1 내부 자료만") == ""
    assert _public_external_query("Iceberg Puffin NDV PoC Iceberg") == \
        "Iceberg Puffin NDV official documentation"
    assert _public_external_query("Iceberg Puffin NDV Confluence marker") == \
        "Iceberg Puffin NDV official documentation"


def test_explicit_comment_evidence_gets_a_scoped_comment_query_when_model_omits_it():
    """`Jira 티켓·댓글` 요청을 issue-only 검색으로 축약하지 않는다."""
    state = {
        "request_text": (
            "Iceberg Puffin NDV 운영 적용 여부를 Jira 티켓·댓글, Confluence 문서와 "
            "외부 공식 문서로 조사해줘"
        ),
        "keywords": ["Iceberg Puffin NDV"],
        "messages": [],
    }
    got = QuerySpecialist().apply(state, {"queries": [
        {"id": "jira-topic", "source": "jira", "query": "Iceberg Puffin NDV",
         "where": "", "completeness": "all", "page_size": 50},
        {"id": "confluence-topic", "source": "confluence", "query": "Iceberg Puffin NDV"},
        {"id": "web-topic", "source": "web",
         "query": "Iceberg Puffin NDV official documentation"},
    ]})["query_plan"]["queries"]

    comments = [query for query in got if query["source"] == "comments"]
    assert len(comments) == 1
    assert comments[0]["query"] == ""
    assert 'text ~ "Iceberg"' in comments[0]["where"]
    assert comments[0]["completeness"] == "all"


def test_research_evidence_preserves_source_quality_metadata():
    state = {"request_text": "DL-73737 근거의 신뢰도와 적합성 평가",
             "mentioned_keys": ["DL-73737"]}
    evidence = [{
        "key": "DL-73737", "title": "자동 컴팩션 잡 개발", "why": "직접 근거",
        "confidence": "high", "fitness": "direct",
        "limitations": "운영 결과는 별도 확인 필요",
        "observations": [{"source": "comment", "text": "운영 체크리스트 첨부"}],
    }]
    got = ResearchAnalyst().apply(state, {"situation": "확인", "evidence": evidence})
    assert got["evidence"][0]["confidence"] == "high"
    assert got["evidence"][0]["fitness"] == "direct"
    assert got["evidence"][0]["limitations"] == "운영 결과는 별도 확인 필요"


def test_research_evidence_localized_quality_labels_are_normalized_without_schema_retry():
    got = ResearchAnalyst().apply({
        "request_text": "DL-73737 근거", "mentioned_keys": ["DL-73737"],
    }, {"situation": "확인", "evidence": [{
        "key": "DL-73737", "title": "자동 컴팩션 잡", "why": "직접 근거",
        "confidence": "높음", "fitness": "직접적", "limitations": "",
    }]})

    assert got["evidence"][0]["confidence"] == "high"
    assert got["evidence"][0]["fitness"] == "direct"


def test_korean_technology_name_uses_canonical_english_query_from_specialist():
    state = {"request_text": "아파치 아이스버그 퍼핀 통계 형식을 외부 공식 문서로 조사해줘",
             "messages": []}
    got = QuerySpecialist().apply(state, {"queries": [{
        "id": "translated-official", "source": "web",
        "query": "Apache Iceberg Puffin statistics official documentation",
    }]})["query_plan"]
    assert [q["query"] for q in got["queries"]] == [
        "Apache Iceberg Puffin statistics official documentation"]
    assert got["uncertainty"] == []


def test_original_and_canonical_technology_queries_are_deduplicated_and_bounded():
    state = {"request_text": "Apache Iceberg의 퍼핀 통계를 외부 조사해줘", "messages": []}
    got = QuerySpecialist().apply(state, {"queries": [{
        "id": "canonical", "source": "web",
        "query": "Apache Iceberg Puffin statistics official documentation",
    }, {
        "id": "duplicate", "source": "web",
        "query": "Apache Iceberg Puffin statistics official documentation",
    }]})["query_plan"]["queries"]
    assert [q["query"] for q in got] == [
        "Apache Iceberg official documentation",
        "Apache Iceberg Puffin statistics official documentation",
    ]
    assert len({q["id"] for q in got}) == 2


def test_external_query_dedup_ignores_retrieval_channel_noise_words():
    state = {"request_text": "Iceberg Puffin NDV 외부 공식 자료를 조사해줘", "messages": []}
    got = QuerySpecialist().apply(state, {"queries": [{
        "id": "noisy-alias", "source": "web",
        "query": "Iceberg Puffin NDV Confluence marker official documentation",
    }]})["query_plan"]["queries"]

    assert [query["query"] for query in got] == [
        "Iceberg Puffin NDV official documentation",
    ]


def test_internal_code_identifier_is_neither_translated_nor_sent_to_public_search():
    identifier = "fdc_summary_trace_ic"
    assert _public_external_query(f"{identifier} 데이터 히스토리 외부 검색") == ""
    assert _safe_model_external_query(f"{identifier} data history") == ""
    state = {"request_text": f"{identifier} 데이터 히스토리를 외부 검색해줘", "messages": []}
    got = QuerySpecialist().apply(state, {"queries": [{
        "id": "unsafe", "source": "web", "query": f"{identifier} data history",
    }]})["query_plan"]
    assert got["queries"] == []
    assert "privacy-safe canonical technology name" in got["uncertainty"][0]


def test_schema_qualified_internal_identifier_does_not_trigger_external_research():
    identifier = "fdc.fdc_trace_summary_ic"
    state = {"request_text": f"{identifier} 데이터 히스토리", "messages": []}
    assert not _external_research_allowed(state)
    assert _public_external_query(state["request_text"]) == ""

    got = QuerySpecialist().apply(state, {"queries": [
        {"id": "internal", "source": "jira", "query": identifier},
        {"id": "wrong-web", "source": "web", "query": "fdc official documentation"},
    ]})["query_plan"]["queries"]
    assert [query["source"] for query in got] == ["jira"]


def test_runtime_environment_and_job_identifier_do_not_trigger_public_research():
    request = "prod의 dag_etl_nightly 야간 배치가 커넥션 타임아웃으로 실패했다. 버그로 등록"
    state = {"request_text": request, "messages": []}
    assert not _external_research_allowed(state)
    assert _public_external_query(request) == ""
    assert _safe_model_external_query("prod official documentation") == ""

    got = QuerySpecialist().apply(state, {"queries": [
        {"id": "internal", "source": "jira", "query": "dag_etl_nightly"},
        {"id": "wrong-web", "source": "web", "query": "prod official documentation"},
    ]})["query_plan"]["queries"]
    assert [query["source"] for query in got] == ["jira"]


def test_roster_user_suffixes_are_neither_public_technology_nor_jira_issue_keys():
    asked = "x1402 x1450 x1042 담당으로 팝업 작업 만들어줘"
    assert _public_external_query(asked) == ""
    assert _safe_model_external_query("x1402 x1450 official documentation") == ""
    assert _public_external_query("회귀 테스트는 x1042. 알아서") == ""
    query = {"id": "wrong-people-as-tickets", "source": "jira", "where": (
        "issueKey='x1402' OR issueKey='x1450' OR issueKey='x1042'")}
    assert _jira_query_is_only_people(query)

    got = QuerySpecialist().apply(
        {"request_text": asked, "messages": []},
        {"queries": [query, {"id": "wrong-people-web", "source": "web",
                             "query": "x1402 official documentation"}]},
    )["query_plan"]
    assert got["queries"] == []
    assert any("담당자 ID" in value for value in got["uncertainty"])


def test_identical_queries_are_merged_and_dependencies_are_repaired():
    plan = {"queries": [
        {"id": "q1", "source": "jira", "query": "issue = DL-9090",
         "fields": ["id"], "page_size": 1, "depends_on": []},
        {"id": "q2", "source": "jira", "query": " issue   =   DL-9090 ",
         "fields": ["summary"], "page_size": 50, "depends_on": ["q1"]},
        {"id": "q3", "source": "comments", "where": "key = DL-9090",
         "fields": [], "page_size": 25, "depends_on": ["q2"]},
    ]}

    _dedupe_equivalent_queries(plan)

    assert [query["id"] for query in plan["queries"]] == ["q1", "q3"]
    assert plan["queries"][0]["fields"] == ["id", "summary"]
    assert plan["queries"][0]["page_size"] == 50
    assert plan["queries"][1]["depends_on"] == ["q1"]


def test_research_evidence_preserves_source_specific_observations():
    state = {"request_text": "DL-73737 운영 방식", "mentioned_keys": ["DL-73737"]}
    evidence = [{
        "key": "DL-73737", "title": "자동 컴팩션 잡 개발", "why": "직접 근거",
        "observations": [
            {"source": "description", "text": "30분 주기"},
            {"source": "comment", "text": "운영 체크리스트 첨부"},
        ],
    }]
    got = ResearchAnalyst().apply(state, {"situation": "확인", "evidence": evidence})
    assert got["evidence"][0]["observations"] == evidence[0]["observations"]


def test_prefetched_web_result_preserves_official_url_and_failed_attempt():
    ctx = _prefetched_external_context([{
        "id": "external-official", "source": "web", "result": {
            "query": "Iceberg Puffin NDV official documentation",
            "results": [{"title": "Puffin spec", "url": "https://iceberg.apache.org/puffin-spec/",
                         "snippet": "Statistics files", "official": True}],
        }}, {
        "id": "external-2", "source": "web", "result": {
            "query": "StarRocks Puffin", "results": [], "error": "network blocked",
        }}])
    assert "https://iceberg.apache.org/puffin-spec/" in ctx and "공식" in ctx
    assert "network blocked" in ctx and "검색 실패" in ctx
