"""Work Architect — 막연한 요구를 실행 가능한 티켓 트리 초안으로 만든다. 모자라면 **되묻는다**.

이 에이전트의 어려운 점은 "만들기"가 아니라 **"언제 묻고 언제 만들 것인가"**다.
다 물어보면 취조가 되고, 안 물어보면 엉뚱한 걸 만든다. 기준은 하나다:

  **찾아보면 아는 것은 묻지 않는다. 사용자만 아는 것만 묻는다.**

관련 티켓·이전 담당자·모듈 인원·가능한 컴포넌트 목록은 **자료에 이미 실려 있다**. 반면 범위
("어디까지가 이번 일인가")·완료 조건·기한·의도는 사용자 머릿속에만 있다. 그것만 묻는다.

컴포넌트·타입·라벨을 지어내지 않기 위해 **실제 목록을 보고 쓴다.** 다만 그 목록을 도구로
두지 않고 **코드가 미리 조회해 자료로 준다**(`_placement_material`·`_rules_material`) —
도구로 두면 모델이 매 턴 다시 부르고, 도구 호출 한 번이 곧 LLM 왕복 한 번이다.
"""

from __future__ import annotations

import json
import re as _re

from app.agent.prompts.roles import SYSTEM_WORK_ARCHITECT
from app.agent.workflow.agents.base import StructuredAgent, invoke_schema
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import (MAX_REFINE_TURNS, AgentState, Intent, Node,
                                      conversation, last_user_text, note, reads_as_bug,
                                      request_text)

# 신규 구축 규모의 신호 — **프롬프트 넛지와 하향 편향 가드가 같은 목록을 본다.**
# 갈라지면 "프롬프트는 시키는데 코드는 안 막는" 상태가 되고, 그건 이 저장소가 반복해서
# 데인 패턴이다(실측 STARR1 재발: 넛지만 있고 가드가 없어 파이프라인 신규 구축이 다시
# 단일 Task 로 뭉쳐졌다).
BUILD_WORDS = ("파이프라인", "구축", "시스템", "개발해야")

ITEM = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Distinct Korean summary for one deliverable."},
        "tier": {"type": "string", "enum": ["epic", "task", "subtask"],
                 "description": "Hierarchy tier. Bug, Story, Improvement, and Feature are all task tier."},
        "issue_type": {"type": "string",
                       "description": "Exact issue type name allowed by project createmeta."},
        "type": {"type": "string", "description": "Exact allowed Jira type such as Task, Story, Bug, Improvement, or Sub-Task."},
        "epic": {"type": "string", "description": "Verified parent Epic key in task mode; empty for an intentional top-level Task."},
        "epic_name": {"type": "string",
                      "description": "Short Korean Epic label for WBS and badges, at most ten characters; empty uses summary."},
        "parent": {"type": "string", "description": "Verified Task-tier parent key in subtask mode."},
        "description": {
            "type": "string",
            "description": (
                "Korean HTML ticket body. A general Task uses <h3>배경</h3>, <h3>작업 범위</h3>, "
                "and <h3>완료 조건 (DoD)</h3> in that order, with optional <h3>참고</h3>. Background "
                "states only a verified trigger or that the user's concrete change was requested. When no "
                "reason is verified, write the literal action followed by `요청됨`; never add generic claims "
                "about user experience, efficiency, accuracy, performance, stability, or operational benefit, "
                "and never specialize a vague verb such as `개선` into an unmentioned quality dimension. Scope "
                "includes inclusions and exclusions; DoD contains two to four independently testable "
                "<li data-checked=\"false\"> items. A Bug instead separates "
                "재현 경로, 기대 동작, and 실제 동작. Every reference must contain a real ticket key or "
                "verified URL and explain relevance. Never copy one reference list across unrelated items. "
                "Represent real child work in children, not as a prose candidate list."),
        },
        "children": {
            "type": "array",
            "description": (
                "Actual Sub-Tasks created under this new parent. Use separate children for distinct execution "
                "units and distribute repeated target batches when evidence supports it. Do not decompose work "
                "whose approach is still undecided."),
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Distinct Korean child summary; do not copy the parent title."},
                    "description": {"type": "string",
                                    "description": "Korean HTML with only this child's 작업 범위 and 완료 조건 (DoD); do not copy parent background."},
                    "assignee": {"type": "string", "description": "Verified user ID, or empty."},
                    "duedate": {"type": "string", "description": "YYYY-MM-DD, or empty when unknown."},
                },
                "required": ["summary"],
            },
        },
        "components": {"type": "array", "items": {"type": "string"}},
        "labels": {"type": "array", "items": {"type": "string"}},
        "priority": {"type": "string"},
        "duedate": {"type": "string", "description": "YYYY-MM-DD, or empty when unknown; never invent."},
    },
    "required": ["summary", "type"],
}

# 질문은 문자열이 아니라 **폼으로 그릴 수 있는 구조**로 받는다. "P1/P2/P3 중 뭘로 할까요?"를
# 문장으로 내면 사용자는 타이핑해야 하지만, choice+options 로 내면 버튼 하나다.
# field 를 표시하면 화면이 그 속성 전용 자동완성(담당자·Epic·우선순위)을 붙인다.
QUESTION = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "One concise Korean question."},
        "kind": {"type": "string", "enum": ["text", "choice", "multi", "date"],
                 "description": "choice=one option, multi=multiple options, date=calendar value, text=free prose. "
                                "Prefer choice whenever a recommendation is possible; reserve text for facts "
                                "such as reproduction steps or background. The UI adds a custom-input option."},
        "options": {"type": "array", "items": {"type": "string"},
                    "description": "Two to five Korean options for choice, with the recommended option first and an optional short reason."},
        "field": {"type": "string",
                  "enum": ["", "assignee", "epic", "priority", "duedate", "component",
                           "target", "parent", "scope", "acceptance", "reproduction"],
                  "description": "Ticket field being asked; the UI supplies field-specific autocomplete."},
        "required_input": {
            "type": "boolean",
            "description": ("True only when no valid and truthful draft can be produced without user-owned "
                            "information. False for a preference with a safe default or omission."),
        },
        "why_required": {
            "type": "string",
            "description": ("One concise Korean reason naming the unresolved decision or payload field when "
                            "required_input is true; otherwise an empty string."),
        },
    },
    "required": ["question", "kind", "required_input", "why_required"],
}

SCHEMA = {
    "type": "object",
    "properties": {
        "interpretation": {
            "type": "string",
            "description": ("Only in a pre-research interpretation turn: two or three Korean sentences "
                            "covering target, purpose, and intended artifact. Empty in other turns."),
        },
        "questions": {
            "type": "array", "items": QUESTION,
            "description": ("At most three questions about user-owned scope, DoD, deadline, or intent. "
                            "Never ask for a fact available through internal retrieval."),
        },
        "mode": {"type": "string", "enum": ["task", "subtask", "epic"],
                 "description": "Creation mode. subtask requires a verified existing Task-tier parent. epic contains exactly one Epic item."},
        "structure": {
            "type": "string",
            "enum": ["single_task", "task_with_subtasks", "multiple_tasks", "new_epic"],
            "description": (
                "Structure decision. Default to single_task. task_with_subtasks is one deliverable split by "
                "stage, target, or owner; multiple_tasks is independent deliverables; new_epic requires at "
                "least two sprints, at least three cross-module or cross-owner Tasks, no suitable verified "
                "existing Epic, and an explicit independent reporting intent."),
        },
        "structure_source": {
            "type": "string", "enum": ["user_specified", "inferred"],
            "description": (
                "Who selected the structure. user_specified means the user explicitly named it; inferred "
                "means the agent selected it from the work shape."),
        },
        "structure_why": {
            "type": "string",
            "description": "One Korean sentence citing the factual signal behind the structure decision.",
        },
        "items": {"type": "array", "items": ITEM,
                  "description": "Ticket drafts. May be empty while a blocking question remains."},
        "change": {
            "type": "object",
            "description": "Existing-ticket change plan for modify intent only; items stays empty.",
            "properties": {
                "key": {"type": "string", "description": "One verified existing ticket key to change."},
                "keys": {"type": "array", "items": {"type": "string"},
                         "description": "Complete verified key snapshot for the same bulk change; leave key empty."},
                "assignee": {"type": "string", "description": "New user ID; empty unassigns; omit when unchanged."},
                "duedate": {"type": "string", "description": "New YYYY-MM-DD due date; omit when unchanged."},
                "priority": {"type": "string", "description": "New exact priority; omit when unchanged."},
                "summary": {"type": "string", "description": "New Korean summary; omit when unchanged."},
                "description": {"type": "string",
                                "description": "Complete replacement HTML body, only when requested."},
                "labels": {"type": "array", "items": {"type": "string"},
                           "description": "Complete replacement labels; omit when unchanged."},
                # ★ 이 필드가 **스키마에 없어서** 모델이 컴포넌트 변경을 표현할 방법이
                #   없었다(실측 MOD8: "컴포넌트를 Catalog 로 바꿔줘" 가 조용히 사라졌고,
                #   라벨 변경만 남아 체커가 '변경 계획 있음'으로 통과시켰다).
                #   쓰기 도구(update_ticket)는 처음부터 components 를 받고 있었다 —
                #   계획을 세우는 쪽에만 구멍이 있었다.
                "components": {"type": "array", "items": {"type": "string"},
                               "description": "Complete replacement component list with exactly one value; omit when unchanged."},
                "status": {"type": "string",
                           "description": "Exact target status name for a requested transition; code resolves its ID."},
                "link": {"type": "object",
                         "description": "Only for an explicit ticket-link request.",
                         "properties": {
                             "other": {"type": "string", "description": "Verified other ticket key."},
                             "relation": {"type": "string",
                                          "description": "Verified Jira link type such as Blocks or Relates."}}},
                "comment": {"type": "string",
                            "description": "Korean comment explicitly requested with the change; otherwise omit."},
            },
        },
        "rationale": {"type": "string", "description": "Two or three Korean user-visible sentences explaining the structure or change."},
    },
    "required": ["questions", "mode", "items"],
}


