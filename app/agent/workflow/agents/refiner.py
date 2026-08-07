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

from app.agent.prompts.roles import SYSTEM_REFINER
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
        "description": {
            "type": "string",
            "description": (
                "티켓 본문 — **HTML 로 작성한다**(에디터가 받는 형식. mock 은 위키로 자동 변환, "
                "prod 는 그대로 저장된다). 구조:\n"
                "<h3>배경</h3><p>왜 하는지 — 계기·관련 티켓 키(DL-123 텍스트로, 자동 링크됨)</p>\n"
                "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                "<li data-checked=\"false\">검증 가능한 조건 1</li>"
                "<li data-checked=\"false\">조건 2</li></ul>\n"
                "여러 후보·항목 비교가 필요하면 <table><tr><th>…</th></tr><tr><td>…</td></tr></table>.\n"
                "관련 문서가 있으면 <a href=\"URL\">제목</a>. "
                "일이 커서 나중에 쪼갤 거면 <h3>후속 Sub-Task 후보</h3><ul><li>…</li></ul> 를 적는다. "
                "조사에서 **알아낸 사실**(왜 멈췄었는지·이미 결정된 것·기술 비교 결론)이 있으면 "
                "<h3>Knowledge</h3><ul><li>…</li></ul> 로 남겨라 — 나중에 이 티켓을 여는 사람과 "
                "검색(RAG)이 그걸 다시 쓴다. References(관련 티켓·문서)는 자동으로 붙는다"),
        },
        "components": {"type": "array", "items": {"type": "string"}},
        "labels": {"type": "array", "items": {"type": "string"}},
        "priority": {"type": "string"},
        "duedate": {"type": "string", "description": "YYYY-MM-DD. 모르면 빈 문자열 — 지어내지 마라"},
    },
    "required": ["summary", "type"],
}

# 질문은 문자열이 아니라 **폼으로 그릴 수 있는 구조**로 받는다. "P1/P2/P3 중 뭘로 할까요?"를
# 문장으로 내면 사용자는 타이핑해야 하지만, choice+options 로 내면 버튼 하나다.
# field 를 표시하면 화면이 그 속성 전용 자동완성(담당자·Epic·우선순위)을 붙인다.
QUESTION = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "물어볼 것 한 문장"},
        "kind": {"type": "string", "enum": ["text", "choice", "date"],
                 "description": "text=자유 서술 / choice=보기 중 선택 / date=날짜"},
        "options": {"type": "array", "items": {"type": "string"},
                    "description": "kind=choice 일 때 보기 2~5개. **네가 추천하는 것을 맨 앞에** 두고, "
                                   "보기 뒤에 짧은 사유를 괄호로 붙여도 된다 — 예: "
                                   "\"P2-Major (운영 영향 있음)\", \"skcc.x1210 (유사 작업 2건)\""},
        "field": {"type": "string",
                  "enum": ["", "assignee", "epic", "priority", "duedate", "component"],
                  "description": "티켓 속성을 묻는 질문이면 그 필드명 — 화면이 전용 자동완성을 붙인다"},
    },
    "required": ["question", "kind"],
}

SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array", "items": QUESTION,
            "description": ("사용자에게 되물을 것. **사용자만 아는 것**만(범위·완료조건·기한·의도). "
                            "찾아보면 아는 것은 넣지 마라. 물을 게 없으면 빈 배열. 최대 3개"),
        },
        "mode": {"type": "string", "enum": ["task", "subtask"],
                 "description": "이번에 만들 것의 종류. Sub-Task 는 부모가 있어야 하므로 대개 먼저 task"},
        "items": {"type": "array", "items": ITEM,
                  "description": "티켓 초안. questions 가 있으면 빈 배열로 두어도 된다"},
        "change": {
            "type": "object",
            "description": "**modify 의도일 때만** — 기존 티켓의 변경 계획. 이때 items 는 빈 배열",
            "properties": {
                "key": {"type": "string", "description": "바꿀 티켓 키. 조사에서 실재 확인된 것만"},
                "assignee": {"type": "string", "description": "새 담당자 id. 떼려면 \"\" (빈 문자열), 안 바꾸면 생략"},
                "duedate": {"type": "string", "description": "새 마감 YYYY-MM-DD. 안 바꾸면 생략"},
                "priority": {"type": "string", "description": "새 우선순위. 안 바꾸면 생략"},
                "summary": {"type": "string", "description": "새 제목. 안 바꾸면 생략"},
                "description": {"type": "string",
                                "description": "새 본문(HTML — 생성 때와 같은 구조). 본문을 "
                                               "고치라는 요청일 때만. 안 바꾸면 생략"},
                "labels": {"type": "array", "items": {"type": "string"},
                           "description": "라벨 전체 교체값. 안 바꾸면 생략"},
                "comment": {"type": "string",
                            "description": "변경과 함께 남길 코멘트(왜 바꾸는지). 없으면 생략"},
            },
            "required": ["key"],
        },
        "rationale": {"type": "string", "description": "왜 이렇게 쪼갰는지/바꾸는지 2~3문장. 사용자에게 보인다"},
    },
    "required": ["questions", "mode", "items"],
}


class Refiner(ToolAgent):
    name = Node.REFINER
    temperature = 0.3          # 초안은 약간의 폭이 필요하다

    @property
    def tools(self):
        from app.agent import tools as T
        # find_parent_epic 을 주는 이유 — 없으면 "Epic Link 는 어디에 연결할까요?"를 **사용자에게**
        # 묻게 된다(실제로 물었다). 상위 후보는 찾아보면 아는 것이다.
        return T.RULE_TOOLS + T.REVIEW_TOOLS + [T.BY_NAME["find_parent_epic"]]

    def system(self, state):
        forced = (state.get("turns") or 0) >= MAX_REFINE_TURNS
        extra = ("\n\n★ 되묻기 횟수를 다 썼다. **더 묻지 말고** 아는 것만으로 초안을 만들어라. "
                 "모르는 필드는 비워 두고 rationale 에 '확인 필요'로 남긴다." if forced else "")
        # 정적 지시는 prompts/roles/refiner.md — 동적 경고(횟수 소진)만 코드가 덧붙인다.
        return persona(state, SYSTEM_REFINER + extra)

    def task(self, state):
        # "알아서/기본값" 은 명령서 수준에서 강제한다 — 되묻기 기준(시스템)만으로는 담당자·기한을
        # 또 물었다(실측 2회). 명령서의 ★ 지시는 따르는 것을 버그 갈래에서 확인했다.
        said = conversation(state)
        defaults = any(w in said for w in ("알아서", "기본값", "맡길게", "맡기겠"))
        force_rule = ("\n- ★ 사용자가 **알아서 진행하라고 했다. questions 는 반드시 빈 배열**로 내고 "
                      "지금 아는 것 + 기본값으로 items 를 완성하라. 담당자는 비워 둔다(다음 단계가 "
                      "정한다). 기한은 사용자가 말한 것을 쓰고, 없으면 비워 둔다."
                      if defaults else "")
        # 버그는 새 기능과 초안 규칙이 다르다 — 갈래를 지시문으로 가른다(Prompt Chaining 의 분기).
        if (state.get("intent") or "") == Intent.REPORT_BUG:
            goal = """버그 신고를 **Bug 티켓 초안**으로 만들어라.
- type 은 Bug. 제목은 증상을 담는다("[모듈] ~~가 ~~할 때 ~~된다").
- description 에 **재현 경로 / 기대 동작 / 실제 동작**을 나눠 적는다. 사용자가 안 준 것은
  빈 칸으로 두고 questions 로 물어라 — 재현 경로 없는 버그 티켓은 아무도 못 잡는다.
- 원인으로 의심되는 기존 티켓이 조사에서 나왔으면 description 에 키를 적어라.
- 이미 같은 증상의 Bug 가 열려 있으면 **새로 만들지 말고** questions 로 사용자 판단을 구하라.
- 버그는 대개 쪼갤 필요가 없다 — Bug 하나면 된다. Sub-Task 로 나누지 마라."""
        elif (state.get("intent") or "") == Intent.MODIFY:
            goal = """기존 티켓의 **변경 계획**(change)을 만들어라. items 는 빈 배열로 둔다.
- key 는 조사에서 **실재가 확인된** 티켓만. 사용자가 댄 키가 조사에 없으면 questions 로 확인하라.
- 사용자가 바꾸라고 한 필드만 change 에 넣는다 — 시키지 않은 필드를 얹지 마라.
- "다음 주 금요일" 같은 상대 날짜는 오늘 날짜 기준으로 계산해 YYYY-MM-DD 로 적는다.
- 담당자 변경이면 새 담당자 id(skcc.x1042 형식)를 확인하라 — 이름만 있으면 조사 자료의
  참여자·로스터에서 id 를 찾고, 못 찾으면 questions 로 묻는다(assignee 필드 자동완성이 붙는다).
- 사용자가 코멘트도 남기라고 했으면 comment 에 그 내용을 담는다."""
        else:
            goal = """아래 요청을 실행 가능한 티켓 초안으로 만들어라. 정보가 모자라면 **초안 대신 질문**을 내라.
- ★ 이번 배치에는 **Task/Story/Bug 만** 담는다. Sub-Task 는 부모 티켓이 실재해야 만들 수
  있으므로 **부모가 만들어진 다음** 별도 승인으로 붙인다 — 지금 같이 내면 전부 반려된다.
  쪼개고 싶은 실행 단계는 description 의 '후속 Sub-Task 후보' 목록으로 적어 두라."""
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
- **제목**: "[모듈] 무엇을 어떻게" 형태, 동사로 끝낸다. 제목만으로 다른 티켓과 구분돼야 한다.
- **description 은 HTML 구조로**: <h3>배경</h3>(계기 + 관련 티켓 키) →
  <h3>완료 조건 (DoD)</h3>(taskList 체크박스 — 각 항목이 **검증 가능**해야 한다) →
  필요 시 비교 표(<table>)·관련 문서 링크(<a>). 통짜 문단 하나로 쓰지 마라.
