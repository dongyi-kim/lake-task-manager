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
from app.agent.workflow.agents.result_integrator import _drop_direct_input_source_rows  # noqa: E402


def _real_key_and_title():
    from app.agent.tools import _ctx
    it = _ctx.client().search_issues("ORDER BY updated DESC", max_results=1)[0]
    return it["key"], (it.get("fields") or {}).get("summary") or ""


def _trusted_observation_facts(evidence):
    """Test producer for the server-owned observation-fact interface."""
    from app.agent.workflow.claim_provenance import bind_evidence_provenance

    facts = []
    for item in bind_evidence_provenance(evidence):
        source_id = item["_source_id"]
        ids = {
            row["ordinal"]: row["observation_id"]
            for row in item["_provenance"]["observations"]
        }
        for ordinal, observation in enumerate(item.get("observations") or [], 1):
            if not isinstance(observation, dict) or not observation.get("claim_kind"):
                continue
            facts.append({
                "observation_id": ids[ordinal], "source_id": source_id,
                "subject_id": observation.get("subject_id"),
                "predicate": observation.get("predicate"),
                "value": observation.get("value"),
                "claim_kind": observation.get("claim_kind"),
                "observed_at": observation.get("observed_at") or "",
                "temporal_role": observation.get("temporal_role") or "observed",
                "normalized_text": observation.get("text") or "",
                "direct": True, "authority": "materialized_match",
            })
    return facts


def test_existing_key_and_faithful_title_pass():
    key, title = _real_key_and_title()
    g = grounding.check(f"{key} ({title}) 는 진행 중입니다.")
    assert g["ok"], g


def test_nonexistent_key_is_flagged():
    g = grounding.check("관련 티켓은 ZZZZ-99999 입니다.")
    assert g["fake_keys"] == ["ZZZZ-99999"] and not g["ok"]


def test_attached_excerpt_filename_never_becomes_a_fake_verified_source_link():
    text = ("### 결론\n\n5개 표본 확인\n\n### 근거\n\n"
            "[1] [puffin-followup-notes.docx](verified URL)\n"
            "- [1-a] 첨부 발췌에서 5개 표본 언급\n"
            "[2] [공식 문서](https://example.com/spec)")
    got = _drop_direct_input_source_rows(text)
    assert "puffin-followup-notes.docx" not in got and "verified URL" not in got
    assert "https://example.com/spec" in got


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


def test_user_supplied_dialogue_speaker_is_not_treated_as_an_assignee_claim():
    g = grounding.check("김운영님과 이개발님 간 대화에서 장애가 보고되었습니다.",
                        allowed_people={"김운영", "이개발"})
    assert "김운영" not in g["fake_people"] and "이개발" not in g["fake_people"]


def test_dialogue_speaker_extraction_only_accepts_colon_prefixed_lines():
    from app.agent.workflow.agents.result_integrator import _dialogue_speakers
    req = ("[10:12] 김운영: 장애가 발생했습니다\n"
           "[10:13] 이개발: 로그를 확인했습니다\n담당자는 김철수로 해줘")
    assert _dialogue_speakers(req) == {"김운영", "이개발"}


def test_confluence_url_is_safe_inside_markdown_destination():
    from app.agent.workflow.agents.result_integrator import _markdown_url
    got = _markdown_url("https://conf/pages/1/[설계]+문서(초안)")
    assert "[" not in got and "]" not in got and "(" not in got and ")" not in got
    assert "%5B" in got and "%28" in got


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


def test_person_table_header_does_not_treat_due_column_as_a_name():
    g = grounding.check("| 작업 | 담당 | 기한 |\n|---|---|---|\n| writer | skcc.i2011 | 2026-08-22 |")
    assert "기한" not in g["fake_people"]


def test_person_table_header_does_not_treat_evidence_column_as_a_name():
    g = grounding.check(
        "| 티켓 | 담당 | 근거 |\n"
        "|---|---|---|\n"
        "| 설계 | [~skcc.x1103] | ETL 로스터 |"
    )
    assert "근거" not in g["fake_people"]


def test_plain_named_assignee_in_sentence_requires_canonical_user_id():
    g = grounding.check("남아 있는 작업의 담당자는 안하준. 성능 측정이 남아 있음.")
    assert not g["ok"]
    assert g["name_as_id"].get("안하준") == "skcc.x1450"


def test_role_sentence_filter_does_not_treat_work_as_a_person():
    g = grounding.check("담당 작업 3건을 오늘 확인")
    assert "작업" not in g["fake_people"]
    assert "작업" not in g["name_as_id"]


def test_role_sentence_filter_does_not_treat_a_grammatical_phrase_as_a_person():
    g = grounding.check("담당자 변경에는 하나의 exact username이 필요함")
    assert "변경에는" not in g["fake_people"]
    assert "변경에는" not in g["name_as_id"]


def test_role_match_does_not_cross_a_newline_into_the_next_label():
    """`1건 담당\n- **대안**:`에서 '대안'은 사람 이름이 아니다(S1 실측 오탐)."""
    g = grounding.check("- 유사 업무 1건 담당\n- **대안**:\n  - skcc.x1042")
    assert "대안" not in g["fake_people"], g


def test_violation_note_carries_real_values():
    key, title = _real_key_and_title()
    g = grounding.check(f"{key}: 전사 보안 패치. 담당자: 김철수. 그리고 ZZZZ-1 도 관련.")
    note = grounding.violation_note(g)
    assert "ZZZZ-1" in note and title in note and "김철수" in note


def test_warning_block_is_visible_not_silent():
    g = {"fake_keys": ["ZZZZ-1"], "wrong_titles": {}, "fake_people": ["김철수"]}
    w = grounding.warning_block(g)
    assert "자동 검증 경고" in w and "ZZZZ-1" in w and "김철수" in w
    assert "승인하지 말고" in w and "무시하고" not in w


def test_responder_warns_without_a_second_llm_repair(monkeypatch):
    """Result 검증은 이미 생성된 답을 다시 LLM에 넣지 않고 deterministic하게 끝낸다."""
    os.environ["LAKE_AGENT_PROVIDER"] = "fake"
    from app.agent.workflow.agents.result_integrator import ResultIntegrator
    r = ResultIntegrator()
    monkeypatch.setattr(
        r, "llm",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("grounding must not trigger a second Result call")),
    )
    out = r.apply({"trace": []}, {"text": "담당자: 김철수 가 맡고 있습니다."})
    assert "자동 검증 경고" in out["reply"]
    assert "김철수" in out["reply"]


def test_question_only_reply_never_exposes_internal_grounding_diagnostics():
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    state = {"trace": [], "questions": [{
        "question": "담당자를 골라 주세요.",
        "kind": "choice",
        "field": "assignee",
        "options": ["동명이 TEST · test.same01", "동명이 TEST · test.same02"],
        "required_input": True,
        "why_required": "담당자 변경에는 하나의 exact username이 필요함",
    }]}
    reply = ResultIntegrator().apply(state, {"text": "담당자: 변경에는"})["reply"]

    assert "exact username" in reply
    assert "자동 검증 경고" not in reply
    assert "확인되지 않는 인물" not in reply


def test_responder_removes_internal_heading_and_renders_reference_tokens():
    from app.agent.workflow.agents.result_integrator import _render_reply_tokens, _strip_instruction_echo
    text = _strip_instruction_echo("# 명령서\nDL-9090은 {{ref:DL-9090}}, 담당 {{mention:skcc.x1402}}")
    text = _render_reply_tokens(text)
    assert not text.startswith("# 명령서")
    assert "{{ref:" not in text and "{{mention:" not in text
    assert "[DL-9090](" in text and "[~skcc.x1402]" in text


def test_typed_ticket_badge_is_never_nested_inside_inline_code():
    from app.agent.workflow.agents.result_integrator import _render_reply_tokens

    got = _render_reply_tokens(
        "writer PoC(`{{ticket-inline:DL-9201}}`)와 `literal_code` 확인"
    )

    assert "(`{{ticket-inline:" not in got
    assert "({{ticket-inline:DL-9201}})" in got
    assert "`literal_code`" in got


def test_responder_forces_known_people_into_canonical_mention_badges():
    from app.agent.workflow.agents.result_integrator import _canonicalize_person_mentions

    state = {"assignment_completion": {"people": [
        {"id": "skcc.x1402", "name": "김동이", "tickets": []},
    ]}}
    got = _canonicalize_person_mentions("미완료자는 김동이이며 김동이에게 확인 필요", state)
    assert got == "미완료자는 [~skcc.x1402]이며 [~skcc.x1402]에게 확인 필요"
    assert "김동이" not in got


def test_roster_workload_display_name_is_also_forced_to_a_mention_badge():
    from app.agent.workflow.agents.result_integrator import _canonicalize_person_mentions

    state = {"roster_load": ("[ETL 로스터·부하]\n"
                             "- skcc.x1103 최하은 — 진행중 8건 · 열림 11건")}
    got = _canonicalize_person_mentions("최하은은 DL-9202 관련 작업을 진행 중", state)

    assert got == "[~skcc.x1103]은 DL-9202 관련 작업을 진행 중"


def test_responder_never_guesses_an_ambiguous_person_badge():
    from app.agent.workflow.agents.result_integrator import _canonicalize_person_mentions

    state = {"query_results": [
        {"assigneeId": "skcc.a1", "assignee": "김철수"},
        {"assigneeId": "skcc.b2", "assignee": "김철수"},
    ]}
    assert _canonicalize_person_mentions("김철수 확인 필요", state) == "김철수 확인 필요"


def test_responder_enforces_compact_heading_style_but_preserves_quotes_and_questions():
    from app.agent.workflow.agents.result_integrator import _enforce_reply_style

    source = ("확인했습니다.\n\n대상은 세 건입니다. 추가 조치가 필요합니다.\n\n"
              "> 담당자가 \"오늘 완료하겠습니다.\"라고 답했습니다.\n\n"
              "어느 범위로 진행할까요?")
    got = _enforce_reply_style(source)
    assert got.startswith("### 요약\n\n확인함")
    assert "### 상세" in got
    assert "대상은 세 건. 추가 조치 필요" in got
    assert '> 담당자가 "오늘 완료하겠습니다."라고 답했습니다.' in got
    assert "어느 범위로 진행할까요?" in got


def test_responder_style_keeps_existing_headings_and_uses_lists_without_polite_endings():
    from app.agent.workflow.agents.result_integrator import _enforce_reply_style

    source = "### 결과\n\n- 첫 작업을 완료했습니다.\n- 두 번째 작업을 진행합니다."
    got = _enforce_reply_style(source)
    assert got.count("### 결과") == 1 and "### 요약" not in got
    assert "- 첫 작업을 완료함" in got
    assert "- 두 번째 작업을 진행" in got


def test_responder_style_normalizes_negative_polite_ending():
    from app.agent.workflow.agents.result_integrator import _enforce_reply_style

    got = _enforce_reply_style("### 결과\n\n이 문제는 더 이상 블로커가 아닙니다")
    assert got.endswith("블로커가 아님")
    assert _enforce_reply_style("### 판단\n\n운영 반영이 가능할 것으로 보입니다.").endswith(
        "가능할 것으로 보임")


def test_responder_style_compacts_obligation_and_unfinished_polite_endings():
    from app.agent.workflow.agents.result_integrator import _enforce_reply_style

    got = _enforce_reply_style(
        "### 판단\n\n운영 반영은 보류되어야 합니다. "
        "reader 검증은 완료되지 않았습니다."
    )

    assert "보류 필요" in got
    assert "완료되지 않음" in got
    assert "합니다" not in got


def test_responder_style_compacts_obligation_before_citation_marker():
    from app.agent.workflow.agents.result_integrator import _enforce_reply_style

    got = _enforce_reply_style(
        "### 다음 단계\n\nreader 검증을 진행해야 합니다 [2][5]"
    )

    assert "진행 필요 [2][5]" in got
    assert "합니다" not in got


def test_responder_uses_the_payload_when_reply_claims_creation_is_impossible():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    state = {"draft": {"items": [{"summary": "[ETL] 재처리 배치 개선", "type": "Task"}]}}
    text = _align_draft_claims("이 작업은 생성할 수 없습니다.", state)
    assert "생성할 수 없습니다" not in text
    assert "재처리 배치 개선" in text and "아직 생성되지 않은" in text


def test_responder_does_not_ask_to_approve_a_missing_draft():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    state = {"draft": {"items": [], "rationale": "부모가 Sub-Task라 생성할 수 없다."}}
    text = _align_draft_claims("티켓 초안을 확인하고 승인해 주세요.", state)
    assert "현재 승인할 티켓 초안은 없습니다" in text


def test_question_only_reply_uses_required_reason_not_speculative_ticket_context():
    """ASKD4/BUG1: no draft means the prose must not invent an Epic, module, or ticket."""
    from app.agent.workflow.agents.result_integrator import ResultIntegrator
    state = {"messages": [], "intent": "plan_work", "draft": {"items": []},
             "questions": [{"question": "임계값을 어떤 값으로 바꿀까요?", "kind": "text",
                            "required_input": True,
                            "why_required": "변경 payload에 넣을 정확한 새 임계값이 없음"}]}
    got = ResultIntegrator().apply(
        state, {"text": "Runtime Epic JIRA820-1 아래 새 Bug를 만들겠습니다."})["reply"]
    assert "정확한 새 임계값" in got
    assert "JIRA820-1" not in got and "Runtime" not in got and "새 Bug" not in got
    assert "아래 입력란" in got


@pytest.mark.parametrize("error", [
    "[work_architect] structured output 실패 — rationale is too long",
    "[work_architect] structured output 실패 — change.mode is invalid",
])
def test_current_work_failure_is_rendered_without_llm_or_stale_success_claims(monkeypatch, error):
    """BUG2/MTG3/MTG9: a failed current Work turn cannot narrate a stale draft as success."""
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    monkeypatch.setattr(TextAgent, "_run", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(AssertionError("failure rendering must not call an LLM")))
    state = {
        "error": error,
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "회의 결정으로 Writer 후속 Task를 만들어줘",
            "intent": "plan_work", "action": "create",
            "target_keys": [], "outcome_ids": ["writer"], "decisions": [],
        },
        "draft": {"mode": "task", "items": [{
            "summary": "이전 턴의 오래된 초안", "type": "Task",
        }]},
        "change_plan": {}, "questions": [], "messages": [], "trace": [],
    }

    reply = ResultIntegrator()._run(state)["reply"]

    assert "작업 실패" in reply
    assert "실행 없음" in reply and "실행 대기 카드 없음" in reply
    assert "초안이 준비" not in reply and "댓글 초안" not in reply
    assert "승인해" not in reply and "아래 카드" not in reply
    assert "오래된 초안" not in reply


def test_write_action_with_no_effect_is_deterministic_no_action(monkeypatch):
    """A schema-valid but empty Work output is still a failed write turn, not prose freedom."""
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    monkeypatch.setattr(TextAgent, "_run", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(AssertionError("empty write rendering must not call an LLM")))
    state = {
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-9090에 결정 댓글을 남겨줘",
            "intent": "modify", "action": "comment",
            "target_keys": ["DL-9090"], "outcome_ids": ["comment"], "decisions": [],
        },
        "draft": {}, "change_plan": {}, "questions": [], "messages": [],
        "trace": [{"node": "work_architect", "note": "초안 없음"}],
    }

    reply = ResultIntegrator()._run(state)["reply"]

    assert "댓글" in reply and "준비하지 못" in reply
    assert "실행 없음" in reply and "실행 대기 카드 없음" in reply
    assert "게시 승인" not in reply and "댓글 승인 초안" not in reply


def test_valid_write_effect_without_approval_token_is_deterministic_no_action(monkeypatch):
    """A prepared payload without a staged capability is not an approval turn."""
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    monkeypatch.setattr(TextAgent, "_run", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(AssertionError("missing-token rendering must not call an LLM")))
    state = {
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-9090에 결정 댓글을 남겨줘",
            "intent": "modify", "action": "comment",
            "target_keys": ["DL-9090"], "outcome_ids": ["comment"],
            "decisions": [],
        },
        "draft": {},
        "change_plan": {"key": "DL-9090", "changes": {}, "comment": "운영 반영 보류"},
        "approval_token": "", "questions": [], "messages": [], "trace": [],
    }

    reply = ResultIntegrator()._run(state)["reply"]

    assert "실행 없음" in reply and "실행 대기 카드 없음" in reply
    assert "승인해" not in reply and "게시 승인" not in reply


def test_postcheck_findings_stay_in_trace_instead_of_leaking_into_reply():
    """후검증은 운영 진단 정보다. 감지 결과를 사용자 본문에 붙이지 않는다."""
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    out = ResultIntegrator().apply(
        {"messages": [], "intent": "ask", "playbook": "history"},
        {"text": "이력 자료를 충분히 정리하지 못했습니다."},
    )
    assert "결과 검증에서 누락 가능성" not in out["reply"]
    assert "내부 후검증" in out["trace"][0]["note"]


def test_running_task_bullets_are_normalized_to_detail_badges_without_duplicate_fields():
    """전용 진행 Task 목록은 제목을 평문으로 반복하지 않고 detail token만 남긴다."""
    from app.agent.workflow.agents.result_integrator import _normalize_ticket_detail_sections

    source = ("### 현재 진행 중인 Task\n\n"
              "- DL-9047 \"[ETL] 안정화 모니터링\"\n"
              "- {{ticket-inline:DL-9062}} [Catalog] 정합성 비교\n\n"
              "### 연표\n\n| 날짜 | 사건 | 근거 |")
    got = _normalize_ticket_detail_sections(source)
    assert "- {{ticket-detail:DL-9047}}" in got
    assert "- {{ticket-detail:DL-9062}}" in got
    assert "안정화 모니터링" not in got and "정합성 비교" not in got
    assert "### 연표" in got


def test_inline_badge_drops_immediately_repeated_title_and_detail_suffix():
    from app.agent.workflow.agents.result_integrator import _normalize_badge_repetitions

    source = (
        '현재 {{ticket-inline:DL-9095}} "[Runtime] 2홉 성능 측정" 진행 중\n\n'
        '### 근거\n\n[1] {{ticket-detail:DL-9095}} — [Runtime] 2홉 성능 측정 · 담당 이다은 · 진행 중'
    )
    got = _normalize_badge_repetitions(source)
    assert '"[Runtime] 2홉 성능 측정"' not in got
    assert "담당 이다은" not in got
    assert "{{ticket-inline:DL-9095}} 진행 중" in got
    assert "[1] {{ticket-detail:DL-9095}}" in got


def test_known_plain_ticket_mentions_become_badges_without_duplicate_titles():
    from app.agent.workflow.agents.result_integrator import _badgeify_known_ticket_mentions

    state = {"mentioned_keys": ["DL-9090"],
             "ticket_progress": 'DL-9095 "[Workbench] 다운스트림 조회 연동" 진행중'}
    got = _badgeify_known_ticket_mentions(
        'DL-9090 "[Workbench] 데이터 리니지 뷰어"의 남은 작업은 '
        'DL-9095 "[Workbench] 다운스트림 조회 연동"', state)
    assert got == ("{{ticket-inline:DL-9090}}의 남은 작업은 "
                   "{{ticket-inline:DL-9095}}")


def test_verified_research_material_ticket_mentions_also_become_badges():
    from app.agent.workflow.agents.result_integrator import _badgeify_known_ticket_mentions

    state = {
        "mentioned_keys": [],
        "topic_dossier": "DL-7001에서 후보 20개를 정리함",
        "pre_survey": "문서 본문에서 DL-9200의 운영 반영 보류 확인",
    }
    got = _badgeify_known_ticket_mentions(
        "DL-7001에서 정리한 후보를 DL-9200에서 검증 중", state)

    assert got == ("{{ticket-inline:DL-7001}}에서 정리한 후보를 "
                   "{{ticket-inline:DL-9200}}에서 검증 중")


def test_external_research_renderer_does_not_semantically_rewrite_the_answer():
    from app.agent.workflow.agents.result_integrator import _ensure_external_research_coverage
    from langchain_core.messages import HumanMessage

    state = {"messages": [HumanMessage(content="내부 외부 자료 조사해줘")],
             "topic_dossier": "2026-07-30 PoC 미수행",
             "pre_survey": "2026-08-16 PoC 수행 완료"}
    got = _ensure_external_research_coverage(
        "2026-08-16 기준 PoC 수행 완료. 2026-07-30 기록은 수행 전 상태", state)
    assert got == "2026-08-16 기준 PoC 수행 완료. 2026-07-30 기록은 수행 전 상태"


