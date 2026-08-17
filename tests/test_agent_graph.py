"""agent/workflow — 그래프 분기 · 되묻기 · HITL 중단/재개.

키 없이 돈다(`fake`). 여기서 검증하는 것은 **문장 품질이 아니라 구조**다 —
어떤 의도가 어떤 길로 가는지, 되묻기가 왜 멈추는지, 승인 전에 정말로 아무것도 안 만드는지.
그건 실 LLM 으로도 확인하기 어렵다(매번 다른 문장이 나오므로).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langgraph", reason="requirements-agent.txt 미설치")

from app.agent import approval                                     # noqa: E402
from app.agent.workflow import graph as G                          # noqa: E402
from app.agent.workflow.state import (MAX_REVISIONS, Intent, Node)  # noqa: E402


@pytest.fixture(autouse=True)
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    import app.infra.settings as S
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    approval.clear()
    G.reset()
    yield
    approval.clear()
    G.reset()


# ── 라우터: State 만 보고 결정한다 ──────────────────────────────────
def test_chitchat_skips_investigation():
    assert G.route_after_request_architect({"intent": Intent.CHITCHAT}) == "respond"


def test_memory_only_context_is_acknowledged_without_research():
    from langchain_core.messages import HumanMessage

    state = {
        "intent": Intent.ASK,
        "messages": [HumanMessage(content=(
            "참고로 다음 주 점검이 예정돼 있어. 지금은 답하지 말고 이 정보만 기억해줘."
        ))],
    }
    assert G.route_after_request_architect(state) == "respond"


def test_shared_context_with_an_actual_request_still_uses_the_normal_route():
    from langchain_core.messages import HumanMessage

    state = {
        "intent": Intent.ASK,
        "messages": [HumanMessage(content=(
            "참고로 다음 주 점검이 예정돼 있어. 이 정보만 기억하고 DL-9090 현황도 알려줘."
        ))],
    }
    assert G.route_after_request_architect(state) == "investigate"


def test_everything_else_investigates_first():
    """조사를 건너뛰고 티켓을 만들어 주는 어시스턴트는 중복 티켓 생성기다.
    plan_work 는 요청이 구체적(sufficient)일 때 조사부터 — 막연하면 해석 확인이 먼저다."""
    for intent in (Intent.ASK, Intent.MODIFY):
        assert G.route_after_request_architect({"intent": intent}) == "investigate"
    assert G.route_after_request_architect({"intent": Intent.PLAN_WORK,
                                  "sufficient": True}) == "investigate"


def test_reviewer_keeps_editorial_advice_non_blocking():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.auditor import _partition_model_problems

    state = {
        "messages": [HumanMessage(content="단계별 Sub-Task로 나눠줘")],
        "draft": {"structure": "task_with_subtasks",
                  "structure_source": "user_specified",
                  "items": [{"type": "Task", "children": [{"summary": "구현"}]}]},
    }
    raw = [
        {"check": "request", "message": "과잉 분해로 Sub-Task가 불필요하게 나뉘었다"},
        {"check": "rule", "message": "제목이 동사로 끝나지 않는다"},
        {"check": "grounded", "message": "본문의 DL-9999는 조사 근거에 없다"},
        {"check": "rule", "message": "지정 담당자 skcc.x1402가 존재하지 않는다"},
    ]
    blocking, advice = _partition_model_problems(state, raw)
    assert [p["check"] for p in blocking] == ["grounded"]
    assert len(advice) == 3


def test_bug_contract_does_not_require_task_dod():
    from app.agent.workflow.agents.auditor import _machine_check

    state = {"draft": {"mode": "task", "items": [{
        "summary": "[Workbench] 리니지 화면이 빈다", "type": "Bug",
        "components": ["Workbench"],
        "description": ("<h3>재현 경로</h3><p>2홉을 펼친다.</p>"
                        "<h3>기대 동작</h3><p>그래프가 보인다.</p>"
                        "<h3>실제 동작</h3><p>화면이 빈다.</p>")}]}}
    review = _machine_check(state)
    assert review["ok"]
    assert not any("완료 조건" in w.get("message", "") for w in review["warnings"])


def test_a_plain_question_stops_after_investigation():
    assert G.route_after_research_analyst({"intent": Intent.ASK}) == "respond"
    assert G.route_after_research_analyst({"intent": Intent.PLAN_WORK}) == "refine"


def test_questions_go_back_to_the_user_instead_of_drafting():
    assert G.route_after_work_architect({"questions": ["범위가 어디까지인가요?"],
                                  "draft": {"items": [{"summary": "x"}]}}) == "respond"


def test_a_draft_fans_out_to_assign_and_review_in_parallel(monkeypatch):
    # 초안이 서면 PeopleAdvisor 와 Auditor 가 동시에 돈다 — 직렬이던 스텝을 접은 최적화(P-2).
    monkeypatch.setattr(G, "_parallel_role_calls_allowed", lambda: True)
    assert G.route_after_work_architect({"questions": [], "draft": {"items": [{"summary": "x"}]}}) \
        == ["assign", "review"]


def test_single_queue_model_schedules_assignment_and_audit_sequentially(monkeypatch):
    monkeypatch.setattr(G, "_parallel_role_calls_allowed", lambda: False)
    assert G.route_after_work_architect({
        "questions": [], "draft": {"items": [{"summary": "x"}]},
    }) == "sequential"


def test_an_empty_draft_does_not_pretend_to_have_one():
    assert G.route_after_work_architect({"questions": [], "draft": {"items": []}}) == "respond"


def test_review_failure_sends_it_back_to_be_rewritten():
    """재작성은 기계 오류가 있을 때만 — LLM 의견만으로 왕복하면 턴이 200초를 넘겼다."""
    assert G.route_after_auditor({"review": {"ok": False,
                                              "errors": [{"message": "없는 부모"}]},
                                   "revisions": 1}) == "revise"
    assert G.route_after_auditor({"review": {"ok": False, "errors": [],
                                              "problems": [{"message": "의견"}]},
                                   "revisions": 0}) == "propose"


def test_rewrite_loop_is_bounded_but_humans_still_get_to_judge():
    """상한이 없으면 두 모델이 서로 만족하지 못해 무한히 돈다. 소진 뒤의 갈래:

    기계 오류가 남았으면 respond(만들어 봤자 Jira 가 거부한다). **LLM 의견만** 남았으면
    propose — 검열자가 만족할 때까지 승인을 막으면 사람이 판단할 기회가 사라진다
    (실제로 멀쩡한 근거를 '불충분'이라 두 번 반려해 승인 카드가 아예 안 떴다).
    """
    exhausted = {"revisions": MAX_REVISIONS}
    assert G.route_after_auditor({**exhausted,
                                   "review": {"ok": False,
                                              "errors": [{"message": "없는 부모"}]}}) == "respond"
    assert G.route_after_auditor({**exhausted,
                                   "review": {"ok": False, "errors": [],
                                              "problems": [{"message": "의견"}]}}) == "propose"


def test_passing_review_goes_to_approval_not_straight_to_execution():
    assert G.route_after_auditor({"review": {"ok": True}}) == "propose"


def test_responder_waits_for_approval_when_a_token_is_pending():
    assert G.route_after_result_integrator({"approval_token": "t"}) == "execute"


def test_responder_ends_after_execution_instead_of_looping():
    assert G.route_after_result_integrator({"approval_token": "t", "result": {"created": []}}) == "end"


def test_responder_ends_when_there_is_nothing_to_approve():
    assert G.route_after_result_integrator({}) == "end"


# ── 조립 ───────────────────────────────────────────────────────────
def test_graph_has_all_six_roles():
    nodes = set(G.build().get_graph().nodes)
    for n in (Node.REQUEST_ARCHITECT, Node.RESEARCH_ANALYST, Node.WORK_ARCHITECT, Node.PEOPLE_ADVISOR,
              Node.AUDITOR, Node.ACTION_EXECUTOR, Node.RESULT_INTEGRATOR):
        assert n in nodes


def test_tool_using_roles_really_are_subgraphs():
    """서브그래프가 아니면 stream(subgraphs=True) 가 '도구 부르는 중'을 못 보여 준다.

    ActionExecutor(결정적 modify)·ResearchAnalyst(결정적 진척률 보강)·PeopleAdvisor(유사 이력 사전 취합)는
    node() 를 한 겹 더 감싸면서 xray 가 클로저 속 서브그래프를 못 본다 — 세 역할의 ReAct 는
    build() 로 따로 지킨다.
    """
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    assert {"think", "act"} <= set(ResearchAnalyst().build().get_graph().nodes)


def test_draft_roles_do_not_use_tools():
    """WorkArchitect·PeopleAdvisor 는 **도구를 쓰지 않는다** — 필요한 재료(허용값·Epic 후보·규칙·
    유사 이력·로스터 부하)를 전부 코드가 미리 조회해 자료로 준다. 도구로 두면 모델이
    매 턴 다시 부르고, 도구 호출 한 번이 곧 LLM 왕복 한 번이라 생성 턴 하나에
    work_architect 12회·people_advisor 5회까지 불어났다(실측 기준선)."""
    from app.agent.workflow.agents.people_advisor import PeopleAdvisor
    from app.agent.workflow.agents.base import StructuredAgent
    from app.agent.workflow.agents.work_architect import WorkArchitect
    for role in (WorkArchitect(), PeopleAdvisor()):
        assert isinstance(role, StructuredAgent)
        assert not getattr(role, "tools", None), f"{role.name} 이 도구를 갖고 있다"


def test_operator_keeps_react_for_creation():
    """ActionExecutor 의 create 갈래는 여전히 ReAct 서브그래프를 탄다(부분 실패 판단이 실제로 있다)."""
    from app.agent.workflow.agents.action_executor import ActionExecutor
    sub = ActionExecutor().build()
    assert {"think", "act"} <= set(sub.get_graph().nodes)


def test_diagram_renders():
    assert G.build().get_graph(xray=1).draw_mermaid().strip()


def test_interrupt_needs_a_checkpointer():
    """체크포인터가 없으면 멈출 자리가 없다 — 그때는 interrupt 도 걸지 않는다."""
    assert not getattr(G.build(), "interrupt_before_nodes", None)


# ── 승인 게이트 ────────────────────────────────────────────────────
def _staged(thread="t1", items=None):
    items = items or [{"summary": "CDC 도입 검토", "type": "Task", "epic": None}]
    state = {"thread_id": thread, "draft": {"mode": "task", "items": items}}
    return G._propose(state)["approval_token"], items


def test_propose_issues_a_token_bound_to_the_draft():
    tok, items = _staged()
    rec = approval.peek(tok)
    assert rec["action"] == "create_tickets" and rec["approved"] is False
    assert rec["fp"] == approval.fingerprint({"mode": "task", "items": items})


def test_propose_issues_nothing_for_an_empty_draft():
    assert G._propose({"thread_id": "t1", "draft": {"mode": "task", "items": []}}) == {}


def test_proposed_token_alone_cannot_create_anything():
    """제안 단계의 토큰은 아직 '승인'이 아니다."""
    from app.agent import tools as T
    tok, items = _staged()
    r = T.BY_NAME["create_tickets"].invoke({"mode": "task", "items": items, "approval_token": tok})
    assert r["ok"] is False and "승인" in r["error"]


def test_a_token_from_another_conversation_is_refused():
    from app.agent.workflow import session
    tok, _ = _staged(thread="t1")
    out = session.resume("t2-남의대화", tok)
    assert out["ok"] is False


# ── 전체 왕복 (fake) ───────────────────────────────────────────────
def test_a_full_turn_runs_and_produces_a_reply():
    from app.agent.workflow import session
    out = session.ask("실시간 수집 파이프라인에 CDC 방식을 도입해야 한다")
    assert out["thread_id"]
    assert out["reply"], out
    assert [t["node"] for t in out["trace"]], "어느 에이전트가 무엇을 했는지 남아야 한다"


def test_the_same_thread_keeps_its_history():
    """Checkpointer 가 없으면 되묻기가 불가능하다 — 답만 듣고 앞을 다 잊는다."""
    from app.agent.workflow import session
    first = session.ask("CDC 도입 검토가 필요하다")
    second = session.ask("범위는 수집까지야", thread_id=first["thread_id"])
    assert second["thread_id"] == first["thread_id"]
    snap = G.get_graph().get_state({"configurable": {"thread_id": first["thread_id"]}})
    humans = [m for m in (snap.values.get("messages") or []) if getattr(m, "type", "") == "human"]
    assert len(humans) >= 2, "두 번째 발화가 같은 대화에 쌓이지 않았다"


def test_nothing_is_created_before_approval():
    """★ HITL 의 본질. 그래프가 어디서 멈추든 티켓이 만들어져 있으면 안 된다."""
    from app.agent.tools import _ctx
    from app.agent.workflow import session
    before = len(_ctx.client().search_issues("ORDER BY created DESC", max_results=200))
    session.ask("신규 카탈로그 품질 규칙을 만들어야 한다")
    after = len(_ctx.client().search_issues("ORDER BY created DESC", max_results=200))
    assert after == before, "승인 전에 티켓이 만들어졌다"


# ── 승인 대기 → 재개 → 실제 생성 (가장 중요한 이음매) ──────────────
ITEMS = [{"summary": "CDC 도입 방식 검토 및 결정", "type": "Task", "epic": None}]


@pytest.fixture
def real_draft(monkeypatch):
    """fake 로는 못 넘는 두 곳만 고정하고, 시험하려는 이음매는 전부 진짜로 굴린다.

    고정하는 것:
      · WorkArchitect — fake 는 배열을 비워 두므로 초안이 아예 서지 않는다.
      · Auditor 의 **LLM 의견** — fake 는 boolean 을 해시로 정해 매번 갈린다.
        단 **기계 판정(`validate_bulk`)은 진짜로 돌린다.** 규칙에 어긋난 초안이 통과하면
        이 테스트가 무의미해지기 때문이다.
      · ActionExecutor 의 **문장** — 시험하려는 건 말이 아니라 토큰이 도구까지 닿는가다.

    진짜로 도는 것: PeopleAdvisor · 기계 검증 · propose(토큰 발급) · interrupt · 재개 ·
    승인 토큰 대조 · 실제 티켓 생성.
    """
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.agents.work_architect import WorkArchitect, as_bulk_items
    from app.agent.workflow.agents.auditor import Auditor, _machine_check
    from app.agent.workflow.state import Intent

    # RequestArchitect 도 고정한다 — fake 의 enum 선택은 해시라 **의도 갈래가 늘어날 때마다** 어디로
    # 떨어질지 바뀐다(실제로 pmo 갈래가 생기자 이 시나리오가 그쪽으로 새서 깨졌다).
    # 이 테스트의 관심사는 분류가 아니라 승인 이음매다.
    monkeypatch.setattr(RequestArchitect, "node", lambda self: (lambda state: {
        "intent": Intent.PLAN_WORK, "keywords": ["CDC"], "sufficient": True}))

    monkeypatch.setattr(WorkArchitect, "node", lambda self: (lambda state: {
        "questions": [], "turns": 1,
        "draft": {"mode": "task", "items": [dict(ITEMS[0])], "rationale": "방식이 정해지기 전엔 검토만"}}))

    def rv_node(self):
        def run(state):
            auto = _machine_check(state)        # ← 여기는 진짜다
            return {"review": {"ok": auto["ok"], "problems": [],
                               "checks": {"grounded": True, "rule_compliant": auto["ok"],
                                          "answers_request": True},
                               "errors": auto["errors"], "warnings": auto["warnings"],
                               "summary": "기계 판정만 사용(fake 환경)"},
                    "revisions": (state.get("revisions") or 0) + 1}
        return run

    monkeypatch.setattr(Auditor, "node", rv_node)

    # ActionExecutor 의 LLM 만 건너뛴다 — 시험하려는 것은 문장이 아니라 **토큰이 도구까지 닿는가**다.
    def op_node(self):
        from app.agent import tools as T

        def run(state):
            r = T.BY_NAME["create_tickets"].invoke({
                "mode": (state.get("draft") or {}).get("mode") or "task",
                "items": as_bulk_items(state.get("draft")),
                "approval_token": state.get("approval_token") or ""})
            return {"result": {"created": r.get("created") or [], "failed": r.get("failed") or [],
                               "error": r.get("error") or ""}}
        return run

    monkeypatch.setattr(ActionExecutor, "node", op_node)
    G.reset()
    yield
    G.reset()


def test_graph_stops_and_asks_for_approval(real_draft):
    from app.agent.workflow import session
    out = session.ask("실시간 수집에 CDC 를 도입해야 한다")
    assert out.get("pending"), f"승인 카드가 안 떴다: {out.get('review')}"
    assert out["pending"]["items"][0]["summary"] == ITEMS[0]["summary"]
    assert not out["result"], "승인 전인데 실행 결과가 있다"


def test_resume_after_approval_actually_creates_the_ticket(real_draft):
    from app.agent.tools import _ctx
    from app.agent.workflow import session
    out = session.ask("실시간 수집에 CDC 를 도입해야 한다")
    tok = out["pending"]["token"]

    done = session.resume(out["thread_id"], tok)
    created = (done.get("result") or {}).get("created") or []
    assert created, done
    key = created[0]["key"]
    assert _ctx.client().get_issue(key), "생성됐다는데 실물이 없다"


def test_cancelling_leaves_nothing_behind(real_draft):
    from app.agent.workflow import session
    out = session.ask("실시간 수집에 CDC 를 도입해야 한다")
    tok = out["pending"]["token"]
    assert session.cancel(out["thread_id"], tok)["cancelled"] is True
    # 거절한 토큰으로는 재개해도 아무것도 만들어지지 않는다
    again = session.resume(out["thread_id"], tok)
    assert again["ok"] is False


def test_trace_labels_are_human_readable():
    from app.agent.workflow import session
    out = session.ask("DL-1 어떻게 되어 가나요")
    assert all(t.get("label") for t in out["trace"])


def test_snapshot_restores_a_conversation():
    from app.agent.workflow import session
    tid = session.ask("데이터 카탈로그 관련 이력 알려줘")["thread_id"]
    assert session.snapshot(tid)["thread_id"] == tid


def test_evaluation_snapshot_exposes_retrieval_evidence_without_secrets():
    from app.agent.workflow import session
    tid = session.ask("데이터 카탈로그 관련 이력 알려줘")["thread_id"]
    evidence = session.evaluation_snapshot(tid)
    assert evidence
    assert set(evidence).issubset({
        "requestPlan", "queryPlan", "queryResults", "queryArtifacts", "preSurvey",
        "seedMap", "webContext", "topicDossier", "evidence", "relatedDocs",
        "knowledgeBrief", "trace",
    })
    assert not ({"messages", "token", "apiKey", "providerConfig"} & set(evidence))


def test_evaluation_case_reset_drops_the_previous_graph_thread():
    from app.agent.workflow import session
    from tools.agent_eval_isolation import begin_case, finish_case

    tid = session.ask("DL-9090 진행 상황 알려줘")["thread_id"]
    assert session.snapshot(tid)
    isolated = begin_case("next-case")
    assert session.evaluation_snapshot(tid) == {}
    assert finish_case(isolated)["worldUnchanged"] is True


def test_knowledge_question_routes_through_curator():
    """지식형 ask("X가 뭐야/정리해줘")는 ResearchAnalyst → KnowledgeCurator → ResultIntegrator 로 흐른다.

    KnowledgeCurator 는 신설 역할(사용자 요청) — 조사 결과를 개념/우리 상황/참고/공백 스키마로
    정리한다. 도구는 없다(새 조사 금지). fake 로 경로와 State 필드만 검증한다.
    """
    from app.agent.workflow.graph import route_after_research_analyst
    from app.agent.workflow.state import Intent
    from langchain_core.messages import HumanMessage
    st = {"intent": Intent.ASK, "messages": [HumanMessage(content="CDC가 뭐야? 정리해줘")]}
    assert route_after_research_analyst(st) == "curate"
    st2 = {"intent": Intent.ASK, "messages": [HumanMessage(content="DL-101 왜 멈췄었지?")]}
    assert route_after_research_analyst(st2) == "respond"
    st3 = {"intent": Intent.PLAN_WORK, "messages": [HumanMessage(content="CDC가 뭐야 정리")]}
    assert route_after_research_analyst(st3) == "refine"
    assert "knowledge_curator" in set(G.build().get_graph().nodes)


def test_curator_produces_brief_from_materials():
    import os
    os.environ["LAKE_AGENT_PROVIDER"] = "fake"
    from app.agent.workflow.agents.knowledge_curator import KnowledgeCurator
    from langchain_core.messages import HumanMessage
    c = KnowledgeCurator()
    txt = c.task({"messages": [HumanMessage(content="CDC가 뭐야?")],
                  "situation": "DL-118 에서 검토", "evidence": [],
                  "web_context": "- CDC 는 변경 데이터 캡처"})
    assert "External Technology Research" in txt and "DL-118" in txt
    out = c.apply({}, {"concepts": [{"term": "CDC", "explanation": "x"}],
                       "our_context": "사내 이력 없음", "references": [], "gaps": ["도입 여부"]})
    kb = out["knowledge_brief"]
    assert kb["concepts"] and kb["gaps"] == ["도입 여부"]


def test_operator_create_is_deterministic_tool_truth():
    """생성 실행도 LLM 없이 — 도구 결과만이 사실이다.

    실측: ReAct 생성이 검증 **경고**(Epic 미연결 안내)를 '실패한 항목·후속 조치'로
    각색해 보고했다. 사용자가 방금 '최상위로 두겠다'고 결정했는데 다시 경고한 셈.
    결정적 실행은 created/failed 를 도구가 준 그대로 옮긴다.
    """
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.work_architect import as_bulk_items
    draft = {"mode": "task", "items": [{"summary": "최상위로 두는 티켓", "type": "Task",
                                        "epic": ""}]}
    tok = approval.stage("t-det", "create_tickets",
                         {"mode": "task", "items": as_bulk_items(draft)})
    approval.approve(tok, "t-det")
    out = ActionExecutor().node()({"draft": draft, "approval_token": tok,
                             "change_plan": {}, "trace": []})
    r = out["result"]
    assert r["created"] and r["created"][0]["key"], r
    assert r["failed"] == [], "경고가 실패로 각색되면 안 된다"
    assert r["note"] == ""


def test_fast_paths_skip_historian_when_safe():
    """빠른 경로 2종 — 후속 턴(조사 결과 보유)과 키 명시 modify 는 재조사 없이 WorkArchitect 직행.

    인터뷰 답변 턴마다 ResearchAnalyst 이 통째로 다시 돌던 것이 턴 시간의 최대 낭비였다
    (턴당 LLM 3~5회). 첫 턴·새 대화는 여전히 조사부터.
    """
    # 첫 턴(구체적 요청) — 조사부터
    assert G.route_after_request_architect({"intent": Intent.PLAN_WORK, "turns": 0,
                                  "sufficient": True}) == "investigate"
    # 첫 턴(막연한 요청) — 조사 전에 해석 확인(clarify)으로 WorkArchitect 직행
    assert G.route_after_request_architect({"intent": Intent.PLAN_WORK, "turns": 0,
                                  "sufficient": False}) == "refine"
    # 막연해도 위임("알아서")이면 묻지 않고 조사부터
    from langchain_core.messages import HumanMessage
    assert G.route_after_request_architect({"intent": Intent.PLAN_WORK, "turns": 0, "sufficient": False,
                                  "messages": [HumanMessage(content="알아서 해줘")]}) == "investigate"
    # 후속 턴 — situation 보유 시 직행
    assert G.route_after_request_architect({"intent": Intent.PLAN_WORK, "turns": 1,
                                  "situation": "DL-118 에서 검토"}) == "refine"
    # modify + 키 명시 — 직행 (키 확인은 WorkArchitect 의 get_ticket 몫)
    assert G.route_after_request_architect({"intent": Intent.MODIFY,
                                  "mentioned_keys": ["DL-101"]}) == "refine"
    # modify 인데 키가 없으면 여전히 조사(어느 티켓인지 찾아야 한다)
    assert G.route_after_request_architect({"intent": Intent.MODIFY}) == "investigate"


def test_trace_reducer_appends_deltas_and_resets_on_sentinel():
    """병렬 fan-out(PeopleAdvisor∥Auditor)에서 두 노드가 같은 스텝에 trace 를 써도
    리듀서가 이어 붙인다. 턴 시작 리셋은 sentinel 로만 가능하다(리듀서엔 대입이 없다)."""
    from app.agent.workflow.state import TRACE_RESET, merge_trace, note
    a = note({}, "people_advisor", "제안 2건")
    b = note({}, "auditor", "통과")
    merged = merge_trace(merge_trace([{"node": "old"}], a), b)
    assert [t["node"] for t in merged] == ["old", "people_advisor", "auditor"]
    assert merge_trace(merged, [TRACE_RESET]) == []
    assert merge_trace(merged, [TRACE_RESET, a[0]]) == a


def test_merge_join_drops_ghost_assignees():
    """Auditor 가 배정 '전' 초안을 검증하므로(병렬), 배정 사용자 실재는 join 코드가 보장한다."""
    real = _any_real_user()
    draft = {"mode": "task", "items": [{"summary": "a"}, {"summary": "b"}]}
    assignments = [{"index": 0, "user": real, "reasons": ["유사 이력 DL-1"]},
                   {"index": 1, "user": "ghost.x9999", "reasons": ["임의"]}]
    out = G._merge_assignments({"draft": draft, "assignments": assignments})
    items = out["draft"]["items"]
    assert items[0].get("assignee") == real
    assert not items[1].get("assignee"), "실재하지 않는 사용자 배정은 join 에서 걸러져야 한다"
    assert not out["assignments"][1].get("user"), "ResultIntegrator 상태에도 유령 추천을 남기지 않는다"


def test_merge_join_resolves_suffix_only_ids():
    """사용자가 "x1103"처럼 접미만 대면 로스터 유일 일치로 풀 아이디로 해소한다 —
    직렬 시절 Auditor 재작성 루프가 하던 교정이 병렬화로 사라져 배정이 통째로 빠졌다(실측)."""
    real = _any_real_user()
    suffix = real.split(".", 1)[1]
    draft = {"mode": "task", "items": [{"summary": "a", "assignee": suffix}]}
    out = G._merge_assignments({"draft": draft, "assignments": []})
    assert out["draft"]["items"][0].get("assignee") == real


def test_merge_join_reapplies_user_named_assignees_after_recommendations():
    from langchain_core.messages import HumanMessage
    draft = {"mode": "subtask", "items": [
        {"summary": "성능을 측정하는 작업", "type": "Sub-Task"},
        {"summary": "가이드를 작성하는 작업", "type": "Sub-Task"}]}
    state = {"messages": [HumanMessage(
        content="성능 측정은 x1402, 가이드 작성은 x1450. 알아서")],
        "draft": draft,
        "assignments": [{"index": 0, "user": "skcc.x1042", "reasons": ["부하 낮음"]},
                        {"index": 1, "user": "skcc.x1045", "reasons": ["부하 낮음"],
                         "alternates": [{"user": "skcc.x1450", "why": "대안"}]}]}
    merged = G._merge_assignments(state)
    out = merged["draft"]["items"]
    assert [i.get("assignee") for i in out] == ["skcc.x1402", "skcc.x1450"]
    assert all(i.get("assignee_source") == "user" for i in out)
    assert [a.get("user") for a in merged["assignments"]] == ["skcc.x1402", "skcc.x1450"]
    assert all("payload" in a["reasons"][0] for a in merged["assignments"])
    assert not merged["assignments"][1].get("alternates"), "1순위와 같은 사용자는 대안이 아니다"


def _any_real_user():
    from app.agent.tools._ctx import client
    lk = client().bulk_lookup()
    for u in ("skcc.x1042", "skcc.x1001", "etl.x1001"):
        if lk.user_exists(u):
            return u
    import pytest
    pytest.skip("mock 사용자 확인 불가")


def test_card_edits_are_applied_and_survive_the_fingerprint(real_draft):
    """카드 인라인 편집(제목·라벨·마감) — State draft 를 고치고 지문을 재생성하므로
    승인·실행이 수정본으로 이루어진다. 두 벌 patch 시절의 지문 어긋남 회귀 방지."""
    from app.agent.tools import _ctx
    from app.agent.workflow import session
    out = session.ask("실시간 수집에 CDC 를 도입해야 한다")
    tok = out["pending"]["token"]
    done = session.resume(out["thread_id"], tok, {
        "items": {"0": {"summary": "[ETL] CDC 파이프라인 도입 — 수정본",
                        "labels": "cdc, q3-2026", "duedate": "2026-09-30"}}})
    created = (done.get("result") or {}).get("created") or []
    assert created, done
    got = _ctx.client().get_issue(created[0]["key"])
    f = got.get("fields") or {}
    assert f.get("summary") == "[ETL] CDC 파이프라인 도입 — 수정본"
    assert "cdc" in (f.get("labels") or [])


def test_card_edit_with_a_bad_duedate_keeps_the_card_alive(real_draft):
    """형식이 틀리면 실행하지 않고 오류만 — 카드는 살아 있어 다시 고칠 수 있다."""
    from app.agent.workflow import session
    out = session.ask("실시간 수집에 CDC 를 도입해야 한다")
    tok = out["pending"]["token"]
    r = session.resume(out["thread_id"], tok, {"items": {"0": {"duedate": "다음주"}}})
    assert r["ok"] is False and "YYYY-MM-DD" in r["error"]
    # 올바른 값으로 다시 — 같은 토큰이 그대로 쓰인다
    done = session.resume(out["thread_id"], tok, {"items": {"0": {"duedate": "2026-10-01"}}})
    assert (done.get("result") or {}).get("created"), done


def test_bulk_change_plan_stages_the_update_tickets_fingerprint():
    """조건 일괄 수정 — 승인 지문이 update_tickets(bulk) 도구의 payload 와 같은 모양이어야
    consume 이 통과한다(E2 실측 갭의 회귀 방지)."""
    from app.agent import approval
    approval.clear()
    plan = {"keys": ["DL-1", "DL-2"], "changes": {"priority": "P1-Critical"}}
    tok = G._propose({"thread_id": "tb", "change_plan": plan})["approval_token"]
    rec = approval.peek(tok)
    assert rec["action"] == "update_tickets"
    rows = [{"key": "DL-1", "changes": {"priority": "P1-Critical"}},
            {"key": "DL-2", "changes": {"priority": "P1-Critical"}}]
    assert rec["fp"] == approval.fingerprint({"items": rows})
    # 라우터도 keys 만으로 propose 로 간다
    assert G.route_after_work_architect({"questions": [], "change_plan": plan, "draft": {}}) == "propose"


# ── 답변 깊이는 대화 단위로 잇는다 (실측: 배터리 DATA13) ────────────────────
def test_answer_depth_is_carried_forward_across_a_clarifying_turn():
    """확인 질문에 답한 턴은 **새 질문이 아니다.** 사용자가 보기 하나를 고르면 그 발화는
    값 질문처럼 보이는데, 거기서 brief 로 떨어지면 ResultIntegrator 의 '물어본 것만 답하라'가
    원 요청(히스토리)을 눌러 버린다 — 실측: 티켓 8건 중 2건만 남았다."""
    from app.agent.workflow.agents.request_architect import _carry_depth
    assert _carry_depth({"answer_depth": "explain"}, {"answer_depth": "brief"}) == "explain"
    assert _carry_depth({"answer_depth": "explain"}, {}) == "explain"
    # 올리는 쪽으로만 붙인다 — 새 대화가 설명형이면 그대로 설명형이다
    assert _carry_depth({"answer_depth": "brief"}, {"answer_depth": "explain"}) == "explain"
    # 처음부터 값 질문이면 brief 다(과하게 붙지 않는다)
    assert _carry_depth({}, {"answer_depth": "brief"}) == "brief"
    assert _carry_depth({}, {}) == "brief"


def test_the_original_request_is_pinned_for_lookup_flows_too():
    """원 요청 고정은 **생성 갈래에만** 걸려 있었다 — 조회도 답의 성격은 원 요청이 정한다.
    실측(DATA11): "…데이터의 히스토리" 로 시작해 표기 확인 질문에 답하자, 그 턴의 발화가
    "fdc.… 말한거야" 뿐이라 request_text 가 거기로 폴백되며 '히스토리'가 사라졌고,
    연표 대신 현재 값 표가 나왔다."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.state import Intent

    def _m(t):
        return {"messages": [HumanMessage(content=t)]}
    st = _m("fdc_flat_summary_ic 데이터의 히스토리")
    got = RequestArchitect().apply(st, {"intent": Intent.ASK, "keywords": ["fdc_flat_summary_ic"]})
    assert got.get("request_text") == "fdc_flat_summary_ic 데이터의 히스토리", got
    # 이미 고정돼 있으면 후속 턴이 덮지 않는다
    st2 = {**_m("fdc.fdc_trace_summary_ic 말한거야"),
           "request_text": "fdc_flat_summary_ic 데이터의 히스토리"}
    got2 = RequestArchitect().apply(st2, {"intent": Intent.ASK, "keywords": []})
    assert "request_text" not in got2, got2


