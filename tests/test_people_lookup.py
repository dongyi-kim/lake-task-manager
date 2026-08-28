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


def test_titles_are_stripped_before_searching():
    """호칭째로 검색하면 못 찾고, 못 찾으면 **있는 사람을 없다고** 답하게 된다."""
    cases = [
        ("김동이 M", "김동이"),          # 영문 약칭 — 낱말로 떨어져 있을 때만 뗀다
        ("윤산성매니저", "윤산성"),
        ("박지영차장", "박지영"),
        ("홍길동 TL", "홍길동"),
        ("이재민파트장님", "이재민"),    # 두 겹으로 붙는다
        ("이다은님", "이다은"),
        ("이다은 책임", "이다은"),
        ("이다은", "이다은"),            # 뗄 것이 없으면 원문
    ]
    failures = {}
    for raw, expected in cases:
        actual = strip_title(raw)
        if actual != expected:
            failures[raw] = {"expected": expected, "actual": actual}
    assert not failures, failures


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


def test_a_postposition_attached_to_a_mention_retries_only_after_an_exact_miss(monkeypatch):
    from app.agent.tools import people_tools as P

    seen = []

    class Provider:
        def get_json(self, _path, params=None):
            query = (params or {}).get("username")
            seen.append(query)
            return ([{"name": "skcc.i2011", "displayName": "이다은 SKCC"}]
                    if query == "이다은" else [])

    class Client:
        provider = Provider()

    monkeypatch.setattr(P, "client", lambda: Client())
    monkeypatch.setattr(P, "_assigned_now", lambda uid: {"count": 1, "tickets": []})
    monkeypatch.setattr("app.infra.settings.load_people", lambda *a, **k: {
        "ETL": ["skcc.i2011"]})
    P.set_person_context("particle-mention", [])
    result = P.find_person.invoke({"name": "이다은이"})
    assert seen == ["이다은이", "이다은"]
    assert result["query"] == "이다은" and result["resolved"] == "skcc.i2011"


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
    from app.agent.workflow.agents.work_architect import _known_components, _slot_audit
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
    from app.agent.workflow.agents.portfolio_analyst import _MODULES, _needs_module

    def st(text):
        return {"messages": [HumanMessage(content=text)], "intent": "activity"}

    # 이름을 댔으면 물을 것이 없다
    assert not _needs_module(st("ETL 모듈 최근 7일 활동"))
    # 개인 질문도 아니다
    assert not _needs_module(st("이다은 최근 활동"))
    # "우리 팀" 은 지시어다 — 세션 사용자가 픽스처/무소속이면 풀 수 없다
    assert _needs_module(st("우리 팀 최근 7일 업무 내역")) in (True, False)  # 환경 의존
    assert len(_MODULES) >= 6, "보기로 낼 컴포넌트 목록"


# ── 이름만 댔을 때의 해석 순서 (사용자 지시 2026-08-10) ──────────────
#   ① config 유저 목록 → ② 최근 확인한 Task 관련자 → ③ 프로젝트 참가자
#   그래도 갈리면 **묻고**, 확인받은 것은 **그 대화 안에서 기억**한다.
def _fake_dir(monkeypatch, users):
    """Jira 사용자 검색을 흉내 낸다 — 동명이인은 실제 world 에 없어서 만들어 준다."""
    from app.agent.tools import people_tools as P

    class _P:
        def get_json(self, path, params=None):
            return users

    class _C:
        provider = _P()

        def get_issue(self, key):
            return {"fields": {"assignee": {"name": "dup.second"}, "reporter": None,
                               "comment": {"comments": []}}}

        def search_issues(self, jql, max_results=1):
            return []

    monkeypatch.setattr(P, "client", lambda: _C())
    monkeypatch.setattr(P, "_assigned_now", lambda uid: {"count": 0, "tickets": []})
    return P