- **컴포넌트는 하나만**(list_ticket_options 의 실값). 두 모듈에 걸치면 티켓을 나눈다.
- **라벨은 기존 것 우선**(list_ticket_options 로 확인) — 같은 뜻의 라벨이 두 벌 생기면
  어느 쪽으로도 검색이 안 된다.
- **분업이 필요해 보이는 큰 일**은 한 티켓에 몰지 말고 **역할 단위로 여러 Task/Story 로 나눠라**
  (티켓 하나 = 담당자 한 명). 각 티켓 안의 실행 단계는 DoD 체크박스로, 더 잘게 쪼갤 후보는
  '후속 Sub-Task 후보' 절로 적는다(부모 생성 후 2차 승인으로 붙는다).
- 이미 같은 일이 진행 중이면 새로 만들지 말고 questions 로 사용자 판단을 구한다.{force_rule}

## 대화
{conversation(state)}

## 원문 요청
{last_user_text(state)}{data}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        # 문자열로 오면(구모델·fake) 구조로 승격한다 — 화면은 dict 만 다루면 된다.
        qs = []
        for q in (out.get("questions") or [])[:3]:
            if isinstance(q, str) and q.strip():
                qs.append({"question": q.strip(), "kind": "text", "options": [], "field": ""})
            elif isinstance(q, dict) and str(q.get("question") or "").strip():
                qs.append({"question": str(q["question"]).strip(),
                           "kind": q.get("kind") or "text",
                           "options": [str(o) for o in (q.get("options") or []) if str(o).strip()][:5],
                           "field": q.get("field") or ""})
        items = [i for i in (out.get("items") or []) if isinstance(i, dict) and i.get("summary")]
        mode = out.get("mode") or "task"
        # ★ 기계적 가드 — task 배치에 Sub-Task 가 섞이면 그 항목은 뺀다. 프롬프트로 막았는데도
        #   실 모델이 섞어 낸 적이 있고, 그대로 두면 검증 실패 → 재작성 왕복만 태우다
        #   한도 소진으로 끝난다. 빼는 것이 반려보다 낫다(부모 생성 후 2차 승인으로 붙일 수 있다).
        if mode == "task":
            dropped = [i for i in items if (i.get("type") or "").lower().startswith("sub")]
            if dropped:
                items = [i for i in items if i not in dropped]
                names = ", ".join(d.get("summary", "") for d in dropped)
                extra_note = f"(Sub-Task {len(dropped)}건은 부모 생성 후 별도 승인으로 붙인다: {names})"
                out["rationale"] = ((out.get("rationale") or "") + "\n" + extra_note).strip()
        turns = (state.get("turns") or 0) + 1
        # 되묻기 상한을 넘겼는데도 질문만 냈다면 질문을 버린다 — 영원히 안 끝나는 대화를 막는다.
        if qs and turns > MAX_REFINE_TURNS:
            qs = []
        draft = {"mode": out.get("mode") or "task", "items": items,
                 "rationale": out.get("rationale") or ""}

        # ── References 자동 첨부 — 조사 결과를 티켓에 박제한다.
        # 대화가 끝나면 Historian 의 조사는 증발하지만, 티켓 description 에 남기면 동적 RAG 가
        # 다음 조사에서 그걸 다시 수확한다(지식이 복리로 쌓인다). 습관을 프롬프트에 맡기지 않고
        # 코드가 보장한다 — 모델이 적었으면 그대로 두고, 안 적었으면 붙인다.
        import re as _re
        refs = []
        for e in (state.get("evidence") or [])[:5]:
            k, why = (e.get("key") or "").strip(), (e.get("why") or e.get("title") or "").strip()
            # 티켓 키 모양만 — PMO 근거에는 "ETL" 같은 모듈명이 섞이는데 그건 References 가 아니다.
            if k and _re.match(r"^[A-Z][A-Z0-9]*-[0-9]+$", k):
                refs.append(f"<li>{k} — {why}</li>" if why else f"<li>{k}</li>")
        for d in (state.get("related_docs") or [])[:3]:
            t, u = (d.get("title") or "").strip(), (d.get("url") or "").strip()
            if t and u:
                refs.append(f'<li><a href="{u}">{t}</a></li>')
        if refs:
            block = "<h3>References</h3><ul>" + "".join(refs) + "</ul>"
            for it in items:
                if "References" not in (it.get("description") or ""):
                    it["description"] = ((it.get("description") or "") + block)

        # PMO_VIT 는 경영진 보고 현안 전용이고 트리 최상위 하나에만 붙는다 — 그런데 모델이
        # 기존 라벨 목록에서 보고는 신규 티켓 셋에 전부 붙였다(실측). 사용자가 입으로 말했을
        # 때만 남기고, 아니면 기계적으로 뗀다. 규칙 위반 라벨은 검색 노이즈가 된다.
        asked_all = conversation(state)
        if "PMO_VIT" not in asked_all and "현안" not in asked_all:
            for it in items:
                if it.get("labels"):
                    it["labels"] = [x for x in it["labels"] if str(x).upper() != "PMO_VIT"]

        # modify 갈래 — 변경 계획. 바꿀 값이 하나도 없는 change 는 계획이 아니다.
        change = out.get("change") if isinstance(out.get("change"), dict) else {}
        plan = {}
        if change.get("key"):
            fields = {k: change[k] for k in ("assignee", "duedate", "priority", "summary",
                                             "labels", "description")
                      if k in change and change[k] is not None}
            cmt = (change.get("comment") or "").strip()
            # 댓글만 남기는 것도 유효한 계획이다 — "이 내용 DL-x 에 댓글로 남겨줘"가 실사용에 있다.
            if fields or cmt:
                plan = {"key": str(change["key"]).strip(), "changes": fields,
                        "comment": cmt, "why": out.get("rationale") or ""}

        return {"questions": qs, "draft": draft, "change_plan": plan, "turns": turns,
                "trace": note(state, self.name,
                              f"변경 계획 {plan.get('key')}" if plan else
                              (f"질문 {len(qs)}개 · 초안 {len(items)}건" if qs or items else "초안 없음"))}


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
