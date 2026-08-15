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

    자동 생성 키는 DL-5001~DL-6026 이고 그 뒤로 늘어나면 안 된다(AGENTS.md §7.1).
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


def test_internal_external_research_fixture_keeps_facts_and_gaps_separate():
    w = _w()
    issue = w.issues["DL-7001"]
    comments = " ".join(comment["body"] for comment in issue["comments"])
    assert "일배치 테이블 20개" in issue["description"]
    assert "실제 구현 PoC는 아직" in issue["description"]
    assert "StarRocks" in comments and "확인되지 않았습니다" in comments
    pages = [page for owner_pages in w.confluence.values() for page in owner_pages
             if page.get("title") == "[Lake] Iceberg Puffin NDV 적용 검토 노트"]
    assert len(pages) == 1
    assert "외부 확인 필요" in pages[0]["body"]


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
    assert any(x["key"] == "DL-7001" for x in r["jira"]), r["jira"]
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
from app.agent.workflow.agents.research_analyst import _topic_dossier   # noqa: E402


def test_dossier_gathers_every_fragment_for_a_table():
    d = _topic_dossier(TABLE)
    for must in ("DL-9044", "30분", "etl_fdc_trace_summary_ic_30m", "skcc.x1042",
                 "CHAMBER_ID", "2시간 1회 → 30분 1회"):
        assert must in d, f"{must} 가 취합에서 빠졌다"
    # 변경 이력은 **필드별로 묶여** 있어야 한다 — 한 줄씩 섞어 두면 모델이 다른 필드의 값을
    # 물어본 필드의 값으로 옮겨 적는다(실측 DATA4: '보존기간 30→90일'을 '적재주기 90일'로).
    assert "[적재주기]" in d, d[:400]
    assert len(d) <= 4000


def test_change_history_is_grouped_by_field_so_values_cannot_cross_wires():
    """대상은 맞고 **필드만 틀린** 오답은 눈에 잘 안 띄는데 사용자는 그 숫자를 그대로
    보고서에 옮긴다. 묶어 두면 '이 값이 무슨 필드의 값인가'가 구조로 보인다."""
    d = _topic_dossier("eqp.eqp_sensor_raw_1s")
    assert "[보존기간]" in d and "30일 → 90일" in d, d[:500]
    # 적재주기 변경 기록이 없는 대상이므로 그 필드 블록이 있어서는 안 된다 —
    # 있으면 다른 필드의 값을 옮겨 적을 자리가 생긴다.
    assert "[적재주기]" not in d, d[:500]


def test_dossier_works_for_a_technology_not_just_a_table():
    """테이블만의 이야기가 아니다 — 특정 기술도 조각이 똑같이 흩어져 있다."""
    d = _topic_dossier("Schema Registry")
    assert "[호환성 정책]" in d and "BACKWARD → FULL" in d and "DL-9071" in d
    assert "skcc.x1501" in d


def test_history_instruction_only_rides_when_history_was_asked():
    """이력 지시를 모든 경로에 실으면 값 하나 묻는 질문에도 연표가 쏟아진다
    (실측 DATA1: '현재 적재주기는?' 에 8행 연표 + 참조 10개)."""
    hist = _topic_dossier(TABLE, history=True)
    assert "이 대상의 **연표**" in hist
    # ★ **표로** 옮기라고 시켜야 한다 — 예전 문구("그대로 옮겨 서술한다")는 줄글을 시키는
    #   말이었고, 실제로 티켓 8건을 표 없이 늘어놓은 실행이 나왔다(실측 DATA11).
    assert "3열 표로" in hist and "줄글로 늘어놓지 마라" in hist
    # 이력 질문의 답은 **연표 + 현재 상태** 두 덩어리다(실사용 지적: 연표만 달랑 나왔다)
    # 답은 **현재 상태 + 현재 진행 중인 Task + 연표** 세 덩어리다(사용자 지적으로 진행 중
    # 작업이 자기 제목을 갖게 됐다 — 표 아래에 줄로 흘리면 표의 꼬리처럼 읽힌다).
    assert "**현재 상태**" in hist and "현재 진행 중인 Task" in hist
    assert "세 덩어리" in hist
    assert "- {{ticket-detail:DL-9047}}" in hist
    assert '- DL-9047 "' not in hist
    plain = _topic_dossier(TABLE, history=False)
    assert "이 대상의 **연표**" not in plain
    assert "물어본 것만 답한다" in plain
    # 목록 자체는 양쪽 다 남는다 — 값의 출처를 찾는 지도로는 여전히 필요하다
    assert "DL-9041" in plain and "DL-9047" in plain


