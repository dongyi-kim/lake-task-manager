# -*- coding: utf-8 -*-
"""플레이북 후검증 — **내보내기 직전의 최소선**(사용자 지시 2026-08-10).

프롬프트에 "이렇게 써라"를 적어 두면 대체로 그렇게 나온다. 문제는 '대체로'다 — 같은
요청이 어떤 날은 연표만, 어떤 날은 현재 상태까지 나온다. 실사용에서 걸린 결함의 상당수가
모델의 실력이 아니라 **흔들림**이었다. 흔들림은 지시로 못 잡으니 **잴 수 있는 것은 코드가
재고**, 못 지켰으면 답 아래에 그 사실을 붙인다(조용히 고치지 않는다 — 무엇이 부족했는지
아무도 모르게 된다).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

from app.agent.workflow import postcheck as P   # noqa: E402

_HIST_OK = ("## 현재 상태\n| 항목 | 값 | 근거 |\n|---|---|---|\n| 적재주기 | 30분 | [1] |\n\n"
            "## 연표\n| 날짜 | 사건 | 근거 |\n|---|---|---|\n| 2026-05-22 | 2시간→30분 | [1] |\n\n"
            "**참조**\n[1] DL-9044 — 적재주기 변경")


def test_history_needs_both_now_and_the_timeline():
    """이력 답변의 최소선 — '지금 어떤가'와 '어떻게 왔나'가 **둘 다**.

    실사용 지적 두 번: 연표만 내고 현재 상태가 없었다.
    """
    assert P.check({"playbook": "history"}, _HIST_OK) == []
    only_timeline = ("| 날짜 | 사건 | 근거 |\n|---|---|---|\n| 2026-05-22 | 변경 | [1] |\n\n"
                     "**참조**\n[1] DL-9044 — 변경")
    bad = P.check({"playbook": "history"}, only_timeline)
    assert bad and "현재" in bad[0]


def test_history_without_references_is_flagged():
    """티켓을 언급했으면 **확인할 길**이 있어야 한다."""
    no_ref = "## 현재 상태\n| 항목 | 값 |\n|---|---|\n| 주기 | 30분 |\n\nDL-9044 에서 바뀜"
    bad = P.check({"playbook": "history"}, no_ref)
    assert any("참조" in b for b in bad), bad


def test_find_tickets_must_show_a_table_or_say_zero():
    """조건 조회 — 표로 내거나, 0건임을 기준과 함께 밝히거나."""
    assert P.check({"playbook": "find_tickets"},
                   "| 키 | 제목 |\n|---|---|\n| DL-1 | x |") == []
    assert P.check({"playbook": "find_tickets"}, "조건에 맞는 티켓이 0건입니다(기준: 3개월)") == []
    assert P.check({"playbook": "find_tickets"}, "몇 건 있습니다.")


def test_draft_flow_needs_something_to_act_on():
    """초안도 질문도 없는 답은 **먹통**이다(실측 2회). 되묻는 턴은 정당하다."""
    assert P.check({"playbook": "task_create", "draft": {"items": [{"summary": "x"}]}}, "초안") == []
    assert P.check({"playbook": "task_create", "questions": [{"question": "언제?"}]}, "물음") == []
    assert P.check({"playbook": "task_create", "draft": {"items": []}}, "준비했습니다")


def test_no_playbook_means_no_opinion():
    """무엇을 하려는 요청인지 모르는 턴에 형식을 강요하면 그게 더 나쁘다."""
    assert P.check({"playbook": ""}, "아무 말") == []
    assert P.check({}, "") == []


def test_pending_card_still_gets_draft_checks_without_playbook_metadata():
    """API 반환 shape에는 playbook이 없어도 pending 카드와 마지막 요청은 남는다."""
    from langchain_core.messages import HumanMessage
    state = {"messages": [HumanMessage(content="단계별 Sub-Task 로 나눠줘")],
             "pending": {"items": [{"summary": "x", "children": []}]}}
    bad = P.check(state, "### 하위 작업\n1. 설계\n2. 구현")
    assert any("자식이 0건" in x for x in bad), bad


def test_pending_flat_children_are_counted_separately_from_parent_items():
    """승인 API는 부모와 자식을 분리한다 — items 안에 children이 없는 것은 정상이다."""
    from langchain_core.messages import HumanMessage
    state = {"messages": [HumanMessage(content="단계별 Sub-Task 로 나눠줘")],
             "pending": {"items": [{"summary": "부모"}],
                         "children": [{"parent_index": 0, "summary": "설계"},
                                      {"parent_index": 0, "summary": "구현"}]}}
    assert not P.check(state, "### 하위 작업\n1. 설계\n2. 구현")


def test_the_note_is_visible_and_bounded():
    """붙이는 경고는 **보이되 답을 덮지 않는다** — 최대 4줄."""
    note = P.note(["a", "b", "c", "d", "e", "f"])
    assert note.startswith("\n\n> ⚠")
    assert "결과 검증에서 누락 가능성" in note
    assert "우리 형식 기준" not in note
    assert note.count("\n> - ") <= 4
    assert P.note([]) == ""


def test_assignment_completion_accepts_complete_people_list_without_table():
    state = {"playbook": "find_tickets", "assignment_completion": {
        "kind": "incomplete_assignees",
        "people": [{"tickets": [{"key": "DL-1"}]}, {"tickets": [{"key": "DL-2"}]}],
        "unassigned": [],
    }}
    assert P.check(state, "- 김동이 — DL-1\n- 박지영 — DL-2") == []
    assert "DL-2" in P.check(state, "- 김동이 — DL-1")[0]


def test_assignment_completion_reply_uses_machine_badges_without_excluded_noise():
    from app.agent.workflow.agents.result_integrator import _assignment_completion_reply
    data = {
        "kind": "incomplete_assignees", "topic": "보안 필수교육 수강",
        "totalSubtasks": 14, "doneSubtasks": 10, "incompleteSubtasks": 4,
        "parents": [{"key": "DL-9100", "total": 14, "done": 10,
                     "incomplete": [{"key": f"DL-{n}"} for n in range(9101, 9105)]}],
        "people": [
            {"id": f"skcc.x{n}", "name": f"작업자{n}",
             "tickets": [{"key": f"DL-{9100 + n}"}]}
            for n in range(1, 5)
        ],
        "unassigned": [],
    }
    reply = _assignment_completion_reply(data)
    assert reply.count("{{ticket-list:") == 4
    assert "{{ticket-detail:DL-9100}}" in reply
    assert "레지스트리" not in reply and "권장" not in reply