def test_external_research_renderer_preserves_research_analyst_conflict_wording():
    from app.agent.workflow.agents.result_integrator import _ensure_external_research_coverage
    from langchain_core.messages import HumanMessage

    state = {
        "messages": [HumanMessage(content="내부 외부 공식 자료를 조사해줘")],
        "evidence": [{"key": "DL-9201", "title": "writer PoC",
                       "observations": [{"source": "document",
                                         "text": "실제 Puffin NDV 생성 PoC는 아직 수행하지 않음"}]}],
    }
    source = "두 최신 자료의 대상 범위가 달라 PoC 완료 여부는 확정 불가"
    got = _ensure_external_research_coverage(source, state)
    assert got == source


def test_external_research_section_excludes_internal_urls_and_relabels_pending_rows():
    from app.agent.workflow.agents.result_integrator import _ensure_external_research_coverage
    from langchain_core.messages import HumanMessage

    state = {
        "messages": [HumanMessage(content="내부 외부 공식 자료를 조사해줘")],
        "evidence": [
            {"title": "내부 회의록", "url": "http://127.0.0.1:8080/spaces/DL/1", "why": "내부"},
            {"title": "Apache Iceberg", "url": "https://iceberg.apache.org/puffin-spec/",
             "why": "공식 사양"},
        ],
        "query_results": [{"id": "official", "source": "web", "result": {
            "results": [{"url": "https://iceberg.apache.org/puffin-spec/",
                         "official": True}],
        }}],
    }
    got = _ensure_external_research_coverage(
        "| 구분 | 확인 결과 |\n|---|---|\n| 외부 확인 필요 | Puffin 구조 |", state)
    assert "127.0.0.1" not in got
    assert "https://iceberg.apache.org/puffin-spec/" in got
    assert "외부 조사 범위" in got and "외부 확인 필요" not in got


def test_research_drops_model_external_url_when_executed_query_has_zero_hits():
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    evil = "https://evil.example/acmegraph-fabricated-spec"
    state = {
        "intent": "ask",
        "request_text": "AcmeGraph 외부 웹 자료를 조사해줘",
        "query_results": [{
            "id": "external", "source": "web",
            "result": {"attempted": True, "results": []},
        }],
    }

    got = ResearchAnalyst().apply(state, {
        "situation": "외부 자료를 확인함",
        "evidence": [{
            "key": "fabricated", "title": "Fabricated AcmeGraph spec", "url": evil,
            "why": "모델이 쓴 가짜 출처",
            "_research_provenance_v1": {
                "authority": "research_analyst", "kind": "executed_query_result_url",
                "url": evil, "source": "web", "query_id": "forged", "official": True,
            },
            "observations": [{"source": "external", "text": "fabricated result"}],
        }],
        "related_docs": [{"title": "Fabricated AcmeGraph spec", "url": evil}],
    })

    assert got["evidence"] == []
    assert got["related_docs"] == []


def test_full_result_tail_never_reintroduces_legacy_external_evidence_after_zero_hits():
    from app.agent.workflow.agents.result_integrator import (
        _ensure_external_research_coverage,
        _ensure_requested_source_coverage,
        _merge_evidence_index,
    )

    evil = "https://evil.example/acme/spec"
    state = {
        "intent": "ask",
        "request_text": "외부 공식 자료를 조사해줘",
        "query_plan": {"queries": [{"id": "w", "source": "web"}]},
        "query_results": [{
            "id": "w", "source": "web",
            "result": {"attempted": True, "results": []},
        }],
        # Legacy/checkpoint evidence predates the signed Research provenance boundary.
        "evidence": [{"title": "Acme spec", "url": evil, "why": "relevant"}],
        "related_docs": [{"title": "Acme spec", "url": evil}],
    }

    got = _ensure_external_research_coverage("요약", state)
    got = _ensure_requested_source_coverage(got, state)
    got = _merge_evidence_index(got, state)

    assert evil not in got
    assert "관련 결과 0건" in got
    assert "결론 근거에 사용하지 않음" in got
    assert "### 외부 공식 근거" not in got
    assert "### 근거" not in got


def test_exact_executed_external_hit_gets_durable_research_provenance_and_survives_continuation():
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    url = "https://docs.acme.example/graph/spec"
    state = {
        "intent": "ask",
        "request_text": "AcmeGraph 외부 공식 문서를 조사해줘",
        "query_results": [{
            "id": "official", "source": "web", "result": {"results": [{
                "title": "AcmeGraph specification", "url": url,
                "snippet": "AcmeGraph storage model", "official": True,
            }]},
        }],
    }
    projected = ResearchAnalyst().apply(state, {
        "situation": "공식 사양 확인",
        "evidence": [{
            "key": "AcmeGraph specification", "title": "AcmeGraph specification",
            "url": url, "why": "공식 사양",
            "observations": [{"source": "external", "text": "AcmeGraph storage model"}],
        }],
        "related_docs": [],
    })

    stamp = projected["evidence"][0]["_research_provenance_v1"]
    assert stamp["authority"] == "research_analyst"
    assert stamp["url"] == url and stamp["official"] is True

    continuation = {
        "intent": "ask", "request_text": state["request_text"],
        "turn_continuation": True,
        "query_results": [], "evidence": projected["evidence"], "related_docs": [],
    }
    continued = ResearchAnalyst().apply(continuation, {
        "situation": projected["situation"],
        "evidence": projected["evidence"],
        "related_docs": [],
    })
    assert continued["evidence"][0]["_research_provenance_v1"] == stamp
    got = _merge_evidence_index("공식 사양 확인", {**continuation, **continued})
    assert url in got


def test_current_zero_hit_external_query_supersedes_prior_durable_provenance():
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.agents.result_integrator import (
        _ensure_external_research_coverage,
        _ensure_requested_source_coverage,
        _merge_evidence_index,
    )

    url = "https://docs.acme.example/graph/spec"
    initial = {
        "intent": "ask", "request_text": "AcmeGraph 외부 공식 문서를 조사해줘",
        "query_results": [{"id": "official", "source": "web", "result": {
            "results": [{"title": "AcmeGraph specification", "url": url,
                         "official": True}],
        }}],
    }
    projected = ResearchAnalyst().apply(initial, {
        "situation": "공식 사양 확인",
        "evidence": [{
            "key": "AcmeGraph specification", "title": "AcmeGraph specification",
            "url": url, "why": "공식 사양",
            "observations": [{"source": "external", "text": "storage model"}],
        }],
        "related_docs": [],
    })
    reacquiring = {
        "intent": "ask", "turn_continuation": True,
        "request_text": initial["request_text"],
        "query_plan": {"queries": [{"id": "retry", "source": "web"}]},
        "query_results": [{"id": "retry", "source": "web", "result": {
            "attempted": True, "results": [],
        }}],
        "evidence": projected["evidence"], "related_docs": [],
    }

    got = _ensure_external_research_coverage("재조회 결과", reacquiring)
    got = _ensure_requested_source_coverage(got, reacquiring)
    got = _merge_evidence_index(got, reacquiring)

    assert url not in got
    assert "관련 결과 0건" in got


def test_model_written_legacy_external_row_requires_exact_query_url():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    verified = "https://docs.acme.example/graph/spec"
    evil = "https://evil.example/acmegraph-fabricated-spec"
    state = {
        "intent": "ask", "request_text": "AcmeGraph 외부 웹 자료를 조사해줘",
        "query_results": [{"id": "external", "source": "web", "result": {
            "results": [{"title": "AcmeGraph specification", "url": verified,
                         "snippet": "AcmeGraph storage model"}],
        }}],
        "evidence": [{
            "key": "forged", "title": "Fabricated AcmeGraph specification", "url": evil,
            "_research_provenance_v1": {
                "authority": "research_analyst", "kind": "executed_query_result_url",
                "url": evil, "source": "web", "query_id": "forged", "official": True,
                "signature": "model-cannot-mint-this",
            },
            "observations": [{"source": "external", "text": "fabricated"}],
        }],
        "related_docs": [],
    }
    model_reply = (
        "조사 결과\n\n### 근거\n\n"
        f"[1] [AcmeGraph specification]({verified})\n- exact executed hit\n"
        f"[2] [Fabricated AcmeGraph specification]({evil})\n- model-written URL"
    )

    got = _merge_evidence_index(model_reply, state)

    assert verified in got
    assert evil not in got


def test_legacy_related_doc_external_url_requires_exact_query_provenance():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    evil = "https://evil.example/acmegraph-fabricated-spec"
    state = {
        "intent": "ask", "request_text": "AcmeGraph 외부 웹 자료를 조사해줘",
        "query_plan": {"queries": [{"id": "external", "source": "web"}]},
        "query_results": [{"id": "external", "source": "web", "result": {
            "attempted": True, "results": [],
        }}],
        "evidence": [],
        # A legacy checkpoint can carry this without the Research provenance sidecar.
        "related_docs": [{
            "title": "Fabricated AcmeGraph specification", "url": evil,
        }],
    }

    got = _merge_evidence_index(
        "Fabricated AcmeGraph specification 검토 결과", state,
    )

    assert evil not in got
    assert "### 근거" not in got


def test_verified_generic_external_section_folds_into_one_canonical_index():
    from app.agent.workflow.agents.result_integrator import (
        _ensure_external_research_coverage,
        _merge_evidence_index,
    )

    url = "https://docs.acme.example/graph/spec"
    state = {
        "intent": "ask", "request_text": "AcmeGraph 외부 웹 자료를 조사해줘",
        "query_results": [{"id": "external", "source": "web", "result": {
            "results": [{"title": "AcmeGraph specification", "url": url,
                         "snippet": "AcmeGraph storage model"}],
        }}],
        "evidence": [{
            "key": "AcmeGraph specification", "title": "AcmeGraph specification",
            "url": url, "why": "외부 사양",
            "observations": [{"source": "external", "text": "AcmeGraph storage model"}],
        }],
        "related_docs": [],
    }

    intermediate = _ensure_external_research_coverage("조사 결과", state)
    assert "### 외부 근거" in intermediate
    got = _merge_evidence_index(intermediate, state)

    assert "### 외부 근거" not in got
    assert got.count("### 근거") == 1
    assert got.count(url) == 1


def test_official_request_drops_exact_external_hit_without_official_true():
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    url = "https://github.example/acme/graph"
    state = {
        "intent": "ask", "request_text": "AcmeGraph 공식 GitHub 저장소를 찾아줘",
        "query_results": [{"id": "github", "source": "github", "result": {
            "results": [{"title": "AcmeGraph", "url": url,
                         "description": "AcmeGraph repository"}],
        }}],
    }
    projected = ResearchAnalyst().apply(state, {
        "situation": "저장소 후보 확인",
        "evidence": [{
            "key": "AcmeGraph", "title": "AcmeGraph", "url": url,
            "observations": [{"source": "external", "text": "repository"}],
        }],
        "related_docs": [],
    })

    assert projected["evidence"] == []
    got = _merge_evidence_index(
        f"후보 확인\n\n### 근거\n\n[1] [AcmeGraph]({url})", {**state, **projected},
    )
    assert url not in got


def test_approval_display_drops_nonofficial_hit_for_official_github_request():
    from app.agent.workflow.agents.result_integrator import _approval_display_evidence

    url = "https://github.example/acme/graph"
    state = {
        "intent": "plan_work",
        "request_text": f"{url} 공식 GitHub 자료를 조사해서 검증 Task를 만들어줘",
        "approval_token": "pending-token",
        "draft": {"items": [{
            "summary": "AcmeGraph GitHub 사양 검증", "description": url,
        }]},
        "query_results": [{"id": "github", "source": "github", "result": {
            "results": [{
                "title": "AcmeGraph", "url": url,
                "description": "AcmeGraph repository specification",
            }],
        }}],
        "evidence": [], "related_docs": [],
    }

    visible = _approval_display_evidence(state)

    assert not any(item.get("url") == url for item in visible)


def test_requested_source_coverage_discloses_only_zero_hit_class_for_acme_fixture():
    from app.agent.workflow.agents.result_integrator import (
        _ensure_requested_source_coverage,
        _requested_source_coverage,
    )

    state = {
        "intent": "ask",
        "request_text": (
            "AcmeGraph DeltaSketch 운영 판단을 위해 Jira 티켓과 댓글, "
            "Confluence wiki, 외부 공식 문서를 함께 조사해줘"
        ),
        "query_plan": {"queries": [
            {"id": "jira", "source": "jira"},
            {"id": "comments", "source": "comments"},
            {"id": "wiki", "source": "confluence"},
            {"id": "official", "source": "web"},
        ]},
        "query_results": [
            {"id": "jira", "source": "jira",
             "result": {"tickets": [{"key": "ACME-1"}], "returned": 1}},
            {"id": "comments", "source": "comments",
             "result": {"comments": [{"ticketKey": "ACME-1", "snippet": "검증 중"}],
                        "returned": 1}},
            {"id": "wiki", "source": "confluence",
             "result": {"documents": [], "returned": 0, "total": 0}},
            {"id": "official", "source": "web",
             "result": {"attempted": True, "results": [{
                 "title": "AcmeGraph DeltaSketch specification",
                 "url": "https://docs.acme.example/deltasketch/spec",
                 "official": True,
             }]}},
        ],
    }

    coverage = {row["source_class"]: row for row in _requested_source_coverage(state)}
    assert coverage["jira"]["status"] == "covered"
    assert coverage["comments"]["status"] == "covered"
    assert coverage["confluence"]["status"] == "zero_hits"
    assert coverage["external_official"]["status"] == "covered"

    source = "확인된 내부 기록 기준으로는 운영 보류\n\n### 근거\n\n[1] {{ticket-detail:ACME-1}}"
    got = _ensure_requested_source_coverage(source, state)

    assert got.count("### 요청 출처 조사 한계") == 1
    assert "**Confluence/wiki**" in got
    assert "조회를 완료했지만 관련 결과 0건" in got
    assert "결론 근거에 사용하지 않음" in got
    assert "**Jira 티켓**" not in got
    assert "**Jira 댓글**" not in got
    assert "**외부 공식 자료**" not in got
    assert got.index("### 요청 출처 조사 한계") < got.index("### 근거")


@pytest.mark.parametrize(("request_value", "source", "source_class"), [
    ("AcmeGraph 외부 웹 자료를 조사해줘", "web", "external_web"),
    ("AcmeGraph 관련 GitHub 자료를 찾아줘", "github", "external_github"),
    ("AcmeGraph 관련 깃허브 자료를 검색해줘", "github", "external_github"),
])
def test_explicit_external_source_request_has_coverage_without_official_word(
        request_value, source, source_class):
    from app.agent.workflow.agents.result_integrator import _requested_source_coverage

    state = {
        "intent": "ask",
        "request_text": request_value,
        "query_plan": {"queries": [{"id": "external", "source": source}]},
        "query_results": [{"id": "external", "source": source, "result": {
            "results": [{
                "title": "AcmeGraph repository",
                "url": "https://github.example/acme/graph",
            }],
        }}],
    }

    rows = _requested_source_coverage(state)

    assert [row["source_class"] for row in rows] == [source_class]
    assert rows[0]["status"] == "covered"
    assert rows[0]["result_hits"] == 1
    assert rows[0]["usable_as_evidence"] is True


@pytest.mark.parametrize(("hit_source", "missing_class", "missing_label"), [
    ("web", "external_github", "GitHub 자료"),
    ("github", "external_web", "외부 웹 자료"),
])
def test_explicit_web_and_github_requests_have_independent_coverage(
        hit_source, missing_class, missing_label):
    from app.agent.workflow.agents.result_integrator import (
        _ensure_requested_source_coverage,
        _requested_source_coverage,
    )

    other_source = "github" if hit_source == "web" else "web"
    state = {
        "intent": "ask",
        "request_text": "AcmeGraph 외부 웹과 GitHub 자료를 모두 조사해줘",
        "query_plan": {"queries": [
            {"id": "web", "source": "web"},
            {"id": "github", "source": "github"},
        ]},
        "query_results": [
            {"id": hit_source, "source": hit_source, "result": {"results": [{
                "title": f"AcmeGraph {hit_source}",
                "url": f"https://{hit_source}.example/acme/graph",
            }]}},
            {"id": other_source, "source": other_source,
             "result": {"attempted": True, "results": []}},
        ],
    }

    rows = {row["source_class"]: row for row in _requested_source_coverage(state)}

    assert set(rows) == {"external_web", "external_github"}
    assert rows[f"external_{hit_source}"]["status"] == "covered"
    assert rows[missing_class]["status"] == "zero_hits"
    assert rows[missing_class]["result_hits"] == 0
    rendered = _ensure_requested_source_coverage("조회 결과", state)
    assert f"**{missing_label}**" in rendered
    covered_label = "외부 웹 자료" if hit_source == "web" else "GitHub 자료"
    assert f"**{covered_label}**" not in rendered


def test_official_github_hit_without_official_provenance_is_not_zero_or_covered():
    from app.agent.workflow.agents.result_integrator import (
        _ensure_requested_source_coverage,
        _requested_source_coverage,
    )

    state = {
        "intent": "ask",
        "request_text": "AcmeGraph 공식 GitHub 저장소를 찾아줘",
        "query_plan": {"queries": [{"id": "github", "source": "github"}]},
        "query_results": [{"id": "github", "source": "github", "result": {
            "results": [{
                "title": "AcmeGraph",
                "url": "https://github.example/acme/graph",
            }],
        }}],
    }

    row = _requested_source_coverage(state)[0]

    assert row["source_class"] == "external_github_official"
    assert row["status"] == "unverified_official"
    assert row["result_hits"] == 0
    assert row["candidate_hits"] == 1
    assert row["usable_as_evidence"] is False
    rendered = _ensure_requested_source_coverage("조회 결과", state)
    assert "공식 소유·발행 주체를 확인하지 못함" in rendered
    assert "관련 결과 0건" not in rendered


def test_requested_source_coverage_distinguishes_unplanned_from_unexecuted():
    from app.agent.workflow.agents.result_integrator import _requested_source_coverage

    state = {
        "intent": "ask",
        "request_text": "Jira 티켓·댓글과 Confluence wiki를 조사해줘",
        "query_plan": {"queries": [{"id": "wiki", "source": "confluence"}]},
        "query_results": [],
    }

    coverage = {row["source_class"]: row for row in _requested_source_coverage(state)}
    assert coverage["jira"]["status"] == "not_planned"
    assert coverage["comments"]["status"] == "not_planned"
    assert coverage["confluence"]["status"] == "not_executed"


@pytest.mark.parametrize(("request_value", "source", "result", "expected"), [
    ("Confluence wiki를 조사해줘", "confluence",
     {"error": "검색 범위 미설정 — search.confluence.spaces를 지정하세요", "documents": []},
     "config_error"),
    ("외부 공식 문서를 조사해줘", "web",
     {"error": "웹 검색 provider가 blocked 상태입니다", "results": [], "attempted": True},
     "provider_error"),
    ("Jira 티켓을 조사해줘", "jira",
     {"error": "invalid cursor", "tickets": []}, "execution_error"),
    ("Jira 댓글을 조사해줘", "comments",
     {"comments": [], "returned": 0}, "zero_hits"),
])
def test_requested_source_coverage_classifies_failure_kind(
        request_value, source, result, expected):
    from app.agent.workflow.agents.result_integrator import _requested_source_coverage

    state = {
        "intent": "ask",
        "request_text": request_value,
        "query_plan": {"queries": [{"id": "source", "source": source}]},
        "query_results": [{"id": "source", "source": source, "result": result}],
    }

    rows = _requested_source_coverage(state)
    assert len(rows) == 1
    assert rows[0]["status"] == expected
    assert rows[0]["usable_as_evidence"] is False


def test_requested_source_coverage_rejects_partial_hits_with_incomplete_pagination():
    from app.agent.workflow.agents.result_integrator import (
        _ensure_requested_source_coverage,
        _requested_source_coverage,
    )

    state = {
        "intent": "ask",
        "request_text": "AcmeGraph 관련 Confluence wiki를 전부 조사해줘",
        "query_plan": {"queries": [{
            "id": "wiki", "source": "confluence", "completeness": "all",
        }]},
        "query_results": [{"id": "wiki", "source": "confluence", "result": {
            "documents": [{"id": "1", "title": "AcmeGraph 설계"}],
            "returned": 1, "total": 8,
            "incomplete": True, "complete": False,
            "incompleteReason": "cursor_cycle",
        }}],
    }

    row = _requested_source_coverage(state)[0]
    assert row["result_hits"] == 1
    assert row["status"] == "incomplete"
    assert row["incomplete_reason"] == "cursor_cycle"
    assert row["usable_as_evidence"] is False

    got = _ensure_requested_source_coverage("확인된 범위 요약", state)
    assert "전체 조회가 완료되지 않음" in got
    assert "pagination cursor 순환" in got
    assert "결론 근거에 사용하지 않음" in got