def test_history_material_merges_ticket_events_and_field_changes_into_one_table():
    """재료가 두 갈래면 그 변동이 산다 — 실측(DATA11): 모델이 '변경 이력' 블록만 보고
    2건짜리 표를 냈다(같은 케이스 다른 실행은 5건·8건). 한 표로 주면 고를 여지가 없다."""
    hist = _topic_dossier(TABLE, history=True)
    chrono = hist.split("이 대상의 **연표**")[1].split("★")[0]
    for must in ("DL-9041", "DL-9042", "DL-9043", "DL-9047"):     # 요청·구축·장애·진행중
        assert must in chrono, f"{must} 가 연표에서 빠졌다"
    assert "[적재주기]" in chrono and "[스키마]" in chrono         # 필드 변경도 같은 표에
    assert "In Progress" in chrono                                 # 진행 중 상태가 보인다
    dates = [ln.split("·")[0].strip("- ").strip() for ln in chrono.strip().splitlines()]
    assert dates == sorted(dates), "연표가 시간순이 아니다"


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
from app.agent.workflow.state import Intent, Node          # noqa: E402


def _msg(text):
    from langchain_core.messages import HumanMessage
    return {"messages": [HumanMessage(content=text)]}


def test_asset_question_reaches_the_investigator_even_if_misclassified():
    """progress로 오분류되면 Portfolio Analyst로 가는데 검색 도구가 없다 — 코드가 막는다."""
    st = {**_msg(f"{TABLE} 현재 적재주기는?"), "intent": Intent.PROGRESS}
    assert G.route_after_request_architect(st) == "investigate"
    # 티켓 키를 짚은 진짜 현황 질문은 그대로 Portfolio Analyst로 간다
    st2 = {**_msg("DL-101 어디까지 왔어?"), "intent": Intent.PROGRESS, "mentioned_keys": ["DL-101"]}
    assert G.route_after_request_architect(st2) == Node.PORTFOLIO_ANALYST


def test_asset_question_goes_through_the_curator():
    """'적재주기는?' 은 기존 지식 키워드에 하나도 안 걸린다 — 식별자로 판정한다."""
    assert G.route_after_research_analyst({**_msg(f"{TABLE} 적재주기는?"),
                                    "intent": Intent.ASK}) == "curate"
    assert G.route_after_research_analyst({**_msg("DL-207 을 x1103 에게 맡겨도 될까?"),
                                    "intent": Intent.ASK}) == "respond"


def test_gathered_material_actually_reaches_the_next_roles():
    """State 에 선언이 없으면 LangGraph 가 반환값에서 버린다 — KnowledgeCurator 자료가 늘 비어 있었다."""
    from app.agent.workflow.agents.knowledge_curator import KnowledgeCurator
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.state import AgentState

    for key in ("pre_survey", "web_context", "seed_map", "topic_dossier"):
        assert key in AgentState.__annotations__, f"{key} 가 State 에 없다"
    out = ResearchAnalyst().apply({"topic_dossier": "X-MARK", "pre_survey": "P-MARK"},
                            {"situation": "s", "evidence": []})
    assert out["topic_dossier"] == "X-MARK" and out["pre_survey"] == "P-MARK"
    assert "X-MARK" in KnowledgeCurator().task({"topic_dossier": "X-MARK"})


