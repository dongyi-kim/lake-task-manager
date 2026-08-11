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
    assert "명시적 미완료(완료로 쓰지 말 것)" in ctx
    assert "성능 측정" in ctx and "사용 가이드 작성" in ctx
    assert "DL-9095" in ctx and "미완료: In Progress" in ctx


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


def test_need_info_signal_survives_inline_code_and_html_wrappers():
    """실모델이 NEED_INFO를 `...` 또는 <code>...</code>로 감싸도 성공 본문이 아니다."""
    assert C._need_info("`NEED_INFO: 검토 대상을 알려 주세요`") == "검토 대상을 알려 주세요"
    assert C._need_info("<p><code>NEED_INFO: 목적을 한 줄 적어 주세요</code></p>") == \
        "목적을 한 줄 적어 주세요"


def test_explicitly_remaining_work_cannot_be_changed_to_completed():
    ctx = ("[DL-9090] 작업 — In Progress\n"
           "명시적 미완료(완료로 쓰지 말 것): 성능 측정 | 사용 가이드 작성")
    assert C._status_conflicts("<p>성능 측정이 완료되었습니다.</p>", ctx) == ["성능 측정"]
    assert C._status_conflicts("<p>성능 측정을 진행할 예정입니다.</p>", ctx) == []
    assert C._status_conflicts("<p>성능 측정 완료 여부를 검토해 주세요.</p>", ctx) == []


def test_future_dod_is_not_mistaken_for_a_current_completion_claim():
    ctx = "명시적 미완료(완료로 쓰지 말 것): 성능 측정 | 사용 가이드 작성"
    unchecked = ('<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
                 '<li data-checked="false">성능 측정 완료</li></ul>')
    assert C._status_conflicts(unchecked, ctx) == []
    # 체크된 조건이나 일반 본문은 현재 완료를 나타내므로 계속 차단한다.
    checked = ('<ul data-type="taskList">'
               '<li data-checked="true">성능 측정 완료</li></ul>')
    assert C._status_conflicts(checked, ctx) == ["성능 측정"]


def test_compose_blocks_a_status_claim_that_conflicts_with_materials(monkeypatch):
    """경고 토스트 뒤 삽입이 아니라 서버 성공 응답 자체를 막는다."""
    from app.agent import config as CFG

    class _Reply:
        content = "<p>성능 측정이 완료되었습니다.</p>"

    class _Llm:
        def invoke(self, _messages):
            return _Reply()

    monkeypatch.setattr(CFG, "get_llm", lambda **_kw: _Llm())
    monkeypatch.setattr(C, "_ticket_context", lambda *_a: (
        "[DL-9090] 작업 — In Progress\n"
        "명시적 미완료(완료로 쓰지 말 것): 성능 측정 | 문서 정리"))
    monkeypatch.setattr(C, "_house_rules", lambda *_a: "")
    r = C.compose(PROG, "comment", "상태 공유")
    assert r["ok"] is False and r.get("contentConflict") is True
    assert "성능 측정" in r["error"] and "삽입하지 않았" in r["error"]


def test_compose_recognizes_backticked_need_info_as_feedback(monkeypatch):
    from app.agent import config as CFG

    class _Reply:
        content = "`NEED_INFO: 어떤 결과를 검토할지 알려 주세요`"

    class _Llm:
        def invoke(self, _messages):
            return _Reply()

    monkeypatch.setattr(CFG, "get_llm", lambda **_kw: _Llm())
    monkeypatch.setattr(C, "_ticket_context", lambda *_a: "[DL-9090] 작업 — In Progress")
    monkeypatch.setattr(C, "_house_rules", lambda *_a: "")
    r = C.compose(PROG, "comment", "검토 요청")
    assert r["ok"] is False and r.get("needsInfo") is True
    assert "어떤 결과" in r["error"]


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


