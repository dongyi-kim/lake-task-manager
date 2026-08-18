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
from app.agent.workflow.state import Intent, Node             # noqa: E402


@pytest.fixture(autouse=True)
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    import app.infra.settings as S
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    G.reset()
    yield
    G.reset()


def test_interview_answer_plus_independent_summary_does_not_freeze_old_plan():
    from app.agent.workflow import session

    prior = {
        "intent": "plan_work",
        "request_text": "Writer 검증 Task를 만들어줘",
        "request_plan": {"tasks": [{
            "id": "writer", "kind": "ticket", "write_intent": True,
            "instruction": "Writer 검증 Task 생성",
        }]},
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "Writer 검증 Task를 만들어줘",
            "intent": "plan_work", "action": "create",
            "target_keys": [], "outcome_ids": ["writer"], "decisions": [],
        },
        "questions": [{
            "field": "assignee", "question": "담당자는 누구인가요?",
            "kind": "text", "required_input": True,
        }],
    }
    utterance = "담당자는 skcc.x1103이야. 그리고 최신 운영 회의 내용을 요약해줘."

    assert session._is_interview_continuation(utterance, prior) is False
    patch = session._turn_start_patch(utterance, prior)
    assert patch["request_text"] == utterance
    assert patch["request_plan"] == {}
    assert patch["continuation_contract"] == {}


@pytest.mark.parametrize("utterance", [
    "담당자는 skcc.x1103이야. 그리고 현재 진행 상황은?",
    "담당자는 skcc.x1103이야. 무엇이 남았어?",
    "담당자는 skcc.x1103이야. 지금 상태가 어때?",
    "담당자는 skcc.x1103이야. 그리고 진행률은?",
    "담당자는 skcc.x1103이야. 그리고 진척도는?",
    "담당자는 skcc.x1103이야. 그리고 완료율은?",
])
def test_interview_answer_plus_independent_read_does_not_drop_the_read(utterance):
    from app.agent.workflow import session

    prior = {
        "intent": "plan_work", "request_text": "Writer 검증 Task 생성",
        "request_plan": {"tasks": [{
            "id": "writer", "kind": "ticket", "write_intent": True,
            "instruction": "Writer 검증 Task 생성",
        }]},
        "continuation_contract": {
            "version": "continuation.v1", "root_request": "Writer 검증 Task 생성",
            "intent": "plan_work", "action": "create", "target_keys": [],
            "outcome_ids": ["writer"], "decisions": [],
        },
        "questions": [{"field": "assignee", "question": "담당자는?",
                       "kind": "text", "required_input": True}],
    }

    patch = session._turn_start_patch(utterance, prior)

    assert patch["turn_continuation"] is False
    assert patch["request_text"] == utterance
    assert patch["request_plan"] == {}


def test_typed_target_answer_may_replace_the_prior_jira_key():
    from app.agent.workflow import session

    prior = {
        "intent": "plan_work", "request_text": "DL-9000 관련 검증 Task 생성",
        "mentioned_keys": ["DL-9000"],
        "request_plan": {"tasks": [{
            "id": "verify", "kind": "ticket", "write_intent": True,
            "instruction": "DL-9000 관련 검증 Task 생성",
        }]},
        "continuation_contract": {
            "version": "continuation.v1", "root_request": "DL-9000 관련 검증 Task 생성",
            "intent": "plan_work", "action": "create", "target_keys": ["DL-9000"],
            "outcome_ids": ["verify"], "decisions": [],
        },
        "questions": [{"field": "target", "question": "실제 대상 티켓은?",
                       "kind": "text", "required_input": True}],
    }

    patch = session._turn_start_patch("DL-9201로 할게", prior)

    assert patch["turn_continuation"] is True
    assert patch["request_plan"] == prior["request_plan"]
    assert patch["continuation_contract"]["target_keys"] == ["DL-9201"]
    assert patch["continuation_contract"]["decisions"][-1]["value"] == "DL-9201"


# ── 라우팅 ─────────────────────────────────────────────────────────
def test_direct_answer_intents_skip_the_historian():
    """my_day·progress·activity 는 과거 발굴이 아니라 지금 상태의 집계다."""
    for i in (Intent.MY_DAY, Intent.PROGRESS, Intent.ACTIVITY):
        assert G.route_after_request_architect({"intent": i}) == Node.PORTFOLIO_ANALYST


def test_incomplete_assignee_question_uses_deterministic_query_runner():
    from langchain_core.messages import HumanMessage
    state = {"intent": Intent.ACTIVITY, "messages": [HumanMessage(
        content="보안 팔수 교육 수강 Task들 누가누가 미완료했나 궁금해")]}
    assert G.route_after_request_architect(state) == Node.QUERY_RUNNER

    from app.agent.workflow.agents.request_architect import RequestArchitect
    classified = RequestArchitect().apply(state, {
        "intent": Intent.ACTIVITY, "keywords": ["보안 필수교육 수강"],
        "mentioned_keys": [], "sufficient": True, "answer_depth": "brief",
        "playbook": "workload",
    })
    assert classified["intent"] == Intent.ASK
    assert classified["playbook"] == "find_tickets"
    assert classified["request_plan"]["tasks"][0]["id"] == "incomplete-assignees"


def test_research_report_request_cannot_drift_into_ticket_creation():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    state = {"messages": [HumanMessage(content=(
        "우리 프로젝트의 Iceberg Puffin NDV 적용 가능성을 내부 작업 이력과 "
        "외부 공식 자료를 함께 조사해줘"))]}
    got = RequestArchitect().apply(state, {
        "intent": Intent.PLAN_WORK, "keywords": ["Iceberg", "Puffin", "NDV"],
        "tasks": [{"id": "1", "kind": "research", "instruction": "내외부 조사",
                   "depends_on": [], "write_intent": False, "completion_criteria": ["조사 완료"]}],
    })
    assert got["intent"] == Intent.ASK
    assert G.route_after_request_architect(got) == "investigate"


def test_new_build_researches_before_interviewing_an_optional_ticket_shape():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    state = {"messages": [HumanMessage(content=(
        "기존 ETL 파이프라인에 Iceberg Puffin NDV 생성 기능을 추가 구현하고 싶어"))]}
    got = RequestArchitect().apply(state, {
        "intent": Intent.PLAN_WORK, "keywords": ["Iceberg", "Puffin", "NDV"],
        "sufficient": True,
    })
    assert not got.get("questions")
    assert G.route_after_request_architect(got) == "investigate"


def test_insufficient_new_work_researches_before_any_blocking_interview():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    state = {"messages": [HumanMessage(content=(
        "기존 ETL 파이프라인에 Iceberg Puffin NDV 생성 기능을 추가 구현하고 싶어"))]}
    got = RequestArchitect().apply(state, {
        "intent": Intent.PLAN_WORK, "keywords": ["Iceberg", "Puffin", "NDV"],
        "sufficient": False,
        "blocking_questions": ["대상 테이블은 무엇인가?"],
    })
    assert not got.get("questions")
    assert G.route_after_request_architect(got) == "investigate"


def test_delegated_or_explicit_build_shape_does_not_add_a_preference_question():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    for text in (
        "기존 ETL 파이프라인을 단계별 Sub-Task로 구현해줘",
        "기존 ETL 파이프라인에 새 단계를 구현해줘. 알아서",
    ):
        got = RequestArchitect().apply({"messages": [HumanMessage(content=text)]}, {
            "intent": Intent.PLAN_WORK, "keywords": ["ETL"], "sufficient": True,
        })
        assert not got.get("questions"), got
        assert G.route_after_request_architect(got) == "investigate"


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
    assert G.route_after_request_architect(bug) == "investigate"
    assert G.route_after_research_analyst(bug) == "refine"
    # sufficient 여부와 무관하게 먼저 조사한다. 내부 이력으로 해소할 수 있는 모호함을
    # 사용자에게 되묻지 않고, 조사 후에도 남은 사용자 소유 blocker만 인터뷰한다.
    vague = dict(bug, messages=[HumanMessage(content="리니지 뷰어를 개선하고 싶다")])
    assert G.route_after_request_architect(vague) == "investigate"


def test_request_architect_pins_lexically_decidable_bug_progress_and_depth_boundaries():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    def classify(text, model_intent=Intent.PLAN_WORK, depth="brief"):
        return RequestArchitect().apply({"messages": [HumanMessage(content=text)]}, {
            "intent": model_intent, "keywords": [], "answer_depth": depth,
            "sufficient": True,
        })

    bug = classify("적재 배치가 어젯밤부터 계속 실패한다")
    assert bug["intent"] == Intent.PLAN_WORK
    assert bug["playbook"] == "bug_report"
    stale = classify("진행중인 티켓 중 2일 이상 업데이트 없는 것들 있니?")
    assert stale["intent"] == Intent.PROGRESS
    assert stale["playbook"] == "find_tickets"
    assert classify("적재 지연이 왜 났고 어떻게 해결했어?", Intent.ASK)["answer_depth"] == "explain"
    assert classify("CDC가 뭐고 우리는 어떻게 쓰고 있어?", Intent.ASK)["answer_depth"] == "explain"