def test_historian_injects_the_dossier_into_its_own_prompt():
    assert "X-MARK" in __import__(
        "app.agent.workflow.agents.research_analyst", fromlist=["ResearchAnalyst"]
    ).ResearchAnalyst().task({"topic_dossier": "X-MARK", **_msg("q")})


# ── 답변 깊이 ──────────────────────────────────────────────────────
def test_answer_depth_shapes_the_reply_instruction():
    """값을 물으면 결론형, 경위·개념을 물으면 설명형 — 어느 쪽이든 더 깊은 설명은 다음 턴에."""
    from app.agent.workflow.agents.result_integrator import ResultIntegrator
    r = ResultIntegrator()
    brief = r.task({**_msg("적재주기는?"), "intent": Intent.ASK, "answer_depth": "brief"})
    assert "Answer only what was asked" in brief and "omit generic background" in brief
    deep = r.task({**_msg("왜 바뀌었어?"), "intent": Intent.ASK, "answer_depth": "explain"})
    assert "Explain relevant background, concept, and history" in deep
    assert "Write the final answer in Korean" in brief and "Write the final answer in Korean" in deep
    assert "Do not append a generic offer for more help" in brief
    assert "Do not append a generic offer for more help" in deep


def test_depth_instruction_is_skipped_while_asking_questions():
    """되묻는 턴은 질문 폼이 주인공이라 깊이 지시가 끼어들면 안 된다."""
    from app.agent.workflow.agents.result_integrator import ResultIntegrator
    t = ResultIntegrator().task({**_msg("초안 잡아줘"), "questions": ["범위가 어디까지인가요?"],
                          "answer_depth": "brief"})
    assert "## Answer Depth" not in t


def test_planner_defaults_to_brief_when_unsure():
    from app.agent.workflow.agents.request_architect import RequestArchitect
    out = RequestArchitect().apply({}, {"intent": Intent.ASK, "keywords": ["x"]})
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


# ── 티켓 진척 조사 ─────────────────────────────────────────────────
# "이 티켓 지금 어디까지 됐어?"의 답은 상태 필드에 없다. 근거가 네 군데로 흩어져 있고,
# 넷 다 모여야 "무엇이 끝났고 무엇이 막혔는지"가 나온다.
from app.agent.tools.survey_tools import progress_report          # noqa: E402
from app.agent.workflow.agents.portfolio_analyst import _ticket_progress        # noqa: E402

PROG = "DL-9090"


def test_progress_fixture_spreads_evidence_across_four_places():
    """픽스처 전제 — 상태 필드만 보면 'In Progress' 한 단어뿐이어야 한다."""
    w = _w()
    it = w.issues[PROG]
    assert it["statusName"] == "In Progress"
    assert len(it["changelog"]) >= 3 and len(it["comments"]) >= 4
    assert it["subtasks"] == ["DL-9093", "DL-9094", "DL-9095"]
    assert any(x["key"] == "DL-9092" for x in it["links"])


def test_progress_report_gathers_all_four_kinds_of_evidence():
    r = progress_report(PROG)
    assert r["children_done"] == "2/3", r.get("children")
    assert any(c["field"] == "마감" for c in r["changes"]), "마감 연기는 진척 사건이다"
    assert any("DL-9092" in (m.get("text") or "") for m in r["comments"])
    assert any(x["key"] == "DL-9092" and x["done"] for x in r["links"]), "막던 티켓의 해소"
    doc = (r.get("documents") or [{}])[0]
    assert doc.get("updated") and "남은 일" in (doc.get("excerpt") or ""), doc


def test_comments_keep_author_id_and_time_order():
    """이름만 남기면 '누가 보고했나'를 검증할 수 없고, 순서가 없으면 이야기가 안 된다."""
    ms = progress_report(PROG)["comments"]
    assert all(m["who"].startswith(("skcc.", "lead")) for m in ms), ms
    assert [m["date"] for m in ms] == sorted(m["date"] for m in ms)


