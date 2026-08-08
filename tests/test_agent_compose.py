# -*- coding: utf-8 -*-
"""에디터 자동완성 — 맥락 수집·프롬프트 분기·접지.

이 기능이 챗과 다른 점은 **쓰기가 아니라는 것**이다. 결과는 에디터에 꽂힐 뿐이고 저장은
사용자가 누른다. 그래서 승인 토큰이 없다 — 대신 접지 검사는 챗과 똑같이 태운다(에디터에
꽂히는 글이라고 날조를 봐줄 이유가 없다).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

from app.agent import compose as C                                # noqa: E402

PROG = "DL-9090"


@pytest.fixture(autouse=True)
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    import app.infra.settings as S
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    yield


# ── 맥락 수집: 화면이 아는 것(어느 티켓·무엇을 쓰는 중)에서 나머지를 끌어온다 ──
def test_comment_context_leads_with_the_recent_conversation():
    """코멘트는 **앞선 대화에 이어서** 쓰는 글이다 — 최근 코멘트가 가장 중요한 재료다."""
    ctx = C._ticket_context(PROG, "comment")
    assert "최근 코멘트" in ctx and "skcc.x1402" in ctx
    assert "하위 2/3 완료" in ctx and "DL-9092" in ctx


def test_description_context_carries_the_current_body_instead_of_chatter():
    """본문은 고쳐 쓸 대상이 이미 있다 — 남의 코멘트를 잔뜩 싣는 것은 방해다."""
    ctx = C._ticket_context(PROG, "description")
    assert "현재 본문" in ctx
    assert "최근 코멘트" not in ctx


def test_a_brand_new_ticket_has_no_context_instead_of_an_error():
    """새 티켓 작성 중에는 키가 없다(__new__) — 조용히 빈 맥락으로 간다."""
    assert C._ticket_context("__new__", "description") == ""
    assert C._ticket_context("", "comment") == ""
    assert C._ticket_context("DL-99999", "comment") == ""


def test_context_is_capped_so_the_cursor_does_not_freeze():
    assert len(C._ticket_context(PROG, "comment")) <= C.MAX_CONTEXT


# ── 생성 ────────────────────────────────────────────────────────────
def test_compose_needs_something_to_go_on():
    r = C.compose(PROG, "comment", "", "")
    assert r["ok"] is False and "알려" in r["error"]


def test_seed_alone_is_enough_to_continue_a_draft():
    """프롬프트 없이 '이어 써 줘'만 하는 흐름 — 시드가 곧 요청이다."""
    r = C.compose(PROG, "comment", "", "<p>모니터링 붙여야 함</p>")
    assert r["ok"] and r["html"]


def test_the_prompt_tells_the_model_which_kind_of_editor_it_is():
    """본문과 코멘트는 규율이 다르다 — 같은 지시를 주면 코멘트에 <h3>배경</h3>이 붙는다."""
    body = C.compose(PROG, "description", "본문 초안")["html"]
    cmt = C.compose(PROG, "comment", "진행 공유")["html"]
    assert "본문" in body and "코멘트" in cmt


def test_fenced_output_is_unwrapped():
    """```html 로 감싸 오는 모델이 있다 — 그대로 꽂으면 에디터에 백틱이 남는다."""
    assert C._unfence("```html\n<p>안녕</p>\n```") == "<p>안녕</p>"
    assert C._unfence("<p>안녕</p>") == "<p>안녕</p>"


def test_fabricated_keys_come_back_as_a_warning_not_silently():
    """접지 검사는 챗과 같은 것을 쓴다 — 사용자는 이 글을 자기 이름으로 올린다."""
    from app.agent.workflow import grounding
    bad = grounding.check("관련: DL-99999 그리고 skcc.x9999 님")
    assert not bad["ok"]


# ── API ─────────────────────────────────────────────────────────────
def test_route_is_registered_and_rejects_an_empty_request():
    from fastapi.testclient import TestClient
    from app.main import app
    cli = TestClient(app)
    r = cli.post("/api/agent/compose", json={"ticketKey": PROG, "kind": "comment"})
    assert r.status_code == 400 and r.json()["ok"] is False


def test_route_returns_html_for_the_editor():
    from fastapi.testclient import TestClient
    from app.main import app
    cli = TestClient(app)
    r = cli.post("/api/agent/compose",
                 json={"ticketKey": PROG, "kind": "comment", "prompt": "진행 공유 코멘트"})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["html"]
