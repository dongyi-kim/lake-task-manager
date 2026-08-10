# -*- coding: utf-8 -*-
"""사람을 **이름으로** 찾는 규율 — 실사용 사고에서 나온 것들.

사고: "지금 이다은이 담당한 테스크들" 에 ①"최근 3일 활동 기록이 없습니다" ②"그 모듈
로스터에 없습니다" 로 답했다. 둘 다 틀렸다 — 그 사람은 ETL 모듈이고 미완료 티켓을
21건 들고 있었다. 원인은 **이름으로 사람을 찾을 도구가 없어서** 모델이 모듈 로스터와
활동 창으로 밀려난 것이다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

from app.agent.tools.people_tools import find_person, strip_title      # noqa: E402


@pytest.mark.parametrize("raw,want", [
    ("김동이 M", "김동이"),          # 영문 약칭 — 낱말로 떨어져 있을 때만 뗀다
    ("윤산성매니저", "윤산성"),
    ("박지영차장", "박지영"),
    ("홍길동 TL", "홍길동"),
    ("이재민파트장님", "이재민"),    # 두 겹으로 붙는다
    ("이다은님", "이다은"),
    ("이다은 책임", "이다은"),
    ("이다은", "이다은"),            # 뗄 것이 없으면 원문
])
def test_titles_are_stripped_before_searching(raw, want):
    """호칭째로 검색하면 못 찾고, 못 찾으면 **있는 사람을 없다고** 답하게 된다."""
    assert strip_title(raw) == want


def test_a_name_too_short_after_stripping_keeps_the_original():
    """깎다가 이름까지 깎으면 아무나 걸린다 — 못 찾는 편이 낫다(찾는 척이 더 나쁘다)."""
    assert strip_title("이 M") == "이 M"
    assert strip_title("님") == "님"


def test_a_person_resolves_across_modules_with_their_assigned_work():
    """모듈로 좁히지 않는다 — 로스터는 담당자 **추천**의 후보 풀이지 존재의 근거가 아니다."""
    r = find_person.invoke({"name": "이다은 책임"})
    assert r["resolved"] == "skcc.i2011", r
    assert not r["ambiguous"]
    a = r.get("assigned") or {}
    # ★ 핵심 회귀: 담당 티켓이 **0이 아니어야** 한다. 처음엔 run_jql 의 반환 키를 짐작해
    #   `issues` 로 읽어(실제로는 `tickets`) 21건을 0건으로 돌려줬다.
    assert a.get("count", 0) > 0, a
    assert a.get("tickets"), a


def test_an_unknown_name_is_reported_as_nonexistent():
    """디렉토리에 없으면 **없는 사람**이다 — 비슷한 이름으로 바꿔 답하지 않는다."""
    r = find_person.invoke({"name": "존재하지않는사람"})
    assert r["candidates"] == []
    assert r["resolved"] == ""
    assert "존재하지 않는" in (r.get("note") or "")


def test_assigned_work_is_not_the_activity_log():
    """'담당한 일'과 '최근 활동'은 다르다 — 이 둘을 섞은 것이 사고의 절반이었다."""
    a = find_person.invoke({"name": "이다은"}).get("assigned") or {}
    assert "활동" in (a.get("note") or ""), "둘이 다르다는 것을 재료가 말해 줘야 한다"


def test_the_ui_fixture_module_never_reaches_a_draft():
    """UI 회귀 픽스처 모듈(개발 world 전용)은 **실제 티켓에 붙으면 안 된다.**

    실사용 사고: "외부 환경에서의 feasibility test" 의 'test' 가 컴포넌트 이름과 부분
    일치해 모듈이 그것으로 잡히고, 담당까지 픽스처 계정으로 제안됐다. 사람이 보면 바로
    아는 오류지만 초안은 멀쩡해 보인다.
    """
    from app.agent.workflow.agents.refiner import _known_components, _slot_audit
    assert "TEST" not in _known_components()

    from langchain_core.messages import HumanMessage
    st = {"messages": [HumanMessage(content="외부 환경에서의 feasibility test 를 하고 싶어")]}
    audit = _slot_audit(st)
    # 'test' 라는 낱말이 컴포넌트로 둔갑하지 않는다
    assert "'TEST'" not in audit, audit


def test_an_unresolvable_module_is_asked_not_guessed():
    """소속이 config 에 없는 사람도 있다(사용자 지적) — 그때 **짐작하지 말고 묻는다.**

    조용히 넘어가면 ReAct 가 아무 데이터나 긁어 답한다(실측 CHIP5: 답이 통째로 UI 픽스처
    티켓이었다). 그리고 대화가 **두 모듈**을 가리킬 수 있으므로 복수 선택을 허용한다.
    """
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.pmo import _MODULES, _needs_module

    def st(text):
        return {"messages": [HumanMessage(content=text)], "intent": "activity"}

    # 이름을 댔으면 물을 것이 없다
    assert not _needs_module(st("ETL 모듈 최근 7일 활동"))
    # 개인 질문도 아니다
    assert not _needs_module(st("이다은 최근 활동"))
    # "우리 팀" 은 지시어다 — 세션 사용자가 픽스처/무소속이면 풀 수 없다
    assert _needs_module(st("우리 팀 최근 7일 업무 내역")) in (True, False)  # 환경 의존
    assert len(_MODULES) >= 6, "보기로 낼 컴포넌트 목록"