def test_request_architect_runtime_examples_use_generic_intent_boundaries():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    task = RequestArchitect().task({
        "messages": [HumanMessage(content="증분 수집 방식의 현재 상태를 설명해줘")],
    })

    assert "실시간 수집 방식을 새로 도입해야 한다" in task
    assert "증분 수집 방식 검토가 왜 멈췄었지" in task
    assert "증분 수집이 뭐고 우리는 어떻게 쓰고 있어" in task
    assert "CDC" not in task


def test_portfolio_analyst_node_exists_and_flows_to_result_integrator():
    g = G.build().get_graph()
    assert Node.PORTFOLIO_ANALYST in g.nodes


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
    from app.agent.workflow.agents.work_architect import WorkArchitect
    st = {"messages": [HumanMessage(content="배치가 실패한다")], "intent": Intent.PLAN_WORK}
    assert "재현 경로" in WorkArchitect().task(st)
    # 의도가 modify 로 미끄러져도 **버그 이야기면** 규율이 유지된다
    assert "재현 경로" in WorkArchitect().task(dict(st, intent=Intent.MODIFY))
    # 버그 이야기가 아니면 평소 규율 — 아무 요청에나 버그 템플릿을 씌우면 안 된다
    plain = {"messages": [HumanMessage(content="메타데이터 등록 작업이 필요해")],
             "intent": Intent.PLAN_WORK}
    assert "재현 경로" not in WorkArchitect().task(plain)


def test_planner_schema_covers_all_new_intents():
    from app.agent.workflow.agents.request_architect import SCHEMA
    enum = SCHEMA["properties"]["intent"]["enum"]
    for i in (Intent.PLAN_WORK, Intent.MY_DAY, Intent.PROGRESS, Intent.ACTIVITY):
        assert i in enum


def test_request_plan_schema_bounds_decomposition_and_verbosity():
    from app.agent.workflow.agents.request_architect import SCHEMA

    tasks = SCHEMA["properties"]["tasks"]
    task = tasks["items"]["properties"]
    assert tasks["maxItems"] == 6
    assert task["instruction"]["maxLength"] <= 280
    assert task["completion_criteria"]["maxItems"] == 3
    assert SCHEMA["properties"]["blocking_questions"]["maxItems"] == 3


