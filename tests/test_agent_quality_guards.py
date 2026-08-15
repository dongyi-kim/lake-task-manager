# -*- coding: utf-8 -*-
"""Full-battery human review에서 발견한 cross-role 품질 회귀의 deterministic guards."""

from app.agent.workflow.agents.portfolio_analyst import _my_day_rank
from app.agent.workflow.agents.result_integrator import (_align_due_claims,
                                                          _ensure_research_status)


def test_my_day_ranks_priority_within_deadline_bucket_and_names_one_primary():
    today = "2026-08-15"
    rows = [
        {"key": "DL-9008", "duedate": "2026-08-06", "priority": "Unclassified"},
        {"key": "DL-9028", "duedate": "2026-08-14", "priority": "P1-Critical"},
        {"key": "DL-9029", "duedate": "2026-08-15", "priority": "P3-Major"},
    ]
    ranked = sorted(rows, key=lambda row: _my_day_rank(row, today))
    assert [row["key"] for row in ranked] == ["DL-9028", "DL-9008", "DL-9029"]


def test_final_reply_due_date_is_aligned_to_the_single_actual_payload():
    state = {"draft": {"items": [{
        "summary": "[DataOps] 적재 지연 알림 임계값 조정",
        "duedate": "2026-08-21",
    }]}}
    text = ("### 변경 내용\n\n- 마감: 2026-08-20 (이번 주 금요일)\n"
            "- 조사 기준일: 2026-08-15")
    got = _align_due_claims(text, state["draft"]["items"])
    assert "마감: 2026-08-21" in got
    assert "조사 기준일: 2026-08-15" in got, "마감과 무관한 근거 날짜는 바꾸면 안 된다"


def test_material_internal_research_facts_survive_the_final_summary():
    dossier = ("문서 발췌: h2. 내부 확인 * 후보 대상: Lake 일배치 Iceberg 테이블 20개 "
               "* 내부 Spark writer 버전 확인 완료 * 실제 Puffin NDV 생성 PoC는 아직 수행하지 않음 "
               "h2. 외부 확인 필요 * 현재 Iceberg spec의 Puffin statistics/NDV 구조 "
               "* StarRocks reader·optimizer의 실제 소비 지원 여부")
    state = {"request_text": "내부 작업 이력과 외부 공식 자료를 함께 조사해줘",
             "topic_dossier": dossier}
    got = _ensure_research_status("### 결론\n\n외부 검색 확인 필요\n\n### 근거\n- 문서", state)
    assert "현재 상태" in got
    for value in ("20개", "writer 버전 확인 완료", "PoC는 아직 수행하지 않음",
                  "StarRocks reader·optimizer"):
        assert value in got
    assert got.index("### 현재 상태") < got.index("### 근거")
