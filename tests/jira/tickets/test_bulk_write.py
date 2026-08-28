# -*- coding: utf-8 -*-
"""여러 티켓을 한 번에 **고치기 / 코멘트 달기** — 검증·권한·승인.

생성(bulk/create)과 같은 규율을 지키는지가 전부다: 화면을 믿지 않고 서버가 다시 검증하고,
편집 권한은 editmeta 가 판정하며, 에이전트는 승인 토큰 없이 아무것도 못 쓴다.
Jira 에는 롤백이 없으므로 **하나라도 어긋나면 시작을 안 한다**(부분 실행이 가장 나쁘다).
"""
import os
import sys

import pytest

os.environ.setdefault("JIRA_ENV", "mock")

from app.domain.bulk import (MAX_ITEMS, validate_bulk_comment,  # noqa: E402
                             validate_bulk_update)


class _Lookup:
    """실값 조회기의 최소 구현 — validate_* 가 어떤 메서드를 쓰는지 드러낸다."""
    def __init__(self, keys=("DL-1", "DL-2"), editable=("DL-1", "DL-2"), done=()):
        self._keys, self._editable, self._done = set(keys), set(editable), set(done)

    def badge(self, key):
        return ({"key": key, "type": "Task",
                 "statusCategory": "done" if key in self._done else "todo"}
                if key in self._keys else None)

    def may_edit(self, key):
        return key in self._editable

    def priorities(self):
        return ["P1-Critical", "P2-Major", "P3-Minor"]

    def components(self):
        return ["ETL", "Catalog"]

    def user_exists(self, uid):
        return uid in ("skcc.x1042", "skcc.x1103")


# ── bulk update ────────────────────────────────────────────────────
def test_update_accepts_different_changes_per_ticket():
    r = validate_bulk_update([{"key": "DL-1", "changes": {"priority": "P2-Major"}},
                              {"key": "DL-2", "changes": {"assignee": "skcc.x1042"}}], _Lookup())
    assert r["ok"], r["errors"]


def test_update_canonicalizes_values_instead_of_rejecting_case():
    """'p2-major' 를 반려하면 사용자가 대소문자를 디버깅하게 된다 — 등록 표기로 맞춰 준다."""
    items = [{"key": "DL-1", "changes": {"priority": "p2-major", "components": ["etl"]}}]
    r = validate_bulk_update(items, _Lookup())
    assert r["ok"], r["errors"]
    assert items[0]["changes"]["priority"] == "P2-Major"
    assert items[0]["changes"]["components"] == ["ETL"]


def test_update_refuses_fields_that_are_not_editable_here():
    """status·epic 같은 것은 전용 경로(전이·Epic Link)가 있다 — 여기서 조용히 받으면 안 된다."""
    r = validate_bulk_update([{"key": "DL-1", "changes": {"status": "Done"}}], _Lookup())
    assert not r["ok"] and "바꿀 수 없는 필드" in r["errors"][0]["message"]


def test_update_refuses_unknown_ticket_and_no_permission():
    lk = _Lookup(keys=("DL-1", "DL-2"), editable=("DL-1",))
    r = validate_bulk_update([{"key": "DL-9999", "changes": {"summary": "x"}}], lk)
    assert not r["ok"] and "찾을 수 없" in r["errors"][0]["message"]
    r2 = validate_bulk_update([{"key": "DL-2", "changes": {"summary": "x"}}], lk)
    assert not r2["ok"] and "권한" in r2["errors"][0]["message"]


def test_update_refuses_done_but_comment_still_accepts_done():
    lk = _Lookup(done=("DL-1",))
    changed = validate_bulk_update(
        [{"key": "DL-1", "changes": {"summary": "바뀐 제목"}}], lk)
    assert not changed["ok"] and "완료된 티켓" in changed["errors"][0]["message"]
    assert validate_bulk_comment([{"key": "DL-1", "body": "완료 후 기록"}], lk)["ok"]


def test_update_refuses_the_same_ticket_twice():
    """같은 티켓에 두 변경이 오면 나중 것이 앞을 덮는다 — 조용한 덮어쓰기는 사고다."""
    r = validate_bulk_update([{"key": "DL-1", "changes": {"summary": "a"}},
                              {"key": "DL-1", "changes": {"summary": "b"}}], _Lookup())
    assert not r["ok"] and "두 번" in r["errors"][0]["message"]