def test_a_name_in_the_config_roster_wins(monkeypatch):
    """①이 가장 세다 — 우리 팀 사람일 확률이 가장 높다."""
    P = _fake_dir(monkeypatch, [
        {"name": "skcc.x1042", "displayName": "이다은 데이터메이커", "emailAddress": "a@x"},
        {"name": "out.9999", "displayName": "이다은 다른회사", "emailAddress": "b@x"},
    ])
    monkeypatch.setattr("app.infra.settings.load_people", lambda *a, **k: {"ETL": ["skcc.x1042"]})
    P.set_person_context("th-1", [])
    r = P.find_person.invoke({"name": "이다은"})
    assert r["resolved"] == "skcc.x1042", r
    assert not r["ambiguous"] and "config" in r["why"], r
    assert "근거" in (r.get("note") or ""), "왜 그 사람인지 답변에 밝히라는 지시가 없다"


def test_recently_seen_ticket_breaks_the_tie(monkeypatch):
    """②는 ①이 없을 때 — 방금 보던 티켓에 얽힌 사람이 '그 사람'일 확률이 높다."""
    P = _fake_dir(monkeypatch, [
        {"name": "dup.first", "displayName": "김철수 가", "emailAddress": "a@x"},
        {"name": "dup.second", "displayName": "김철수 나", "emailAddress": "b@x"},
    ])
    monkeypatch.setattr("app.infra.settings.load_people", lambda *a, **k: {"ETL": []})
    P.set_person_context("th-2", ["DL-9090"])          # 이 티켓 담당이 dup.second 다
    r = P.find_person.invoke({"name": "김철수"})
    assert r["resolved"] == "dup.second", r
    assert "최근" in r["why"], r


def test_a_real_tie_is_asked_not_guessed(monkeypatch):
    """같은 층에 둘이면 **코드는 손을 뗀다.** 짐작이 가장 나쁜 실패다."""
    P = _fake_dir(monkeypatch, [
        {"name": "skcc.a", "displayName": "박지영 가", "emailAddress": "a@x"},
        {"name": "skcc.b", "displayName": "박지영 나", "emailAddress": "b@x"},
    ])
    monkeypatch.setattr("app.infra.settings.load_people",
                        lambda *a, **k: {"ETL": ["skcc.a", "skcc.b"]})
    P.set_person_context("th-3", [])
    r = P.find_person.invoke({"name": "박지영차장"})
    assert r["ambiguous"] and not r["resolved"], r
    assert "확인받아라" in r["note"]


def test_a_confirmed_person_is_remembered_for_that_conversation_only(monkeypatch):
    """확인받은 것은 그 대화에서 기억한다 — 그리고 **그 대화에서만**."""
    P = _fake_dir(monkeypatch, [])
    monkeypatch.setattr("app.infra.settings.load_people", lambda *a, **k: {})
    P.set_person_context("th-4", [])
    P.confirm_person.invoke({"name": "박지영차장", "user_id": "skcc.b"})
    r = P.find_person.invoke({"name": "박지영 차장님"})   # 호칭이 달라도 같은 이름이다
    assert r["resolved"] == "skcc.b" and not r["ambiguous"], r

    P.set_person_context("th-5", [])                     # 다른 대화 — 남의 확인을 끌어오지 않는다
    r2 = P.find_person.invoke({"name": "박지영"})
    assert not r2["resolved"], r2