class WorkArchitect(StructuredAgent):
    """★ 도구를 쓰지 않는다 — 필요한 재료는 **코드가 전부 미리 조회**한다.

    예전엔 ToolAgent 였다. 그런데 가진 도구가 하나도 빠짐없이 사전취합과 중복이었다:
    `list_ticket_options`/`list_child_types` → `_placement_material` 이 이미 준다,
    `find_parent_epic` → `_epic_options` 가 이미 부른다, `list_transitions` → apply 가
    코드로 해석한다, `validate_ticket_plan` → Auditor 의 `_machine_check` 가 같은
    `validate_bulk` 로 한다, `search_rules` → 아래 `_rules_material` 로 싣는다.

    그런데도 모델은 매 턴 그것들을 다시 불렀고, 도구 호출 한 번이 곧 LLM 왕복 한 번이라
    **생성 턴 하나에 work_architect 만 12회 · 86초 · 226k 토큰**을 먹었다(실측 기준선).
    재료가 이미 손안에 있으면 순회할 이유가 없다 — 한 번 묻고 스키마로 받는다.
    """

    name = Node.WORK_ARCHITECT
    temperature = 0.3          # 초안은 약간의 폭이 필요하다
    _force_draft = False       # 질문-도피 재시도 플래그(단일 사용자 앱 — 인스턴스 보관으로 충분)

    def node(self):
        base = super().node()

        def run(state):
            out = base(state)
            # ── 질문-도피 가드: "알아서" 위임인데 질문만 내고 초안 0건이면 **한 번 재시도**.
            # 프롬프트(force_rule)로 막아도 부하·모델 변덕으로 재발한다(스위트 실측 5건).
            # 해석 확인 턴(조사 전 — situation 없음)은 질문이 정답이므로 제외.
            try:
                dodged = (bool(out.get("questions"))
                          and not ((out.get("draft") or {}).get("items"))
                          and not ((out.get("change_plan") or {}).get("key"))
                          and (_said_defaults(state) or state.get("bulk_targets"))
                          and not any(_question_requires_input(q)
                                      for q in (out.get("questions") or []))
                          and (state.get("situation") or "").strip())
                # 초안 수정 요청인데 수정본(items)도 유효한 변경 계획도 없다 — 말로만
                # 설명하고 끝(실측 2회). mentioned_keys 는 오염될 수 있어 조건에 안 쓴다.
                cp0 = out.get("change_plan") or {}
                dodged = dodged or (
                    (state.get("intent") or "") == "modify"
                    and bool((state.get("draft") or {}).get("items"))
                    and not ((out.get("draft") or {}).get("items"))
                    and not (cp0.get("key") or cp0.get("keys")))
            except Exception:
                dodged = False
            if dodged and not WorkArchitect._force_draft:
                WorkArchitect._force_draft = True
                try:
                    out2 = base(state)
                    if ((out2.get("draft") or {}).get("items")) \
                            or ((out2.get("change_plan") or {}).get("key")):
                        out2["trace"] = note(state, self.name,
                                             f"질문 도피 재시도 → 초안 "
                                             f"{len((out2.get('draft') or {}).get('items') or [])}건")
                        return out2
                finally:
                    WorkArchitect._force_draft = False
            return out

        return run


    def system(self, state):
        forced = (state.get("turns") or 0) >= MAX_REFINE_TURNS
        extra = ("\n\n## Refinement Limit Reached\n\nStop asking optional preference questions. If required "
                 "user-owned input is still missing, return only those questions with "
                 "`required_input=true` and no competing payload; the turn limit never permits guessing a "
                 "required value. Otherwise complete the safest draft from verified information, leave "
                 "optional fields empty, and record a material Korean `확인 필요` in `rationale`."
                 if forced else "")
        if WorkArchitect._force_draft:
            extra += ("\n\n## Required Draft Recovery\n\nThe previous attempt omitted the requested artifact. "
                      "Return `questions=[]` and complete `items`. For a draft-revision request, return the "
                      "entire revised item set from Current Draft Data, not an explanation. Record unresolved "
                      "points in Korean in `rationale`.")
        # 정적 지시는 prompts/roles/work_architect.md — 동적 경고(횟수 소진)만 코드가 덧붙인다.
        # ★ 경로에 안 쓰이는 절은 싣지 않는다. 기존 티켓의 필드를 바꾸는 턴에 '어떻게
        #   쪼갤 것인가'·'본문 4섹션'·'Epic 생성' 지시는 판단에 쓰이지 않으면서 매 호출
        #   2천 토큰을 태운다(work_architect system 4.2k tok 중 절반이 생성 전용이었다).
        return persona(state, _role_md(state) + extra)

    def task(self, state):
        # "알아서/기본값" 은 선택 재량만 위임한다. 이전 계약은 질문을 전부 금지해 target·parent·
        # 재현 조건처럼 payload 성립에 필요한 값까지 추측하게 만들었다. 필수 입력에는 명시적
        # 표식을 요구하고, 그 외 선호 질문만 억제한다.
        said = conversation(state)
        defaults = any(w in said for w in ("알아서", "기본값", "맡길게", "맡기겠"))
        force_rule = ("\n- The user delegated optional choices; this does not supply required input. Return "
                      "`questions=[]` and at least one complete item when the literal request and verified "
                      "evidence support a valid conservative draft. If user-owned information is indispensable "
                      "to identify the target, action, valid hierarchy, exact mutation, or truthful minimum "
                      "scope/acceptance boundary, return up to three questions with `required_input=true`, a "
                      "specific Korean `why_required`, and no competing payload. Mark optional preference "
                      "questions `required_input=false`; the runtime suppresses them under delegation. Select "
                      "the best supported Epic candidate without asking; use an intentional top-level Task if "
                      "none fits. Preserve supplied assignee IDs and deadlines; leave optional unspecified "
                      "fields empty. Record only material defaults or open facts in Korean `rationale`."
                      if defaults else "")
        # 버그는 새 기능과 초안 규칙이 다르다 — 갈래를 지시문으로 가른다(Prompt Chaining 의 분기).
        # ★ **버그 초안은 의도가 아니라 요청의 낱말로 고른다**(사용자 지적 — "결국 버그
        #   신고도 Task 생성 아니야? type 이 Bug 일 뿐이지"). 예전에는 `report_bug` 라는
        #   갈래가 있었지만 `plan_work` 와 지나는 노드도 도구도 같았고, 코드에서 다르게
        #   쓰이는 곳이 이 goal 하나뿐이었다 — **갈래가 아니라 산출물 유형**이다.
        #   낱말로 고르면 분류가 흔들려도 본문 규율이 안 바뀐다: "적재 배치가 계속
        #   실패한다"가 어느 갈래로 가든 재현·기대·실제가 유지된다.
        _said = request_text(state) + " " + conversation(state)
        _is_bug = reads_as_bug(_said)      # 판정은 state.reads_as_bug 한 곳에만 있다
        if _is_bug and (state.get("intent") or "") in Intent.DRAFTS_TICKETS:
            goal = """Draft one Task-tier `Bug`.

- Use `type="Bug"` and a Korean summary that names the observed symptom.
- Separate the Korean body sections `재현 경로`, `기대 동작`, and `실제 동작`. Ask only for a missing material reproduction fact; never fabricate it.
- Add a verified suspected-cause ticket key only when research directly supports that relationship.
- If an open Bug already has the same symptom, do not draft a duplicate; ask the user to choose how to proceed.
- Default to one Bug without Sub-Tasks unless the user explicitly requests a valid split."""
        elif ((state.get("intent") or "") == Intent.MODIFY
                and not state.get("mentioned_keys")
                and (state.get("draft") or {}).get("items")):
            goal = """Revise the pending draft; this is not a change to an existing Jira ticket.

Return the complete revised `items` set from Current Draft Data, preserving every unaffected field. Do not create `change` or ask a question; the revision becomes a new approval card."""
        elif (state.get("intent") or "") == Intent.MODIFY:
            goal = """Build an existing-ticket `change` plan and leave `items=[]`.

- Use only verified ticket keys. If a user-supplied key cannot be verified, ask one target-resolution question.
- Include only fields explicitly requested.
- For a condition-based bulk change, put the complete verified target snapshot in `change.keys` and leave `key` empty. If retrieval failed, record the Korean phrase `대상 조회 실패` in `rationale` rather than inventing targets.
- Resolve relative dates from today's runtime date and store `YYYY-MM-DD`.
- Resolve an assignee to an exact user ID. Ask only when a supplied name cannot be mapped from evidence.
- Include `comment` only when explicitly requested.
- Put a requested transition's exact target name in `change.status`; code resolves the transition ID. Put a requested ticket relation in `change.link {other, relation}` rather than a comment.
- A comment-only request creates only `key` or `keys` plus `comment`; add no field change or transition.
- Do not ask for execution permission; the deterministic approval card owns approval.
- Creation placement rules do not apply to a change plan.
- Ticket deletion is unsupported. For a deletion request, return no `change` and no `items`; explain `삭제는 지원되지 않음` in Korean `rationale` and offer one non-destructive transition or archive-label alternative. Never create a new Task to perform deletion."""
        elif (state.get("intent") or "") == Intent.PLAN_WORK \
                and not (state.get("situation") or "").strip():
            # ── 해석 확인 턴(조사 전) — 혼자 오래 조사하고 한 번에 결론 내는 호흡이
            # 방향 착오를 낳았다(실측 STARR NDV). 조사 **전에** 해석과 갈림길을 확인받는다.
            goal = """Confirm interpretation before research and return `items=[]` in this turn.

- Write `interpretation` in two or three Korean sentences: target or technology, understood purpose, and intended artifact. Preserve the user's unique terms and label inference as `~로 이해함`.
- Ask at most two high-impact questions, preferring `choice` with the recommended option first. Ask only slots marked `ASK` in Minimum Creation-Input Audit; never ask `INFER` or `LATER` slots.
- Candidate material questions are: initial scope such as review, PoC, or minimum implementation; the verified trigger or business background; an observable DoD artifact or metric; decomposition only when the user did not specify structure; ambiguous module from real placement values; and ambiguous Epic placement from verified candidates plus `없음(최상위)` and `새 Epic 검토`.
- Do not ask for values already present in conversation or discoverable through the next research step.
- When the user delegates optional choices with `알아서`, skip preference questions. Keep any question whose answer is required to identify a valid action or truthful payload and mark it `required_input=true`."""
        else:
            goal = """Build an executable ticket draft. Ask a blocking question instead only when material user-owned information prevents a safe draft.

- Decide `structure` and `structure_why` first. Default to `single_task`; require factual evidence to choose Task with Sub-Tasks, multiple Tasks, or a new Epic. Use `new_epic` only when all four Epic criteria in the role contract hold.
- Map every independent deliverable clause in Original Request Data to exactly one item before deriving any internal stage. Do not invent a separate analysis, design, report, or optimization deliverable unless the user requested it or verified evidence makes it mandatory.
- With `structure="multiple_tasks"`, cross-module or independently accepted deliverables are sibling Tasks. A requested deliverable must not remain as a child of another sibling or appear at both tiers.
- Put decomposed execution units in actual `children`, never as prose-only future candidates.
- When the user names a verified existing Task-tier parent, use `mode="subtask"` and set each item's `parent` to that key. Do not wrap the requested children in a new Task. For multiple named Task-tier parents, assign each child to its intended parent. Never use a Sub-Task as parent.
- For a new parent draft, top-level `items` contain Task-tier issue types; Sub-Tasks belong in `children`."""
        # 형태를 사용자가 말했는지 **코드가 판정해** 알려 준다 — 같은 문장을 모델이 매번
        # 다르게 읽지 않도록. 말했으면 그대로 따르고, 열려 있으면 판단하되 갈림이 크면
        # 시스템이 확인 질문을 붙인다(모델이 임의로 되묻지 않게).
        shape, word = shape_hint(state)
        # ★ 구조가 **이미 합의된** 턴 — 이제 할 일은 살을 붙이는 것뿐이다. 이 지시가 없으면
        #   모델이 첫 항목만 제대로 쓰고 나머지는 제목만 남긴다(실측 STR2: 3건 중 2건이
        #   '작업 범위'·'완료 조건' 없이 나왔다). 뼈대 단계에서 본문을 지운 뒤라 **처음부터
        #   쓰는 것**이므로, 몇 건이든 전부 채우라고 못 박아야 한다.
        if (state.get("structure_ok") or structure_accepted(state)) \
                and (state.get("structure_plan") or []):
            goal += ("\n- The structure in Agreed Structure Data is already approved. Add no new deliverable "
                     "and omit none. Complete the Korean background, explicit `포함:` and `제외:` scope, and "
                     "testable DoD for every item; never leave later items as title-only placeholders. "
                     "Preserve each requested deliverable exactly once. Promote a child whose module or "
                     "independent deliverable differs from its parent into a sibling Task and remove the "
                     "duplicate child.")
        if shape:
            goal += (f"\n- The user explicitly selected the shape `{word}` -> `{shape}`. Preserve it, set "
                     "`structure_source=\"user_specified\"`, and do not recommend a competing shape.")
        elif any(w in request_text(state) for w in BUILD_WORDS):
            # 신규 구축 규모는 하향(단일 Task 뭉개기)이 실측된 실패 모드다 — 넛지를 준다.
            goal += ("\n- This is a new build or pipeline request. When design, implementation, validation, "
                     "and integration have distinct owners or time boundaries, use `task_with_subtasks` and "
                     "real `children`; listing stages as DoD bullets is not decomposition.")
        elif not defaults:
            goal += ("\n- The user specified work but not shape. Select it, set "
                     "`structure_source=\"inferred\"`, and do not add a separate structure-confirmation "
                     "question because the runtime adds one when required.")
        ev = "\n".join(f"- {e.get('key','')} {e.get('title','')} — {e.get('why','')}"
                       for e in (state.get("evidence") or []))
        # ★ 후속 턴에는 **지금 고치는 초안 전문**을 준다 — 이게 없으면 모델은 매번 처음부터
        #   다시 쓰고, 그 사이 제목·주제가 흘러간다(실측: 원 요청이 Epic 주제로 둔갑).
        prev = draft_full_text(state.get("draft")) if (state.get("turns") or 0) > 0 else ""
        data = wrap_data(
            data_block("Minimum Creation-Input Audit: Ask Only ASK Slots; Infer or Defer Others",
                       # ★ **새 티켓을 만드는 갈래에만** 건다. `DRAFTS_TICKETS` 에는 MODIFY 도
                       #   들어 있어서, "티켓 전부에 코멘트 남겨줘" 같은 요청에 배경·완료
                       #   조건·분할 여부를 물었다 — 코멘트 남기는 일에 "완료 조건이
                       #   무엇인가요"는 부조리하다(사용자 관점 리뷰 F4, blocker 2건).
                       #   오늘 슬롯을 늘리면서 이 갈래까지 샌 것이고, 계약 배터리는
                       #   change_plan 만 보느라 못 잡았다.
                       _slot_audit(state)
                       if (state.get("intent") or "") == Intent.PLAN_WORK
                       else ""),
            data_block("Current Draft Data: Preserve Unaffected Items and Children; Append Only New Requested Items", prev),
            data_block("Verified Bulk-Change Target Snapshot: Put Every Key in change.keys",
                       ", ".join(state.get("bulk_targets") or [])),
            # ★ 구조 피드백은 **누적해서** 준다. 마지막 한 마디만 주면 앞의 수정이 되돌아간다
            #   ("3번 빼줘" → 반영 → "1번을 둘로" → 3번이 되살아남). 사용자가 같은 말을
            #   두 번 하게 만드는 순간 이 피드백 루프는 쓸모가 없어진다.
            data_block("Cumulative Structure Feedback: Apply Every Item in Order",
                       "\n".join(f"{n}. {x}" for n, x
                                 in enumerate(state.get("structure_notes") or [], 1))),
            data_block("Agreed or Pending Structure Data: Revise This Structure Instead of Rebuilding It",
                       "\n".join(
                           f"- {i.get('summary')}"
                           + (f" [{', '.join(i.get('components') or [])}]"
                              if i.get("components") else "")
                           + ("".join(f"\n    · {c}" for c in (i.get("children") or []))
                              if i.get("children") else "")
                           for i in (state.get("structure_plan") or []))),
            data_block("Research Analyst Current-Situation Summary", state.get("situation")),
            # 사전 조사(코드 취합) — 재배분 후보처럼 **키 목록이 곧 재료**인 자료가 여기
            # 실린다. situation(모델 요약)만 주면 목록이 요약에서 증발한다(실측 M2).
            data_block("Prefetched Research Data and Exact Candidate Keys",
                       (state.get("pre_survey") or "")[:2000]),
            data_block("Verified Evidence Tickets", ev),
            # 외부 기술 조사는 지금까지 ResearchAnalyst·KnowledgeCurator 에만 갔다. 그런데 **본문의 배경과
            # 범위를 쓰는 것은 WorkArchitect** 다 — 그래서 "StarRocks 가 읽는 Iceberg 테이블의
            # 통계", "플랜 반영 확인" 같은 도메인 관계가 조사에는 있는데 초안에는 안 실렸고,
            # Sub-Task 제목이 "설계 완료/테스트 수행" 처럼 일반어로 떨어졌다
            # (DRAFT-COMPARISON 갭 ①). data_block 은 비면 빈 문자열이라, 웹 조사가 돈
            # 턴(신기술 요청)에만 붙는다 — 평소 경로의 토큰은 그대로다.
            data_block("External Technology Research Data: Use for Domain Relationships, Never as Verified Internal State",
                       (state.get("web_context") or "")[:1500]),
            data_block("Verified Placement Values: Epic, Component, Label, Priority, and Type",
                       _placement_material(state)),
            # 예전엔 `search_rules` 도구로 모델이 직접 읽었다 — 부를 때만 보이고 안 부르면
            # 규칙 없이 썼다. 초안 작성에 늘 필요한 것이므로 코드가 싣는다(정적 RAG).
            data_block("Applicable Internal Authoring Rules", _rules_material(state)),
            data_block("Verified Suitable Parent Epic", state.get("epic_candidate")),
            data_block("Materially Equivalent Existing Work", "exists: notify the user before creating"
                       if state.get("already_exists") else ""))
        return f"""\
# Task

{goal}

## Constraints

- Never invent a ticket key, person, date, field value, or source absent from verified research.
- The subject of every summary and body is Original Request Data. Epic bodies, comments, and related tickets provide placement or evidence only. Preserve unique request terms such as product, technology, table, asset, and symptom.
- Write each Korean summary as `[Module]` plus a distinct action phrase for one deliverable.
- In `배경`, state only the verified trigger or the fact that the concrete change was requested. Never invent generic benefits or current problems such as improved user experience, efficiency, accuracy, performance, stability, or reduced exposure.
- Write a structured HTML body with the correct Korean sections. Use independently testable task-list DoD items. Use a comparison table or verified link only when needed; never return one wall-of-text paragraph.
- Select an Epic independently for each Task from Verified Placement Values. If no candidate fits, choose an intentional top-level Task. Ask an Epic choice only when materially different verified candidates remain and the user did not delegate the choice.
- Use exactly one verified component per item. Split independent cross-module deliverables to avoid double-counted workload.
- Prefer existing verified labels. Do not create a typo or synonym; a truly new label must remain visible as new on the approval card.
- Split independent deliverables into Tasks. Split one deliverable shared across stages, targets, or owners into real Sub-Task `children`.
- If equivalent work already exists, ask how to proceed unless the user delegated with `알아서`; under delegation, draft safely and record the overlap in Korean `rationale`.
- A request to create new work must not become a `change` to a similar existing ticket. Use that ticket only as relevant evidence.{force_rule}

## Conversation Data

{conversation(state)}

## Original Request Data

{request_text(state)}

## Current User Message Data

{last_user_text(state)}{data}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
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
        # 위임은 선택 질문만 제거한다. 필수 질문은 남겨 임의 payload보다 먼저 답받는다.
        if delegated:
            qs = [q for q in qs if _question_requires_input(q)]
        # 모델이 낸 질문은 **초안을 만들기 전에 답이 필요한 질문**이다. 뒤에서 코드가
        # 붙이는 구조 확인 질문과 구분해 둔다 — 전자는 초안과 함께 내면 사용자가 무엇을
        # 승인해야 할지 모순되고, 후자는 초안의 모양을 보여 주려고 일부러 함께 낸다.
        model_questions = bool(qs)
        items = [i for i in (out.get("items") or []) if isinstance(i, dict) and i.get("summary")]
        for item in items:
            if item.get("issue_type") and not item.get("type"):
                item["type"] = item["issue_type"]
        mode = out.get("mode") or "task"
        # 조사까지 끝난 명시적 "기존 Task 아래 A와 B Sub-Task 추가" 요청에서 모델이
        # interpretation만 내고 items를 비우는 변동이 있다. 대상·부모·산출물이 모두 사용자
        # 문장에 있으므로 다시 묻지 않고 최소 초안을 결정적으로 복원한다(SUB2 실측).
        if not items and not qs and state.get("situation"):
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
        # ★ 기계적 가드 — task 배치에 Sub-Task 가 섞이면 그 항목은 뺀다. 프롬프트로 막았는데도
        #   실 모델이 섞어 낸 적이 있고, 그대로 두면 검증 실패 → 재작성 왕복만 태우다
        #   한도 소진으로 끝난다. 빼는 것이 반려보다 낫다(부모 생성 후 2차 승인으로 붙일 수 있다).
        # 모델이 parent 를 비운 채 Sub-Task 를 내는 일이 잦다 — 사용자가 "DL-9090 밑에"
        # 라고 지목했으면 그 키가 부모다(실재는 조사에서 이미 확인됐다). **모드와 무관하게**
        # 채운다: mode=subtask 로 내면서 parent 만 빠뜨리면 검증에서 통째로 반려돼
        # "만들겠습니다" 라고 말해 놓고 초안이 0건이 된다(실측: PAR1).
        named = [k for k in (state.get("mentioned_keys") or []) if _ticket_exists(k)]
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
                   "options": ["별도의 최상위 Task로 만든다 (권장)",
                               "부모 Task를 지정한다", "이번에는 만들지 않는다"]}]
            model_questions = False
        if named:
            for i in items:
                if (i.get("type") or "").lower().startswith("sub") or mode == "subtask":
                    if not str(i.get("parent") or "").strip():
                        i["parent"] = named[0]

        # ★ 새 일을 "단계별 Sub-Task"로 만들라는 요청인데 모델이 임의의 기존 티켓을 골라
        # mode=subtask 로 내면, 사용자가 요청한 **새 부모 Task가 사라진다**. S1 재검증에서
        # 부모 키를 말하지 않았는데 Sub-Task 3건만 승인 카드에 올랐다. 지목한 부모가 없는
        # 새 일은 Task 배치로 되돌려 아래 단계/번호 접기가 `Task 1 + children N`으로 만든다.
        explicit_new_tree = shape_hint(state)[0] == "task_with_subtasks" and not named
        if explicit_new_tree and mode == "subtask" and items:
            for i in items:
                i["type"] = "Task"
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
                i["type"] = "Task"
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
                        i["type"] = "Task"
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
                i["type"] = "Task"
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
        # 초안 관련 인터뷰의 마지막엔 항상 **자유 의견** 질문 하나를 붙인다(사용자 요청) —
        # 객관식 보기가 못 담는 계획·우려를 받아낼 출구. 코드가 붙이므로 모델이 잊지 못한다.
        # ★ 이미 넷 이상 물었으면 **자유 의견 칸은 붙이지 않는다.** 슬롯이 늘어(배경·완료
        #   조건·분할) 질문이 6개까지 나왔는데(실측 ASK1·DUP1·RULE1), 그쯤 되면 출구가
        #   하나 더 있는 것이 아니라 **취조로 읽힌다.** 자유 의견은 물을 것이 적을 때
        #   객관식이 못 담는 말을 받으려던 장치다 — 많이 물었으면 이미 받은 것이다.
        if qs and len(qs) < 3 \
                and not any(_question_requires_input(q) for q in qs) \
                and not any(q.get("kind") == "text" and "자유" in q.get("question", "") for q in qs):
            qs.append({"question": "그 밖에 반영할 의견이나 원하는 진행 방식이 있으면 자유롭게 "
                                   "적어 주세요 (없으면 건너뛰어도 됩니다)",
                       "kind": "text", "options": [], "field": ""})
        # ── 시스템·픽스처 라벨은 사람이 붙이는 것이 아니다 ────────────────
        # 배치 재료로 기존 라벨 **목록**을 주니 모델이 거기서 아무거나 집었다(실측:
        # 카탈로그 검색 개선 티켓에 `ui-fixture`). 데이터 관리용 표식은 업무 티켓의
        # 라벨이 아니고, 잘못 붙으면 그 필터로 조회하는 화면이 오염된다.
        # 판정은 **딱 이 부류만** — 일반 라벨의 적절성은 사용자가 카드에서 판단한다.
        said_all = (request_text(state) + " " + last_user_text(state)).lower()
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
        if mode == "task" and items and _simple_delegated_request(state):
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
        src = out.get("structure_source") or ""
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
        elif said_shape == "single_task" and mode == "task" and items:
            # `Task 만들어줘`처럼 issue type을 단수로 지정했으면 모델이 임의로 붙인
            # 설계/구현/검증 children을 접는다. 사용자 지정 형태는 권고가 아니라 결정이다.
            best = _best_item_for_request(state, items)
            best.pop("children", None)
            items[:] = [best]
            structure = out["structure"] = "single_task"
            why = out["structure_why"] = "사용자가 단일 티켓 타입으로 생성을 요청했다"
        if said_shape:                      # 사용자가 말한 것은 판단이 아니다 — 코드가 확정한다
            src = "user_specified"

        # 생성 payload는 Story Point를 지원하지 않는다. 모델이 rationale에 "생성 후 할당"
        # 같은 약속을 남겨도 실제 승인 payload와 모순되므로 제거하고 정확한 안내를 남긴다.
        sp = _re.search(r"(?:스토리\s*포인트|Story\s*Points?|\bSP)\s*(?:를|은|:|=)?\s*(\d+)",
                        request_text(state), _re.I)
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
        # 상대 날짜 산술은 모델의 언어 능력이 아니라 런타임 계산으로 확정한다. 생성 배터리
        # ATTR1에서 같은 날의 "이번 주 금요일"을 다음 주 화요일로 내면서도 형식 검사는
        # 통과했다. 여러 티켓에 서로 다른 기한이 섞인 경우까지 추측하지 않도록, 현재 사용자
        # 메시지가 상대 기한을 명시한 단일 초안에만 적용한다.
        _apply_relative_due_to_single_draft(state, items)
        draft = {"mode": out.get("mode") or "task", "items": items,
                 "structure": structure, "structure_why": why,
                 "structure_source": src,
                 "rationale": out.get("rationale") or ""}
        # ★ 형태가 **우리 판단**이고 기본값(단일 Task)에서 올라간 것이면 한 번 확인한다.
        #   티켓 하나로 끝날 일을 다섯 개로 쪼개 놓고 승인만 받는 것은 사용자가 원한 게
        #   아닐 수 있다. 사용자가 '알아서'라고 했으면 묻지 않는다(위임이 이긴다).
        if (src == "inferred" and structure in ("task_with_subtasks", "multiple_tasks",
                                                "new_epic")
                and items and not qs and not _said_defaults(state)):
            qs = [{"question": _shape_question(structure, items),
                   "kind": "choice", "field": "",
                   "options": _shape_options(structure)}]
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
            from app.agent.workflow.relevance import evidence_is_relevant
            if not evidence_is_relevant(e):
                continue
            k, why = (e.get("key") or "").strip(), (e.get("why") or e.get("title") or "").strip()
            # 티켓 키 모양만 — PMO 근거에는 "ETL" 같은 모듈명이 섞이는데 그건 참고가 아니다.
            if k and _re.match(r"^[A-Z][A-Z0-9]*-[0-9]+$", k):
                refs.append((k, f"<li>{k} — {why}</li>" if why else f"<li>{k}</li>"))
        for d in (state.get("related_docs") or [])[:3]:
            t, u = (d.get("title") or "").strip(), (d.get("url") or "").strip()
            if t and u:
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
                            (state.get("related_docs") or []) if isinstance(d, dict)}
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
        # 실측: Epic 본문("증분 적재")이 원 요청("starrocks puffin ndv")을 잠식해 전혀
        # 다른 티켓이 만들어졌다. 판정은 코드가, 고치는 판단은 사람이 한다(경고 노출).
        drift = _topic_drift(state, items)
        if drift:
            out["rationale"] = ((out.get("rationale") or "") + "\n" + drift).strip()
            draft["topic_drift"] = True     # Auditor 의 단건 우회(L3b)를 막는 신호

        # ── Epic Link 는 **실재하고 관련 있는 write-project Epic** 이어야 한다 ─────
        # 실측: 사용자가 "기존 에픽 중 맞는 걸로 붙여줘"라고 했는데 모델이 Task(DL-9072)를
        # 에픽이라 답하고 초안에는 아예 안 실었다. 타입 확인은 판단이 아니라 조회다.
        explicit_epic = _explicit_parent_epic(state)
        if explicit_epic and mode != "subtask":
            for it in items:
                if not str(it.get("type") or "").lower().startswith("sub"):
                    it["epic"] = explicit_epic
        for it in items:
            ek = str(it.get("epic") or "").strip()
            if not ek:
                continue
            if not _is_epic(ek):
                it["epic"] = ""
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n({ek} 는 Epic 이 아니라 연결하지 않았다 — "
                                      "Epic 후보를 다시 확인해야 한다)").strip()
                continue
            # 사용자가 직접 지목한 Epic은 의식적인 선택이므로 그대로 둔다. 모델/검색이 추론한
            # Epic만 write project·모듈·주제 적합성을 검증한다. 부적합하면 최상위 Task가
            # 엉뚱한 진척률을 오염시키는 것보다 연결을 비우는 편이 안전하다.
            if ek == explicit_epic:
                continue
            reason = _inferred_epic_rejection(state, it, ek)
            if reason:
                it["epic"] = ""
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n({ek} 연결을 뺐다 — {reason})").strip()
                continue
            em = _epic_module(ek)
            comps = [str(c) for c in (it.get("components") or []) if str(c).strip()]
            if em and comps and em != comps[0]:
                it["epic"] = ""
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n({ek} 연결을 뺐다 — {em} 모듈 Epic과 "
                                      f"{comps[0]} 컴포넌트가 다르다)").strip()

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
        if mode == "task" and len(items) >= need:
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
                head["summary"] = base
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
        if (out.get("mode") or "") == "epic" and items:
            twin = _existing_epic_like(items[0].get("summary") or "")
            if twin:
                qs = (qs or []) + [{
                    "question": f"{twin['key']} \"{twin.get('summary', '')}\" 가 이미 있습니다. "
                                "여기에 Task 로 붙일까요, 그래도 새 Epic 을 만들까요?",
                    "kind": "choice", "field": "epic",
                    "options": [f"{twin['key']} 아래 Task 로 (권장 — 중복 Epic 은 진척 집계를 흐린다)",
                                "새 Epic 을 만든다"]}]
                # draft 는 이 위에서 이미 조립됐고 items 를 **참조로** 공유한다 —
                # 이름을 다시 묶으면(items = []) 초안에는 반영되지 않는다. 비운다.
                items.clear()
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n(Epic 격상 보류 — {twin['key']} 와 이름이 겹친다)").strip()
                structure = "single_task"

        # ── "Epic 은 네가 골라줘" 는 **고르라는 말이지 만들라는 말이 아니다** ──────
        # 실측 STARR1: "Epic 은 네가 골라줘. … 알아서 진행해" 에 모델이 **새 Epic** 을
        # 만들었다(본문도 빈 채로). 위임은 선택을 맡긴 것이지 격상 권한을 준 것이 아닌데,
        # 모델은 "알아서"를 격상 승인으로 읽는다. 새 Epic 은 진척 보고 단위가 하나 더 생기는
        # 일이라 되돌리기가 가장 비싸다 — knowledge/04 의 격상 조건도 보수적으로 적혀 있다.
        # 담을 Epic 이 하나도 없으면 격상을 그대로 둔다(그때는 만드는 것이 맞다).
        if (out.get("mode") or "") == "epic" and items and not qs and _re.search(
                r"(에픽|epic)[^.\n]{0,12}(골라|정해|선택)", conversation(state), _re.I):
            pick = _pick_parent_epic(str(items[0].get("summary") or ""))
            if pick:
                items[0]["type"] = "Task"
                items[0]["epic"] = pick["key"]
                # ★ `draft` 는 이 위에서 이미 조립됐다 — `out` 만 고치면 승인 카드는 여전히
                #   Epic 이다(items 는 참조로 공유돼 항목만 바뀐 채 mode 는 epic). 코드가
                #   만든 값이 소비하는 쪽에 안 닿는 §5-f 의 그 부류라, 두 벌 다 쓴다.
                mode = out["mode"] = draft["mode"] = "task"
                structure = out["structure"] = draft["structure"] = "single_task"
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n(Epic 을 **고르라**고 해서 {pick['key']} "
                                      f"\"{str(pick.get('summary') or '')[:40]}\" 아래 Task 로 뒀다 — "
                                      "새 Epic 은 진척 보고 단위가 하나 더 생기는 일이라 "
                                      "말하지 않았으면 만들지 않는다)").strip()

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
                items[0]["children"] = kept + [c for c in fix
                                               if str(c.get("summary") or "") not in have]
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
            building = any(w in request_text(state) for w in BUILD_WORDS)
            if dod >= 5 or stages >= 3 or building:
                if _said_defaults(state):
                    # 위임받았으면 묻지 않고 **나눠서** 낸다 — 보정 호출 1회로 단계를
                    # children 으로 뽑는다(실측: 위임 케이스에서 단일 Task 뭉개기가 반복).
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
                                            + "\n(다단계 규모라 단계별 Sub-Task 로 나눴다 — "
                                              "위임에 따라 자동. 승인 화면에서 고칠 수 있다)").strip()
                    else:
                        out["rationale"] = ((out.get("rationale") or "")
                                            + "\n(확인 필요: 설계·구현·검증처럼 단계가 나뉘는 "
                                              "규모로 보이는데 단일 Task 다 — Sub-Task 분할 검토)").strip()
                else:
                    qs = [{"question": "작업이 여러 단계(설계·구현·검증 등)로 나뉘는 규모로 "
                                       "보입니다. 어떻게 만들까요?",
                           "kind": "choice", "field": "",
                           "options": ["Task 하나 + 단계별 Sub-Task (권장 — 단계·담당이 나뉜다)",
                                       "단일 Task 로 둔다"]}]

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
                    c["type"] = "Task"
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
            want = modules_in_text(request_text(state))
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
        if mode == "task" and len(items) > 1:
            removed = _dedupe_semantic_items(state, items)
            if removed:
                structure = out["structure"] = draft["structure"] = \
                    ("multiple_tasks" if len(items) > 1 else "single_task")
                out["rationale"] = ((out.get("rationale") or "")
                                    + "\n(같은 산출물의 중복 초안을 합쳤다: "
                                    + ", ".join(removed[:4]) + ")").strip()

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
                and (state.get("intent") or "") != Intent.MODIFY \
                and (state.get("situation") or "").strip() \
                and is_composite(items) and not state.get("structure_ok"):
            if structure_accepted(state) and (state.get("structure_plan") or []):
                pass                      # 사용자가 방금 승인했다 — 이번 턴부터 살을 붙인다
            else:
                struct_stage = True
                for it in items:          # 뼈대 단계에서는 본문을 만들지 않는다
                    it.pop("description", None)
                    for c in (it.get("children") or []):
                        if isinstance(c, dict):
                            c.pop("description", None)
                qs = [structure_question(items)]
                out["rationale"] = ((out.get("rationale") or "")
                                    + "\n(먼저 **구조**를 맞춥니다 — 이 뼈대가 확정되면 각 "
                                      "티켓의 배경·범위·완료 조건을 채웁니다)").strip()

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
            # 모델은 구체적 변경만 받은 경우에도 "사용자 편의성", "운영 효율성", "성능·안정성"
            # 같은 그럴듯한 효과를 배경·범위·DoD에 보탠다. 문장은 자연스럽지만 검증된 사실은
            # 아니다. 원 요청에 없는 품질 차원을 안전한 요청/검증 문장으로 되돌린다.
            _remove_unrequested_quality_claims(state, items)
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
                _dedupe_dod_rows(items)
                _drop_unrequested_deployment_dod(state, items)

        # 우선순위 표기 정규화 — 모델은 "P3" 라고 줄여 쓰고 Jira 는 "P3-Minor" 만 받는다.
        # Auditor 가 반려하면 재작성 왕복 하나가 통째로 날아가고, 한도 소진이면 그 지적이
        # 사용자에게 떠넘겨진다(실측: "P3는 적절한 우선순위가 아닙니다"가 답변에 노출).
        # 판단이 아니라 표기 문제다 — 코드가 정규화한다.
        for it in items:
            p = str(it.get("priority") or "").strip()
            if p:
                it["priority"] = _PRI.get(p.upper(), p)

        # PMO_VIT 는 경영진 보고 현안 전용이고 트리 최상위 하나에만 붙는다 — 그런데 모델이
        # 기존 라벨 목록에서 보고는 신규 티켓 셋에 전부 붙였다(실측). 사용자가 입으로 말했을
        # 때만 남기고, 아니면 기계적으로 뗀다. 규칙 위반 라벨은 검색 노이즈가 된다.
        asked_all = conversation(state)
        if "PMO_VIT" not in asked_all and "현안" not in asked_all:
            for it in items:
                if it.get("labels"):
                    it["labels"] = [x for x in it["labels"] if str(x).upper() != "PMO_VIT"]

        # 변경 계획(modify)은 갈래가 통째로 다르다 — `_change_plan` 이 맡는다.
        plan, qs = _change_plan(state, out, items, qs)
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
        interp_turn = bool(str(out.get("interpretation") or "").strip()
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
            qs = [{"question": "요청하신 내용으로는 만들 수 있는 티켓이 없었습니다. "
                               "어떻게 할까요?",
                       "kind": "choice", "field": "",
                       "options": ["범위를 다시 알려주면 그것으로 다시 잡는다",
                                   "부모/Epic 을 지정해 그 아래로 만든다",
                                   "이번엔 만들지 않는다"]}]

        # 해석 확인 턴의 "제가 이해한 바" — ResultIntegrator 가 질문에 앞세워 보여 준다.
        # 그 외 턴에는 지난 해석이 남지 않게 비운다(오래된 해석은 오해가 된다).
        interp = (str(out.get("interpretation") or "").strip()
                  if not items and not state.get("situation") else "")

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

        # tier와 issue_type을 분리해 downstream/보고서가 Bug를 별도 계층으로 오해하지 않게 한다.
        # 기존 write adapter가 쓰는 `type`은 호환을 위해 함께 유지한다.
        for item in items:
            issue_type = str(item.get("issue_type") or item.get("type") or "Task").strip()
            tier = str(item.get("tier") or "").strip().lower()
            if not tier:
                tier = "epic" if issue_type.lower() == "epic" or mode == "epic" else \
                    ("subtask" if issue_type.lower().replace("-", "").startswith("subtask")
                     or mode == "subtask" else "task")
            item["tier"], item["issue_type"], item["type"] = tier, issue_type, issue_type
            for child in (item.get("children") or []):
                if isinstance(child, dict):
                    child_type = str(child.get("issue_type") or child.get("type") or "Sub-Task")
                    child["tier"], child["issue_type"] = "subtask", child_type

        return {"questions": qs, "draft": draft, "change_plan": plan, "turns": turns,
                "interpretation": interp, **st_out,
                "trace": note(state, self.name,
                              f"변경 계획 {plan.get('key')}" if plan else
                              ("해석 확인 " if interp and not items else "")
                              + (f"질문 {len(qs)}개 · 초안 {len(items)}건" if qs or items else "초안 없음"))}


# 우선순위 표기 정규화 표 — 모델은 "P3" 라고 줄여 쓰고 Jira 는 "P3-Minor" 만 받는다.
_PRI = {"P0": "P0-Blocker", "P1": "P1-Critical", "P2": "P2-Major",
        "P3": "P3-Minor", "P4": "P4-Trivial",
        "BLOCKER": "P0-Blocker", "CRITICAL": "P1-Critical", "MAJOR": "P2-Major",
        "MINOR": "P3-Minor", "TRIVIAL": "P4-Trivial"}


def _change_plan(state, out, items, qs):
    """modify 갈래 — **기존 티켓 변경 계획**을 확정한다. `(plan, qs)` 를 돌려준다.

    초안(items)을 다듬는 일과 기존 티켓을 고치는 일은 재료도 실패 방식도 다르다.
    한 함수에 같이 두었더니 `apply` 가 773줄이 되어 어느 가드가 어느 갈래의 것인지
    읽어서는 알 수 없었다 — 여기 있는 것은 전부 **변경 계획** 쪽 가드다.
    (필드 범위 제한 · 상대 날짜 계산 · 전이 해석 · 링크 조립 · 벌크 대상 확정)
    """
    # modify 갈래 — 변경 계획. 바꿀 값이 하나도 없는 change 는 계획이 아니다.
    change = out.get("change") if isinstance(out.get("change"), dict) else {}
    # ★ 새 일을 만들라고 한 요청에는 변경 계획을 만들지 않는다. 조사에서 비슷한 티켓이
    #   나오면 모델이 그걸 고치겠다고 답하는 일이 있는데(실측), 그러면 사용자가 부탁한
    #   생성은 통째로 사라지고 시키지도 않은 수정이 승인 카드에 오른다.
    if change.get("key") and (state.get("intent") or "") != Intent.MODIFY:
        out["rationale"] = ((out.get("rationale") or "")
                            + f"\n(참고: {change['key']} 가 비슷한 일이지만, 요청은 "
                              "새로 만드는 것이라 변경하지 않았다)").strip()
        change = {}
    plan = {}
    if change.get("key"):
        fields = {k: change[k] for k in ("assignee", "duedate", "priority", "summary",
                                         "labels", "components", "description")
                  if k in change and change[k] is not None}
        # 빈 문자열은 "안 바꿈"이지 변경이 아니다 — 지원하지 않는 필드를 요청받으면
        # (실측: "스토리포인트 5로") 모델이 나머지를 전부 ""로 채워 **빈 변경 카드**가
        # 떴다. 담당 해제("assignee": "")만 예외로 인정한다(사용자가 뗄 때 쓴다).
        _said = request_text(state) + " " + last_user_text(state)
        _wipe = _re.search(r"(담당|assignee)\w*\s*(해제|비워|없애|제거)", _said)
        fields = {k: v for k, v in fields.items()
                  if (isinstance(v, list) and v) or str(v or "").strip()
                  or (k == "assignee" and _wipe)}
        # 말하지 않은 필드는 바꾸지 않는다 — 마감만 미뤄 달라고 했는데 우선순위까지
        # 카드에 얹히면(실측 Round P: priority=P3-Minor) 사용자가 모르고 승인한다.
        _WORDS = {"priority": r"우선순위|priority|P[0-4]|긴급|중요|사소",
                  "duedate": r"마감|기한|due|날짜|미뤄|당겨|연장|늦춰|앞당",
                  "assignee": r"담당|배정|할당|넘겨|맡",
                  "summary": r"제목|이름|타이틀|summary",
                  "labels": r"라벨|label|태그",
                  "description": r"본문|설명|내용|description"}
        _extra = [k for k in list(fields)
                  if k in _WORDS and not _re.search(_WORDS[k], _said, _re.I)] \
            if _said.strip() else []      # 발화가 없으면 근거도 없다 — 지우지 않는다
        for k in _extra:
            fields.pop(k, None)
        if _extra:
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(요청에 없던 {', '.join(_extra)} 변경은 뺐다 — "
                                  "말한 것만 바꾼다)").strip()
        if str(fields.get("priority") or "").strip():
            p = str(fields["priority"]).strip()
            fields["priority"] = _PRI.get(p.upper(), p)
        # 상대 날짜("다음주 수요일")는 **코드가 계산**한다 — 모델 산술이 흔들렸다
        # (실측: 같은 질문에 8-12(수·정답)와 8-16(일·오답)을 번갈아 냈다).
        rel = _relative_due(request_text(state) + " " + last_user_text(state))
        if rel and str(fields.get("duedate") or "") != rel:
            if fields.get("duedate"):
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n(마감을 {rel} 로 계산해 바로잡았다 — 상대 날짜는 "
                                      "코드가 계산한다)").strip()
            fields["duedate"] = rel
        cmt = (change.get("comment") or "").strip()
        # 댓글만 남기는 것도 유효한 계획이다 — "이 내용 DL-x 에 댓글로 남겨줘"가 실사용에 있다.
        if fields or cmt:
            plan = {"key": str(change["key"]).strip(), "changes": fields,
                    "comment": cmt, "why": out.get("rationale") or ""}
            # 바뀌기 **전** 값은 코드가 조회해 싣는다 — 모델이 "변경 전: 미정"이라고
            # 지어냈다(실측 Round P: 실제로는 마감이 있었다).
            try:
                from app.agent import tools as T
                cur = T.BY_NAME["get_ticket"].invoke({"key": plan["key"]}) or {}
                if not cur.get("error"):
                    plan["before"] = {k: (cur.get(k) or "") for k in fields}
                    # ── 말과 방향이 어긋나면 짚는다 ─────────────────────────
                    # 실측: "DL-101 마감을 다음 주 금요일로 **미뤄** 줘" 에 8-27 → 8-14
                    # (오히려 당기는 것)를 아무 말 없이 카드에 올렸다. 사용자가 현재
                    # 마감을 기억하고 말하는 일은 드물다 — 어긋남을 알아채는 건 코드 몫이다.
                    _old = str(plan["before"].get("duedate") or "")
                    _new = str(fields.get("duedate") or "")
                    if _re.match(r"^\d{4}-\d{2}-\d{2}$", _old) and _new and _old != _new:
                        _later = _re.search(r"미뤄|미루|연장|늦춰|늦추|뒤로", _said)
                        _sooner = _re.search(r"당겨|앞당|땡겨|앞으로", _said)
                        _warn = ("앞당기는" if (_later and _new < _old)
                                 else ("미루는" if (_sooner and _new > _old) else ""))
                        if _warn:
                            out["rationale"] = (
                                (out.get("rationale") or "")
                                + f"\n(확인 필요: 현재 마감이 {_old} 라 {_new} 로 바꾸면 "
                                  f"말씀과 반대로 {_warn} 셈이다 — 날짜가 맞는지 봐 달라)"
                            ).strip()
                            plan["why"] = out["rationale"]
            except Exception:
                pass
        # ── 상태 전이 — 이름을 전이 id 로 **코드가** 해석한다(실측: status 필드가 없어
        # '정보 확인 안 됨'으로 죽었다). 못 찾으면 가능한 전이를 choice 로 묻는다.
        k0 = str(change.get("key") or "").strip()
        want = str(change.get("status") or "").strip()
        # 사용자 문장의 상태명이 **1차**다 — 모델이 불가능한 목표('리뷰 대기')를 임의로
        # 다른 상태('Open')로 바꿔치기한 실측. 요청과 다르면 요청 쪽을 쓴다.
        mu_t = _re.search(r"([가-힣A-Za-z ]{2,16}?)\s*(?:상태)?\s*로\s*(?:옮겨|바꿔|전이|이동)",
                          request_text(state) + " " + last_user_text(state))
        if mu_t:
            want = mu_t.group(1).strip()
        if k0 and want and not fields and not plan:
            try:
                from app.agent import tools as T
                cands = [t for t in (T.BY_NAME["list_transitions"].invoke({"key": k0}) or [])
                         if isinstance(t, dict) and not t.get("error")]
                hit = next((t for t in cands
                            if want.lower() in str(t.get("name", "")).lower()
                            or str(t.get("name", "")).lower() in want.lower()
                            or want.lower() in str(t.get("to", "")).lower()), None)
                if hit:
                    plan = {"key": k0, "transition": {"id": str(hit.get("id")),
                                                      "name": hit.get("to") or hit.get("name")},
                            "comment": cmt, "why": out.get("rationale") or ""}
                elif cands:
                    # 보기는 **도착 상태 이름**으로 — 전이 이름("To Resolved")을 그대로
                    # 내밀면 사용자가 읽는 상태명과 어긋난다(실측 T2).
                    opts, seen_o = [], set()
                    for t in cands:
                        nm = str(t.get("to") or t.get("name") or "").strip()
                        nm = _re.sub(r"^(?:To|이동|전이)\s+", "", nm).strip()
                        if nm and nm not in seen_o:
                            seen_o.add(nm)
                            opts.append(nm)
                    qs = [{"question": f"{k0} 를 '{want}' 상태로 옮길 수는 없습니다. "
                                       "지금 갈 수 있는 상태는 다음뿐입니다 — 고르시면 "
                                       "그대로 변경 카드를 만들어 드립니다.",
                           "kind": "choice", "field": "",
                           "options": opts[:5]}]
            except Exception:
                pass
        # ── 티켓 링크 — link_tickets 도구가 실행한다(실측: 링크 요청이 코멘트로 우회됐다).
        lk = change.get("link") if isinstance(change.get("link"), dict) else {}
        if k0 and lk.get("other") and not plan:
            plan = {"key": k0,
                    "link": {"other": str(lk["other"]).strip(),
                             "relation": str(lk.get("relation") or "Relates").strip()},
                    "comment": "", "why": out.get("rationale") or ""}
    # 조건 일괄 수정 — keys 복수. 실재하는 키만 남긴다(조사에서 온 것이지만 한 번 더).
    bulk_keys = [str(k).strip() for k in (change.get("keys") or []) if str(k).strip()]
    # 코드가 확정한 대상(bulk_targets)이 있는데 모델이 keys 를 빠뜨리거나 일부만 담았으면
    # **전부로 강제한다** — 일부 누락은 조용한 미수정이다(실측: 대상 없음 오답 2회).
    # ★ **코멘트만 남기는 일괄도 있다.** 여기 조건이 '바꿀 필드가 있는가'만 봐서, "티켓
    #   전부에 담당자를 멘션해 상태 점검을 요청" 같은 요청이 통째로 일괄 경로를 못 탔다
    #   (실측 CMTB1: 대상이 안 잡히자 모델이 아무 티켓 둘을 골라 이야기했다).
    #   코멘트는 필드가 아니지만 **N건에 쓰는 일**이라는 점은 같다.
    if state.get("bulk_targets") and (change.get("assignee") is not None
                                      or change.get("duedate") is not None
                                      or change.get("priority") is not None
                                      or change.get("labels") is not None
                                      or str(change.get("comment") or "").strip()
                                      or bulk_keys):
        bulk_keys = [str(k) for k in state["bulk_targets"]]
    if bulk_keys and not plan:
        fields = {k: change[k] for k in ("assignee", "duedate", "priority", "labels",
                                         "components")
                  if k in change and change[k] is not None}
        if str(fields.get("priority") or "").strip():
            p = str(fields["priority"]).strip()
            fields["priority"] = _PRI.get(p.upper(), p)
        # 빈 문자열 값은 변경이 아니다 — 모델이 안 바꿀 필드를 "" 로 채워 빈 changes
        # 일괄 카드가 떴다(실측). 해제(비우기)는 단건 change 로만 받는다.
        fields = {k: v for k, v in fields.items()
                  if (isinstance(v, list) and v) or str(v or "").strip()}
        real = [k for k in dict.fromkeys(bulk_keys) if _ticket_exists(k)][:30]
        gone = [k for k in bulk_keys if k not in real]
        if gone:
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(실재하지 않아 제외: {', '.join(gone[:5])})").strip()
        # 필드가 없어도 **코멘트가 있으면** 일괄이다(위 주석 참조).
        if real and (fields or str(change.get("comment") or "").strip()):
            if len(real) == 1:
                # 단건이면 단건 카드다 — 일괄 카드는 대상이 여럿일 때만.
                plan = {"key": real[0], "changes": fields,
                        "comment": (change.get("comment") or "").strip(),
                        "why": out.get("rationale") or ""}
            else:
                plan = {"keys": real, "changes": fields,
                        "comment": (change.get("comment") or "").strip(),
                        "why": out.get("rationale") or ""}
                # ★ **티켓별 코멘트를 코드가 미리 조립한다**(사용자 요청).
                #   "담당자를 멘션해서 상태 점검을 요청" 같은 일괄 코멘트는 **티켓마다 문구가
                #   다르다** — 멘션 대상이 그 티켓의 담당자이기 때문이다. 한 문장을 N건에
                #   그대로 붙이면 멘션이 틀리거나 통째로 빠진다. 승인 화면이 "무엇이 어디에
                #   달리는가"를 티켓별로 보여 줘야 승인이 의미를 갖는다.
                pv = _bulk_comment_preview(real, plan["comment"])
                if pv:
                    plan["comments"] = pv
    # ── 전이 최종 보장: "DL-x 를 <상태>로 옮겨/바꿔" 인데 모델이 change.status 를
    # 안 쓰고 엉뚱한 초안을 냈다(실측: '상태로 옮김' Task 를 새로 만듦) — 코드가
    # 요청에서 상태명을 뽑아 전이를 조립하고 초안을 버린다.
    if not plan and (state.get("intent") or "") == Intent.MODIFY \
            and (state.get("mentioned_keys") or []):
        req_t = request_text(state) + " " + last_user_text(state)
        mt = _re.search(r"([가-힣A-Za-z ]{2,16}?)\s*(?:상태)?\s*로\s*(?:옮겨|바꿔|전이|이동)",
                        req_t)
        if mt:
            want_t = mt.group(1).strip()
            k_t = str(state["mentioned_keys"][0]).strip()
            try:
                from app.agent import tools as T
                cands_t = [t for t in
                           (T.BY_NAME["list_transitions"].invoke({"key": k_t}) or [])
                           if isinstance(t, dict) and not t.get("error")]
                hit_t = next((t for t in cands_t
                              if want_t.lower() in str(t.get("name", "")).lower()
                              or want_t.lower() in str(t.get("to", "")).lower()), None)
                if hit_t:
                    plan = {"key": k_t,
                            "transition": {"id": str(hit_t.get("id")),
                                           "name": hit_t.get("to") or hit_t.get("name")},
                            "comment": "",
                            "why": ((out.get("rationale") or "")
                                    + "\n(상태 전이 — 전이 id 는 코드가 확정)").strip()}
                    qs = []
                    items.clear()
                elif cands_t:
                    # 모델이 낸 잡질문("제목을 알려주실…")은 버린다 — 정확한 choice 하나가 답이다.
                    qs = [{"question": f"{k_t} 를 '{want_t}' 로 옮길 전이가 없습니다. "
                                       "가능한 전이 중에서 골라 주세요.",
                           "kind": "choice", "field": "",
                           "options": [str(t.get("to") or t.get("name"))
                                       for t in cands_t][:5]}]
                    items.clear()
            except Exception:
                pass

    # ── 링크 최종 보장: "A 가 B 를 막는 관계로 연결" — 키 둘 + 관계 낱말이면 조립.
    # (실측: 모델이 change.link 대신 무의미한 확인 질문 4개를 냈다.)
    if not plan and (state.get("intent") or "") == Intent.MODIFY:
        req_l = request_text(state) + " " + last_user_text(state)
        keys_l = _re.findall(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b", req_l)
        if len(dict.fromkeys(keys_l)) >= 2 and _re.search(r"연결|링크|link", req_l):
            a, b = list(dict.fromkeys(keys_l))[:2]
            rel = "Blocks" if _re.search(r"막|block", req_l, _re.I) else "Relates"
            if _ticket_exists(a) and _ticket_exists(b):
                plan = {"key": a, "link": {"other": b, "relation": rel},
                        "comment": "",
                        "why": ((out.get("rationale") or "")
                                + f"\n(링크 {rel}: {a} → {b} — 요청에서 코드가 확정)").strip()}
                qs = []
                items.clear()

    # ── 최종 보장: 대상(JQL)과 변경 필드(요청 파싱)가 둘 다 확정되면 **코드가 계획을
    # 조립**한다 — 모델이 Epic 질문으로 새는 것을 두 번의 프롬프트 교정으로도 못 막았다.
    if not plan and state.get("bulk_targets") \
            and (state.get("intent") or "") == Intent.MODIFY:
        req = request_text(state)
        fields = {}
        # \b 는 한글 앞에서 안 선다("P1으로") — ASCII 경계만 본다.
        mp = _re.search(r"(?<![0-9A-Za-z])P([0-4])(?![0-9A-Za-z])", req)
        if mp and ("우선순위" in req or "올려" in req or "내려" in req or "로 바꿔" in req):
            fields["priority"] = _PRI["P" + mp.group(1)]
        rel = _relative_due(req)
        if rel and "마감" in req:
            fields["duedate"] = rel
        mu = _re.search(r"(?<![0-9A-Za-z.])(?:skcc\.)?([a-z]{1,2}\d{2,6})(?![0-9A-Za-z])", req)
        if mu and ("담당" in req or "에게" in req):
            fields["assignee"] = f"skcc.{mu.group(1)}"
        if fields:
            plan = {"keys": [str(k) for k in state["bulk_targets"]], "changes": fields,
                    "comment": "",
                    "why": ((out.get("rationale") or "")
                            + "\n(조건 일괄 수정 — 대상은 JQL 로, 변경 값은 요청에서 "
                              "코드가 확정했다)").strip()}
            qs = []
            items.clear()          # 수정 요청에 초안을 만들었어도 계획이 이긴다(참조 공유)

    # ── Done field update 금지 ──────────────────────────────────────────────
    # Jira editmeta가 우연히 비어 있는 데 기대지 않는다. 완료 티켓은 comment와 현재 Jira가
    # 제공한 transition은 가능하지만 field update는 불가능하다. Reopened와 field update를
    # 한 approval에 묶으면 실행 전에는 여전히 Done이므로 유효하지 않다 — 반드시 두 단계다.
    if plan and plan.get("changes"):
        planned_keys = ([str(k) for k in (plan.get("keys") or [])]
                        if plan.get("keys") else [str(plan.get("key") or "")])
        done_keys = []
        try:
            from app.agent.tools._ctx import client as _ticket_client
            from app.domain.ticket_actions import is_done, reopen_transition
            done_keys = [k for k in planned_keys
                         if k and is_done(_ticket_client().ticket_badge(k))]
        except Exception:
            done_keys = []
        if done_keys:
            reopen = None
            if len(done_keys) == 1:
                try:
                    from app.agent import tools as T
                    reopen = reopen_transition(
                        T.BY_NAME["list_transitions"].invoke({"key": done_keys[0]}) or [])
                except Exception:
                    reopen = None
            if reopen:
                opts = [f"{done_keys[0]}를 {reopen.get('to') or 'Reopened'}로 전이한다 "
                        "(권장 — 전이 후 속성 변경은 새 승인)"]
                if str(plan.get("comment") or "").strip():
                    opts.append("속성은 바꾸지 않고 요청한 댓글만 남긴다")
                opts.append("취소한다")
                qs = [{"question": f"{done_keys[0]}는 이미 Done이라 속성을 바꿀 수 없습니다. "
                                   "먼저 다시 연 뒤 새 승인으로 속성을 변경해야 합니다.",
                       "kind": "choice", "field": "", "options": opts[:3]}]
            else:
                keys_text = ", ".join(done_keys[:8])
                qs = [{"question": f"{keys_text}는 이미 Done이라 속성을 바꿀 수 없습니다. "
                                   "현재 Jira가 제공하는 Reopened 전이를 먼저 실행한 뒤 "
                                   "새 승인으로 다시 요청해 주세요.",
                       "kind": "choice", "field": "", "options": ["취소한다"]}]
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(Done 티켓의 field update를 차단했다 — comment는 가능하고, "
                                  "Reopened 전이와 속성 변경은 별도 승인이다)").strip()
            plan = {}
            items.clear()

    # 담당 변경의 사번은 **초안 단계에서 실재 검증** — 미실재면 카드 대신 정확한 안내
    # (실측: 없는 사번에 '이메일 주소를 알려달라'는 엉뚱한 질문이 나갔다).
    _asg = (plan.get("changes") or {}).get("assignee") if plan else None
    if _asg:
        try:
            from app.agent.tools._ctx import client as _c2, settings as _s2
            from app.domain.search import search_users as _su
            found = _su(_c2(), _s2(), _asg, 5) or []
            if not any(str(u.get("id") or "") == _asg for u in found):
                plan = {}
                qs = [{"question": f"'{_asg}' 는 존재하지 않는 사번입니다. 올바른 사번을 "
                                   "알려 주세요 (skcc.x1042 형식 — 자동완성이 붙습니다).",
                       "kind": "text", "options": [], "field": "assignee"}]
        except Exception:
            pass

    # 삭제 요청 — 지원되지 않는다. 모델이 빈 변경+코멘트 카드를 만들던 것(실측)을 코드가
    # 막는다: 카드 없이 사유·대안만 답하게 한다.
    if plan and not plan.get("changes") \
            and _re.search(r"삭제|지워\s*줘|없애",
                           request_text(state) + " " + last_user_text(state)):
        plan = {}
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(삭제는 지원되지 않는다 — 상태 전이(닫음)나 보관 라벨을 "
                              "대안으로 안내)").strip()
    # 에이전트가 바꿀 수 없는 필드 — 빈 카드 대신 무엇을 못 하는지 말한다.
    # (update_ticket 은 담당/마감/우선순위/제목/라벨/컴포넌트/본문만 다룬다.
    #  스토리포인트는 티켓 화면에서 직접, 그것도 Story 에만 설정된다 — 도메인 제약.)
    if not plan and not items and _re.search(
            r"스토리\s*포인트|story\s*point|\bSP\b",
            request_text(state) + " " + last_user_text(state), _re.I):
        out["rationale"] = ((out.get("rationale") or "")
                            + "\n(스토리포인트는 에이전트가 바꾸지 못한다 — 티켓 화면에서 "
                              "직접 입력해야 하고, 애초에 Story 타입에만 설정된다. "
                              "바꿀 수 있는 것: 담당·마감·우선순위·제목·라벨·컴포넌트·본문)"
                            ).strip()
    # 초안 수정 요청인데 기존 티켓 변경 계획을 냈다(실측: DL-109 로 샜다 — 사용자는
    # 그 키를 입에 올린 적이 없다) — 버린다. 판정은 **사용자 발화에 그 키가 있는가**로
    # 한다(mentioned_keys 는 모델·이월로 오염될 수 있다).
    if plan and plan.get("key") \
            and ((state.get("draft") or {}).get("items")) and not items:
        said_by_user = " ".join(str(getattr(m, "content", "") or "")
                                for m in (state.get("messages") or [])
                                if getattr(m, "type", "") == "human")
        if plan["key"] not in said_by_user:
            plan = {}
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(승인 대기 초안에 대한 수정 요청 — 기존 티켓 변경이 "
                                  "아니라 초안을 고쳐야 한다)").strip()

    # ── ★ **묻지 않은 것에 대한 안내를 근거 줄에 싣지 않는다** ────────────────
    # 승인 카드의 근거 줄은 사용자가 **판단하는 자리**다. 무관한 문장이 있으면 무엇을
    # 근거로 승인하는지가 흐려진다. 실측(CMTB1): 일괄 코멘트 계획의 why 가
    # "삭제는 지원되지 않음. 상태를 닫음으로 전이하거나…" 였다 — 삭제 요청이 아니었다.
    # 프롬프트에 적힌 예외 안내(삭제·스토리포인트)를 모델이 아무 데나 옮겨 적는다.
    said_all = request_text(state) + " " + conversation(state)
    for pat, need in ((r"삭제[^)\n]{0,20}지원되지\s*않", ("삭제", "지워", "없애")),
                      (r"스토리\s*포인트[^)\n]{0,20}(?:설정할 수 없|지원)", ("포인트", "SP"))):
        if _re.search(pat, str(out.get("rationale") or "")) \
                and not any(w in said_all for w in need):
            out["rationale"] = _re.sub(r"\n?\([^)\n]*" + pat + r"[^)\n]*\)", "",
                                       out["rationale"]).strip()
            if isinstance(plan, dict) and plan.get("why"):
                plan["why"] = _re.sub(pat + r"[^\n]*", "", str(plan["why"])).strip(" .·\n")
    return plan, qs

def draft_text(draft: dict) -> str:
    """초안을 프롬프트/화면에 실을 수 있는 글로. PeopleAdvisor 가 이걸 보고 배정한다.

    **자식(Sub-Task)도 번호와 함께 보여 준다.** 안 보여 줬더니 PeopleAdvisor 는 부모 담당만
    정했고, 자식은 WorkArchitect 가 모듈 명단을 순번으로 돌려 채웠다 — 그래서 PeopleAdvisor 가
    "부하가 높아 부적합"이라 적은 사람이 자식 담당으로 들어갔다(실측).
    """
    if not draft or not draft.get("items"):
        return ""
    rows = []
    for i, it in enumerate(draft.get("items") or []):
        bits = [f"[{i}] {it.get('type','')} — {it.get('summary','')}"]
        for k, label in (("epic", "상위"), ("parent", "부모"), ("components", "모듈"),
                         ("labels", "라벨"), ("duedate", "마감"), ("priority", "우선순위")):
            v = it.get(k)
            if v:
                bits.append(f"{label}={v if not isinstance(v, list) else ', '.join(map(str, v))}")
        if it.get("description"):
            bits.append(f"\n    설명: {str(it['description'])[:150]}")
        rows.append("  ".join(bits))
        for j, c in enumerate(it.get("children") or []):
            if isinstance(c, dict):
                rows.append(f"    └ 하위[{j}] {c.get('summary', '')}"
                            + (f" (현재 담당 {c['assignee']} — 코드가 모듈 명단으로 임시로 "
                               "채운 값이다. 부하를 보고 고쳐라)" if c.get("assignee") else ""))
    return f"mode={draft.get('mode')}\n" + "\n".join(rows)


def _slot_audit(state) -> str:
    """티켓 생성 **최소 요건 슬롯**을 코드가 점검한다 — 채워진 것/빈 것/빈 것의 처리 방침.

    모델이 매번 다르게 가리던 것을 표로 굳힌다(knowledge/07 §최소 요건과 같은 룰).
    해석 확인 턴에는 '무엇을 물을지'의 근거가 되고, 초안 턴에는 '무엇을 기본값으로
    채웠는지'의 근거가 된다."""
    req, conv = request_text(state), conversation(state)
    text = (req + " " + conv).strip()
    low = text.lower()
    shape, _w = shape_hint(state)
    comps = _known_components()
    # ★ **부분 일치로 모듈을 정하지 않는다.** 컴포넌트 이름이 흔한 낱말이면 아무 문장에나
    #   걸린다(실측: "feasibility test" 의 'test'). 낱말 경계로 끊어 본다.
    module = next((c for c in comps
                   if _re.search(rf"(?<![0-9a-z]){_re.escape(c.lower())}(?![0-9a-z])", low)), "")
    rows = []

    def row(name, filled, how, empty_act):
        rows.append(f"- {name}: " + (f"채워짐({how})" if filled else f"비어 있음 → {empty_act}"))

    row("주제·산출물", bool(req.strip()), "원문 요청", "ASK — 무엇을 만들지부터")
    row("범위(1차 목표)", any(w in text for w in ("까지만", "범위", "1차", "PoC", "포함", "제외",
                                             "검토만", "최소 기능", "전체")),
        "사용자 언급", "ASK — 검토만/PoC/최소 구현 중 choice")
    row("모듈(컴포넌트)", bool(module), f"'{module}'", "INFER — 조사·제목 접두로 추론, 갈리면 ASK")
    row("Epic 배치", bool(_re.search(r"\b[A-Z][A-Z0-9]*-\d+\b", text)) or "에픽" in text
        or "최상위" in text, "사용자 언급",
        "ASK(choice, field=epic) — 후보 + 없음(최상위) + 새 Epic 필요")
    row("형태(구조)", bool(shape), f"'{shape}'", "INFER — 규모 신호로 판단, 갈림 크면 확인 질문")
    row("마감", bool(_re.search(r"\d{4}-\d{2}-\d{2}|다음\s*주|이번\s*주|말까지|주까지|일까지", text)),
        "사용자 언급", "ASK(date) — 단 위임이면 비워 둔다")
    row("우선순위", bool(_re.search(r"P[0-4]|긴급|우선순위", text)), "사용자 언급",
        "INFER — 기본 P3-Minor, 묻지 않는다")
    row("담당자", False, "", "LATER — 다음 단계(PeopleAdvisor)가 근거와 함께 정한다, 묻지 않는다")

    # ── ★ 여기부터는 **티켓의 질**을 정하는 슬롯이다(사용자 요청으로 신설) ──────────
    # 위 슬롯들은 티켓을 **어디에 놓을지**(모듈·Epic·마감)를 정한다. 그런데 승인하는 사람이
    # 읽는 것은 배치가 아니라 **배경·완료 조건**이고, 나중에 "이거 왜 만들었지"·"이거 끝난
    # 거 맞나"가 갈리는 자리도 거기다. 이 셋이 비면 코드가 채울 수 있는 것은 형식뿐이라
    # (배경은 원 요청을 옮기고, DoD 는 모델이 지어낸다) 결국 **물어야 좋아진다**.
    # ★ **이미 물었고 사용자가 답했으면 채워진 것이다.** 판정 낱말만 보면 답을 놓친다 —
    #   실측(사용자 관점 리뷰 F1): "배경은 StarRocks QueryQueueV2 Estimation 성능 개선"
    #   이라고 답했는데 그 문장에 판정 낱말("때문"·"위해"…)이 하나도 없어 **또 물었다**.
    #   슬롯 이름 자체가 대화에 나왔다는 것은 그 질문이 오갔다는 뜻이다.
    def _answered(*names):
        return any(n in conv for n in names)

    row("배경(왜 지금 필요한가)",
        _answered("배경") or
        any(w in text for w in ("때문", "위해", "요청이", "VoC", "장애", "이슈", "불편",
                                "느려", "실패", "필요해서", "라서", "니까", "목표",
                                "개선", "성능", "부하", "요구")),
        "사용자 언급",
        "ASK — 계기를 한 줄로. 없으면 배경이 원 요청 복사가 된다(승인자가 판단할 수 없다)")
    row("완료 조건(무엇을 보고 끝났다고 하나)",
        _answered("완료 조건", "DoD") or
        any(w in text for w in ("완료 조건", "DoD", "끝났다고", "판정", "기준은", "확인되면",
                                "까지 되면", "성공하면", "리포트", "지표", "구현", "적용")),
        "사용자 언급",
        "ASK — '무엇을 보고' 끝인지. 없으면 '테스트 완료' 같은 판정 불가 문장이 남는다")
    row("분할 여부(한 사람이 며칠에 끝나나)",
        bool(shape) or any(w in text for w in ("나눠", "쪼개", "단계", "며칠", "주 정도",
                                               "혼자", "같이", "분담")),
        "사용자 언급 또는 형태 지정",
        "ASK(choice) — 한 티켓 / 단계별 Sub-Task / 담당 나눠 여러 건")
    return "\n".join(rows)


