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
    assert G.route_after_planner({"intent": Intent.CHITCHAT}) == "respond"


def test_everything_else_investigates_first():
    """조사를 건너뛰고 티켓을 만들어 주는 어시스턴트는 중복 티켓 생성기다."""
    for intent in (Intent.ASK, Intent.PLAN_WORK, Intent.MODIFY):
        assert G.route_after_planner({"intent": intent}) == "investigate"


def test_a_plain_question_stops_after_investigation():
    assert G.route_after_historian({"intent": Intent.ASK}) == "respond"
    assert G.route_after_historian({"intent": Intent.PLAN_WORK}) == "refine"


def test_questions_go_back_to_the_user_instead_of_drafting():
    assert G.route_after_refiner({"questions": ["범위가 어디까지인가요?"],
                                  "draft": {"items": [{"summary": "x"}]}}) == "respond"


def test_a_draft_moves_on_to_assignment():
    assert G.route_after_refiner({"questions": [], "draft": {"items": [{"summary": "x"}]}}) == "assign"


def test_an_empty_draft_does_not_pretend_to_have_one():
    assert G.route_after_refiner({"questions": [], "draft": {"items": []}}) == "respond"


def test_review_failure_sends_it_back_to_be_rewritten():
    assert G.route_after_reviewer({"review": {"ok": False}, "revisions": 1}) == "revise"


def test_rewrite_loop_is_bounded_but_humans_still_get_to_judge():
    """상한이 없으면 두 모델이 서로 만족하지 못해 무한히 돈다. 소진 뒤의 갈래:

    기계 오류가 남았으면 respond(만들어 봤자 Jira 가 거부한다). **LLM 의견만** 남았으면
    propose — 검열자가 만족할 때까지 승인을 막으면 사람이 판단할 기회가 사라진다
    (실제로 멀쩡한 근거를 '불충분'이라 두 번 반려해 승인 카드가 아예 안 떴다).
    """
    exhausted = {"revisions": MAX_REVISIONS}
    assert G.route_after_reviewer({**exhausted,
                                   "review": {"ok": False,
                                              "errors": [{"message": "없는 부모"}]}}) == "respond"
    assert G.route_after_reviewer({**exhausted,
                                   "review": {"ok": False, "errors": [],
                                              "problems": [{"message": "의견"}]}}) == "propose"


def test_passing_review_goes_to_approval_not_straight_to_execution():
    assert G.route_after_reviewer({"review": {"ok": True}}) == "propose"


def test_responder_waits_for_approval_when_a_token_is_pending():
    assert G.route_after_responder({"approval_token": "t"}) == "execute"


def test_responder_ends_after_execution_instead_of_looping():
    assert G.route_after_responder({"approval_token": "t", "result": {"created": []}}) == "end"


def test_responder_ends_when_there_is_nothing_to_approve():
    assert G.route_after_responder({}) == "end"


# ── 조립 ───────────────────────────────────────────────────────────
def test_graph_has_all_six_roles():
    nodes = set(G.build().get_graph().nodes)
    for n in (Node.PLANNER, Node.HISTORIAN, Node.REFINER, Node.ASSIGNER,
              Node.REVIEWER, Node.OPERATOR, Node.RESPONDER):
        assert n in nodes


def test_tool_using_roles_really_are_subgraphs():
    """서브그래프가 아니면 stream(subgraphs=True) 가 '도구 부르는 중'을 못 보여 준다.

    Operator 는 여기 없다 — modify 를 결정적으로 실행하려고 node() 를 한 겹 더 감싸면서
    xray 가 서브그래프를 못 본다. create 갈래의 ReAct 는 별도 테스트가 지킨다.
    """
    nodes = set(G.build().get_graph(xray=1).nodes)
    for role in (Node.HISTORIAN, Node.REFINER, Node.ASSIGNER):
        assert f"{role}:think" in nodes and f"{role}:act" in nodes


def test_operator_keeps_react_for_creation():
    """Operator 의 create 갈래는 여전히 ReAct 서브그래프를 탄다(부분 실패 판단이 실제로 있다)."""
    from app.agent.workflow.agents.operator import Operator
    sub = Operator().build()
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
      · Refiner — fake 는 배열을 비워 두므로 초안이 아예 서지 않는다.
      · Reviewer 의 **LLM 의견** — fake 는 boolean 을 해시로 정해 매번 갈린다.
        단 **기계 판정(`validate_bulk`)은 진짜로 돌린다.** 규칙에 어긋난 초안이 통과하면
        이 테스트가 무의미해지기 때문이다.
      · Operator 의 **문장** — 시험하려는 건 말이 아니라 토큰이 도구까지 닿는가다.

    진짜로 도는 것: Assigner · 기계 검증 · propose(토큰 발급) · interrupt · 재개 ·
    승인 토큰 대조 · 실제 티켓 생성.
    """
    from app.agent.workflow.agents.operator import Operator
    from app.agent.workflow.agents.planner import Planner
    from app.agent.workflow.agents.refiner import Refiner, as_bulk_items
    from app.agent.workflow.agents.reviewer import Reviewer, _machine_check
    from app.agent.workflow.state import Intent

    # Planner 도 고정한다 — fake 의 enum 선택은 해시라 **의도 갈래가 늘어날 때마다** 어디로
    # 떨어질지 바뀐다(실제로 pmo 갈래가 생기자 이 시나리오가 그쪽으로 새서 깨졌다).
    # 이 테스트의 관심사는 분류가 아니라 승인 이음매다.
    monkeypatch.setattr(Planner, "node", lambda self: (lambda state: {
        "intent": Intent.PLAN_WORK, "keywords": ["CDC"], "sufficient": True}))

    monkeypatch.setattr(Refiner, "node", lambda self: (lambda state: {
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

    monkeypatch.setattr(Reviewer, "node", rv_node)

    # Operator 의 LLM 만 건너뛴다 — 시험하려는 것은 문장이 아니라 **토큰이 도구까지 닿는가**다.
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

    monkeypatch.setattr(Operator, "node", op_node)
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
