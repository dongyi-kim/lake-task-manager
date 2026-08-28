"""Final payload sealing and response assembly for Work Architect."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def finalize_work_architect(
        self, state, out, items, qs, draft, mode, structure, why, action, turns,
        struct_stage, typed_parent_slots, policies: Mapping[str, Any]):
    PENDING_RATIONALE_CONTRACT = policies["PENDING_RATIONALE_CONTRACT"]
    _apply_source_bound_meeting_assignments = policies["_apply_source_bound_meeting_assignments"]
    _apply_verified_evidence_obligations = policies["_apply_verified_evidence_obligations"]
    _base_title = policies["_base_title"]
    _canonicalize_meeting_mentions = policies["_canonicalize_meeting_mentions"]
    _change_plan = policies["_change_plan"]
    _collapse_repeated_summary = policies["_collapse_repeated_summary"]
    _current_request_boundary_text = policies["_current_request_boundary_text"]
    _drop_meeting_sibling_exclusions = policies["_drop_meeting_sibling_exclusions"]
    _drop_unrequested_meeting_create_fields = policies["_drop_unrequested_meeting_create_fields"]
    _ensure_meeting_background_attribution = policies["_ensure_meeting_background_attribution"]
    _ensure_meeting_reviewers = policies["_ensure_meeting_reviewers"]
    _is_create_action = policies["_is_create_action"]
    _normalize_duplicate_and_bug_questions = policies["_normalize_duplicate_and_bug_questions"]
    _normalize_priority = policies["_normalize_priority"]
    _normalize_question_contracts = policies["_normalize_question_contracts"]
    _preserve_defined_meeting_terms = policies["_preserve_defined_meeting_terms"]
    _preserve_required_user_anchors = policies["_preserve_required_user_anchors"]
    _re = policies["_re"]
    _said_defaults = policies["_said_defaults"]
    _seal_meeting_item_mentions = policies["_seal_meeting_item_mentions"]
    bind_resolved_slot_item_ids = policies["bind_resolved_slot_item_ids"]
    is_composite = policies["is_composite"]
    last_user_text = policies["last_user_text"]
    note = policies["note"]
    project_pending_rationale = policies["project_pending_rationale"]
    seal_requested_effect_contract = policies["seal_requested_effect_contract"]
    seal_work_item_identities = policies["seal_work_item_identities"]
    structure_feedback = policies["structure_feedback"]
    structure_tree = policies["structure_tree"]
    _ensure_meeting_background_attribution(state, items)
    _ensure_meeting_reviewers(state, items)
    _preserve_defined_meeting_terms(state, items)
    _seal_meeting_item_mentions(state, items)
    _drop_meeting_sibling_exclusions(state, items)
    _drop_unrequested_meeting_create_fields(state, items)
    if not any(item.get("labels") for item in items):
        draft.pop("new_labels", None)

    # 우선순위 표기 정규화 — 모델은 "P3" 라고 줄여 쓰고 Jira 는 "P3-Minor" 만 받는다.
    # Auditor 가 반려하면 재작성 왕복 하나가 통째로 날아가고, 한도 소진이면 그 지적이
    # 사용자에게 떠넘겨진다(실측: "P3는 적절한 우선순위가 아닙니다"가 답변에 노출).
    # 판단이 아니라 표기 문제다 — 코드가 정규화한다.
    for it in items:
        p = str(it.get("priority") or "").strip()
        if p:
            it["priority"] = _normalize_priority(p)

    # PMO_VIT 는 경영진 보고 현안 전용이고 트리 최상위 하나에만 붙는다 — 그런데 모델이
    # 기존 라벨 목록에서 보고는 신규 티켓 셋에 전부 붙였다(실측). 사용자가 입으로 말했을
    # 때만 남기고, 아니면 기계적으로 뗀다. 규칙 위반 라벨은 검색 노이즈가 된다.
    asked_all = _current_request_boundary_text(state)
    if "PMO_VIT" not in asked_all and "현안" not in asked_all:
        for it in items:
            if it.get("labels"):
                it["labels"] = [x for x in it["labels"] if str(x).upper() != "PMO_VIT"]

    # 변경 계획(modify)은 갈래가 통째로 다르다 — `_change_plan` 이 맡는다.
    plan, qs = _change_plan(state, out, items, qs)
    _canonicalize_meeting_mentions(state, plan)
    qs = _normalize_duplicate_and_bug_questions(state, qs, items=items, plan=plan)
    qs = _normalize_question_contracts(
        state, qs, mode=str(out.get("mode") or mode), items=items,
    )
    # ★ 바꿀 값을 **정확히 말한** 수정 요청에는 되묻지 않는다. 계획이 이미 섰으면
    #   승인 카드가 곧 확인 단계다(work_architect.md: "NEVER ask permission to proceed").
    #   실측(MOD8): "라벨 data-quality 추가하고 컴포넌트를 Catalog 로" 처럼 값을 다 준
    #   요청에 "새 라벨을 추가할까요?" 로 선회하는 일이 실행마다 갈렸다 —
    #   MODEL-COMPARISON 에도 같은 관측이 있다(4o/5 는 되묻고 mini 는 즉시 카드).
    #   신규 라벨은 카드에 '신규'로 표시되므로 사용자는 거기서 보고 판단한다.
    if qs and (plan or {}).get("key") and (plan or {}).get("changes"):
        qs = []
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(바꿀 값이 다 정해져 있어 되묻지 않았다 — "
                              "승인 카드에서 확인하고 취소할 수 있다)").strip()
    # ── "하나 더 추가해줘" — 승인 전 초안은 통째로 사라지면 안 된다 ────────
    # 실측(O1): 승인 대기 초안이 있는 상태에서 항목 추가를 요청하니 모델이 **기존 항목만**
    # 다시 내고 새 항목을 빠뜨렸다(반대로 새 항목만 내고 기존을 버리기도 한다).
    # 프롬프트 지시는 이미 있지만 mini 는 지킬 때와 아닐 때가 갈린다 → 코드가 병합한다.
    # 판정은 사용자 발화의 추가 낱말로만 한다(수정·교체 요청에는 발동하지 않는다).
    prev_items = [i for i in ((state.get("draft") or {}).get("items") or [])
                  if isinstance(i, dict) and i.get("summary")]
    if items and prev_items and mode == ((state.get("draft") or {}).get("mode") or "task") \
            and _re.search(r"(하나|한\s*개|1개|항목|티켓)?\s*더\s*(추가|만들|넣)|"
                           r"추가(해|로)\s*(줘|주세요)|덧붙",
                           last_user_text(state)):
        have = {_base_title(str(i.get("summary") or "")) for i in items}
        missing = [p for p in prev_items
                   if _base_title(str(p.get("summary") or "")) not in have]
        if missing:
            items[:0] = missing          # 기존 항목을 앞에, 새 항목은 뒤에
            draft["items"] = items
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(승인 전 초안 {len(missing)}건을 유지한 채 새 항목을 "
                                  "덧붙였다)").strip()

    # 실제 Epic 필드가 비어 있는데 판단문만 "Epic을 선택했다"고 남는 모순을 제거한다.
    # Epic 연결을 보류/제거했다는 경고는 이 긍정 동사 패턴과 달라 그대로 보존된다.
    if items and not any(str(i.get("epic") or "").strip() for i in items):
        out["rationale"] = _re.sub(
            r"(?:Epic|에픽)(?![^.\n]{0,140}(?:아니|않|못|뺐|제거|보류))"
            r"[^.\n]{0,140}(?:선택|연결|붙였|배치|포함|생성)[^.\n]*(?:\.|$)", "",
            str(out.get("rationale") or ""), flags=_re.I).strip()
    if items and not any(str(i.get("type") or "").lower() == "epic" for i in items):
        out["rationale"] = _re.sub(
            r"[^.\n]*(?:새(?:로운)?\s*)?(?:Epic|에픽)[^.\n]{0,80}(?:생성|만들)[^.\n]*(?:\.|$)",
            "", str(out.get("rationale") or ""), flags=_re.I).strip()
    # `알아서` 위임으로 초안을 이미 만들었는데 초기 질문을 rationale에 옮겨 적으면
    # ResultIntegrator가 다시 범위/완료조건 입력을 요구한다. 질문 payload가 비었고 승인 카드가
    # 존재하므로 그 문구는 상태와 모순이다.
    if items and not qs and _said_defaults(state):
        out["rationale"] = _re.sub(
            r"\n?\(사용자가\s*['\"]?알아서['\"]?라고 해서 기본값으로 채웠다:[^)]*\)",
            "", str(out.get("rationale") or "")).strip()

    # 가드들이 out["rationale"] 에 덧붙인 경고(Epic 불일치·컴포넌트 정리 등)를 초안에 반영한다
    # — draft 는 items 를 참조로 공유하지만 rationale 은 문자열이라 여기서 맞춰 줘야 한다.
    draft["rationale"] = out.get("rationale") or draft.get("rationale") or ""
    if structure and why:
        # 앞선 왕복에서 붙은 (구조: …) 줄은 **지우고** 지금 것으로 다시 쓴다 —
        # Auditor 반려로 재작성이 돌면 이유가 바뀌는데, 옛 줄이 남아 카드에 서로
        # 다른 두 이유가 떴다(실측). 구조 이유는 언제나 한 줄이어야 한다.
        draft["rationale"] = _re.sub(r"\n?\(구조: [^\n]*\)", "",
                                     draft["rationale"]).strip()
        draft["rationale"] = (draft["rationale"] + f"\n(구조: {structure} — {why})").strip()
        draft["structure_why"] = why    # 카드 헤더와 근거 줄이 같은 값을 쓴다

    # ── ★ **초안이 통째로 사라진 채 끝나지 않는다** ────────────────────────
    # 모델은 항목을 냈는데 가드들을 지나며 전부 걷힌 실행이 있었다(실측 STARR1:
    # 답변은 "Epic을 제안합니다"인데 items 가 비고 질문도 0건 — 사용자에게는 실패가
    # 아니라 **먹통**이다). 같은 부류를 이미 두 번 고쳤지만(전량 삭제 분기·부모 검사
    # 연쇄) 어느 가드가 지웠는지는 **사후에 알 수 없었다** — 지운 자리에 기록이 없어서다.
    #
    # 그래서 두 가지를 한다:
    #   ① 들어온 항목 수와 나가는 수를 비교해 **없어졌다는 사실을 rationale·trace 에 남긴다**
    #   ② 질문도 없으면 **어떻게 할지 묻는다** — 아무것도 없이 끝내는 것보다 낫다.
    # 여기서 초안을 되살리지는 않는다. 왜 걷혔는지 모른 채 되살리면 가드가 막으려던
    # 것(부모 없는 Sub-Task 등)이 그대로 승인 카드로 간다.
    came_in = len([i for i in (out.get("items") or []) if isinstance(i, dict)
                   and str(i.get("summary") or "").strip()])
    # 해석 확인 턴은 초안이 없는 것이 정상이다 — 대신 '제가 이해한 바'가 나가고 사용자가
    # 거기에 답한다. 그것마저 비었으면 아래 갈래다(막다른 턴이라는 점은 같다).
    interp_turn = bool(_is_create_action(state)
                       and str(out.get("interpretation") or "").strip()
                       and not state.get("situation"))
    if not items and not plan and not qs and not interp_turn:
        # 들어온 것이 있었으면 **몇 건이 걷혔는지** 남긴다. 애초에 없었으면(모델이 빈손)
        # 그 사실만으로도 이 갈래다 — 실측(PASTE2): 답변은 "버그 티켓을 등록하겠습니다.
        # 아래 카드에서 확인 후 승인해 주세요"인데 items 가 비어 카드가 없었다.
        # **초안도 질문도 없이 끝나는 턴은 어느 경우에도 정상이 아니다.**
        if came_in:
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(초안 {came_in}건이 검증 과정에서 모두 제외됐다)").strip()
            draft["rationale"] = out["rationale"]
        if action == "comment":
            qs = [{
                "question": "댓글 계획의 대상과 남길 내용을 확정하지 못했습니다. "
                            "대상 티켓과 댓글 내용 또는 전달 목적을 알려 주세요.",
                "kind": "text", "field": "comment", "options": [],
                "required_input": True,
                "why_required": "검증을 통과한 comment payload가 남아 있지 않음",
            }]
        elif action == "update":
            qs = [{
                "question": "기존 티켓에서 바꿀 필드와 정확한 새 값을 알려 주세요.",
                "kind": "text", "field": "change", "options": [],
                "required_input": True,
                "why_required": "검증을 통과한 update payload가 남아 있지 않음",
            }]
        else:
            qs = [{"question": "요청하신 내용으로는 만들 수 있는 티켓이 없었습니다. "
                               "어떻게 할까요?",
                       "kind": "choice", "field": "",
                       "required_input": True,
                       "why_required": "검증을 통과한 생성 payload가 남아 있지 않음",
                       "options": ["범위를 다시 알려주면 그것으로 다시 잡는다",
                                   "부모/Epic 을 지정해 그 아래로 만든다",
                                   "이번엔 만들지 않는다"]}]

    # 해석 확인 턴의 "제가 이해한 바" — ResultIntegrator 가 질문에 앞세워 보여 준다.
    # 그 외 턴에는 지난 해석이 남지 않게 비운다(오래된 해석은 오해가 된다).
    interp = (str(out.get("interpretation") or "").strip()
              if _is_create_action(state) and not items and not state.get("situation")
              else "")

    # ── 구조 합의 상태를 State 에 남긴다 ──────────────────────────────────
    # `structure_notes` 는 **누적**이다. "3번 빼줘" 다음 턴에 "1번을 둘로" 라고 하면
    # 둘 다 반영해야 한다 — 앞의 말을 잊으면 사용자는 같은 말을 반복하게 되고, 그게
    # 이 피드백 루프를 못 쓰게 만드는 유일한 실패 방식이다.
    notes = list(state.get("structure_notes") or [])
    fb = structure_feedback(state)
    if fb and not state.get("structure_ok"):
        notes.append(fb)
    st_out = {"structure_notes": notes[-8:]}
    if struct_stage:
        st_out["structure_plan"] = [
            {"summary": str(i.get("summary") or ""),
             "type": str(i.get("type") or "Task"),
             "components": [str(c) for c in (i.get("components") or []) if str(c)],
             "children": [str(c.get("summary") or "")
                          for c in (i.get("children") or []) if isinstance(c, dict)]}
            for i in items]
        st_out["structure_ok"] = False
        draft["structure_tree"] = structure_tree(items, str(items[0].get("epic") or ""))
    elif items and is_composite(items):
        st_out["structure_ok"] = True     # 살을 붙이는 단계로 넘어왔다

    # Evidence fidelity is payload authority, not prose preference.  Apply the bounded
    # verified contract after every body/grouping normalizer so a late rewrite cannot
    # silently discard a completed baseline, uncertain dependency, approval gate, or
    # reusable validation artifact.  The Auditor independently enforces the same typed
    # contract at the approval boundary.
    evidence_obligations = _apply_verified_evidence_obligations(state, items)
    if evidence_obligations:
        draft["evidence_obligations"] = evidence_obligations
    else:
        draft.pop("evidence_obligations", None)

    # Late normalizers (stage folding and approved-structure restore) can rewrite titles
    # after the first guard. Seal the same invariant again at the payload boundary.
    _preserve_required_user_anchors(state, items)

    # tier와 issue_type을 분리해 downstream/보고서가 Bug를 별도 계층으로 오해하지 않게 한다.
    # 기존 write adapter가 쓰는 `type`은 호환을 위해 함께 유지한다.
    for item in items:
        item["summary"] = _collapse_repeated_summary(item.get("summary"))
        issue_type = str(item.get("type") or item.get("issue_type") or "Task").strip()
        tier = str(item.get("tier") or "").strip().lower()
        if not tier:
            tier = "epic" if issue_type.lower() == "epic" or mode == "epic" else \
                ("subtask" if issue_type.lower().replace("-", "").startswith("subtask")
                 or mode == "subtask" else "task")
        item["tier"], item["issue_type"], item["type"] = tier, issue_type, issue_type
        for child in (item.get("children") or []):
            if isinstance(child, dict):
                child["summary"] = _collapse_repeated_summary(child.get("summary"))
                child_type = str(child.get("type") or child.get("issue_type") or "Sub-Task")
                child["tier"], child["issue_type"], child["type"] = \
                    "subtask", child_type, child_type

    # Typed outcome/source identity and exact requested fields are runtime authority.
    # Seal them only after every body/hierarchy normalizer has finished, and carry the
    # prior immutable effect snapshot across an Auditor repair instead of recomputing it
    # from mutable prose.
    _apply_source_bound_meeting_assignments(state, items)
    seal_work_item_identities(state, draft)
    bind_resolved_slot_item_ids(draft)
    seal_requested_effect_contract(
        state, draft=draft, change_plan=plan,
        force_refresh=bool(typed_parent_slots),
    )
    # Existing-ticket rationale is entirely describable by the current effect payload.
    # Rebuild it after every normalizer and effect seal so an older priority, due date,
    # comment, or title sentence cannot survive a replacement turn.
    if plan and action in {"update", "comment"}:
        plan["why"] = project_pending_rationale(change_plan=plan)
        plan["rationale_contract"] = PENDING_RATIONALE_CONTRACT
    previous_review = state.get("review") or {}
    prior_signature = str(previous_review.get("defect_signature") or "")
    if previous_review.get("ok") is False and prior_signature:
        repair_attempt = {
            "defect_signature": prior_signature,
            "payload_digest": str(previous_review.get("payload_digest") or ""),
        }
        (draft if _is_create_action(state) else plan)["repair_attempt"] = repair_attempt

    # Deterministic late questions use the same typed ownership path as model questions.
    qs = _normalize_question_contracts(
        state, qs, mode=str(draft.get("mode") or mode), items=items,
    )
    reset_review = ({"review": {}} if previous_review else {})

    return {"questions": qs, "draft": draft, "change_plan": plan, "turns": turns,
            "interpretation": interp, **st_out, **reset_review,
            "trace": note(state, self.name,
                          f"변경 계획 {plan.get('key')}" if plan else
                          ("해석 확인 " if interp and not items else "")
                          + (f"질문 {len(qs)}개 · 초안 {len(items)}건" if qs or items else "초안 없음"))}


__all__ = ["finalize_work_architect"]