def test_request_plan_collapses_model_invented_internal_pipeline_tasks():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    text = "우리 기존 ETL 파이프라인에 Iceberg Puffin NDV 통계 생성 기능을 추가 구현하고 싶어"
    got = RequestArchitect().apply({"messages": [HumanMessage(content=text)]}, {
        "intent": Intent.PLAN_WORK,
        "goal": "Puffin NDV 기능 구현",
        "keywords": ["Iceberg Puffin NDV"],
        "tasks": [
            {"id": "q", "kind": "query", "instruction": "관련 이력 조회", "depends_on": [],
             "write_intent": False, "completion_criteria": ["이력 확인"]},
            {"id": "a", "kind": "analyze", "instruction": "구현안 분석", "depends_on": ["q"],
             "write_intent": False, "completion_criteria": ["구현안 분석"]},
            {"id": "t", "kind": "ticket", "instruction": "티켓 초안", "depends_on": ["a"],
             "write_intent": True, "completion_criteria": ["초안 작성"]},
        ],
    })

    tasks = got["request_plan"]["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["kind"] == "plan"
    assert tasks[0]["write_intent"] is True
    assert tasks[0]["instruction"] == text


def test_request_plan_keeps_genuinely_compound_user_outcomes():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    text = "Puffin 적용 이력을 조사하고 DL-9090에 결론 댓글을 남겨줘"
    model_tasks = [
        {"id": "r", "kind": "research", "instruction": "적용 이력 조사", "depends_on": [],
         "write_intent": False, "completion_criteria": ["이력 확인"]},
        {"id": "c", "kind": "comment", "instruction": "결론 댓글", "depends_on": ["r"],
         "write_intent": True, "completion_criteria": ["댓글 초안"]},
    ]
    got = RequestArchitect().apply({"messages": [HumanMessage(content=text)]}, {
        "intent": Intent.MODIFY, "keywords": ["Puffin"], "tasks": model_tasks,
    })

    assert got["request_plan"]["tasks"] == model_tasks


def test_request_plan_keeps_explicit_parallel_ticket_outcomes_of_the_same_effect():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    text = "로그인 실패 재현 Bug와 권한 모델 개선 Story를 각각 만들어줘"
    model_tasks = [
        {"id": "bug", "kind": "ticket", "instruction": "로그인 실패 재현 Bug 생성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["Bug 초안"]},
        {"id": "story", "kind": "ticket", "instruction": "권한 모델 개선 Story 생성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["Story 초안"]},
    ]

    got = RequestArchitect().apply(
        {"messages": [HumanMessage(content=text)]},
        {"intent": Intent.PLAN_WORK, "goal": "Bug와 Story 생성", "tasks": model_tasks})

    assert got["request_plan"]["tasks"] == model_tasks


def test_request_plan_keeps_repeated_one_item_clauses_with_a_shared_action():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    text = "로그인 재현 Bug 1건과 사용자 안내 Story 1건 만들어줘"
    model_tasks = [
        {"id": "bug", "kind": "ticket", "instruction": "로그인 재현 Bug 생성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["Bug 초안"]},
        {"id": "story", "kind": "ticket", "instruction": "사용자 안내 Story 생성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["Story 초안"]},
    ]

    got = RequestArchitect().apply(
        {"messages": [HumanMessage(content=text)]},
        {"intent": Intent.PLAN_WORK, "goal": "Bug와 Story 생성", "tasks": model_tasks})

    assert got["request_plan"]["tasks"] == model_tasks


def test_request_plan_does_not_preserve_more_parallel_tasks_than_the_user_requested():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    text = "로그인 실패 Bug와 사용자 안내 Story를 각각 만들어줘"
    model_tasks = [
        {"id": "bug", "kind": "ticket", "instruction": "로그인 실패 Bug 생성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["Bug"]},
        {"id": "story", "kind": "ticket", "instruction": "사용자 안내 Story 생성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["Story"]},
        {"id": "extra", "kind": "ticket", "instruction": "추가 Task 생성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["Task"]},
    ]

    got = RequestArchitect().apply(
        {"messages": [HumanMessage(content=text)]},
        {"intent": Intent.PLAN_WORK, "goal": "Bug와 Story 생성", "tasks": model_tasks})

    assert len(got["request_plan"]["tasks"]) == 1
    assert got["request_plan"]["tasks"][0]["instruction"] == text


def test_request_plan_does_not_treat_attribute_count_as_parallel_ticket_count():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    text = "Puffin 검증 Task 완료 조건 2개를 넣어줘"
    model_tasks = [
        {"id": "title", "kind": "ticket", "instruction": "Task 제목 작성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["제목"]},
        {"id": "criteria", "kind": "ticket", "instruction": "완료 조건 작성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["완료 조건"]},
    ]

    got = RequestArchitect().apply(
        {"messages": [HumanMessage(content=text)]},
        {"intent": Intent.PLAN_WORK, "goal": "Task 속성 작성", "tasks": model_tasks})

    assert len(got["request_plan"]["tasks"]) == 1
    assert got["request_plan"]["tasks"][0]["instruction"] == text


def test_request_plan_does_not_false_split_one_compound_ticket_expression():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    text = "로그인 Bug 원인 분석과 수정 방안을 담은 Task를 만들어줘"
    model_tasks = [
        {"id": "analysis", "kind": "ticket", "instruction": "로그인 Bug 원인 분석",
         "depends_on": [], "write_intent": True, "completion_criteria": ["원인"]},
        {"id": "fix", "kind": "ticket", "instruction": "수정 방안 Task 생성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["방안"]},
    ]

    got = RequestArchitect().apply(
        {"messages": [HumanMessage(content=text)]},
        {"intent": Intent.PLAN_WORK, "goal": "원인과 수정 방안 Task", "tasks": model_tasks})

    tasks = got["request_plan"]["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["instruction"] == text


def test_request_plan_keeps_existing_epic_selection_and_drops_invented_literals():
    """Selection delegation cannot become Epic creation; hard facts stay user-grounded."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    original = "Puffin NDV 통계 파이프라인을 개발해야 해"
    followup = "Epic은 네가 골라줘. 2026-09-30까지 최소 기능 1차 구현. 알아서"
    got = RequestArchitect().apply({
        "messages": [HumanMessage(content=followup)],
        "request_text": original,
        "questions": [{"field": "structure"}],
        "turns": 1,
    }, {
        "intent": Intent.PLAN_WORK,
        "keywords": ["Puffin NDV"],
        "goal": "Puffin NDV 파이프라인 Epic 생성",
        "tasks": [{
            "id": "t1", "kind": "query", "instruction": "Epic 생성을 위한 구조 확인",
            "depends_on": [], "write_intent": False,
            "completion_criteria": ["Epic 생성 티켓이 생성됨", "2026-09-30 적용"],
        }],
        "blocking_questions": ["Epic 생성 전에 세부 기술을 알려주세요"],
        "assumptions": [
            "마감일 2026-09-30은 월요일로 가정합니다.",
            "내부 검토일은 2026-09-28로 가정합니다.",
        ],
    })

    plan = got["request_plan"]
    rendered = str(plan)
    assert "Epic 생성" not in rendered
    assert "기존 Epic 선택" in rendered
    assert plan["tasks"][0]["instruction"] == original
    assert plan["tasks"][0]["write_intent"] is True
    assert plan["tasks"][0]["kind"] == "plan"
    assert "2026-09-28" not in rendered and "월요일" not in rendered


def test_research_interview_answer_preserves_original_write_outcome_before_work_runs():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    original = "Puffin NDV reader 검증 Task를 만들어줘"
    got = RequestArchitect().apply({
        "messages": [HumanMessage(content="RGP는 Reader Gate Policy라는 뜻이야")],
        "request_text": original,
        "turn_continuation": True,
        "questions": [],
        "turns": 0,
    }, {
        "intent": Intent.PLAN_WORK,
        "keywords": ["RGP"],
        "tasks": [{
            "id": "t1", "kind": "plan", "instruction": "RGP 의미 반영",
            "depends_on": [], "write_intent": True,
            "completion_criteria": ["Task 초안"],
        }],
    })

    assert got["request_plan"]["tasks"][0]["instruction"] == original
    assert got["request_plan"]["tasks"][0]["write_intent"] is True
    assert got["request_text"] == original


def test_research_interview_answer_preserves_compound_outcome_dag_and_write_intent():
    """The short term answer cannot replace or collapse the prior requested outcomes."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.anchors import requested_outcome_contract
    from app.agent.workflow.session import _turn_start_patch

    original = "Puffin 이력을 조사하고 후속 Task를 만든 뒤 DL-9090에 결론 댓글도 남겨줘"
    prior_plan = {
        "goal": "Puffin 조사 결과를 후속 실행 항목에 반영",
        "tasks": [
            {"id": "research", "kind": "research", "instruction": "Puffin 적용 이력 조사",
             "depends_on": [], "write_intent": False, "completion_criteria": ["이력 확인"]},
            {"id": "ticket", "kind": "ticket", "instruction": "후속 Task 생성",
             "depends_on": ["research"], "write_intent": True,
             "completion_criteria": ["Task 초안"]},
            {"id": "comment", "kind": "comment", "instruction": "DL-9090에 결론 댓글 작성",
             "depends_on": ["research"], "write_intent": True,
             "completion_criteria": ["댓글 초안"]},
        ],
        "blocking_questions": [], "assumptions": [],
    }
    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": original,
        "request_plan": prior_plan,
        "questions": [{"field": "term", "question": "RGP의 뜻은 무엇인가요?"}],
        "situation": "내부 이력 조사 완료",
        "materialized_ticket_sources": {
            "ticketDetails": [{"key": "DL-9200", "summary": "Puffin 상위 Epic"}],
            "parentCandidateKeys": ["DL-9200"],
        },
        "turns": 0,
    }
    answer = "RGP는 Reader Gate Policy라는 뜻이야"
    continued = _turn_start_patch(answer, prior)
    state = {**continued, "messages": [HumanMessage(content=answer)]}

    got = RequestArchitect().apply(state, {
        # Reproduce the small-model failure: it classifies the answer as a read and emits
        # one control task instead of the three user-visible outcomes.
        "intent": Intent.ASK,
        "keywords": ["RGP"],
        "goal": "RGP 의미 반영",
        "tasks": [{
            "id": "answer", "kind": "respond", "instruction": answer,
            "depends_on": [], "write_intent": False,
            "completion_criteria": ["용어 의미 확인"],
        }],
    })

    assert got["intent"] == Intent.PLAN_WORK
    assert got["request_text"] == original
    assert got["request_plan"]["goal"] == prior_plan["goal"]
    assert got["request_plan"]["tasks"] == prior_plan["tasks"]
    assert requested_outcome_contract({**prior, **got}) == requested_outcome_contract(prior)
    assert continued["materialized_ticket_sources"]["parentCandidateKeys"] == ["DL-9200"]


def test_verified_field_only_continuation_skips_request_model_and_preserves_outcomes(monkeypatch):
    """A parent/phase/exact-due overlay must not rediscover an authoritative work plan."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.anchors import requested_outcome_contract

    prior_plan = {
        "goal": "Puffin 이력을 조사하고 후속 Task와 결정 댓글을 작성한다",
        "tasks": [
            {"id": "research", "kind": "research", "instruction": "Puffin 적용 이력 조사",
             "depends_on": [], "write_intent": False, "completion_criteria": ["이력 확인"]},
            {"id": "ticket", "kind": "ticket", "instruction": "Puffin NDV 후속 구현 Task 생성",
             "depends_on": ["research"], "write_intent": True,
             "completion_criteria": ["Task 초안"]},
            {"id": "comment", "kind": "comment", "instruction": "DL-9090 결정 댓글 작성",
             "depends_on": ["research"], "write_intent": True,
             "completion_criteria": ["결정 공유"]},
        ],
        "blocking_questions": [], "assumptions": [],
    }
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": "StarRocks Puffin NDV 적용 이력을 조사하고 후속 구현 Task와 댓글을 작성해줘",
        "request_plan": prior_plan,
        "turn_continuation": True,
        "keywords": ["StarRocks", "Puffin", "NDV"],
        "module": "Runtime", "mentioned_keys": ["DL-9090"],
        "sufficient": True, "playbook": "task_create", "answer_depth": "explain",
        "situation": "관련 Jira 이력과 상위 Epic을 확인함",
        "materialized_ticket_sources": {
            "ticketDetails": [{"key": "DL-9200", "fields": {
                "issuetype": {"name": "Epic"}}}],
            "parentCandidateKeys": ["DL-9200"],
        },
        "messages": [HumanMessage(content=(
            "Epic은 네가 골라줘. 최소 기능 1차 구현. "
            "마감은 2026-09-30까지. 알아서."))],
    }
    before = requested_outcome_contract(state)
    agent = RequestArchitect()
    calls = []
    monkeypatch.setattr(
        agent, "invoke_structured",
        lambda *_args, **_kwargs: calls.append(True) or pytest.fail(
            "verified typed refinement must not call RequestArchitect LLM"),
    )

    patch = agent._run(state)

    assert calls == []
    assert patch["intent"] == Intent.PLAN_WORK
    assert patch["request_text"] == state["request_text"]
    assert patch["request_plan"] == prior_plan
    assert patch["request_plan"] is not prior_plan
    assert requested_outcome_contract({**state, **patch}) == before
    assert patch["keywords"] == ["StarRocks", "Puffin", "NDV"]
    assert patch["mentioned_keys"] == ["DL-9090"]
    assert "모델 호출 생략" in patch["trace"][0]["note"]
    assert G.route_after_request_architect({**state, **patch}) == "refine"


def test_typed_phase_action_change_falls_back_to_semantic_request_architect(monkeypatch):
    """An ordinal parser must not erase a newly requested execution stage."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    prior_task = {
        "id": "delivery", "kind": "ticket",
        "instruction": "VectorIndex 구현 Task 생성",
        "depends_on": [], "write_intent": True,
        "completion_criteria": ["VectorIndex 구현 초안"],
    }
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": "VectorIndex 구현 Task를 만들어줘",
        "request_plan": {"goal": "VectorIndex 구현", "tasks": [prior_task]},
        "turn_continuation": True,
        "materialized_ticket_sources": {
            "ticketDetails": [{"key": "DL-9200"}],
            "parentCandidateKeys": ["DL-9200"],
        },
        "request_refinement": {
            "parent": "select_existing", "phase": "기존", "duedate": "2026-08-31",
        },
        "messages": [HumanMessage(content=(
            "Epic은 네가 골라줘. 범위는 1차 검증까지, 마감은 2026-09-30"))],
    }
    calls = []
    agent = RequestArchitect()
    monkeypatch.setattr(agent, "invoke_structured", lambda *_args, **_kwargs: (
        calls.append(True) or {
            "intent": Intent.PLAN_WORK, "keywords": ["VectorIndex", "검증"],
            "sufficient": True, "goal": "VectorIndex 1차 검증",
            "tasks": [{**prior_task, "instruction": "VectorIndex 1차 검증 Task 생성"}],
        }))

    patch = agent._run(state)

    assert calls == [True]
    assert patch["request_refinement"] == {}


def test_typed_phase_action_without_prior_action_identity_falls_back(monkeypatch):
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    prior_task = {
        "id": "delivery", "kind": "ticket", "instruction": "VectorIndex Task 생성",
        "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
    }
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": "VectorIndex Task를 만들어줘",
        "request_plan": {"goal": "VectorIndex Task 생성", "tasks": [prior_task]},
        "turn_continuation": True,
        "materialized_ticket_sources": {"ticketDetails": [{"key": "DL-9200"}]},
        "messages": [HumanMessage(content=(
            "Epic은 네가 골라줘. 범위는 1차 검증까지, 마감은 2026-09-30"))],
    }
    calls = []
    agent = RequestArchitect()
    monkeypatch.setattr(agent, "invoke_structured", lambda *_args, **_kwargs: (
        calls.append(True) or {
            "intent": Intent.PLAN_WORK, "keywords": ["VectorIndex", "검증"],
            "sufficient": True, "goal": "VectorIndex 검증",
            "tasks": [{**prior_task, "instruction": "VectorIndex 검증 Task 생성"}],
        }))

    patch = agent._run(state)

    assert calls == [True]
    assert patch["request_refinement"] == {}


@pytest.mark.parametrize(("prior_action", "phase_clause"), [
    ("개발", "범위는 1차 구현까지"),
    ("기획", "범위는 1차 설계까지"),
    ("개발", "범위는 1차까지"),
], ids=("same-implementation-family", "same-design-family", "ordinal-only"))
def test_typed_phase_same_or_unspecified_action_keeps_zero_call_fast_path(
        monkeypatch, prior_action, phase_clause):
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    prior_task = {
        "id": "delivery", "kind": "ticket",
        "instruction": f"VectorIndex {prior_action} Task 생성",
        "depends_on": [], "write_intent": True,
        "completion_criteria": [f"VectorIndex {prior_action} 초안"],
    }
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": f"VectorIndex {prior_action} Task를 만들어줘",
        "request_plan": {"goal": f"VectorIndex {prior_action}", "tasks": [prior_task]},
        "turn_continuation": True,
        "materialized_ticket_sources": {
            "ticketDetails": [{"key": "DL-9200"}],
            "parentCandidateKeys": ["DL-9200"],
        },
        "messages": [HumanMessage(content=(
            f"Epic은 네가 골라줘. {phase_clause}, 마감은 2026-09-30"))],
    }
    agent = RequestArchitect()
    monkeypatch.setattr(
        agent, "invoke_structured",
        lambda *_args, **_kwargs: pytest.fail("compatible typed refinement must stay zero-call"),
    )

    patch = agent._run(state)

    assert patch["request_refinement"] == {
        "parent": "select_existing", "phase": "1차", "duedate": "2026-09-30",
    }


def test_request_fast_path_does_not_bypass_missing_parent_candidate_retrieval(monkeypatch):
    """Skipping classification must not turn an unrelated opened ticket into parent authority."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    task = {
        "id": "ticket", "kind": "ticket", "instruction": "Puffin NDV 구현 Task 생성",
        "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
    }
    state = {
        "intent": Intent.PLAN_WORK, "request_text": "Puffin NDV 구현 Task를 만들어줘",
        "request_plan": {"goal": "Puffin NDV 구현 Task 생성", "tasks": [task]},
        "turn_continuation": True,
        "materialized_ticket_sources": {
            "ticketDetails": [{"key": "DL-9201", "fields": {
                "issuetype": {"name": "Task"}}}],
            "parentCandidateKeys": [],
        },
        "messages": [HumanMessage(content=(
            "Epic은 네가 골라줘. 범위는 1차 구현까지. 마감은 2026-09-30까지"))],
    }
    agent = RequestArchitect()
    monkeypatch.setattr(
        agent, "invoke_structured",
        lambda *_args, **_kwargs: pytest.fail("typed refinement must not call the model"),
    )

    patch = agent._run(state)

    assert patch["request_plan"] == state["request_plan"]
    assert not ({"query_plan", "query_results", "query_artifacts"} & set(patch))
    assert G.route_after_request_architect({**state, **patch}) == "investigate"


@pytest.mark.parametrize("latest", [
    "Kafka Epic은 네가 골라줘. 1차 구현. 마감은 2026-09-30까지",
    "Epic은 네가 골라줘. 1차 구현 Task를 새로 만들어줘. 마감은 2026-09-30까지",
    "Epic은 네가 골라줘. 1차 구현. 댓글도 추가로 남겨줘. 마감은 2026-09-30까지",
    "댓글은 빼고 Task만 진행해줘",
    "Epic은 네가 골라줘. 범위는 writer와 reader까지. 마감은 2026-09-30까지",
    "Epic은 네가 골라줘. 1차 구현. 마감은 2026-09-30 또는 2026-10-07 중 하나",
    "그걸로 진행해줘",
    "DL-9200으로 연결해줘. 1차 구현. 마감은 2026-09-30까지",
], ids=("new-topic", "new-action", "added-outcome", "removed-outcome",
        "free-form-scope", "ambiguous-due", "ambiguous-answer", "bare-parent-key"))
def test_request_fast_path_fails_safe_to_semantic_model_for_non_typed_changes(
        monkeypatch, latest):
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    prior_task = {
        "id": "ticket", "kind": "ticket", "instruction": "Puffin NDV Task 생성",
        "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
    }
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin NDV Task를 만들어줘",
        "request_plan": {"goal": "Puffin NDV Task 생성", "tasks": [prior_task]},
        "turn_continuation": True,
        "situation": "관련 이력 조사 완료",
        "materialized_ticket_sources": {
            "ticketDetails": [{"key": "DL-9200"}], "parentCandidateKeys": []},
        # A prior fast turn must never leak its execution-field overlay into a semantic turn.
        "request_refinement": {
            "parent": "select_existing", "phase": "1차", "duedate": "2026-08-31",
        },
        "messages": [HumanMessage(content=latest)],
    }
    calls = []

    def semantic_once(_state, _messages):
        calls.append(True)
        return {
            "intent": Intent.PLAN_WORK, "keywords": ["Puffin", "NDV"],
            "sufficient": True, "goal": "의미 변경 재분류", "tasks": [prior_task],
        }

    agent = RequestArchitect()
    monkeypatch.setattr(agent, "invoke_structured", semantic_once)

    patch = agent._run(state)

    assert calls == [True]
    assert patch["request_refinement"] == {}


def test_explicit_parent_field_fast_path_is_visible_to_work_architect(monkeypatch):
    """Only an explicit parent/Epic clause may skip classification for an exact key."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.agents import work_architect as work

    task = {
        "id": "ticket", "kind": "ticket", "instruction": "Puffin NDV 구현 Task 생성",
        "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
    }
    latest = "상위 Epic은 DL-9200으로 연결해줘. 1차 구현. 마감은 2026-09-30까지"
    state = {
        "intent": Intent.PLAN_WORK, "request_text": "Puffin NDV 구현 Task를 만들어줘",
        "request_plan": {"goal": "Puffin NDV 구현 Task 생성", "tasks": [task]},
        "turn_continuation": True,
        "materialized_ticket_sources": {"ticketDetails": [{"key": "DL-9200"}]},
        "messages": [HumanMessage(content=latest)],
    }
    agent = RequestArchitect()
    monkeypatch.setattr(
        agent, "invoke_structured",
        lambda *_args, **_kwargs: pytest.fail("explicit typed parent must use fast path"),
    )
    monkeypatch.setattr(work, "_is_epic", lambda key: key == "DL-9200")

    patch = agent._run(state)

    assert patch["mentioned_keys"] == ["DL-9200"]
    assert work._explicit_parent_epic({**state, **patch}) == "DL-9200"


def test_select_existing_and_top_level_fast_fields_remain_typed_and_visible_downstream():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import _typed_continuation_refinement
    from app.agent.workflow.agents.work_architect import _delegates_existing_epic_choice

    select = "Epic은 네가 골라줘. 1차 구현. 마감은 2026-09-30까지"
    top_level = "최상위 Task로 진행해줘. 1차 구현. 마감은 2026-09-30까지"

    assert _typed_continuation_refinement(select)["parent"] == "select_existing"
    assert _typed_continuation_refinement(top_level)["parent"] == "top_level"
    assert _delegates_existing_epic_choice({
        "request_text": "Puffin NDV Task를 만들어줘",
        "turn_continuation": True,
        "messages": [HumanMessage(content=select)],
    })


@pytest.mark.parametrize("optional_marker", [
    {},
    {"required_input": False},
], ids=("required-input-absent", "required-input-false"))
def test_exact_optional_structure_question_uses_zero_call_typed_continuation(
        monkeypatch, optional_marker):
    """r25 raw shape: an optional structure preference must not reclassify a complete answer."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.session import _turn_start_patch

    prior_plan = {"goal": "Puffin 최소 기능 구현", "tasks": [{
        "id": "delivery", "kind": "ticket", "instruction": "Puffin 최소 기능 구현 Task 생성",
        "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
    }]}
    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin NDV 적용 이력을 반영한 최소 기능을 개발해야 해",
        "request_plan": prior_plan,
        "draft": {"mode": "task", "items": [{"summary": "Puffin 최소 기능"}]},
        "situation": "관련 이력과 상위 Epic 후보 조사 완료",
        "materialized_ticket_sources": {
            "ticketDetails": [
                {"key": "DL-9200", "type": "Epic"},
                {"key": "DL-9202", "type": "Task"},
                {"key": "DL-9203", "type": "Task"},
                {"key": "DL-9201", "type": "Task"},
                {"key": "DL-7001", "type": "Epic"},
            ],
            "parentCandidateKeys": ["DL-9200", "DL-7001"],
            "parentCandidateSearchAttempted": True,
        },
        "questions": [{
            "question": "작업이 여러 단계로 보이는데 어떻게 만들까요?",
            "kind": "choice", "field": "",
            "options": [
                "Task 하나 + 단계별 Sub-Task (권장 — 단계·담당이 나뉜다)",
                "단일 Task 로 둔다",
            ],
            **optional_marker,
        }],
    }
    latest = (
        "Epic 은 네가 골라줘. 범위는 최소 기능 1차 구현까지, "
        "마감은 2026-09-30. 알아서 진행해"
    )

    continued = _turn_start_patch(latest, prior)
    state = {**continued, "messages": [HumanMessage(content=latest)]}
    agent = RequestArchitect()
    calls = []
    monkeypatch.setattr(
        agent, "invoke_structured",
        lambda *_args, **_kwargs: calls.append(True) or pytest.fail(
            "complete typed answer to an optional question must not call the model"),
    )

    patch = agent._run(state)

    assert continued["turn_continuation"]
    assert continued["request_refinement"] == {}
    assert continued["request_text"] == prior["request_text"]
    assert continued["request_plan"] == prior_plan
    assert continued["draft"] == prior["draft"]
    assert [row["key"] for row in continued["materialized_ticket_sources"][
        "ticketDetails"]] == ["DL-9200", "DL-9202", "DL-9203", "DL-9201", "DL-7001"]
    assert calls == []
    assert patch["request_plan"] == prior_plan
    assert patch["request_text"] == prior["request_text"]
    assert patch["request_refinement"] == {
        "parent": "select_existing",
        "phase": "1차",
        "duedate": "2026-09-30",
    }
    assert G.route_after_request_architect({**state, **patch}) == "refine"


def test_new_turn_clears_stale_typed_request_refinement():
    from app.agent.workflow.session import _turn_start_patch

    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin NDV Task를 만들어줘",
        "request_plan": {"goal": "Puffin NDV Task 생성", "tasks": [{
            "id": "delivery", "kind": "ticket", "instruction": "Puffin NDV Task 생성",
            "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
        }]},
        "request_refinement": {
            "parent": "DL-9200", "phase": "1차", "duedate": "2026-09-30",
        },
        "draft": {"items": [{"summary": "Puffin NDV Task"}]},
    }

    fresh = _turn_start_patch("완전히 다른 보안 교육 현황을 알려줘", prior)

    assert not fresh["turn_continuation"]
    assert fresh["request_refinement"] == {}


@pytest.mark.parametrize("question", [
    {"field": "target", "required_input": True},
    {"field": "person", "required_input": True},
    {"field": "term", "required_input": True},
    {"field": "", "required_input": True},
], ids=("target", "person", "term", "blank-but-required"))
def test_typed_fields_do_not_bypass_a_required_pending_question(question):
    from app.agent.workflow.session import _turn_start_patch

    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin 검증 Task를 만들어줘",
        "request_plan": {"goal": "Puffin 검증", "tasks": [{
            "id": "delivery", "kind": "ticket", "instruction": "Puffin 검증 Task 생성",
            "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
        }]},
        "draft": {"items": [{"summary": "Puffin 검증"}]},
        "questions": [{
            "question": "필수 정보를 알려주세요", "kind": "text", "options": [], **question,
        }],
    }
    latest = (
        "Epic 은 네가 골라줘. 범위는 최소 기능 1차 구현까지, "
        "마감은 2026-09-30. 알아서 진행해"
    )

    fresh = _turn_start_patch(latest, prior)

    assert not fresh["turn_continuation"]
    assert fresh["request_plan"] == {}
    assert fresh["draft"] == {}


def test_typed_fields_do_not_fast_bypass_a_required_due_question():
    from app.agent.workflow.session import _turn_start_patch

    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin 검증 Task를 만들어줘",
        "request_plan": {"goal": "Puffin 검증", "tasks": [{
            "id": "delivery", "kind": "ticket", "instruction": "Puffin 검증 Task 생성",
            "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
        }]},
        "questions": [{
            "question": "마감일은 언제인가요?", "kind": "text", "field": "due",
            "options": [], "required_input": True,
        }],
    }
    latest = (
        "Epic 은 네가 골라줘. 범위는 최소 기능 1차 구현까지, "
        "마감은 2026-09-30. 알아서 진행해"
    )

    fresh = _turn_start_patch(latest, prior)

    assert not fresh["turn_continuation"]
    assert fresh["request_plan"] == {}


@pytest.mark.parametrize("question", [
    {"question": "검증 대상 테이블을 알려주세요", "kind": "text", "options": []},
    {"question": "누구를 담당자로 배정할까요?", "kind": "text", "options": []},
    {"question": "RGP 뜻을 알려주세요", "kind": "text", "options": []},
], ids=("legacy-target", "legacy-person", "legacy-term"))
def test_blank_legacy_research_question_is_not_optional_from_missing_metadata(question):
    from app.agent.workflow.session import _turn_start_patch

    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin 검증 Task를 만들어줘",
        "request_plan": {"goal": "Puffin 검증", "tasks": [{
            "id": "delivery", "kind": "ticket", "instruction": "Puffin 검증 Task 생성",
            "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
        }]},
        "draft": {"items": [{"summary": "Puffin 검증"}]},
        # Legacy/current producer omitted both field and required_input.
        "questions": [question],
    }
    latest = (
        "Epic 은 네가 골라줘. 범위는 최소 기능 1차 구현까지, "
        "마감은 2026-09-30. 알아서 진행해"
    )

    fresh = _turn_start_patch(latest, prior)

    assert not fresh["turn_continuation"]
    assert fresh["request_text"] == latest
    assert fresh["request_plan"] == {}
    assert fresh["draft"] == {}
    assert prior["questions"] == [question]


def test_explicit_optional_structure_field_accepts_the_complete_typed_answer():
    from app.agent.workflow.session import _turn_start_patch

    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin 검증 Task를 만들어줘",
        "request_plan": {"goal": "Puffin 검증", "tasks": [{
            "id": "delivery", "kind": "ticket", "instruction": "Puffin 검증 Task 생성",
            "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
        }]},
        "questions": [{
            "question": "어떤 구조로 만들까요?", "kind": "choice", "field": "structure",
            "options": ["단일 Task", "Task + Sub-Task"], "required_input": False,
        }],
    }
    latest = (
        "Epic 은 네가 골라줘. 범위는 최소 기능 1차 구현까지, "
        "마감은 2026-09-30. 알아서 진행해"
    )

    continued = _turn_start_patch(latest, prior)

    assert continued["turn_continuation"]
    assert continued["request_plan"] == prior["request_plan"]


@pytest.mark.parametrize("question", [
    {
        "question": "어떤 구성 요소를 만들어야 할까요?", "kind": "choice",
        "field": "", "options": ["reader", "writer"],
    },
    {
        "question": "어떤 형태의 데이터를 대상으로 할까요?", "kind": "choice",
        "field": "", "options": ["정형 데이터", "비정형 데이터"],
    },
    {
        "question": "어떤 구조체를 사용할까요?", "kind": "choice",
        "field": "", "options": ["배열", "맵"],
    },
], ids=("component-target", "data-shape", "data-structure"))
def test_domain_shape_words_are_not_legacy_ticket_hierarchy_choices(question):
    from app.agent.workflow.session import _turn_start_patch

    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin 검증 Task를 만들어줘",
        "request_plan": {"goal": "Puffin 검증", "tasks": [{
            "id": "delivery", "kind": "ticket", "instruction": "Puffin 검증 Task 생성",
            "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
        }]},
        "draft": {"items": [{"summary": "Puffin 검증"}]},
        # Blank field and absent required_input are legacy metadata, not optionality proof.
        "questions": [question],
    }
    latest = (
        "Epic 은 네가 골라줘. 범위는 최소 기능 1차 구현까지, "
        "마감은 2026-09-30. 알아서 진행해"
    )

    fresh = _turn_start_patch(latest, prior)

    assert not fresh["turn_continuation"]
    assert fresh["request_plan"] == {}
    assert fresh["draft"] == {}


@pytest.mark.parametrize("state_change", [
    {"turn_continuation": False},
    {"materialized_ticket_sources": {}},
    {"intent": Intent.MODIFY},
    {"request_text": "새 Puffin Epic을 만들어줘"},
], ids=("new-turn", "unverified-context", "different-intent", "changed-epic-action"))
def test_request_fast_path_requires_authoritative_plan_work_context(monkeypatch, state_change):
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    task = {
        "id": "ticket", "kind": "ticket", "instruction": "Puffin Task 생성",
        "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
    }
    state = {
        "intent": Intent.PLAN_WORK, "request_text": "Puffin Task를 만들어줘",
        "request_plan": {"goal": "Puffin Task 생성", "tasks": [task]},
        "turn_continuation": True,
        "materialized_ticket_sources": {"ticketDetails": [{"key": "DL-9200"}]},
        "messages": [HumanMessage(content=(
            "Epic은 네가 골라줘. 1차 구현. 마감은 2026-09-30까지"))],
        **state_change,
    }
    calls = []
    agent = RequestArchitect()
    monkeypatch.setattr(agent, "invoke_structured", lambda *_args, **_kwargs: (
        calls.append(True) or {
            "intent": Intent.PLAN_WORK, "keywords": ["Puffin"], "sufficient": True,
            "goal": "의미 재분류", "tasks": [task],
        }))

    agent._run(state)

    assert calls == [True]


def test_explicit_new_topic_does_not_restore_a_stale_compound_write_plan():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.session import _turn_start_patch

    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin Task를 만들고 결론 댓글도 남겨줘",
        "request_plan": {"goal": "old", "tasks": [
            {"id": "old-ticket", "kind": "ticket", "instruction": "Puffin Task 생성",
             "depends_on": [], "write_intent": True, "completion_criteria": ["Task"]},
            {"id": "old-comment", "kind": "comment", "instruction": "결론 댓글",
             "depends_on": [], "write_intent": True, "completion_criteria": ["댓글"]},
        ]},
        "questions": [{"field": "term", "question": "RGP 뜻?"}],
        "materialized_ticket_sources": {
            "ticketDetails": [{"key": "DL-9200"}], "parentCandidateKeys": ["DL-9200"]},
    }
    new_request = "이건 취소하고 완전히 다른 보안 교육 Task를 새로 만들어줘"
    fresh = _turn_start_patch(new_request, prior)
    got = RequestArchitect().apply(
        {**fresh, "messages": [HumanMessage(content=new_request)]},
        {"intent": Intent.PLAN_WORK, "keywords": ["보안 교육"], "goal": "보안 교육 Task 생성",
         "tasks": [{"id": "new-ticket", "kind": "ticket", "instruction": new_request,
                    "depends_on": [], "write_intent": True,
                    "completion_criteria": ["새 Task 초안"]}]})

    assert not fresh["turn_continuation"]
    assert fresh["materialized_ticket_sources"] == {}
    assert [task["id"] for task in got["request_plan"]["tasks"]] == ["new-ticket"]
    assert "Puffin" not in str(got["request_plan"])


def test_compound_continuation_applies_an_explicit_typed_outcome_removal():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.anchors import requested_outcome_contract

    prior_plan = {
        "goal": "Puffin 검증 Task를 만들고 결정 댓글을 남긴다",
        "tasks": [
            {"id": "ticket", "kind": "ticket", "instruction": "Puffin 검증 Task 생성",
             "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"]},
            {"id": "comment", "kind": "comment", "instruction": "결정 댓글 작성",
             "depends_on": ["ticket"], "write_intent": True,
             "completion_criteria": ["댓글 초안"]},
        ],
        "blocking_questions": [], "assumptions": [],
    }
    answer = "댓글은 빼고 Task만 진행해줘"
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin 검증 Task를 만들고 결정 댓글을 남겨줘",
        "request_plan": prior_plan,
        "turn_continuation": True,
        "messages": [HumanMessage(content=answer)],
    }
    before = requested_outcome_contract(state)
    got = RequestArchitect().apply(state, {
        "intent": Intent.PLAN_WORK, "goal": "Puffin 검증 Task만 진행",
        "keywords": ["Puffin"],
        # A small model may still echo both old outcomes. Runtime owns the explicit removal.
        "tasks": prior_plan["tasks"],
    })

    assert got["intent"] == Intent.PLAN_WORK
    assert got["request_plan"]["tasks"] == [prior_plan["tasks"][0]]
    assert "댓글" not in got["request_plan"]["goal"]
    after = requested_outcome_contract({**state, **got})
    assert len(before["outcomes"]) == 2 and len(after["outcomes"]) == 1
    assert after["outcomes"][0]["source_task_id"] == "ticket"


def test_compound_continuation_replaces_only_the_explicitly_changed_outcome():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    prior_tasks = [
        {"id": "ticket", "kind": "ticket", "instruction": "Puffin 검증 Task 생성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"]},
        {"id": "comment", "kind": "comment", "instruction": "검증 시작 결정 댓글 작성",
         "depends_on": ["ticket"], "write_intent": True,
         "completion_criteria": ["시작 결정을 알린다"]},
    ]
    answer = "댓글 내용은 검증 보류 결정으로 바꿔줘"
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin 검증 Task를 만들고 시작 결정 댓글을 남겨줘",
        "request_plan": {"goal": "Task와 댓글 작성", "tasks": prior_tasks},
        "turn_continuation": True,
        "messages": [HumanMessage(content=answer)],
    }
    changed_comment = {
        "id": "model-comment", "kind": "comment", "instruction": "검증 보류 결정 댓글 작성",
        "depends_on": [], "write_intent": True, "completion_criteria": ["보류 결정을 알린다"],
    }

    got = RequestArchitect().apply(state, {
        "intent": Intent.MODIFY, "goal": "Task 생성과 보류 댓글 작성",
        "tasks": [changed_comment], "keywords": ["Puffin"],
    })

    tasks = got["request_plan"]["tasks"]
    assert tasks[0] == prior_tasks[0]
    assert tasks[1]["id"] == "comment"
    assert tasks[1]["instruction"] == changed_comment["instruction"]
    assert tasks[1]["depends_on"] == ["ticket"]
    assert tasks[1]["completion_criteria"] == changed_comment["completion_criteria"]


def test_compound_continuation_treats_instead_inside_comment_content_as_a_change():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    prior_tasks = [
        {"id": "ticket", "kind": "ticket", "instruction": "Puffin 검증 Task 생성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["Task"]},
        {"id": "comment", "kind": "comment", "instruction": "검증 시작 결정 댓글 작성",
         "depends_on": ["ticket"], "write_intent": True,
         "completion_criteria": ["시작 결정을 알린다"]},
    ]
    answer = "댓글 내용은 검증 시작 대신 보류 결정으로 바꿔줘"
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin 검증 Task를 만들고 시작 결정 댓글을 남겨줘",
        "request_plan": {"goal": "Task와 댓글 작성", "tasks": prior_tasks},
        "turn_continuation": True,
        "messages": [HumanMessage(content=answer)],
    }
    changed_comment = {
        "id": "model-comment", "kind": "comment", "instruction": "검증 보류 결정 댓글 작성",
        "depends_on": [], "write_intent": True,
        "completion_criteria": ["보류 결정을 알린다"],
    }

    got = RequestArchitect().apply(state, {
        "intent": Intent.MODIFY, "goal": "Task 생성과 보류 댓글 작성",
        "tasks": [changed_comment], "keywords": ["Puffin"],
    })

    tasks = got["request_plan"]["tasks"]
    assert len(tasks) == 2
    assert tasks[0] == prior_tasks[0]
    assert tasks[1]["id"] == "comment"
    assert tasks[1]["instruction"] == changed_comment["instruction"]


def test_ambiguous_outcome_removal_keeps_the_authoritative_compound_plan():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    prior_tasks = [
        {"id": "ticket", "kind": "ticket", "instruction": "Puffin Task 생성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["Task"]},
        {"id": "comment", "kind": "comment", "instruction": "결정 댓글 작성",
         "depends_on": ["ticket"], "write_intent": True, "completion_criteria": ["댓글"]},
    ]
    state = {
        "intent": Intent.PLAN_WORK, "request_text": "Puffin Task와 댓글을 작성해줘",
        "request_plan": {"goal": "Task와 댓글", "tasks": prior_tasks},
        "turn_continuation": True,
        "messages": [HumanMessage(content="그건 빼고 진행해줘")],
    }
    got = RequestArchitect().apply(state, {
        "intent": Intent.PLAN_WORK, "goal": "하나를 제외", "tasks": [{
            "id": "guess", "kind": "ticket", "instruction": "Task만 진행",
            "depends_on": [], "write_intent": True, "completion_criteria": ["Task"],
        }],
    })

    assert got["request_plan"]["tasks"] == prior_tasks
    assert got["request_plan"]["goal"] == "Task와 댓글"


def test_add_one_more_task_preserves_prior_dag_and_appends_semantic_outcome():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.anchors import requested_outcome_contract
    from app.agent.workflow.session import _turn_start_patch

    prior_tasks = [
        {"id": "research", "kind": "research", "instruction": "Puffin 이력 조사",
         "depends_on": [], "write_intent": False, "completion_criteria": ["이력 확인"]},
        {"id": "delivery", "kind": "ticket", "instruction": "Puffin 적용 Task 생성",
         "depends_on": ["research"], "write_intent": True,
         "completion_criteria": ["적용 Task 초안"]},
    ]
    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin 이력을 조사하고 적용 Task를 만들어줘",
        "request_plan": {"goal": "Puffin 조사와 적용 Task", "tasks": prior_tasks},
        "draft": {"items": [{"summary": "Puffin 적용"}]},
        "query_plan": [{"kind": "ticket_search", "params": {"query": "Puffin"}}],
        "query_results": [{"key": "DL-9200"}],
        "questions": [],
    }
    latest = "Task 하나 더. 범위는 1차로, 마감은 2026-10-15"
    continued = _turn_start_patch(latest, prior)
    state = {**continued, "messages": [HumanMessage(content=latest)]}
    new_task = {
        "id": "validation", "kind": "ticket", "instruction": "Puffin 1차 검증 Task 생성",
        "depends_on": ["delivery"], "write_intent": True,
        "completion_criteria": ["2026-10-15까지 1차 검증 완료"],
    }

    got = RequestArchitect().apply(state, {
        "intent": Intent.PLAN_WORK, "keywords": ["Puffin", "1차 검증"],
        "goal": "검증 Task 한 건 추가", "tasks": [new_task],
    })

    assert continued["turn_continuation"]
    assert continued["query_plan"] == {}
    assert continued["query_results"] == []
    assert [task["id"] for task in got["request_plan"]["tasks"]] == [
        "research", "delivery", "validation",
    ]
    assert got["request_plan"]["tasks"][:2] == prior_tasks
    assert got["request_plan"]["tasks"][2] == new_task
    assert [row["source_task_id"] for row in requested_outcome_contract(
            {**state, **got})["outcomes"]] == ["delivery", "validation"]


def test_additive_task_creation_keeps_existing_outcomes_and_is_idempotent():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.session import _turn_start_patch

    prior_tasks = [
        {"id": "delivery", "kind": "ticket", "instruction": "Puffin 적용 Task 생성",
         "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"]},
        {"id": "decision", "kind": "comment", "instruction": "DL-9090 결정 댓글 작성",
         "depends_on": ["delivery"], "write_intent": True,
         "completion_criteria": ["결정 공유"]},
    ]
    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin 적용 Task를 만들고 DL-9090에 결정 댓글을 남겨줘",
        "request_plan": {"goal": "Task와 댓글", "tasks": prior_tasks},
        "draft": {"items": [{"summary": "Puffin 적용"}]},
        "questions": [],
    }
    latest = "검증 Task도 하나 만들어줘. 범위는 1차로, 마감은 2026-10-15"
    continued = _turn_start_patch(latest, prior)
    state = {**continued, "messages": [HumanMessage(content=latest)]}
    new_task = {
        "id": "validation", "kind": "ticket", "instruction": "Puffin 검증 Task 생성",
        "depends_on": ["delivery"], "write_intent": True,
        "completion_criteria": ["1차 검증 완료"],
    }
    model_out = {
        "intent": Intent.PLAN_WORK, "keywords": ["Puffin", "검증"],
        "goal": "검증 Task 추가",
        # A small model may echo one authoritative prior outcome despite the prompt. Runtime
        # must discard that duplicate and append only the one novel semantic outcome.
        "tasks": [{**prior_tasks[0], "id": "echo-delivery"}, new_task],
    }

    first = RequestArchitect().apply(state, model_out)
    repeated = RequestArchitect().apply(
        {**state, **first, "turn_continuation": True}, model_out,
    )

    assert continued["turn_continuation"]
    assert [task["id"] for task in first["request_plan"]["tasks"]] == [
        "delivery", "decision", "validation",
    ]
    assert first["request_plan"]["tasks"][:2] == prior_tasks
    assert repeated["request_plan"]["tasks"] == first["request_plan"]["tasks"]


def test_non_additive_more_word_does_not_modify_the_prior_outcome_dag():
    from app.agent.workflow.agents.request_architect import _continuation_outcome_directive
    from app.agent.workflow.session import _turn_start_patch

    assert not _continuation_outcome_directive("Task를 더 자세히 설명해줘")
    assert not _continuation_outcome_directive("성능을 더 개선해줘")
    prior = {
        "intent": Intent.PLAN_WORK,
        "request_text": "Puffin 적용 Task를 만들어줘",
        "request_plan": {"goal": "Puffin 적용", "tasks": [{
            "id": "delivery", "kind": "ticket", "instruction": "Puffin 적용 Task 생성",
            "depends_on": [], "write_intent": True, "completion_criteria": ["Task 초안"],
        }]},
        "draft": {"items": [{"summary": "Puffin 적용"}]},
        "questions": [],
    }

    fresh = _turn_start_patch("Task를 더 자세히 설명해줘", prior)

    assert not fresh["turn_continuation"]
    assert fresh["request_plan"] == {}


def test_request_plan_preserves_an_explicit_new_epic_creation():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    text = "데이터 품질 관리 Epic을 새로 만들어줘"
    got = RequestArchitect().apply({"messages": [HumanMessage(content=text)]}, {
        "intent": Intent.PLAN_WORK,
        "keywords": ["데이터 품질"],
        "goal": "데이터 품질 관리 Epic 생성",
        "tasks": [{
            "id": "t1", "kind": "ticket", "instruction": "Epic 생성",
            "depends_on": [], "write_intent": True,
            "completion_criteria": ["Epic 생성"],
        }],
    })

    assert "Epic 생성" in str(got["request_plan"])


def test_request_plan_preserves_create_if_no_existing_epic_fallback():
    """Existing-first selection and explicit fallback creation are both user outcomes."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    text = "관련 Epic은 네가 골라줘. 적합한 게 없으면 새로 만들어줘"
    got = RequestArchitect().apply({"messages": [HumanMessage(content=text)]}, {
        "intent": Intent.PLAN_WORK,
        "keywords": ["관련 Epic"],
        "goal": "기존 Epic을 선택하고 없으면 Epic 생성",
        "tasks": [{
            "id": "t1", "kind": "plan", "instruction": text,
            "depends_on": [], "write_intent": True,
            "completion_criteria": ["기존 Epic을 우선 선택하고 없으면 새 Epic 생성"],
        }],
    })

    rendered = str(got["request_plan"])
    assert "없으면 Epic 생성" in rendered
    assert "기존 Epic 선택" not in rendered


# ── modify 실행 경로 — 변경 계획 → 승인 → update_ticket ────────────
def test_change_plan_routes_to_approval_not_assignment():
    """변경 계획은 담당자 추천·생성 검증을 지나지 않는다 — 해당이 없는 단계다."""
    assert G.route_after_work_architect({"questions": [],
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
    """modify 이음매 전체 — RequestArchitect/WorkArchitect 만 고정. **ActionExecutor 는 실물이다**(변경 실행이
    결정적이라 LLM 없이 돈다). interrupt·이중 토큰·update·코멘트까지 진짜로 굴린다."""
    from app.agent.workflow import session
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.agents.work_architect import WorkArchitect
    from app.agent.tools import _ctx
    import app.agent.tools as T

    key = _ctx.client().search_issues("ORDER BY created DESC", max_results=5)[0]["key"]
    plan = {"key": key, "changes": {"duedate": "2026-11-11"},
            "comment": "의존 작업 지연으로 일정 조정", "why": "일정 조정"}

    monkeypatch.setattr(RequestArchitect, "node", lambda self: (lambda st: {
        "intent": Intent.MODIFY, "keywords": [key], "mentioned_keys": [key], "sufficient": True}))
    monkeypatch.setattr(WorkArchitect, "node", lambda self: (lambda st: {
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
    from app.agent.workflow.agents.work_architect import WorkArchitect
    r = WorkArchitect()
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
    from app.agent.workflow.agents.work_architect import WorkArchitect
    st = {"messages": [HumanMessage(content="CDC 도입")], "trace": [],
          "evidence": [{"key": "DL-118", "why": "소스 DB 부하로 중단됐던 선행 검토"}],
          "related_docs": [{"title": "CDC 설계 문서", "url": "https://conf/x"}]}
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "s", "type": "Task", "description": "<h3>배경</h3><p>x</p>"}]}
    got = WorkArchitect().apply(st, out)
    d = got["draft"]["items"][0]["description"]
    assert "References" not in d and d.count("<h3>참고</h3>") == 1
    assert "DL-118" in d and "https://conf/x" in d
    # 모델이 이미 '참고'를 적었으면 그 ul 에 **병합**되고, 이미 있는 키는 다시 붙지 않는다
    out2 = {"questions": [], "mode": "task", "rationale": "",
            "items": [{"summary": "s", "type": "Task",
                       "description": "<h3>참고</h3><ul><li>DL-118 — 이미 적음</li></ul>"}]}
    d2 = WorkArchitect().apply(st, out2)["draft"]["items"][0]["description"]
    assert d2.count("<h3>참고</h3>") == 1 and d2.count("DL-118") == 1
    assert "https://conf/x" in d2      # 없던 문서는 병합된다


def test_comment_only_change_plan_goes_through_approval(monkeypatch):
    """"이 내용 DL-x 에 댓글로 남겨줘" — 변경 필드 없이 댓글만도 승인→실행이 돼야 한다."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow import session
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.agents.work_architect import WorkArchitect
    from app.agent.tools import _ctx
    import app.agent.tools as T

    key = _ctx.client().search_issues(
        "statusCategory = indeterminate ORDER BY updated DESC", max_results=3)[0]["key"]
    plan = {"key": key, "changes": {}, "comment": "회의 결정: 다음 릴리스로 미룸", "why": ""}
    monkeypatch.setattr(RequestArchitect, "node", lambda self: (lambda st: {
        "intent": Intent.MODIFY, "keywords": [key], "mentioned_keys": [key], "sufficient": True}))
    monkeypatch.setattr(WorkArchitect, "node", lambda self: (lambda st: {
        "questions": [], "change_plan": dict(plan), "turns": 1, "draft": {}}))
    G.reset()


def test_comment_only_pending_uses_comment_action_semantics():
    from types import SimpleNamespace
    from app.agent.workflow.session import _shape

    base = {"approval_token": "token", "reply": "", "trace": [],
            "change_plan": {"key": "DL-9201", "changes": {}, "comment": "결정 공유"}}
    one = _shape("t", base, SimpleNamespace(next=("action_executor",)))
    assert one["pending"]["action"] == "add_ticket_comment"

    base["change_plan"] = {
        "keys": ["DL-9201", "DL-9202"], "changes": {}, "comment": "결정 공유",
        "comments": [{"key": "DL-9201", "body": "A"}, {"key": "DL-9202", "body": "B"}],
    }
    many = _shape("t", base, SimpleNamespace(next=("action_executor",)))
    assert many["pending"]["action"] == "add_ticket_comments"


def test_link_and_comment_pending_shows_every_approved_effect():
    from types import SimpleNamespace
    from app.agent.workflow.session import _shape

    base = {
        "approval_token": "primary", "comment_token": "secondary",
        "reply": "", "trace": [],
        "change_plan": {
            "key": "DL-100", "changes": {},
            "link": {"other": "DL-200", "relation": "Relates"},
            "comment": "관련 결정 기록",
        },
    }

    shaped = _shape("t", base, SimpleNamespace(next=("action_executor",)))

    assert shaped["pending"]["key"] == "DL-100"
    assert shaped["pending"]["changes"] == {"link": "Relates → DL-200"}
    assert shaped["pending"]["comment"] == "관련 결정 기록"


def test_latest_person_work_request_overrides_previous_ticket_context(monkeypatch):
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.portfolio_analyst import _current_person_work
    from app.agent.workflow.agents.request_architect import RequestArchitect
    import app.agent.tools.people_tools as people_tools

    state = {
        "messages": [HumanMessage(content="DL-9090 진행상황 알려줘"),
                     HumanMessage(content="잠깐 다른 얘기. @이다은이 지금 맡은 업무를 요약해줘")],
        "mentioned_keys": ["DL-9090"], "intent": Intent.PROGRESS,
    }
    patch = RequestArchitect().apply(state, {
        "intent": Intent.PROGRESS, "keywords": ["DL-9090"], "mentioned_keys": ["DL-9090"],
    })
    assert patch["intent"] == Intent.ACTIVITY
    assert patch["mentioned_keys"] == []

    fake = SimpleNamespace(invoke=lambda _args: {
        "resolved": "skcc.i2011", "ambiguous": False,
        "assigned": {"count": 1, "tickets": [{"key": "DL-9201", "summary": "writer PoC"}]},
    })
    monkeypatch.setattr(people_tools, "find_person", fake)
    snapshot = _current_person_work({**state, "intent": Intent.ACTIVITY})
    assert snapshot["user_id"] == "skcc.i2011"
    assert [row["key"] for row in snapshot["tickets"]] == ["DL-9201"]


def test_person_name_with_spaced_title_resolves_the_name_not_the_title(monkeypatch):
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.portfolio_analyst import _current_person_work
    import app.agent.tools.people_tools as people_tools

    seen = {}
    fake = SimpleNamespace(invoke=lambda args: (
        seen.update(args) or {
            "resolved": "skcc.i2011", "ambiguous": False,
            "assigned": {"count": 1, "tickets": [{"key": "DL-9201", "summary": "writer PoC"}]},
        }))
    monkeypatch.setattr(people_tools, "find_person", fake)
    state = {"messages": [HumanMessage(content="이다은 책임이 지금 맡고 있는 일 알려줘")],
             "intent": Intent.ACTIVITY}
    snapshot = _current_person_work(state)
    assert seen["name"] == "이다은"
    assert snapshot["user_id"] == "skcc.i2011"


def test_runtime_identity_is_minimal_verified_context_without_display_name(monkeypatch):
    from types import SimpleNamespace
    from app.agent.workflow import session
    from app.agent.tools import _ctx
    import app.infra.settings as settings

    session._IDENTITY_CACHE.update(at=0.0, val=None)
    monkeypatch.setattr(_ctx, "client", lambda: SimpleNamespace(
        current_user=lambda: {"name": "skcc.i2011", "displayName": "이다은 책임"}))
    monkeypatch.setattr(settings, "load_people", lambda: {"ETL": ["skcc.i2011"]})
    got = session._identity()
    assert "user_id: `skcc.i2011`" in got and "modules: ETL" in got
    assert "{{mention:skcc.i2011}}" in got
    assert "이다은" not in got
    session._IDENTITY_CACHE.update(at=0.0, val=None)


def test_daily_priority_snapshot_and_reply_keep_exactly_one_primary_ticket():
    from app.agent.workflow.agents.portfolio_analyst import _daily_priority_snapshot
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    material = """[권장 1순위] DL-9028 — 마감 구간과 Jira 우선순위를 함께 적용한 첫 항목
- DL-9028 "[ETL] Schema Registry 장애 대응" (To Do, 우선 P1-Critical, 마감 2026-08-14 · 마감 지남)
- DL-9029 "[ETL] 다음 후보" (To Do, 우선 P2-Major, 마감 2026-08-15)"""
    snapshot = _daily_priority_snapshot(material)
    assert snapshot == {
        "key": "DL-9028", "title": "[ETL] Schema Registry 장애 대응", "status": "To Do",
        "priority": "P1-Critical", "due": "2026-08-14", "overdue": True,
    }
    reply = ResultIntegrator()._run({"daily_priority_snapshot": snapshot})["reply"]
    assert "DL-9028" in reply and "DL-9029" not in reply
    assert "P1-Critical" in reply and "마감 2026-08-14" in reply

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
    티켓 본문·필드·댓글은 같은 실제 출처 아래 하위 발견으로 남는다."""
    from app.agent.workflow.agents.result_integrator import _dedupe_refs
    t = ("주기 [1]. 잡 [3]. 담당 [4].\n\n**참조**\n"
         "- [1] DL-9044 — 주기 변경 근거\n"
         "- [3] DL-9044 — 같은 티켓 다른 설명\n"
         "- [4] DL-9044 코멘트 (skcc.x1103, 2026-08-06) — 담당\n")
    out = _dedupe_refs(t)
    assert "잡 [1-b]" in out and "담당 [1-c]" in out
    assert out.count("[1] {{ticket-detail:DL-9044}}") == 1
    assert "- [1-a] 본문에서 주기 변경 근거" in out
    assert "- [1-c] 댓글(skcc.x1103, 2026-08-06)에서 담당" in out
    assert "[2]" not in out
    assert _dedupe_refs("참조 없는 답") == "참조 없는 답"
    # 문서는 canonical Markdown link 한 개로 보존해 UI와 복사본 모두에서 제목과 URL을 제공한다.
    t2 = ("값 [1].\n\n**참조**\n"
          "- [1] [데이터카탈로그] 특성 분석 (http://x/pages/1/문서) — 스키마 근거\n")
    o2 = _dedupe_refs(t2)
    assert "[1] [[데이터카탈로그] 특성 분석](http://x/pages/1/문서)" in o2, o2
    assert "- 문서 본문에서 스키마 근거" in o2, o2