def _apply_named_assignees(state, items: list) -> None:
    """"성능 측정은 x1402, 가이드 작성은 x1450" 식의 **입으로 지정한 담당**을 초안에 강제한다.

    패턴: <작업 문구>(은|는) <사번>. 문구의 핵심 낱말이 제목에 들어 있는 항목(자식 포함)의
    빈 assignee 를 채운다 — 모델이 이미 적은 값은 존중한다(덮지 않는다)."""
    text = conversation(state) or request_text(state)
    rows = list(items) + [c for i in items for c in (i.get("children") or [])
                          if isinstance(c, dict)]
    if not text or not rows:
        return
    for m in _re.finditer(r"([가-힣A-Za-z0-9·/ ]{2,24}?)\s*(?:은|는)\s*(?:skcc\.)?"
                          r"([a-z]{1,2}\d{2,6})\b", text):
        phrase, uid = m.group(1).strip(), f"skcc.{m.group(2)}"
        words = [w for w in _re.split(r"\s+", phrase) if len(w) >= 2][-2:]
        if not words:
            continue
        for r in rows:
            s = str(r.get("summary") or "")
            if all(w in s for w in words):
                # 문구가 맞으면 **덮어쓴다** — 사용자의 명시 지정이 모델 배정보다 우선이다
                # (실측: 모델이 세 항목 전부 한 사람으로 배정해 지정을 뭉갰다).
                # 표식을 남겨 PeopleAdvisor 의 merge 가 다시 덮지 못하게 한다(2차 뭉갬 실측).
                r["assignee"] = uid
                r["assignee_source"] = "user"