def test_my_own_work_request_is_pinned_to_my_day_by_code():
    """"내가 할 만한 일" 은 **내 일감**이지 진척 집계가 아니다.

    실측(REC9): 같은 문장이 실행마다 my_day / progress 로 갈렸다. 두 갈래는 지나는 노드와
    재료가 통째로 달라(내 일감 사전취합 vs 진척률) 갈리는 순간 답의 성격이 바뀐다.
    낱말로 하는 판정은 흔들릴 이유가 없으므로 코드가 확정한다.
    """
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.state import Intent

    def _intent(text, model_said):
        st = {"messages": [HumanMessage(content=text)]}
        return RequestArchitect().apply(st, {"intent": model_said, "keywords": []})["intent"]

    assert _intent("지금 내가 할 만한 일 추천해줘", Intent.PROGRESS) == Intent.MY_DAY
    assert _intent("나 오늘 뭐부터 하지?", Intent.ASK) == Intent.MY_DAY
    # ★ '추천' 하나로는 판정하지 않는다 — 생성 요청을 빼앗으면 안 된다
    assert _intent("내가 만들 티켓 추천해줘", Intent.PLAN_WORK) == Intent.PLAN_WORK
    # 남의 진척을 묻는 것은 그대로 progress
    assert _intent("ETL 모듈 진척 어때?", Intent.PROGRESS) == Intent.PROGRESS