def test_requested_source_coverage_rejects_missing_planned_query_result_despite_hit():
    from app.agent.workflow.agents.result_integrator import (
        _ensure_requested_source_coverage,
        _requested_source_coverage,
    )

    state = {
        "intent": "ask",
        "request_text": "AcmeGraph 외부 웹 자료를 모두 조사해줘",
        "query_plan": {"queries": [
            {"id": "w1", "source": "web"},
            {"id": "w2", "source": "web"},
        ]},
        "query_results": [{"id": "w1", "source": "web", "result": {
            "results": [{
                "title": "AcmeGraph specification",
                "url": "https://docs.acme.example/graph/spec",
            }],
        }}],
    }

    row = _requested_source_coverage(state)[0]

    assert row["source_class"] == "external_web"
    assert row["planned_queries"] == 2
    assert row["executed_queries"] == 1
    assert row["result_hits"] == 1
    assert row["status"] == "incomplete"
    assert row["incomplete_reason"] == "missing_query_result"
    assert row["missing_query_ids"] == ["w2"]
    assert row["usable_as_evidence"] is False
    rendered = _ensure_requested_source_coverage("확인된 웹 자료 요약", state)
    assert "계획된 조회 결과 누락" in rendered
    assert "결론 근거에 사용하지 않음" in rendered


def test_embedded_jira_comments_inherit_supplying_query_incomplete_status():
    from app.agent.workflow.agents.result_integrator import (
        _ensure_requested_source_coverage,
        _requested_source_coverage,
    )

    state = {
        "intent": "ask",
        "request_text": "AcmeGraph Jira 댓글을 모두 조사해줘",
        "query_plan": {"queries": [{
            "id": "jira-with-comments", "source": "jira", "completeness": "all",
        }]},
        "query_results": [{"id": "jira-with-comments", "source": "jira", "result": {
            "ticketDetails": [{
                "key": "ACME-1",
                "comments": [{"id": "100", "body": "validation pending"}],
            }],
            "incomplete": True, "complete": False,
            "incompleteReason": "cursor_cycle",
        }}],
    }

    row = _requested_source_coverage(state)[0]

    assert row["source_class"] == "comments"
    assert row["result_hits"] == 1
    assert row["status"] == "incomplete"
    assert row["incomplete_reason"] == "cursor_cycle"
    assert row["usable_as_evidence"] is False
    assert row["planned_queries"] == 1
    assert row["executed_queries"] == 1
    rendered = _ensure_requested_source_coverage("댓글 조사 결과", state)
    assert "전체 조회가 완료되지 않음" in rendered
    assert "pagination cursor 순환" in rendered
    assert "결론 근거에 사용하지 않음" in rendered


def test_requested_source_coverage_is_idempotent_and_skips_a_satisfied_class():
    from app.agent.workflow.agents.result_integrator import _ensure_requested_source_coverage

    complete = {
        "intent": "ask",
        "request_text": "Confluence wiki를 조사해줘",
        "query_plan": {"queries": [{"id": "wiki", "source": "confluence"}]},
        "query_results": [{"id": "wiki", "source": "confluence", "result": {
            "documents": [{"title": "AcmeGraph 설계", "url": "/spaces/ACME/pages/1"}],
            "returned": 1,
        }}],
    }
    assert _ensure_requested_source_coverage("확인된 문서 요약", complete) == "확인된 문서 요약"

    missing = {**complete, "query_results": []}
    once = _ensure_requested_source_coverage("확인된 Jira 근거 요약", missing)
    twice = _ensure_requested_source_coverage(once, missing)
    assert once == twice
    assert twice.count("### 요청 출처 조사 한계") == 1
    assert _ensure_requested_source_coverage(twice, complete) == "확인된 Jira 근거 요약"


def test_result_integrator_task_forbids_using_an_unavailable_source_as_evidence():
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    state = {
        "intent": "ask",
        "request_text": "AcmeGraph 운영 판단을 위해 Confluence wiki를 조사해줘",
        "query_plan": {"queries": [{"id": "wiki", "source": "confluence"}]},
        "query_results": [{"id": "wiki", "source": "confluence", "result": {
            "documents": [], "returned": 0, "total": 0,
        }}],
        "messages": [],
    }

    task = ResultIntegrator().task(state)

    assert "Requested Source Coverage Ledger is a binding evidence boundary" in task
    assert '"source_class": "confluence"' in task
    assert '"status": "zero_hits"' in task
    assert '"usable_as_evidence": false' in task


def test_comment_approval_quotes_every_markdown_line():
    from app.agent.workflow.agents.result_integrator import _approval_reply

    state = {"change_plan": {"keys": ["DL-1"], "changes": {},
                              "comments": [{"key": "DL-1", "body": "### 결정\n\n- 항목 A"}]}}
    got = _approval_reply(state)
    assert "  > ### 결정\n  >\n  > - 항목 A" in got


def test_deterministic_change_reply_keeps_distinct_before_and_after_dates():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.result_integrator import ResultIntegrator, _approval_reply

    state = {
        "_deterministic_reply": True,
        "messages": [HumanMessage(content="DL-9203 기한을 2026-08-31로 변경")],
        "questions": [],
        "change_plan": {
            "key": "DL-9203",
            "changes": {"duedate": "2026-08-31"},
            "before": {"duedate": "2026-08-28"},
        },
    }
    reply = ResultIntegrator().apply(state, {"text": _approval_reply(state)})["reply"]

    assert "| 기한 | 2026-08-28 | 2026-08-31 |" in reply


def test_approval_reply_exposes_assignment_evidence_for_every_child_ticket():
    from app.agent.workflow.agents.result_integrator import _approval_reply

    state = {
        "draft": {"mode": "task", "items": [{
            "summary": "Puffin 검증", "type": "Task", "assignee": "skcc.x1103",
            "children": [
                {"summary": "Writer 확인", "assignee": "skcc.i2011"},
                {"summary": "Reader 확인", "assignee": "skcc.i2044"},
            ],
        }]},
        "assignments": [{
            "index": 0, "user": "skcc.x1103", "reasons": ["진행중 8건"],
            "children": [
                {"index": 0, "user": "skcc.i2011", "why": "DL-9201 Writer 수행 이력"},
                {"index": 1, "user": "skcc.i2044", "why": "진행중 6건"},
            ],
        }],
    }

    got = _approval_reply(state)
    assert "| Puffin 검증 | {{mention:skcc.x1103}} | 진행중 8건 |" in got
    assert "| Writer 확인 | {{mention:skcc.i2011}} | DL-9201 Writer 수행 이력 |" in got
    assert "| Reader 확인 | {{mention:skcc.i2044}} | 진행중 6건 |" in got


def test_person_work_reply_exposes_the_complete_bounded_current_work_snapshot():
    from app.agent.workflow.agents.result_integrator import _person_work_reply

    tickets = [{"key": f"DL-{index}", "status": "Open", "priority": "P2-Major",
                "duedate": "2026-08-30"} for index in range(1, 8)]
    got = _person_work_reply({"user_id": "skcc.i2011", "tickets": tickets})
    assert "{{mention:skcc.i2011}}" in got and "미완료 7건" in got
    assert "| 상태 | Open 7건 |" in got and "외 2건" not in got
    assert all(f"{{{{ticket-inline:DL-{index}}}}}" in got for index in range(1, 8))


def test_person_work_reply_states_the_remainder_above_the_readable_ceiling():
    from app.agent.workflow.agents.result_integrator import _person_work_reply

    tickets = [{"key": f"DL-{index}", "status": "Open", "priority": "P2-Major"}
               for index in range(1, 29)]
    got = _person_work_reply({"user_id": "skcc.i2011", "tickets": tickets})

    assert "{{ticket-inline:DL-25}}" in got
    assert "DL-26" not in got and "최근 갱신 순 25건 표시 · 외 3건" in got


def test_progress_reply_gets_a_compact_complete_child_snapshot_when_model_omits_it():
    from app.agent.workflow.agents.result_integrator import _ensure_progress_child_coverage

    state = {
        "intent": "progress",
        "ticket_progress": (
            '하위 Sub-Task 2/3 완료:\n'
            '  - DL-9093 "렌더" 완료 (담당 A)\n'
            '  - DL-9094 "업스트림" 완료 (담당 B)\n'
            '  - DL-9095 "다운스트림" 진행중 (담당 B)'
        ),
    }
    got = _ensure_progress_child_coverage(
        "### 진행 상황\n\n현재 진행 중인 작업은 {{ticket-inline:DL-9095}}\n\n### 근거\n",
        state,
    )
    assert all(f"{{{{ticket-list:{key}}}}}" in got for key in ("DL-9093", "DL-9094"))
    assert "{{ticket-list:DL-9095}}" in got
    assert got.index("### 하위 작업 현황") < got.index("### 근거")


def test_progress_child_snapshot_is_not_duplicated_when_every_key_is_present():
    from app.agent.workflow.agents.result_integrator import _ensure_progress_child_coverage

    state = {
        "intent": "progress",
        "ticket_progress": '- DL-2 "끝" 완료\n- DL-3 "남음" 진행중',
    }
    source = "완료 {{ticket-list:DL-2}}, 진행 중 {{ticket-list:DL-3}}"
    assert _ensure_progress_child_coverage(source, state) == source


def test_task_linked_to_epic_is_not_described_as_a_new_epic_draft():
    """STARR1: a valid parent Epic must not disable draft-type contradiction checks."""
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    state = {"draft": {"items": [{"summary": "[ETL] Puffin NDV 파이프라인 1차 구현",
                                      "type": "Story", "epic": "DL-102"}]}}
    got = _align_draft_claims(
        "### Epic 초안\n\n- **Epic 이름**: [ETL] Puffin NDV 파이프라인 1차 구현\n\n"
        "새로운 Epic을 생성하고 Epic Name을 설정합니다.\n\n"
        "상위 Epic DL-102 아래에 배치합니다.\n\n### 승인 요청\n승인해 주세요.", state)
    assert "새로운 Epic" not in got and "Epic Name" not in got
    assert "**실제 티켓 초안**: Story" in got and "DL-102" in got
    assert "- **Story 제목**:" in got and "Epic 이름" not in got
    assert "상위 Epic" in got


def test_existing_children_are_not_promised_for_a_later_turn():
    from app.agent.workflow.agents.result_integrator import _align_child_presence_claims
    items = [{"children": [{"summary": "설계"}, {"summary": "구현"}, {"summary": "검증"}]}]
    got = _align_child_presence_claims(
        "하위 Task는 별도로 제안할 예정\n승인 후 하위 Task를 제안하겠습니다", items)
    assert "Sub-Task 3건이 초안에 포함됨" in got
    assert "승인 후" not in got and "제안할 예정" not in got


def test_reply_owner_uses_the_assignment_row_that_drives_the_final_payload():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    item = {"summary": "[ETL] Puffin NDV 파이프라인 개발", "type": "Task",
            "assignee": "skcc.i2011"}
    state = {"draft": {"items": [item]},
             "assignments": [{"index": 0, "user": "skcc.x1103",
                              "reasons": ["진행중 8건"],
                              "alternates": [{"user": "skcc.i2011", "why": "진행중 12건"}]}]}
    got = _align_draft_claims(
        "### 티켓 초안\n- **제목**: [ETL] Puffin NDV 파이프라인 개발\n"
        "### 할당 근거\n- **현재 담당자**: skcc.i2011 (진행중 12건)\n"
        "- **대안**: skcc.i2011 (진행중 12건)", state)
    assert "| [ETL] Puffin NDV 파이프라인 개발 | [~skcc.x1103] | 진행중 8건 |" in got
    assert "[~skcc.i2011] — 진행중 12건" in got


def test_primary_owner_alignment_does_not_overwrite_a_different_candidate():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    state = {"draft": {"items": [{"summary": "[Workbench] 화면 빈 현상", "type": "Bug",
                                      "assignee": "skcc.x1402"}]},
             "assignments": [{"index": 0, "user": "skcc.x1402",
                              "reasons": ["진행중 14건"],
                              "alternates": [{"user": "skcc.x1450", "why": "진행중 22건"}]}]}
    got = _align_draft_claims(
        "### 티켓 초안\n- 제목: [Workbench] 화면 빈 현상\n"
        "### 담당자\n담당자로 skcc.x1402를 추천. 다른 후보인 skcc.x1450은 진행중 22건", state)
    assert "[~skcc.x1402]" in got and "[~skcc.x1450]" in got


def test_exclusion_is_never_labeled_as_a_completion_condition():
    from app.agent.workflow.agents.result_integrator import _align_scope_labels
    assert _align_scope_labels("- **완료 조건**: 성능 최적화 작업은 제외") == \
        "- **제외 범위**: 성능 최적화 작업은 제외"


def test_fabricated_uid_with_real_suffix_is_caught():
    """etl.x1001 — 접두만 바꾼 날조 사번. 접미(x1001)가 실존 사번(skcc.x1001)과 겹쳐도
    전체 id 가 실재하지 않으면 위반이다(실측: 접미 검색만 해서 통과했다)."""
    from app.agent.workflow import grounding
    r = grounding.check("ETL 소속 etl.x1001 의 최근 활동이 없습니다.")
    assert not r["ok"] and "etl.x1001" in r["fake_people"], r


# ── 되묻기 턴: 폼에 있는 것을 본문에 또 쓰지 않는다 (사용자 지적) ──────────
def test_the_reply_does_not_echo_the_question_form():
    """질문은 카드 폼이 묻는다. 같은 질문·보기를 산문에 늘어놓으면 같은 말이 두 벌 뜬다."""
    from app.agent.workflow.agents.result_integrator import _drop_form_echo
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
    from app.agent.workflow.agents.result_integrator import _drop_form_echo
    text = "DL-9044 에서 적재주기가 30분으로 바뀌었습니다.\n담당은 skcc.x1042 입니다."
    qs = [{"question": "어느 모듈로 볼까요?", "kind": "choice", "options": ["ETL", "Catalog"]}]
    assert _drop_form_echo(text, qs) == text


# ── 참조에 링크·키가 없으면 확인할 방법이 없다 (실측: fdc 히스토리 답변) ──────
def test_a_reference_without_a_key_or_link_is_a_violation():
    """common.md 가 두 곳에서 금지하는데도 샜다 — `[4] [데이터카탈로그] … — 적재 Job 정보`.
    재료에는 그 문서의 URL 이 실려 있었으므로 **쓸 수 있었는데 안 쓴 것**이다.
    본문 참고 불릿에는 같은 가드가 이미 있었고(work_architect), 답변 텍스트 쪽에만 없었다."""
    from app.agent.workflow import grounding
    bad = "**참조**\n[4] [데이터카탈로그] fdc_trace_summary_ic 테이블 특성 분석 — 적재 Job 정보"
    r = grounding.check(bad)
    assert not r["ok"] and r["unlinked_refs"], r
    assert "확인할 방법이 없다" in grounding.violation_note(r)


def test_verifiable_references_are_not_flagged():
    """티켓 키·마크다운 링크·맨 URL 은 전부 확인 가능한 출처다. 본문 속 [n] 마커도 참조 줄이
    아니다 — 줄 머리 형식(`[n] `)만 본다."""
    from app.agent.workflow.grounding import _unlinked_refs
    ok = ("현재 30분 주기다 [1]. 자세한 것은 [2] 참고.\n\n**참조**\n"
          "[1] DL-9044 — 적재주기 변경의 근거\n"
          "[2] [문서 제목](http://wiki/x) — 무엇을 볼 수 있는지\n"
          "[3] DL-9062 코멘트 (skcc.x1103, 2026-08-06) — 운영 담당자\n"
          "[4] 설계 노트 http://wiki/y")
    assert _unlinked_refs(ok) == []


def test_reference_section_is_canonicalized_as_one_evidence_section():
    """본문 marker와 source index를 별도 '참조' 개념으로 노출하지 않는다."""
    from app.agent.workflow.agents.result_integrator import _dedupe_refs

    source = ("현재 주기는 30분 [1].\n\n### 참조\n"
              "[1] {{ticket-detail:DL-9044}} — 적재주기 변경")
    got = _dedupe_refs(source)
    assert "### 근거\n" in got
    assert "### 참조" not in got
    assert "{{ticket-detail:DL-9044}}" in got


def test_same_source_findings_share_one_reference_and_get_subnumbers():
    """티켓 하나의 본문·댓글 발견을 별도 source 번호로 부풀리지 않는다."""
    from app.agent.workflow.agents.result_integrator import _dedupe_refs

    source = (
        "현재 주기는 30분 [2]. 운영상 주의점은 별도 확인 [5]. "
        "문서 절차도 동일 [7], [8].\n\n"
        "### 참조\n"
        "[2] {{ticket-detail:DL-73737}} — 본문에서 30분 적재 주기 언급\n"
        "[5] DL-73737 코멘트 (skcc.x1042, 2026-08-15) — "
        "댓글에서 중복 적재 확인 절차 첨부\n"
        "[7] [운영 절차](https://wiki.example/spaces/DL/pages/7/runbook) — "
        "문서 본문에서 재처리 순서 확인\n"
        "[8] https://wiki.example/spaces/DL/pages/7/runbook — "
        "문서 본문에서 승인 조건 확인"
    )
    got = _dedupe_refs(source)

    assert got.count("{{ticket-detail:DL-73737}}") == 1
    assert got.count("https://wiki.example/spaces/DL/pages/7/runbook") == 1
    assert "[1] {{ticket-detail:DL-73737}}" in got
    assert "- [1-a] 본문에서 30분 적재 주기 언급" in got
    assert "- [1-b] 댓글" in got and "중복 적재 확인 절차 첨부" in got
    assert "[2] [운영 절차](https://wiki.example/spaces/DL/pages/7/runbook)" in got
    assert "- [2-a] 문서 본문에서 재처리 순서 확인" in got
    assert "- [2-b] 문서 본문에서 승인 조건 확인" in got
    assert "[1-a]" in got.split("### 근거", 1)[0]
    assert "[1-b]" in got.split("### 근거", 1)[0]
    assert "[2-a]" in got.split("### 근거", 1)[0]
    assert "[2-b]" in got.split("### 근거", 1)[0]


def test_structured_evidence_is_merged_into_the_single_source_index():
    """별도 시스템 근거 state도 답변의 canonical 근거 목록에 합쳐진다."""
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = {
        "evidence": [{
            "key": "DL-73737",
            "title": "자동 컴팩션 잡 개발",
            "why": "현재 운영 방식의 직접 근거",
            "observations": [
                {"source": "description", "text": "본문에서 30분 주기를 명시"},
                {"source": "comment", "text": "댓글에서 운영 체크리스트를 첨부"},
            ],
        }],
        "related_docs": [{
            "title": "LTM 사용 가이드", "url": "#/home",
        }],
    }
    got = _merge_evidence_index("현재 운영 방식 확인", state)

    assert "### 근거" in got
    assert got.count("{{ticket-detail:DL-73737}}") == 1
    assert "[1-a]" in got and "[1-b]" in got
    assert "LTM 사용 가이드" not in got  # 답에 쓰이지 않은 client-only 문서는 근거가 아님