def test_update_refuses_empty_changes_and_bad_values():
    lk = _Lookup()
    assert not validate_bulk_update([{"key": "DL-1", "changes": {}}], lk)["ok"]
    assert not validate_bulk_update(
        [{"key": "DL-1", "changes": {"assignee": "ghost.x9"}}], lk)["ok"]
    assert not validate_bulk_update(
        [{"key": "DL-1", "changes": {"labels": "문자열"}}], lk)["ok"]


def test_update_has_the_same_batch_ceiling_as_create():
    rows = [{"key": f"DL-{i}", "changes": {"summary": "x"}} for i in range(MAX_ITEMS + 1)]
    assert not validate_bulk_update(rows, None)["ok"]


# ── bulk comment ───────────────────────────────────────────────────
def test_comment_requires_a_real_ticket_and_a_body():
    lk = _Lookup()
    assert validate_bulk_comment([{"key": "DL-1", "body": "회의 결과"}], lk)["ok"]
    assert not validate_bulk_comment([{"key": "DL-1", "body": "  "}], lk)["ok"]
    assert not validate_bulk_comment([{"key": "DL-404", "body": "x"}], lk)["ok"]


# ── 에이전트 도구: 승인 없이는 아무것도 못 쓴다 ────────────────────
pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

from app.agent import approval, tools as T                       # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    approval.clear()
    yield
    approval.clear()


def test_bulk_tools_are_registered_for_writing():
    assert {"update_tickets", "add_ticket_comments"} <= {t.name for t in T.WRITE_TOOLS}


def test_bulk_update_tool_refuses_without_approval():
    r = T.BY_NAME["update_tickets"].invoke(
        {"items": [{"key": "DL-101", "changes": {"priority": "P2-Major"}}],
         "approval_token": "지어낸토큰"})
    assert r["ok"] is False and "승인" in r["error"]


def test_bulk_update_tool_applies_every_row_after_approval():
    rows = [{"key": "DL-101", "changes": {"priority": "P2-Major"}},
            {"key": "DL-102", "changes": {"labels": ["needs-review"]}}]
    tok = approval.stage("t1", "update_tickets", {"items": rows})
    approval.approve(tok, "t1")
    r = T.BY_NAME["update_tickets"].invoke({"items": rows, "approval_token": tok})
    assert r["ok"], r
    assert [u["key"] for u in r["updated"]] == ["DL-101", "DL-102"]


def test_bulk_comment_tool_applies_every_row_after_approval():
    rows = [{"key": "DL-101", "body": "<p>회의 결과 공유합니다.</p>"},
            {"key": "DL-102", "body": "<p>같은 건으로 진행합니다.</p>"}]
    tok = approval.stage("t1", "add_ticket_comments", {"items": rows})
    approval.approve(tok, "t1")
    r = T.BY_NAME["add_ticket_comments"].invoke({"items": rows, "approval_token": tok})
    assert r["ok"], r
    assert [c["key"] for c in r["created"]] == ["DL-101", "DL-102"]


def test_a_token_for_a_different_action_is_not_accepted():
    """update 토큰으로 코멘트를 달 수 있으면 토큰은 그냥 '쓰기 허가증'이 된다."""
    rows = [{"key": "DL-101", "body": "x"}]
    tok = approval.stage("t1", "update_tickets", {"items": rows})
    approval.approve(tok, "t1")
    r = T.BY_NAME["add_ticket_comments"].invoke({"items": rows, "approval_token": tok})
    assert r["ok"] is False


def test_nothing_is_written_when_one_row_is_invalid():
    """부분 실행이 가장 나쁘다 — 하나가 규칙에 어긋나면 시작을 안 한다."""
    rows = [{"key": "DL-101", "changes": {"priority": "P2-Major"}},
            {"key": "DL-99999", "changes": {"priority": "P2-Major"}}]
    tok = approval.stage("t1", "update_tickets", {"items": rows})
    approval.approve(tok, "t1")
    r = T.BY_NAME["update_tickets"].invoke({"items": rows, "approval_token": tok})
    assert r["ok"] is False and not r["updated"]
    assert approval.peek(tok), "검증에서 막혔으면 토큰은 아직 살아 있어야 한다(고쳐서 재시도)"