def test_progress_preaggregation_only_fires_for_progress_questions():
    base = {"mentioned_keys": [PROG], "intent": Intent.PROGRESS}
    assert "하위 Sub-Task 2/3" in _ticket_progress({**_msg("DL-9090 지금 어디까지 됐어?"), **base})
    # 키를 안 댔으면 대상이 없다 — 비싼 취합을 돌리지 않는다
    assert _ticket_progress({**_msg("진척 어때?"), "intent": Intent.PROGRESS}) == ""
    # 진척 질문이 아니면(단순 수정 요청) 돌지 않는다
    assert _ticket_progress({**_msg("DL-9090 우선순위 바꿔줘"),
                             "mentioned_keys": [PROG], "intent": Intent.MODIFY}) == ""


def test_progress_preaggregation_recovers_explicit_key_after_context_reset():
    """A new authoritative request can temporarily have no carried mentioned_keys."""
    material = _ticket_progress({**_msg("DL-9090과 하위 Task의 진행상황과 남은 작업만 알려줘"),
                                 "mentioned_keys": [], "intent": Intent.PROGRESS})
    assert all(key in material for key in ("DL-9090", "DL-9093", "DL-9094", "DL-9095"))


def test_responder_reports_progress_as_a_story_not_a_status_word():
    from app.agent.workflow.agents.result_integrator import ResultIntegrator
    t = ResultIntegrator().task({**_msg("DL-9090 진척 어때?"), "intent": Intent.PROGRESS,
                          "ticket_progress": _ticket_progress(
                              {**_msg("DL-9090 진척 어때?"), "mentioned_keys": [PROG],
                               "intent": Intent.PROGRESS})})
    assert "remaining work plus deadline risk" in t
    assert "updated result documents" in t
    assert "하위 Sub-Task 2/3" in t, "취합 자료가 프롬프트에 실려야 한다"


def test_follow_up_keeps_the_ticket_in_context():
    """후속 턴의 지시대명사는 앞 턴 대상을 가리킨다 — 실측: 'DL-9090 진척' 다음 '마감까지
    위험한 건?'에서 대상을 잃고 프로젝트 전체의 마감 초과 티켓을 답했다."""
    from app.agent.workflow.agents.request_architect import _carry_keys
    prev = {"mentioned_keys": [PROG], "turns": 1, "situation": "조사됨"}
    assert _carry_keys({**prev, **_msg("마감까지 위험한 건 뭐야?")}, {}) == [PROG]
    assert _carry_keys({**prev, **_msg("그럼 남은 일은?")}, {}) == [PROG]
    # 이번 턴이 키를 댔으면 그게 우선
    assert _carry_keys({**prev, **_msg("DL-101 은?")}, {"mentioned_keys": ["DL-101"]}) == ["DL-101"]
    # 첫 턴은 이어받을 것이 없다
    assert _carry_keys(_msg("마감 위험한 거 뭐야?"), {}) == []
    # 새 주제를 길게 말하면 앞 대상을 끌고 오지 않는다
    assert _carry_keys({**prev, **_msg("카탈로그 모듈에서 메타데이터 등록이 안 된 테이블들을 "
                                       "정리하는 작업을 새로 시작하려고 하는데 초안 잡아줘")}, {}) == []


def test_how_to_questions_do_not_take_the_dossier_shortcut():
    """사용법 질문의 답은 티켓이 아니라 knowledge/05 에 있다. 그런데 주제 dossier 직결이
    티켓을 물어와 그것으로 답해 버렸다(실측 GUIDE7: "티켓 담당자 어떻게 바꿔?" 에 UI 회귀
    픽스처 티켓 DL-9010).

    §5-c 의 "사전취합이 자라면 ReAct 에만 있던 도구가 조용히 도달 불능이 된다"가 한 겹 더
    깊게 재현된 것 — 이번에 도달 불능이 된 것은 도구가 아니라 **_presurvey 에 이미 있던
    search_rules 배선**이었다. 사전취합이 사전취합을 가렸다."""
    from app.agent.workflow.agents.research_analyst import _HOWTO_WORDS
    for q in ("LTM에서 티켓 담당자는 어떻게 바꿔?", "강제 새로고침은 어디 있어?",
              "이 앱에서 단축키 뭐 있어?"):
        assert any(w in q for w in _HOWTO_WORDS), q
    # 자산 질의는 여전히 dossier 로 간다 — 사용법 낱말이 걸리면 안 된다
    for q in ("fdc.fdc_trace_summary_ic 적재주기는?", "wip.wip_lot_track_hist 지금 담당 누구야?"):
        assert not any(w in q for w in _HOWTO_WORDS), q


