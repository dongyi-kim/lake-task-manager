# -*- coding: utf-8 -*-
"""주제 지식 추론 — 한 대상(테이블·기술)의 사실이 여러 티켓·코멘트·문서에 흩어져 있을 때.

여기서 지키는 것은 답변 문장이 아니라 **재료가 모델에게 실제로 도달하는가**다.
문장 품질은 배터리(tools/agent_scenarios.py DATA*)가 실 LLM 으로 본다.

두 층으로 나뉜다.
  · world 층 — 픽스처가 정말 그 사실을 갖고 있고, world 시퀀스를 흔들지 않았는가
  · 도구·취합 층 — 검색이 이름을 찾고, 코멘트 원문이 인용되고, 변경 이력이 보이는가
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

from app.mock.world import get_world                       # noqa: E402

TABLE = "fdc.fdc_trace_summary_ic"
UNKNOWN = "mes.mes_wip_move_hist"


def _w():
    return get_world()


# ── world 층 ───────────────────────────────────────────────────────
def test_key_sequence_is_untouched():
    """★ 이 파일에서 가장 중요한 단언 — 픽스처가 rng 를 건드렸으면 world 전체가 뒤바뀐다.

    자동 생성 키는 DL-5001~DL-6026 이고 그 뒤로 늘어나면 안 된다(CLAUDE.md §7.1).
    """
    assert _w()._counter == 6026


def test_dataset_fixtures_stay_out_of_the_ui_fixture_epic():
    """DL-9000 자식은 '[UI]' 접두어가 강제된다 — 데이터셋 픽스처는 자체 Epic 을 쓴다."""
    w = _w()
    assert w.issues["DL-9040"]["type"] == "Epic"
    kids = [i for i in w.issues.values() if i.get("epicKey") == "DL-9040"]
    assert len(kids) >= 12
    assert all(i["module"] != "TEST" for i in kids), "실 모듈이어야 담당·워크로드가 말이 된다"
    assert all(i["key"] not in ("DL-9000",) and i.get("epicKey") != "DL-9000" for i in kids)


def test_the_answer_is_split_across_tickets_on_purpose():
    """한 티켓만 읽어서는 답이 안 나오게 심었는지 — 이게 이 과제의 전제다."""
    w = _w()
    # 최초 요청(VoC)에는 '희망' 주기만, 현재 주기는 없다
    voc = w.issues["DL-9041"]["description"]
    assert "희망 적재주기 : 2시간 1회" in voc and "30분" not in voc
    # 현재 주기는 변경 티켓의 changelog 에만 확정적으로 있다
    chg = w.issues["DL-9044"]["changelog"][0]["items"][0]
    assert chg["field"] == "적재주기" and chg["toString"] == "30분 1회"
    # 스키마 변경은 또 다른 티켓
    assert any(i["field"] == "스키마" for i in w.issues["DL-9045"]["changelog"][0]["items"])


def test_a_fact_lives_only_in_another_subjects_ticket_comment():
    """다른 테이블 티켓(DL-9062)의 코멘트에만 있는 조각 — 제목·본문 검색으로는 안 잡힌다."""
    it = _w().issues["DL-9062"]
    assert "yms_lot_yield_daily" in it["summary"]
    body = " ".join(c["body"] for c in it["comments"])
    assert TABLE in body and "30분" in body and "skcc.x1042" in body
    assert TABLE not in it["description"], "본문에 있으면 '코멘트에만'이라는 전제가 깨진다"


def test_the_unknown_table_is_mentioned_exactly_once():
    """정직한 '기록 없음'을 시험하려면 딱 한 번만 스쳐야 한다."""
    hits = [k for k, i in _w().issues.items()
            if UNKNOWN in (i["summary"] + i["description"]
                           + " ".join(c["body"] for c in i["comments"]))]
    assert hits == ["DL-9051"]


def test_every_fixture_comment_serializes():
    """코멘트 shape 누락(kind/text)은 그 티켓의 코멘트 API 를 통째로 죽인다(DL-9036 의 전례)."""
    w = _w()
    for k, it in w.issues.items():
        if "dataset-fixture" in (it.get("labels") or []):
            assert w.jira_comments(k) is not None


def test_analysis_document_is_reachable_from_its_ticket():
    """문서 URL 은 파생값(md5) — 티켓 remotelink 와 어긋나면 링크가 死한다. 중복도 없어야."""
    from app.agent.tools._ctx import client
    w = _w()
    title = "[데이터카탈로그] fdc_trace_summary_ic 테이블 특성 분석"
    docs = client().ticket_documents("DL-9046") or []
    assert [d.get("title") for d in docs] == [title], "본문 언급과 remotelink 가 1건으로 접혀야 한다"
    assert docs[0]["url"] == w._conf_url(title)


# ── 도구·취합 층 ───────────────────────────────────────────────────
pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

from app.agent.tools import BY_NAME                        # noqa: E402
from app.agent.tools._ctx import client                    # noqa: E402
from app.agent.tools._ident import find_identifiers, subject_term, variants  # noqa: E402


def test_identifiers_are_recognised_without_swallowing_filenames():
    assert find_identifiers("fdc.fdc_trace_summary_ic 적재주기는?") == [TABLE]
    assert find_identifiers("etl_yms_lot_yield_daily 담당?") == ["etl_yms_lot_yield_daily"]
    assert find_identifiers("graph.py 를 고쳐줘") == [], "파일명을 테이블로 오인하면 조사가 샌다"
    assert find_identifiers("ETL 모듈 진척률") == []
    assert variants(TABLE)[0] == "fdc_trace_summary_ic", "접미형이 먼저다(부분문자열 상위집합)"


def test_subject_term_handles_tables_technologies_and_nothing():
    assert subject_term("적재주기는?", [TABLE, "적재주기"]) == TABLE
    assert subject_term("호환성 정책?", ["Schema Registry", "호환성"]) == "Schema Registry"
    assert subject_term("업무 정리 좀", ["업무", "정리"]) == "", "일반어로 조사를 시작하면 노이즈뿐"


def test_search_finds_a_dotted_table_name():
    r = BY_NAME["search_work_history"].invoke({"query": f"{TABLE} 적재주기", "limit": 8})
    assert any(x["key"] == "DL-9044" for x in r["jira"]), r["jira"]


def test_search_ladder_still_rejects_unrelated_tickets():
    """core 토큰에 `_` 를 넣은 뒤에도 무관성 차단이 살아 있어야 한다(연관성 규율 회귀)."""
    r = BY_NAME["search_work_history"].invoke({"query": "Iceberg Puffin NDV 통계", "limit": 8})
    assert not any(x["key"].startswith("DL-904") for x in r["jira"]), r["jira"]


def test_find_mentions_quotes_the_comment_with_its_author():
    r = BY_NAME["find_mentions"].invoke({"term": TABLE})
    cm = [h for h in r["hits"] if h.get("where") == "comment" and h["key"] == "DL-9062"]
    assert cm, r["hits"]
    assert cm[0]["author"] == "skcc.x1103" and "30분" in cm[0]["snippet"]
    assert len(json.dumps(r, ensure_ascii=False)) < 8000, "프롬프트에 실을 수 있는 크기여야 한다"


def test_find_mentions_is_honest_about_an_unknown_table():
    r = BY_NAME["find_mentions"].invoke({"term": UNKNOWN})
    assert [h["key"] for h in r["hits"]] == ["DL-9051"]


def test_comment_limit_is_not_poisoned_by_an_earlier_small_read():
    """캐시 키에 limit 이 없다 — 5건 요청이 먼저 오면 20건 요청까지 5건에 묶였다."""
    c = client()
    c.cache.invalidate(f"comments:{c.env}:DL-9043")
    assert len(c.issue_comments("DL-9043", 5)) == 5
    assert len(c.issue_comments("DL-9043", 20)) == 6


def test_read_document_returns_body_beyond_the_search_excerpt():
    r = BY_NAME["find_mentions"].invoke({"term": TABLE})
    doc = BY_NAME["read_document"].invoke({"url_or_id": r["documents"][0]["url"]})
    assert "CHAMBER_ID" in doc["text"], "발췌 200자 밖의 본문을 읽어야 스키마를 답할 수 있다"


def test_field_history_exposes_non_workflow_fields():
    """화면 타임라인은 status·assignee 만 남긴다 — 적재주기 변경은 이 함수로만 보인다."""
    rows = client().ticket_field_history("DL-9044")
    assert any(r["field"] == "적재주기" and "30분" in (r["to"] or "") for r in rows), rows


# ── 사전 취합(dossier) ─────────────────────────────────────────────
from app.agent.workflow.agents.historian import _topic_dossier   # noqa: E402


def test_dossier_gathers_every_fragment_for_a_table():
    d = _topic_dossier(TABLE)
    for must in ("DL-9044", "30분", "etl_fdc_trace_summary_ic_30m", "skcc.x1042",
                 "CHAMBER_ID", "적재주기 2시간 1회 → 30분 1회"):
        assert must in d, f"{must} 가 취합에서 빠졌다"
    assert len(d) <= 4000


def test_dossier_works_for_a_technology_not_just_a_table():
    """테이블만의 이야기가 아니다 — 특정 기술도 조각이 똑같이 흩어져 있다."""
    d = _topic_dossier("Schema Registry")
    assert "호환성 정책 BACKWARD → FULL" in d and "DL-9071" in d
    assert "skcc.x1501" in d


def test_dossier_does_not_contaminate_an_unknown_subject():
    """★ 이 유형의 전형적 실패 — 재료가 없으면 가장 가까운 대상의 사실을 끌어다 붙인다."""
    d = _topic_dossier(UNKNOWN)
    assert "DL-9051" in d
    for leaked in ("DL-9044", "30분", "etl_fdc_trace_summary_ic_30m", "CHAMBER_ID"):
        assert leaked not in d, f"다른 테이블의 사실({leaked})이 새어 들어왔다"


def test_dossier_reports_nothing_found_instead_of_guessing():
    d = _topic_dossier("zzz.no_such_table_anywhere")
    assert "찾지 못했다" in d


# ── 라우팅·전달 ────────────────────────────────────────────────────
from app.agent.workflow import graph as G                  # noqa: E402
from app.agent.workflow.state import Intent                # noqa: E402


def _msg(text):
    from langchain_core.messages import HumanMessage
    return {"messages": [HumanMessage(content=text)]}


def test_asset_question_reaches_the_investigator_even_if_misclassified():
    """progress 로 오분류되면 pmo 노드로 가는데, 거기엔 검색 도구가 아예 없다 — 코드가 막는다."""
    st = {**_msg(f"{TABLE} 현재 적재주기는?"), "intent": Intent.PROGRESS}
    assert G.route_after_planner(st) == "investigate"
    # 티켓 키를 짚은 진짜 현황 질문은 그대로 pmo 로 간다
    st2 = {**_msg("DL-101 어디까지 왔어?"), "intent": Intent.PROGRESS, "mentioned_keys": ["DL-101"]}
    assert G.route_after_planner(st2) == "pmo"


def test_asset_question_goes_through_the_curator():
    """'적재주기는?' 은 기존 지식 키워드에 하나도 안 걸린다 — 식별자로 판정한다."""
    assert G.route_after_historian({**_msg(f"{TABLE} 적재주기는?"),
                                    "intent": Intent.ASK}) == "curate"
    assert G.route_after_historian({**_msg("DL-207 을 x1103 에게 맡겨도 될까?"),
                                    "intent": Intent.ASK}) == "respond"


def test_gathered_material_actually_reaches_the_next_roles():
    """State 에 선언이 없으면 LangGraph 가 반환값에서 버린다 — Curator 자료가 늘 비어 있었다."""
    from app.agent.workflow.agents.curator import Curator
    from app.agent.workflow.agents.historian import Historian
    from app.agent.workflow.state import AgentState

    for key in ("pre_survey", "web_context", "seed_map", "topic_dossier"):
        assert key in AgentState.__annotations__, f"{key} 가 State 에 없다"
    out = Historian().apply({"topic_dossier": "X-MARK", "pre_survey": "P-MARK"},
                            {"situation": "s", "evidence": []})
    assert out["topic_dossier"] == "X-MARK" and out["pre_survey"] == "P-MARK"
    assert "X-MARK" in Curator().task({"topic_dossier": "X-MARK"})


def test_historian_injects_the_dossier_into_its_own_prompt():
    assert "X-MARK" in __import__(
        "app.agent.workflow.agents.historian", fromlist=["Historian"]
    ).Historian().task({"topic_dossier": "X-MARK", **_msg("q")})


# ── 답변 깊이 ──────────────────────────────────────────────────────
def test_answer_depth_shapes_the_reply_instruction():
    """값을 물으면 결론형, 경위·개념을 물으면 설명형 — 어느 쪽이든 더 깊은 설명은 다음 턴에."""
    from app.agent.workflow.agents.responder import Responder
    r = Responder()
    brief = r.task({**_msg("적재주기는?"), "intent": Intent.ASK, "answer_depth": "brief"})
    assert "결론형" in brief and "개념 설명·배경·일반론을 덧붙이지 마라" in brief
    deep = r.task({**_msg("왜 바뀌었어?"), "intent": Intent.ASK, "answer_depth": "explain"})
    assert "설명형" in deep
    assert "말씀 주세요" in brief and "말씀 주세요" in deep, "다음 턴 제안이 양쪽 다 있어야 한다"


def test_depth_instruction_is_skipped_while_asking_questions():
    """되묻는 턴은 질문 폼이 주인공이라 깊이 지시가 끼어들면 안 된다."""
    from app.agent.workflow.agents.responder import Responder
    t = Responder().task({**_msg("초안 잡아줘"), "questions": ["범위가 어디까지인가요?"],
                          "answer_depth": "brief"})
    assert "답변 깊이" not in t


def test_planner_defaults_to_brief_when_unsure():
    from app.agent.workflow.agents.planner import Planner
    out = Planner().apply({}, {"intent": Intent.ASK, "keywords": ["x"]})
    assert out["answer_depth"] == "brief", "애매하면 짧게 — 더 필요하면 사용자가 다시 묻는다"


def test_dossier_decides_ownership_in_code_not_by_guessing():
    """담당은 기록에 '담당'이라고 적힌 사람뿐 — 코멘트 작성자를 담당으로 답한 실측 오답 2회."""
    assert "[담당] skcc.x1042" in _topic_dossier(TABLE)
    d = _topic_dossier(UNKNOWN)
    assert "[담당] 확인된 기록 없음" in d
    assert "skcc.x1560" not in d.split("[담당]")[1], "코멘트 작성자가 담당 자리에 오면 안 된다"


# ── 다른 실패 유형 2종: 담당 이관 / 티켓 0건 ──────────────────────
HANDOVER = "wip.wip_lot_track_hist"
DOC_ONLY = "qms.qms_defect_code_mst"


def test_handover_fixture_hides_the_current_owner_from_the_build_ticket():
    """최초 구축 티켓만 읽으면 틀리도록 심었는지 — 이 전제가 깨지면 시험이 무의미하다."""
    w = _w()
    assert "skcc.x1103" in w.issues["DL-9080"]["description"]
    rows = client().ticket_field_history("DL-9081")
    assert any(r["field"] == "운영 담당" and r["to"] == "skcc.i2011" for r in rows), rows


def test_dossier_picks_the_latest_owner_after_a_handover():
    """이관 기록이 있으면 그게 이긴다 — 옛 담당을 현재로 답하는 것이 이 유형의 전형적 실패."""
    d = _topic_dossier(HANDOVER)
    assert "[담당] 현재 skcc.i2011" in d and "skcc.x1103 는 **이전** 담당" in d


def test_a_table_with_no_tickets_is_still_answerable_from_documents():
    """티켓 검색 0건에서 멈추면 오답 — 문서에만 사는 대상도 있다."""
    hits = [k for k, i in _w().issues.items() if DOC_ONLY in (i["summary"] + i["description"])]
    assert hits == [], "티켓이 하나도 없어야 이 시험이 성립한다"
    d = _topic_dossier(DOC_ONLY)
    for must in ("주 1회", "etl_qms_defect_code_mst_w", "DEFECT_CD", "[담당] skcc.i2044"):
        assert must in d, f"{must} 가 문서 취합에서 빠졌다"
