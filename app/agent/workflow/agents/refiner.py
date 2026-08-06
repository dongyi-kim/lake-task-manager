"""Refiner — 막연한 요구를 실행 가능한 티켓 트리 초안으로 만든다. 모자라면 **되묻는다**.

이 에이전트의 어려운 점은 "만들기"가 아니라 **"언제 묻고 언제 만들 것인가"**다.
다 물어보면 취조가 되고, 안 물어보면 엉뚱한 걸 만든다. 기준은 하나다:

  **찾아보면 아는 것은 묻지 않는다. 사용자만 아는 것만 묻는다.**

관련 티켓·이전 담당자·모듈 인원·가능한 컴포넌트 목록은 도구로 확인할 수 있다. 반면 범위
("어디까지가 이번 일인가")·완료 조건·기한·의도는 사용자 머릿속에만 있다. 그것만 묻는다.

ToolAgent 인 이유는 **컴포넌트·타입·라벨을 지어내지 않기 위해서**다. 없는 컴포넌트를 적으면
Reviewer 에서 튕기고 왕복이 한 번 늘어난다. 만들기 전에 실제 목록을 보는 편이 싸다.
쪼개는 기준(SP 8 초과면 쪼갠다, 조사 단계는 과잉 분해하지 않는다)은 `search_rules` 로 읽는다.
"""

from __future__ import annotations

import json

from app.agent.workflow.agents.base import ToolAgent
from app.agent.workflow.prompts import data_block, persona, wrap_data
from app.agent.workflow.state import (MAX_REFINE_TURNS, AgentState, Intent, Node,
                                      conversation, last_user_text, note)

ITEM = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "동사로 끝나는 제목. 제목만으로 구분되어야 한다"},
        "type": {"type": "string", "description": "Task/Story/Bug/Improvement/Sub-Task 중 실제 허용된 값"},
        "epic": {"type": "string", "description": "task 모드에서 상위 Epic 키. 최상위로 둘 거면 빈 문자열"},
        "parent": {"type": "string", "description": "subtask 모드에서 부모 티켓 키"},
        "description": {"type": "string", "description": "왜 하는지(배경) + 완료 조건 + 관련 티켓 키"},
        "components": {"type": "array", "items": {"type": "string"}},
        "labels": {"type": "array", "items": {"type": "string"}},
        "priority": {"type": "string"},
        "duedate": {"type": "string", "description": "YYYY-MM-DD. 모르면 빈 문자열 — 지어내지 마라"},
    },
    "required": ["summary", "type"],
}

SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array", "items": {"type": "string"},
            "description": ("사용자에게 되물을 것. **사용자만 아는 것**만(범위·완료조건·기한·의도). "
                            "찾아보면 아는 것은 넣지 마라. 물을 게 없으면 빈 배열. 최대 3개"),
        },
        "mode": {"type": "string", "enum": ["task", "subtask"],
                 "description": "이번에 만들 것의 종류. Sub-Task 는 부모가 있어야 하므로 대개 먼저 task"},
        "items": {"type": "array", "items": ITEM,
                  "description": "티켓 초안. questions 가 있으면 빈 배열로 두어도 된다"},
        "rationale": {"type": "string", "description": "왜 이렇게 쪼갰는지 2~3문장. 사용자에게 보인다"},
    },
    "required": ["questions", "mode", "items"],
}