def _fill_owners(item: dict, kids: list) -> None:
    """빈 자식 담당을 모듈 로스터로 돌려 채운다 — 자식 담당 채움 가드보다 늦게 만들어진
    (보정 호출) children 용."""
    fb = str(item.get("assignee") or "").strip()
    try:
        pool = [u for u in _module_pool(item, fb) if u]
    except Exception:
        pool = []
    if not pool:
        return
    for n, c in enumerate(kids):
        if not str(c.get("assignee") or "").strip():
            c["assignee"] = pool[n % len(pool)]


def _volume_partition_children(state, item: dict) -> list:
    """`N개를 사람 나눠서` 요청을 실제 roster 수만큼의 분량 묶음으로 나눈다."""
    said = last_user_text(state)
    if not any(w in said for w in ("사람 나눠", "담당 나눠", "나눠 맡", "나눠서 진행")):
        return []
    match = _re.search(r"(?P<count>[2-9][0-9]{0,3})\s*(?P<unit>개|건)", said)
    if not match:
        return []
    total = int(match.group("count"))
    unit = match.group("unit")
    fallback = str(item.get("assignee") or "").strip()
    pool = [u for u in _module_pool(item, fallback) if u]
    groups = min(total, min(5, max(2, len(pool))))
    quotient, remainder = divmod(total, groups)
    raw_summary = str(item.get("summary") or "").strip()
    prefix_match = _re.match(r"^\s*(\[[^]]+\])", raw_summary)
    prefix = prefix_match.group(1) if prefix_match else ""
    subject = _re.sub(r"^\s*\[[^]]+\]\s*", "", raw_summary)
    # 모델이 임의로 `10개씩 분담`을 제목에 넣어도 실제 roster가 2명이면 15개씩이다.
    # 계산 전 숫자는 제거하고 자식 제목의 계산된 분량만 source-of-truth로 둔다.
    subject = _re.sub(r"\s*[-–—]?\s*\d*\s*개씩\s*(?:분담|배분|처리|등록)?\s*$", "",
                      subject).strip(" -–—")
    base = _base_title(subject).strip() or "요청 대상 처리"
    item["summary"] = f"{prefix} {base}".strip()
    safe_base = _esc(base)
    item["description"] = (
        f"<h3>배경</h3><p>{safe_base} 대상 {total}{unit}를 여러 담당자가 중복 없이 "
        "나누어 처리해야 한다.</p>"
        f"<h3>작업 범위</h3><ul><li>포함: 대상 {total}{unit} 목록을 담당 묶음별로 "
        "확정하고 메타데이터를 등록한다.</li>"
        "<li>제외: 원본 테이블의 스키마·데이터 내용 변경</li></ul>"
        "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
        f"<li data-checked=\"false\">처리 건수 합계가 {total}{unit}이고 중복·누락이 "
        "없음을 대상 목록과 대조해 확인한다.</li>"
        "<li data-checked=\"false\">등록 실패·보류 대상과 사유를 parent ticket에 "
        "기록한다.</li></ul>")
    children = []
    for idx in range(groups):
        size = quotient + (1 if idx < remainder else 0)
        label = f"담당 묶음 {idx + 1}/{groups}"
        child = {
            "summary": f"{base} — {label} ({size}{unit})",
            "description": (
                f"<h3>작업 범위</h3><ul><li>parent의 확정 대상 목록 중 {_esc(label)}에 "
                f"배정된 {size}{unit}를 처리한다.</li></ul>"
                "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                f"<li data-checked=\"false\">배정된 {size}{unit}의 등록 결과와 실패·보류 "
                "목록을 기록한다.</li></ul>")}
        if pool:
            child["assignee"] = pool[idx % len(pool)]
        children.append(child)
    return children


def _split_into_children(state, item: dict) -> list:
    """단일 Task 로 뭉개진 다단계 초안을 **실행 단위 Sub-Task 로 나누는 보정 호출 1회**.

    위임("알아서") 케이스 전용 — 물을 수 없으니 나눠서 내고, 승인 카드에서 사람이 고친다.
    실패하면 빈 리스트(경고 경로로 폴백) — 보정이 본 흐름을 죽이면 안 된다."""
    # 같은 대상을 N개 처리하고 "사람 나눠서"라고 한 것은 기능 단계가 아니라 **분량
    # 분할**이다. LLM에 맡기면 설계/구현/검증으로 바꾸거나 단일 Task로 뭉개졌고, 그때마다
    # 호출도 하나 더 들었다(STR1). 대상 이름은 지어내지 않고, 요청에 있는 총량과 실제
    # module roster 크기만으로 담당 묶음을 만든다. 어떤 테이블이 어느 묶음인지는 부모의
    # 확정 목록/승인 화면에서 정한다 — 존재하지 않는 table name을 만드는 것보다 안전하다.
    volume = _volume_partition_children(state, item)
    if volume:
        return volume
    try:
        schema = {"title": "split_children", "type": "object", "properties": {
            "children": {"type": "array", "items": {
                "type": "object", "properties": {
                    "summary": {"type": "string",
                                "description": "Korean execution-unit summary naming the specific target and outcome; do not copy the parent."}},
                "required": ["summary"]}}},
            "required": ["children"]}
        r = invoke_schema(schema, [
            ("system", "You are a PMO ticket architect. Split multi-stage work into Korean Sub-Task "
                       "execution units that one owner can finish within several days. Return JSON only."),
            ("user", f"Original request: {request_text(state)}\n\n"
                     f"Task summary: {item.get('summary')}\n"
                     f"Body data: {str(item.get('description') or '')[:1200]}\n\n"
                     "Split this Task into two to five Sub-Tasks. Every Korean summary must identify the "
                     "specific work target and outcome. A stage name alone, such as `설계 단계`, `구현 단계`, "
                     "or `검증 단계`, is invalid. Good examples preserve the target: `Puffin NDV 통계 스키마 "
                     "설계`, `통계 생성 배치 Job 구현`, `StarRocks 플랜 반영 검증`.")],
            tier="simple", temperature=0.1, name="split_children")
        kids = [{"summary": str(c.get("summary") or "").strip()}
                for c in (r or {}).get("children") or []
                if str(c.get("summary") or "").strip()]
        # ★ **"설계 단계"는 제목이 아니다** — 어느 일에나 붙는 이름이라 티켓을 열기 전에는
        #   무슨 일인지 알 수 없다(사용자 지적). 프롬프트에 예시까지 줬는데도 나온다.
        #   하나라도 이 꼴이면 이 분할을 **버리고** DoD 기반 분할로 떨어진다 — 거기 제목은
        #   본문에서 온 것이라 구체적이다.
        if len(kids) >= 2 and not any(_generic_title(k["summary"]) for k in kids):
            return kids[:5]
    except Exception:
        pass
    # ★ 보정 호출이 빈손이면 **DoD 에서 코드가 뽑는다.** 이 호출은 LLM 한 방이라 레이트리밋·
    #   흔들림으로 그냥 실패하는데, 그러면 다단계 규모가 조용히 단일 Task 로 남았다
    #   (실측 STARR1: 같은 케이스가 실행마다 통과/실패로 뒤집혔다).
    #   knowledge/07 이 이미 규정한다 — "DoD 가 5개를 넘고 서로 다른 단계라면 그건 DoD 가
    #   아니라 **Sub-Task 목록**이다". 규정이 있으니 코드가 그대로 집행한다.
    fallback = _children_from_dod(item)
    if fallback:
        return fallback
    # 사용자가 **새 일의 단계별 Sub-Task 형태를 명시**한 경우에는 빈 리스트로 돌아가면
    # 안 된다. 보정 LLM이 일반적인 단계명만 내어 필터에 걸리고 DoD도 두 줄뿐이면 두
    # 폴백이 모두 빈손이 될 수 있다. 구조 판단은 사용자가 이미 했으므로 빈 산출을 허용하지 않는다.
    # 코드가 부모 제목의 대상을 보존한 최소 3단계를 만든다.
    if shape_hint(state)[0] == "task_with_subtasks":
        base = _base_title(str(item.get("summary") or "")).strip()
        if base:
            return [{"summary": f"{base} {stage}"} for stage in ("설계", "구현", "검증")]
    return []


def _task_grade_body(body) -> bool:
    """최상위 Task 본문의 최소선 — 배경·작업 범위(제외 포함)·완료 조건이 다 있나.

    `tools/agent_create_suite.py` 의 본문 게이트와 **같은 규율**을 코드 쪽에서 본다.
    검사만 있고 고칠 자리가 없으면 배터리에서만 잡히고 실사용에서는 그대로 나간다.
    """
    b = str(body or "")
    return (len(b) >= 80 and all(s in b for s in ("배경", "작업 범위", "완료"))
            and bool(_re.search(r"제외|하지\s*않", b)))


def _is_bug_item(it) -> bool:
    return str((it or {}).get("type") or "").strip().lower() == "bug"


def _bug_grade_body(body) -> bool:
    """Bug 본문의 최소선 — **재현 경로·기대 동작·실제 동작**이 다 있나.

    Task 와 규율이 다르다. 버그 티켓에 배경·작업 범위·DoD 를 적어 봐야 잡는 사람에게
    쓸모가 없다 — 필요한 것은 "어떻게 하면 재현되고, 무엇이 나와야 하는데, 무엇이
    나오는가" 셋이다.
    """
    b = str(body or "")
    return len(b) >= 60 and all(s in b for s in ("재현", "기대", "실제"))


_ASK_REPORTER = "확인 필요 — 신고자에게 물을 것"


def _report_sentences(text: str) -> list[str]:
    """붙여넣기 wrapper를 빼고 신고자가 실제로 쓴 문장만 보수적으로 나눈다."""
    raw = str(text or "")
    if "---" in raw:
        raw = raw.split("---", 1)[1]
    rows = []
    for part in _re.split(r"(?<=[.!?])\s+|[\r\n]+", raw):
        row = _re.sub(r"^\[[0-9: ]+\]\s*[^:]{1,20}:\s*", "", part).strip(" -\t")
        if row and not _re.search(r"(?:티켓|작업)(?:으로)?\s*(?:만들|등록)|알아서", row):
            rows.append(row)
    return rows


def _reported_symptom(text: str) -> str:
    """원문에 명시된 실제 증상 한 문장. 없으면 빈 문자열 — 추측하지 않는다."""
    bad = _re.compile(r"안\s*(?:보|되|떠|열|나오)|보이지\s*않|되지\s*않|실패|오류|"
                      r"에러|타임아웃|빈(?:다|다\b|화면)|깨(?:진|짐)|곤란")
    return next((s for s in _report_sentences(text) if bad.search(s)), "")


def _reported_expectation(text: str) -> str:
    """신고자가 직접 말한 희망/기대 문장만 반환한다."""
    want = _re.compile(r"좋겠|원(?:합니다|해요|한다)|기대|해야\s*한다|바로\s*(?:보|확인)")
    return next((s for s in _report_sentences(text) if want.search(s)), "")


def _reported_steps(text: str, symptom: str) -> list[str]:
    """원문에 화면과 표시 대상이 모두 있을 때만 한 단계 재현 경로를 구성한다."""
    joined = " ".join(_report_sentences(text))
    places = _re.findall(
        r"([가-힣A-Za-z0-9]+(?:\s+[가-힣A-Za-z0-9]+){0,2}\s+"
        r"(?:화면|페이지|탭|메뉴|편집기|뷰어))(?:에서|에서는)", joined)
    subjects = _re.findall(
        r"([가-힣A-Za-z0-9_.-]+(?:\s+[가-힣A-Za-z0-9_.-]+){0,2})(?:이|가)\s*"
        r"(?:안\s*보|보이지\s*않|안\s*나오|나오지\s*않)", symptom)
    if not places or not subjects:
        return []
    place = places[-1].strip()
    subject = subjects[-1].strip()
    if "때 " in subject:
        subject = subject.split("때 ", 1)[1].strip()
    return [f"{place}에서 {subject} 표시 여부를 확인한다."]


def _looks_like_report_wrapper(text: str) -> bool:
    value = str(text or "")
    return bool("---" in value or len(value) > 300
                or _re.search(r"(?:그대로\s*)?(?:티켓|작업)(?:으로)?\s*(?:만들|등록)|알아서",
                              value))


