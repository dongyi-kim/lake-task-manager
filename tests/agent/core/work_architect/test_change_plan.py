# -*- coding: utf-8 -*-
"""Focused existing-ticket change-plan regressions."""

import os

import pytest

os.environ.setdefault("JIRA_ENV", "mock")
pytest.importorskip("langgraph", reason="requirements-agent.txt 미설치")

from app.agent import approval  # noqa: E402
from app.agent.workflow.agents.work_architect import _change_plan  # noqa: E402
from app.agent.workflow.state import Intent  # noqa: E402


@pytest.fixture(autouse=True)
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    import app.infra.settings as settings
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    approval.clear()
    yield
    approval.clear()


def _msg(text, **extra):
    from langchain_core.messages import HumanMessage
    return {"messages": [HumanMessage(content=text)], **extra}


def test_comment_without_content_gets_a_deterministic_required_question():
    state = _msg("DL-9090에 댓글 남겨줘. 내용은 알아서",
                 intent=Intent.MODIFY, mentioned_keys=["DL-9090"])
    plan, questions = _change_plan(
        state,
        {"change": {"key": "DL-9090", "comment": "요청하신 대로 처리하겠습니다."},
         "rationale": ""}, [], [],
    )
    assert not plan
    assert questions and questions[0]["field"] == "comment"
    assert questions[0]["required_input"] is True


def test_ambiguous_assignee_name_lists_exact_usernames_instead_of_invalid_id_error():
    state = _msg("DL-9090 담당자를 동명이로 바꿔줘. 알아서",
                 intent=Intent.MODIFY, mentioned_keys=["DL-9090"])
    out = {"change": {"key": "DL-9090", "assignee": "동명이"}, "rationale": ""}
    plan, questions = _change_plan(state, out, [], [])
    options = " ".join(questions[0]["options"] if questions else [])
    assert not plan
    assert "test.same01" in options and "test.same02" in options
    assert "존재하지 않는 사번" not in questions[0]["question"]


def test_done_change_plan_requires_reopen_as_a_separate_approval(monkeypatch):
    class _DoneClient:
        def ticket_badge(self, key):
            return {"key": key, "type": "Task", "status": "Resolved",
                    "statusCategory": "done"}

        def transitions(self, key):
            return [{"id": "4", "name": "Reopen Issue", "to": "Reopened",
                     "toCategory": "todo"}]

        def get_issue(self, key):
            return {"key": key, "fields": {
                "issuetype": {"name": "Task", "subtask": False},
                "status": {"name": "Resolved", "statusCategory": {"key": "done"}},
                "summary": "완료된 작업", "priority": {"name": "P3-Minor"}}}

        def issue_comments(self, key, limit):
            return []

    cli = _DoneClient()
    monkeypatch.setattr("app.agent.tools._ctx.client", lambda: cli)
    monkeypatch.setattr("app.agent.tools.search_tools.client", lambda: cli)
    monkeypatch.setattr("app.agent.tools.write_tools.client", lambda: cli)
    state = _msg("DL-9 우선순위를 P1으로 올려줘", intent=Intent.MODIFY,
                 mentioned_keys=["DL-9"])
    plan, questions = _change_plan(
        state, {"change": {"key": "DL-9", "priority": "P1-Critical"}, "rationale": ""},
        [], [])
    assert not plan
    assert questions and "Done" in questions[0]["question"]
    assert "Reopened" in questions[0]["options"][0]
    assert "새 승인" in questions[0]["options"][0]


def test_done_comment_only_change_plan_is_allowed(monkeypatch):
    class _DoneClient:
        def ticket_badge(self, key):
            return {"key": key, "type": "Task", "statusCategory": "done"}

        def get_issue(self, key):
            return {"key": key, "fields": {"issuetype": {"name": "Task"},
                                             "status": {"statusCategory": {"key": "done"}}}}

        def issue_comments(self, key, limit):
            return []

    cli = _DoneClient()
    monkeypatch.setattr("app.agent.tools._ctx.client", lambda: cli)
    monkeypatch.setattr("app.agent.tools.search_tools.client", lambda: cli)
    state = _msg("DL-9에 완료 후 회고 댓글 남겨줘", intent=Intent.MODIFY,
                 mentioned_keys=["DL-9"])
    plan, questions = _change_plan(
        state, {"change": {"key": "DL-9", "comment": "완료 후 회고"}, "rationale": ""},
        [], [])
    assert plan["comment"] == "완료 후 회고" and not plan["changes"]
    assert not questions


