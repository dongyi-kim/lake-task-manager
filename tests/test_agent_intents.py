"""의도별 라우팅 · PMO 도구 · 권한.

전부 fake 로 돈다. 문장 품질은 실 LLM 검증(1회)에서 보고, 여기서는 **구조**를 지킨다:
어느 의도가 어느 길로 가는지, PMO 도구가 옳은 숫자를 주는지, 매니저 게이트가 실제로 막는지.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langgraph", reason="requirements-agent.txt 미설치")

from app.agent.workflow import graph as G                     # noqa: E402
from app.agent.workflow.state import Intent                   # noqa: E402


@pytest.fixture(autouse=True)
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    import app.infra.settings as S
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    G.reset()
    yield
    G.reset()


# ── 라우팅 ─────────────────────────────────────────────────────────
def test_direct_answer_intents_skip_the_historian():
    """my_day·progress·activity 는 과거 발굴이 아니라 지금 상태의 집계다."""
    for i in (Intent.MY_DAY, Intent.PROGRESS, Intent.ACTIVITY):
        assert G.route_after_planner({"intent": i}) == "pmo"


def test_bug_reports_still_go_through_investigation():
    """버그도 조사를 지난다 — 같은 증상의 Bug 가 이미 열려 있으면 새로 만들면 안 된다.

    ★ 버그 신고는 **갈래가 아니다**(`report_bug` enum 제거, §7 16-b) — 만드는 것이
      Task 이고 type 만 Bug 다. 여기서 지킬 것은 그것이 `plan_work` 와 **같은 길**을
      지난다는 것, 그리고 갈래가 되살아나지 않는다는 것이다.
    """
    from langchain_core.messages import HumanMessage
    bug = {"intent": Intent.PLAN_WORK,
           "messages": [HumanMessage(content="리니지 뷰어에서 2홉 이상 펼치면 화면이 빈다")]}
    assert not hasattr(Intent, "REPORT_BUG"), "갈래로 되돌리지 마라 — 산출물 유형이다"
    assert G.route_after_planner(bug) == "investigate"
    assert G.route_after_historian(bug) == "refine"
    # ★ sufficient 가 안 붙어도 조사로 간다 — 버그는 "막연한 신규 개발"이 아니다.
    #   같은 문장에서 낱말이 빠지면(=버그가 아니면) 해석 확인이 먼저다.
    vague = dict(bug, messages=[HumanMessage(content="리니지 뷰어를 개선하고 싶다")])
    assert G.route_after_planner(vague) == "refine"


def test_pmo_node_exists_and_flows_to_responder():
    g = G.build().get_graph()
    assert "pmo" in g.nodes


# ── PMO 도구: 숫자가 실물과 같은가 ──────────────────────────────────
def test_progress_matches_the_dashboard_numbers():
    """에이전트의 진척률과 WBS 대시보드의 진척률이 갈라지면 어느 쪽도 못 믿게 된다."""
    from app.agent import tools as T
    from app.agent.tools import _ctx
    from app.domain import rollup
    from app.infra.settings import load_plan
    plan = load_plan()
    built = rollup.build(plan, _ctx.client().epic_progress_map(plan))
    expected = (built.get("rollup") or {}).get("pmo", {}).get("progressPct")

    r = T.BY_NAME["get_progress"].invoke({"target": ""})
    assert r["overallPct"] == expected
    assert r["modules"], "모듈 목록이 비면 진척률을 설명할 수 없다"


def test_progress_for_one_epic_includes_the_denominator_note():
    """"진척률이 왜 이런가"의 답은 분모 규칙에 있다 — 숫자만 주면 안 된다.

    Epic 키는 plan["epics"](이름 오버라이드 맵 — 비어 있을 수 있다)가 아니라
    **wbs 항목이 실제로 참조하는 티켓**에서 얻는다.
    """
    from app.agent import tools as T
    from app.infra.settings import load_plan
    plan = load_plan()
    epic = next(e.get("key") for w in plan.get("wbs") or []
                for e in (w.get("epics") or []) if e.get("key"))
    r = T.BY_NAME["get_progress"].invoke({"target": epic})
    assert r.get("donePct") is not None, r
    assert "빠진다" in (r.get("note") or "")


def test_progress_rejects_an_unlinked_epic_with_a_reason():
    from app.agent import tools as T
    r = T.BY_NAME["get_progress"].invoke({"target": "DL-99999"})
    assert "wbs_config" in (r.get("error") or "")


def test_my_workload_gives_judgement_material_not_judgement():
    """도구는 dueInDays·overdue·staleDays 같은 **숫자**를 준다 — 고르는 건 모델의 일이다."""
    from app.agent import tools as T
    from app.infra.settings import load_people
    uid = next(u for ids in load_people().values() for u in ids)
    r = T.BY_NAME["get_my_workload"].invoke({"user_id": uid})
    assert r["count"] >= 0
    for t in r["tickets"][:5]:
        assert "overdue" in t and "staleDays" in t


def test_stale_tickets_are_actually_stale():
    from app.agent import tools as T
    r = T.BY_NAME["find_stale_tickets"].invoke({"module": "", "days": 14})
    assert r["count"] >= 1, "12개월 world 에 2주 정체 티켓이 하나도 없을 리 없다"
    assert all((t.get("staleDays") or 0) >= 14 for t in r["tickets"])


# ── 권한: 프롬프트가 아니라 도구가 막는다 ────────────────────────────
def _as_non_manager(monkeypatch):
    """세션 사용자를 '매니저 아님'으로 만든다. managers 목록이 비면 전원 매니저라
    반드시 **다른 사람**을 매니저로 지정해 둔다."""
    import app.agent.tools.pmo_tools as P
    monkeypatch.setattr(P, "_is_manager", lambda: False)
    monkeypatch.setattr(P, "_me", lambda: "skcc.x9999")


def test_activity_of_others_is_manager_only(monkeypatch):
    _as_non_manager(monkeypatch)
    from app.agent import tools as T
    r = T.BY_NAME["get_user_activity"].invoke({"user_id": "skcc.x1042", "days": 3})
    assert r.get("denied") is True
    assert "매니저" in r["error"]


def test_others_workload_is_manager_only(monkeypatch):
    _as_non_manager(monkeypatch)
    from app.agent import tools as T
    r = T.BY_NAME["get_my_workload"].invoke({"user_id": "skcc.x1042"})
    assert r.get("denied") is True


def test_my_own_workload_needs_no_privilege(monkeypatch):
    import app.agent.tools.pmo_tools as P
    monkeypatch.setattr(P, "_is_manager", lambda: False)
    from app.agent import tools as T
    r = T.BY_NAME["get_my_workload"].invoke({"user_id": ""})
    assert "denied" not in r


def test_manager_can_see_others_activity():
    from app.agent import tools as T
    from app.infra.settings import load_people
    uid = next(u for ids in load_people().values() for u in ids)
    r = T.BY_NAME["get_user_activity"].invoke({"user_id": uid, "days": 7})
    assert r.get("denied") is None
    assert "touched" in r


# ── 새 쓰기 도구도 승인 게이트를 지난다 ─────────────────────────────
def test_new_write_tools_demand_tokens_too():
    from app.agent import tools as T
    for name in ("link_tickets", "attach_document"):
        assert "approval_token" in T.BY_NAME[name].args, f"{name} 에 승인 인자가 없다"


def test_link_without_approval_is_refused():
    from app.agent import approval
    from app.agent import tools as T
    approval.clear()
    r = T.BY_NAME["link_tickets"].invoke(
        {"key": "DL-1", "other_key": "DL-2", "relation": "Relates", "approval_token": "없음"})
    assert r["ok"] is False and r.get("needsApproval") is True


def test_bug_body_rules_follow_the_request_not_the_intent():
    """버그 초안 규율은 **요청의 내용**으로 고른다 — 의도가 미끄러져도 바뀌면 안 된다.

    ★ 이 테스트는 원래 "intent 로 분기한다"를 단언했다. 그런데 `report_bug` 는 `plan_work`
    와 지나는 노드도 도구도 같고 다른 것은 이 goal 하나뿐이었다(사용자 지적: "결국 Task
    생성 아니야? type 이 Bug 일 뿐이지"). 갈래로 두면 **분류가 틀릴 때 본문 템플릿이
    통째로 바뀐다** — 재현·기대·실제가 배경·범위·DoD 로 뒤바뀐다. 그래서 판정을 요청의
    낱말로 옮겼고(갈래는 §7 16-b 에서 제거), 이 테스트도 그 규율을 잰다.
    """
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.refiner import Refiner
    st = {"messages": [HumanMessage(content="배치가 실패한다")], "intent": Intent.PLAN_WORK}
    assert "재현 경로" in Refiner().task(st)
    # 의도가 modify 로 미끄러져도 **버그 이야기면** 규율이 유지된다
    assert "재현 경로" in Refiner().task(dict(st, intent=Intent.MODIFY))
    # 버그 이야기가 아니면 평소 규율 — 아무 요청에나 버그 템플릿을 씌우면 안 된다
    plain = {"messages": [HumanMessage(content="메타데이터 등록 작업이 필요해")],
             "intent": Intent.PLAN_WORK}
    assert "재현 경로" not in Refiner().task(plain)


def test_planner_schema_covers_all_new_intents():
    from app.agent.workflow.agents.planner import SCHEMA
    enum = SCHEMA["properties"]["intent"]["enum"]
    for i in (Intent.PLAN_WORK, Intent.MY_DAY, Intent.PROGRESS, Intent.ACTIVITY):
        assert i in enum


# ── modify 실행 경로 — 변경 계획 → 승인 → update_ticket ────────────
def test_change_plan_routes_to_approval_not_assignment():
    """변경 계획은 담당자 추천·생성 검증을 지나지 않는다 — 해당이 없는 단계다."""
    assert G.route_after_refiner({"questions": [],
                                  "change_plan": {"key": "DL-1", "changes": {"duedate": "2026-09-01"}},
                                  "draft": {}}) == "propose"


def test_propose_stages_an_update_token_matching_the_tool_payload():
    """토큰 지문은 update_ticket 도구가 만들 payload 와 **같은 모양**이어야 승인이 통한다."""
    from app.agent import approval
    approval.clear()
    plan = {"key": "DL-1", "changes": {"duedate": "2026-09-01"}}
    tok = G._propose({"thread_id": "t1", "change_plan": plan})["approval_token"]
    rec = approval.peek(tok)
    assert rec["action"] == "update_ticket"
    assert rec["fp"] == approval.fingerprint({"key": "DL-1", "changes": {"duedate": "2026-09-01"}})


def test_modify_end_to_end_updates_the_real_ticket(monkeypatch):
    """modify 이음매 전체 — Planner/Refiner 만 고정. **Operator 는 실물이다**(변경 실행이
    결정적이라 LLM 없이 돈다). interrupt·이중 토큰·update·코멘트까지 진짜로 굴린다."""
    from app.agent.workflow import session
    from app.agent.workflow.agents.planner import Planner
    from app.agent.workflow.agents.refiner import Refiner
    from app.agent.tools import _ctx
    import app.agent.tools as T

    key = _ctx.client().search_issues("ORDER BY created DESC", max_results=5)[0]["key"]
    plan = {"key": key, "changes": {"duedate": "2026-11-11"},
            "comment": "의존 작업 지연으로 일정 조정", "why": "일정 조정"}

    monkeypatch.setattr(Planner, "node", lambda self: (lambda st: {
        "intent": Intent.MODIFY, "keywords": [key], "mentioned_keys": [key], "sufficient": True}))
    monkeypatch.setattr(Refiner, "node", lambda self: (lambda st: {
        "questions": [], "change_plan": dict(plan), "turns": 1, "draft": {}}))
    G.reset()

    out = session.ask(f"{key} 마감을 11월 11일로 미루고 사유도 코멘트로 남겨줘")
    assert out.get("pending"), out.get("reply")
    assert out["pending"]["action"] == "update_ticket"
    assert out["pending"]["changes"] == {"duedate": "2026-11-11"}
    assert "의존 작업" in out["pending"]["comment"]

    done = session.resume(out["thread_id"], out["pending"]["token"])
    r = done.get("result") or {}
    assert r.get("updated"), done
    assert not r.get("note"), f"코멘트가 실패했다: {r.get('note')}"
    got = T.BY_NAME["get_ticket"].invoke({"key": key, "comment_limit": 20})
    assert got["duedate"] == "2026-11-11", "승인했는데 실물이 안 바뀌었다"
    # limit 을 넉넉히 준다 — jira820 은 orderBy=-created 를 무시하고 오래된 순으로 주므로
    # 방금 단 코멘트는 목록의 **끝**에 있다.
    assert any("의존 작업" in (c.get("body") or "") for c in got.get("comments") or []),         "코멘트가 실물에 안 남았다"
    G.reset()


def test_pmo_vit_label_is_stripped_unless_user_asked(monkeypatch):
    """PMO_VIT 는 경영진 현안 전용·최상위 하나에만 — 모델이 신규 티켓 셋에 전부 붙였다(실측).
    사용자가 입에 올리지 않았으면 기계적으로 뗀다."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.refiner import Refiner
    r = Refiner()
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "s", "type": "Task", "labels": ["PMO_VIT", "quality"]}]}
    st = {"messages": [HumanMessage(content="품질 규칙 기능 만들어줘")], "trace": []}
    got = r.apply(st, dict(out, items=[dict(out["items"][0])]))
    assert got["draft"]["items"][0]["labels"] == ["quality"]
    st2 = {"messages": [HumanMessage(content="이거 PMO_VIT 현안으로 올려줘")], "trace": []}
    got2 = r.apply(st2, dict(out, items=[dict(out["items"][0], labels=["PMO_VIT"])]))
    assert "PMO_VIT" in got2["draft"]["items"][0]["labels"]