def _bug_body_for(state, it) -> str:
    """Bug 본문을 **조각으로 받아 코드가 조립한다** — Task 쪽과 같은 방식.

    실측(사용자 관점 리뷰 F5, blocker): 사용자가 "크롬에서 재현되고 기대는 그래프가
    그려지는 것"까지 줬는데 본문은 **배경·작업 범위·DoD** 로 나갔다. 재현 경로가 통째로
    사라진 Bug 티켓은 아무도 못 잡는다.

    원인은 판단이 아니라 **배선**이었다. 지시문은 갈래를 나눠 "재현/기대/실제를 적어라"고
    했는데, 본문이 얇을 때 다시 쓰는 `_fill_thin_bodies` 는 **Task 템플릿밖에 몰랐다** —
    모델이 옳게 써 놔도 코드가 Task 모양으로 덮어썼다.
    (이 저장소가 반복해서 배운 것: **판단이 갈리면 보장도 같이 갈려야 한다.**)

    사용자가 안 준 칸은 지어내지 않고 "확인 필요"로 남긴다 — 빈 칸이 거짓말보다 낫고,
    질문 갈래(BUG1)가 그 칸을 채우러 간다.
    """
    said = (request_text(state) + "\n" + conversation(state)).strip()
    symptom0 = _reported_symptom(said)
    expected0 = _reported_expectation(said)
    steps0 = _reported_steps(said, symptom0)
    steps, expected, actual, notes = steps0, expected0, symptom0, []
    # 화면·증상·희망이 원문에 모두 명시된 VoC는 이미 추출 판단이 끝났다. 같은 문장을
    # simple LLM에 다시 보내던 보정 호출을 생략한다(PASTE1: WorkArchitect 2 calls→1 call).
    direct_report = bool(steps and expected and actual)
    try:
        if direct_report:
            raise StopIteration
        schema = {"title": "bug_body", "type": "object", "properties": {
            "steps": {"type": "array", "items": {"type": "string"},
                      "description": "Korean reproduction steps in the user's stated order; empty if absent."},
            "expected": {"type": "string", "description": "Korean expected behavior; empty if absent."},
            "actual": {"type": "string", "description": "Korean observed behavior; empty if absent."},
            "notes": {"type": "array", "items": {"type": "string"},
                      "description": "Verified ticket keys, environment, or other supplied context; empty if absent."}},
            "required": ["steps", "expected", "actual", "notes"]}
        r = invoke_schema(schema, [
            ("system", "You are a QA analyst. Extract reproduction steps, expected behavior, and observed "
                       "behavior exactly from the report. Never invent a missing fact; leave it empty. Return JSON only."),
            ("user", f"Bug summary: {it.get('summary')}\n\nReport data:\n{said[:1500]}")],
            tier="simple", temperature=0.1, name="bug_body") or {}
        steps = [str(x).strip() for x in (r.get("steps") or []) if str(x).strip()]
        expected = str(r.get("expected") or "").strip()
        actual = str(r.get("actual") or "").strip()
        notes = [str(x).strip() for x in (r.get("notes") or []) if str(x).strip()]
    except StopIteration:
        pass
    except Exception:
        pass
    # 붙여넣기 wrapper 전체가 actual로 돌아오는 것은 증상이 아니다(PASTE1 실측). 원문에
    # 화면·증상·희망이 명시돼 있으면 그 문장만 사용한다. 없는 칸은 여전히 확인 필요로
    # 남겨 두므로, 이 보정은 정보를 만들어내지 않는다.
    symptom = _reported_symptom(said)
    if not actual or _looks_like_report_wrapper(actual):
        actual = symptom or _ASK_REPORTER
    if not expected:
        expected = _reported_expectation(said)
    if not steps:
        steps = _reported_steps(said, symptom)
    html = ["<h3>재현 경로</h3>"]
    if steps:
        html.append("<ol>" + "".join(f"<li>{_esc(x)}</li>" for x in steps) + "</ol>")
    else:
        html.append(f"<p>{_ASK_REPORTER}</p>")
    html.append("<h3>기대 동작</h3><p>" + _esc(expected or _ASK_REPORTER) + "</p>")
    html.append("<h3>실제 동작</h3><p>" + _esc(actual) + "</p>")
    if notes:
        # 신고자가 준 브라우저·시간대 등은 출처 문서가 아니라 **환경 정보**다. '참고'에
        # 두면 출처 없는 문서/키로 오인되어 참고 가드가 지운다(PASTE2 실측).
        html.append("<h3>환경 및 추가 정보</h3><ul>"
                    + "".join(f"<li>{_esc(x)}</li>" for x in notes) + "</ul>")
    return "".join(html)


def _task_for_module(state, mod: str, ref: dict, want: str = "") -> dict:
    """요청에는 있는데 초안에서 빠진 **모듈 하나의 Task** 를 보정 호출 1회로 만든다.

    실측 STR2: 모델이 둘째 모듈 일을 본문 '제외'에 적어 놓고 티켓은 안 만들었다. 그러면
    사용자가 시킨 일의 절반이 없어지는데 초안은 멀쩡해 보인다.

    **본문은 이 저장소의 4섹션 규율을 그대로 지킨다** — 얇게 만들어 붙이면 본문 게이트에서
    걸리고, 무엇보다 사람이 승인 화면에서 판단할 재료가 없다. 실패하면 빈 dict 를 돌려
    경고 경로로 간다(보정이 본 흐름을 죽이면 안 된다).
    """
    try:
        # ★ **HTML 을 모델에게 받지 않는다 — 조각만 받고 코드가 조립한다.** 처음엔 본문
        #   전체를 HTML 로 받았는데, 모델이 <h1>/<h2> 로 쓰고 '배경'·'완료 조건' 절을 빼서
        #   본문 게이트에 걸려 매번 빈손이 됐다(실측). 섹션 순서·이름·체크박스 형식은
        #   knowledge/07 이 정해 둔 **형식**이지 판단이 아니다 — 코드가 하면 항상 맞는다.
        schema = {"title": "module_task", "type": "object", "properties": {
            "summary": {"type": "string", "description": f"One Korean summary beginning with [{mod}]."},
            "background": {"type": "string", "description": "Two or three Korean sentences explaining the verified need."},
            "includes": {"type": "array", "items": {"type": "string"},
                         "description": "Korean work included in this ticket."},
            "excludes": {"type": "array", "items": {"type": "string"},
                         "description": "Korean exclusions, especially work owned by sibling tickets."},
            "dod": {"type": "array", "items": {"type": "string"},
                    "description": "Korean completion checks naming observable evidence."}},
            "required": ["summary", "background", "includes", "excludes", "dod"]}
        r = invoke_schema(schema, [
            ("system", "You are a PMO ticket architect. Draft one missing deliverable present in the original "
                       "request. Never add unrequested work. Return JSON only."),
            ("user", f"Original request: {request_text(state)}\n\n"
                     f"Existing sibling draft: {ref.get('summary')}\n"
                     f"Module for this ticket: {mod}\n"
                     + (f"Fixed summary: {want}\n" if want else "")
                     + f"\nDraft only the part owned by module {mod}. Do not overlap the sibling; put sibling-owned "
                     "work in `excludes`. Every DoD item must name observable completion evidence rather than "
                     "a generic phrase such as `테스트 완료`.")],
            tier="simple", temperature=0.1, name="module_task")
        r = r or {}
        s = (want or str(r.get("summary") or "")).strip()
        inc = [str(x).strip() for x in (r.get("includes") or []) if str(x).strip()]
        exc = [str(x).strip() for x in (r.get("excludes") or []) if str(x).strip()]
        dod = [str(x).strip() for x in (r.get("dod") or []) if str(x).strip()]
        bg = str(r.get("background") or "").strip()
        if len(s) >= 4 and bg and inc and exc and len(dod) >= 2:
            body = ("<h3>배경</h3><p>" + _esc(bg) + "</p>"
                    "<h3>작업 범위</h3><ul>"
                    + "".join(f"<li>포함: {_esc(x)}</li>" for x in inc)
                    + "".join(f"<li>제외: {_esc(x)}</li>" for x in exc)
                    + "</ul><h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                    + "".join(f'<li data-checked="false">{_esc(x)}</li>' for x in dod)
                    + "</ul>")
            if not s.startswith("["):
                s = f"[{mod}] {s}"
            return {"summary": s, "type": "Task", "description": body, "components": [mod],
                    "priority": ref.get("priority"), "epic": ref.get("epic")}
    except Exception:
        pass
    return {}


# "언제 끝났다고 할 수 있나"가 안 적힌 완료 조건의 전형. knowledge/07 이 이미 금지하는데
# 코드로 받치는 자리가 없어 그때그때 통과했다. **여기가 원본이고 배터리가 이것을 import 한다**
# — 같은 규칙을 두 벌로 적으면 더 관대한 쪽이 사고를 낸다(§5-e).
DOD_VAGUE = ("테스트 완료", "정상 동작", "잘 동작", "이상 없음", "문제 없음",
              "성공적으로 완료", "완료됨", "구현 완료", "설계 완료", "검증 완료",
              "작성 완료", "성능 개선 확인", "성능 기준 충족", "문서 검토 완료",
              "검토 완료", "결과 검토 완료", "결과 검토 및 승인 완료", "초안 작성", "피드백 반영",
              "정상적으로 구현", "성공적으로 구현", "기능이 검증", "정상적으로 작동")


def _vague_dod(rows) -> list:
    """판정 방법이 없는 완료 조건 줄들. 짧고 뭉뚱그린 것만 — 길게 쓴 것은 방법이 들어 있다."""
    return [d for d in rows if any(v in d for v in DOD_VAGUE) and len(d) < 24]


def _dod_rows(body) -> list:
    rows = _re.findall(r'data-checked="[^"]*"[^>]*>(.*?)</li>', str(body or ""), _re.S)
    return [x for x in (_re.sub(r"<[^>]+>", "", d).strip() for d in rows) if x]


def _drop_unrequested_deployment_dod(state, items) -> bool:
    """개발·MVP 요청을 운영 배포 약속으로 확대하지 않는다."""
    import re
    req = request_text(state)
    if re.search(r"배포|릴리(?:스|즈)|운영\s*반영|production|prod\b", req, re.I):
        return False
    changed = False
    for item in items:
        if not isinstance(item, dict):
            continue
        targets = [item] + [c for c in (item.get("children") or []) if isinstance(c, dict)]
        for target in targets:
            body = str(target.get("description") or "")
            # 범위에 끼어든 배포도 같은 의미 확장이다. 구현 자체는 유지한다.
            body, n = _re.subn(r"코드\s*작성\s*및\s*배포", "코드 작성 및 테스트 환경 검증", body)
            changed = changed or bool(n)
            rows = _dod_rows(body)
            for old in rows:
                if not re.search(r"운영\s*환경|production|prod\b|배포", old, re.I):
                    continue
                fresh = "검증 결과가 테스트 리포트 또는 결과 보고서로 확인됨"
                body = body.replace(f">{old}</li>", f">{_esc(fresh)}</li>")
                changed = True
            target["description"] = body
    return changed


def _mark_unspecified_acceptance_criteria(state, items) -> bool:
    """정의되지 않은 품질·성능 기준을 충족했다고 단정하지 않고 확인 과제로 남긴다."""
    req = request_text(state)
    has_metric = bool(_re.search(
        r"(?:p\d{2}|\d+(?:\.\d+)?\s*(?:ms|초|분|시간|%|건/초|tps|qps|mb|gb))",
        req, _re.I))
    changed = False
    for item in items:
        if not isinstance(item, dict):
            continue
        targets = [item] + [c for c in (item.get("children") or []) if isinstance(c, dict)]
        for target in targets:
            body = str(target.get("description") or "")
            for old in _dod_rows(body):
                fresh = ""
                context = f"{target.get('summary', '')} {body}"
                metric = _re.search(
                    r"(?:p\d{2}\s*(?:[<>]=?|이하|이상)?\s*\d+(?:\.\d+)?\s*ms|"
                    r"\d+(?:\.\d+)?\s*(?:ms|초|분|시간|%|건/초|tps|qps|mb|gb))",
                    old, _re.I)
                if metric and metric.group(0).replace(" ", "").lower() not in \
                        req.replace(" ", "").lower():
                    fresh = ("성능 측정 지표와 목표값은 담당팀 확인 필요 — "
                             "확정 후 측정값과 판정 결과를 티켓에 기록한다")
                if not fresh and (("품질" in context and _re.search(r"품질\s*룰.*점검\s*완료|모든\s*품질\s*룰", old))
                        or (_re.search(r"품질\s*기준", old)
                            and _re.search(r"점검|확인|충족|만족", old))):
                    fresh = ("점검 대상 품질 룰과 항목별 통과 기준은 담당팀 확인 필요 — "
                             "확정된 기준별 점검 결과를 티켓에 기록한다")
                elif not fresh and "품질" in context and _re.search(r"점검\s*결과\s*보고서.*(?:작성|공유)", old):
                    fresh = "점검 대상·결과·미충족 항목을 포함한 보고서 링크를 티켓에 기록한다"
                elif (not fresh and not has_metric
                      and _re.search(r"성능(?:이|\s)*(?:요구사항|기준)", old)
                      and _re.search(r"충족|만족", old)):
                    fresh = ("성능 측정 지표와 목표값은 담당팀 확인 필요 — "
                             "확정 후 측정값과 판정 결과를 티켓에 기록한다")
                elif not fresh and not has_metric and _re.search(r"안정적으로\s*동작", old):
                    fresh = ("안정성 판정 지표와 목표값은 담당팀 확인 필요 — "
                             "확정 후 테스트 결과를 티켓에 기록한다")
                elif not fresh and _re.search(r"성공적으로\s*구현", old):
                    fresh = "구현 결과와 테스트 결과가 티켓에 기록되어 리뷰로 확인된다"
                elif (not fresh and "리니지" in context and "홉" in context
                      and _re.search(r"구현\s*결과|테스트\s*결과", old)):
                    fresh = ("3홉 조회 결과가 기대한 업스트림·다운스트림 경로와 일치함을 "
                             "조회 테스트 결과로 확인한다")
                elif (not fresh and "리니지" in context and "홉" in context
                      and _re.search(r"관련\s*문서.*업데이트", old)):
                    fresh = "3홉 조회 범위와 테스트 결과 문서 링크를 티켓에 기록한다"
                elif (not fresh and not has_metric and "성능 측정" in context
                      and _re.search(r"성능.*(?:보고서|결과|개선\s*필요)", old)):
                    fresh = ("성능 측정 지표와 목표값은 담당팀 확인 필요 — "
                             "확정 후 측정값과 판정 결과를 티켓에 기록한다")
                elif (not fresh and "사용 가이드" in context
                      and _re.search(r"(?:검증\s*결과|사용자\s*피드백|가이드.*(?:승인|완료))", old)):
                    fresh = "가이드 링크와 내부 리뷰 결과를 parent ticket에 기록해 확인한다"
                elif not fresh and _re.search(r"^\d{4}-\d{2}-\d{2}.*마감|마감.*\d{4}-\d{2}-\d{2}", old):
                    body = body.replace(f">{old}</li>", "")
                    changed = True
                    continue
                if fresh and fresh != old:
                    body = body.replace(f">{old}</li>", f">{_esc(fresh)}</li>")
                    changed = True
            target["description"] = body
    return changed


def _dedupe_dod_rows(items) -> bool:
    """같은 확인 과제로 정규화된 중복 DoD 행을 한 번만 남긴다."""
    changed = False
    for item in items:
        if not isinstance(item, dict):
            continue
        targets = [item] + [c for c in (item.get("children") or []) if isinstance(c, dict)]
        for target in targets:
            body, seen = str(target.get("description") or ""), set()

            def keep(match):
                nonlocal changed
                plain = _re.sub(r"<[^>]+>", "", match.group(1)).strip()
                key = _re.sub(r"\s+", " ", plain)
                if key in seen:
                    changed = True
                    return ""
                seen.add(key)
                return match.group(0)

            target["description"] = _re.sub(
                r"<li\b[^>]*data-checked=[\"'][^\"']*[\"'][^>]*>(.*?)</li>",
                keep, body, flags=_re.S | _re.I)
    return changed


def _sharpen_dod(state, items) -> bool:
    """완료 조건이 "테스트 완료" 수준이면 **무엇을 보고 끝났다고 하는지**로 다시 쓴다.

    실측 STR2: 구조를 다 고치고도 "인덱스 수정 후 성능 테스트 완료" 한 줄에서 떨어졌다.
    승인하는 사람 입장에선 이게 제일 중요한 줄이다 — 여기가 흐리면 티켓이 언제 닫히는지
    아무도 모른다. 판단(무엇을 재나)은 모델이 하고, 줄을 갈아 끼우는 것은 코드가 한다.
    호출은 초안당 최대 2건으로 묶는다(왕복 비용).
    """
    hit = False
    # Sub-Task는 배경을 반복하지 않지만 DoD는 판정 가능해야 한다. 자식마다 LLM을 다시
    # 부르지 않고, 기존 문장에 어떤 증거를 parent에 남길지만 덧붙인다.
    subordinate = []
    for parent in items[:6]:
        subordinate.extend((parent.get("children") or []) if isinstance(parent, dict) else [])
        if (isinstance(parent, dict)
                and str(parent.get("type") or "").lower().startswith("sub")):
            subordinate.append(parent)
    for child in subordinate:
        if not isinstance(child, dict):
            continue
        body = str(child.get("description") or "")
        for old in _vague_dod(_dod_rows(body)):
            if any(w in old for w in ("성능", "정확", "검증")):
                proof = "검증 기준·측정값·판정 결과를 parent ticket에 기록해 확인한다"
            elif any(w in old for w in ("테스트", "구현", "코드")):
                proof = "실행 로그와 테스트 결과를 parent ticket에 기록해 확인한다"
            else:
                proof = "산출물 링크와 리뷰 결과를 parent ticket에 기록해 확인한다"
            body = body.replace(f">{old}</li>", f">{_esc(old)} — {_esc(proof)}</li>")
            hit = True
        child["description"] = body
    for it in items[:6]:
        # 최상위 Task도 모호한 행을 하나라도 남기지 않는다. 예전에는 절반을 넘을 때만 별도
        # LLM 호출로 고쳐, `알림 테스트 완료` 한 줄 + 구체 행 한 줄이면 사람 품질은 나쁜데
        # 자동 게이트는 통과했다. 관찰 증거의 형식은 판단이 아니므로 코드로 붙인다. 이로써
        # draft마다 발생하던 추가 simple-model 왕복도 제거된다.
        if not isinstance(it, dict) or str(it.get("type") or "").lower().startswith("sub"):
            continue
        body = str(it.get("description") or "")
        bad = _vague_dod(_dod_rows(body))
        for old in bad:
            stem = _re.sub(
                r"(?:테스트\s*완료|검토\s*완료|검증\s*완료|구현\s*완료|작성\s*완료|"
                r"설계\s*완료|완료됨?|성능\s*개선\s*확인|성능\s*기준\s*충족|"
                r"정상(?:적으로)?\s*(?:동작|작동|구현)|이상\s*없음|문제\s*없음)",
                "", old, flags=_re.I).strip(" -·:;")
            subject = stem or _re.sub(
                r"^\s*\[[^\]]+\]\s*", "", str(it.get("summary") or "작업")).strip()
            if any(w in old for w in ("성능", "정확", "검증", "기준")):
                fresh = f"{subject} 검증 기준·측정값·판정 결과를 티켓에 기록해 확인한다"
            elif any(w in old for w in ("테스트", "구현", "코드", "동작", "작동")):
                fresh = f"{subject} 실행 로그와 테스트 결과를 티켓에 기록해 확인한다"
            else:
                fresh = f"{subject} 산출물 링크와 리뷰 결과를 티켓에 기록해 확인한다"
            body = body.replace(f">{old}</li>", f">{_esc(fresh)}</li>")
            hit = True
        it["description"] = body
    return hit


def _fill_thin_bodies(state, items, repair: bool = True) -> bool:
    """최상위 Task 본문이 4섹션 규율을 못 채우면 **조각을 받아 코드가 다시 조립한다.**

    실측 STR1: 구조(부모 1 + 자식 30)를 다 맞추고도 부모 본문에 '배경'이 없어서 떨어졌다.
    승인 화면에서 사람이 판단할 재료가 본문인데, 그게 얇으면 구조가 맞아도 쓸모가 없다.

    **한 초안에 한 건만** 고친다 — 왕복 비용이고, 여러 건이 동시에 얇으면 그건 본문 문제가
    아니라 요청 해석 문제라 다른 가드가 볼 일이다. Sub-Task 는 대상이 아니다(knowledge/07:
    자식 본문에 배경을 반복해 쓰지 않는다).
    """
    tops = [i for i in items[:6]
            if isinstance(i, dict) and not str(i.get("type") or "").lower().startswith("sub")]
    # ① 배경 채우기는 **LLM 호출이 없다 — 전 항목에 건다.** 처음엔 아래 보정과 함께 "한 건만"
    #    고치게 뒀는데, 초안이 4건으로 갈린 실행에서 **둘 이상이 얇아** 첫 건만 고쳐진 채
    #    나갔다(실측 STR2: `[2] '배경' 섹션 없음`). 공짜인 수리를 아낄 이유가 없다.
    req = request_text(state).strip()
    hit = False
    if req:
        for it in tops:
            # ★ **Bug 은 이 수리의 대상이 아니다**(사용자 관점 리뷰 F5, blocker).
            #   여기서 '배경'을 붙이면 Bug 본문이 Task 모양으로 오염되고, 아래 ②의 게이트
            #   (`_task_grade_body`)도 통과해 버려 **재현 경로가 영영 안 들어간다**.
            #   버그는 아래 ②-b 가 자기 규율로 따로 채운다.
            if _is_bug_item(it):
                continue
            body = str(it.get("description") or "")
            if "배경" not in body:
                it["description"] = f"<h3>배경</h3><p>{_esc(req[:400])}</p>" + body
                hit = True
    # ② 그러고도 4섹션을 못 채우는 항목 **하나**만 보정 호출로 다시 쓴다(왕복 비용).
    #    되묻는 턴에서는 이 왕복을 건너뛴다 — 초안이 아직 확정 전이라 다시 쓸 값이 바뀐다.
    if not repair:
        return hit
    # ②-b Bug 은 **다른 최소선**을 본다 — 재현 경로·기대·실제. 판단(무엇이 버그 규율인가)이
    #     갈렸으면 보장도 같이 갈려야 한다. 예전엔 여기가 Task 템플릿 하나뿐이어서, 모델이
    #     옳게 써 놔도 코드가 덮어썼다.
    for it in tops:
        if _is_bug_item(it) and not _bug_grade_body(it.get("description")):
            it["description"] = _bug_body_for(state, it)
            hit = True
    for it in tops:
        if _is_bug_item(it) or _task_grade_body(it.get("description")):
            continue
        mod = str((it.get("components") or [""])[0] or "")
        # ★ 보정 호출은 LLM 한 방이라 그냥 빈손일 때가 있다(실측 STARR1: 20케이스 한 실행에서
        #   이 한 건 때문에 떨어졌는데, 따로 3회 돌리면 3회 다 통과했다 — 즉 호출 실패다).
        #   그래서 ①의 배경 채우기를 **먼저** 돌려 두고, 여기 실패는 원본 유지로 끝낸다.
        full = _task_for_module(state, mod, it, want=str(it.get("summary") or ""))
        if full.get("description"):
            it["description"] = full["description"]
            hit = True
        break                 # 왕복은 한 초안에 한 번. 나머지는 ①이 이미 최소선을 채웠다
    return hit