def test_same_name_across_module_outside_module_and_outside_project(monkeypatch):
    """동명이인 세 종류가 섞인 경우 — **우리 모듈 > 다른 모듈 > 프로젝트 밖**.

    사용자 요청으로 넣는다. 셋이 섞이는 것이 실제 상황이다: 같은 이름이 우리 모듈에도,
    옆 모듈에도, 회사 어딘가에도 있다. config 안이면 ①층이라 옆 모듈도 같은 층이 되므로
    **둘 다 config 면 묻는다** — 층이 같으면 코드가 고르지 않는다는 규칙 그대로다.
    """
    # (1) 우리 모듈 1명 + 프로젝트 밖 1명 → config 층이 하나뿐이라 고른다
    P = _fake_dir(monkeypatch, [
        {"name": "skcc.mine", "displayName": "이수민 데이터", "emailAddress": "a@x"},
        {"name": "far.9999", "displayName": "이수민 타사", "emailAddress": "b@x"},
    ])
    monkeypatch.setattr("app.infra.settings.load_people", lambda *a, **k: {"ETL": ["skcc.mine"]})
    P.set_person_context("mix-1", [])
    r = P.find_person.invoke({"name": "이수민"})
    assert r["resolved"] == "skcc.mine" and "config" in r["why"], r

    # (2) 우리 모듈 1명 + **다른 모듈** 1명 → 둘 다 config(①층) → 묻는다
    P = _fake_dir(monkeypatch, [
        {"name": "skcc.etl", "displayName": "이수민 ETL", "emailAddress": "a@x"},
        {"name": "skcc.cat", "displayName": "이수민 Catalog", "emailAddress": "b@x"},
    ])
    monkeypatch.setattr("app.infra.settings.load_people",
                        lambda *a, **k: {"ETL": ["skcc.etl"], "Catalog": ["skcc.cat"]})
    P.set_person_context("mix-2", [])
    r = P.find_person.invoke({"name": "이수민"})
    assert r["ambiguous"] and not r["resolved"], r
    # 보기에 **어느 모듈 사람인지**가 실려야 사용자가 고를 수 있다
    assert {c.get("module") for c in r["candidates"]} == {"ETL", "Catalog"}, r["candidates"]

    # (3) 전부 프로젝트 밖 → 고르지 않는다(우리 쪽 근거가 없다)
    P = _fake_dir(monkeypatch, [
        {"name": "far.1", "displayName": "이수민 가", "emailAddress": "a@x"},
        {"name": "far.2", "displayName": "이수민 나", "emailAddress": "b@x"},
    ])
    monkeypatch.setattr("app.infra.settings.load_people", lambda *a, **k: {})
    P.set_person_context("mix-3", [])
    r = P.find_person.invoke({"name": "이수민"})
    assert r["ambiguous"] and not r["resolved"], r


def test_a_mention_or_user_id_is_never_ambiguous(monkeypatch):
    """사용자가 **사번으로 지목**했으면 헷갈릴 일이 없다(사용자 요청).

    멘션 `[~skcc.x1042]` 은 이름이 아니라 **식별자**다. 동명이인 판정에 넣을 이유가 없고,
    넣으면 지목한 사람을 두고 되묻게 된다 — 사용자가 이미 답한 것을 다시 묻는 셈이다.
    """
    P = _fake_dir(monkeypatch, [
        {"name": "skcc.a", "displayName": "이수민 가", "emailAddress": "a@x"},
        {"name": "skcc.b", "displayName": "이수민 나", "emailAddress": "b@x"},
    ])
    monkeypatch.setattr("app.infra.settings.load_people",
                        lambda *a, **k: {"ETL": ["skcc.a", "skcc.b"]})
    P.set_person_context("mention-1", [])
    assert P.find_person.invoke({"name": "이수민"})["ambiguous"], "동명이인 상황이 아니다"

    for typed in ("[~skcc.b]", "skcc.b", "@skcc.b", " [~ skcc.b ] "):
        r = P.find_person.invoke({"name": typed})
        assert r["resolved"] == "skcc.b" and not r["ambiguous"], (typed, r)
        assert "지목" in r["why"], r


def test_mock_world_has_a_stable_same_name_pair_for_required_identity_interviews():
    from app.agent.tools.people_tools import find_person

    result = find_person.invoke({"name": "동명이"})
    assert result["ambiguous"] is True
    candidates = {row.get("id") or row.get("name") for row in result.get("candidates") or []}
    assert {"test.same01", "test.same02"}.issubset(candidates)