def test_link_and_comment_survive_work_change_plan_assembly():
    state = _msg(
        "ACME-10을 ACME-20과 연결하고 관련 결정 댓글도 남겨줘",
        intent=Intent.MODIFY, mentioned_keys=["ACME-10", "ACME-20"],
        continuation_contract={
            "version": "continuation.v1",
            "root_request": "ACME-10을 ACME-20과 연결하고 관련 결정 댓글도 남겨줘",
            "intent": "modify", "action": "mixed",
            "target_keys": ["ACME-10", "ACME-20"],
            "outcome_ids": ["link", "comment"], "decisions": [],
        },
    )
    plan, questions = _change_plan(state, {
        "change": {
            "key": "ACME-10",
            "link": {"other": "ACME-20", "relation": "Relates"},
            "comment": "관련 결정 기록",
        },
        "rationale": "",
    }, [], [])

    assert not questions
    assert plan["key"] == "ACME-10"
    assert plan["link"] == {"other": "ACME-20", "relation": "Relates"}
    assert "관련 결정" in plan["comment"]


def test_duedate_change_against_the_users_word_is_flagged():
    """"미뤄 줘"인데 현재 마감보다 **앞** 날짜면 확인을 요청한다.

    실측: DL-101(마감 2026-08-27)에 "다음 주 금요일로 미뤄 줘" → 2026-08-14 를 아무 말
    없이 카드에 올렸다. 사용자가 현재 마감을 기억하고 말하는 일은 드물다.
    """
    import re

    from app.agent.workflow.agents import work_architect as R

    got = {}

    class _FakeTicket:
        def invoke(self, args):
            return {"key": args["key"], "duedate": "2026-08-27", "summary": "x"}

    real = R._relative_due
    R._relative_due = lambda t: "2026-08-14"
    try:
        import app.agent.tools as T
        keep = T.BY_NAME.get("get_ticket")
        T.BY_NAME["get_ticket"] = _FakeTicket()
        try:
            state = {"intent": "modify", "messages": [], "request_text": "",
                     "mentioned_keys": ["DL-101"]}
            from langchain_core.messages import HumanMessage
            state["messages"] = [HumanMessage(content="DL-101 마감을 다음 주 금요일로 미뤄줘")]
            out = {"change": {"key": "DL-101", "duedate": "2026-08-14"}}
            plan, _qs = R._change_plan(state, out, [], [])
            got = plan
        finally:
            if keep is not None:
                T.BY_NAME["get_ticket"] = keep
    finally:
        R._relative_due = real

    assert got.get("key") == "DL-101", got
    assert re.search(r"확인 필요.*2026-08-27.*반대", got.get("why") or "", re.S), got.get("why")


def test_an_unrelated_capability_notice_is_stripped_from_the_reason_line():
    """승인 카드의 근거 줄은 사용자가 **판단하는 자리**다 — 묻지 않은 안내가 있으면 안 된다.

    실측(CMTB1): 일괄 코멘트 계획의 why 가 "삭제는 지원되지 않음. 상태를 닫음으로 전이…"
    였다. 삭제 요청이 아니었는데 프롬프트의 예외 안내를 모델이 옮겨 적은 것이다.
    """
    from app.agent.workflow.agents.work_architect import _change_plan
    out = {"rationale": "(삭제는 지원되지 않는다 — 상태 전이(닫음)나 보관 라벨을 대안으로 안내)",
           "change": {}}
    plan = {"keys": ["DL-9090"], "changes": {},
            "why": "삭제는 지원되지 않음. 상태를 닫음으로 전이하세요."}
    st = _msg("ETL 티켓 전부에 상태 점검 코멘트 남겨줘")     # 삭제 이야기가 없다
    got, _qs = _change_plan(st, out, [], [])
    assert "삭제는 지원되지" not in (out.get("rationale") or ""), out.get("rationale")

    # 진짜 삭제 요청이면 안내가 남아야 한다 — 지우는 가드가 필요한 안내까지 지우면 안 된다
    out2 = {"rationale": "(삭제는 지원되지 않는다 — 상태 전이(닫음)나 보관 라벨을 대안으로 안내)",
            "change": {}}
    _change_plan(_msg("DL-9090 삭제해줘"), out2, [], [])
    assert "삭제는 지원되지" in (out2.get("rationale") or "")
