# -*- coding: utf-8 -*-
"""검색 hit를 관련 근거/유사 경험으로 과장하지 않는 후단 계약."""

from app.agent.workflow.agents.assigner import Assigner
from app.agent.workflow.agents.query_specialist import (QuerySpecialist,
                                                        _external_research_allowed)
from app.agent.workflow.agents.historian import Historian, _relevant_only
from app.agent.workflow.relevance import (discriminating_keywords, evidence_is_relevant,
                                          matches_focus, negative_relation)


def test_negative_relation_phrases_are_not_evidence():
    assert negative_relation("현재 요청과 직접적인 관련은 없음")
    assert negative_relation("단축키 팝업과는 관련이 없음")
    assert evidence_is_relevant({"key": "DL-1", "why": "같은 단축키 UX 결정의 선행 작업"})
    assert not evidence_is_relevant({"key": "DL-2", "why": "같은 모듈이지만 현재 요청과 무관"})
    assert not evidence_is_relevant({"key": "DL-3", "why": "유사하지만 다른 방향성을 가짐"})


def test_a_model_written_why_cannot_make_a_generic_title_relevant():
    """관련성은 해석문이 아니라 ticket 사실 필드와 원 요청을 교차 확인한다."""
    state = {"request_text": "메타데이터 미등록 테이블 30개 등록"}
    evidence = [{"key": "DL-5431", "title": "[Catalog] 코드 리뷰 반영",
                 "why": "메타데이터 등록과 관련 있을 가능성이 있다"}]
    assert _relevant_only(state, evidence) == []
    got = Historian().apply(state, {"situation": "DL-5431과 유사하다",
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
    assert "DL-5122" not in Assigner().task(state)
    got = Assigner().apply(state, {"assignments": [{
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
    got = Historian().apply(state, {"situation": "조사", "evidence": evidence})
    assert [e["key"] for e in got["evidence"]] == ["DL-9093", "DL-9092"]


def test_assigner_cannot_credit_another_users_similar_ticket(monkeypatch):
    state = {"draft": {"items": [{"summary": "[Catalog] 리니지 오류", "type": "Bug"}]},
             "similar_history": '- skcc.x1402 — 유사 1건: DL-9090 "리니지 뷰어"(Open)'}
    got = Assigner().apply(state, {"assignments": [{
        "index": 0, "user": "skcc.x1210",
        "reasons": ["유사 티켓 DL-9090 담당(1건)", "진행중 10건"],
        "alternates": []}]})
    assert got["assignments"][0]["reasons"] == ["진행중 10건"]


def test_external_query_requires_explicit_research_or_external_technology():
    assert not _external_research_allowed({"request_text": "리니지 뷰어 성능 측정 Task 초안"})
    assert not _external_research_allowed({"request_text": "붙여넣은 VoC를 Bug 티켓으로 작성"})
    assert _external_research_allowed({"request_text": "StarRocks Puffin NDV 적용 사례 조사"})
    assert _external_research_allowed({"request_text": "쿼리 엔진 인덱스 외부 자료도 찾아줘"})

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