def _esc(s) -> str:
    """모델이 준 조각을 HTML 로 넣기 전에 — 꺾쇠가 그대로 들어가면 본문이 깨진다."""
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))



# 어느 일에나 붙는 껍데기 제목 — 이것만으로는 무슨 작업인지 알 수 없다.
_STAGE_ONLY = _re.compile(
    r"^(설계|구현|개발|검증|테스트|배포|분석|조사|문서화|리뷰|기획|적용|점검)"
    r"\s*(단계|작업|하기|진행)?$")


def _generic_title(summary: str) -> bool:
    """제목이 **단계 이름뿐**인가 — "설계 단계"·"구현"·"검증 작업" 같은 것.

    실사용 지적: Sub-Task 가 '설계 단계 / 구현 단계 / 검증 단계' 로 나왔다. 세 티켓의
    제목이 서로 구분은 되지만 **무슨 일인지는 어느 것도 말해 주지 않는다** — 담당자가
    티켓을 열기 전에는 시작할 수 없고, 목록에서는 세 줄이 똑같아 보인다.
    """
    t = str(summary or "").strip().strip("[]()")
    if not t:
        return True
    return bool(_STAGE_ONLY.match(t)) or len(t) <= 4


def _preserve_parent_topic_in_children(items: list) -> bool:
    """부모의 영문 기술 고유어가 빠진 generic child 제목에 주제를 복원한다."""
    changed = False
    for parent in items or []:
        if not isinstance(parent, dict):
            continue
        subject = _re.sub(r"^\s*\[[^]]+\]\s*", "", str(parent.get("summary") or "")).strip()
        tokens = [t for t in _re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", subject)
                  if t.lower() not in {"task", "story", "bug", "feature"}]
        if not tokens:
            continue
        core = _re.sub(r"\s*(?:개발|구현|개선|작업|진행|추가|수정)\s*$", "", subject).strip()
        for child in (parent.get("children") or []):
            if not isinstance(child, dict):
                continue
            title = str(child.get("summary") or "").strip()
            child_tokens = _re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", title)
            # 이미 `NDV Batch Job 설계`처럼 자기 기술 대상을 가진 제목에 부모의 다른
            # 영문 토픽까지 붙이면 두 작업을 한 제목으로 합쳐 버린다. 이 가드는
            # `파이프라인 설계 완료`처럼 기술 대상이 통째로 빠진 제목만 복원한다.
            if (not title or child_tokens
                    or any(t.lower() in title.lower() for t in tokens)):
                continue
            # parent core의 마지막 일반 대상어가 child 첫머리에 반복되면 한 번만 둔다.
            tail = core.split()[-1] if core.split() else ""
            rest = _re.sub(rf"^{_re.escape(tail)}\s+", "", title) if tail else title
            child["summary"] = f"{core} {rest}".strip()
            changed = True
    return changed


def _children_from_dod(item: dict) -> list:
    """본문 DoD 불릿을 실행 단위 Sub-Task 로 — LLM 없이. 조건이 안 맞으면 빈 리스트.

    knowledge/07: "DoD 가 5개를 넘고 서로 다른 단계(설계/구현/검증/연동)라면 그건 DoD 가
    아니라 Sub-Task 목록이다 — 구조를 다시 판단하라." 판단은 이미 문서에 있다.
    """
    body = str(item.get("description") or "")
    rows = [_re.sub(r"<[^>]+>", "", d).strip()
            for d in _re.findall(r'data-checked="[^"]*"[^>]*>(.*?)</li>', body, _re.S)]
    rows = [r for r in rows if 6 <= len(r) <= 60]
    stages = ("설계", "구현", "검증", "연동", "테스트", "배포", "모니터링", "전환", "분석", "문서")
    if len(rows) < 3 or sum(1 for w in stages if any(w in r for r in rows)) < 2:
        return []          # 단계가 안 갈리면 그건 진짜 DoD 다 — 건드리지 않는다
    return [{"summary": r} for r in rows[:5]]


_WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}


def _relative_due(text: str) -> str:
    """"다음주 수요일"·"이번주 금요일"·"내일"·"모레" → YYYY-MM-DD. 못 알아들으면 "".

    날짜 산술은 판단이 아니라 계산이다 — 모델에게 맡기면 요일이 틀린다(실측)."""
    from datetime import date, timedelta
    t = (text or "").replace(" ", "")
    today = date.today()
    if "내일" in t:
        return (today + timedelta(days=1)).isoformat()
    if "모레" in t:
        return (today + timedelta(days=2)).isoformat()
    # 업무 문맥의 "이번 주까지"는 해당 주의 마지막 근무일(금요일)로 고정한다.
    # 모델에게 맡기면 화요일·토요일처럼 실행마다 다른 날짜가 나왔다(실측 ASK2).
    # 이미 금요일이 지난 주말에 들어온 요청은 과거 날짜를 만들지 않고 다음 금요일로 보낸다.
    if "이번주까지" in t or "금주까지" in t:
        monday = today - timedelta(days=today.weekday())
        d = monday + timedelta(days=4)
        if d < today:
            d += timedelta(days=7)
        return d.isoformat()
    m = _re.search(r"(다음\s*주|이번\s*주|담주|차주)([월화수목금토일])요일", text or "") \
        or _re.search(r"(다음주|이번주|담주|차주)([월화수목금토일])", t)
    if not m:
        return ""
    wd = _WEEKDAYS[m.group(2)]
    # 이번 주 = 오늘이 속한 주(월요일 시작), 다음 주 = 그다음 주.
    monday = today - timedelta(days=today.weekday())
    base = monday if m.group(1).replace(" ", "") == "이번주" else monday + timedelta(days=7)
    d = base + timedelta(days=wd)
    if d < today:               # 일요일에 "이번주 금요일" = 이미 지난 날 — 다가오는 그 요일로
        d += timedelta(days=7)
    return d.isoformat()


def _apply_relative_due_to_single_draft(state: dict, items: list) -> str:
    """현재 메시지의 상대 기한을 단일 생성 초안에 적용하고 확정값을 반환한다.

    원 요청 전체가 아니라 마지막 사용자 메시지를 우선하는 이유는, 후속 편집에서 이미 바꾼
    기한을 첫 턴의 상대 표현이 다시 덮지 않게 하기 위해서다. 메시지가 없는 단위 테스트·내부
    호출만 원 요청을 fallback으로 사용한다.
    """
    if len(items or []) != 1 or not isinstance(items[0], dict):
        return ""
    source = last_user_text(state) or request_text(state)
    due = _relative_due(source)
    if due:
        items[0]["duedate"] = due
    return due


_QUALITY_DIMENSIONS = (
    r"사용자\s*(?:편의성|경험)|편의성|사용성|\bUX\b|usability|user\s+experience",
    r"운영\s*효율성|업무\s*효율성|효율성|생산성|efficien(?:cy|t)|productivity",
    r"성능|처리량|응답\s*속도|performance|throughput|latency",
    r"안정성|신뢰(?:성|할)|가용성|stable|stability|reliability|availability",
    r"정확(?:성|한|도)?|정합성|품질\s*(?:향상|개선)|accuracy|correctness",
    r"보안성|보안\s*(?:강화|향상|개선)|개인정보|노출\s*(?:감소|방지)|security|privacy",
    r"유지보수성|확장성|scalability|maintainability",
    r"비용\s*(?:절감|감소|최적화)|cost\s*(?:saving|reduction|optimization)",
    r"적시성|timeliness",
)


def _remove_unrequested_quality_claims(state: dict, items: list) -> bool:
    """원 요청에 없는 품질 효과를 ticket body의 배경·범위·DoD에서 제거한다.

    `개선`처럼 차원이 열려 있는 동사를 모델이 임의로 `성능 개선`, `안정성 향상`으로
    좁히면 형식 검사는 통과해도 다른 작업이 된다. 여기서는 사용자 원문에 실제로 등장한
    차원만 허용한다. Bug는 재현/기대/실제 계약이 별도이므로 대상에서 제외한다.
    """
    request = (request_text(state) + " " + last_user_text(state)).strip()
    forbidden = [p for p in _QUALITY_DIMENSIONS if not _re.search(p, request, _re.I)]
    if not forbidden:
        return False

    changed = False
    for item in items or []:
        if not isinstance(item, dict) or _is_bug_item(item):
            continue
        body = str(item.get("description") or "")
        if not body:
            continue
        summary = _re.sub(r"^\s*\[[^\]]+\]\s*", "", str(item.get("summary") or "작업")).strip()
        safe = _esc(summary or "요청한 작업")

        def has_forbidden(value: str) -> bool:
            return any(_re.search(p, value or "", _re.I) for p in forbidden)

        # 배경은 효과를 추측하지 않고 요청 사실만 남긴다.
        bg_pattern = r"(<h3>\s*배경\s*</h3>\s*)(.*?)(?=<h3>|$)"
        bg = _re.search(bg_pattern, body, _re.S | _re.I)
        if bg and has_forbidden(bg.group(2)):
            body = body[:bg.start(2)] + f"<p>{safe} 요청됨.</p>" + body[bg.end(2):]
            changed = True

        # 범위에서 새 품질 차원이 생겼다면 합의된 summary 경계로 복원한다.
        scope_pattern = r"(<h3>\s*작업 범위\s*</h3>\s*)(.*?)(?=<h3>|$)"
        scope = _re.search(scope_pattern, body, _re.S | _re.I)
        if scope and has_forbidden(scope.group(2)):
            fresh = (f"<ul><li>포함: {safe}</li>"
                     "<li>제외: 요청에 명시되지 않은 연관 기능 변경</li></ul>")
            body = body[:scope.start(2)] + fresh + body[scope.end(2):]
            changed = True

        # 완료 조건은 해당 행만 보수적인 증거 확인 문장으로 바꾼다. 이후 dedupe가 같은 행을
        # 한 번으로 접는다.
        def clean_dod(match):
            nonlocal changed
            inner = match.group(1)
            if not has_forbidden(_re.sub(r"<[^>]+>", " ", inner)):
                return match.group(0)
            changed = True
            return (match.group(0)[:match.group(0).find(">") + 1]
                    + f"{safe} 결과와 검증 기록이 티켓에 남고 담당 리뷰로 확인됨</li>")

        body = _re.sub(
            r"<li\b[^>]*data-checked=[\"'][^\"']*[\"'][^>]*>(.*?)</li>",
            clean_dod, body, flags=_re.S | _re.I)
        item["description"] = body
    return changed


def _base_title(s: str) -> str:
    """제목에서 분할 표식(번호·단계 낱말)을 뗀 몸통 — 같으면 같은 산출물이다.

    번호는 꼬리("… - 테이블 3")만이 아니라 중간("테이블 3 등록")에도 온다(실측) —
    숫자를 전부 지우고 공백을 접어 비교한다. 단계 낱말은 꼬리에서만 뗀다."""
    s = _re.sub(r"\d+", "", s or "")
    s = _re.sub(r"\s*[-–—:]?\s*(?:설계|구현|검증|테스트|연동|모니터링|문서화|배포|개발)"
                r"(?:\s*단계)?\s*$", "", s.strip()).strip()
    return _re.sub(r"\s{2,}", " ", s).strip(" -–—:#")


def draft_full_text(draft: dict, cap: int = 4000) -> str:
    """초안 **전문** — 후속 턴 WorkArchitect 와 Auditor 가 본다.

    draft_text() 는 본문을 150자로 잘라 채팅 표시엔 맞지만, 그걸 '고칠 대상'이나 '검열
    대상'으로 주면 중복 섹션·날조 불릿·주제 이탈이 컷 밖에 숨는다(실측). 전문을 준다."""
    if not draft or not draft.get("items"):
        return ""
    rows = [f"mode={draft.get('mode')} · structure={draft.get('structure') or '?'}"]
    for i, it in enumerate(draft.get("items") or []):
        head = [f"[{i}] {it.get('type', '')} — {it.get('summary', '')}"]
        for k, label in (("epic", "상위"), ("parent", "부모"), ("components", "모듈"),
                         ("labels", "라벨"), ("duedate", "마감"), ("priority", "우선순위"),
                         ("assignee", "담당")):
            v = it.get(k)
            if v:
                head.append(f"{label}={v if not isinstance(v, list) else ', '.join(map(str, v))}")
        rows.append("  ".join(head))
        if it.get("description"):
            rows.append("  본문:\n  " + str(it["description"]).replace("\n", "\n  "))
        for c in (it.get("children") or []):
            if isinstance(c, dict):
                rows.append(f"  └ Sub-Task: {c.get('summary', '')}"
                            + (f" (담당 {c.get('assignee')})" if c.get("assignee") else ""))
    return "\n".join(rows)[:cap]


def _merge_refs(desc: str, refs: list) -> str:
    """조사 근거를 본문의 **'참고' 섹션에 병합**한다. refs = [(중복판정키, "<li>…</li>")].

    별도 <h3>References</h3> 를 덧붙이던 방식은 모델이 쓴 <h3>참고</h3> 와 무조건
    중복됐다(실측: 참고/Knowledge/References 3벌). 섹션은 '참고' 하나다 —
    본문에 이미 있는 키·URL 은 붙이지 않고, '참고' h3 가 없을 때만 새로 만든다."""
    fresh = "".join(li for key, li in refs if key not in (desc or ""))
    if not fresh:
        return desc
    m = _re.search(r"(<h3>\s*참고\s*</h3>\s*<ul[^>]*>)(.*?)(</ul>)", desc or "",
                   _re.S | _re.I)
    if m:
        return desc[:m.end(2)] + fresh + desc[m.end(2):]
    return (desc or "") + "<h3>참고</h3><ul>" + fresh + "</ul>"


def _drop_empty_sections(desc: str) -> str:
    """내용 없는 섹션(헤딩 + 빈 목록/공백)을 걷어낸다.

    실측: 참고에 실을 것이 없는데 `<h3>참고</h3><ul></ul>` 이 그대로 남아 티켓에
    박제됐다. 빈 섹션은 "여기 뭔가 있어야 하는데 빠졌다"로 읽힌다 — 없는 게 낫다.
    """
    if not desc:
        return desc
    # ① 빈 목록/문단 제거 → ② 그 결과 헤딩만 남은 섹션 제거
    out = _re.sub(r"<(ul|ol)>\s*(?:<li>\s*</li>\s*)*</\1>", "", desc)
    out = _re.sub(r"<p>\s*(?:&nbsp;)?\s*</p>", "", out)
    out = _re.sub(r"<h([1-6])>[^<]*</h\1>\s*(?=(<h[1-6]>|$))", "", out)
    return out.strip()


def _drop_subtask_ticket_refs(desc: str) -> str:
    """기존 parent가 맥락을 가진 Sub-Task 본문에서는 중복 참고 섹션을 제거한다."""
    return _re.sub(r"(<h3>\s*참고(?:\s*(?:사항|자료|문서))?\s*</h3>\s*<ul[^>]*>)"
                   r"(.*?)(</ul>)", "", desc or "", flags=_re.S | _re.I)


def _drop_unverified_refs(desc: str, allowed_keys: set, allowed_urls: set) -> tuple:
    """Research Analyst가 검증한 key/URL만 참고 섹션에 남긴다."""
    gone = []
    keys = {str(k).upper() for k in (allowed_keys or set()) if str(k)}
    urls = {str(u).strip() for u in (allowed_urls or set()) if str(u).strip()}

    def _clean(m):
        head, body, tail = m.group(1), m.group(2), m.group(3)
        kept = []
        for li in _re.findall(r"<li[^>]*>.*?</li>", body, _re.S):
            found_keys = {k.upper() for k in
                          _re.findall(r"\b[A-Z][A-Z0-9]*-\d+\b", li, _re.I)}
            found_urls = {u.strip() for u in
                          _re.findall(r"href=[\"']([^\"']+)[\"']", li, _re.I)}
            if (found_keys and found_keys & keys) or (found_urls and found_urls & urls):
                kept.append(li)
            else:
                gone.append(_re.sub(r"<[^>]+>", "", li).strip()[:50])
        return head + "".join(kept) + tail

    out = _re.sub(r"(<h3>\s*참고(?:\s*(?:사항|자료|문서))?\s*</h3>\s*<ul[^>]*>)"
                  r"(.*?)(</ul>)", _clean, desc or "", flags=_re.S | _re.I)
    return out, gone


def _drop_unlinked_refs(desc: str) -> tuple:
    """'참고' 섹션에서 **티켓 키도 링크도 없는 불릿**을 뺀다 → (본문, 뺀 것 목록).

    링크 없는 문서 제목("아키텍처 결정 기록" 등)은 검증할 수 없다 — 실측에서 mock 코멘트
    속 문구가 문서인 양 나열됐다. 챗 답변의 grounding 과 같은 원칙: 출처 없는 것은 안 싣는다."""
    gone = []

    def _clean(m):
        head, body, tail = m.group(1), m.group(2), m.group(3)
        kept = []
        for li in _re.findall(r"<li[^>]*>.*?</li>", body, _re.S):
            if _re.search(r"\b[A-Z][A-Z0-9]*-\d+\b", li) or "<a " in li:
                kept.append(li)
            else:
                gone.append(_re.sub(r"<[^>]+>", "", li).strip()[:30])
        return head + "".join(kept) + tail

    out = _re.sub(r"(<h3>\s*참고(?:\s*(?:사항|자료|문서))?\s*</h3>\s*<ul[^>]*>)"
                  r"(.*?)(</ul>)", _clean, desc or "",
                  flags=_re.S | _re.I)
    return out, gone


def _topic_drift(state, items: list) -> str:
    """원 요청의 고유어가 제목·본문 어디에도 없으면 경고 문구를 돌려준다(없으면 빈 문자열).

    고유어 = 식별자(테이블명 등) + 영문 기술 토큰(4자↑, 일반어 제외). 판정은 코드가 하고
    고칠지는 사람이 정한다 — 경고는 rationale 로 승인 카드에 노출된다."""
    req = request_text(state)
    if not req or not items:
        return ""
    try:
        from app.agent.tools._ident import find_identifiers
        terms = {str(t).strip().rstrip(".,;:()[]") for t in find_identifiers(req)}
    except Exception:
        terms = set()
    _COMMON = {"task", "story", "bug", "feature", "improvement", "epic", "jira",
               "test", "data", "table", "api", "the", "and", "pipeline", "with",
               "for", "this"}
    terms |= {w for w in _re.findall(r"[A-Za-z][A-Za-z0-9_.-]{3,}", req)
              if w.lower() not in _COMMON}
    # priority/label 같은 배치 속성은 주제가 아니다. 제목·본문에 label이 없다는 이유로
    # topic drift를 띄우면 단건 자동 검증도 불필요하게 우회한다(ATTR1: hotfix.).
    labels = {str(x).strip().lower() for i in items for x in (i.get("labels") or [])}
    terms = {t for t in terms if t and t.lower() not in labels and t.lower() not in _COMMON}
    if not terms:
        return ""
    hay = " ".join(str(i.get("summary") or "") + " " + str(i.get("description") or "")
                   for i in items).lower()
    if any(t.lower() in hay for t in terms):
        return ""
    shown = ", ".join(sorted(terms)[:4])
    return (f"(확인 필요: 원 요청의 고유어({shown})가 제목·본문에 없다 — 요청과 다른 "
            "주제의 티켓일 수 있다. Epic 본문을 따라간 것은 아닌지 검토)")


def as_bulk_items(draft: dict) -> list:
    """초안 → `validate_ticket_plan` / `create_tickets` 가 받는 형태.

    `epic` 이 빈 문자열이면 **`None` 으로 바꾼다** — 규칙상 "최상위로 두겠다"는 명시가 필요하고,
    빈 문자열은 그 명시로 인정되지 않는다.
    """
    mode = (draft or {}).get("mode") or "task"
    if mode == "epic":
        # Epic 은 bulk 생성 대상이 아니다 — 화면·검증 표시용 한 줄만 만든다.
        # 실행은 ActionExecutor 가 create_epic 도구로 한다(승인 지문은 epic_payload 가 정의).
        out = []
        for it in (draft or {}).get("items") or []:
            out.append({"summary": (it.get("summary") or "").strip(), "type": "Epic",
                        **({"epic_name": it["epic_name"]} if it.get("epic_name") else {}),
                        **({k: it[k] for k in ("description", "priority", "duedate", "assignee")
                            if str(it.get(k) or "").strip()}),
                        **({"components": it["components"]} if it.get("components") else {})})
        return out
    out = []
    for it in (draft or {}).get("items") or []:
        row = {"summary": (it.get("summary") or "").strip(), "type": (it.get("type") or "").strip()}
        if mode == "subtask":
            row["parent"] = (it.get("parent") or "").strip()
        else:
            row["epic"] = (it.get("epic") or "").strip() or None
        for k in ("description", "priority", "duedate", "assignee"):
            if str(it.get(k) or "").strip():
                row[k] = str(it[k]).strip()
        for k in ("components", "labels"):
            vals = [str(v).strip() for v in (it.get(k) or []) if str(v).strip()]
            if vals:
                row[k] = vals
        out.append(row)
    return out


def child_items(draft: dict) -> list:
    """초안의 children 을 부모 index 와 함께 평평하게 편다 — 승인 지문·연쇄 생성이 같은 것을 본다.

    Sub-Task 는 부모 키가 있어야 만들 수 있는데(도메인 규칙), 부모는 아직 없다. 그래서
    **부모 index** 로 묶어 두고 ActionExecutor 가 부모 생성 결과 키로 치환한다.
    """
    rows = []
    for i, it in enumerate((draft or {}).get("items") or []):
        for ch in (it.get("children") or []):
            if not isinstance(ch, dict) or not str(ch.get("summary") or "").strip():
                continue
            row = {"parent_index": i, "summary": str(ch["summary"]).strip(), "type": "Sub-Task"}
            for k in ("description", "assignee", "duedate"):
                if str(ch.get(k) or "").strip():
                    row[k] = str(ch[k]).strip()
            rows.append(row)
    return rows


def epic_payload(draft: dict) -> dict:
    """epic 모드의 승인 지문 payload — `create_epic` 도구가 consume 때 만드는 것과
    **같은 모양**이어야 지문이 맞는다(도구는 compact 로 빈 값을 떨군다)."""
    from app.agent.tools._ctx import compact
    it = ((draft or {}).get("items") or [{}])[0]
    return compact({"summary": (it.get("summary") or "").strip(),
                    "epic_name": (it.get("epic_name") or "").strip(),
                    "description": it.get("description") or "",
                    "components": [x for x in (it.get("components") or []) if x],
                    "priority": (it.get("priority") or "").strip(),
                    "duedate": (it.get("duedate") or "").strip(),
                    "assignee": (it.get("assignee") or "").strip()})


def draft_json(draft: dict) -> str:
    return json.dumps(as_bulk_items(draft), ensure_ascii=False, indent=1)


def _is_epic(key: str) -> bool:
    """그 키가 정말 Epic 인가 — 타입 확인은 판단이 아니라 조회다."""
    try:
        from app.agent.tools._ctx import client
        f = (client().get_issue(key) or {}).get("fields") or {}
        return str((f.get("issuetype") or {}).get("name") or "") == "Epic"
    except Exception:
        return False