def test_approval_evidence_keeps_decision_sources_and_hides_generic_web_hits():
    """승인 화면만 압축하고 원본 조사 artifact는 손실하지 않는다."""
    from copy import deepcopy

    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    generic_search = "https://docs.starrocks.io/search/"
    generic_home = "https://www.starrocks.io/"
    generic_readme = "https://github.com/StarRocks/starrocks/blob/main/README.md"
    generic_nested_readme = "https://github.com/StarRocks/starrocks/blob/main/docs/README.md"
    generic_docs_tree = "https://github.com/StarRocks/starrocks/tree/main/docs"
    direct_spec = "https://iceberg.apache.org/puffin-spec/"
    internal_note = "http://127.0.0.1:8080/spaces/DL/pages/44/puffin-ndv"
    state = {
        "intent": "plan_work",
        "approval_token": "approval-1",
        "request_text": "StarRocks Iceberg Puffin NDV 통계 파이프라인 작업 초안",
        "draft": {"items": [{
            "type": "Task",
            "summary": "StarRocks Puffin NDV 통계 파이프라인 검증",
            "epic": "DL-7001",
        }]},
        "evidence": [
            {
                "key": "DL-7001",
                "title": "Iceberg 통계 수집 도입",
                "observations": [{
                    "source": "description",
                    "text": "StarRocks reader의 Puffin NDV 소비 지원을 검증",
                }],
            },
            {
                "key": "Search the documentation - StarRocks",
                "title": "Search the documentation - StarRocks",
                "url": generic_search,
                "observations": [
                    {"source": "external", "text": "Search the documentation"},
                    {"source": "query", "text": "StarRocks Iceberg Puffin NDV 검색"},
                ],
            },
            {
                "key": "StarRocks",
                "title": "StarRocks",
                "url": generic_home,
                "observations": [{
                    "source": "external",
                    "text": "Open source analytical database product homepage",
                }],
            },
            {
                "key": "README - StarRocks",
                "title": "README - StarRocks",
                "url": generic_readme,
                "observations": [{
                    "source": "external",
                    "text": "StarRocks is a real-time analytical database",
                }],
            },
            {
                "key": "starrocks/docs/README.md at main - GitHub",
                "title": "starrocks/docs/README.md at main - GitHub",
                "url": generic_nested_readme,
                "observations": [{
                    "source": "external",
                    "text": ("Automatic checks verify the Contributor License Agreement (CLA) "
                             "and whether documentation conforms to Markdown syntax"),
                }],
            },
            {
                "key": "starrocks/docs at main",
                "title": "starrocks/docs at main",
                "url": generic_docs_tree,
                "observations": [{
                    "source": "external",
                    "text": "Contributing documentation tips, writing process, and templates",
                }],
            },
            {
                "key": "Puffin NDV 설계 회의",
                "title": "Puffin NDV 설계 회의",
                "observations": [{
                    "source": "document",
                    "text": "Puffin NDV 파이프라인의 검증 경계를 합의",
                }],
            },
        ],
        "query_results": [{
            "source": "web",
            "result": {
                "query": "StarRocks Iceberg Puffin NDV",
                "attempted": True,
                "results": [
                    {"url": generic_search, "official": True},
                    {"url": generic_home, "official": True},
                    {"url": generic_readme, "official": False},
                    {
                        "title": "starrocks/docs/README.md at main - GitHub",
                        "url": generic_nested_readme,
                        "snippet": ("Contributor License Agreement and Markdown syntax checks "
                                    "for documentation contributions"),
                        "official": False,
                    },
                    {
                        "title": "starrocks/docs at main",
                        "url": generic_docs_tree,
                        "snippet": "Documentation contribution writing process and templates",
                        "official": False,
                    },
                    {
                        "title": "Apache Iceberg Puffin specification",
                        "url": direct_spec,
                        "snippet": "Puffin files store NDV statistics for Iceberg tables",
                        "official": True,
                    },
                ],
            },
        }],
        "query_artifacts": {
            "external-official": {"body": "complete raw response", "resultCount": 27},
        },
        "related_docs": [{"title": "Puffin NDV 설계 회의", "url": internal_note}],
    }
    before = deepcopy(state)

    got = _merge_evidence_index("### 티켓 승인 초안\n\n초안 1건", state)

    assert "{{ticket-detail:DL-7001}}" in got
    assert direct_spec in got
    assert internal_note in got
    assert all(url not in got for url in (
        generic_search, generic_home, generic_readme, generic_nested_readme, generic_docs_tree,
    ))
    assert "### 조사 한계" not in got
    assert state == before


def test_approval_evidence_materializes_selected_parent_and_hides_query_provenance():
    """Only an opened parent is authoritative; JQL remains debug metadata."""
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = {
        "intent": "plan_work",
        "approval_token": "approval-parent",
        "request_text": "Puffin NDV 파이프라인 Task 생성",
        "draft": {"items": [{
            "type": "Task", "summary": "Puffin NDV 파이프라인 구현", "epic": "DL-9200",
        }]},
        "evidence": [{
            "key": "DL-9200", "title": "[회의] Iceberg Puffin NDV 도입",
            "observations": [{
                "source": "query",
                "text": ('QueryPlan jira:parent-candidate · canonicalJql=project in ("DL") '
                         'AND text ~ "Puffin"'),
            }],
        }],
        "query_results": [{
            "id": "parent-candidate", "source": "jira", "result": {
                "ticketDetails": [{
                    "key": "DL-9200", "type": "Epic",
                    "summary": "[회의] Iceberg Puffin NDV 도입", "status": "Open",
                    "description": "Puffin NDV 도입 배경과 reader 검증 범위를 논의",
                    "comments": [],
                }],
            },
        }],
        "related_docs": [],
    }

    got = _merge_evidence_index("### 티켓 승인 초안\n\n초안 1건", state)

    assert "{{ticket-detail:DL-9200}}" in got
    assert "Puffin NDV 도입 배경과 reader 검증 범위를 논의" in got
    assert "QueryPlan" not in got and "canonicalJql" not in got


def test_materialized_parent_rich_text_is_plain_and_bounded_in_evidence():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    long_tail = "검증 결과 " * 120
    state = {
        "intent": "plan_work",
        "approval_token": "approval-rich-parent",
        "request_text": "Puffin NDV 검증 Task 생성",
        "draft": {"items": [{
            "type": "Task", "summary": "Puffin NDV 검증", "epic": "DL-9200",
        }]},
        "evidence": [],
        "query_results": [{
            "id": "parent-candidate-check", "source": "jira", "result": {
                "ticketDetails": [{
                    "key": "DL-9200", "type": "Epic", "status": "Open",
                    "summary": "[Platform] Puffin NDV 도입",
                    "description": ("<h3>배경</h3><ul><li>Puffin writer PoC 완료</li>"
                                    f"<li>{long_tail}</li></ul>"),
                    "comments": [{"author": "skcc.x1103",
                                  "body": "<p>reader 검증은 진행 중</p>"}],
                }],
            },
        }],
        "related_docs": [],
    }

    got = _merge_evidence_index("### 티켓 승인 초안\n\n초안 1건", state)

    assert got.count("{{ticket-detail:DL-9200}}") == 1
    assert "Puffin writer PoC 완료" in got and "reader 검증은 진행 중" in got
    assert all(tag not in got for tag in ("<h3>", "<ul>", "<li>", "<p>"))
    description_line = next(line for line in got.splitlines()
                            if "Puffin writer PoC 완료" in line)
    assert len(description_line) < 500


def test_approval_evidence_does_not_promote_an_unmaterialized_selected_parent():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = {
        "intent": "plan_work",
        "approval_token": "approval-parent-unverified",
        "request_text": "Puffin NDV 파이프라인 Task 생성",
        "draft": {"items": [{
            "type": "Task", "summary": "Puffin NDV 파이프라인 구현", "epic": "DL-9999",
        }]},
        "evidence": [],
        "query_results": [{
            "id": "parent-candidate", "source": "jira", "result": {
                "tickets": [{"key": "DL-9999", "summary": "검색 hit만 있는 Epic"}],
                "ticketDetails": [],
            },
        }],
        "related_docs": [],
    }

    got = _merge_evidence_index("### 티켓 승인 초안\n\n초안 1건", state)

    assert "{{ticket-detail:DL-9999}}" not in got
    assert "검색 hit만 있는 Epic" not in got


def test_approval_evidence_uses_bounded_materialized_parent_after_interview_reset():
    """An interview may clear raw query rows, but not the verified parent source."""
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = {
        "intent": "plan_work",
        "approval_token": "approval-parent-after-interview",
        "request_text": "Puffin NDV 파이프라인 Task 생성",
        "draft": {"items": [{
            "type": "Task", "summary": "Puffin NDV 파이프라인 구현", "epic": "DL-9200",
        }]},
        "query_results": [],
        "evidence": [],
        "materialized_ticket_sources": {
            "ticketDetails": [{
                "key": "DL-9200", "type": "Epic", "status": "Open",
                "summary": "Puffin NDV 도입", "description": "reader 검증 범위 합의",
                "comments": [],
            }],
            "parentCandidateKeys": ["DL-9200"],
        },
        "related_docs": [],
    }

    got = _merge_evidence_index("### 티켓 승인 초안\n\n초안 1건", state)

    assert "{{ticket-detail:DL-9200}}" in got
    assert "reader 검증 범위 합의" in got


def test_approval_evidence_reports_search_limit_without_a_direct_official_source():
    from app.agent.workflow.agents.result_integrator import ResultIntegrator, _approval_reply

    generic_home = "https://www.starrocks.io/"
    state = {
        "intent": "plan_work",
        "approval_token": "approval-2",
        "request_text": "외부 공식 자료도 조사해서 StarRocks Puffin NDV reader 지원 검증 Task 생성",
        "draft": {"items": [{
            "type": "Task", "summary": "Puffin NDV reader 지원 검증", "epic": "DL-7001",
        }]},
        "evidence": [
            {
                "key": "DL-7001", "title": "Iceberg 통계 도입",
                "observations": [{"source": "description", "text": "Puffin NDV reader 검증 필요"}],
            },
            {
                "key": "StarRocks", "title": "StarRocks", "url": generic_home,
                "observations": [{
                    "source": "external", "text": "Open source analytical database homepage",
                }],
            },
        ],
        "query_results": [{
            "source": "web",
            "result": {
                "query": "StarRocks Puffin NDV reader",
                "attempted": True,
                "results": [{"url": generic_home, "official": True}],
            },
        }],
        "related_docs": [],
        "messages": [],
        "trace": [],
        "_deterministic_reply": True,
    }

    got = ResultIntegrator().apply(state, {"text": _approval_reply(state)})["reply"]

    assert generic_home not in got
    assert "### 조사 한계" in got
    assert "요청 주제를 직접 뒷받침하는 공식 자료는 확인하지 못함" in got
    assert got.index("### 조사 한계") < got.index("### 근거")


def test_research_answer_evidence_keeps_direct_cross_source_coverage_only():
    """Research keeps cross-source facts but never renders navigation pages as evidence."""
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    generic_search = "https://docs.starrocks.io/search/"
    direct_spec = "https://iceberg.apache.org/puffin-spec/"
    component_readme = "https://github.com/apache/iceberg/blob/main/puffin/README.md"
    state = {
        "intent": "ask",
        "request_text": "내부 기록과 외부 자료를 함께 조사해줘",
        "evidence": [
            {
                "key": "DL-7001", "title": "내부 도입 기록",
                "observations": [{"source": "comment", "text": "reader 지원은 미확인"}],
            },
            {
                "key": "Search the documentation - StarRocks",
                "title": "Search the documentation - StarRocks",
                "url": generic_search,
                "observations": [{"source": "external", "text": "Search the documentation"}],
            },
            {
                "key": "Puffin Spec", "title": "Puffin Spec", "url": direct_spec,
                "observations": [{"source": "external", "text": "Puffin file format"}],
            },
            {
                "key": "Puffin component specification",
                "title": "Puffin component specification", "url": component_readme,
                "observations": [{
                    "source": "external", "text": "Puffin NDV binary layout and validation",
                }],
            },
        ],
        "query_results": [{"id": "external-sources", "source": "web", "result": {
            "results": [
                {"url": generic_search},
                {"url": direct_spec},
                {"url": component_readme},
            ],
        }}],
        "related_docs": [],
    }

    got = _merge_evidence_index("조사 결과", state)

    assert "{{ticket-detail:DL-7001}}" in got
    assert generic_search not in got
    assert direct_spec in got and component_readme in got
    assert "### 조사 한계" not in got


def test_result_integrator_does_not_redrop_query_runner_approved_direct_intro():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    intro = "https://docs.starrocks.io/docs/introduction/StarRocks_intro/"
    state = {
        "intent": "ask", "request_text": "StarRocks가 뭐야",
        "evidence": [{
            "key": "StarRocks introduction", "title": "StarRocks introduction",
            "url": intro,
            "observations": [{
                "source": "external", "text": "StarRocks is an analytical database",
            }],
        }],
        "query_results": [{"id": "intro", "source": "web", "result": {
            "results": [{"url": intro}],
        }}],
        "related_docs": [],
    }

    got = _merge_evidence_index("StarRocks 개요", state)

    assert intro in got


def test_result_integrator_filters_product_intro_for_feature_specific_create():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    intro = "https://docs.starrocks.io/docs/introduction/StarRocks_intro/"
    state = {
        "intent": "plan_work",
        "request_text": "StarRocks Puffin NDV writer pipeline Task 생성",
        "evidence": [{
            "key": "StarRocks introduction", "title": "StarRocks introduction",
            "url": intro,
            "observations": [{
                "source": "external", "text": "StarRocks is an analytical database",
            }],
        }],
        "related_docs": [],
    }

    got = _merge_evidence_index("기능 초안", state)

    assert intro not in got


def test_result_integrator_filters_zero_anchor_external_evidence():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    unrelated = "https://github.com/InsBug/ARLtoAWVS/blob/main/domain_2w.txt"
    direct = "https://iceberg.apache.org/puffin-spec/"
    state = {
        "intent": "plan_work",
        "request_text": "starrocks puffin ndv 통계정보를 생성하는 파이프라인을 개발해야해",
        "evidence": [
            {
                "key": "ARLtoAWVS/domain_2w.txt", "title": "ARLtoAWVS/domain_2w.txt",
                "url": unrelated,
                "observations": [{
                    "source": "external",
                    "text": "ARL与AWVS联动，实现自动化扫描并推送结果",
                }],
            },
            {
                "key": "Puffin Spec", "title": "Apache Iceberg Puffin specification",
                "url": direct,
                "observations": [{
                    "source": "external", "text": "Puffin files store NDV statistics",
                }],
            },
        ],
        "query_results": [{"id": "external", "source": "web", "result": {
            "results": [{"url": unrelated}, {"url": direct}],
        }}],
        "related_docs": [],
    }

    model_reply = (
        "기능 초안\n\n### 근거\n\n"
        f"[6] [ARLtoAWVS/domain_2w.txt]({unrelated})\n"
        "- [6-a] 웹 문서에서 ARL与AWVS联动，实现自动化扫描并推送结果\n"
        f"[7] [Apache Iceberg Puffin specification]({direct})\n"
        "- [7-a] 웹 문서에서 Puffin files store NDV statistics"
    )
    got = _merge_evidence_index(model_reply, state)

    assert unrelated not in got
    assert direct in got


def test_result_integrator_keeps_direct_numbered_standard_for_public_name_alias():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    direct = "https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3096.pdf"
    state = {
        "intent": "ask",
        "request_text": "C23 specification을 외부 조사해줘",
        "evidence": [{
            "key": "N3096", "title": "N3096", "url": direct,
            "observations": [{"source": "external", "text": "WG14 working draft N3096"}],
        }],
        "query_results": [{"id": "standard", "source": "web", "result": {
            "results": [{"url": direct}],
        }}],
        "related_docs": [],
    }

    got = _merge_evidence_index(
        f"조사 결과\n\n### 근거\n\n[1] [N3096]({direct})\n"
        "- [1-a] WG14 working draft N3096",
        state,
    )

    assert direct in got


def test_result_integrator_filters_official_numbered_document_from_unrelated_family():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    unrelated = "https://www.ietf.org/archive/id/rfc9999.pdf"
    state = {
        "intent": "ask",
        "request_text": "C23 specification을 외부 조사해줘",
        "evidence": [{
            "key": "RFC9999", "title": "RFC9999", "url": unrelated,
            "observations": [{"source": "external", "text": "Internet Standard RFC9999"}],
        }],
        "related_docs": [],
    }

    got = _merge_evidence_index(
        f"조사 결과\n\n### 근거\n\n[1] [RFC9999]({unrelated})\n"
        "- [1-a] Internet Standard RFC9999",
        state,
    )

    assert unrelated not in got


def test_ticket_description_evidence_dedupes_only_canonical_exact_duplicate():
    """Badge/plain ticket spellings may duplicate one description; distinct facts must survive."""
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    repeated = ("h2. 배경 DL-7001에서 정리한 20개 후보 중 5개 표본으로 writer PoC를 "
                "수행한다. h2. 완료 조건 5개 표본의 실행 로그와 결과표를 첨부한다")
    state = {
        "request_text": "StarRocks Puffin NDV writer PoC 기록 확인",
        "evidence": [{
            "key": "DL-9201", "title": "Iceberg Puffin NDV writer PoC",
            "observations": [
                {"source": "description", "text": repeated.replace(
                    "DL-7001", "{{ticket-inline:DL-7001}}")},
                {"source": "comment", "text": "5개 표본의 Puffin 파일 생성 결과를 확보함"},
                {"source": "description", "text": repeated},
                {"source": "description", "text": "NDV 오차를 별도 결과표에 기록한다"},
            ],
        }],
        "related_docs": [],
    }
    model_reply = (
        "PoC 기록\n\n### 근거\n\n[4] {{ticket-detail:DL-9201}}\n"
        f"- [4-a] 본문에서 {repeated.replace('DL-7001', '{{ticket-inline:DL-7001}}')}"
    )

    got = _merge_evidence_index(model_reply, state)

    assert got.count("20개 후보 중 5개 표본으로 writer PoC") == 1
    assert "5개 표본의 Puffin 파일 생성 결과를 확보함" in got
    assert "NDV 오차를 별도 결과표에 기록한다" in got


@pytest.mark.parametrize(("prompt_text", "title", "url", "observation"), [
    (
        "StarRocks 공식 홈페이지 알려줘", "StarRocks",
        "https://www.starrocks.io/", "Official StarRocks homepage",
    ),
    (
        "StarRocks 공식 문서 링크 알려줘", "StarRocks Documentation",
        "https://docs.starrocks.io/docs/", "Official StarRocks documentation",
    ),
    (
        "Qwen 공식 GitHub 저장소 찾아줘", "QwenLM/Qwen3 - GitHub",
        "https://github.com/QwenLM/Qwen3", "Official Qwen model repository",
    ),
])
def test_result_integrator_keeps_explicitly_requested_official_navigation_target(
        prompt_text, title, url, observation):
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = {
        "intent": "ask", "request_text": prompt_text,
        "evidence": [{
            "key": title, "title": title, "url": url,
            "observations": [{"source": "external", "text": observation}],
        }],
        "query_results": [{
            "id": "official-link",
            "source": "github" if "github.com" in url else "web",
            "result": {"results": [{"url": url, "official": True}]},
        }],
        "related_docs": [],
    }

    got = _merge_evidence_index("공식 링크", state)

    assert url in got


@pytest.mark.parametrize(("prompt_text", "title", "url", "observation"), [
    (
        "StarRocks Puffin NDV writer pipeline Task 생성", "StarRocks",
        "https://www.starrocks.io/", "Official StarRocks homepage",
    ),
    (
        "Qwen structured output 동작 분석", "QwenLM/Qwen3 - GitHub",
        "https://github.com/QwenLM/Qwen3", "Official Qwen model repository",
    ),
])
def test_result_integrator_filters_navigation_target_for_feature_specific_work(
        prompt_text, title, url, observation):
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = {
        "intent": "plan_work", "request_text": prompt_text,
        "evidence": [{
            "key": title, "title": title, "url": url,
            "observations": [{"source": "external", "text": observation}],
        }],
        "related_docs": [],
    }

    got = _merge_evidence_index("기능 검토", state)

    assert url not in got


def test_change_approval_keeps_the_exact_target_ticket_source():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    state = {
        "intent": "modify",
        "approval_token": "approval-3",
        "request_text": "DL-8123 기한을 다음 주로 변경",
        "change_plan": {"key": "DL-8123", "changes": {"duedate": "2026-08-24"}},
        "evidence": [{
            "key": "DL-8123", "title": "백필 배치 운영",
            "observations": [{"source": "description", "text": "현재 기한 2026-08-17"}],
        }],
        "related_docs": [],
    }

    got = _merge_evidence_index("### 변경 승인 초안\n\n기한 변경", state)

    assert "{{ticket-detail:DL-8123}}" in got


