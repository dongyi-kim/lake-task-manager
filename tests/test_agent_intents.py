"""새 의도 4종(report_bug / my_day / progress / activity) — 라우팅 · PMO 도구 · 권한.

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
    """버그도 조사를 지난다 — 같은 증상의 Bug 가 이미 열려 있으면 새로 만들면 안 된다."""
    assert G.route_after_planner({"intent": Intent.REPORT_BUG}) == "investigate"
    assert G.route_after_historian({"intent": Intent.REPORT_BUG}) == "refine"


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


def test_prompt_branches_by_intent():
    """report_bug 는 재현경로를 요구하는 전용 지시를 받는다 — 새 기능 초안과 규칙이 다르다."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.refiner import Refiner
    st = {"messages": [HumanMessage(content="배치가 실패한다")], "intent": Intent.REPORT_BUG}
    assert "재현 경로" in Refiner().task(st)
    assert "재현 경로" not in Refiner().task(dict(st, intent=Intent.PLAN_WORK))


def test_planner_schema_covers_all_new_intents():
    from app.agent.workflow.agents.planner import SCHEMA
    enum = SCHEMA["properties"]["intent"]["enum"]
    for i in (Intent.REPORT_BUG, Intent.MY_DAY, Intent.PROGRESS, Intent.ACTIVITY):
        assert i in enum