def test_structured_agent_survives_a_server_without_function_calling(monkeypatch):
    """구조화 출력이 안 되는 서버에서도 역할이 죽지 않는다.

    실사용 사고: 자체 LLM 으로 붙이면 "Invalid json output" 이 반복됐다. langchain 의
    `with_structured_output` 은 기본적으로 OpenAI 의 **함수 호출**로 스키마를 강제하는데,
    사내 게이트웨이나 자체 서빙(vLLM·TGI 등)은 그 기능이 없거나 반쪽일 수 있다.

    우리가 원하는 건 '함수 호출'이 아니라 **JSON 한 덩이**다. 한 번 더 묻되 스키마를 말로
    주고 정확한 JSON object를 받는다. code fence나 prefix를 잘라 성공 처리하지 않는다.
    """
    from langchain_core.messages import AIMessage

    from app.agent.workflow.agents.base import StructuredAgent

    class _Broken:                      # with_structured_output 이 죽는 서버 흉내
        def with_structured_output(self, *a, **k):
            class _S:
                def invoke(self, *a, **k):
                    raise ValueError("Invalid json output: 여기 결과입니다 ...")
            return _S()

        def invoke(self, *a, **k):      # prompt JSON 계약은 정확한 객체 한 개로 답한다
            return AIMessage(content='{"picked": "ok"}')

    class _A(StructuredAgent):
        name = "tester"

        def system(self, state):
            return "sys"

        def task(self, state):
            return "task"

        def schema(self):
            return {"type": "object", "properties": {"picked": {"type": "string"}}}

        def apply(self, state, out):
            return {"got": out}

    a = _A()
    monkeypatch.setattr(a, "llm", lambda **kw: _Broken())
    r = a.node()({})
    assert r.get("got") == {"picked": "ok"}, r
    assert "error" not in r, "폴백이 있는데도 실패로 떨어졌다"