def spread_volume_split(items: list) -> bool:
    """분량 분할 자식이 한 사람에게 몰렸으면 모듈 인력으로 고루 돌린다. 바꿨으면 True.

    knowledge/07: 같은 일을 나눈 **분량 분할은 골고루** 나눈다 — 한 사람에게 몰면 쪼갠
    의미가 없다. 프롬프트로 지시하되 몰아준 경우 코드가 되돌린다(새 사람을 지어내지 않고
    그 모듈 로스터 안에서만 돌린다).

    **부르는 자리가 둘이다** — WorkArchitect 직후(배정 전)와 `merge_assignments` 직후(배정 후).
    자식 담당의 주인이 PeopleAdvisor 로 옮겨 가면서(역할 정합 감사 §5-c) WorkArchitect 에서만 돌던
    이 가드가 **덮어쓰기 뒤편에 남았다**: 실측(생성 스위트 STR1) 테이블 29건이 WorkArchitect
    에서 고루 나뉜 뒤 PeopleAdvisor 제안으로 전부 skcc.x1210 이 됐다. 규칙은 한 벌이고 부르는
    자리만 둘이다 — 가드를 두 벌로 베끼면 더 관대한 쪽이 사고를 낸다.

    사용자가 입으로 지정한 담당(`assignee_source == "user"`)은 건드리지 않는다.
    """
    changed = False
    for it in items or []:
        if not isinstance(it, dict):
            continue
        kids = [c for c in (it.get("children") or []) if isinstance(c, dict)]
        if any(c.get("assignee_source") == "user" for c in kids):
            continue                      # 지정은 결정이다 — 배분이 덮지 않는다
        named = [str(c.get("assignee") or "").strip() for c in kids
                 if str(c.get("assignee") or "").strip()]
        if len(kids) < 3 or len(named) != len(kids) or len(set(named)) != 1:
            continue
        pool = _module_pool(it, named[0])
        if len(pool) > 1:
            for i, c in enumerate(kids):
                c["assignee"] = pool[i % len(pool)]
            changed = True
    return changed


def _module_pool(item: dict, fallback: str) -> list:
    """이 티켓 모듈의 실 인력. 분량 분할을 돌릴 때 **지어내지 않기 위해** 로스터를 쓴다."""
    try:
        from app.infra.settings import load_people, resolve_module
        roster = load_people() or {}
        for comp in (item.get("components") or []):
            # 정확 일치 → 표기 정규화 순. 컴포넌트 이름과 로스터 키는 두 벌이라
            # 대소문자·공백에서 갈리고, 갈리면 로스터가 통째로 비어 채움이 무산된다.
            key = str(comp) if str(comp) in roster else resolve_module(comp)
            ids = [str(x) for x in (roster.get(key) or []) if str(x)]
            if ids:
                return ids
        # 컴포넌트를 못 믿겠으면 그 사람이 속한 모듈로
        for ids in roster.values():
            if fallback in (ids or []):
                return [str(x) for x in ids]
    except Exception:
        pass
    return [fallback] if fallback else []


# 경로별로 **안 쓰이는** 역할 지시 절. 제목은 work_architect.md 의 `## …` 과 정확히 같아야 한다
# (오타는 조용히 아무것도 안 빼므로, 아래 테스트가 제목 존재를 지킨다).
_CREATE_ONLY = ["Structure Selection", "Decomposition Rules", "Ticket Body Contract",
                "Epic Creation", "Bulk Sub-Task Creation", "Pasted Notes and Lists",
                "Title and Topic Preservation"]
_MODIFY_ONLY = ["Comment Drafting", "Existing Ticket Changes"]


def _role_md(state) -> str:
    """이번 경로에 필요한 절만 조립한다.

    ★ 초안을 만드는 턴(생성·버그·초안 수정)에는 **전부** 싣는다 — 품질이 먼저다.
    빼는 것은 기존 티켓의 필드를 바꾸는 순수 modify 턴뿐이고, 거기서는 생성 지시가
    판단에 쓰이지 않는다(초안 items 를 내지 않는 경로다).
    """
    from app.agent.prompts.roles import compose
    intent = (state.get("intent") or "").strip()
    editing_draft = bool((state.get("draft") or {}).get("items"))
    if intent == Intent.MODIFY and not editing_draft:
        return compose(SYSTEM_WORK_ARCHITECT, _CREATE_ONLY)
    if intent in Intent.DRAFTS_TICKETS:
        return compose(SYSTEM_WORK_ARCHITECT, _MODIFY_ONLY)
    return SYSTEM_WORK_ARCHITECT


def _rules_material(state) -> str:
    """초안에 필요한 **작성 규칙 발췌**(정적 RAG). 규칙 전문을 프롬프트에 붓지 않는다.

    Auditor 의 `_rules_for` 와 같은 재료다 — 검열이 볼 규칙을 작성자도 봐야 왕복이 준다.
    """
    try:
        from app.agent.retrieval import static_index
        shape = " ".join(str(i.get("type") or "") for i in
                         (state.get("draft") or {}).get("items") or [])
        q = ("티켓 작성 규칙 본문 구조 완료 조건 " + shape).strip()
        return "\n\n".join(h["text"] for h in static_index.search(q, k=3))[:2500]
    except Exception:
        return ""


def _placement_material(state) -> str:
    """배치 재료 — Epic 후보·허용 컴포넌트·기존 라벨을 **코드가 미리 조회**해 준다.

    도구로 두면 모델이 부를 때만 보이고, 안 부르면 지어낸다(실측: Task 를 Epic 이라 답하고
    초안엔 안 실었다). 반복 조회는 판단이 아니므로 코드가 한다.
    """
    # 두 조회는 독립 — 병렬로. prod 는 호출당 수백 ms~수 초라 직렬이 그대로 대기가 된다.
    from concurrent.futures import ThreadPoolExecutor

    from app.agent.tools.write_tools import list_ticket_options
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_epic = ex.submit(_epic_options, state)
        fut_opts = ex.submit(lambda: list_ticket_options.invoke({"kind": ""}) or {})
    parts = []
    try:
        rows = fut_epic.result()
        if rows:
            parts.append("Epic 후보 (여기서 고른다. 모듈이 다르면 항목마다 다른 Epic 이 정상. "
                         "마땅한 게 없으면 questions 로 물어라):\n"
                         + "\n".join('- {} [{}] "{}"'.format(r["key"], r.get("module") or "-",
                                                             r.get("summary", ""))
                                     for r in rows[:10]))
    except Exception:
        pass
    try:
        opts = fut_opts.result()
        if opts.get("components"):
            parts.append("컴포넌트(모듈) 실값 — **하나만** 고른다: "
                         + ", ".join(str(x) for x in opts["components"][:12]))
        if opts.get("labels"):
            parts.append("기존 라벨 (여기 없는 라벨은 '신규'로 표시된다): "
                         + ", ".join(str(x) for x in opts["labels"][:30]))
    except Exception:
        pass
    return "\n\n".join(parts)


def _epic_module(key: str) -> str:
    """Epic 의 모듈(컴포넌트). 티켓 컴포넌트와 어긋나면 배치가 틀린 신호다."""
    try:
        from app.agent.tools._ctx import client
        f = (client().get_issue(key) or {}).get("fields") or {}
        comps = [c.get("name") for c in (f.get("components") or []) if c.get("name")]
        return str(comps[0]) if comps else ""
    except Exception:
        return ""


def _known_labels() -> set:
    """기존 라벨 집합 — 여기 없으면 '신규 라벨'로 승인 카드에 표시한다(막지는 않는다)."""
    try:
        from app.agent.tools.write_tools import list_ticket_options
        return {str(x) for x in ((list_ticket_options.invoke({"kind": "labels"}) or {})
                                 .get("labels") or [])}
    except Exception:
        return set()


def _epic_options(state) -> list:
    """붙일 수 있는 Epic 목록 — 도구와 **같은 것**을 본다(`find_parent_epic`).

    모듈을 짐작했으면 그 모듈 Epic 을 앞에 둔다. 여러 Task 가 서로 다른 Epic 에 붙는 것이
    정상이므로 다른 모듈 Epic 도 뒤에 남긴다.
    """
    from app.agent.tools.search_tools import find_parent_epic
    rows = [r for r in (find_parent_epic.invoke({"query": "", "limit": 25}) or [])
            if isinstance(r, dict) and r.get("key") and not r.get("error")]
    mod = (state or {}).get("module") or ""
    if mod:
        rows.sort(key=lambda r: r.get("module") != mod)
    return rows

def _said_defaults(state) -> bool:
    """사용자가 선택 재량을 위임했나. 필수 입력 질문까지 끄는 신호는 아니다."""
    said = conversation(state)
    return any(w in said for w in ("알아서", "기본값", "맡길게", "맡기겠", "네가 정해", "아무거나"))


def _question_requires_input(question) -> bool:
    """질문이 없으면 유효하고 사실적인 action/payload를 만들 수 없는가."""
    return (isinstance(question, dict)
            and question.get("required_input") is True
            and bool(str(question.get("why_required") or "").strip()))


_COMPOSITE_SIGNALS = (
    "각각", "여러", "나눠", "쪼개", "단계", "사람 나눠", "그리고", "동시에", "별도",
    "설계", "구현", "검증", "배포", "모니터링", "파이프라인", "구축", "마이그레이션",
    "에픽으로", "epic으로", "sub-task", "서브태스크",
)


def _simple_delegated_request(state) -> bool:
    """명시적 단일 산출물 + 위임 요청인가. 과분해를 막는 보수적 판정."""
    req = request_text(state).strip()
    if not req or len(req) > 150 or not _said_defaults(state):
        return False
    if shape_hint(state)[0] or any(w.lower() in req.lower() for w in _COMPOSITE_SIGNALS):
        return False
    if _re.search(r"(?:두|세|네|다섯|\d+)\s*(?:가지|건|개)|"
                  r"(?:하고|하며|및|뿐만\s*아니라)|할\s*일\s*(?:뽑|정리)", req):
        return False
    # 문장 안의 열거·복수 산출물도 제외한다. 쉼표 하나는 속성 나열일 수 있어 두 개부터 본다.
    if req.count(",") >= 2 or req.count("·") >= 2:
        return False
    # 단지 짧고 "알아서"라고 한 것만으로는 단일 산출물이 아니다. 하나라는 표식 또는
    # 작은 국소 변경 동사가 있어야 한다. "관련 정리 좀" 같은 모호한 요청은 기존 구조
    # 판단에 맡긴다.
    return bool(_re.search(r"하나(?:만)?|한\s*(?:건|개)|"
                           r"(?:추가|수정|변경|노출|숨김|삭제|토글)(?:해|하|\s|줘|$)", req))


_SEMANTIC_STOP = {
    "task", "story", "bug", "feature", "improvement", "티켓", "작업", "업무", "추가",
    "진행", "수행", "작성", "적용", "개선", "조정", "최적화", "구현", "개발", "검증",
    "workbench", "catalog", "runtime", "dataops", "etl", "quality", "module",
}


def _semantic_terms(text: str) -> set:
    clean = _re.sub(r"^\s*\[[^\]]+\]\s*", "", str(text or ""))
    words = _re.findall(r"[A-Za-z0-9_.-]{2,}|[가-힣]{2,}", clean.lower())
    return {w for w in words if w not in _SEMANTIC_STOP and not w.isdigit()}


def _best_item_for_request(state, items: list) -> dict:
    """과분해를 접을 때 원 요청과 가장 가까운 항목을 보존한다."""
    req_terms = _semantic_terms(request_text(state))
    try:
        from app.infra.settings import modules_in_text, resolve_module
        wanted = set(modules_in_text(request_text(state)))
    except Exception:
        wanted, resolve_module = set(), lambda x: ""

    def score(it):
        terms = _semantic_terms(it.get("summary") or "")
        comp = resolve_module(((it.get("components") or [""])[0]))
        return (len(req_terms & terms) * 3 + (4 if comp and comp in wanted else 0),
                len(terms), -items.index(it))
    return max(items, key=score)


def _explicit_parent_epic(state) -> str:
    """사용자가 키와 Epic/상위 관계를 직접 말한 경우만 반환한다."""
    said = request_text(state) + " " + last_user_text(state)
    relation = bool(_re.search(r"에픽|epic|아래|밑에|상위", said, _re.I))
    if not relation:
        return ""
    for key in state.get("mentioned_keys") or []:
        if str(key) in said and _is_epic(key):
            return str(key)
    return ""


def _epic_summary(key: str) -> str:
    try:
        from app.agent.tools._ctx import client
        t = client().get_issue(str(key or "")) or {}
        return str((t.get("fields") or {}).get("summary") or t.get("summary") or "")
    except Exception:
        return ""


def _inferred_epic_rejection(state, item: dict, key: str) -> str:
    """모델이 추론한 Epic을 연결하면 안 되는 결정적 사유. 빈 문자열이면 허용."""
    try:
        from app.agent.tools._ctx import settings
        write_project = str(settings().project_key or "").upper()
    except Exception:
        write_project = ""
    if write_project and str(key).split("-", 1)[0].upper() != write_project:
        return f"write project {write_project} 밖의 Epic이다"
    em = _epic_module(key)
    comps = [str(c) for c in (item.get("components") or []) if str(c).strip()]
    if em and comps and em != comps[0]:
        return f"{em} 모듈 Epic과 {comps[0]} 컴포넌트가 다르다"
    title = _epic_summary(key)
    if title:
        epic_terms = _semantic_terms(title)
        work_terms = _semantic_terms(str(item.get("summary") or "") + " " + request_text(state))
        # 모듈명·'작업/개선' 같은 공통어를 제거한 뒤 업무 고유어가 하나도 겹치지 않으면
        # 관련성은 확인되지 않은 것이다. 최상위 Task는 되돌리기 쉬우나 잘못된 Epic 집계는
        # 조용히 장기간 오염된다.
        overlap = epic_terms & work_terms
        if epic_terms and work_terms and (len(overlap) < 2
                                          and not any(len(x) >= 6 for x in overlap)):
            return "업무 고유어가 Epic 제목과 겹치지 않는다"
    return ""


def _dedupe_semantic_items(state, items: list) -> list:
    """행동어만 다른 동일 산출물 Task를 하나로 접고 제거된 제목을 반환한다."""
    try:
        from app.infra.settings import modules_in_text, resolve_module
    except Exception:
        modules_in_text, resolve_module = lambda _: [], lambda _: ""
    groups = []
    for it in list(items):
        terms = _semantic_terms(it.get("summary") or "")
        if len(terms) < 2:
            groups.append([(it, terms)])
            continue
        target = None
        for group in groups:
            base = group[0][1]
            union = terms | base
            if terms == base or (len(terms & base) >= 2 and union
                                 and len(terms & base) / len(union) >= 0.67):
                target = group
                break
        if target is None:
            groups.append([(it, terms)])
        else:
            target.append((it, terms))

    removed = []
    keep = []
    for group in groups:
        # 위의 새 그룹에는 최초 항목 한 개, 기존 그룹에는 append된 항목들이 들어 있다.
        if len(group) == 1:
            keep.append(group[0][0])
            continue
        # alias 표는 "쿼리 엔진"처럼 어순을 가진 구문이다. set을 정렬하면 "엔진 … 쿼리"가
        # 되어 매핑이 깨지므로 대표 제목의 원래 어순으로 판정한다.
        core_text = _re.sub(r"^\s*\[[^\]]+\]\s*", "",
                            str(group[0][0].get("summary") or ""))
        wanted = set(modules_in_text(core_text))

        def score(pair):
            it, terms = pair
            comp = resolve_module(((it.get("components") or [""])[0]))
            return (5 if comp and comp in wanted else 0,
                    len(_semantic_terms(request_text(state)) & terms))
        chosen = max(group, key=score)[0]
        keep.append(chosen)
        removed.extend(str(it.get("summary") or "") for it, _ in group if it is not chosen)
    if removed:
        items[:] = [it for it in items if it in keep]
    return removed


_PLACEHOLDER_BODY = _re.compile(
    r"설명(?:해|하여)\s*주|적어\s*주|작성해\s*주|구체적으로\s*(?:적|작성)|"
    r"명확한\s*완료\s*기준\s*필요|\(미정\)|"
    r"\[?기입\s*필요\]?|설명하는\s*문장이\s*필요|필요한\s*이유[^<]{0,30}설명|"
    r"관련(?:된)?\s*(?:사건|요청)[^<]{0,30}명시|구체적인\s*검증\s*방법[^<]{0,30}추가|"
    r"명시해야\s*(?:합니다|한다)|추가\s*확인이\s*필요|"
    r"왜\s*이\s*일이\s*필요한지[^<]{0,30}설명[^<]{0,15}필요|"
    r"왜\s*이\s*작업이\s*필요한지[^<]{0,30}설명[^<]{0,15}필요|"
    r"작업의\s*필요성[^<]{0,30}설명(?:해야|해\s*주)|"
    r"왜\s*이\s*일이\s*필요한지\s*\d*\s*[~～-]?\s*\d*\s*문장|"
    r"계기가\s*된\s*(?:사건|요청|장애)[^<]{0,50}(?:함께|추가)|"
    r"포함\s*:\s*이번에\s*하는\s*것|제외\s*:\s*이번에\s*하지\s*않는\s*것|"
    r"검증\s*가능한\s*조건\s*\d+|내용\s*작성\s*필요|"
    r"필요한\s*이유를\s*사용자에게\s*확인|"
    r"사용자에게[^<]{0,35}(?:물어보|확인\s*필요)|사용자와\s*협의\s*필요|"
    r"포함\s*:\s*사용자에게\s*확인\s*필요|제외\s*:\s*사용자에게\s*확인\s*필요|"
    r"티켓\s*키[^<]{0,30}추가\s*해?\s*주|"
    r"명확한\s*완료\s*(?:기준|조건)(?:이)?[^<]{0,15}필요|TODO|TBD",
    _re.I)


def _has_placeholder_body(body) -> bool:
    return bool(_PLACEHOLDER_BODY.search(str(body or "")))


def _has_lineage_game_drift(state, item: dict) -> bool:
    """데이터 리니지를 게임 `Lineage` 서사로 해석한 명백한 sense drift를 잡는다."""
    req = request_text(state)
    if "리니지" not in req:
        return False
    game_terms = ("게임", "플레이어", "캐릭터", "클라이맥스", "결말", "몰입감")
    if any(w in req for w in game_terms):
        return False                       # 사용자가 실제 게임 문맥을 말했다
    body = str((item or {}).get("description") or "")
    return sum(1 for w in game_terms if w in body) >= 2


def _minimal_grounded_body(item: dict) -> str:
    """작성 지시문을 사실을 꾸미지 않는 최소 실행 본문으로 교체한다."""
    summary = _re.sub(r"^\s*\[[^\]]+\]\s*", "", str(item.get("summary") or "작업")).strip()
    old = str(item.get("description") or "")
    refs = ""
    m = _re.search(r"<h3>\s*참고[^<]*</h3>\s*<ul[^>]*>.*?</ul>", old,
                   _re.S | _re.I)
    if m:
        refs = m.group(0)
    safe = _esc(summary)
    if "리니지" in summary and "홉" in summary:
        dod = (f"{summary} 결과가 기대한 업스트림·다운스트림 경로와 일치함을 "
               "조회 테스트 결과로 확인한다.")
    elif "품질" in summary and ("룰" in summary or "점검" in summary):
        dod = ("점검 대상 품질 룰과 항목별 통과 기준은 담당팀 확인 필요 — "
               "확정된 기준별 점검 결과를 티켓에 기록한다.")
    else:
        dod = "요청한 작업이 반영되고 검증 결과가 티켓에 기록되었음을 리뷰로 확인한다."
    return (f"<h3>배경</h3><p>{safe} 요청을 실행 가능한 작업으로 관리한다.</p>"
            f"<h3>작업 범위</h3><ul><li>포함: {safe}</li>"
            "<li>제외: 요청에 명시되지 않은 연관 기능 변경</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            f"<li data-checked=\"false\">{_esc(dod)}</li></ul>" + refs)


def _preserve_existing_parent_topic(items: list) -> bool:
    """기존 Task 아래 top-level Sub-Task 제목에도 부모 업무 주제를 보존한다."""
    changed, cache = False, {}
    for item in items:
        if not isinstance(item, dict) or not str(item.get("type") or "").lower().startswith("sub"):
            continue
        parent = str(item.get("parent") or "")
        if not parent:
            continue
        if parent not in cache:
            try:
                from app.agent.tools._ctx import client
                issue = client().get_issue(parent) or {}
                cache[parent] = str((issue.get("fields") or {}).get("summary") or "")
            except Exception:
                cache[parent] = ""
        parent_title = cache[parent]
        if not parent_title:
            continue
        module = ""
        m = _re.match(r"^\s*\[([^]]+)\]\s*", parent_title)
        if m:
            module = m.group(1).strip()
        subject = _re.sub(r"^\s*\[[^]]+\]\s*", "", parent_title)
        subject = _re.sub(r"\s*(?:1차|2차)?\s*(?:오픈|개발|구현|작업)\s*$", "", subject).strip()
        child = _re.sub(r"^\s*\[[^]]+\]\s*", "", str(item.get("summary") or "")).strip()
        if subject and subject not in child:
            item["summary"] = (f"[{module}] {subject} {child}" if module
                               else f"{subject} {child}").strip()
            changed = True
    return changed


def _recover_explicit_subtasks(state) -> list:
    """기존 Task와 2개 이상의 자식 이름을 명시한 요청의 빈 model output을 복원한다."""
    req = request_text(state)
    if not _asks_subtasks(state):
        return []
    parents = [str(k) for k in (state.get("mentioned_keys") or [])
               if _can_parent_subtask(k)]
    if not parents:
        return []
    m = _re.search(r"(?:에|아래|밑에)\s*(.+?)\s*(?:서브\s*태스크|sub-?task)", req, _re.I)
    if not m:
        return []
    names = [_re.sub(r"^(?:각각|추가로)\s*|\s*(?:작업|항목)$", "", x).strip(" .")
             for x in _re.split(r"\s*(?:이랑|랑|와|과|및|,)\s*", m.group(1))]
    names = [x for x in names if len(x) >= 2 and not _re.fullmatch(r"\d+개", x)]
    if not 2 <= len(names) <= 6:
        return []
    parent = parents[0]
    component = ""
    try:
        from app.agent.tools._ctx import client
        fields = (client().get_issue(parent) or {}).get("fields") or {}
        component = str(next((c.get("name") for c in (fields.get("components") or [])
                              if isinstance(c, dict) and c.get("name")), ""))
    except Exception:
        component = ""
    out = []
    for name in names:
        title = name
        if any(w in name for w in ("성능", "테스트", "검증")) and not _re.search(r"수행|실행|완료$", name):
            title += " 수행"
        summary = f"[{component}] {title}" if component else title
        if any(w in name for w in ("성능", "측정", "검증", "테스트")):
            dod = "검증 기준·측정값·판정 결과가 parent ticket에 기록되어 검토 가능함"
        elif any(w in name for w in ("가이드", "문서", "작성")):
            dod = "산출물 링크와 리뷰 결과가 parent ticket에 기록되어 검토 완료됨"
        else:
            dod = "산출물과 검토 결과가 parent ticket에 기록되어 완료 여부를 확인할 수 있음"
        body = (f"<h3>작업 범위</h3><ul><li>{_esc(name)}</li></ul>"
                "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                f"<li data-checked=\"false\">{_esc(dod)}</li></ul>")
        item = {"summary": summary, "type": "Sub-Task", "parent": parent,
                "description": body, "priority": "P3-Minor"}
        if component:
            item["components"] = [component]
        out.append(item)
    return out


def _ticket_exists(key) -> bool:
    k = str(key or "").strip()
    if not k:
        return False
    try:
        from app.agent.tools._ctx import client
        return bool((client().get_issue(k) or {}).get("key"))
    except Exception:
        return False