class Refiner(ToolAgent):
    name = Node.REFINER
    temperature = 0.3          # 초안은 약간의 폭이 필요하다

    @property
    def tools(self):
        from app.agent import tools as T
        return T.RULE_TOOLS + T.REVIEW_TOOLS

    def system(self, state):
        forced = (state.get("turns") or 0) >= MAX_REFINE_TURNS
        extra = ("\n\n★ 되묻기 횟수를 다 썼다. **더 묻지 말고** 아는 것만으로 초안을 만들어라. "
                 "모르는 필드는 비워 두고 rationale 에 '확인 필요'로 남긴다." if forced else "")
        return persona(state, f"""\
너는 지금 업무를 **구체화**한다. 아직 아무것도 만들지 않는다.

먼저 `search_rules` 로 쪼개는 기준과 티켓 작성 규칙을 확인하라. 그다음
`list_ticket_options` / `list_child_types` 로 **실제로 허용된 값**을 확인하라 —
컴포넌트·타입·우선순위를 지어내면 검증에서 튕긴다.

되묻기 기준:
- **찾아보면 아는 것은 묻지 않는다.** 관련 티켓·담당 이력·모듈 인원·가능한 값 목록은 네가 확인한다.
- **사용자만 아는 것만 묻는다** — 범위(어디까지가 이번 일인가), 완료 조건, 기한, 의도.
- 한 번에 최대 3개. 취조가 되면 안 된다.

쪼개는 기준:
- 아직 방식이 안 정해진 일을 실행 단위로 쪼개지 마라. **조사·설계는 Task 하나**로 두고,
  결과가 나온 뒤 실행을 쪼갠다. 지금 5개로 쪼개면 방식이 정해지는 순간 5개를 다시 만든다.
- 하나의 티켓은 담당자 한 명이 책임질 수 있어야 한다. 둘이 필요하면 쪼갠다.
- Story Point 는 넣지 않는다(Story 에만 매길 수 있고, 생성 시점엔 못 넣는다).{extra}""")

    def task(self, state):
        # 버그는 새 기능과 초안 규칙이 다르다 — 갈래를 지시문으로 가른다(Prompt Chaining 의 분기).
        if (state.get("intent") or "") == Intent.REPORT_BUG:
            goal = """버그 신고를 **Bug 티켓 초안**으로 만들어라.
- type 은 Bug. 제목은 증상을 담는다("[모듈] ~~가 ~~할 때 ~~된다").
- description 에 **재현 경로 / 기대 동작 / 실제 동작**을 나눠 적는다. 사용자가 안 준 것은
  빈 칸으로 두고 questions 로 물어라 — 재현 경로 없는 버그 티켓은 아무도 못 잡는다.
- 원인으로 의심되는 기존 티켓이 조사에서 나왔으면 description 에 키를 적어라.
- 이미 같은 증상의 Bug 가 열려 있으면 **새로 만들지 말고** questions 로 사용자 판단을 구하라.
- 버그는 대개 쪼갤 필요가 없다 — Bug 하나면 된다. Sub-Task 로 나누지 마라."""
        else:
            goal = "아래 요청을 실행 가능한 티켓 초안으로 만들어라. 정보가 모자라면 **초안 대신 질문**을 내라."
        ev = "\n".join(f"- {e.get('key','')} {e.get('title','')} — {e.get('why','')}"
                       for e in (state.get("evidence") or []))
        data = wrap_data(
            data_block("Historian 이 정리한 현재 상황", state.get("situation")),
            data_block("근거 티켓", ev),
            data_block("붙일 만한 상위 Epic", state.get("epic_candidate")),
            data_block("이미 같은 일이 있는가", "있음 — 새로 만들기 전에 사용자에게 알릴 것"
                       if state.get("already_exists") else ""))
        return f"""\
# 명령서
{goal}

## 제약조건
- 조사 결과에 없는 티켓 키·사람·날짜를 지어내지 않는다.
- description 에는 **왜 하는지(배경)** 와 **완료 조건**을 반드시 넣는다.
- 컴포넌트는 하나만. 두 모듈에 걸치면 티켓을 나눈다.
- 이미 같은 일이 진행 중이면 새로 만들지 말고 questions 로 사용자 판단을 구한다.

## 대화
{conversation(state)}

## 원문 요청
{last_user_text(state)}{data}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        qs = [q for q in (out.get("questions") or []) if str(q).strip()][:3]
        items = [i for i in (out.get("items") or []) if isinstance(i, dict) and i.get("summary")]
        turns = (state.get("turns") or 0) + 1
        # 되묻기 상한을 넘겼는데도 질문만 냈다면 질문을 버린다 — 영원히 안 끝나는 대화를 막는다.
        if qs and turns > MAX_REFINE_TURNS:
            qs = []
        draft = {"mode": out.get("mode") or "task", "items": items,
                 "rationale": out.get("rationale") or ""}
        return {"questions": qs, "draft": draft, "turns": turns,
                "trace": note(state, self.name,
                              f"질문 {len(qs)}개 · 초안 {len(items)}건" if qs or items else "초안 없음")}


def draft_text(draft: dict) -> str:
    """초안을 프롬프트/화면에 실을 수 있는 글로. Assigner·Reviewer 가 같은 걸 본다."""
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
            bits.append(f"\n    설명: {str(it['description'])[:300]}")
        rows.append("  ".join(bits))
    return f"mode={draft.get('mode')}\n" + "\n".join(rows)


def as_bulk_items(draft: dict) -> list:
    """초안 → `validate_ticket_plan` / `create_tickets` 가 받는 형태.

    `epic` 이 빈 문자열이면 **`None` 으로 바꾼다** — 규칙상 "최상위로 두겠다"는 명시가 필요하고,
    빈 문자열은 그 명시로 인정되지 않는다.
    """
    mode = (draft or {}).get("mode") or "task"
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


def draft_json(draft: dict) -> str:
    return json.dumps(as_bulk_items(draft), ensure_ascii=False, indent=1)
