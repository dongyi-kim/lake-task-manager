"""Work Architect draft post-processing pipeline.

The agent facade passes its current policy namespace for every invocation.  Dependencies
are copied to invocation-local bindings, preserving legacy monkeypatch behavior without
shared mutable module state between concurrent requests.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.agent.workflow.work_architect.finalize import finalize_work_architect


def apply_work_architect(self, state, out, policies: Mapping[str, Any]):
    BUILD_WORDS = policies["BUILD_WORDS"]
    Intent = policies["Intent"]
    MAX_REFINE_TURNS = policies["MAX_REFINE_TURNS"]
    _align_modules_from_summary = policies["_align_modules_from_summary"]
    _apply_named_assignees = policies["_apply_named_assignees"]
    _apply_relative_due_to_single_draft = policies["_apply_relative_due_to_single_draft"]
    _apply_scoped_continuation_decisions = policies["_apply_scoped_continuation_decisions"]
    _apply_typed_parent_resolution = policies["_apply_typed_parent_resolution"]
    _asks_subtasks = policies["_asks_subtasks"]
    _authoritative_explicit_due = policies["_authoritative_explicit_due"]
    _base_title = policies["_base_title"]
    _best_item_for_request = policies["_best_item_for_request"]
    _bind_deterministic_multi_outcomes = policies["_bind_deterministic_multi_outcomes"]
    _can_parent_subtask = policies["_can_parent_subtask"]
    _complete_bug_draft_from_report = policies["_complete_bug_draft_from_report"]
    _creation_target_guard_reason = policies["_creation_target_guard_reason"]
    _current_request_boundary_text = policies["_current_request_boundary_text"]
    _dedupe_dod_rows = policies["_dedupe_dod_rows"]
    _dedupe_semantic_items = policies["_dedupe_semantic_items"]
    _delegated_parent_epic = policies["_delegated_parent_epic"]
    _delegated_question_is_blocking = policies["_delegated_question_is_blocking"]
    _delegates_existing_epic_choice = policies["_delegates_existing_epic_choice"]
    _discard_projected_assignees = policies["_discard_projected_assignees"]
    _display_base_title = policies["_display_base_title"]
    _drop_cross_item_dod = policies["_drop_cross_item_dod"]
    _drop_empty_sections = policies["_drop_empty_sections"]
    _drop_self_exclusions = policies["_drop_self_exclusions"]
    _drop_subtask_ticket_refs = policies["_drop_subtask_ticket_refs"]
    _drop_unlinked_refs = policies["_drop_unlinked_refs"]
    _drop_unneeded_meeting_questions = policies["_drop_unneeded_meeting_questions"]
    _drop_unrequested_deployment_dod = policies["_drop_unrequested_deployment_dod"]
    _drop_unrequested_nested_work = policies["_drop_unrequested_nested_work"]
    _drop_unrequested_requester_attribution = policies["_drop_unrequested_requester_attribution"]
    _drop_unverified_refs = policies["_drop_unverified_refs"]
    _enforce_agreed_structure = policies["_enforce_agreed_structure"]
    _ensure_child_descriptions = policies["_ensure_child_descriptions"]
    _ensure_minimum_task_dod = policies["_ensure_minimum_task_dod"]
    _ensure_split_exclusions = policies["_ensure_split_exclusions"]
    _execution_stage = policies["_execution_stage"]
    _existing_epic_like = policies["_existing_epic_like"]
    _expected_due_dates_by_root = policies["_expected_due_dates_by_root"]
    _expected_parent_epics_by_root = policies["_expected_parent_epics_by_root"]
    _explicit_due_instruction_status = policies["_explicit_due_instruction_status"]
    _explicit_parent_epic = policies["_explicit_parent_epic"]
    _explicit_parentless_subtask = policies["_explicit_parentless_subtask"]
    _explicit_single_mutation_from_request = policies["_explicit_single_mutation_from_request"]
    _explicit_single_subtask_request = policies["_explicit_single_subtask_request"]
    _fill_owners = policies["_fill_owners"]
    _fill_thin_bodies = policies["_fill_thin_bodies"]
    _force_item_type = policies["_force_item_type"]
    _has_concrete_work_target = policies["_has_concrete_work_target"]
    _has_lineage_game_drift = policies["_has_lineage_game_drift"]
    _has_placeholder_body = policies["_has_placeholder_body"]
    _inferred_epic_rejection = policies["_inferred_epic_rejection"]
    _is_bug_item = policies["_is_bug_item"]
    _is_create_action = policies["_is_create_action"]
    _is_epic = policies["_is_epic"]
    _known_components = policies["_known_components"]
    _known_labels = policies["_known_labels"]
    _mark_unspecified_acceptance_criteria = policies["_mark_unspecified_acceptance_criteria"]
    _materialize_creation_parts = policies["_materialize_creation_parts"]
    _merge_refs = policies["_merge_refs"]
    _minimal_grounded_body = policies["_minimal_grounded_body"]
    _missing_data_quality_target = policies["_missing_data_quality_target"]
    _missing_exact_mutation = policies["_missing_exact_mutation"]
    _missing_subtask_deliverable = policies["_missing_subtask_deliverable"]
    _module_pool = policies["_module_pool"]
    _new_epic_unmet_criteria = policies["_new_epic_unmet_criteria"]
    _normalize_due_rationale = policies["_normalize_due_rationale"]
    _normalize_question_contracts = policies["_normalize_question_contracts"]
    _pick_parent_epic = policies["_pick_parent_epic"]
    _preserve_existing_parent_topic = policies["_preserve_existing_parent_topic"]
    _preserve_explicit_value_transition = policies["_preserve_explicit_value_transition"]
    _preserve_parent_topic_in_children = policies["_preserve_parent_topic_in_children"]
    _preserve_required_user_anchors = policies["_preserve_required_user_anchors"]
    _question_requires_input = policies["_question_requires_input"]
    _re = policies["_re"]
    _recover_decided_meeting_tasks = policies["_recover_decided_meeting_tasks"]
    _recover_delegated_creation = policies["_recover_delegated_creation"]
    _recover_delegated_epic_downgrade = policies["_recover_delegated_epic_downgrade"]
    _recover_explicit_subtasks = policies["_recover_explicit_subtasks"]
    _remove_assignee_semantic_drift = policies["_remove_assignee_semantic_drift"]
    _remove_unrequested_quality_claims = policies["_remove_unrequested_quality_claims"]
    _repair_bug_facts_from_report = policies["_repair_bug_facts_from_report"]
    _repair_malformed_dod = policies["_repair_malformed_dod"]
    _repair_split_scope = policies["_repair_split_scope"]
    _repair_statistics_generation_semantics = policies["_repair_statistics_generation_semantics"]
    _required_parent_resolution_question = policies["_required_parent_resolution_question"]
    _said_defaults = policies["_said_defaults"]
    _sharpen_dod = policies["_sharpen_dod"]
    _simple_delegated_request = policies["_simple_delegated_request"]
    _split_into_children = policies["_split_into_children"]
    _task_for_module = policies["_task_for_module"]
    _task_grade_body = policies["_task_grade_body"]
    _ticket_exists = policies["_ticket_exists"]
    _ticket_kind = policies["_ticket_kind"]
    _topic_drift = policies["_topic_drift"]
    _typed_continuation_contract = policies["_typed_continuation_contract"]
    _typed_decision_values = policies["_typed_decision_values"]
    _typed_target_keys = policies["_typed_target_keys"]
    _volume_partition_children = policies["_volume_partition_children"]
    _work_action = policies["_work_action"]
    bind_single_outcome_contract = policies["bind_single_outcome_contract"]
    is_composite = policies["is_composite"]
    last_user_text = policies["last_user_text"]
    parent_selection_authority = policies["parent_selection_authority"]
    requested_outcome_contract = policies["requested_outcome_contract"]
    shape_hint = policies["shape_hint"]
    spread_volume_split = policies["spread_volume_split"]
    structure_accepted = policies["structure_accepted"]
    verified_parent_epic_candidates = policies["verified_parent_epic_candidates"]
    if (not _typed_continuation_contract(state)
            and not str((state or {}).get("intent") or "").strip()
            and isinstance(out, dict)
            and any(key in out for key in
                    ("mode", "items", "interpretation", "structure", "structure_why"))):
        # Isolated callers and old checkpoints may omit intent, but their current
        # CREATE projection is still typed provenance.  Scope this compatibility flag
        # to this apply call; an intent-less read/respond state stays non-creating.
        state = {**state, "_legacy_work_projection": "create"}
    action = _work_action(state)
    # RequestPlan cardinality is typed authority.  Generic "small/delegated" and
    # semantic-title compactors operate on mutable prose, so they may simplify only
    # when there is no explicit multi-outcome contract to preserve.
    typed_outcome_count = len(
        requested_outcome_contract(state).get("outcomes") or []
    )
    if _is_create_action(state):
        _materialize_creation_parts(out, state)
    # 문자열로 오면(구모델·fake) 구조로 승격한다 — 화면은 dict 만 다루면 된다.
    # 한 번에 필요한 질문은 3개까지 묶는다. 이후 턴에도 필수 입력이 남으면 계속 묻되,
    # 질문 수 상한을 필수값 추측 허가로 바꾸지 않는다.
    qs = []
    delegated = _said_defaults(state)
    for q in (out.get("questions") or [])[:3]:
        if isinstance(q, str) and q.strip():
            qs.append({"question": q.strip(), "kind": "text", "options": [], "field": "",
                       "required_input": not delegated,
                       "why_required": ("유효한 초안을 위해 사용자 확인 필요"
                                        if not delegated else "")})
        elif isinstance(q, dict) and str(q.get("question") or "").strip():
            required_input = q.get("required_input") is True
            why_required = str(q.get("why_required") or "").strip()
            if required_input and not why_required:
                why_required = "유효한 초안에 필요한 사용자 소유 정보가 미확정"
            qs.append({"question": str(q["question"]).strip(),
                       "kind": q.get("kind") or "text",
                       "options": [(o.get("label") or o.get("value") or "").strip()
                                   if isinstance(o, dict) else str(o).strip()
                                   for o in (q.get("options") or [])
                                   if (isinstance(o, dict) and (o.get("label") or o.get("value")))
                                   or (not isinstance(o, dict) and str(o).strip())][:5],
                       "field": q.get("field") or "",
                       "required_input": required_input,
                       "why_required": why_required})
    # 위임은 선택 질문만 제거한다. 모델의 required_input 표시는 그대로 신뢰하지 않는다.
    # 배경·DoD·Epic 같은 선택을 '필수'라고 표시해 단순 요청을 취조로 만든 실측 회귀가
    # 있었으므로, 코드가 실제 blocker(대상·정확한 변경값·계층·사람·댓글·재현)를 확인한다.
    if delegated:
        qs = [q for q in qs if _delegated_question_is_blocking(state, q)]
    qs = _drop_unneeded_meeting_questions(state, qs)
    items = [i for i in (out.get("items") or []) if isinstance(i, dict) and i.get("summary")]
    qs = _normalize_question_contracts(
        state, qs, mode=str(out.get("mode") or "task"), items=items,
    )
    # 모델이 낸 질문은 **초안을 만들기 전에 답이 필요한 질문**이다. 뒤에서 코드가
    # 붙이는 구조 확인 질문과 구분해 둔다 — 전자는 초안과 함께 내면 사용자가 무엇을
    # 승인해야 할지 모순되고, 후자는 초안의 모양을 보여 주려고 일부러 함께 낸다.
    model_questions = bool(qs)
    if not out.get("_construction"):
        _discard_projected_assignees(items)
    if _is_create_action(state) and not items and not qs:
        recovered_meeting = _recover_decided_meeting_tasks(state)
        if recovered_meeting:
            items = out["items"] = recovered_meeting
            out["mode"] = "task"
            out["interpretation"] = ""
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(회의록과 보완 답변에 담당·미할당·기한이 확정되어 "
                                  "추가 질문 없이 Task 초안을 복원했다)").strip()
            model_questions = False
    # An exact single-ticket update in the current human turn is authoritative.  A
    # long previous research turn can bias the model into returning a creation draft
    # even though RequestArchitect correctly classified the new intent as MODIFY
    # (CTX1: an fdc investigation leaked into a DL-9203 priority-only request).
    # Recover only literal, typed values; free-form bodies and inferred fields remain
    # model/interview territory.
    exact_change = _explicit_single_mutation_from_request(state)
    if exact_change and action == "update":
        out["change"] = exact_change
        out["items"] = []
        exact_fields = ", ".join(key for key in exact_change if key != "key")
        out["rationale"] = (
            f"{exact_change['key']}의 {exact_fields}만 현재 요청의 리터럴 값으로 변경"
        )
        items = []
        qs = []
        model_questions = False
    # A complete pasted incident/VoC already contains the information that a
    # reproduction interview would request.  Some models still return only a
    # generic question and no item.  Recover the report into a conservative Bug
    # draft before hierarchy/placement/body guards run; do not invent missing facts.
    if _is_create_action(state) and not items:
        recovered_bug = _complete_bug_draft_from_report(state)
        if recovered_bug:
            items = out["items"] = [recovered_bug]
            out["mode"] = "task"
            qs = []
            model_questions = False
            rationale = _re.sub(
                r"[^.\n]*(?:재현\s*(?:경로|정보)[^.\n]{0,80}(?:필요|요청)|"
                r"추가\s*정보[^.\n]{0,40}요청)[^.\n]*(?:\.|$)",
                "", str(out.get("rationale") or ""), flags=_re.I,
            ).strip()
            out["rationale"] = (rationale
                                + "\n(신고 내용에 재현 경로·기대·실제 동작이 있어 "
                                  "중복 질문 없이 Bug 초안으로 정리했다)").strip()
    # 규칙/측정 방법만 있고 적용할 데이터 대상이 없으면 실행 가능한 초안이 아니다.
    # `널 비율 체크`만 받은 두 번째 턴에서 조기 초안을 낸 ASK2 회귀를 결정적으로 막는다.
    if _is_create_action(state) and _missing_data_quality_target(state):
        qs = [{"question": "어느 데이터셋·테이블·컬럼을 대상으로 적용할지 알려 주세요.",
               "kind": "text", "options": [], "field": "",
               "required_input": True,
               "why_required": "품질 규칙을 적용할 데이터 대상을 식별할 수 없음"}]
        model_questions = True
    if _is_create_action(state) and _missing_subtask_deliverable(state):
        qs = [{"question": "이 Sub-Task에서 수행할 구체적인 작업 내용이나 목적을 알려 주세요.",
               "kind": "text", "options": [], "field": "scope",
               "required_input": True,
               "why_required": "부모와 개수만 있고 생성할 Sub-Task의 실행 내용이 없음"}]
        model_questions = True
    human_request = _current_request_boundary_text(state)
    if action in {"create", "update", "mixed"} \
            and _missing_exact_mutation(human_request):
        qs = [{"question": "임계값을 어떤 값으로 변경할지 알려 주세요.",
               "kind": "text", "options": [], "field": "",
               "required_input": True,
               "why_required": "변경 payload에 넣을 정확한 새 임계값이 없음"}]
        model_questions = True
    explicit_create = (
        str((state or {}).get("intent") or "") == Intent.PLAN_WORK
        or (_typed_continuation_contract(state).get("action") in {"create", "mixed"})
    )
    if (explicit_create
            and _said_defaults(state)
            and not _has_concrete_work_target(_current_request_boundary_text(state))
            and not any(_has_concrete_work_target(value) for value in
                        _typed_decision_values(state, "target", "scope", "action"))):
        items.clear()
        qs = [{
            "question": "어떤 대상에 무엇을 해야 하는지 구체적인 작업 내용을 알려 주세요.",
            "kind": "text", "options": [], "field": "target",
            "required_input": True,
            "why_required": "티켓의 작업 대상과 실행할 행동을 식별할 수 없음",
        }]
        model_questions = True
    # Query's deterministic compiler has already proved that this PLAN_WORK turn has
    # only execution controls and no creation subject. This typed provenance outranks a
    # plausible model-invented item regardless of delegation; no downstream recovery or
    # approval path may turn ``[ETL] data pipeline``-style filler into a live draft.
    target_guard_reason = _creation_target_guard_reason(state)
    if target_guard_reason:
        items.clear()
        out["items"] = []
        qs = [{
            "question": "어떤 대상에 무엇을 해야 하는지 구체적인 작업 내용을 알려 주세요.",
            "kind": "text", "options": [], "field": "target",
            "required_input": True,
            "why_required": target_guard_reason,
        }]
        model_questions = True
    # A non-native small model can return valid JSON but leave ``items=[]`` even after
    # the user delegated every optional choice.  Repeating the same call produced the
    # same empty object and a generic "만들 수 있는 티켓 없음" interview.  Recover only
    # from the literal request when all real blocker guards above are clear; the normal
    # hierarchy, decomposition, assignment, body, and Auditor passes still run below.
    if _is_create_action(state) and not items and not qs and state.get("situation"):
        epic_downgrade = _recover_delegated_epic_downgrade(state)
        recovered = ([] if epic_downgrade else _recover_delegated_creation(state))
        if epic_downgrade:
            items = out["items"] = epic_downgrade["items"]
            mode = out["mode"] = epic_downgrade["mode"]
            out["structure"] = epic_downgrade["structure"]
            out["structure_source"] = epic_downgrade["structure_source"]
            out["structure_why"] = epic_downgrade["structure_why"]
            out["interpretation"] = ""
            out["rationale"] = epic_downgrade["rationale"]
            out["_construction"] = "literal_delegated"
            out["_epic_downgrade"] = True
            model_questions = False
        elif recovered:
            items = out["items"] = recovered
            mode = out["mode"] = "task"
            inferred_shape = (shape_hint(state)[0]
                              or ("multiple_tasks" if len(recovered) > 1
                                  else "single_task"))
            out["structure"] = inferred_shape
            out["structure_source"] = "user_specified" if shape_hint(state)[0] else "inferred"
            out["structure_why"] = "사용자가 위임한 구체 작업을 최소 실행 범위로 복원"
            out["interpretation"] = ""
            out["rationale"] = "사용자 리터럴 요청과 안전 기본값으로 초안 구성"
            out["_construction"] = "literal_delegated"
            model_questions = False
    for item in items:
        if item.get("issue_type") and not item.get("type"):
            item["type"] = item["issue_type"]
        elif item.get("type"):
            # `type` is the canonical creation field.  Keeping an older model-produced
            # `issue_type=Epic` beside a repaired `type=Task` made the final normalizer
            # silently turn the item back into an Epic (STR3).
            item["issue_type"] = item["type"]
    mode = out.get("mode") or "task"
    # 조사까지 끝난 명시적 "기존 Task 아래 A와 B Sub-Task 추가" 요청에서 모델이
    # interpretation만 내고 items를 비우는 변동이 있다. 대상·부모·산출물이 모두 사용자
    # 문장에 있으므로 다시 묻지 않고 최소 초안을 결정적으로 복원한다(SUB2 실측).
    if _is_create_action(state) and not items and not qs and state.get("situation"):
        recovered = _recover_explicit_subtasks(state)
        if recovered:
            items = out["items"] = recovered
            mode = out["mode"] = "subtask"
            out["interpretation"] = ""
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(명시된 기존 부모와 Sub-Task 목록으로 빈 초안을 복원했다)").strip()
    # 사용자가 직전 턴의 구조를 승인했다면 이번 model output은 **본문 재료**일 뿐이다.
    # 제목·순서·module·자식 관계는 이미 사용자와 합의한 값이므로 코드가 그대로 복원한다.
    # 합의 신호를 apply 끝에서야 structure_ok=True로 쓰던 탓에, 승인 직후 한 번은 model이
    # 구조를 다시 짰고 STR2에서 Runtime이 Workbench로 돌아갔다.
    _enforce_agreed_structure(state, items)
    # Sub-Task 는 자식을 가질 수 없다 — subtask 모드 항목에 모델이 children 을 또 달면
    # (실측: 같은 내용을 items 와 children 에 이중으로) 떼어 낸다.
    if mode == "subtask":
        for i in items:
            i.pop("children", None)
    # ── 사용자가 입으로 지정한 담당("성능 측정은 x1402")은 **코드가 보장**한다 —
    # force_rule 로 지시해도 모델이 떨어뜨리는 일이 반복됐다(실측 PAR1 2회).
    _apply_named_assignees(state, items)
    _apply_scoped_continuation_decisions(state, items, mode, qs)
    # ★ 기계적 가드 — task 배치에 Sub-Task 가 섞이면 그 항목은 뺀다. 프롬프트로 막았는데도
    #   실 모델이 섞어 낸 적이 있고, 그대로 두면 검증 실패 → 재작성 왕복만 태우다
    #   한도 소진으로 끝난다. 빼는 것이 반려보다 낫다(부모 생성 후 2차 승인으로 붙일 수 있다).
    # 모델이 parent 를 비운 채 Sub-Task 를 내는 일이 잦다 — 사용자가 "DL-9090 밑에"
    # 라고 지목했으면 그 키가 부모다(실재는 조사에서 이미 확인됐다). **모드와 무관하게**
    # 채운다: mode=subtask 로 내면서 parent 만 빠뜨리면 검증에서 통째로 반려돼
    # "만들겠습니다" 라고 말해 놓고 초안이 0건이 된다(실측: PAR1).
    named = [k for k in dict.fromkeys([
        *_typed_target_keys(state), *(state.get("mentioned_keys") or []),
    ]) if _ticket_exists(k)]
    # 사용자가 지목한 부모 자체가 Sub-Task/Epic이면 그 아래에 Sub-Task를 또 만들 수 없다.
    # 예전 검사는 "Epic이 아닌 실재 티켓"만 봐서 Sub-Task를 부모로 승인했고, 답변은
    # 불가능하다고 말하면서 payload에는 생성될 항목이 남는 모순이 생겼다. 불가능한 요청은
    # 임의의 최상위 Task로 바꾸지 않고 초안을 비운 뒤, 가능한 대안을 한 번만 묻는다.
    invalid_named = [k for k in named if _asks_subtasks(state)
                     and not _can_parent_subtask(k)]
    if invalid_named:
        items.clear()
        mode = out["mode"] = "subtask"
        kinds = ", ".join(f"{k}({_ticket_kind(k) or '알 수 없는 타입'})"
                          for k in invalid_named)
        out["rationale"] = ((out.get("rationale") or "")
                            + f"\n({kinds} 아래에는 Sub-Task를 만들 수 없어 초안을 "
                              "보류했다. Epic→Task 계층 또는 기존 Task 아래의 Sub-Task만 "
                              "허용한다)").strip()
        qs = [{"question": "지목한 티켓은 Sub-Task의 부모가 될 수 없습니다. "
                           "어떤 방식으로 바꿀까요?",
               "kind": "choice", "field": "",
               "required_input": True,
               "why_required": "Sub-Task 생성에는 Task-tier 부모 또는 별도 Task 전환 결정이 필요함",
               "options": ["실제 상위 Task 아래에 형제 Sub-Task로 만든다 (권장)",
                           "별도의 최상위 Task로 만든다", "이번에는 만들지 않는다"]}]
        model_questions = False         # 코드가 만든 안전 대안이며, 초안은 이미 비웠다
    # 사용자가 부모 부재를 명시한 Sub-Task 요청은 배경·DoD를 인터뷰할 단계가 아니다.
    # 먼저 Jira에서 가능한 계층으로 바꿀지를 결정해야 한다. 실측 RULE1에서는 모델의
    # 일반 질문만 남아 사용자가 답해도 생성할 수 없는 구조가 계속 유지됐다.
    if _explicit_parentless_subtask(state) and not named:
        items.clear()
        mode = out["mode"] = "subtask"
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(Sub-Task는 Task-tier 부모가 필수이므로 부모 없는 "
                              "Sub-Task 초안을 제외하고 생성 요청을 보류했다)").strip()
        qs = [{"question": "Sub-Task는 부모 없이 만들 수 없습니다. 어떤 방식으로 바꿀까요?",
               "kind": "choice", "field": "parent",
               "required_input": True,
               "why_required": "Sub-Task 생성에는 Task-tier 부모가 필수임",
               "options": ["별도의 최상위 Task로 만든다 (권장)",
                           "부모 Task를 지정한다", "이번에는 만들지 않는다"]}]
        model_questions = False
    if named:
        for i in items:
            if (i.get("type") or "").lower().startswith("sub") or mode == "subtask":
                if not str(i.get("parent") or "").strip():
                    i["parent"] = named[0]

    # An exact one-child request is an output cardinality contract, not a suggestion
    # to decompose the child's work.  Collapse a projector-expanded list/wrapper before
    # any paid split path can turn that one Sub-Task into four descendants.
    if (_explicit_single_subtask_request(state) and items and named
            and _can_parent_subtask(named[0])
            and len((requested_outcome_contract(state).get("outcomes") or [])) <= 1):
        best = _best_item_for_request(state, items)
        best.pop("children", None)
        _force_item_type(best, "Sub-Task", "subtask")
        best["parent"] = named[0]
        items[:] = [best]
        mode = out["mode"] = "subtask"
        out["structure"] = "single_task"
        out["structure_why"] = "사용자가 기존 Task 아래 Sub-Task 한 건을 정확히 요청했다"
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(Sub-Task 한 건 요청을 그대로 보존해 추가 분해하지 않았다)"
                            ).strip()

    # ★ 새 일을 "단계별 Sub-Task"로 만들라는 요청인데 모델이 임의의 기존 티켓을 골라
    # mode=subtask 로 내면, 사용자가 요청한 **새 부모 Task가 사라진다**. S1 재검증에서
    # 부모 키를 말하지 않았는데 Sub-Task 3건만 승인 카드에 올랐다. 지목한 부모가 없는
    # 새 일은 Task 배치로 되돌려 아래 단계/번호 접기가 `Task 1 + children N`으로 만든다.
    explicit_new_tree = shape_hint(state)[0] == "task_with_subtasks" and not named
    if explicit_new_tree and mode == "subtask" and items:
        for i in items:
            _force_item_type(i, "Task", "task")
            i.pop("parent", None)
        mode = "task"
        out["mode"] = "task"
        out["structure"] = "task_with_subtasks"
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(새 일의 단계별 Sub-Task 요청이라 임의의 기존 부모에 "
                              "붙이지 않고 새 Task 아래로 묶었다)").strip()

    # ── 지목한 티켓이 부모다: 껍데기 Task 를 만들지 않는다 ─────────────
    # "DL-9090 에 서브태스크 추가해줘" 는 그 티켓 **아래**에 붙이라는 뜻인데, 모델이
    # 감싸는 새 Task 를 만들고 그 밑에 children 을 다는 일이 잦다(실측 SUB1~3) —
    # 그러면 사용자가 말한 티켓은 그대로 두고 엉뚱한 껍데기가 하나 더 생긴다.
    # "쪼개줘"인데 children 없이 껍데기 Task 만 낸 경우(단계를 작업 범위 글로만 나열 —
    # 실측 SUB1 재발): 보정 호출로 단계를 뽑아 지목한 부모의 Sub-Task 로 바꾼다.
    if named and mode == "task" and len(items) == 1 and not items[0].get("children") \
            and _asks_subtasks(state):
        fix = _split_into_children(state, items[0])
        if fix:
            items[0]["children"] = fix
    if (named and mode == "task" and len(items) == 1 and items[0].get("children")
            and _asks_subtasks(state)):
        kids0 = [c for c in items[0].get("children") or [] if isinstance(c, dict)]
        if kids0:
            items[:] = [{"summary": c.get("summary") or "", "type": "Sub-Task",
                         "parent": str(c.get("parent") or named[0]),
                         **{k: c[k] for k in ("description", "assignee", "duedate")
                            if str(c.get(k) or "").strip()}}
                        for c in kids0]
            mode = "subtask"
            out["mode"] = "subtask"
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n({named[0]} 아래에 바로 붙였다 — 감싸는 Task 를 "
                                  "새로 만들지 않았다)").strip()

    # ── Epic 모드 승격 — "새 Epic 만들어줘"에 모델이 type=Epic 항목을 내면서 mode 는
    # task 로 두는 일이 잦다(실측: epic 경로를 못 타 validate_bulk 가 Epic 타입을
    # 거부 → 재작성 소진 → 승인 카드 없이 종료). 산출물이 Epic 이면 모드도 epic 이다.
    if mode == "task" and items and (items[0].get("type") or "").strip() == "Epic":
        mode = "epic"
        out["mode"] = "epic"
        if len(items) > 1:      # epic 모드는 Epic 1건 — 나머지는 승인 후 연쇄로
            extra_items = ", ".join(str(i.get("summary") or "") for i in items[1:])
            del items[1:]
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(Epic 승인 후 이어서: {extra_items[:120]})").strip()

    # ★ 부모로 지목된 것이 **Epic 이면 버리지 말고 Epic Link 로 옮긴다.**
    #   Epic 밑에 Sub-Task 는 못 달지만 Task 는 정상이고, 사용자가 말한 것은
    #   "저 밑에서 진행하자"였다. 이 처리를 안 넣었더니 아래 `elif subs` 가 항목을
    #   **전부 걷어내 초안이 0건**이 됐다(실측 STR1: 답변만 남고 승인할 것이 없었다) —
    #   나쁜 초안을 고치려던 가드가 **초안 없음**을 만들었다. 그쪽이 더 나쁘다.
    for i in items:
        if (i.get("type") or "").lower().startswith("sub") and _is_epic(i.get("parent")):
            i["epic"] = i.pop("parent")
            _force_item_type(i, "Task", "task")
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n({i['epic']} 이 Epic 이라 Sub-Task 대신 그 아래 "
                                  "Task 로 뒀다 — Epic 밑에는 Sub-Task 를 달 수 없다)").strip()

    if mode == "task":
        subs = [i for i in items if (i.get("type") or "").lower().startswith("sub")]
        # ★ 전부 Sub-Task 이고 부모가 실재하면 **모드를 승격**한다 — 사용자가 부모를
        #   지목했는데 mode 만 task 로 잘못 낸 경우다(버리면 초안이 0건이 된다).
        if subs and len(subs) == len(items) \
                and all(_can_parent_subtask(i.get("parent")) for i in subs):
            mode = "subtask"
            out["mode"] = "subtask"
        elif subs:
            rest = [i for i in items if i not in subs]
            # ★ **떼어 내면 남는 게 있을 때만 뗀다.** 이 분기는 "부모가 이 초안 안에 같이
            #   있으니 자식은 나중에 붙이자"는 뜻인데, 전부가 Sub-Task 면 뗀 결과가
            #   **초안 0건**이다 — 답변은 "부모 티켓을 생성하여 진행하겠습니다"라고 말하고
            #   승인할 것은 없는 먹통이 된다(실측 STR1: 이 케이스가 세션 내내 네 가지
            #   모양으로 흔들린 뿌리가 여기였다). 남는 게 없으면 **Task 로 강등**하고,
            #   접기·자식 담당 채움 가드가 이어받아 "Task 하나 + Sub-Task N" 으로 만든다.
            if rest:
                items = rest
                names = ", ".join(d.get("summary", "") for d in subs)
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n(Sub-Task {len(subs)}건은 부모 생성 후 별도 승인으로 "
                                      f"붙인다: {names})").strip()
            else:
                for i in subs:
                    _force_item_type(i, "Task", "task")
                    i.pop("parent", None)
                out["rationale"] = ((out.get("rationale") or "")
                                    + "\n(부모로 삼을 티켓이 없어 Sub-Task 가 아니라 Task 로 "
                                      "냈다 — Sub-Task 는 부모가 이미 있어야 만들 수 있다)").strip()
    # ★ 반대 방향 — mode=subtask 인데 **부모가 아무 데도 없으면** task 로 강등한다.
    # Sub-Task 는 부모가 이미 있어야 만들 수 있다(knowledge/01). 부모 없는 Sub-Task 는
    # 승인 카드까지 올라가 봐야 생성에서 100% 실패하는데, 지금까지 이 방향만 막는 곳이
    # 없었다(위 승격은 task→subtask 한 방향뿐). 실측 2건:
    #   STR1  "테이블 30개 등록, 사람 나눠서" → 최상위 Sub-Task 8건, 부모 없음
    #   RULE1 "부모는 없어도 돼"             → 답변은 "만들 수 없다"인데 초안은 그대로
    # 강등만 해 두면 아래 가드들이 이어받는다 — 번호 접기(_base_title)가 "Task 하나 +
    # Sub-Task N" 으로 접고, 자식 담당 채움이 로스터로 나눈다. 접기를 여기서 또 구현하지
    # 않는 이유다(가드가 두 벌이 되면 더 관대한 쪽이 사고를 낸다).
    # ★ "부모가 없다"에는 **Epic 을 부모로 지목한 경우**도 들어간다 — Jira 에서 Epic 밑에는
    #   Sub-Task 를 못 단다. 실재 검사만 하던 때 STR1 이 Epic DL-5982 를 부모로 한
    #   Sub-Task 10건을 그대로 승인 카드까지 올렸다(답변에서는 스스로 "Epic이라 부적합"
    #   이라고 적으면서). 생성에서 100% 실패하는 초안이라 결과는 부모 없는 것과 같다.
    if mode == "subtask" and items \
            and not any(_can_parent_subtask(i.get("parent")) for i in items):
        epic_parent = any(_ticket_exists(i.get("parent")) for i in items)
        for i in items:
            _force_item_type(i, "Task", "task")
            i.pop("parent", None)
        mode = "task"
        out["mode"] = "task"
        out["rationale"] = ((out.get("rationale") or "")
                            + ("\n(부모로 지목한 것이 Epic 이라 Sub-Task 가 아니라 Task 로 냈다 — "
                               "Epic 밑에는 Sub-Task 를 달 수 없다)" if epic_parent else
                               "\n(부모로 삼을 티켓이 없어 Sub-Task 가 아니라 Task 로 냈다 — "
                               "Sub-Task 는 부모가 이미 있어야 만들 수 있다)")).strip()
    # 변환(껍데기→Sub-Task 승격 등)이 items 를 **재구성**하므로 — 지정 담당을 다시
    # 강제하고, Sub-Task 항목에 남은 children(이중 산출)을 최종적으로 뗀다.
    if mode == "subtask":
        for i in items:
            i.pop("children", None)
    _apply_named_assignees(state, items)
    _apply_scoped_continuation_decisions(state, items, mode, qs)
    turns = (state.get("turns") or 0) + 1
    # 되묻기 상한 뒤에는 선택 질문만 버린다. 필수 질문은 답이 없으면 계속 미확정이다.
    if qs and turns > MAX_REFINE_TURNS:
        qs = [q for q in qs if _question_requires_input(q)]
    # ★ **질문 또는 초안**이지, 질문과 초안이 동시에 아니다. "재현 경로가 무엇인가요"를
    # 물으면서 근거 없는 Bug 초안을 함께 승인 카드에 올리거나, 범위를 물으면서 임의의
    # Task 를 만드는 실측 실패가 반복됐다(ASK1/BUG1). 사용자가 '알아서'라고 위임한
    # 경우도 필수 입력 질문이 남으면 임의 초안을 버린다. 선택 질문은 위에서 제거됐고,
    # 뒤에서 코드가 붙이는 구조 확인 질문에는 영향 없다.
    if model_questions and qs and items:
        items.clear()
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(답이 필요한 질문이 남아 있어 임의 초안은 보류했다)"
                            ).strip()
    # Optional feedback belongs on the approval surface. ``questions`` carries only
    # values without which a truthful executable payload cannot be formed.
    # ── 시스템·픽스처 라벨은 사람이 붙이는 것이 아니다 ────────────────
    # 배치 재료로 기존 라벨 **목록**을 주니 모델이 거기서 아무거나 집었다(실측:
    # 카탈로그 검색 개선 티켓에 `ui-fixture`). 데이터 관리용 표식은 업무 티켓의
    # 라벨이 아니고, 잘못 붙으면 그 필터로 조회하는 화면이 오염된다.
    # 판정은 **딱 이 부류만** — 일반 라벨의 적절성은 사용자가 카드에서 판단한다.
    said_all = _current_request_boundary_text(state).lower()
    for it in items:
        drop_l = [str(lb) for lb in (it.get("labels") or [])
                  if _re.match(r"^(ui|dataset|test|demo|sample)[-_]?fixture$|^tbl[-_]",
                               str(lb).strip(), _re.I)
                  and str(lb).strip().lower() not in said_all]
        if drop_l:
            it["labels"] = [lb for lb in (it.get("labels") or []) if str(lb) not in drop_l]
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(데이터 관리용 라벨은 뺐다: {', '.join(drop_l[:4])})"
                                ).strip()

    # 신규 라벨은 막지 않되 **표시**한다(사용자 결정) — 오탈자·동의어가 검색을 망가뜨린다.
    known = _known_labels()
    if known:
        new_labels = sorted({str(x) for it in items for x in (it.get("labels") or [])
                             if str(x) and str(x) not in known})
        if new_labels:
            draft_new_labels = new_labels
        else:
            draft_new_labels = []
    else:
        draft_new_labels = []

    # ── 작고 명시적인 위임은 한 Task로 끝낸다 ───────────────────────────
    # "체크박스 하나 추가, 알아서" 같은 요청이 조사 문맥의 단계어를 주워 Epic+Sub-Task
    # 다섯 건으로 부풀었다. 단일 산출물·짧은 요청·전권 위임이 동시에 확인된 경우에만
    # 적용하며, 사용자가 분할/단계/복수 산출물을 말한 요청은 건드리지 않는다.
    if (mode == "task" and items and typed_outcome_count <= 1
            and _simple_delegated_request(state)
            and out.get("_construction") != "request_refinement"):
        best = _best_item_for_request(state, items)
        changed = len(items) > 1 or bool(best.get("children"))
        best.pop("children", None)
        items[:] = [best]
        if changed:
            out["structure"] = "single_task"
            out["structure_source"] = "inferred"
            out["structure_why"] = "단일 산출물 하나를 위임한 작은 변경 요청이다"
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(단일 산출물 요청이라 과도한 단계·하위 티켓을 접고 "
                                  "Task 한 건으로 정리했다)").strip()

    # 구조 판단은 **드러내 놓고** 싣는다 — 숨은 판단은 매번 달라지고 검증도 못 한다.
    # Epic 격상은 보수적으로: 새 Epic 을 고르고도 조건을 못 채웠으면(단일 모듈·소규모)
    # 코드가 되돌리지는 않되(사용자가 명시적으로 원했을 수 있다) 근거를 남기게 강제한다.
    structure = out.get("structure") or ""
    # ★ 비어 있으면 코드가 채운다. 모델이 이 필드를 빠뜨리면 **구조 가드 둘이 조용히
    #   꺼진다** — 하향 편향 보정은 "single_task" 를, 산출 어긋남 보정은
    #   "task_with_subtasks" 를 키로 보기 때문이다. 실측(생성 스위트 STR1 4회): 2회가
    #   구조 미지정으로 나왔고 그 두 번 다 두 가드가 돌지 않았다.
    #   structure 는 "숨은 판단은 매번 달라지고 검증도 못 한다"는 이유로 만든 필드인데,
    #   비어 있으면 정확히 그 상태가 된다. 여기서 채우는 것은 **의도 추측이 아니라
    #   산출물 모양의 기술**이다(몇 건인가·자식이 있는가) — 그래서 코드가 할 수 있다.
    if not structure and items:
        structure = ("multiple_tasks" if len(items) > 1
                     else "task_with_subtasks"
                     if sum(len(i.get("children") or []) for i in items) else "single_task")
        out["structure"] = structure
    why = (out.get("structure_why") or "").strip()
    # ``structure_source`` is runtime provenance, not semantic model output.  Asking a
    # projector to classify its own decision produced the invalid enum `user_request`
    # in r24 and spent a repair call even though request text and accepted state already
    # determine the answer.  Never trust or normalize a model-provided string here.
    src = "inferred"
    said_shape, _word = shape_hint(state)
    # 사용자가 새 일의 형태를 "단계별 Sub-Task"로 지정했는데 모델이 single_task 를
    # 내면 `user_specified` 표지만 붙고 산출 구조는 사용자 지정과 달라진다. 표식은
    # 보장이 아니므로 실제 structure도 코드가 맞춘다.
    # 기존 부모에 붙이는 mode=subtask 는 앞의 named-parent 경로가 이미 처리한다.
    if said_shape == "task_with_subtasks" and mode != "subtask" and items:
        structure = "task_with_subtasks"
        out["structure"] = structure
        if _re.search(r"[2-9][0-9]{0,3}\s*(?:개|건)", last_user_text(state)) and any(
                w in last_user_text(state) for w in
                ("사람 나눠", "담당 나눠", "나눠 맡", "나눠서 진행")):
            why = "같은 반복 대상을 module roster에 분량으로 나누라는 요청이다"
            out["structure_why"] = why
    elif (said_shape == "single_task" and mode == "task" and items
          and out.get("_construction") != "request_refinement"):
        # `Task 만들어줘`처럼 issue type을 단수로 지정했으면 모델이 임의로 붙인
        # 설계/구현/검증 children을 접는다. 사용자 지정 형태는 권고가 아니라 결정이다.
        best = _best_item_for_request(state, items)
        best.pop("children", None)
        items[:] = [best]
        structure = out["structure"] = "single_task"
        why = out["structure_why"] = "사용자가 단일 티켓 타입으로 생성을 요청했다"
    authoritative_plan = any(
        isinstance(row, dict) and str(row.get("summary") or "").strip()
        for row in (state.get("structure_plan") or [])
    )
    if said_shape or (authoritative_plan
                      and (state.get("structure_ok") or structure_accepted(state))):
        # 실제 형태를 말했거나 화면에 제시된 nonempty 뼈대를 승인한 경우만 사용자
        # 지정이다. 빈 plan에서 `그냥 진행해줘`의 일반 동사를 승인으로 읽지 않는다.
        src = "user_specified"
    # 사용자는 Epic을 요청했지만 최종 Task 구조는 Epic reporting-unit 기준에 따라
    # Work Architect가 보수적으로 선택했다. 이 구조까지 user_specified로 표시하면
    # 승인 카드가 실제 사용자 선택과 반대로 설명한다.
    if out.get("_epic_downgrade"):
        src = "inferred"

    # 생성 payload는 Story Point를 지원하지 않는다. 모델이 rationale에 "생성 후 할당"
    # 같은 약속을 남겨도 실제 승인 payload와 모순되므로 제거하고 정확한 안내를 남긴다.
    sp = _re.search(r"(?:스토리\s*포인트|Story\s*Points?|\bSP)\s*(?:를|은|:|=)?\s*(\d+)",
                    _current_request_boundary_text(state), _re.I)
    if sp and items:
        for it in items:
            for field in ("story_points", "storyPoints", "story_point", "sp"):
                it.pop(field, None)
        rationale = str(out.get("rationale") or "")
        rationale = _re.sub(
            r"[^.\n;]*(?:스토리\s*포인트|Story\s*Points?|\bSP\b)[^.\n;]*(?:[.;]|$)",
            "", rationale, flags=_re.I).strip(" ;\n")
        out["rationale"] = (rationale + f"\n(Story Point {sp.group(1)}는 생성 payload 미지원 — "
                            "생성 후 티켓 화면에서 직접 설정)").strip()
    # 명시한 ISO 마감일과 상대 날짜 산술은 모델의 언어 능력이 아니라 런타임 계산으로
    # 확정한다. 형식상 유효한 다른 날짜를 모델이 내도 사용자가 쓴 유일한 exact date가
    # 우선한다. 여러 루트 티켓이나 여러 날짜의 대응 관계까지 추측하지는 않는다.
    # A deterministic multi-outcome recovery may have established a literal root
    # bijection but not yet attached its opaque refs. Bind that proven mapping before
    # per-outcome scalar fields are compiled; an unproven mapping remains untouched.
    if (_is_create_action(state) and out.get("_construction")
            and requested_outcome_contract(state)):
        _bind_deterministic_multi_outcomes(state, out)
    per_outcome_due = _expected_due_dates_by_root(state, items)
    due_status, due_literal = _explicit_due_instruction_status(state)
    if per_outcome_due:
        for index, item in enumerate(items):
            item["duedate"] = per_outcome_due[index]
    elif due_status in {"invalid", "ambiguous"} and items:
        for item in items:
            item["duedate"] = ""
        if not any(str(question.get("field") or "") == "duedate" for question in qs):
            detail = (f"'{due_literal}'은 유효한 날짜가 아닙니다. "
                      if due_status == "invalid" and due_literal else
                      "서로 다른 마감일이 함께 지정되어 있습니다. ")
            due_question = {
                "question": detail + "적용할 마감일 하나를 YYYY-MM-DD로 알려 주세요.",
                "kind": "date", "options": [], "field": "duedate",
                "required_input": True,
                "why_required": "사용자 지정 마감일을 유효한 단일 날짜로 확정할 수 없음",
            }
            qs = [question for question in qs
                  if _question_requires_input(question)][:2] + [due_question]
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(확인 필요: 마감일이 유효한 단일 날짜로 확정되지 않아 "
                              "초안의 임의 날짜를 제거했다)").strip()
    elif due_status == "clear" and items:
        for item in items:
            item["duedate"] = ""
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(사용자 요청에 따라 마감일을 비워 뒀다)").strip()
    else:
        applied_due = _apply_relative_due_to_single_draft(state, items)
        authoritative_due = _authoritative_explicit_due(state)
        if applied_due and authoritative_due:
            out["rationale"] = _normalize_due_rationale(
                str(out.get("rationale") or ""), authoritative_due)
    outcome_contract = (requested_outcome_contract(state)
                        if _is_create_action(state) else {})
    # Apply after every deterministic recovery/grouping path has settled ``items``.
    # Binding the raw model output at method entry misses a meeting/bug/delegation
    # artifact that code safely reconstructs later in this method.
    single_binding_payload = {"items": items}
    if outcome_contract and bind_single_outcome_contract(state, single_binding_payload):
        out["outcome_contract_id"] = single_binding_payload["outcome_contract_id"]
    # Deterministic literal recovery is itself derived from the authoritative user
    # request, so attach its typed binding here. Normal model output is never silently
    # repaired: a missing/wrong binding remains visible to the Auditor's fail-closed
    # machine check.
    elif outcome_contract and out.get("_construction"):
        if not _bind_deterministic_multi_outcomes(state, out):
            # Never make coverage pass by copying every outcome to every root. Without
            # a proven one-to-one literal mapping, leave the contract visibly invalid so
            # Auditor blocks it and the semantic path/user can resolve the ambiguity.
            out.pop("outcome_contract_id", None)
            for item in items:
                if isinstance(item, dict):
                    item.pop("outcome_refs", None)
    # Runtime recovery can attach outcome refs after the earlier hierarchy/owner pass.
    # Seal scoped user decisions once more at the final draft boundary.
    _apply_scoped_continuation_decisions(
        state, items, out.get("mode") or mode, qs)
    draft = {"mode": out.get("mode") or "task", "items": items,
             "structure": structure, "structure_why": why,
             "structure_source": src,
             "rationale": out.get("rationale") or ""}
    if outcome_contract or out.get("outcome_contract_id"):
        draft["outcome_contract_id"] = str(out.get("outcome_contract_id") or "")
    if out.get("_construction"):
        draft["construction"] = str(out["_construction"])
    # ★ 형태가 **우리 판단**이고 기본값(단일 Task)에서 올라간 것이면 한 번 확인한다.
    #   티켓 하나로 끝날 일을 다섯 개로 쪼개 놓고 승인만 받는 것은 사용자가 원한 게
    #   아닐 수 있다. 사용자가 '알아서'라고 했으면 묻지 않는다(위임이 이긴다).
    if (src == "inferred" and structure in ("task_with_subtasks", "multiple_tasks",
                                            "new_epic")
            and items and not qs and not _said_defaults(state)):
        out["rationale"] = (
            (out.get("rationale") or "")
            + "\n(구조는 실행 단위와 현재 초안에 맞춘 안전 기본값이다 — 승인 화면에서 "
              "Task/하위 작업 구성을 조정할 수 있다)"
        ).strip()
    if draft_new_labels:
        draft["new_labels"] = draft_new_labels
    # (구조: …) 줄은 **맨 끝에서 한 번만** 붙인다 — 여기서도 붙이던 것을 뺐다.
    # 뒤의 가드가 구조를 바꾸면 이유도 바뀌는데, 여기서 이미 붙여 둔 옛 이유가
    # 남아 카드에 서로 다른 두 줄이 떴다(실측: 재작성 왕복이 있던 턴).

    # ── 조사 근거를 '참고' 섹션에 **병합**한다 — 조사 결과를 티켓에 박제한다.
    # 대화가 끝나면 ResearchAnalyst 의 조사는 증발하지만, 티켓 description 에 남기면 동적 RAG 가
    # 다음 조사에서 그걸 다시 수확한다(지식이 복리로 쌓인다). 습관을 프롬프트에 맡기지 않고
    # 코드가 보장한다.
    # ★ 별도 <h3>References</h3> 를 덧붙이던 방식은 폐기 — 모델이 쓴 <h3>참고</h3> 와
    #   무조건 중복됐다(실측: 한 본문에 참고/Knowledge/References 3벌·한영 혼재).
    #   섹션은 '참고' 하나이고, 없던 키·링크만 그 ul 에 이어 붙인다(_merge_refs).
    refs = []
    for e in (state.get("evidence") or [])[:5]:
        from app.agent.workflow.relevance import evidence_is_relevant, matches_focus
        if not evidence_is_relevant(e) or not matches_focus(
                f"{e.get('title', '')} {e.get('why', '')}", state.get("keywords") or []):
            continue
        k, why = (e.get("key") or "").strip(), (e.get("why") or e.get("title") or "").strip()
        # 티켓 키 모양만 — PMO 근거에는 "ETL" 같은 모듈명이 섞이는데 그건 참고가 아니다.
        if k and _re.match(r"^[A-Z][A-Z0-9]*-[0-9]+$", k):
            refs.append((k, f"<li>{k} — {why}</li>" if why else f"<li>{k}</li>"))
    for d in (state.get("related_docs") or [])[:3]:
        t, u = (d.get("title") or "").strip(), (d.get("url") or "").strip()
        # A client-side navigation fragment such as ``#/home`` is not a durable
        # source that Jira readers can open. Keep it in the LTM answer UI, but do
        # not persist it as ticket evidence.
        if t and u.startswith(("http://", "https://")):
            refs.append((u, f'<li><a href="{u}">{t}</a></li>'))
    for it in items:
        it["description"] = _merge_refs(it.get("description") or "", refs)
        if mode == "subtask":
            # parent는 payload의 `parent` 필드로 이미 배지/링크가 된다. 같은 parent와
            # 기존 형제·문서를 각 Sub-Task 참고에 반복하면 본문 대부분이 중복 참고가
            # 된다(PAR1/SUB2 실측). 기존 parent가 맥락의 source-of-truth이므로 자식
            # 본문에서는 참고 섹션 자체를 빼고, 답변의 출처 링크는 별도로 유지한다.
            it["description"] = _drop_subtask_ticket_refs(it["description"])

    # 모델이 본 참고는 Research Analyst의 검증을 우회할 수 없다. evidence/related_docs에
    # 없는 ticket·URL과 프롬프트 내부 문서 링크는 승인 카드에서 제거한다.
    allowed_ref_keys = {str(e.get("key") or "").upper() for e in
                        (state.get("evidence") or []) if isinstance(e, dict)}
    allowed_ref_keys |= {str(k).upper() for k in (state.get("mentioned_keys") or [])}
    allowed_ref_urls = {str(d.get("url") or "").strip() for d in
                        (state.get("related_docs") or []) if isinstance(d, dict)
                        and str(d.get("url") or "").strip().startswith(("http://", "https://"))}
    unverified = []
    for it in items:
        it["description"], gone = _drop_unverified_refs(
            it.get("description") or "", allowed_ref_keys, allowed_ref_urls)
        unverified += gone
    if unverified:
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(Research Analyst가 검증하지 않은 참고를 뺐다: "
                            + ", ".join(unverified[:4]) + ")").strip()

    # ── 참고 불릿 가드 — 링크도 키도 없는 불릿은 **날조 문서 제목**이다(실측:
    # "아키텍처 결정 기록/스프린트 회의록/설계 노트" 가 링크 없이 나열됐다 — mock
    # 코멘트 속 문구를 문서인 양 옮긴 것). 검증 불가능한 나열은 빼는 것이 맞다.
    dropped = []
    for it in items:
        it["description"], gone = _drop_unlinked_refs(it.get("description") or "")
        dropped += gone
    if dropped:
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(참고에서 출처 없는 항목을 뺐다: "
                            + ", ".join(dropped[:4]) + ")").strip()

    # ── 빈 섹션은 없느니만 못하다 — 헤딩만 남은 '참고'가 티켓에 박제됐다(실측 S4).
    # 참고가 비는 것은 정상이다(관련 이력이 없을 수 있다). 그러면 섹션을 지운다.
    for it in items:
        it["description"] = _drop_empty_sections(it.get("description") or "")

    # ── 작업 범위에 '제외'가 없으면 알린다 ─────────────────────────────
    # knowledge/07: "하지 않는 것을 적는 게 절반이다." 범위가 닫히지 않은 티켓은 리뷰
    # 때마다 "이것도 포함인가요?"가 반복된다. 실측(DRAFT-COMPARISON 갭 ③): mini 는
    # 제외를 자주 생략하는데 지금까지 체커만 있고 가드가 없었다.
    # **채워 넣지는 않는다** — 무엇을 빼는지는 사용자만 아는 것이라, 지어낸 제외는
    # 그냥 날조다. 코드가 할 수 있는 것은 빠졌다는 사실을 눈에 보이게 하는 것뿐이다.
    no_excl = [str(it.get("summary") or "") for it in items
               if "작업 범위" in str(it.get("description") or "")
               and not _re.search(r"제외|하지\s*않", str(it.get("description") or ""))]
    if no_excl:
        out["rationale"] = ((out.get("rationale") or "")
                            + f"\n(확인 필요: \"{no_excl[0][:40]}\" 의 작업 범위에 "
                              "**이번에 하지 않는 것**이 없다 — 제외를 적어야 범위가 "
                              "닫힌다)").strip()

    # ── 주제 가드 — 제목·본문이 **원 요청의 고유어**를 유지하는지 확인한다.
    # Semantic projection and later grouping may produce a structurally valid title while
    # dropping one product token or turning ``1차`` into bare ``차``. High-precision user
    # anchors are facts, not editorial wording, so restore them before judging drift.
    if _preserve_required_user_anchors(state, items):
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(원 요청의 고유 식별자·차수 표기를 제목에 복원했다)").strip()
    # 실측: Epic 본문("증분 적재")이 원 요청("starrocks puffin ndv")을 잠식해 전혀
    # 다른 티켓이 만들어졌다. 판정은 코드가, 고치는 판단은 사람이 한다(경고 노출).
    drift = _topic_drift(state, items)
    if drift:
        out["rationale"] = ((out.get("rationale") or "") + "\n" + drift).strip()
        draft["topic_drift"] = True     # Auditor 의 단건 우회(L3b)를 막는 신호

    # ── Epic Link 는 **실재하고 관련 있는 write-project Epic** 이어야 한다 ─────
    # 실측: 사용자가 "기존 에픽 중 맞는 걸로 붙여줘"라고 했는데 모델이 Task(DL-9072)를
    # 에픽이라 답하고 초안에는 아예 안 실었다. 타입 확인은 판단이 아니라 조회다.
    typed_parent_authority = parent_selection_authority(state)
    typed_parent_slots = _apply_typed_parent_resolution(state, items)
    if typed_parent_slots:
        draft["resolved_slots"] = typed_parent_slots
        if any(row.get("status") != "resolved" for row in typed_parent_slots):
            items.clear()
            qs.append(_required_parent_resolution_question())
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(요청한 기존 parent에 검증된 호환 후보가 없어 "
                                  "실행 초안을 차단했다)").strip()
        else:
            selected_keys = [str(row.get("value") or "")
                             for row in typed_parent_slots if row.get("value")]
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(typed parent 결정을 검증된 후보에 결속: "
                                + ", ".join(selected_keys) + ")").strip()
    explicit_epic = _explicit_parent_epic(state)
    explicit_epics = _expected_parent_epics_by_root(state, items)
    delegated_epic_choice = bool(typed_parent_authority) \
        or _delegates_existing_epic_choice(state)
    verified_parent_keys = ({
        str(row.get("key") or "").strip().upper()
        for row in verified_parent_epic_candidates(state)
    } if delegated_epic_choice else set())
    if explicit_epic and mode != "subtask":
        for it in items:
            if not str(it.get("type") or "").lower().startswith("sub"):
                it["epic"] = explicit_epic
    elif explicit_epics and mode != "subtask":
        for index, it in enumerate(items):
            if not str(it.get("type") or "").lower().startswith("sub"):
                it["epic"] = explicit_epics[index]
    for index, it in enumerate(items):
        ek = str(it.get("epic") or "").strip()
        if not ek:
            replacement = _delegated_parent_epic(state, it) \
                if delegated_epic_choice else None
            if replacement:
                it["epic"] = str(replacement["key"])
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n(선택을 위임받아 검증된 기존 Epic "
                                      f"{replacement['key']} 아래에 배치했다)").strip()
            elif delegated_epic_choice and mode != "epic":
                # Delegation chooses among relevant verified candidates; it is not
                # permission to attach the first Epic from the same component.  A
                # top-level Task is reversible and does not corrupt another Epic's
                # progress when no subject-relevant candidate is supported.
                it.pop("epic", None)
                out["rationale"] = ((out.get("rationale") or "")
                                    + "\n(관련성이 검증된 기존 Epic 후보가 없어 "
                                      "최상위 Task로 보수적으로 배치했다)").strip()
            continue
        if delegated_epic_choice and ek.upper() not in verified_parent_keys:
            replacement = _delegated_parent_epic(state, it, rejected_key=ek)
            if replacement:
                it["epic"] = str(replacement["key"])
                message = (
                    f"초안 parent {ek}은 현재 조회에서 상세 확인된 기존 Epic 후보가 "
                    f"아니어서 {replacement['key']}으로 대체했다"
                )
            else:
                it.pop("epic", None)
                message = (
                    f"초안 parent {ek}은 현재 조회에서 상세 확인된 기존 Epic 후보가 "
                    "아니어서 연결을 비우고 최상위 Task로 뒀다"
                )
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n({message})").strip()
            continue
        # The successful opened detail already proves a delegated candidate's type.
        # Other parent paths retain the live Jira type guard.
        if not delegated_epic_choice and not _is_epic(ek):
            replacement = _delegated_parent_epic(state, it, rejected_key=ek) \
                if delegated_epic_choice else None
            it["epic"] = str(replacement["key"]) if replacement else ""
            note_text = (f"검증된 기존 Epic {replacement['key']}으로 대체했다"
                         if replacement else "Epic 후보를 다시 확인해야 한다")
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n({ek} 는 Epic 이 아니다 — {note_text})").strip()
            continue
        # 사용자가 직접 지목한 Epic은 의식적인 선택이므로 그대로 둔다. 모델/검색이 추론한
        # Epic만 write project·모듈·주제 적합성을 검증한다. 부적합하면 최상위 Task가
        # 엉뚱한 진척률을 오염시키는 것보다 연결을 비우는 편이 안전하다.
        if ek == explicit_epic or ek == explicit_epics.get(index):
            continue
        if (out.get("_construction") == "request_refinement"
                and delegated_epic_choice and ek.upper() in verified_parent_keys):
            # The typed continuation selected from QueryRunner's already-materialized
            # candidate set. Do not re-open or semantically reinterpret it during a
            # zero-call field overlay.
            continue
        if (str(it.get("parent_source") or "") == "resolved_slot"
                and typed_parent_authority and ek.upper() in verified_parent_keys):
            # The shared ResolvedSlot already binds typed authority, materialized type
            # proof and semantic compatibility. Do not reopen/reinterpret it here.
            continue
        reason = _inferred_epic_rejection(state, it, ek)
        if reason:
            replacement = _delegated_parent_epic(state, it, rejected_key=ek) \
                if delegated_epic_choice else None
            it["epic"] = str(replacement["key"]) if replacement else ""
            if replacement:
                message = (f"{ek} 배치를 거부했다 — {reason}; 검증된 기존 Epic "
                           f"{replacement['key']}으로 대체했다")
            else:
                # Preserve the established rationale wording consumed by the UI/tests.
                message = f"{ek} 연결을 뺐다 — {reason}"
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n({message})").strip()

    # 컴포넌트는 하나만 — 둘이면 워크로드가 이중 계상된다(knowledge/03).
    for it in items:
        comps = [str(c) for c in (it.get("components") or []) if str(c).strip()]
        if len(comps) > 1:
            it["components"] = comps[:1]
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n({comps[0]} 만 남겼다 — 컴포넌트가 둘이면 워크로드가 "
                                  f"이중 계상된다. {', '.join(comps[1:])} 몫은 별도 티켓으로 "
                                  "나누는 것이 맞다)").strip()

    # ── 제목의 모듈 접두는 관행이다(knowledge/01 §제목) — 코드가 붙인다 ─────
    # 대개는 모델이 붙이지만 재료가 길면(회의록 붙여넣기 등) 빠뜨린다(실측 Round P).
    # 검색이 접두로 걸리기 때문에 빠지면 나중에 안 찾힌다.
    for it in items:
        comp = next((str(c).strip() for c in (it.get("components") or []) if str(c).strip()),
                    "")
        s = str(it.get("summary") or "").strip()
        if comp and s and not s.startswith("["):
            it["summary"] = f"[{comp}] {s}"

    # summary의 명시적 module alias는 구조가 사용자 지정인지와 무관하게 적용한다.
    # 예전에는 아래의 "모듈이 갈리는 자식 승격" 블록 안에만 있어, `사람 나눠서`처럼
    # shape를 사용자가 말한 요청은 그 블록을 건너뛰며 `[DataOps] 메타데이터 …` 같은
    # 모순이 그대로 남았다(STR1 실측). alias 표는 구조 추론이 아니라 사람이 관리하는
    # module source-of-truth이므로 모든 Task 초안에 같은 시점에 적용해야 한다.
    if mode == "task" and _align_modules_from_summary(items):
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(summary의 module alias에 맞춰 component를 바로잡았다)"
                            ).strip()

    # `N개를 사람 나눠서`는 모델이 이미 children을 냈더라도 다시 계산한다. 실제 대상
    # 목록 없이 만든 `테이블 1~10` 같은 식별자는 사실이 아니며 실행마다 개수도 흔들린다.
    # 확정 사실인 총량과 실제 module roster만으로 분량 묶음을 만든다.
    if mode == "task" and items and shape_hint(state)[0] == "task_with_subtasks":
        volume_children = _volume_partition_children(state, items[0])
        if volume_children:
            items[0]["children"] = volume_children
            structure = out["structure"] = draft["structure"] = "task_with_subtasks"
            total_m = _re.search(r"[2-9][0-9]{0,3}\s*(?:개|건)", last_user_text(state))
            out["rationale"] = _re.sub(
                r"각\s*Sub-Task[^.\n]*(?:\.|$)", "", str(out.get("rationale") or ""),
                flags=_re.I).strip()
            if total_m:
                size_m = _re.search(r"\((\d+(?:개|건))\)", volume_children[0]["summary"])
                detail = (f"{total_m.group(0)}를 실제 roster {len(volume_children)}명의 "
                          f"담당 묶음으로 나눴다"
                          + (f" — 묶음당 {size_m.group(1)}" if size_m else ""))
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n({detail})").strip()

    # ── 번호·단계만 다른 Task N개는 한 산출물이다 — 하나로 접고 children 으로 ──
    # work_architect.md 오판 #1(단계를 Task 로)·#2("테이블 30개 → 30 Tasks")를 코드가
    # 보장한다(실측 재발 2종: "테이블 1~5" Task 5개 / "…설계·…구현·…검증" Task 3개).
    # 제목에서 꼬리 번호·단계 낱말을 떼면 같은 제목 = 같은 산출물.
    # 제목이 **순수 단계어**("구현 단계", "검증 단계")인 항목은 독립 Task 가 아니라
    # 첫 실질 항목의 Sub-Task 다(실측: 재구축+구현 단계+검증 단계 3 Task).
    if mode == "task" and len(items) >= 2:
        stage_only = [i for i in items[1:] if _re.match(
            r"^(설계|구현|검증|테스트|배포|모니터링|문서화|분석|리뷰)\s*(단계)?$",
            str(i.get("summary") or "").strip())]
        if stage_only:
            head0 = items[0]
            kids0 = [c for c in (head0.get("children") or []) if isinstance(c, dict)]
            for s_it in stage_only:
                kids0.append({"summary": f"{s_it.get('summary')} — "
                                         f"{str(head0.get('summary'))[:30]}",
                              **({"assignee": s_it["assignee"]}
                                 if s_it.get("assignee") else {})})
                items.remove(s_it)
            head0["children"] = kids0
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(단계 항목은 독립 Task 가 아니라 Sub-Task 로 접었다)").strip()

    need = 2 if structure == "task_with_subtasks" else 3
    # A typed RequestPlan already states whether these are independent outcomes. Never
    # collapse those roots by title similarity: overlapping producer/consumer/reviewer
    # descriptions are mutable prose and cannot replace stable outcome identity.
    has_typed_outcomes = typed_outcome_count > 0
    if mode == "task" and len(items) >= need and not has_typed_outcomes:
        bases = [_base_title(str(i.get("summary") or "")) for i in items]
        # 전원일치를 요구하면 **30개 중 하나만 어긋나도 접기가 통째로 무산된다**
        # (실측 STR1: 같은 요청이 8건·30건·1+30 으로 매번 다르게 나온다). 그렇다고
        # 느슨하게 묶으면 서로 다른 산출물이 한 Task 밑으로 빨려 들어간다 — 그래서
        # **최빈 몸통이 2건 이내를 남기고 전부 덮을 때만** 접고, 남은 것은 독립 Task 로
        # 그대로 둔다. 오차 허용이지 그룹핑이 아니다.
        cand = [b for b in bases if b and len(b) >= 8]
        base = max(set(cand), key=cand.count) if cand else ""
        n = bases.count(base) if base else 0
        if base and n >= 3 and n >= len(items) - 2:
            group = [i for i, b in zip(items, bases) if b == base]
            rest = [i for i, b in zip(items, bases) if b != base]
            head = dict(group[0])
            # ``base`` intentionally removes digits for grouping equality. It must never
            # become a visible title: an ordinal such as ``1차`` is user-authored scope.
            head["summary"] = _display_base_title(
                str(group[0].get("summary") or "")) or base
            head["children"] = [{"summary": str(i.get("summary") or ""),
                                 **({"assignee": i["assignee"]} if i.get("assignee") else {}),
                                 **({"duedate": i["duedate"]} if i.get("duedate") else {})}
                                for i in group]
            # draft 가 이 리스트를 **참조로** 공유한다 — 이름을 다시 묶으면 반영되지 않는다.
            items[:] = [head] + rest
            structure = "multiple_tasks" if rest else "task_with_subtasks"
            # 구조를 코드가 바꿨으면 **그 이유도 바꾼다** — 모델이 쓴 옛 이유("간단해
            # 보인다")가 새 구조 옆에 그대로 붙어 승인 카드에서 앞뒤가 안 맞았다(실측).
            why = "번호만 다른 Task 들은 같은 산출물의 분량 분할이라 한 Task 로 접었다"
            out["structure"], out["structure_why"] = structure, why
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(번호만 다른 Task {n}건은 같은 산출물의 분량 분할이라 "
                                  "한 Task + Sub-Task 로 접었다"
                                + (f" — 몸통이 다른 {len(rest)}건은 그대로 뒀다)" if rest
                                   else ")")).strip()
            draft["structure"], draft["structure_why"] = structure, why

    # ── 분량 분할 Sub-Task 는 골고루 ───────────────────────────────
    if spread_volume_split(items):
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(같은 분량 작업이라 담당을 골고루 나눴다)").strip()

    # ── 제목 하나에 산출물 둘이 들어가면 알린다 ─────────────────────
    # "A 및 B" 는 대개 티켓 둘이다(모듈·담당·완료 시점이 갈린다). 쪼개는 판단은 사람이
    # 하되, 조용히 넘어가지는 않는다 — 실측: 모듈 3개 일을 "성능 측정 및 인덱스 조정"
    # 한 건에 뭉갰다.
    for it in items:
        title = str(it.get("summary") or "")
        if _re.search(r"\s(및|그리고)\s", title) and not it.get("children"):
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(확인 필요: \"{title[:40]}\" 는 한 제목에 두 가지 일이 "
                                  "들어가 있다 — 모듈·담당이 다르면 티켓을 나누는 게 맞다)").strip()
            break

    # ── 같은 이름의 Epic 이 이미 있으면 격상을 보류한다 ─────────────
    # Epic 은 진척 보고 단위라 중복이 생기면 둘 다 영원히 60% 에서 멈춘다. 사용자가
    # "에픽으로 크게 잡아줘" 라고 해도, 담을 Epic 이 이미 있으면 그걸 쓰는 게 맞다
    # (knowledge/04 의 격상 조건 ③ '담을 기존 Epic 이 없다'를 코드가 확인한다).
    if items and str(items[0].get("type") or items[0].get("issue_type") or "").lower() == "epic" \
            and str(items[0].get("epic") or "").strip():
        # An Epic cannot itself be placed under another Epic.  Under delegated defaults,
        # the inferred parent is useful evidence of the safer intent: create a Task in that
        # existing reporting unit.  Without delegation, retain the explicitly requested new
        # Epic but drop the invalid parent link.
        item = items[0]
        parent_epic = str(item.get("epic") or "").strip()
        if _said_defaults(state):
            _force_item_type(item, "Task", "task")
            item.pop("epic_name", None)
            mode = out["mode"] = draft["mode"] = "task"
            structure = out["structure"] = draft["structure"] = "single_task"
            why = out["structure_why"] = draft["structure_why"] = \
                f"기존 Epic {parent_epic} 아래의 단일 Task로 중복 보고 단위를 피했다"
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(Epic 아래 Epic은 허용되지 않아 {parent_epic} 아래 "
                                  "Task로 정리했다)").strip()
        else:
            item.pop("epic", None)
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n({parent_epic} Epic 연결을 제거했다 — Epic은 다른 "
                                  "Epic 아래에 둘 수 없다)").strip()

    if (out.get("mode") or "") == "epic" and items:
        twin = _existing_epic_like(items[0].get("summary") or "")
        if twin:
            if _said_defaults(state):
                # `알아서`는 새 보고 단위를 만들라는 승인이 아니라 안전한 기본값을
                # 선택하라는 위임이다. 검증된 동일 Epic이 하나면 중복 생성 여부를 다시
                # 물을 필수정보가 없다. 기존 Epic 아래 Task가 되돌리기 쉬운 기본값이다.
                item = items[0]
                _force_item_type(item, "Task", "task")
                item["epic"] = twin["key"]
                item.pop("epic_name", None)
                mode = out["mode"] = draft["mode"] = "task"
                structure = out["structure"] = draft["structure"] = "single_task"
                why = out["structure_why"] = draft["structure_why"] = \
                    f"기존 Epic {twin['key']} 아래의 단일 Task로 중복 Epic을 피했다"
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n(Epic 격상 보류 — {twin['key']} 와 이름이 겹쳐 "
                                      "기존 Epic 아래 Task로 정리했다)").strip()
            else:
                qs = (qs or []) + [{
                    "question": f"{twin['key']} \"{twin.get('summary', '')}\" 가 이미 있습니다. "
                                "여기에 Task 로 붙일까요, 그래도 새 Epic 을 만들까요?",
                    "kind": "choice", "field": "epic",
                    "required_input": True,
                    "why_required": "명시한 새 Epic 생성과 기존 동일 보고 단위 재사용이 충돌함",
                    "options": [f"{twin['key']} 아래 Task 로 (권장 — 중복 Epic 은 진척 집계를 흐린다)",
                                "새 Epic 을 만든다"]}]
                # draft 는 이 위에서 이미 조립됐고 items 를 **참조로** 공유한다 —
                # 이름을 다시 묶으면(items = []) 초안에는 반영되지 않는다. 비운다.
                items.clear()
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n(Epic 격상 보류 — {twin['key']} 와 이름이 겹친다)").strip()
                structure = "single_task"

    # A literal "make it an Epic" does not waive the four reporting-unit criteria.
    # If duration/scale evidence is insufficient, choose the reversible Task placement
    # even when no near-duplicate Epic title was found.
    if (out.get("mode") or "") == "epic" and items \
            and _current_request_boundary_text(state):
        unmet = _new_epic_unmet_criteria(state)
        if unmet:
            item = items[0]
            component = next((str(value).strip() for value in
                              (item.get("components") or []) if str(value).strip()), "")
            pick = _pick_parent_epic(str(item.get("summary") or ""), component)
            _force_item_type(item, "Task", "task")
            item.pop("epic_name", None)
            if pick:
                item["epic"] = pick["key"]
                placement = f"기존 Epic {pick['key']} 아래 Task"
            else:
                item.pop("epic", None)
                placement = "최상위 Task"
            mode = out["mode"] = draft["mode"] = "task"
            structure = out["structure"] = draft["structure"] = "single_task"
            why = out["structure_why"] = draft["structure_why"] = \
                f"Epic 조건 미충족({', '.join(unmet)})으로 {placement}로 보수화"
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(Epic 격상 보류 — {', '.join(unmet)}; "
                                  f"{placement}로 정리했다)").strip()
            draft["rationale"] = out["rationale"]

    # ── "Epic 은 네가 골라줘" 는 **고르라는 말이지 만들라는 말이 아니다** ──────
    # 실측 STARR1: "Epic 은 네가 골라줘. … 알아서 진행해" 에 모델이 **새 Epic** 을
    # 만들었다(본문도 빈 채로). 위임은 선택을 맡긴 것이지 격상 권한을 준 것이 아닌데,
    # 모델은 "알아서"를 격상 승인으로 읽는다. 새 Epic 은 진척 보고 단위가 하나 더 생기는
    # 일이라 되돌리기가 가장 비싸다 — knowledge/04 의 격상 조건도 보수적으로 적혀 있다.
    # 관련 후보가 없더라도 '고르라'는 위임은 새 Epic 생성 권한이 아니다. 그 경우
    # 되돌리기 쉬운 최상위 Task로 보수화한다.
    if ((out.get("mode") or "") == "epic" and items and not qs
            and delegated_epic_choice):
        # QueryRunner already opened a bounded candidate set. Do not consult a second
        # global Epic list here: an unopened search hit is not sufficient evidence for
        # a write-time hierarchy decision.
        pick = _delegated_parent_epic(state, items[0])
        _force_item_type(items[0], "Task", "task")
        if pick:
            items[0]["epic"] = pick["key"]
            placement = (f"{pick['key']} \"{str(pick.get('summary') or '')[:40]}\" "
                         "아래 Task")
        else:
            items[0].pop("epic", None)
            placement = "관련 Epic 없는 최상위 Task"
        items[0].pop("epic_name", None)
        # ★ `draft` 는 이 위에서 이미 조립됐다 — `out` 만 고치면 승인 카드는 여전히
        #   Epic 이다(items 는 참조로 공유돼 항목만 바뀐 채 mode 는 epic). 코드가
        #   만든 값이 소비하는 쪽에 안 닿는 §5-f 의 그 부류라, 두 벌 다 쓴다.
        mode = out["mode"] = draft["mode"] = "task"
        structure = out["structure"] = draft["structure"] = "single_task"
        out["rationale"] = ((out.get("rationale") or "")
                            + f"\n(Epic 을 **고르라**고 해서 {placement}로 뒀다 — "
                              "새 Epic 은 진척 보고 단위가 하나 더 생기는 일이라 "
                              "명시적 생성 요청 없이는 만들지 않는다)").strip()

    # ── 컴포넌트가 비면 제목의 [모듈] 접두에서 채운다 ────────────────
    # 우리 제목 규약이 "[모듈] 무엇을 한다"다. 모델이 제목엔 넣고 필드엔 빠뜨리는 일이
    # 잦은데, 컴포넌트가 없으면 워크로드 집계에서 통째로 빠지고 담당도 못 고른다.
    known_comps = _known_components()
    for it in items:
        if it.get("components"):
            continue
        m = _re.match(r"^\s*\[([^\]]+)\]", str(it.get("summary") or ""))
        name = (m.group(1).strip() if m else "")
        if name and name in known_comps:
            it["components"] = [name]

    # ── 자식 담당을 비워 두지 않는다 ─────────────────────────────────
    # "사람 나눠서" 라고 한 일에 담당이 하나도 없으면 나눈 의미가 없다. PeopleAdvisor 는
    # 상위 items 만 보므로(자식은 그 뒤에 생긴다) 여기서 코드가 채운다 — 사용자가
    # 지정한 자식 담당은 건드리지 않고, **빈 것만** 모듈 로스터로 돌린다.
    for it in items:
        kids = [c for c in (it.get("children") or []) if isinstance(c, dict)]
        empty = [c for c in kids if not str(c.get("assignee") or "").strip()]
        if not kids or not empty:
            continue
        taken = [str(c.get("assignee")).strip() for c in kids
                 if str(c.get("assignee") or "").strip()]
        # 폴백 사다리: 자식 담당 → 부모 담당 — 컴포넌트가 로스터 키와 안 맞아도
        # 부모 담당의 소속 모듈로 풀을 찾는다(실측: 채움이 통째로 무산됐다).
        fb = taken[0] if taken else str(it.get("assignee") or "").strip()
        pool = [u for u in _module_pool(it, fb) if u]
        if not pool or pool == [fb]:
            continue
        order = [u for u in pool if u not in taken] or pool
        for n, c in enumerate(empty):
            c["assignee"] = order[n % len(order)]
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(나눠 맡도록 자식 담당을 모듈 인력에 배분했다 — "
                              "승인 화면에서 바꿀 수 있다)").strip()

    # ── 구조 판단과 산출이 어긋나면 고친다/알린다 ─────────────────────
    # "Sub-Task 로 나눈다"고 해 놓고 children 이 없거나 1개뿐이면 판단이 아니라 말뿐이다
    # (실측: "30개 나눠서"에 자식 1개). subtask 모드는 제외 — Sub-Task 는 자식이 없다.
    if structure == "task_with_subtasks" and items and mode != "subtask" \
            and sum(len(i.get("children") or []) for i in items) < 2:
        fix = (_split_into_children(state, items[0])
               if _said_defaults(state) or said_shape == "task_with_subtasks" else [])
        if len(fix) >= 2:
            kept = [c for c in (items[0].get("children") or []) if isinstance(c, dict)]
            have = {str(c.get("summary") or "") for c in kept}
            covered_stages = {_execution_stage(c.get("summary")) for c in kept}
            covered_stages.discard("")
            items[0]["children"] = kept + [c for c in fix
                                           if str(c.get("summary") or "") not in have
                                           and (not _execution_stage(c.get("summary"))
                                                or _execution_stage(c.get("summary"))
                                                not in covered_stages)]
            _fill_owners(items[0], items[0]["children"])   # 자식 담당 채움 가드는 이미 지나갔다
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(구조 판단대로 단계별 Sub-Task 를 채웠다 — "
                                  "승인 화면에서 고칠 수 있다)").strip()
        else:
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(확인 필요: 나눠서 진행한다고 판단했는데 Sub-Task 가 "
                                  "없다 — 한 티켓으로 둘지 쪼갤지 정해야 한다)").strip()

    # ── 하향 편향 보정 — single_task 인데 다단계·다인 규모면 확인을 받는다.
    # 상향(쪼갬)에는 확인 질문이 붙는데 하향(뭉갬)은 아무도 안 막았다(실측: 파이프라인
    # 신규 구축을 단일 Task 로 뭉갰다). 판정 기준은 **사용자가 형태를 입으로 말했는가**
    # (said_shape)다 — 모델이 적어 낸 structure_source 는 위임("알아서")을 지정으로
    # 오독한다(실측).
    #
    # 신호는 **두 곳**에서 읽는다:
    #   ① 모델이 쓴 본문 — DoD 불릿 5개↑ 또는 서로 다른 단계 낱말 3종↑
    #   ② 사용자의 원 요청 — 신규 구축 낱말(BUILD_WORDS)
    # ②를 더한 이유: ①만 보면 **모델이 본문을 얇게 쓸수록 가드가 헐거워진다.** 뭉갠
    # 초안은 대개 본문도 얇으니 정확히 거꾸로 된 판정이다(실측 STARR1 재발 — 프롬프트
    # 넛지는 같은 낱말로 이미 경고하고 있었는데 코드가 안 받쳤다). 원 요청은 모델이
    # 못 바꾸는 입력이라 이 판정의 바닥이 된다.
    # ★ 판정은 **구조 이름이 아니라 산출물 모양**으로 한다. 처음엔 `single_task` 만 봤는데,
    #   같은 요청에서 모델이 `new_epic` 이라고 적은 실행은 가드가 통째로 비껴갔다
    #   (실측 STARR1: 실행마다 single_task / new_epic 로 갈렸다). 게다가 자식 없는
    #   Task 하나짜리 `new_epic` 은 그 자체로 앞뒤가 안 맞는다 — Epic 은 여러 일을
    #   묶으려고 만드는 것이라, 밑에 하나뿐이면 Epic 일 이유가 없다.
    if structure in ("single_task", "new_epic") and not said_shape and not qs \
            and not _simple_delegated_request(state) \
            and len(items) == 1 and not (items[0].get("children") or []):
        body = " ".join(str(i.get("description") or "") + " " + str(i.get("summary") or "")
                        for i in items)
        dod = body.count("data-checked")
        stages = sum(1 for w in ("설계", "구현", "검증", "연동", "모니터링", "전환",
                                 "PoC", "테스트", "배포") if w in body)
        # request_text가 후속 답변으로 덮이는 회귀가 있더라도 전체 사용자 발화에서
        # 최초 구축 신호를 복원한다(STARR1: 첫 턴 `파이프라인`이 둘째 턴에서 사라짐).
        building_request = _current_request_boundary_text(state)
        building = any(w in building_request for w in BUILD_WORDS)
        if dod >= 5 or stages >= 3 or building:
            # An understructured build has a deterministic conservative shape: keep
            # the requested deliverable as its parent and expose execution stages as
            # children. The approval card remains the reversible editing boundary.
            fix = _split_into_children(state, items[0])
            if fix:
                items[0]["children"] = fix
                _fill_owners(items[0], fix)
                structure = "task_with_subtasks"
                why = ("설계·구현·검증처럼 단계가 나뉘고 담당이 갈릴 규모라 "
                       "단계별 Sub-Task 로 나눴다")
                out["structure"], out["structure_why"] = structure, why
                draft["structure"], draft["structure_why"] = structure, why
                out["rationale"] = ((out.get("rationale") or "")
                                    + "\n(다단계 규모라 단계별 Sub-Task 로 나눈 안전 "
                                      "기본값이다 — 승인 화면에서 고칠 수 있다)").strip()
            else:
                out["rationale"] = ((out.get("rationale") or "")
                                    + "\n(구조 권고: 다단계 규모이지만 근거 있는 하위 실행 "
                                      "단위를 결정적으로 만들 수 없어 현재 Task를 유지했다)").strip()

    # ── 모듈이 갈리는 자식은 **형제 Task 로 올린다** ─────────────────────────
    # knowledge/03: 요청이 두 모듈에 걸치면 컴포넌트를 둘 다 넣지 말고 **티켓을 나눠서
    # 링크**한다. 이유는 집계다 — Sub-Task 는 부모 컴포넌트에 딸려 세어지므로, 모듈이
    # 다른 일을 자식으로 넣으면 **Runtime 일이 Workbench 로 계상된다.** 티켓은 멀쩡해
    # 보이고 어디서도 안 터지는데 워크로드만 조용히 틀린다(실측 STR2: "리니지 뷰어
    # 성능 측정 + 쿼리 엔진 인덱스 + 사용 가이드"를 한 Task 의 자식 둘로 뭉갰다).
    #
    # 판정은 **사람이 적은 별칭 표**(config/module-aliases.yaml)로만 한다 — 코드가
    # 뜻을 넘겨짚으면 남의 모듈에 계상하는 것이 바로 이 결함이라, 가드가 결함을 재현하는
    # 꼴이 된다. 모듈이 **하나로 딱 떨어지는** 자식만 올린다(둘 이상 걸리면 모호하니 둔다).
    # 사용자가 형태를 입으로 말했으면(said_shape) 건드리지 않는다.
    promoted = False
    if items and not said_shape and mode != "subtask":
        from app.infra.settings import modules_in_text, resolve_module
        for it in items:
            # 컴포넌트가 비어 있으면 제 본문이 부른 모듈로 채운다 — 비어 있으면 담당
            # 찾기가 전사 명단으로 넓어진다(§5-e `resolve_module` 과 같은 갈래).
            # ★ **제목만** 본다. 본문까지 넣었더니 "리니지 뷰어 성능 측정" 티켓의 배경에
            #   적힌 "쿼리 엔진 인덱스 조정"까지 잡혀 모듈이 둘로 갈렸고, 그래서 채우기가
            #   조용히 무산됐다(실측 STR2). 본문은 **옆 티켓 이야기**를 하는 자리다 —
            #   이 티켓이 무엇인가는 제목이 말한다.
            if not (it.get("components") or []):
                own = modules_in_text(str(it.get("summary") or ""))
                if len(own) == 1:
                    it["components"] = [own[0]]
        for it in list(items):
            kids = [c for c in (it.get("children") or []) if isinstance(c, dict)]
            if not kids:
                continue
            parent_mod = resolve_module((it.get("components") or [""])[0]) or \
                (modules_in_text(str(it.get("summary") or "")) or [""])[0]
            if not parent_mod:
                continue
            moved, stay = [], []
            for c in kids:
                mods = modules_in_text(str(c.get("summary") or ""))
                (moved if len(mods) == 1 and mods[0] != parent_mod else stay).append(c)
            if not moved:
                continue
            promoted = True
            it["children"] = stay
            for c in moved:
                c.pop("parent", None)
                _force_item_type(c, "Task", "task")
                c["components"] = [modules_in_text(str(c.get("summary") or ""))[0]]
                c.setdefault("priority", it.get("priority"))
                if it.get("epic"):
                    c.setdefault("epic", it["epic"])   # 형제가 됐으니 배치도 형제와 같다
                # ★ 자리를 옮기면 **본문 규율도 바뀐다.** Sub-Task 본문은 배경을 쓰지
                #   않는 것이 규칙이라(knowledge/07) 짧게 쓰여 있는데, 최상위 Task 로
                #   올라오면 배경·범위(포함/제외)·완료 조건을 갖춰야 한다. 처음엔 몸통을
                #   그대로 들고 올려서 실측 STR2 가 "작업 범위에 제외가 없다" 2건으로
                #   떨어졌다 — 구조는 고쳐 놓고 본문 계약을 깨뜨린 셈이다.
                if not _task_grade_body(c.get("description")):
                    full = _task_for_module(state, c["components"][0], it,
                                            want=str(c.get("summary") or ""))
                    if full.get("description"):
                        c["description"] = full["description"]
            # 부모 **바로 뒤**에 순서대로 넣는다. `items.index` 는 dict 를 값으로 비교해
            # 내용이 같은 다른 항목을 짚을 수 있어 **동일성**으로 찾는다.
            at = next(n for n, x in enumerate(items) if x is it)
            items[at + 1:at + 1] = moved
            names = ", ".join(str(c.get("components")[0]) for c in moved)
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(모듈이 다른 작업({names})은 별도 Task 로 나눴다 — "
                                  "Sub-Task 로 두면 부모 모듈로 워크로드가 잘못 집계된다)").strip()
        # ★ 승격이 **실제로 일어났을 때만** 구조를 다시 쓴다. 처음엔 이 갱신이 루프
        #   밖 조건문 하나로 걸려 있어서, 승격이 없는 초안까지 모양을 덮어썼다
        #   (자식 있는 항목이 섞인 multiple_tasks → task_with_subtasks). 가드가
        #   제 일 아닌 것을 건드리는 전형이다.
        if promoted:
            structure = out["structure"] = draft["structure"] = \
                "task_with_subtasks" if any(i.get("children") for i in items) \
                else "multiple_tasks"

        # ── 요청한 모듈 하나가 통째로 빠졌으면 그 Task 를 만든다 ─────────────
        # 실측 STR2: "리니지 뷰어 성능 측정하고 **쿼리 엔진 인덱스도** 손봐야 해" 에
        # 모델이 Workbench Task 하나만 내고, 본문 작업 범위에
        # **"제외: 쿼리 엔진 인덱스 조정은 별도의 작업으로 진행"** 이라고 적었다.
        # 뭉갠 것보다 나쁘다 — 사용자가 시킨 일의 절반이 **없어졌는데** 초안은 멀쩡해
        # 보이고, 제외 문구가 그것을 정당해 보이게 만든다. 모델 자신이 "별도 작업"이라고
        # 판단했으니 남은 것은 그 별도 작업을 **만드는 일**뿐이다.
        want = modules_in_text(_current_request_boundary_text(state))
        have = {resolve_module((i.get("components") or [""])[0]) for i in items}
        missing = [m for m in want if m not in have]
        if missing and _said_defaults(state) and not qs and len(want) >= 2:
            for mod in missing[:2]:
                extra = _task_for_module(state, mod, items[0])
                if extra:
                    items.append(extra)
                    structure = out["structure"] = draft["structure"] = "multiple_tasks"
                    out["rationale"] = ((out.get("rationale") or "")
                                        + f"\n(요청에 있던 {mod} 작업이 초안에서 빠져 "
                                          "별도 Task 로 채웠다 — 승인 화면에서 뺄 수 있다)").strip()
                else:
                    out["rationale"] = ((out.get("rationale") or "")
                                        + f"\n(확인 필요: 요청에 {mod} 작업이 있는데 초안에 "
                                          "없다 — 별도 티켓으로 만들지 정해야 한다)").strip()

    # 같은 산출물을 모듈만 달리해 2~3벌 만든 경우를 접는다. 단순 문자열 중복이 아니라
    # 행동어(조정/최적화/개선)를 뗀 업무 핵심어로 묶고, module-aliases가 가리키는 실제
    # 모듈의 항목을 남긴다. 예: "쿼리 엔진 인덱스"는 Runtime 한 건만 유지한다.
    if mode == "task" and len(items) > 1 and not has_typed_outcomes:
        removed = _dedupe_semantic_items(state, items)
        if removed:
            structure = out["structure"] = draft["structure"] = \
                ("multiple_tasks" if len(items) > 1 else "single_task")
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(같은 산출물의 중복 초안을 합쳤다: "
                                + ", ".join(removed[:4]) + ")").strip()

    # Several named deliverables are already the complete top-level work plan.
    # Do not turn one of them into an accidental mini-project unless the user
    # explicitly requested stages/Sub-Tasks/splitting.  This is a hierarchy
    # authorization rule, not a case-specific item-count rule.
    if mode == "task" and len(items) > 1:
        removed_children = _drop_unrequested_nested_work(state, items)
        if removed_children:
            structure = out["structure"] = draft["structure"] = "multiple_tasks"
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(요청하지 않은 하위 작업은 추가하지 않았다: "
                                + ", ".join(removed_children[:4]) + ")").strip()

    # 서로 다른 Task로 나눴으면 각 본문도 자기 deliverable만 소유해야 한다. sibling의
    # 고유어가 작업 범위에 들어간 본문은 잘못 복사된 것으로 보고, 확인된 summary와
    # sibling 목록만 사용한 최소 본문으로 되돌린다. 그 뒤 모든 Task에 "별도 ticket"
    # 제외 범위를 채운다 — 새 범위를 발명하는 것이 아니라 이미 합의한 경계를 기록한다.
    if mode == "task" and len(items) > 1:
        repaired = _repair_split_scope(items)
        _ensure_split_exclusions(items)
        if repaired:
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(sibling 작업을 잘못 포함한 본문을 합의된 Task 경계로 "
                                  "되돌렸다: " + ", ".join(repaired[:3]) + ")").strip()

    # ── 완료 조건이 흐리면 판정 가능한 문장으로 다시 쓴다 ────────────────
    # 승인하는 사람에게 제일 중요한 줄이 "테스트 완료"면 티켓이 언제 닫히는지 아무도
    # 모른다. knowledge/07 이 금지하는데 코드로 받치는 자리가 없었다(실측 STR2).
    # ── ★ 뼈대 합의 단계 — 복합 산출물은 **구조 먼저, 살은 나중** ──────────
    # 사용자 요청: 여러 Task / Task+Sub-Task 처럼 복합인 경우, 본문까지 다 쓴 초안을
    # 한 번에 내밀지 말고 **구조도를 먼저 보이고 합의**한 뒤 내용을 채운다.
    # 합의 전에는 본문을 쓰지 않는다 — 구조가 바뀌면 본문은 어차피 버려진다.
    # ★ 게이트를 **조사를 마친 실제 초안 턴**으로 좁힌다(`situation` 이 그 표식이다).
    #   복합이기만 하면 걸리게 뒀더니 부모를 지목해 자식만 붙이는 흐름·카드 편집 흐름까지
    #   뼈대 확인을 물어 왕복이 하나씩 더 붙었다(테스트 38건이 그것을 잡았다).
    #   뼈대 합의가 값을 하는 자리는 **처음부터 여러 티켓을 새로 만드는** 경우다.
    struct_stage = False
    # `plan`(변경 계획)은 이 아래 `_change_plan()` 에서 만들어진다 — 여기서 참조하면
    # UnboundLocalError 다. 변경 갈래는 **의도**로 거른다(그쪽엔 items 도 없다).
    # ★ 사용자가 **형태를 이미 말했으면 묻지 않는다**(said_shape). "사람 나눠서 진행하게"
    #   라고 한 사람에게 "이 구조로 할까요?"를 다시 묻는 것은 취조다 — 그 요청 자체가
    #   구조 지시였다(실측 STR1: 이것 때문에 본문 없는 초안이 카드에 올라갔다).
    if items and not qs and mode != "subtask" and not said_shape \
            and not _said_defaults(state) \
            and _is_create_action(state) \
            and (state.get("situation") or "").strip() \
            and is_composite(items) and not state.get("structure_ok"):
        if structure_accepted(state) and (state.get("structure_plan") or []):
            pass                      # 사용자가 방금 승인했다 — 이번 턴부터 살을 붙인다
        else:
            # Optional structure preference must not suspend an otherwise complete
            # draft. Preserve bodies and show the inferred shape as editable advice.
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(구조 권고: 독립 실행 단위가 여러 개라 현재 복합 초안을 "
                                  "유지했다 — 승인 화면에서 합치거나 나눌 수 있다)").strip()

    # ★ **질문이 붙는 턴에도 초안은 화면에 보인다** — 되묻는 턴이라고 본문을 방치하면
    #   그 얇은 본문이 그대로 사용자에게 간다("확인을 받되 초안은 그대로 보여 준다"가
    #   이 저장소의 규칙이다). 다만 왕복 비용은 갈라 쓴다:
    #     · 배경 채우기(_fill_thin_bodies ①)는 **호출이 없으니 언제나** 돈다
    #     · DoD 다듬기·본문 재작성은 LLM 왕복이라 **질문이 없을 때만**(초안이 확정 단계)
    if items and not struct_stage:
        _preserve_parent_topic_in_children(items)
        _preserve_existing_parent_topic(items)
        for it in items:
            sense_drift = _has_lineage_game_drift(state, it)
            if not _is_bug_item(it) and (_has_placeholder_body(it.get("description"))
                                         or sense_drift):
                it["description"] = _minimal_grounded_body(it)
                out["rationale"] = (
                    (out.get("rationale") or "")
                    + f"\n(\"{str(it.get('summary') or '')[:36]}\" 본문의 "
                    + ("게임 서사 의미 이탈을 데이터 리니지 작업으로 복원했다)"
                       if sense_drift else
                       "작성 지시 placeholder를 실제 최소 본문으로 바꿨다)")
                ).strip()
        _fill_thin_bodies(state, items, repair=not qs)
        # User IDs are typed owner metadata. They must never be reinterpreted as a
        # model, device, or technical target inside the authored ticket body.
        _remove_assignee_semantic_drift(state, items)
        _drop_unrequested_requester_attribution(state, items)
        _repair_bug_facts_from_report(state, items)
        # 모델은 구체적 변경만 받은 경우에도 "사용자 편의성", "운영 효율성", "성능·안정성"
        # 같은 그럴듯한 효과를 배경·범위·DoD에 보탠다. 문장은 자연스럽지만 검증된 사실은
        # 아니다. 원 요청에 없는 품질 차원을 안전한 요청/검증 문장으로 되돌린다.
        _remove_unrequested_quality_claims(state, items)
        _repair_statistics_generation_semantics(state, items)
        _ensure_child_descriptions(items)
        _drop_self_exclusions(items)
        _preserve_explicit_value_transition(state, items)
        # 본문 보정은 위의 참고 불릿 가드 **뒤에서** 새 HTML을 만든다. 생산자 뒤에서
        # 다시 검사하지 않으면 보정 호출이 만든 출처 없는 참고가 그대로 승인 카드로 간다
        # (PASTE1/PASTE2 실측). 같은 함수로 한 번 더 보며 규칙은 복제하지 않는다.
        late_dropped = []
        for it in items:
            it["description"], gone = _drop_unlinked_refs(it.get("description") or "")
            late_dropped += gone
        if late_dropped:
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(본문 보정 뒤 참고에서 출처 없는 항목을 뺐다: "
                                + ", ".join(late_dropped[:4]) + ")").strip()
        if not qs:
            _sharpen_dod(state, items)
            _mark_unspecified_acceptance_criteria(state, items)
            _drop_cross_item_dod(state, items)
            _repair_malformed_dod(state, items)
            _dedupe_dod_rows(items)
            _drop_unrequested_deployment_dod(state, items)
            # Later normalizers can themselves introduce a quality dimension.
            # Seal the user's requested scope at the end, then collapse any
            # conservative evidence row produced by that final normalization.
            _remove_unrequested_quality_claims(state, items)
            _repair_statistics_generation_semantics(state, items)
            # Scope sealing can itself introduce a conservative evidence sentence.
            # Pass it through the same DoD source of truth once more so no late
            # generic review wording escapes into the approval payload.
            _sharpen_dod(state, items)
            _dedupe_dod_rows(items)
            _ensure_minimum_task_dod(state, items)

    # A meeting record is an authoritative decision record.  Preserve reviewers in the
    # ticket body and remove optional create fields that the minutes never decided.
    return finalize_work_architect(
        self, state, out, items, qs, draft, mode, structure, why, action, turns,
        struct_stage, typed_parent_slots, policies,
    )


__all__ = ["apply_work_architect"]