def _enforce_agreed_structure(state, items: list) -> bool:
    """사용자가 승인한 structure_plan의 제목·순서·module·children을 복원한다."""
    plan = [p for p in (state.get("structure_plan") or []) if isinstance(p, dict)]
    if not plan or not (state.get("structure_ok") or structure_accepted(state)):
        return False
    pool = [dict(i) for i in items if isinstance(i, dict)]
    fixed, changed = [], False
    for p in plan:
        summary = str(p.get("summary") or "").strip()
        match = next((i for i in pool if str(i.get("summary") or "").strip() == summary), None)
        if match is None and pool:
            want = _semantic_terms(summary)
            match = max(pool, key=lambda i: len(want & _semantic_terms(i.get("summary") or "")))
        row = dict(match or {})
        if match in pool:
            pool.remove(match)
        changed = changed or row.get("summary") != summary
        row["summary"] = summary
        row["type"] = str(p.get("type") or row.get("type") or "Task")
        if p.get("components"):
            comps = [str(c) for c in p.get("components") if str(c).strip()]
            changed = changed or row.get("components") != comps
            row["components"] = comps
        planned_children = [str(c).strip() for c in (p.get("children") or []) if str(c).strip()]
        old_children = [dict(c) for c in (row.get("children") or []) if isinstance(c, dict)]
        if planned_children:
            kids = []
            for title in planned_children:
                kid = next((c for c in old_children
                            if str(c.get("summary") or "").strip() == title), None)
                child = dict(kid or {"type": "Sub-Task"})
                child["summary"] = title
                kids.append(child)
            row["children"] = kids
        else:
            changed = changed or bool(row.get("children"))
            row.pop("children", None)
        fixed.append(row)
    changed = changed or len(items) != len(fixed)
    items[:] = fixed
    return changed


def _align_modules_from_summary(items: list) -> bool:
    """summary 본문에 alias가 하나면 component와 `[Module]` prefix를 그 값으로 맞춘다."""
    try:
        from app.infra.settings import modules_in_text
    except Exception:
        return False
    changed = False
    for it in items:
        summary = str(it.get("summary") or "").strip()
        subject = _re.sub(r"^\s*\[[^]]+\]\s*", "", summary)
        mods = modules_in_text(subject)
        if len(mods) != 1:
            continue
        mod = mods[0]
        comps = [str(c) for c in (it.get("components") or []) if str(c).strip()]
        fresh_summary = f"[{mod}] {subject}" if subject else summary
        if comps != [mod] or fresh_summary != summary:
            it["components"] = [mod]
            it["summary"] = fresh_summary
            changed = True
    return changed


def _scope_html(body: str) -> str:
    m = _re.search(r"<h3>\s*작업 범위\s*</h3>\s*<ul[^>]*>(.*?)</ul>",
                   str(body or ""), _re.S | _re.I)
    return m.group(1) if m else ""


def _split_body(item: dict, siblings: list[dict]) -> str:
    """합의된 summary와 sibling 경계만으로 만드는 보수적인 Task 본문."""
    own = _re.sub(r"^\s*\[[^]]+\]\s*", "", str(item.get("summary") or "")).strip()
    safe = _esc(own or str(item.get("summary") or "작업"))
    excludes = "".join(
        f"<li>제외(별도 ticket): {_esc(str(s.get('summary') or ''))}</li>"
        for s in siblings[:4] if str(s.get("summary") or "").strip())
    return (f"<h3>배경</h3><p>{safe} 요청을 독립된 산출물과 완료 기준으로 관리한다.</p>"
            f"<h3>작업 범위</h3><ul><li>포함: {safe}</li>{excludes}</ul>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            f"<li data-checked=\"false\">{safe} 결과가 반영되었음을 담당 리뷰로 확인한다.</li>"
            "<li data-checked=\"false\">검증 결과와 남은 제약을 ticket에 기록한다.</li></ul>")


def _repair_split_scope(items: list) -> list[str]:
    """작업 범위가 sibling의 고유 deliverable을 2개 이상 포함하면 본문 복사를 되돌린다."""
    terms = [_semantic_terms(i.get("summary") or "") for i in items]
    counts = {}
    for ts in terms:
        for t in ts:
            counts[t] = counts.get(t, 0) + 1
    signatures = [{t for t in ts if counts.get(t) == 1} for ts in terms]
    repaired = []
    for idx, it in enumerate(items):
        scope_terms = _semantic_terms(_re.sub(r"<[^>]+>", " ", _scope_html(it.get("description") or "")))
        invades = any(len(scope_terms & signatures[j]) >= 2
                      for j in range(len(items)) if j != idx)
        if invades:
            siblings = [s for j, s in enumerate(items) if j != idx]
            it["description"] = _split_body(it, siblings)
            repaired.append(str(it.get("summary") or ""))
    return repaired


def _ensure_split_exclusions(items: list) -> None:
    """분리된 Task의 작업 범위에 sibling이 별도 ticket임을 기록한다."""
    for idx, it in enumerate(items):
        body = str(it.get("description") or "")
        scope = _scope_html(body)
        if not scope or _re.search(r"제외|하지\s*않", scope):
            continue
        siblings = [s for j, s in enumerate(items) if j != idx]
        extra = "".join(
            f"<li>제외(별도 ticket): {_esc(str(s.get('summary') or ''))}</li>"
            for s in siblings[:4] if str(s.get("summary") or "").strip())
        if not extra:
            continue
        m = _re.search(r"(<h3>\s*작업 범위\s*</h3>\s*<ul[^>]*>)(.*?)(</ul>)",
                       body, _re.S | _re.I)
        if m:
            it["description"] = body[:m.end(2)] + extra + body[m.end(2):]


def _is_epic(key) -> bool:
    """그 티켓이 Epic 인가. 부모로 지목된 것이 Epic 이면 **버릴 게 아니라 Epic Link 로 옮긴다**
    — 사용자는 "저 밑에서 진행하자"고 말한 것이고, Epic 밑에 Task 를 다는 것은 정상이다."""
    k = str(key or "").strip()
    if not k:
        return False
    try:
        from app.agent.tools._ctx import client
        t = client().get_issue(k) or {}
        kind = str((t.get("fields") or {}).get("issuetype", {}).get("name")
                   or t.get("issuetype") or t.get("type") or "")
        return bool(t.get("key")) and "epic" in kind.lower()
    except Exception:
        return False


def _can_parent_subtask(key) -> bool:
    """그 티켓이 **Sub-Task 의 부모가 될 수 있나** — 실재하는 Task tier여야 한다.

    실재 여부만 보던 자리들이 있었는데, Jira 에서 **Epic 밑에는 Sub-Task 를 못 단다**
    (Epic 의 자식은 Story/Task 다). 실측 STR1: 모델이 Epic DL-5982 를 부모로 지목한
    Sub-Task 10건을 냈고 — 답변에서 스스로 "Epic이라 부모로 적합하지 않다"고 적으면서도
    초안에는 그대로 실었다. 실재 검사는 통과하니 강등 가드도 안 걸렸다.
    같은 규칙을 BULK3 케이스에서 이미 확인했다(그때는 **케이스가** 틀렸었다 — §8).
    """
    k = str(key or "").strip()
    if not k:
        return False
    try:
        from app.agent.tools._ctx import client
        t = (client().get_issue(k) or {})
        if not t.get("key"):
            return False
        fields = t.get("fields") or {}
        issue_type = fields.get("issuetype") or {}
        kind = str((issue_type.get("name") if isinstance(issue_type, dict) else issue_type)
                   or t.get("issuetype") or t.get("type") or "")
        flag = issue_type.get("subtask") if isinstance(issue_type, dict) else None
        from app.domain.ticket_actions import TIER_TASK, issue_tier
        return issue_tier(kind, flag) == TIER_TASK
    except Exception:
        return False


def _ticket_kind(key) -> str:
    """사람에게 보일 이슈 타입. 계층 오류 설명용이며 실패하면 빈 문자열."""
    try:
        from app.agent.tools._ctx import client
        t = client().get_issue(str(key or "").strip()) or {}
        issue_type = (t.get("fields") or {}).get("issuetype") or {}
        return str((issue_type.get("name") if isinstance(issue_type, dict) else issue_type)
                   or t.get("issuetype") or t.get("type") or "").strip()
    except Exception:
        return ""


# UI 회귀 픽스처 전용 모듈 — **실제 티켓에 붙으면 안 된다.** 개발 world 에만 있고 prod
# config 에는 없다. 실사용 사고: "외부 환경에서의 feasibility test" 라는 제목의 '**test**'
# 가 컴포넌트 이름 'TEST' 와 부분 일치해 모듈이 TEST 로 잡혔고, 담당까지 픽스처 계정
# (test.ui02)으로 제안됐다. 사람이 보면 바로 아는 오류지만 초안은 멀쩡해 보인다.
_FIXTURE_COMPONENTS = {"TEST"}



def _bulk_comment_preview(keys: list, body: str) -> list:
    """일괄 코멘트의 **티켓별 미리보기** — [{key, title, assignee, body}].

    `[~담당자]` 자리표시자가 있으면 그 티켓의 실제 담당으로 바꿔 준다. 담당이 없으면
    멘션을 빼고 문장을 살린다(존재하지 않는 사람을 멘션하면 알림이 안 가고 링크만 깨진다).
    """
    if not body:
        return []
    rows = []
    for k in keys[:30]:
        try:
            f = (_client_issue(k) or {}).get("fields") or {}
        except Exception:
            f = {}
        who = ((f.get("assignee") or {}).get("name") or "").strip()
        # ★ **멘션은 티켓마다 그 티켓의 담당이어야 한다.** 모델은 대상 담당자를 전부 모아
        #   한 문장에 넣는다(실측 CMTB1: 두 티켓의 코멘트가 똑같이
        #   "[~skcc.x1042] [~skcc.i2011]" 였다) — 그러면 남의 티켓 알림이 모두에게 간다.
        #   그래서 본문의 멘션을 **전부 걷어내고** 이 티켓의 담당만 앞에 붙인다.
        text = _re.sub(r"\[~[^\]]*\]", "", body)
        text = _re.sub(r"\s{2,}", " ", text).strip(" ,·")
        if who:
            text = f"[~{who}] " + text        # 담당자에게 알리는 것이 이 코멘트의 목적이다
        rows.append({"key": k, "title": str(f.get("summary") or ""),
                     "assignee": who, "body": text})
    return rows


def _client_issue(key: str):
    from app.agent.tools._ctx import client
    return client().get_issue(key)


def _known_components() -> set:
    try:
        from app.agent.tools.write_tools import list_ticket_options
        got = {str(x) for x in ((list_ticket_options.invoke({"kind": "components"}) or {})
                                .get("components") or [])}
        return {c for c in got if c not in _FIXTURE_COMPONENTS}
    except Exception:
        return set()


def _existing_epic_like(summary: str):
    """제목이 사실상 같은 Epic 이 이미 있나 — 모듈 접두와 조사를 걷어내고 비교한다."""
    base = _re.sub(r"^\s*\[[^\]]+\]\s*", "", str(summary or "")).strip()
    key_words = [w for w in _re.split(r"\s+", base) if len(w) >= 2]
    if not key_words:
        return None
    try:
        from app.agent.tools.search_tools import find_parent_epic
        for r in (find_parent_epic.invoke({"query": "", "limit": 25}) or []):
            if not isinstance(r, dict) or not r.get("key"):
                continue
            other = _re.sub(r"^\s*\[[^\]]+\]\s*", "", str(r.get("summary") or "")).strip()
            if not other:
                continue
            # 낱말이 전부 겹치면 같은 이름으로 본다("쿼리 성능 개선" ↔ "[ETL] 쿼리 성능 개선")
            if all(w in other for w in key_words) or all(w in base for w in other.split()):
                return r
    except Exception:
        pass
    return None


def _pick_parent_epic(summary: str):
    """이 일을 담을 만한 **기존 Epic** 하나 — 낱말이 가장 많이 겹치는 것. 없으면 None.

    `_existing_epic_like` 는 "이름이 사실상 같은가"를 보고(중복 격상 방지), 이쪽은
    "담을 데가 있나"를 본다. 겹치는 낱말이 하나도 없으면 고르지 않는다 — 아무 Epic 에나
    넣으면 그 Epic 의 진척률이 남의 일로 흐려진다.
    """
    base = _re.sub(r"^\s*\[[^\]]+\]\s*", "", str(summary or "")).strip()
    words = [w for w in _re.split(r"[\s·,/]+", base) if len(w) >= 2]
    if not words:
        return None
    best, score = None, 0
    try:
        from app.agent.tools.search_tools import find_parent_epic
        for r in (find_parent_epic.invoke({"query": "", "limit": 25}) or []):
            if not isinstance(r, dict) or not r.get("key"):
                continue
            other = str(r.get("summary") or "")
            n = sum(1 for w in words if w in other)
            if n > score:
                best, score = r, n
    except Exception:
        return None
    return best if score else None


def _asks_subtasks(state) -> bool:
    """"서브태스크로 쪼개줘 / 하위 작업 추가해줘" 처럼 **자식을 붙여 달라**는 요청인가."""
    said = last_user_text(state)
    return any(w in said for w in ("서브태스크", "서브 태스크", "subtask", "sub-task",
                                   "하위 작업", "하위작업", "쪼개", "나눠서 붙"))


def _explicit_parentless_subtask(state) -> bool:
    """Sub-Task 요청과 부모 부재가 모두 사용자 문장에 명시됐는지 판별한다."""
    said = (request_text(state) + " " + last_user_text(state)).replace(" ", "")
    return _asks_subtasks(state) and bool(_re.search(
        r"(?:부모(?:는|가|티켓은|티켓이)?(?:없|없이|필요없)|최상위(?:로|에))", said))


# ── 사용자가 형태를 말했나, 열려 있나 ───────────────────────────────
# 같은 "만들어줘"라도 둘은 완전히 다른 상황이다. 형태를 말했으면 그대로 따르는 것이 맞고,
# 열려 있으면 우리가 판단하되 **갈림이 크면 확인을 받는** 것이 맞다. 이 판정을 모델에게
# 맡기면 흔들리므로(같은 문장에 다른 답), 낱말로 하는 판정은 코드가 한다.
_SHAPE_WORDS = (
    ("new_epic", ("에픽으로", "epic 으로", "에픽 만들", "에픽으로 크게", "이니셔티브")),
    # 새 일의 "단계별 Sub-Task"는 **Task 하나 + children**이다. 기존 코드에서는 아래의
    # generic `subtask`에 먼저 걸려, 사용자가 구조를 말했는데도 single_task 산출을 고치지
    # 못했다. 기존 부모 키를 지목한 경우는 shape_hint 의 선행 분기가 mode=subtask 로 본다.
    ("task_with_subtasks", ("단계별 서브태스크", "단계별 서브 태스크",
                            "단계별 sub-task", "단계별 subtask", "하위 작업으로 나눠",
                            "하위작업으로 나눠", "단계로 쪼개", "단계별로 쪼개")),
    ("subtask", ("서브태스크", "서브 태스크", "sub-task", "subtask", "하위 작업", "하위작업",
                 "쪼개", "분할")),
    # ★ "사람 나눠서 진행하게" 도 **형태를 말한 것**이다 — 낱말이 "나눠서 만들" 하나뿐이라
    #   이 표현을 못 알아듣고 구조 확인을 다시 물었다(실측 STR1). 사용자가 이미 말한 것을
    #   되묻는 것은 취조다.
    ("multiple_tasks", ("각각 티켓", "티켓 여러", "테스크 여러", "따로따로", "나눠서 만들",
                        "나눠서 진행", "사람 나눠", "담당 나눠", "나눠 맡")),
    ("single_task", ("하나만", "한 건만", "티켓 하나", "테스크 하나", "단일")),
)


def shape_hint(state) -> tuple:
    """(사용자가 말한 형태 | "", 근거 낱말). 말하지 않았으면 열려 있는 것이다."""
    said = last_user_text(state)
    said_l = said.lower()
    # 실재/지목 부모 아래에 붙이는 요청은 새 Task+children 구조가 아니라 Sub-Task 배치다.
    # "DL-9090을 단계별로 쪼개줘"도 이쪽이므로 구체적인 새-일 패턴보다 먼저 판정한다.
    if _re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", said, _re.I) and any(
            w in said_l for w in ("서브태스크", "서브 태스크", "sub-task", "subtask",
                                  "하위 작업", "하위작업", "쪼개", "분할")):
        return "subtask", "기존 부모 키"
    # `테이블 30개를 사람 나눠서`는 독립 산출물 30개가 아니라 **한 목표의 분량 분할**이다.
    # 따라서 여러 Task가 아니라 부모 Task 하나 + roster별 Sub-Task로 해석한다. 숫자 없는
    # `사람 나눠서 진행`은 기능별 독립 산출물일 수도 있어 기존 multiple_tasks 규칙에 맡긴다.
    amount = _re.search(r"[2-9][0-9]{0,3}\s*(?:개|건)", said)
    split_word = next((w for w in ("사람 나눠", "담당 나눠", "나눠 맡", "나눠서 진행")
                       if w in said_l), "")
    if amount and split_word:
        return "task_with_subtasks", f"{amount.group(0)} · {split_word}"
    # issue type을 단수로 지목해 "Task/Story/Bug 만들어줘"라고 한 것은 형태 지정이다.
    # 복수·분할 신호가 함께 있을 때만 아래의 더 구체적인 규칙에 맡긴다.
    if (_re.search(r"(?<![A-Za-z])(?:task|story|bug|태스크|테스크)\s*(?:로\s*)?"
                   r"(?:만들|생성|등록|올려)", said_l, _re.I)
            and not any(w in said_l for w in
                        ("여러", "각각", "단계", "서브", "sub-task", "나눠", "쪼개"))):
        return "single_task", "단수 issue type 생성 요청"
    for kind, words in _SHAPE_WORDS:
        for w in words:
            if w.lower() in said_l:
                return kind, w
    return "", ""


_SHAPE_LABEL = {"single_task": "티켓 하나로", "task_with_subtasks": "Task 하나 + Sub-Task 로 나눠서",
                "multiple_tasks": "Task 여러 개로", "new_epic": "새 Epic 으로 크게"}


def _shape_question(structure, items) -> str:
    n = len(items)
    kids = sum(len(i.get("children") or []) for i in items)
    made = (f"Task {n}건" + (f" + Sub-Task {kids}건" if kids else "")) if n else "초안"
    return (f"이렇게 만들면 {made} 입니다({_SHAPE_LABEL.get(structure, structure)}). "
            "이 형태로 진행할까요?")


def _shape_options(structure) -> list:
    """추천(지금 구조)을 맨 앞에, 나머지 갈래를 뒤에 — 사용자가 한 번에 고를 수 있게."""
    order = [structure] + [k for k in ("single_task", "task_with_subtasks",
                                       "multiple_tasks", "new_epic") if k != structure]
    tail = {"single_task": "티켓 하나로 (쪼개지 않는다)",
            "task_with_subtasks": "Task 하나 + Sub-Task 로 나눈다",
            "multiple_tasks": "Task 를 여러 개로 나눈다",
            "new_epic": "새 Epic 으로 격상한다 (보수적으로 — 2스프린트·여러 모듈일 때만)"}
    opts = [f"{tail[order[0]]} (추천 — 지금 초안이 이 형태다)"]
    opts += [tail[k] for k in order[1:3]]
    return opts


# ── 구조 합의 단계 (사용자 요청) ───────────────────────────────────────────
# 왜 나누나: 복합 산출물을 **본문까지 다 써서** 한 번에 내밀면, 구조가 틀렸을 때 사용자가
# 고칠 것이 너무 많다. 티켓 넷의 배경·범위·DoD 를 다 읽고 나서야 "2번은 1번에 합쳐야지"를
# 말하게 된다 — 그 시점에 우리는 이미 본문 넷을 쓴 뒤다(왕복도 돈도 버려진다).
# 그래서 **뼈대 먼저 합의하고 살은 나중에** 붙인다.

_OK_WORDS = ("좋아", "좋습니다", "그대로", "진행", "승인", "확정", "맞아", "맞습니다",
             "이대로", "ok", "OK", "예", "네", "동의", "괜찮")
# ★ 활용형까지 적는다 — "합치"만 적어 두면 **"합쳐"가 안 걸린다**(실측: 테스트가 잡았다).
#   한국어는 어미가 바뀌므로 낱말 판정은 어간 하나로 끝나지 않는다.
_EDIT_WORDS = ("빼", "제거", "삭제", "합치", "합쳐", "합칠", "합병", "묶", "나눠", "나누",
               "쪼개", "추가", "바꿔", "바꾸", "옮겨", "옮기", "위로", "아래로",
               "이름", "제목", "지워", "없애")


def structure_feedback(state) -> str:
    """이번 턴 사용자 발화가 **뼈대에 대한 수정 요구**면 그 문장. 아니면 ""."""
    said = last_user_text(state)
    return said if any(w in said for w in _EDIT_WORDS) else ""


def structure_accepted(state) -> bool:
    """사용자가 이 뼈대로 가자고 했나 — **수정 요구가 섞여 있으면 승인이 아니다.**

    "좋아, 근데 3번은 빼줘" 를 승인으로 읽으면 사용자의 수정이 통째로 증발한다.
    """
    said = last_user_text(state)
    if not said.strip():
        return False
    if any(w in said for w in _EDIT_WORDS):
        return False
    return any(w in said for w in _OK_WORDS)


def is_composite(items) -> bool:
    """뼈대 합의가 필요한 **복합** 산출물인가.

    ★ 기준을 좁힌 이유(실측): 처음엔 "자식 2건 이상"도 복합으로 봤더니 생성 스위트가
    20/20 → **16/20** 으로 떨어졌다. "Task 만들어줘, P1, 금요일까지"(ATTR1) 같은 단순
    요청까지 구조 확인을 받아 왕복이 두 배가 됐고, 본문 없는 초안이 카드에 올라갔다.

    구조 합의가 값을 하는 자리는 **최상위가 갈릴 때**다 — 사용자가 든 예도
    "두 개의 Task + 각 Task 의 SubTask" 였다. Task 하나에 자식이 붙는 모양은 관계가
    단순해서 본문까지 함께 봐도 판단할 수 있다. 다만 자식이 **아주 많으면**(4건 이상)
    그것도 한눈에 안 들어오므로 합의 대상이다.
    """
    rows = [i for i in (items or []) if isinstance(i, dict)]
    if len(rows) > 1:
        return True
    return bool(rows) and len([c for c in (rows[0].get("children") or [])
                               if isinstance(c, dict)]) >= 4


def structure_tree(items, epic: str = "") -> str:
    """뼈대를 **눈에 보이는 나무**로. 사용자가 한눈에 보고 고칠 것을 짚을 수 있어야 한다.

    표가 아니라 나무인 이유: 사용자가 고치는 것은 값이 아니라 **관계**다(합치기·나누기·
    올리기). 들여쓰기가 그 관계를 그대로 보여 준다.
    """
    rows = [i for i in (items or []) if isinstance(i, dict)]
    out = [f"{epic} (Epic)"] if epic else []
    pad = "  " if epic else ""
    for n, it in enumerate(rows, 1):
        out.append(f"{pad}{n}. {str(it.get('summary') or '').strip()}"
                   + (f"   [{(it.get('components') or [''])[0]}]"
                      if (it.get("components") or [""])[0] else ""))
        kids = [c for c in (it.get("children") or []) if isinstance(c, dict)]
        for m, c in enumerate(kids, 1):
            tail = "└─" if m == len(kids) else "├─"
            out.append(f"{pad}   {tail} {n}-{m}. {str(c.get('summary') or '').strip()}")
    return "\n".join(out)


def structure_question(items) -> dict:
    n = len([i for i in (items or []) if isinstance(i, dict)])
    k = sum(len([c for c in (i.get("children") or []) if isinstance(c, dict)])
            for i in (items or []) if isinstance(i, dict))
    made = f"Task {n}건" + (f" · Sub-Task {k}건" if k else "")
    return {"question": f"이 구조로 진행할까요? ({made}) — 고칠 것이 있으면 그대로 "
                        "말씀해 주세요(합치기·나누기·추가·삭제·이름 변경).",
            "kind": "choice", "field": "",
            "options": ["이 구조로 진행한다", "고칠 것이 있다 (아래에 적는다)"]}