# ── 렌더링 왕복: 우리가 쓰라고 한 표기가 실제로 살아남는가 ──────────
def test_checklists_survive_the_save_conversion():
    """에이전트는 taskList 로 쓰고, 저장은 사내 Jira 의 <p><input> 로 평탄화된다.

    이 변환이 깨지면 체크리스트가 그냥 불릿이 된다 — 표기 규칙(knowledge/08)의 근거다.
    """
    from app.agent.tools._ctx import client
    html = ('<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
            '<li data-checked="false">2홉 측정</li>'
            '<li data-checked="true">문서 갱신</li></ul>')
    stored = str(client().desc_field_value(html))
    assert stored.count('type="checkbox"') == 2
    assert 'checked="checked"' in stored, "체크된 항목이 풀리면 안 된다"
    assert "2홉 측정" in stored and "문서 갱신" in stored


def test_markdown_would_not_survive_so_the_prompt_forbids_it():
    """마크다운 링크는 변환되지 않고 글자로 남는다 — composer.md 가 금지하는 이유."""
    from app.agent.tools._ctx import client
    stored = str(client().desc_field_value('<p>[설계 문서](https://x/y)</p>'))
    assert "](https" in stored, "변환되지 않는다는 사실 자체가 규칙의 근거다"


def test_composer_prompt_states_the_rendering_rules():
    """규칙이 문서에만 있고 프롬프트에 없으면 모델은 모른다."""
    from app.agent.prompts.roles import SYSTEM_COMPOSER
    assert "markdown은 쓰지 않는다" in SYSTEM_COMPOSER
    assert "[~사번]" in SYSTEM_COMPOSER
    assert "taskList" in SYSTEM_COMPOSER


def test_legacy_reference_placeholders_cannot_wrap_generated_badges():
    from app.agent.compose import _badgeify, _legacy_reference_tokens

    rendered = _badgeify(_legacy_reference_tokens(
        "<p>상위 {{ref:DL-9090}} 담당 {{mention:skcc.x1402}}</p>"))
    assert "{{ref:" not in rendered and "{{mention:" not in rendered
    assert rendered.count('data-key="DL-9090"') == 1
    assert rendered.count('data-type="mention"') == 1
    assert "{{" not in rendered and "}}" not in rendered

    nested = _badgeify(_legacy_reference_tokens(
        "<p>상위 {{{{ref:DL-9090}}}} 또는 {{DL-9090}} 및 [DL-9090]</p>"))
    assert "{{" not in nested and "}}" not in nested
    assert nested.count('data-key="DL-9090"') == 3
    assert "[<a" not in nested


def test_non_done_child_is_added_to_the_explicit_remaining_guard():
    from app.agent.compose import _status_conflicts

    context = "명시적 미완료(완료로 쓰지 말 것): 다운스트림 조회 연동"
    assert _status_conflicts("<p>다운스트림 조회 연동 작업은 완료되었습니다.</p>", context)
    assert _status_conflicts("<p>다운스트림 조회 연동을 완료하였습니다.</p>", context)
    assert not _status_conflicts("<p>다운스트림 조회 연동 작업은 진행 중입니다.</p>", context)


def test_rendering_rules_are_indexed_for_retrieval():
    """knowledge/08 은 정적 RAG 에 실려야 다른 역할도 같은 규칙을 본다."""
    from app.agent.retrieval import static_index
    assert (static_index.knowledge_dir() / "08-editor-and-rendering.md").exists()


# ── LLM 미설정 게이트 ──────────────────────────────────────────────
def test_llm_ready_says_what_is_missing_per_provider(monkeypatch):
    """키가 없으면 버튼을 비활성으로 보여야 한다 — 눌러 보고 에러로 아는 것보다 낫다."""
    from unittest import mock
    from app.agent import config as C2, secrets as S
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai")   # fake 는 키가 필요 없다
    with mock.patch.object(S, "get", return_value=""):
        ok, why = C2.llm_ready()
        assert ok is False and "키" in why
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    ok2, _ = C2.llm_ready()          # 테스트 provider — 키 없이 살아난다
    assert ok2 is True


def test_compose_refuses_with_needs_setup_when_no_llm(monkeypatch):
    """서버 쪽에서도 한 번 더 가드 — 버튼이 우회돼도 명확한 사유가 나간다."""
    from unittest import mock
    from app.agent import config as C2
    with mock.patch.object(C2, "llm_ready", return_value=(False, "OpenAI API 키가 설정되지 않았습니다.")):
        r = C.compose("DL-9090", "comment", "아무거나")
    assert r["ok"] is False and r.get("needsSetup") is True
    assert "설정" in r["error"]
