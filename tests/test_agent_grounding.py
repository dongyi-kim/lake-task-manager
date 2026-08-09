"""grounding — 답변의 날조(없는 키·바뀐 제목·가짜 인명)를 코드가 잡는가.

실측된 사고를 그대로 재현해 검사한다. 프롬프트가 아니라 검증이 막는 부류라,
여기 테스트가 곧 그 사고의 회귀 방어선이다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

from app.agent.workflow import grounding                    # noqa: E402


def _real_key_and_title():
    from app.agent.tools import _ctx
    it = _ctx.client().search_issues("ORDER BY updated DESC", max_results=1)[0]
    return it["key"], (it.get("fields") or {}).get("summary") or ""


def test_existing_key_and_faithful_title_pass():
    key, title = _real_key_and_title()
    g = grounding.check(f"{key} ({title}) 는 진행 중입니다.")
    assert g["ok"], g


def test_nonexistent_key_is_flagged():
    g = grounding.check("관련 티켓은 ZZZZ-99999 입니다.")
    assert g["fake_keys"] == ["ZZZZ-99999"] and not g["ok"]


def test_swapped_title_is_flagged():
    """실측 사고: '[ETL] Dashboard widget' 을 '데이터 처리 성능 개선'이라 단정했다."""
    key, title = _real_key_and_title()
    g = grounding.check(f"- **{key}**: 전사 보안 취약점 긴급 패치 작업입니다.")
    assert key in g["wrong_titles"], g
    assert g["wrong_titles"][key] == title


def test_paraphrased_title_is_not_flagged():
    """요약·의역은 허용한다 — 핵심 토큰이 겹치면 날조가 아니다."""
    key, title = _real_key_and_title()
    head = title.replace("[", "").replace("]", "").split()[0]
    g = grounding.check(f"{key}: {head} 관련 작업이 진행 중입니다.")
    assert key not in g["wrong_titles"], g


def test_fabricated_person_in_role_context_is_flagged():
    """실측 사고: 'PM: 김철수' — 로스터에 없는 실명을 관여자로 만들었다."""
    g = grounding.check("담당자: 김철수, 진행 중입니다.")
    assert "김철수" in g["fake_people"]


def test_markdown_bold_roles_are_still_caught():
    """실측: 답변은 '**PM**: 김철수' 꼴(마크다운 볼드) — 첫 배포의 정규식이 전부 놓쳤다."""
    g = grounding.check("- **PM**: 김철수 — 일정 조율\n- **개발자**: 이영희")
    assert {"김철수", "이영희"} <= set(g["fake_people"])


def test_key_to_name_mapping_leak_is_caught():
    """실측 변종: 역할 낱말 없이 '**JIRA820-15**: 김철수' — 제목 줄에만 '담당자'가 있어
    역할 문맥 정규식이 놓쳤다. 키→사람 매핑 꼴도 본다."""
    g = grounding.check("### 담당자\n- **JIRA820-15**: 김철수\n- **JIRA820-16**: 이영희")
    assert {"김철수", "이영희"} <= set(g["fake_people"]), g


def test_placeholder_uid_is_flagged():
    """실측: 재작성 지시문의 예시 표기(skcc.xNNNN)를 답에 그대로 복사했다 — 자리표시자는 위반이다."""
    g = grounding.check("- **PM**: skcc.xNNNN (전체 관여)")
    assert any("자리표시자" in p for p in g["fake_people"]), g


def test_unknown_uid_is_flagged_but_real_uid_passes():
    g = grounding.check("담당 skcc.z9999 확인 필요. 실제로는 skcc.x1042 가 맡는다.")
    assert "skcc.z9999" in g["fake_people"]
    assert "skcc.x1042" not in g["fake_people"]


def test_real_person_and_uid_are_not_flagged():
    g = grounding.check("담당자: 최민서 가 보고, skcc.x1042 님 이 진행합니다.")
    assert "최민서" not in g["fake_people"]
    assert not any("skcc" in p for p in g["fake_people"])


def test_plain_hangul_words_are_not_mistaken_for_names():
    """'진행 중이며' 같은 일반 낱말을 사람으로 오인하면 오탐 지옥이 된다 — 역할 문맥만 본다."""
    g = grounding.check("현재 진행 중이며, 다음 주에 완료됩니다. 검토가 필요합니다.")
    assert g["fake_people"] == []


def test_violation_note_carries_real_values():
    key, title = _real_key_and_title()
    g = grounding.check(f"{key}: 전사 보안 패치. 담당자: 김철수. 그리고 ZZZZ-1 도 관련.")
    note = grounding.violation_note(g)
    assert "ZZZZ-1" in note and title in note and "김철수" in note


def test_warning_block_is_visible_not_silent():
    g = {"fake_keys": ["ZZZZ-1"], "wrong_titles": {}, "fake_people": ["김철수"]}
    w = grounding.warning_block(g)
    assert "자동 검증 경고" in w and "ZZZZ-1" in w and "김철수" in w


def test_responder_appends_warning_when_rewrite_cannot_fix(monkeypatch):
    """재작성으로도 못 고치면 경고가 **보이게** 붙는다 — 조용히 넘어가지 않는다."""
    os.environ["LAKE_AGENT_PROVIDER"] = "fake"
    from app.agent.workflow.agents.responder import Responder
    r = Responder()
    # fake llm 의 재작성도 같은 날조를 담는다고 가정
    monkeypatch.setattr(r, "llm", lambda **k: type("L", (), {
        "invoke": lambda self, msgs: type("M", (), {"content": "여전히 담당자: 김철수 입니다."})()})())
    out = r.apply({"trace": []}, {"text": "담당자: 김철수 가 맡고 있습니다."})
    assert "자동 검증 경고" in out["reply"]
    assert "김철수" in out["reply"]


def test_fabricated_uid_with_real_suffix_is_caught():
    """etl.x1001 — 접두만 바꾼 날조 사번. 접미(x1001)가 실존 사번(skcc.x1001)과 겹쳐도
    전체 id 가 실재하지 않으면 위반이다(실측: 접미 검색만 해서 통과했다)."""
    from app.agent.workflow import grounding
    r = grounding.check("ETL 소속 etl.x1001 의 최근 활동이 없습니다.")
    assert not r["ok"] and "etl.x1001" in r["fake_people"], r


# ── 되묻기 턴: 폼에 있는 것을 본문에 또 쓰지 않는다 (사용자 지적) ──────────
def test_the_reply_does_not_echo_the_question_form():
    """질문은 카드 폼이 묻는다. 같은 질문·보기를 산문에 늘어놓으면 같은 말이 두 벌 뜬다."""
    from app.agent.workflow.agents.responder import _drop_form_echo
    text = ("요약: 'fdc_flat_summary_ic' 표기로는 기록을 찾지 못했습니다. 유사 식별자 "
            "1건(fdc.fdc_trace_summary_ic)을 확인했습니다.\n\n"
            "확인 부탁\n\n아래 중 어떤 것을 말씀하신 건가요?\n"
            "1) fdc.fdc_trace_summary_ic\n\n2) 이 중에 없음 — 정확한 표기를 알려주세요\n\n"
            "대상 환경: 개발/스테이징/운영")
    qs = [{"question": "'fdc_flat_summary_ic' 표기로는 기록을 찾지 못했습니다. 이 중 어느 것을 "
                       "말씀하신 건가요?",
           "kind": "choice",
           "options": ["fdc.fdc_trace_summary_ic", "이 중에 없음 — 정확한 표기를 알려주세요"]}]
    out = _drop_form_echo(text, qs)
    assert "유사 식별자" in out, "상황 요약은 남아야 한다"
    assert "아래 중 어떤" not in out and "1) fdc" not in out
    assert "이 중에 없음" not in out and "대상 환경" not in out


def test_the_form_echo_filter_keeps_a_normal_answer_intact():
    """질문이 없는 턴이나 폼과 무관한 문장은 건드리지 않는다."""
    from app.agent.workflow.agents.responder import _drop_form_echo
    text = "DL-9044 에서 적재주기가 30분으로 바뀌었습니다.\n담당은 skcc.x1042 입니다."
    qs = [{"question": "어느 모듈로 볼까요?", "kind": "choice", "options": ["ETL", "Catalog"]}]
    assert _drop_form_echo(text, qs) == text