def test_how_to_material_is_the_guide_only_not_ticket_search():
    """답이 티켓에 없는데 재료에 티켓이 있으면 모델은 그걸 고른다 — 규칙 발췌를 '1차 출처'라
    못 박아 나란히 줘도 졌다(실측 GUIDE7). 고르게 두지 말고 **줄 것만 준다.**

    그리고 출처 문서를 **이름으로 아는** 질문이라 의미 검색의 운에 맡기지 않는다: k=6 까지
    늘려도 05-ltm-guide 에서 한 절만 오고 나머지는 티켓 작성 규칙이 유사도에서 이겨,
    '담당자 변경'은 답하고 '강제 새로고침'은 "확인되지 않았다"고 했다. 가이드는 3KB 다."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.research_analyst import _presurvey
    st = {"messages": [HumanMessage(content="LTM에서 티켓 담당자는 어떻게 바꿔? "
                                            "그리고 강제 새로고침은 어디 있어?")],
          "keywords": ["티켓 담당자", "강제 새로고침", "LTM"]}
    m = _presurvey(st)
    assert "LTM 사용 가이드" in m
    for must in ("인라인", "새로고침", "↻"):        # 두 질문의 답이 **둘 다** 있어야 한다
        assert must in m, f"{must} 가 재료에서 빠졌다"
    assert "키워드 검색" not in m and "의미 검색" not in m, "이 갈래에서 티켓은 소음이다"


def test_asset_questions_still_get_ticket_search():
    """사용법 차단이 자산 질의까지 굶기면 안 된다."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.research_analyst import _presurvey
    st = {"messages": [HumanMessage(content="fdc.fdc_trace_summary_ic 적재주기는?")],
          "keywords": ["fdc.fdc_trace_summary_ic", "적재주기"]}
    assert "키워드 검색" in _presurvey(st)