def test_bare_document_reference_is_promoted_to_verified_url_without_duplicate_source():
    """S8 실측: 모델 제목 행과 structured evidence URL이 서로 다른 출처 번호가 됐다.

    제목은 동일하고 URL은 런타임이 검증했으므로 bare title을 URL source로 승격해야 한다.
    승격 뒤 late grounding guard도 링크 없는 출처 경고를 만들면 안 된다.
    """
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index
    from app.agent.workflow.grounding import _unlinked_refs

    title = "[Lake] Iceberg Puffin NDV 적용 검토 노트"
    url = "http://wiki.example/spaces/DL/pages/1604594534/puffin"
    source = (
        "운영 적용은 보류 [4].\n\n### 근거\n"
        f"[4] {title}\n- PoC 미수행 기록\n"
        f"[8] [{title}]({url})\n- 내부 writer 버전 확인"
    )
    state = {
        "evidence": [{
            "key": title, "title": title, "url": url,
            "observations": [
                {"source": "document", "text": "PoC 미수행 기록"},
                {"source": "document", "text": "내부 writer 버전 확인"},
            ],
        }],
        "query_results": [{"id": "document", "source": "confluence", "result": {
            "documents": [{"title": title, "url": url}],
        }}],
        "related_docs": [{"title": title, "url": url}],
    }

    got = _merge_evidence_index(source, state)

    assert got.count(url) == 1
    assert got.count("\n[1] ") == 1
    assert "[2]" not in got.split("### 근거", 1)[1]
    assert "[1-a]" in got and "[1-b]" in got
    assert _unlinked_refs(got) == []


def test_legacy_standalone_source_is_folded_into_the_single_evidence_index():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    title = "설계 가이드"
    url = "https://wiki.example/spaces/DL/pages/9/design"
    state = {"evidence": [{
        "key": title, "title": title, "url": url,
        "observations": [{"source": "document", "text": "설계 원칙 확인"}],
    }], "query_results": [{"id": "document", "source": "confluence", "result": {
        "documents": [{"title": title, "url": url}],
    }}], "related_docs": [{"title": title, "url": url}]}

    got = _merge_evidence_index(f"초안 작성\n\n출처: [{title}]({url})", state)

    assert "출처:" not in got
    assert got.count("### 근거") == 1 and got.count(url) == 1
    assert f"[1] [{title}]({url})" in got


def test_external_official_source_block_is_folded_into_the_single_evidence_index():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    source = (
        "Puffin 형식은 외부 사양 확인 필요.\n\n"
        "### 외부 공식 근거\n\n"
        "- [Puffin Spec](https://iceberg.apache.org/puffin-spec/) — 공식 자료\n\n"
        "### 근거\n\n[1] {{ticket-detail:DL-9200}}\n- 내부 도입 검토"
    )
    got = _merge_evidence_index(source, {
        "evidence": [], "related_docs": [],
        "query_results": [{"id": "official", "source": "web", "result": {
            "results": [{"url": "https://iceberg.apache.org/puffin-spec/",
                         "official": True}],
        }}],
    })

    assert "### 외부 공식 근거" not in got
    assert got.count("### 근거") == 1
    assert got.count("https://iceberg.apache.org/puffin-spec/") == 1
    assert "{{ticket-detail:DL-9200}}" in got


def test_pasted_conversation_is_request_data_not_an_unlinked_evidence_source():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index
    from app.agent.workflow.grounding import _unlinked_refs

    state = {"evidence": [{
        "key": "김운영과 이개발의 대화", "title": "대화 기록", "url": "",
        "why": "운영 배치 실패 제보",
        "observations": [{"source": "external", "text": "connection timeout"}],
    }], "related_docs": []}
    source = ("Bug 초안\n\n### 근거\n\n[1] 대화 기록\n"
              "- 웹 문서에서 connection timeout")

    got = _merge_evidence_index(source, state)

    assert "대화 기록" not in got and "웹 문서에서" not in got
    assert "### 근거" not in got
    assert _unlinked_refs(got) == []


def test_named_pasted_dialogue_is_not_rendered_as_an_external_source():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    title = "김운영과 이개발의 대화"
    got = _merge_evidence_index("Bug 초안", {"evidence": [{
        "key": title, "title": title, "url": "",
        "observations": [{"source": "external", "text": "야간 배치 실패 제보"}],
    }], "related_docs": []})

    assert title not in got
    assert "### 근거" not in got

    mismatched_labels = _merge_evidence_index("Bug 초안", {"evidence": [{
        "key": "김운영 대화", "title": "김운영과 이개발의 대화", "url": "",
        "observations": [{"source": "external", "text": "야간 배치 실패 제보"}],
    }], "related_docs": []})
    assert "김운영" not in mismatched_labels
    assert "### 근거" not in mismatched_labels


def test_negative_lookup_is_not_rendered_as_a_dead_evidence_source():
    from app.agent.workflow.agents.result_integrator import _merge_evidence_index

    got = _merge_evidence_index("### 티켓 승인 초안\n\n초안 1건", {"evidence": [{
        "key": "dag_etl_nightly",
        "title": "dag_etl_nightly",
        "url": "",
        "why": "사내 티켓이나 문서에서 확인되지 않았음을 보여줌",
        "limitations": "사내 기록에서 존재를 확인할 수 없음",
        "observations": [{"source": "query", "text": "사내 어디에서도 찾지 못했다"}],
    }], "related_docs": []})

    assert "dag_etl_nightly" not in got
    assert "### 근거" not in got

    web_miss = _merge_evidence_index("Bug 초안", {"evidence": [{
        "key": "prod official documentation",
        "title": "prod official documentation",
        "url": "",
        "observations": [{"source": "web", "text":
                          "웹 문서에서 prod 관련 공식 문서가 검색 결과에 나타나지 않았습니다"}],
    }], "related_docs": []})
    assert "official documentation" not in web_miss
    assert "### 근거" not in web_miss

    synthetic_query_id = _merge_evidence_index("초안 1건", {"evidence": [{
        "key": "internal-duplicate-check",
        "title": "Jira 검색 결과 (쿼리 편집기 단축키 도움말)",
        "url": "", "confidence": "low", "fitness": "context-only",
        "limitations": "검색 결과가 없으므로 구현 여부 판단 불가",
        "observations": [{"source": "query", "text": "검색 결과 0건 반환됨"}],
    }], "related_docs": []})
    assert "internal-duplicate-check" not in synthetic_query_id
    assert "Jira 검색 결과" not in synthetic_query_id
    assert "### 근거" not in synthetic_query_id


def test_external_format_description_cannot_invent_an_outcome_guarantee():
    from app.agent.workflow.agents.result_integrator import _drop_unsupported_guarantees

    state = {"request_text": "Puffin 운영 적용 여부 조사",
             "evidence": [{"url": "https://iceberg.apache.org/puffin-spec/",
                           "observations": [{"text": "통계와 인덱스를 저장한다"}]}]}
    raw = ("Puffin은 통계와 인덱스를 저장하는 형식이며, "
           "NDV 통계의 일관성과 신선도를 보장.")

    got = _drop_unsupported_guarantees(raw, state)

    assert got == "Puffin은 통계와 인덱스를 저장하는 형식임."
    assert "보장" not in got

    benefit = ("Puffin은 통계를 저장하는 형식. NDV 통계는 쿼리 최적화에 사용될 수 있음. "
               "이는 운영 적용이 성능 최적화에 기여할 수 있음을 시사하지만, "
               "reader 검증 전에는 적용할 수 없음.")
    got = _drop_unsupported_guarantees(benefit, state)
    assert "최적화" not in got
    assert "reader 검증 전에는 적용할 수 없음" in got

    reader_state = {
        "request_text": "회의록 조사",
        "topic_dossier": "StarRocks의 Puffin NDV 실제 소비 지원 여부는 미확인",
        "evidence": [],
    }
    reader = ("StarRocks는 Iceberg와 함께 사용되는 데이터베이스. "
              "StarRocks는 Puffin 통계와 인덱스를 소비할 수 있음. "
              "실제 지원 여부는 검증 필요.")
    got = _drop_unsupported_guarantees(reader, reader_state)
    assert "소비할 수 있음" not in got
    assert "실제 지원 여부는 검증 필요" in got


@pytest.mark.parametrize(
    ("consumer", "artifact"),
    [("AtlasReader", "QuartzStats"), ("NimbusReader", "HarborStats")],
)
def test_unconfirmed_capability_gate_is_product_name_invariant(consumer, artifact):
    from app.agent.workflow.agents.result_integrator import _drop_unsupported_guarantees

    state = {
        "request_text": "회의 결과를 요약해줘",
        "topic_dossier": f"{consumer}의 {artifact} 소비 지원 여부는 미확인",
    }
    raw = (
        f"{consumer}는 데이터 처리 구성요소. "
        f"{consumer}는 {artifact} 통계를 소비할 수 있음. "
        "실제 지원 여부는 검증 필요."
    )

    got = _drop_unsupported_guarantees(raw, state)

    assert "소비할 수 있음" not in got
    assert "실제 지원 여부는 검증 필요" in got


def test_public_relation_policy_is_conservative_across_consumers():
    """One public matcher fails closed on actor, artifact, and direction ambiguity."""
    from app.agent.workflow.evidence_relations import parse_relation, same_relation

    def relation(value):
        parsed = parse_relation(value)
        assert parsed
        return parsed

    unresolved = relation(
        "AtlasReader는 QuartzStats를 소비할 수 있는지는 미확인"
    )
    same = relation("AtlasReader는 QuartzStats를 소비할 수 있음")
    role_reversed = relation("AtlasReader는 QuartzStats를 생성할 수 있음")
    parser_gap = relation(
        "AtlasReader reader의 QuartzStats 소비 지원 여부는 미확인"
    )
    different_actor = relation("NimbusReader는 QuartzStats를 소비할 수 있음")
    different_artifact = relation("AtlasReader는 HarborStats를 소비할 수 있음")
    shared_context_different_artifact = relation(
        "AcmePlatform에서 AtlasReader는 HarborStats를 소비할 수 있음"
    )
    contextual_unresolved = relation(
        "AcmePlatform에서 AtlasReader는 QuartzStats를 소비할 수 있는지는 미확인"
    )
    actorless_single_anchor = relation(
        "QuartzStats consumption support was completed"
    )
    actorless_unresolved = relation(
        "QuartzStats consumption support is not yet confirmed"
    )

    assert same_relation(unresolved, same)
    assert not same_relation(unresolved, role_reversed)
    assert not same_relation(parser_gap, same)
    assert not same_relation(unresolved, different_actor)
    assert not same_relation(unresolved, different_artifact)
    assert not same_relation(contextual_unresolved, shared_context_different_artifact)
    assert not same_relation(actorless_unresolved, actorless_single_anchor)


def test_latest_user_confirmation_authorizes_only_the_same_capability_relation():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.result_integrator import _drop_unsupported_guarantees

    state = {
        "request_text": "회의 결과를 요약해줘",
        "topic_dossier": "AtlasReader의 QuartzStats 소비 지원 여부는 미확인",
        "messages": [HumanMessage(
            content="AtlasReader의 QuartzStats 소비 지원 검증을 완료했고 작동을 확인했어"
        )],
    }
    raw = "AtlasReader는 QuartzStats 통계를 소비할 수 있음."

    assert _drop_unsupported_guarantees(raw, state) == raw

    state["messages"] = [HumanMessage(
        content="NimbusReader의 HarborStats 소비 지원 검증을 완료했고 작동을 확인했어"
    )]
    assert "소비할 수 있음" not in _drop_unsupported_guarantees(raw, state)

    state["messages"] = [HumanMessage(
        content="AtlasReader의 QuartzStats 소비 지원 상태를 확인해줘"
    )]
    assert "소비할 수 있음" not in _drop_unsupported_guarantees(raw, state)


@pytest.mark.parametrize(
    "raw",
    [
        "NimbusReader는 QuartzStats 통계를 소비할 수 있음.",
        "AtlasReader는 HarborStats 통계를 소비할 수 있음.",
        "NimbusReader는 HarborStats 통계를 소비할 수 있음.",
    ],
)
def test_unconfirmed_relation_does_not_suppress_an_unrelated_capability(raw):
    from app.agent.workflow.agents.result_integrator import _drop_unsupported_guarantees

    state = {
        "request_text": "회의 결과를 요약해줘",
        "topic_dossier": "AtlasReader의 QuartzStats 소비 지원 여부는 미확인",
    }

    assert _drop_unsupported_guarantees(raw, state) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "AtlasReader cannot consume QuartzStats.",
        "AtlasReader does not support QuartzStats consumption.",
        "AtlasReader는 QuartzStats 통계를 소비할 수 없음.",
    ],
)
def test_unconfirmed_relation_gate_preserves_negative_capability_limits(raw):
    from app.agent.workflow.agents.result_integrator import _drop_unsupported_guarantees

    state = {"topic_dossier": "AtlasReader의 QuartzStats 소비 지원 여부는 미확인"}

    assert _drop_unsupported_guarantees(raw, state) == raw


def test_rollout_validation_gate_does_not_negate_a_supported_capability():
    from app.agent.workflow.agents.result_integrator import _drop_unsupported_guarantees

    state = {
        "topic_dossier": (
            "AtlasReader는 QuartzStats 통계를 소비한다. "
            "운영 반영 전 검증 필요."
        ),
    }
    raw = "AtlasReader는 QuartzStats 통계를 소비할 수 있음."

    assert _drop_unsupported_guarantees(raw, state) == raw


def test_unsupported_benefit_gate_is_product_name_invariant():
    from app.agent.workflow.agents.result_integrator import _drop_unsupported_guarantees

    state = {
        "request_text": "QuartzStats 형식의 운영 적용 여부를 조사해줘",
        "evidence": [
            {"observations": [{"text": "QuartzStats는 통계를 저장하는 형식"}]},
            {"observations": [{"text": (
                "NimbusReader는 HarborStats를 소비하며 쿼리 최적화 성능을 개선함"
            )}]},
        ],
    }
    raw = (
        "QuartzStats는 통계를 저장하는 형식. "
        "QuartzStats 통계는 쿼리 최적화에 사용될 수 있음. "
        "이는 운영 적용이 성능 최적화에 기여할 수 있음을 시사하지만, "
        "소비 검증 전에는 적용할 수 없음."
    )

    got = _drop_unsupported_guarantees(raw, state)

    assert "최적화" not in got
    assert "소비 검증 전에는 적용할 수 없음" in got


def test_direct_same_relation_evidence_can_support_a_benefit_claim():
    from app.agent.workflow.agents.result_integrator import _drop_unsupported_guarantees

    state = {"evidence": [{"observations": [{"text": (
        "AtlasReader는 QuartzStats를 소비하며 쿼리 최적화 성능을 개선함"
    )}]}]}
    raw = "AtlasReader는 QuartzStats 소비로 쿼리 최적화 성능을 개선할 수 있음."

    assert _drop_unsupported_guarantees(raw, state) == raw


def test_definition_citation_rebinding_does_not_add_a_fifth_table_column():
    from app.agent.workflow.agents.result_integrator import _rebind_definition_citations

    source = (
        "### 출처 평가\n\n"
        "| 출처 | 신뢰도 | 요청 적합성 | 한계 |\n|---|---|---|---|\n"
        "| {{ticket-detail:DL-9200}} | 높음 | 직접 | 파일 형식 검증 필요 |\n\n"
        "Puffin은 통계 파일 형식.\n\n### 근거\n\n"
        "[1] {{ticket-detail:DL-9200}}\n"
        "[2] [Puffin Spec](https://iceberg.apache.org/puffin-spec/)"
    )
    got = _rebind_definition_citations(source)
    table_row = next(line for line in got.splitlines() if "ticket-detail:DL-9200" in line
                     and line.startswith("|"))
    assert table_row.endswith("|") and not table_row.endswith("| [1]")
    assert "Puffin은 통계 파일 형식. [2]" in got


def test_public_format_definition_is_rebound_from_internal_ticket_to_external_spec():
    from app.agent.workflow.agents.result_integrator import (
        _merge_evidence_index, _rebind_definition_citations,
    )

    source = (
        "- Iceberg Puffin은 통계 파일 형식 [1]\n"
        "- 현재 도입은 검증 전 보류 [1]\n\n"
        "### 외부 공식 근거\n\n"
        "- [Puffin Spec](https://iceberg.apache.org/puffin-spec/) — 공식 자료\n\n"
        "### 근거\n\n[1] {{ticket-detail:DL-9200}}\n- 내부 도입 검토"
    )
    state = {
        "evidence": [], "related_docs": [],
        "query_results": [{"id": "official", "source": "web", "result": {
            "results": [{"url": "https://iceberg.apache.org/puffin-spec/",
                         "official": True}],
        }}],
    }
    got = _rebind_definition_citations(
        _merge_evidence_index(source, state))
    body = got.split("### 근거", 1)[0]
    assert "파일 형식 [2]" in body
    assert "검증 전 보류 [1]" in body


def test_mixed_research_paragraph_binds_each_claim_to_its_own_source():
    from app.agent.workflow.agents.result_integrator import _rebind_definition_citations

    source = (
        "### 조사로 보강한 맥락\n\n"
        "Iceberg Puffin NDV는 통계를 저장하는 파일 형식. "
        "StarRocks는 Apache Iceberg와 통합됨. "
        "현재 도입 작업은 {{ticket-detail:DL-9200}}에서 진행 중[2][3]. "
        "이 티켓에서는 검증 전 운영 반영을 금지함. [3]\n\n"
        "### 근거\n\n"
        "[1] {{ticket-detail:DL-9200}}\n- 본문에서 검증 전 운영 반영 금지\n"
        "[2] [Puffin Spec](https://iceberg.apache.org/puffin-spec/)\n"
        "[3] [Apache Iceberg Lakehouse - StarRocks](https://docs.starrocks.io/iceberg/)"
    )
    got = _rebind_definition_citations(source)
    body = got.split("### 근거", 1)[0]

    assert "파일 형식. [2]" in body
    assert "StarRocks는 Apache Iceberg와 통합됨. [3]" in body
    assert "{{ticket-detail:DL-9200}}에서 진행 중. [1]" in body
    assert "이 티켓에서는 검증 전 운영 반영을 금지함. [1]" in body
    assert "진행 중[2][3]" not in body


def test_adjacent_citations_are_merged_and_orphans_never_remain_dead_markers():
    from app.agent.workflow.agents.result_integrator import _dedupe_refs

    source = (
        "결론은 세 출처가 일치 [4] [5], [10]. 미확인 주장 [99].\n\n"
        "### 근거\n"
        "[4] DL-9044 — 주기 변경\n"
        "[5] [운영 문서](https://wiki.example/pages/5) — 운영 절차\n"
        "[10] [공식 문서](https://example.org/spec) — 표준 동작\n"
    )
    got = _dedupe_refs(source)

    assert "일치 [1][2][3]" in got
    assert "[99]" not in got and "미확인 주장 (근거 확인 필요)" in got
    assert got.count("### 근거") == 1


def test_near_duplicate_observations_differing_only_by_source_prefix_are_collapsed():
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    got = canonicalize_evidence_index(
        "### 근거\n\n[1] DL-9201 — 파일 생성 결과 확보\n"
        "- [1-a] 파일 생성 결과 확보\n- [1-b] 댓글에서 파일 생성 결과 확보",
    )

    assert got.count("파일 생성 결과 확보") == 1
    assert "[1-a]" not in got and "[1-b]" not in got


def test_old_trailing_observation_marker_is_removed_during_renumbering():
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    got = canonicalize_evidence_index(
        "결과 확인 [7].\n\n### 근거\n\n"
        "[7] DL-9201\n- [7-a] 파일 생성 결과 확보 [3-a]\n"
        "- [7-b] reader 검증 필요 [4-b]"
    )

    assert "[3-a]" not in got and "[4-b]" not in got
    assert "- [1-a] 파일 생성 결과 확보" in got
    assert "- [1-b] reader 검증 필요" in got


def test_encoded_and_decoded_confluence_urls_share_one_source_and_findings_collapse():
    """S8 실측: 같은 페이지 URL·같은 본문 사실이 표기 차이만으로 두 벌이 됐다."""
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    encoded = ("http://wiki.example/spaces/DL/pages/2548961256/"
               "%5B%ED%9A%8C%EC%9D%98%EB%A1%9D%5D+Puffin+NDV")
    decoded = ("http://wiki.example/spaces/DL/pages/2548961256/"
               "[회의록]+Puffin+NDV")
    got = canonicalize_evidence_index(
        "### 근거\n\n"
        f"[1] [회의록]({encoded})\n"
        "- 검증 전 운영 반영은 금지한다는 내용이 포함되어 있음\n"
        f"[2] [회의록]({decoded})\n"
        "- 문서 본문에서 검증 전 운영 반영은 금지한다"
    )

    assert got.count("http://wiki.example") == 1
    assert got.count("검증 전 운영 반영은 금지") == 1
    assert "\n[2] " not in got


def test_typed_external_url_delimiter_never_becomes_part_of_source_identity():
    """A typed-token suffix beside Korean prose must merge with the structured URL."""
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    url = "https://iceberg.apache.org/puffin-spec/"
    got = canonicalize_evidence_index(
        "공식 사양 확인 [7].\n\n### 근거\n\n"
        f"[7] {{{{external:{url}}}}}는 공식 사양\n"
        f"[9] [Puffin Spec]({url})\n- 웹 문서에서 파일 구조 확인"
    )

    assert got.count(url) == 1
    assert "}}는" not in got
    assert "\n[2] " not in got