def test_references_are_merged_into_the_참고_section():
    """조사 근거를 티켓에 박제하되 — 섹션은 '참고' **하나**다. 별도 References h3 를
    덧붙이던 방식은 모델이 쓴 <h3>참고</h3> 와 무조건 중복됐다(실측: 3벌·한영 혼재)."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.refiner import Refiner
    st = {"messages": [HumanMessage(content="CDC 도입")], "trace": [],
          "evidence": [{"key": "DL-118", "why": "소스 DB 부하로 중단됐던 선행 검토"}],
          "related_docs": [{"title": "CDC 설계 문서", "url": "https://conf/x"}]}
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "s", "type": "Task", "description": "<h3>배경</h3><p>x</p>"}]}
    got = Refiner().apply(st, out)
    d = got["draft"]["items"][0]["description"]
    assert "References" not in d and d.count("<h3>참고</h3>") == 1
    assert "DL-118" in d and "https://conf/x" in d
    # 모델이 이미 '참고'를 적었으면 그 ul 에 **병합**되고, 이미 있는 키는 다시 붙지 않는다
    out2 = {"questions": [], "mode": "task", "rationale": "",
            "items": [{"summary": "s", "type": "Task",
                       "description": "<h3>참고</h3><ul><li>DL-118 — 이미 적음</li></ul>"}]}
    d2 = Refiner().apply(st, out2)["draft"]["items"][0]["description"]
    assert d2.count("<h3>참고</h3>") == 1 and d2.count("DL-118") == 1
    assert "https://conf/x" in d2      # 없던 문서는 병합된다


def test_comment_only_change_plan_goes_through_approval(monkeypatch):
    """"이 내용 DL-x 에 댓글로 남겨줘" — 변경 필드 없이 댓글만도 승인→실행이 돼야 한다."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow import session
    from app.agent.workflow.agents.planner import Planner
    from app.agent.workflow.agents.refiner import Refiner
    from app.agent.tools import _ctx
    import app.agent.tools as T

    key = _ctx.client().search_issues(
        "statusCategory = indeterminate ORDER BY updated DESC", max_results=3)[0]["key"]
    plan = {"key": key, "changes": {}, "comment": "회의 결정: 다음 릴리스로 미룸", "why": ""}
    monkeypatch.setattr(Planner, "node", lambda self: (lambda st: {
        "intent": Intent.MODIFY, "keywords": [key], "mentioned_keys": [key], "sufficient": True}))
    monkeypatch.setattr(Refiner, "node", lambda self: (lambda st: {
        "questions": [], "change_plan": dict(plan), "turns": 1, "draft": {}}))
    G.reset()

    out = session.ask(f"{key} 에 '회의 결정: 다음 릴리스로 미룸' 이라고 댓글 남겨줘")
    assert out.get("pending"), out.get("reply")
    assert out["pending"]["comment"] and not out["pending"]["changes"]

    done = session.resume(out["thread_id"], out["pending"]["token"])
    assert (done.get("result") or {}).get("updated"), done
    got = T.BY_NAME["get_ticket"].invoke({"key": key, "comment_limit": 20})
    assert any("다음 릴리스로 미룸" in (c.get("body") or "") for c in got.get("comments") or [])
    G.reset()