def test_candidate_material_reaches_the_responder():
    """코드가 로스터·부하까지 조회해 실어 줬는데 **ResultIntegrator 에 오지 않았다** —
    pre_survey 에서 티켓 현재값과 문서 본문만 잘라 썼기 때문이다. 그래서 후보가 ResearchAnalyst
    의 situation 요약 한 겹을 지나며 사라졌다(실측 EDGE13: "누가 하면 좋을지랑 지금 상황"
    에 상황만 답하고 후보를 통째로 뺐다 — 세 번 연속, 재료에는 사번까지 있었다)."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.research_analyst import _presurvey
    from app.agent.workflow.agents.result_integrator import _candidate_block
    q = "카탈로그쪽 메타 등록 안된 태이블들 정리하는 일 누가 하면 좋을지랑 지금 상황 알려줘"
    pre = _presurvey({"messages": [HumanMessage(content=q)],
                      "keywords": ["메타 등록", "테이블", "카탈로그"], "module": "Catalog"})
    block = _candidate_block(pre)
    assert "skcc." in block and "진행중" in block, block[:300]
    # 후보를 안 물은 질의에서는 비어 있어야 한다 — 늘 실으면 토큰만 먹는다
    assert _candidate_block("키워드 검색:\n- DL-1 x") == ""


def test_the_guide_material_does_not_disable_the_direct_path():
    """직결 경로는 dossier 에 '찾지 못했다'가 있으면 꺼진다(미발견 dossier 로 결론 내지
    않으려는 가드). 처음 쓴 가이드 헤더에 그 문구가 들어가 **지시문이 자기가 타야 할
    경로를 막는** 꼴이었다."""
    from app.agent.workflow.agents.research_analyst import _ltm_guide
    g = _ltm_guide()
    assert g and "찾지 못했다" not in g, g[:200]
    for must in ("인라인", "새로고침", "↻"):
        assert must in g, f"{must} 가 가이드에서 빠졌다"
    assert "티켓이 아니다" in g and "이력" in g


def test_module_only_evidence_is_dropped_but_named_keys_survive():
    """common.md 의 관련성 기준이 **산문으로만** 있어 반복해 샜다 — REL14 는 "Iceberg
    Puffin NDV 통계" 에 모듈만 같은 DL-5487·5876 을, EDGE13 은 "메타 등록 안 된 테이블" 에
    UI 회귀 픽스처 DL-9001 을 근거로 붙였다. 노이즈는 신뢰를 깎고, 문서 자신이 "관련 이력
    없음이 정답인 자리를 채우는 것이 더 나쁘다"고 적어 뒀다."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.research_analyst import _relevant_only
    req = "ETL 파이프라인에서 Iceberg Puffin NDV 통계정보를 생성하는 단계를 추가하려고해"
    st = {"messages": [HumanMessage(content=req)], "request_text": req}
    ev = [{"key": "DL-5487", "title": "[ETL] 경계값 오류 수정", "why": "ETL 모듈"},
          {"key": "DL-9100", "title": "[ETL] Iceberg 테이블 통계 수집 검토", "why": "같은 주제"}]
    assert [e["key"] for e in _relevant_only(st, ev)] == ["DL-9100"]

    # 사용자가 직접 댄 키는 관련성 판단의 대상이 아니다
    st2 = {"messages": [HumanMessage(content="DL-5487 어떻게 됐어?")],
           "request_text": "DL-5487 어떻게 됐어?", "mentioned_keys": ["DL-5487"]}
    assert len(_relevant_only(st2, [ev[0]])) == 1

    # 고유어가 없는 질문에서는 **아무것도 빼지 않는다** — 판정 근거가 없으면 판정하지 않는다
    st3 = {"messages": [HumanMessage(content="안녕")], "request_text": "안녕"}
    assert len(_relevant_only(st3, ev)) == 2


def test_the_relevance_filter_does_not_starve_key_centric_or_typo_questions():
    """가드가 잘못 막는 쪽을 **같은 자리에서** 고정한다 — 이 필터는 넣자마자 두 케이스를
    깨뜨렸다(내가 바로 앞 커밋에 "가드는 옆 케이스와 함께 재라"고 적어 놓고 5케이스만 봤다):

      · PROG1 — "DL-9090 지금 어디까지?" 의 근거는 자식·차단처럼 **구조로** 이어진 티켓이라
        제목에 질문 낱말이 없는 것이 정상인데, 막고 있던 DL-9092 가 통째로 걸러졌다.
      · DATA11 — 오탈자로 물었으니 원문 낱말이 실제 제목과 한 글자도 안 겹쳐 근거가 전멸했다.
    """
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.research_analyst import _relevant_only
    key_centric = {"messages": [HumanMessage(content="DL-9090 지금 어디까지 진행됐어?")],
                   "request_text": "DL-9090 지금 어디까지 진행됐어?",
                   "mentioned_keys": ["DL-9090"]}
    ev = [{"key": "DL-9092", "title": "[Workbench] 인덱스 추가", "why": "막고 있던 것"}]
    assert len(_relevant_only(key_centric, ev)) == 1, "키 중심 질문에는 필터를 걸지 않는다"

    typo = {"messages": [HumanMessage(content="fdc.fdc_trace_summary_ic 말한거야")],
            "request_text": "fdc_flat_summary_ic 데이터의 히스토리",
            "topic_dossier": "[대상] fdc.fdc_trace_summary_ic\n관련 티켓…"}
    ev2 = [{"key": "DL-9044", "title": "[ETL] fdc.fdc_trace_summary_ic 적재주기 변경", "why": ""}]
    assert len(_relevant_only(typo, ev2)) == 1, "코드가 확정한 대상도 판정 낱말이다"