def test_confluence_page_id_alias_is_promoted_to_the_verified_document_url():
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    url = "http://wiki.example/spaces/DL/pages/2548961256/meeting"
    got = canonicalize_evidence_index(
        "회의 결정 [5].\n\n### 근거\n\n[5] 2548961256\n- 운영 반영 보류",
        evidence=[{
            "key": "실무회의", "title": "실무회의", "url": url,
            "observations": [{"source": "document", "text": "운영 반영 보류"}],
        }],
        related_docs=[{"title": "실무회의", "url": url}],
    )

    assert got.count(url) == 1
    assert "[1] [실무회의]" in got
    assert "\n[2] " not in got


def test_unused_source_shell_without_observation_is_removed():
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    got = canonicalize_evidence_index(
        "reader 검증 진행 [1].\n\n### 근거\n\n"
        "[1] DL-9202\n- reader 검증 진행\n"
        "[2] [검토 노트](https://wiki.example/pages/2/note)"
    )

    assert "DL-9202" in got
    assert "검토 노트" not in got
    assert "https://wiki.example/pages/2/note" not in got


def test_explicit_document_title_rebinds_the_following_wrong_marker():
    from app.agent.workflow.agents.result_integrator import _rebind_explicit_source_citations

    got = _rebind_explicit_source_citations(
        "실무회의는 운영 반영 보류를 결정함 [5]. "
        "Puffin Spec은 파일 형식을 정의함 [5].\n\n### 근거\n\n"
        "[4] [실무회의](https://wiki.example/pages/4/meeting)\n"
        "[5] [Puffin Spec](https://iceberg.apache.org/puffin-spec/)"
    )

    body = got.split("### 근거", 1)[0]
    assert "실무회의는 운영 반영 보류를 결정함 [4]." in body
    assert "Puffin Spec은 파일 형식을 정의함 [5]." in body


def test_grounding_runs_after_verified_badge_and_document_normalization(monkeypatch):
    """Known plain ids must not spend a second LLM call or leak an internal warning."""
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    doc_url = "http://127.0.0.1:8080/spaces/DL/pages/2548961256/meeting"
    state = {
        "messages": [], "intent": "ask", "trace": [],
        "evidence": [
            {"key": "DL-9200", "title": "[회의] Iceberg Puffin NDV 도입",
             "observations": [{"source": "description", "text": "운영 반영 보류"}]},
            {"key": "실무회의", "title": "실무회의", "url": doc_url,
             "observations": [{"source": "document", "text": "reader 검증 필요"}]},
        ],
        "related_docs": [{"title": "실무회의", "url": doc_url}],
    }
    integrator = ResultIntegrator()
    monkeypatch.setattr(integrator, "llm", lambda: (_ for _ in ()).throw(
        AssertionError("verified normalization must avoid a grounding rewrite")))

    got = integrator.apply(state, {
        "text": "운영 반영 보류 [1][2].\n\n### 근거\n\n"
                "[1] DL-9200\n- 운영 반영 보류\n"
                "[2] 2548961256\n- reader 검증 필요"
    })["reply"]

    assert "자동 검증 경고" not in got
    assert got.count(doc_url) == 1
    assert "{{ticket-detail:DL-9200}}" in got


def test_related_document_link_nested_under_ticket_is_promoted_to_its_own_source():
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    title = "[회의록] Puffin NDV 실무회의"
    url = "http://wiki.example/spaces/DL/pages/12/[회의록]+Puffin+NDV"
    got = canonicalize_evidence_index(
        "### 근거\n\n[1] {{ticket-detail:DL-7001}}\n"
        f"- [1-a] {title} {url}\n"
        "- [1-b] 댓글에서 reader 지원은 미확인",
        related_docs=[{"title": title, "url": url}],
    )

    assert got.count(url) == 1
    assert f"[{title}]({url})" in got
    ticket_block = got.split("{{ticket-detail:DL-7001}}", 1)[1].split(f"[{title}]", 1)[0]
    assert url not in ticket_block
    assert "reader 지원은 미확인" in ticket_block


def test_explicit_source_quality_and_marker_contract_is_completed_from_structured_evidence():
    from app.agent.workflow.agents.result_integrator import (
        _ensure_requested_body_citations, _merge_evidence_index,
        _render_requested_source_quality,
    )

    state = {
        "request_text": "근거 marker와 출처별 신뢰도·요청 적합성을 판단해줘",
        "evidence": [
            {"key": "DL-9201", "title": "Writer PoC", "why": "writer 결과 확보",
             "confidence": "high", "fitness": "direct", "limitations": "reader 미검증",
             "observations": [{"source": "comment", "text": "writer 결과 확보"}]},
            {"key": "Guide", "title": "Puffin Spec", "url": "https://example.com/puffin",
             "why": "Puffin 사양", "confidence": "high", "fitness": "supporting",
             "limitations": "내부 운영 여부는 판단하지 않음",
             "observations": [{"source": "external", "text": "Puffin 통계 파일 사양"}]},
        ],
        "query_results": [{"id": "external", "source": "web", "result": {
            "results": [{"url": "https://example.com/puffin", "official": True}],
        }}],
    }
    source = "### 결론\n\nWriter PoC 결과를 확보했으며 reader 검증은 남아 있음\n\n### 근거\n\n[1] DL-9201"
    got = _render_requested_source_quality(source, state)
    got = _merge_evidence_index(got, state)
    got = _ensure_requested_body_citations(got, state)

    assert "| {{ticket-detail:DL-9201}} | 높음 | 직접 | reader 미검증 |" in got
    assert "| [Puffin Spec](https://example.com/puffin) | 중간 | 보조 |" in got
    conclusion = got.split("### 출처 평가", 1)[0]
    assert "[1]" in conclusion
    assert got.count("### 근거") == 1


def test_same_source_child_citations_share_one_bracket():
    from app.agent.workflow.agents.result_integrator import _dedupe_refs

    source = (
        "변경 배경과 운영 확인 [4] [5].\n\n### 근거\n"
        "[4] DL-9044 — 본문에서 변경 배경\n"
        "[5] DL-9044 댓글 (skcc.x1103) — 운영 확인\n"
    )
    got = _dedupe_refs(source)

    assert "[1-a][1-b]" in got
    assert got.count("{{ticket-detail:DL-9044}}") == 1


def test_assignment_completion_reply_does_not_expose_raw_jql_predicates():
    """사용자에게 필요한 것은 판정 의미이며 내부 조회식은 아님."""
    from app.agent.workflow.agents.result_integrator import _assignment_completion_reply

    got = _assignment_completion_reply({
        "topic": "보안 필수교육",
        "parents": [{"key": "DL-3671", "total": 14, "done": 13,
                     "incomplete": [{"key": "DL-3685"}]}],
        "people": [{"id": "skcc.x1042", "name": "최민서",
                    "tickets": [{"key": "DL-3685"}]}],
        "unassigned": [], "incompleteSubtasks": 1, "totalSubtasks": 14,
        "doneSubtasks": 13,
    })

    assert "완료 상태가 아닌 직계 Sub-Task" in got
    assert "statusCategory" not in got and "!= done" not in got


# ── 탐지와 교정을 분리한다 (실측: 위반이 잡혔는데 경고도 재작성도 없이 나갔다) ──────
def test_grounding_violation_attaches_warning_without_a_result_rewrite(monkeypatch):
    """탐지 결과는 두 번째 Result LLM 호출 없이 deterministic warning으로 남는다."""
    from app.agent.workflow.agents.result_integrator import ResultIntegrator
    r = ResultIntegrator()
    monkeypatch.setattr(ResultIntegrator, "llm",
                        lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("429")))
    bad = ("DL-9044 에서 30분으로 바뀌었습니다 [1].\n\n**참조**\n"
           "[1] DL-9044 — 적재주기 변경\n"
           "[2] [데이터카탈로그] 테이블 특성 분석 — 스키마 정보\n")
    out = r.apply({"messages": [], "intent": "ask"}, {"text": bad})
    reply = out.get("reply") or ""
    assert "자동 검증 경고" in reply, reply
    if "### 근거" in reply:
        assert reply.index("자동 검증 경고") < reply.index("### 근거"), reply


def test_a_rewrite_that_guts_the_answer_is_rejected():
    """위반을 없애는 가장 쉬운 방법은 **내용을 지우는 것**이다 — 그 길을 막는다.
    실측(fake 프로브): 지시문을 복창한 껍데기가 검사를 통과해 멀쩡한 답을 대체했다."""
    from app.agent.workflow.agents.result_integrator import _kept_substance
    full = ("DL-9041·DL-9042·DL-9043 를 시간순으로 정리하면 다음과 같습니다. "
            "각 티켓의 경위와 현재 상태를 아래 표에 담았습니다.")
    assert _kept_substance(full, full.replace("DL-9043", "DL-9046")) is True
    assert _kept_substance(full, "[fake] 방금 쓴 답에 사실 오류가 있다") is False
    assert _kept_substance(full, "") is False


# ── 후처리가 마크다운 링크를 먹던 것 (실측: 문서 참조가 제목만/URL만 남았다) ──────
def test_bracketed_link_titles_survive_the_dangling_bracket_cleanup():
    """우리 Confluence 제목은 전부 `[데이터카탈로그] …` 꼴이라 링크 텍스트에 대괄호가 있다.
    옛 정규식은 60자를 내다보는 lookahead 라 그 링크를 통째로 뭉갰다 — "링크 없는 문서
    제목" 사고를 프롬프트로 몇 라운드나 쫓았는데 모델은 제대로 쓰고 있었다."""
    from app.agent.workflow.agents.result_integrator import _drop_dangling_bracket as f
    keep = "[1] [[데이터카탈로그] qms_defect_code_mst 정의](http://wiki/x) — 주 1회"
    assert f(keep) == keep
    assert f("[2] [설계 노트](http://wiki/z) — 무엇") == "[2] [설계 노트](http://wiki/z) — 무엇"
    assert f("현재 30분이다 [1].") == "현재 30분이다 [1]."
    # 쓰다 만 토막은 여전히 지운다(원래 목적)
    assert f("자세한 것은 [여기에서 확인") == "자세한 것은 여기에서 확인"


def test_post_processing_damage_is_caught_by_a_late_recheck():
    """접지 검사는 후처리 **앞**에 있어서, 후처리가 만든 결함은 검사를 통과한 셈이 됐다.
    마지막에 한 번 더 본다 — 재작성은 이미 끝난 자리라 경고만 붙인다."""
    from app.agent.workflow.agents.result_integrator import ResultIntegrator
    r = ResultIntegrator()
    # 링크가 온전하면 경고가 붙지 않는다
    ok = ("주 1회 동기화된다 [1].\n\n**참조**\n"
          "[1] [[데이터카탈로그] qms 정의](http://wiki/x) — 주 1회\n")
    out = r.apply({"messages": [], "intent": "ask"}, {"text": ok})
    assert "자동 검증 경고" not in (out.get("reply") or ""), out.get("reply")


# ── 가드가 만든 회피 경로: 링크가 없으면 아무 URL 이나 붙인다 (실측) ──────────
def test_a_ticket_reference_pointing_at_a_document_url_is_a_violation():
    """①(링크 없음)을 막았더니 모델이 아무 URL 이나 붙여 통과했다:
        1. [DL-9044 — 적재주기 변경](http://…/pages/…/[데이터카탈로그]+특성+분석)
    클릭하면 전혀 다른 것이 열린다 — **링크가 없는 것보다 나쁘다**(있는 척한다)."""
    from app.agent.workflow.grounding import _unlinked_refs
    bad = ("**참조**\n1. [DL-9044 — 적재주기 변경]"
           "(http://x/spaces/DL/pages/352/%EB%AC%B8%EC%84%9C)")
    assert _unlinked_refs(bad), bad
    ok = "**참조**\n[1] [DL-9044 적재주기 변경](http://x/browse/DL-9044) — 근거"
    assert _unlinked_refs(ok) == []


def test_a_numbered_reference_form_is_checked_too():
    """금지한 형식(`1.`)이라고 검사에서 빼면 **그 형식으로 새어 나간다** — 실측으로
    `1.` 참조 줄이 통째로 검사 밖이었다."""
    from app.agent.workflow.grounding import _unlinked_refs
    assert _unlinked_refs("**참조**\n1. [데이터카탈로그] 정의 — 링크 없음")


def test_a_plain_numbered_list_in_the_body_is_not_a_reference():
    """본문의 번호 목록까지 출처로 오인하면 멀쩡한 답에 경고가 붙는다."""
    from app.agent.workflow.grounding import _unlinked_refs
    assert _unlinked_refs("순서는 이렇다.\n\n1. 대상을 정한다\n2. job 을 붙인다\n") == []


def test_a_link_slot_without_a_real_url_is_a_violation():
    """가드가 만든 회피 3번째 — 재작성 지시문의 문구를 **URL 자리에 그대로 복사**했다:
        1. [DL-9044 — 적재주기 변경](확인할 방법이 없음)
    예전 검사는 `](` 만 보고 '링크 있음'으로 통과시켰다. 이 저장소가 이미 겪은 부류다
    (사번 자리표시자 NNNN 을 답에 복사한 사고)."""
    from app.agent.workflow.grounding import _unlinked_refs
    for bad in ("**참조**\n1. [DL-9044 — 변경](확인할 방법이 없음)",
                "**참조**\n[1] [문서 제목]() — 설명",
                "**참조**\n[1] [문서 제목](URL) — 설명"):
        assert _unlinked_refs(bad), bad
    for ok in ("**참조**\n[1] DL-9044 — 적재주기 변경의 근거",
               "**참조**\n[2] [[데이터카탈로그] 특성 분석](http://x/pages/352/y) — 스키마",
               "**참조**\n[3] [DL-9044 변경](http://x/browse/DL-9044) — 근거"):
        assert _unlinked_refs(ok) == [], ok


def test_document_urls_are_attached_by_code_not_asked_for():
    """재료에는 URL 이 있는데 모델이 참조로 옮기지 않는 일이 반복됐다(실측 DATA9: 참조
    세 줄이 전부 제목만). 프롬프트로 두 라운드 고쳤는데도 재발했다 — **아는 URL 을 그
    제목에 붙이는 것은 지어내는 것이 아니라 옮기는 것**이라 코드가 할 일이다."""
    from app.agent.workflow.agents.result_integrator import _attach_known_doc_urls
    from app.agent.workflow.grounding import _unlinked_refs
    st = {"topic_dossier": "문서 「[데이터카탈로그] qms 정의」 (http://x/pages/239/y) 발췌:\n본문"}
    t = ("**참조**\n1. [데이터카탈로그] qms 정의 — 적재주기\n"
         "[2] DL-9044 — 티켓 참조는 그대로\n"
         "[3] [다른 문서](http://z/a) — 이미 링크\n")
    out = _attach_known_doc_urls(t, st)
    assert "](http://x/pages/239/y)" in out
    assert "[2] DL-9044 — 티켓 참조는 그대로" in out      # 티켓 줄은 안 건드린다
    assert out.count("http://z/a") == 1                     # 이미 링크인 줄도 그대로
    assert _unlinked_refs(out) == []


def test_unknown_titles_are_not_given_a_borrowed_url():
    """부분 일치로 엉뚱한 문서를 붙이면 그게 곧 위조다 — 제목이 그대로 있을 때만 붙인다."""
    from app.agent.workflow.agents.result_integrator import _attach_known_doc_urls
    st = {"topic_dossier": "문서 「A 설계 노트」 (http://x/a) 발췌:\n본문"}
    t = "**참조**\n1. B 운영 런북 — 다른 문서다\n"
    assert _attach_known_doc_urls(t, st) == t


def test_real_names_leaking_through_table_cells_are_caught():
    """"| 담당자 | 한예준 |" — **표 칸**으로 새는 변종(실측 EDGE13). 역할 낱말과 이름 사이가
    콜론이 아니라 파이프라 기존 두 패턴이 전부 놓쳤다. 답변이 표를 많이 쓰는 화면이라
    이 꼴이 흔하다. common.md: "People appear as ids (skcc.x1042)"."""
    from app.agent.workflow.grounding import TABLE_NAME_RE
    assert [m.group(1) for m in TABLE_NAME_RE.finditer("| 담당자 | 한예준 | [1] |")] == ["한예준"]
    assert [m.group(1) for m in TABLE_NAME_RE.finditer("| 담당 | skcc.x1042 |")] == []
    assert [m.group(1) for m in TABLE_NAME_RE.finditer("| 진행 중인 업무 수 | 5건 |")] == []


def test_a_real_display_name_is_still_a_violation_with_its_id_as_the_fix():
    """result_integrator.md: "never translate ids into names". 그런데 이 검사는 **날조만** 봐서
    실재하는 실명은 그냥 통과했다(실측 EDGE13: "담당자 한예준"). 화면은 사번을 뱃지·프로필로
    렌더하고, 실명은 동명이인·표기 흔들림에 취약해 검증이 안 된다.
    실값을 알고 있으니 **고칠 사번까지** 쥐여 준다 — '지워라'가 아니라 '바꿔라'다."""
    from app.agent.workflow import grounding
    r = grounding.check("| 항목 | 값 |\n|---|---|\n| 담당자 | 한예준 |\n")
    assert not r["ok"] and r["name_as_id"].get("한예준") == "skcc.x1210", r
    note = grounding.violation_note(r)
    assert "skcc.x1210" in note and "바꿔" in note, note
    # 사번으로 쓴 답은 통과한다
    assert grounding.check("담당 후보는 skcc.x1210 입니다.")["ok"]

def test_a_quoted_title_sharing_only_common_words_is_flagged():
    """실측(사용자 관점 리뷰 F6, blocker): 흔한 낱말 둘로 제목을 안 척했다.

    답변은 `DL-9008 '[내Task] Epic 없는 내 Task — 마감 초과'` 라고 단정했는데 실물은
    `[UI] 마감 초과(D+) — 기한 붉은 강조` 였다. 겹친 것은 '마감'·'초과' — **아무 티켓에나
    있는 말**이다. "한 토큰이라도 겹치면 통과"면 이 가드는 있으나 마나다.
    """
    key, title = _real_key_and_title()
    g = grounding.check(f"- **{key} '[내Task] Epic 없는 내 Task — 마감 초과'** — 진행 중")
    # 실제 제목과 겹치는 흔한 낱말이 있어도, 없던 말을 잔뜩 더한 **따옴표 단정**은 걸린다.
    if not (set(title.split()) & {"마감", "초과"}):
        assert key in g["wrong_titles"], (title, g)


def test_a_shortened_quoted_title_is_not_flagged():
    """줄여 부르는 것은 정당하다 — 부분집합이면 통과."""
    key, title = _real_key_and_title()
    head = " ".join(title.replace("[", "").replace("]", "").split()[:2])
    g = grounding.check(f'{key} "{head}" 는 진행 중')
    assert key not in g["wrong_titles"], (title, g)


def test_two_separately_quoted_ticket_keys_are_not_parsed_as_one_title():
    key, _ = _real_key_and_title()
    g = grounding.check(f"현재 '{key}' 티켓이 진행 중이며, '{key}' 티켓도 확인했습니다.")
    assert key not in g["wrong_titles"], g


def test_ticket_table_word_task_is_not_parsed_as_a_person():
    key, _ = _real_key_and_title()
    g = grounding.check(f"- {key}: 작업 진행 중")
    assert "작업" not in g["fake_people"], g


def test_atomic_fact_ledger_supersedes_only_later_direct_same_subject_field():
    from app.agent.workflow.evidence_index import build_atomic_fact_ledger

    trusted = [
        {
            "subject_id": "ACME-41", "predicate": "validation_state",
            "value": "not run", "state": "pending",
            "observed_at": "2026-08-01T09:00:00+09:00", "direct": True,
            "source_id": "ticket:ACME-41#document",
            "provenance": "query_runner.ticket[ACME-41].description",
            "authority": "query_runner_deterministic",
        },
        {
            "subject_id": "ACME-41", "predicate": "validation_state",
            "value": "completed", "state": "completed",
            "observed_at": "2026-08-10T18:00:00+09:00", "direct": True,
            "source_id": "ticket:ACME-41#comment:7",
            "provenance": "query_runner.ticket[ACME-41].comments[7]",
            "authority": "query_runner_deterministic",
        },
    ]

    facts = [row for row in build_atomic_fact_ledger({}, extra_facts=trusted)
             if row["subject_id"] == "ACME-41"
             and row["predicate"] == "validation_state"]

    assert [(row["value"], row["temporal_role"]) for row in facts] == [
        ("not run", "historical"), ("completed", "current"),
    ]
    assert {row["source_id"] for row in facts} == {
        "ticket:ACME-41#document", "ticket:ACME-41#comment:7",
    }
    assert all(row["provenance"] for row in facts)