def test_description_change_survives_the_token_fingerprint():
    """본문 수정 — propose 가 만드는 payload 와 도구가 만드는 payload 의 지문이 같아야 한다."""
    from app.agent import approval
    import app.agent.tools as T
    from app.agent.tools import _ctx
    approval.clear()
    key = _ctx.client().search_issues("ORDER BY updated DESC", max_results=1)[0]["key"]
    html = "<h3>배경</h3><p>보강</p><h3>완료 조건 (DoD)</h3><ul><li>검증</li></ul>"
    plan = {"key": key, "changes": {"description": html}}
    tok = G._propose({"thread_id": "t1", "change_plan": plan})["approval_token"]
    approval.approve(tok, "t1")
    r = T.BY_NAME["update_ticket"].invoke({"key": key, "description": html, "approval_token": tok})
    assert r.get("ok"), r
    assert "description" in (r.get("updated") or [])


def test_reference_index_duplicates_are_merged():
    """같은 출처가 두 번호를 받으면 코드가 접는다([1]·[3] 같은 티켓 — 실측).
    티켓 참조와 그 티켓의 코멘트 참조는 다른 출처라 남는다."""
    from app.agent.workflow.agents.responder import _dedupe_refs
    t = ("주기 [1]. 잡 [3]. 담당 [4].\n\n**참조**\n"
         "- [1] DL-9044 — 주기 변경 근거\n"
         "- [3] DL-9044 — 같은 티켓 다른 설명\n"
         "- [4] DL-9044 코멘트 (skcc.x1103, 2026-08-06) — 담당\n")
    out = _dedupe_refs(t)
    assert "잡 [1]" in out and out.count("[1] DL-9044") >= 1
    assert "\n[2] DL-9044 코멘트" in out
    assert "- [" not in out, "불릿과 [n] 이중 표식 금지(실측 지적)"
    assert _dedupe_refs("참조 없는 답") == "참조 없는 답"
    # 문서 참조의 "제목 (URL)" 중복 표기는 URL 만 남긴다 — 뱃지가 제목을 보여 준다
    t2 = ("값 [1].\n\n**참조**\n"
          "- [1] [데이터카탈로그] 특성 분석 (http://x/pages/1/문서) — 스키마 근거\n")
    o2 = _dedupe_refs(t2)
    assert "[1] http://x/pages/1/문서 — 스키마 근거" in o2, o2