def test_temporal_relation_never_merges_another_actor_or_predicate():
    """Progression is exact on entity and relation, not broad topic similarity."""
    from app.agent.workflow.evidence_index import build_atomic_fact_ledger

    trusted = [
        {"subject_id": "actor:acme-writer", "predicate": "artifact_generation",
         "value": "not performed", "observed_at": "2026-08-01T09:00:00Z",
         "direct": True, "source_id": "document:writer-plan",
         "provenance": "document:writer-plan#observation:1", "authority": "query_runner"},
        {"subject_id": "actor:acme-writer", "predicate": "artifact_generation",
         "value": "completed", "observed_at": "2026-08-10T09:00:00Z",
         "direct": True, "source_id": "ticket:ACME-12#comment:2",
         "provenance": "ticket:ACME-12#comment:2", "authority": "query_runner"},
        {"subject_id": "actor:acme-reader", "predicate": "artifact_generation",
         "value": "not performed", "observed_at": "2026-08-11T09:00:00Z",
         "direct": True, "source_id": "ticket:ACME-13#comment:1",
         "provenance": "ticket:ACME-13#comment:1", "authority": "query_runner"},
        {"subject_id": "actor:acme-writer", "predicate": "artifact_consumption",
         "value": "not performed", "observed_at": "2026-08-12T09:00:00Z",
         "direct": True, "source_id": "ticket:ACME-14#comment:1",
         "provenance": "ticket:ACME-14#comment:1", "authority": "query_runner"},
    ]

    facts = build_atomic_fact_ledger({}, extra_facts=trusted)
    by_relation = {(row["subject_id"], row["predicate"], row["value"]):
                   row["temporal_role"] for row in facts}

    assert by_relation[("actor:acme-writer", "artifact_generation", "not performed")] \
        == "historical"
    assert by_relation[("actor:acme-writer", "artifact_generation", "completed")] \
        == "current"
    assert by_relation[("actor:acme-reader", "artifact_generation", "not performed")] \
        == "observed"
    assert by_relation[("actor:acme-writer", "artifact_consumption", "not performed")] \
        == "observed"


def test_structured_claim_provenance_binds_source_and_observation_before_renumbering():
    from app.agent.workflow.evidence_index import (
        build_claim_provenance_graph, canonicalize_evidence_index,
    )
    from app.agent.workflow.claim_provenance import bind_evidence_provenance

    evidence = [
        {"key": "ACME-12", "title": "AcmeWriter validation", "observations": [
            {"source": "description", "text": "AcmeWriter output validation plan",
             "subject_id": "component:acme-writer", "predicate": "generation_state",
             "value": "planned", "claim_kind": "state", "temporal_role": "historical",
             "direct": True},
            {"source": "comment", "text": "AcmeWriter output generation completed",
             "subject_id": "component:acme-writer", "predicate": "generation_state",
             "value": "completed", "claim_kind": "completion", "temporal_role": "current",
             "direct": True},
        ]},
        {"key": "HTTPS://SPEC.example/acme-format/",
         "title": "Acme format specification", "observations": [
             {"source": "external", "text": "The format can store statistics"},
         ]},
    ]
    source = (
        "AcmeWriter output generation completed [[1-b]]. "
        "The public format can store statistics [2-a]."
    )

    bound = bind_evidence_provenance(evidence)
    assert bound[0]["observations"] == evidence[0]["observations"]
    assert bound[0]["_provenance"]["observations"][1]["observation_id"]
    observation_facts = _trusted_observation_facts(evidence)
    graph = build_claim_provenance_graph(
        source, evidence, observation_facts=observation_facts,
    )
    assert len(graph["claims"]) == 2
    assert {row["source_id"] for row in graph["claims"]} == {
        "ticket:ACME-12", "url:https://spec.example/acme-format",
    }
    writer = next(row for row in graph["claims"]
                  if row["source_id"] == "ticket:ACME-12")
    assert writer["observation_id"] in {
        row["observation_id"] for row in graph["observations"]
        if row["source_id"] == "ticket:ACME-12" and row["source"] == "comment"
    }
    external = next(row for row in graph["sources"]
                    if row["source_id"] == "url:https://spec.example/acme-format")
    assert external["internal_readiness_authority"] is False

    reordered = build_claim_provenance_graph(
        "AcmeWriter output generation completed [2-b].", [evidence[1], evidence[0]],
        observation_facts=observation_facts,
    )
    assert reordered["claims"][0]["claim_id"] == writer["claim_id"]
    assert reordered["claims"][0]["source_id"] == writer["source_id"]

    source_scope = build_claim_provenance_graph("AcmeWriter source context [1].", evidence)
    source_claim = source_scope["claims"][0]
    assert source_claim["observation_id"].endswith("#observation:source-scope")
    assert any(row["observation_id"] == source_claim["observation_id"]
               for row in source_scope["observations"])

    rendered = canonicalize_evidence_index(
        source, evidence=evidence, observation_facts=observation_facts,
    )
    assert "근거 확인 필요" not in rendered
    assert "[[" not in rendered
    assert "completed [1-b]" in rendered
    assert "statistics [2]" in rendered


def test_source_coverage_keeps_entity_completeness_separate_from_source_green():
    from app.agent.workflow.source_coverage import _requested_source_coverage

    state = {
        "intent": "ask",
        "request_text": "Jira 티켓 근거를 조사해줘",
        "query_plan": {"queries": [{
            "id": "jira", "source": "jira", "query": "AcmeWriter AcmeReader",
        }]},
        "query_results": [{
            "id": "jira", "source": "jira", "result": {
                "tickets": [{"key": "ACME-10"}], "returned": 1, "total": 1,
                "complete": True,
                "entityCoverage": {
                    "mode": "bounded_one_hop", "rootKeys": ["ACME-10"],
                    "selectedKeys": ["ACME-11"], "complete": False,
                    "truncated": True,
                },
            },
        }],
    }

    row = _requested_source_coverage(state)[0]
    assert row["status"] == "covered" and row["usable_as_evidence"] is True
    assert row["entity_coverage_status"] == "bounded"
    assert row["entity_coverage_complete"] is False


def test_atomic_fact_ledger_never_inherits_parent_due_into_due_null_child():
    from app.agent.workflow.evidence_index import build_atomic_fact_ledger

    state = {"materialized_ticket_sources": {"ticketDetails": [
        {"key": "ACME-10", "type": "Task", "duedate": "2026-09-30",
         "updated": "2026-08-17T10:00:00+09:00"},
        {"key": "ACME-11", "type": "Sub-Task", "parentKey": "ACME-10",
         "duedate": "", "updated": "2026-08-18T10:00:00+09:00"},
    ]}}

    facts = build_atomic_fact_ledger(state)

    assert any(row["subject_id"] == "ACME-10" and row["predicate"] == "duedate"
               and row["value"] == "2026-09-30" for row in facts)
    assert not any(row["subject_id"] == "ACME-11" and row["predicate"] == "duedate"
                   for row in facts)
    assert any(row["subject_id"] == "ACME-11" and row["predicate"] == "parent_key"
               and row["value"] == "ACME-10" for row in facts)


def test_atomic_fact_ledger_keeps_actor_values_and_provenance_separate():
    from app.agent.workflow.evidence_index import build_atomic_fact_ledger

    trusted = [
        {"subject_id": "actor:alice", "predicate": "completed_count",
         "value": "3", "observed_at": "2026-08-15", "direct": True,
         "source_id": "portfolio:actor:alice", "provenance": "portfolio[alice].completed",
         "authority": "portfolio_deterministic"},
        {"subject_id": "actor:bob", "predicate": "completed_count",
         "value": "1", "observed_at": "2026-08-16", "direct": True,
         "source_id": "portfolio:actor:bob", "provenance": "portfolio[bob].completed",
         "authority": "portfolio_deterministic"},
    ]

    facts = [row for row in build_atomic_fact_ledger({}, extra_facts=trusted)
             if row["predicate"] == "completed_count"]

    assert {(row["subject_id"], row["value"]) for row in facts} == {
        ("actor:alice", "3"), ("actor:bob", "1"),
    }
    assert {row["source_id"] for row in facts} == {
        "portfolio:actor:alice", "portfolio:actor:bob",
    }
    assert all(row["temporal_role"] == "observed" for row in facts)


def test_atomic_fact_ledger_rejects_model_rebinding_to_a_different_ticket():
    from app.agent.workflow.evidence_index import build_atomic_fact_ledger

    state = {"evidence": [{
        "key": "ACME-20", "title": "Acme source ticket", "observations": [{
            "source": "comment", "text": "borrowed due",
            "subject_id": "ACME-99", "predicate": "duedate", "value": "2026-09-30",
            "observed_at": "2026-08-18", "direct": True,
        }],
    }]}

    facts = build_atomic_fact_ledger(state)

    assert len(facts) == 1
    assert facts[0]["subject_id"] == "ACME-20"
    assert facts[0]["source_id"] == "ticket:ACME-20"
    assert facts[0]["predicate"] == "untyped"
    assert facts[0]["typed"] is False
    assert facts[0]["direct"] is False
    assert not any(row["subject_id"] == "ACME-99" for row in facts)


def test_model_evidence_cannot_fabricate_actor_or_supersede_canonical_field():
    from app.agent.workflow.evidence_index import build_atomic_fact_ledger

    state = {
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "ACME-20", "assignee": "acct.alice", "status": "In Progress",
            "updated": "2026-08-18T08:00:00Z",
        }]},
        "evidence": [{
            "key": "ACME-20", "title": "Acme source ticket", "observations": [{
                "source": "comment", "text": "fabricated reassignment",
                "actor_id": "actor:mallory", "predicate": "assignee",
                "value": "actor:mallory", "observed_at": "2026-08-19T08:00:00Z",
                "direct": True,
            }],
        }],
    }

    facts = build_atomic_fact_ledger(state)
    canonical = next(row for row in facts
                     if row["authority"] == "materialized_ticket_sources"
                     and row["predicate"] == "assignee")
    model_row = next(row for row in facts if row["authority"] == "evidence")

    assert canonical["value"] == "acct.alice"
    assert canonical["temporal_role"] == "current"
    assert model_row["subject_id"] == "ACME-20"
    assert model_row["typed"] is False and model_row["direct"] is False
    assert not any(row["subject_id"] == "actor:mallory" and row["typed"] for row in facts)


def test_model_evidence_cannot_make_materialized_current_historical():
    from app.agent.workflow.evidence_index import build_atomic_fact_ledger

    state = {
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "ACME-21", "status": "In Progress",
            "updated": "2026-08-18T08:00:00Z",
        }]},
        "evidence": [{
            "key": "ACME-21", "observations": [{
                "source": "field", "text": "fabricated completion",
                "subject_id": "ACME-21", "predicate": "status", "value": "Done",
                "observed_at": "2026-08-19T08:00:00Z", "direct": True,
            }],
        }],
    }

    facts = build_atomic_fact_ledger(state)
    canonical = next(row for row in facts
                     if row["authority"] == "materialized_ticket_sources"
                     and row["predicate"] == "status")
    fabricated = next(row for row in facts if row["authority"] == "evidence")

    assert canonical["temporal_role"] == "current"
    assert fabricated["typed"] is False and fabricated["direct"] is False
    assert not any(row["predicate"] == "status" and row["value"] == "Done"
                   and row["typed"] for row in facts)


def test_canonical_matching_evidence_is_typed_without_displacing_snapshot():
    from app.agent.workflow.evidence_index import build_atomic_fact_ledger

    state = {
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "ACME-22", "status": "Done", "updated": "2026-08-18T08:00:00Z",
        }]},
        "evidence": [{
            "key": "ACME-22", "observations": [{
                "source": "field", "text": "status Done",
                "subject_id": "ACME-22", "predicate": "status", "value": "Done",
                "observed_at": "2026-08-19T08:00:00Z", "direct": True,
            }],
        }],
    }

    facts = build_atomic_fact_ledger(state)
    canonical = next(row for row in facts
                     if row["authority"] == "materialized_ticket_sources")
    matching = next(row for row in facts if row["authority"] != "materialized_ticket_sources")

    assert canonical["temporal_role"] == "current"
    assert matching["typed"] is True and matching["direct"] is True
    assert matching["authority"] == "materialized_match"


def test_canonical_comment_matches_supply_provenance_not_model_temporal_relation():
    from app.agent.workflow.evidence_index import build_atomic_fact_ledger

    state = {
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "ACME-23", "updated": "2026-08-10T18:00:00Z",
            "comments": [
                {"created": "2026-08-01T09:00:00Z", "body": "validation not run"},
                {"created": "2026-08-10T18:00:00Z", "body": "validation completed"},
            ],
        }]},
        "evidence": [{
            "key": "ACME-23", "observations": [
                {"source": "comment", "text": "validation not run",
                 "subject_id": "ACME-23", "predicate": "validation_state",
                 "state": "fabricated pending label",
                 "provenance": "fabricated:other-actor"},
                {"source": "comment", "text": "validation completed",
                 "subject_id": "ACME-23", "predicate": "validation_state",
                 "state": "fabricated completion label"},
            ],
        }],
    }

    facts = [row for row in build_atomic_fact_ledger(state)
             if row["authority"] == "materialized_match"]

    assert [(row["value"], row["observed_at"], row["temporal_role"])
            for row in facts] == [
        ("validation not run", "2026-08-01T09:00:00Z", "observed"),
        ("validation completed", "2026-08-10T18:00:00Z", "observed"),
    ]
    assert all(row["authority"] == "materialized_match" for row in facts)
    assert all(row["state"] == "" for row in facts)
    assert len({row["predicate"] for row in facts}) == 2
    assert all("fabricated:other-actor" not in row["provenance"] for row in facts)
    assert len({row["provenance"] for row in facts}) == 2


def test_canonical_comment_match_does_not_authorize_fabricated_value_or_actor():
    from app.agent.workflow.evidence_index import build_atomic_fact_ledger

    state = {
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "ACME-24", "comments": [{
                "created": "2026-08-18T09:00:00Z", "body": "Alice started validation",
            }],
        }]},
        "evidence": [{
            "key": "ACME-24", "observations": [{
                "source": "comment", "text": "Alice started validation",
                "actor_id": "actor:mallory", "predicate": "completed_count", "value": "99",
                "observed_at": "2026-08-19T09:00:00Z", "direct": True,
            }, {
                "source": "comment", "text": "Alice started validation",
                "predicate": "assignee", "direct": True,
            }],
        }],
    }

    model_rows = [row for row in build_atomic_fact_ledger(state)
                  if row["authority"] == "materialized_match"]

    assert len(model_rows) == 2
    assert all(row["subject_id"] == "ACME-24" for row in model_rows)
    assert all(row["predicate"].startswith("canonical_observation:")
               for row in model_rows)
    assert all(row["value"] == "Alice started validation" for row in model_rows)
    assert all(row["typed"] is True and row["direct"] is True for row in model_rows)
    assert not any(row["predicate"] in {"completed_count", "assignee"}
                   for row in model_rows)


def test_atomic_fact_ledger_preserves_contemporary_conflict_provenance():
    from app.agent.workflow.evidence_index import build_atomic_fact_ledger

    trusted = [
        {"subject_id": "ACME-30", "predicate": "batch_limit", "value": "30",
         "observed_at": "2026-08-18T09:00:00Z", "direct": True,
         "source_id": "document:acme-limit", "provenance": "document#limit",
         "authority": "query_runner_deterministic"},
        {"subject_id": "ACME-30", "predicate": "batch_limit", "value": "45",
         "observed_at": "2026-08-18T09:00:00Z", "direct": True,
         "source_id": "ticket:ACME-30#comment", "provenance": "comment#limit",
         "authority": "query_runner_deterministic"},
    ]

    facts = [row for row in build_atomic_fact_ledger({}, extra_facts=trusted)
             if row["predicate"] == "batch_limit"]

    assert {row["value"] for row in facts} == {"30", "45"}
    assert {row["temporal_role"] for row in facts} == {"conflict"}
    assert len({row["provenance"] for row in facts}) == 2


def test_atomic_fact_boundary_drops_foreign_parent_due_from_child_claim():
    from app.agent.workflow.evidence_index import (
        build_atomic_fact_ledger, enforce_atomic_fact_boundaries,
    )

    state = {"materialized_ticket_sources": {"ticketDetails": [
        {"key": "ACME-10", "duedate": "2026-09-30", "updated": "2026-08-17"},
        {"key": "ACME-11", "parentKey": "ACME-10", "updated": "2026-08-18"},
    ]}}
    source = "{{ticket-inline:ACME-11}}의 마감은 2026-09-30"

    got = enforce_atomic_fact_boundaries(
        source, build_atomic_fact_ledger(state),
    )

    assert "2026-09-30" not in got
    assert "마감 확인되지 않음" in got


def test_atomic_fact_boundary_does_not_infer_progression_from_free_text_comments():
    from app.agent.workflow.evidence_index import (
        build_atomic_fact_ledger, enforce_atomic_fact_boundaries,
    )

    state = {
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "ACME-41", "comments": [
                {"created": "2026-08-01", "body": "validation not run"},
                {"created": "2026-08-10", "body": "validation completed"},
            ],
        }]},
        "evidence": [{"key": "ACME-41", "observations": [
            {"source": "comment", "text": "validation not run",
             "predicate": "validation_state"},
            {"source": "comment", "text": "validation completed",
             "predicate": "validation_state"},
        ]}],
    }
    source = "{{ticket-inline:ACME-41}} validation not run"

    got = enforce_atomic_fact_boundaries(
        source, build_atomic_fact_ledger(state),
    )

    assert got == source
    assert "이전 기록" not in got and "현재 기록" not in got


def test_result_integrator_receives_bounded_atomic_fact_sidecar():
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    state = {
        "intent": "ask", "messages": [], "request_text": "Acme validation history",
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "ACME-41", "status": "Done", "done": True,
            "updated": "2026-08-10",
            "comments": [
                {"created": "2026-08-01", "body": "validation not run"},
                {"created": "2026-08-10", "body": "validation completed"},
            ],
        }]},
        "evidence": [{"key": "ACME-41", "observations": [
            {"source": "comment", "text": "validation not run",
             "predicate": "validation_state"},
            {"source": "comment", "text": "validation completed",
             "predicate": "validation_state"},
        ]}],
    }

    task = ResultIntegrator().task(state)

    assert "Typed Atomic Fact Ledger" in task
    assert '"temporal_role": "historical"' not in task
    assert '"predicate": "canonical_observation:' in task
    assert '"temporal_role": "observed"' in task
    assert '"authority": "materialized_ticket_sources"' in task
    assert 'materialized_ticket_sources.ticketDetails[ACME-41].comments[0]' in task
    assert "Never transfer a value across subject_id or predicate" in task


def test_atomic_fact_cap_keeps_late_temporal_group_complete():
    from app.agent.workflow.evidence_index import atomic_fact_sidecar

    details = []
    for index in range(1, 9):
        details.append({
            "key": f"ACME-{index}", "status": "Done", "done": True,
            "assignee": f"acct.user{index}", "duedate": "2026-09-30",
            "parentKey": "ACME-100", "epicKey": "ACME-200", "priority": "High",
            "resolution": "Fixed", "sp": index, "components": ["Core"],
            "labels": ["acme"], "summary": f"Acme item {index}", "type": "Task",
            "updated": "2026-08-10", "comments": ([
                {"created": "2026-08-01", "body": "validation not run"},
                {"created": "2026-08-10", "body": "validation completed"},
            ] if index == 8 else []),
        })
    state = {
        "materialized_ticket_sources": {"ticketDetails": details},
        "evidence": [{"key": "ACME-8", "observations": [
            {"source": "comment", "text": "validation not run",
             "predicate": "validation_state"},
            {"source": "comment", "text": "validation completed",
             "predicate": "validation_state"},
        ]}],
    }

    trusted_progression = [
        {"subject_id": "ACME-8", "predicate": "validation_state",
         "value": "not run", "observed_at": "2026-08-01", "direct": True,
         "source_id": "ticket:ACME-8#comment:1", "authority": "query_runner"},
        {"subject_id": "ACME-8", "predicate": "validation_state",
         "value": "completed", "observed_at": "2026-08-10", "direct": True,
         "source_id": "ticket:ACME-8#comment:2", "authority": "query_runner"},
    ]
    sidecar = atomic_fact_sidecar(state, extra_facts=trusted_progression)
    progression = [row for row in sidecar if row["predicate"] == "validation_state"]

    assert len(sidecar) <= 24
    assert {row["temporal_role"] for row in progression} == {"historical", "current"}


def test_portfolio_activity_atomic_facts_bind_each_value_to_exact_actor():
    from app.agent.workflow.agents.portfolio_analyst import activity_atomic_facts
    from app.agent.workflow.evidence_index import build_atomic_fact_ledger

    material = (
        "[로스터] Acme: acct.alice, acct.bob (2명)\n"
        "[조회 기간] 최근 7일\n"
        "[acct.alice] 담당/변경 티켓: ACME-1 \"export\"(Done) | "
        "코멘트 등 활동: ACME-1 rollout note | 문서 활동: export guide\n"
        "[acct.bob] 담당/변경 티켓: ACME-2 \"import\"(Open) | "
        "코멘트 등 활동: 없음 | 문서 활동: import guide"
    )

    facts = build_atomic_fact_ledger({}, extra_facts=activity_atomic_facts(material))

    assert {(row["subject_id"], row["predicate"], row["value"]) for row in facts} == {
        ("acct.alice", "assigned_or_changed_tickets", 'ACME-1 "export"(Done)'),
        ("acct.alice", "jira_activity", "ACME-1 rollout note"),
        ("acct.alice", "document_activity", "export guide"),
        ("acct.bob", "assigned_or_changed_tickets", 'ACME-2 "import"(Open)'),
        ("acct.bob", "jira_activity", "없음"),
        ("acct.bob", "document_activity", "import guide"),
    }
    assert all(row["source_id"] == f"portfolio:activity:{row['subject_id']}"
               for row in facts)
    assert all(row["temporal_role"] == "observed" for row in facts)


def test_exact_date_math_corrects_weekday_and_exposes_relative_conflict_without_action():
    from app.agent.workflow.evidence_index import enforce_atomic_fact_boundaries

    source = (
        "2026-08-11부터 2026-08-25(금요일)까지 한 주이므로 즉시 배포를 권고합니다."
    )

    got = enforce_atomic_fact_boundaries(source, [])

    assert "2026-08-25(화요일)" in got
    assert "정확히 14일" in got and "한 주" in got and "불일치" in got
    assert "배포를 권고" not in got
    assert got.startswith("2026-08-11부터"), "유용한 exact-date 문맥은 보존해야 함"


def test_completion_claim_rebinds_to_current_direct_typed_observation():
    from app.agent.workflow.claim_provenance import build_claim_provenance_graph
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    evidence = [{
        "key": "ACME-71", "title": "AtlasWriter validation", "observations": [{
            "source": "description", "text": "AtlasWriter generation is planned",
            "subject_id": "component:atlas-writer", "predicate": "generation_state",
            "value": "planned", "claim_kind": "state", "temporal_role": "historical",
            "direct": True, "observed_at": "2026-08-01T09:00:00Z",
        }, {
            "source": "comment", "text": "AtlasWriter generation completed",
            "subject_id": "component:atlas-writer", "predicate": "generation_state",
            "value": "completed", "claim_kind": "completion", "temporal_role": "current",
            "direct": True, "observed_at": "2026-08-10T09:00:00Z",
        }],
    }]
    source = "AtlasWriter generation completed [1-a]."
    claim_facts = [{
        "citation_index": 1, "subject_id": "component:atlas-writer",
        "predicate": "generation_state", "value": "completed",
        "claim_kind": "completion", "direct": True,
        "authority": "result_claim_sidecar",
    }]

    observation_facts = _trusted_observation_facts(evidence)
    graph = build_claim_provenance_graph(
        source, evidence, observation_facts=observation_facts,
        claim_facts=claim_facts,
    )
    rendered = canonicalize_evidence_index(
        source, evidence=evidence, observation_facts=observation_facts,
        claim_facts=claim_facts,
    )

    assert graph["claims"][0]["entailment"] == "rebound"
    assert graph["claims"][0]["observation_ordinal"] == 2
    assert graph["unsupported_claim_ids"] == []
    assert "completed [1-b]" in rendered
    assert "직접 완료 근거 확인 필요" not in rendered


def test_completion_claim_is_qualified_when_only_plan_or_noncompletion_exists():
    from app.agent.workflow.claim_provenance import build_claim_provenance_graph
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    evidence = [{
        "key": "ACME-72", "title": "AcmeReader validation", "observations": [{
            "source": "description", "text": "AcmeReader validation plan",
            "subject_id": "component:acme-reader", "predicate": "validation_state",
            "value": "planned", "claim_kind": "state", "temporal_role": "historical",
            "direct": True, "observed_at": "2026-08-01T09:00:00Z",
        }, {
            "source": "comment", "text": "AcmeReader validation not completed",
            "subject_id": "component:acme-reader", "predicate": "validation_state",
            "value": "in_progress", "claim_kind": "state", "temporal_role": "current",
            "direct": True, "observed_at": "2026-08-10T09:00:00Z",
        }],
    }]
    source = "AcmeReader validation completed [1-a]."

    observation_facts = _trusted_observation_facts(evidence)
    graph = build_claim_provenance_graph(
        source, evidence, observation_facts=observation_facts,
    )
    rendered = canonicalize_evidence_index(
        source, evidence=evidence, observation_facts=observation_facts,
    )

    assert graph["unsupported_claim_ids"] == [graph["claims"][0]["claim_id"]]
    assert "직접 완료 근거 확인 필요" in rendered


def test_normalized_observation_identity_dedupes_verified_presentation_variants():
    from app.agent.workflow.claim_provenance import bind_evidence_provenance
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    evidence = [{
        "key": "ACME-73", "title": "Acme review", "observations": [{
            "source": "comment", "text": "[~acct.x17] approved the rollout",
            "normalized_text": "person:acct.x17 approved the rollout",
            "observed_at": "2026-08-10T09:00:00Z",
        }, {
            "source": "comment", "text": "@Reviewer approved the rollout",
            "normalized_text": "person:acct.x17 approved the rollout",
            "observed_at": "2026-08-10T09:00:00Z",
        }],
    }]

    bound = bind_evidence_provenance(evidence)
    ids = [row["observation_id"] for row in bound[0]["_provenance"]["observations"]]
    rendered = canonicalize_evidence_index("Review recorded [1].", evidence=evidence)

    assert len(set(ids)) == 1
    assert rendered.count("approved the rollout") == 1


def test_incomplete_entity_coverage_is_disclosed_even_when_source_query_is_green():
    from app.agent.workflow.agents.result_integrator import _ensure_entity_coverage_disclosure

    state = {
        "intent": "ask", "request_text": "Jira 근거를 조사해줘",
        "query_plan": {"queries": [{"id": "jira", "source": "jira", "query": "Atlas"}]},
        "query_results": [{"id": "jira", "source": "jira", "result": {
            "tickets": [{"key": "ACME-70"}], "returned": 1, "total": 1,
            "complete": True, "entityCoverage": {
                "rootKeys": ["ACME-70"], "selectedKeys": ["ACME-71"],
                "complete": False, "truncated": False,
            },
        }}],
    }

    got = _ensure_entity_coverage_disclosure("결론\n\n### 근거\n\n[1] ACME-70", state)

    assert "전체 관련 엔티티를 확인한 것은 아님" in got
    assert got.index("전체 관련 엔티티") < got.index("### 근거")


def test_plain_common_noun_grounding_candidate_does_not_trigger_result_repair(monkeypatch):
    from app.agent.workflow import grounding
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    calls = {"check": 0}

    def false_person(_text, allowed_people=None):
        calls["check"] += 1
        return {"fake_keys": [], "wrong_titles": {}, "fake_people": ["하위"],
                "real_titles": {}, "unlinked_refs": [], "name_as_id": {},
                "person_findings": [{
                    "candidate": "하위", "context_kind": "common_noun",
                    "verdict": "non_person",
                }], "ok": False}

    monkeypatch.setattr(grounding, "check", false_person)
    integrator = ResultIntegrator()
    monkeypatch.setattr(
        integrator, "llm",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("plain noun must not trigger a second Result call")),
    )

    got = integrator.apply(
        {"messages": [], "intent": "ask", "trace": []},
        {"text": "하위 작업의 현재 상태를 정리했습니다."},
    )["reply"]

    assert calls["check"] == 1
    assert "자동 검증 경고" not in got


def test_bare_person_assertion_is_not_suppressed_as_a_common_noun():
    from app.agent.workflow.agents.result_integrator import _filter_plain_person_candidates

    checked = {
        "fake_keys": [], "wrong_titles": {}, "fake_people": ["김철수"],
        "unlinked_refs": [], "name_as_id": {}, "ok": False,
    }

    got = _filter_plain_person_candidates(checked, "김철수가 진행 담당을 맡았습니다.")

    assert got["fake_people"] == ["김철수"] and got["ok"] is False


@pytest.mark.parametrize("text", [
    "담당자는 김철수 입니다.",
    "담당자: 김철수",
    "담당자 - 김철수",
    "| 담당자 | 김철수 |",
    "김철수가 진행 담당을 맡았습니다.",
])
def test_structural_person_grammar_keeps_fabricated_names_enforceable(text):
    got = grounding.check(text)

    assert "김철수" in got["fake_people"]
    finding = next(row for row in got["person_findings"]
                   if row["candidate"] == "김철수")
    assert finding["context_kind"] != "common_noun"


@pytest.mark.parametrize("text", ["담당 성능", "담당 기능", "담당 일정"])
def test_bare_responsibility_noun_is_not_parsed_as_a_person(text):
    got = grounding.check(text)

    assert not got["fake_people"] and got["ok"] is True


def test_completion_rebind_does_not_swap_subjects_that_share_a_predicate():
    from app.agent.workflow.claim_provenance import build_claim_provenance_graph

    evidence = [{
        "key": "ACME-90", "observations": [{
            "source": "description", "text": "AtlasWriter generation planned",
            "subject_id": "component:atlas-writer", "predicate": "generation_state",
            "value": "planned", "claim_kind": "state", "temporal_role": "historical",
            "direct": True, "observed_at": "2026-08-01T09:00:00Z",
        }, {
            "source": "comment", "text": "AtlasWriter generation completed",
            "subject_id": "component:atlas-writer", "predicate": "generation_state",
            "value": "completed", "claim_kind": "completion", "temporal_role": "current",
            "direct": True, "observed_at": "2026-08-10T09:00:00Z",
        }],
    }]

    graph = build_claim_provenance_graph(
        "AcmeReader generation completed [1-a].", evidence,
        observation_facts=_trusted_observation_facts(evidence),
    )

    assert graph["claims"][0]["entailment"] == "unsupported"
    assert graph["unsupported_claim_ids"] == [graph["claims"][0]["claim_id"]]


def test_completion_rebind_tie_fails_closed():
    from app.agent.workflow.claim_provenance import build_claim_provenance_graph

    evidence = [{
        "key": "ACME-90", "observations": [{
            "source": "description", "text": "AtlasWriter generation plan",
            "observed_at": "2026-08-01T09:00:00Z",
        }],
    }] + [{
        "key": key, "observations": [{
            "source": "comment", "text": "AtlasWriter generation completed",
            "subject_id": f"component:{key.casefold()}", "predicate": "generation_state",
            "value": "completed", "claim_kind": "completion", "temporal_role": "current",
            "direct": True, "observed_at": "2026-08-10T09:00:00Z",
        }],
    } for key in ("ACME-91", "ACME-92")]

    graph = build_claim_provenance_graph(
        "AtlasWriter generation completed [1].", evidence,
        observation_facts=_trusted_observation_facts(evidence),
    )

    assert graph["claims"][0]["entailment"] == "unsupported"
    assert graph["unsupported_claim_ids"] == [graph["claims"][0]["claim_id"]]


def test_multi_ticket_status_sentence_gets_an_exact_subject_field_ledger():
    from app.agent.workflow.evidence_index import (
        build_atomic_fact_ledger, enforce_atomic_fact_boundaries,
    )

    state = {"materialized_ticket_sources": {"ticketDetails": [
        {"key": "ACME-81", "status": "Closed", "updated": "2026-08-10"},
        {"key": "ACME-82", "status": "In Progress", "updated": "2026-08-10"},
    ]}}

    got = enforce_atomic_fact_boundaries(
        "ACME-81 및 ACME-82는 Jira 상태 In Progress입니다.",
        build_atomic_fact_ledger(state),
    )

    assert got == "티켓별 Jira 상태: ACME-81=Closed; ACME-82=In Progress."
    assert "ACME-81 및 ACME-82는 Jira 상태 In Progress" not in got


def test_multi_ticket_shared_status_is_preserved_when_every_binding_matches():
    from app.agent.workflow.evidence_index import (
        build_atomic_fact_ledger, enforce_atomic_fact_boundaries,
    )

    state = {"materialized_ticket_sources": {"ticketDetails": [
        {"key": "ACME-83", "status": "Closed", "updated": "2026-08-10"},
        {"key": "ACME-84", "status": "Closed", "updated": "2026-08-10"},
    ]}}
    source = "ACME-83 및 ACME-84는 Jira 상태 Closed입니다."

    got = enforce_atomic_fact_boundaries(source, build_atomic_fact_ledger(state))

    assert got == source


@pytest.mark.parametrize("separator", [". ", "; ", " | "])
def test_status_replacement_preserves_an_unrelated_neighboring_fact(separator):
    from app.agent.workflow.evidence_index import (
        build_atomic_fact_ledger, enforce_atomic_fact_boundaries,
    )

    state = {"materialized_ticket_sources": {"ticketDetails": [
        {"key": "ACME-85", "status": "Closed", "updated": "2026-08-10"},
        {"key": "ACME-86", "status": "Open", "updated": "2026-08-10"},
    ]}}
    source = (
        "ACME-85 및 ACME-86는 Jira 상태 Open입니다"
        + separator + "검토 창은 금요일입니다."
    )

    got = enforce_atomic_fact_boundaries(source, build_atomic_fact_ledger(state))

    assert "티켓별 Jira 상태: ACME-85=Closed; ACME-86=Open" in got
    assert "검토 창은 금요일입니다." in got


@pytest.mark.parametrize("marker", ["[1, 2]", "[1][2]"])
def test_combined_completion_citations_preserve_unsupported_qualification(marker):
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    evidence = [{
        "key": key, "observations": [{
            "source": "description", "text": "AtlasWriter generation plan",
        }],
    } for key in ("ACME-101", "ACME-102")]

    got = canonicalize_evidence_index(
        f"AtlasWriter generation completed {marker}.", evidence=evidence,
    )

    body = got.split("### 근거", 1)[0]
    assert "[1][2]" in body
    assert body.count("직접 완료 근거 확인 필요") == 1


def test_combined_child_citations_keep_each_typed_observation_binding():
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    evidence = [{
        "key": "ACME-103", "observations": [
            {"source": "description", "text": "export plan"},
            {"source": "comment", "text": "export result"},
        ],
    }, {
        "key": "ACME-104", "observations": [
            {"source": "description", "text": "import plan"},
            {"source": "comment", "text": "import result"},
        ],
    }]

    got = canonicalize_evidence_index(
        "Selected facts [1-a, 2-b].", evidence=evidence,
    )

    assert "Selected facts [1-a][2-b]." in got.split("### 근거", 1)[0]


def test_invalid_citation_does_not_shift_later_occurrence_bindings():
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    evidence = [{
        "key": "ACME-105", "observations": [
            {"source": "description", "text": "export plan"},
            {"source": "comment", "text": "export result"},
        ],
    }, {
        "key": "ACME-106", "observations": [
            {"source": "description", "text": "import plan"},
            {"source": "comment", "text": "import result"},
        ],
    }]

    got = canonicalize_evidence_index(
        "Unknown context [99]. Selected facts [1-a, 2-b].", evidence=evidence,
    )
    body = got.split("### 근거", 1)[0]

    assert "Unknown context (근거 확인 필요)" in body
    assert "Selected facts [1-a][2-b]." in body


@pytest.mark.parametrize("separator", [
    ". ", "; ", " | ", ", AcmeReader는 ", " 그리고 AcmeReader는 ",
    "이고 AcmeReader는 ",
])
def test_date_math_never_borrows_a_duration_from_an_unrelated_clause(separator):
    from app.agent.workflow.evidence_index import enforce_atomic_fact_boundaries

    source = (
        "A 일정: 2026-01-01부터 2026-01-08까지"
        + separator + "별도 B 작업은 3주 소요된다."
    )

    got = enforce_atomic_fact_boundaries(source, [])

    assert "3주 소요" in got
    assert "불일치" not in got and "정확히 7일" not in got


def test_canonical_evidence_heading_is_idempotent_and_glued_heading_is_separated():
    from app.agent.workflow.evidence_index import canonicalize_evidence_index

    evidence = [{"key": "ACME-107", "observations": [
        {"source": "description", "text": "Atlas context"},
    ]}]
    canonical = canonicalize_evidence_index(
        "Atlas context [1].\n\n### 근거\n[1] ACME-107", evidence=evidence,
    )
    glued = canonicalize_evidence_index(
        "Atlas context [1].### 근거\n[1] ACME-107", evidence=evidence,
    )

    for got in (canonical, glued):
        assert got.count("### 근거") == 1
        assert "\n#\n\n## 근거" not in got


def test_exact_materialized_free_text_proves_provenance_but_not_completion():
    from app.agent.workflow.evidence_index import (
        canonical_observation_facts, canonicalize_evidence_index,
    )

    text = "Atlas rollout은 검증 완료 후 실행 예정"
    state = {"materialized_ticket_sources": {"ticketDetails": [{
        "key": "ACME-108", "comments": [
            {"created": "2026-08-10T09:00:00Z", "body": text},
        ],
    }]}}
    evidence = [{"key": "ACME-108", "observations": [{
        "source": "comment", "text": text,
        # These model-supplied semantic fields must carry no authority.
        "subject_id": "component:atlas", "predicate": "rollout_state",
        "value": "completed", "claim_kind": "completion", "direct": True,
    }]}]
    facts = canonical_observation_facts(state, evidence)

    got = canonicalize_evidence_index(
        "Atlas rollout 검증을 완료했습니다 [1-a].", evidence=evidence,
        observation_facts=facts,
    )

    assert len(facts) == 1 and facts[0]["claim_kind"] == "observation"
    assert facts[0]["subject_id"] == "ACME-108"
    assert "직접 완료 근거 확인 필요" in got


def test_unrelated_materialized_comments_never_temporally_supersede_each_other():
    from app.agent.workflow.evidence_index import canonical_observation_facts

    evidence = [{"key": "ACME-109", "observations": [
        {"source": "comment", "text": "AtlasReader validation completed"},
        {"source": "comment", "text": "AcmeWriter deployment planned"},
    ]}]
    state = {"materialized_ticket_sources": {"ticketDetails": [{
        "key": "ACME-109", "comments": [
            {"created": "2026-08-01T09:00:00Z",
             "body": "AtlasReader validation completed"},
            {"created": "2026-08-10T09:00:00Z",
             "body": "AcmeWriter deployment planned"},
        ],
    }]}}

    facts = canonical_observation_facts(state, evidence)

    assert len(facts) == 2
    assert {row["temporal_role"] for row in facts} == {"observed"}
    assert len({row["predicate"] for row in facts}) == 2


def test_materialized_observation_overlay_fails_closed_on_duplicate_or_other_ticket():
    from app.agent.workflow.evidence_index import canonical_observation_facts

    text = "Atlas validation result recorded"
    state = {"materialized_ticket_sources": {"ticketDetails": [
        {"key": "ACME-110", "comments": [
            {"created": "2026-08-01T09:00:00Z", "body": text},
            {"created": "2026-08-02T09:00:00Z", "body": text},
        ]},
        {"key": "ACME-111", "comments": [
            {"created": "2026-08-03T09:00:00Z", "body": "Acme other result"},
        ]},
    ]}}
    duplicate = [{"key": "ACME-110", "observations": [
        {"source": "comment", "text": text,
         "observed_at": "2026-08-01T09:00:00Z"},
    ]}]
    swapped = [{"key": "ACME-110", "observations": [
        {"source": "comment", "text": "Acme other result"},
    ]}]

    assert canonical_observation_facts(state, duplicate) == []
    assert canonical_observation_facts(state, swapped) == []
