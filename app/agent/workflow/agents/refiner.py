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
import re as _re

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
        "epic_name": {"type": "string",
                      "description": "epic 모드 전용 — WBS·뱃지에 보일 짧은 단축어(예: 'CDC도입'). "
                                     "비우면 summary 를 쓴다"},
        "parent": {"type": "string", "description": "subtask 모드에서 부모 티켓 키"},
        "description": {
            "type": "string",
            "description": (
                "티켓 본문 — **HTML 로 작성한다**(에디터가 받는 형식. mock 은 위키로 자동 변환, "
                "prod 는 그대로 저장된다). 네 섹션을 이 순서로 반드시 채운다"
                "(knowledge/07-ticket-body-guide.md):\n"
                "<h3>배경</h3><p>왜 하는지 2~3문장 — 계기가 된 사건·요청을 티켓 키와 함께. "
                "감상이 아니라 사실로</p>\n"
                "<h3>작업 범위</h3><ul><li>이번에 하는 것</li>"
                "<li>이번에 하지 않는 것</li></ul> ← **제외 항목을 적는 게 절반**이다\n"
                "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                "<li data-checked=\"false\">검증 가능한 조건</li></ul> (2~4개)\n"
                "<h3>참고</h3><ul><li>DL-123 \"제목\" — **이 일과 무슨 관계인지 한 마디**</li>"
                "<li><a href=\"URL\">문서 제목</a> — 무엇을 볼 수 있는지</li></ul>\n"
                "★ 참고는 **항목마다 다르다** — 같은 목록을 여러 티켓에 복사하지 마라. "
                "관계를 못 적겠으면 관련이 없는 것이니 빼라. 모듈이 같다는 이유로 붙이지 마라.\n"
                "★ 쪼갤 일은 본문에 '후속 Sub-Task 후보'라고 **글로만 적지 말고** children 에 "
                "실제 Sub-Task 로 적어라 — 글은 티켓이 되지 않는다.\n"
                "조사에서 알아낸 사실(왜 멈췄었는지·이미 결정된 것·기술 비교 결론)이 있으면 "
                "<h3>Knowledge</h3><ul><li>…</li></ul> 로 남겨라 — 나중에 이 티켓을 여는 "
                "사람과 검색(RAG)이 그걸 다시 쓴다. 여러 후보 비교는 <table> 로."),
        },
        "children": {
            "type": "array",
            "description": (
                "이 티켓 **아래에 함께 만들 Sub-Task**. 일이 커서 여러 사람이 나눠 해야 하면 "
                "여기 적는다 — 본문에 '후속 Sub-Task 후보'라고 글로만 쓰지 마라(그건 티켓이 "
                "되지 않는다). 승인 후 부모를 먼저 만들고 그 키로 Sub-Task 를 이어 붙인다.\n"
                "· 기능이 다른 일(검증 스크립트 / 모니터링 / 전환)은 **각각 다른 Sub-Task**로, "
                "그 일에 맞는 모듈 사람에게.\n"
                "· 같은 일을 분량으로 나눈 것(토픽 A/B/C 전환)은 **골고루 다른 사람에게** "
                "배분한다 — 한 사람에게 몰면 나눈 의미가 없다.\n"
                "· 과잉 분해 금지: 아직 방식이 안 정해진 일은 쪼개지 않는다."),
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "동사로 끝나는 제목. 부모 제목을 "
                                                                 "그대로 베끼지 마라"},
                    "description": {"type": "string",
                                    "description": "본문(HTML). 부모 본문을 복사하지 마라 — 배경은 "
                                                   "부모에 있다. 이 조각이 **무엇을 어떻게 "
                                                   "끝내는지**만: <h3>작업 범위</h3> + "
                                                   "<h3>완료 조건 (DoD)</h3>"},
                    "assignee": {"type": "string", "description": "담당 사번. 모르면 빈 문자열"},
                    "duedate": {"type": "string", "description": "YYYY-MM-DD. 모르면 빈 문자열"},
                },
                "required": ["summary"],
            },
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
        "kind": {"type": "string", "enum": ["text", "choice", "multi", "date"],
                 "description": "choice=하나 선택 / multi=**여러 개** 선택 가능(대상 파이프라인·"
                                "라벨처럼 복수가 자연스러운 질문) / date=날짜 / text=자유 서술. "
                                "**choice 를 우선하라** — 네가 답을 추천할 수 있는 질문"
                                "(우선순위·범위·방식·대상)은 전부 choice 다. text 는 정말 "
                                "자유 서술만 가능한 것(재현 경로, 배경 설명)에만 쓴다. "
                                "화면이 '직접 입력' 선택지를 자동으로 붙이므로 보기가 빠짐없이 "
                                "완전할 필요는 없다"},
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
        "mode": {"type": "string", "enum": ["task", "subtask", "epic"],
                 "description": "이번에 만들 것의 종류. Sub-Task 는 부모가 있어야 하므로 대개 먼저 task. "
                                "epic = 사용자가 새 Epic(이니셔티브)을 만들자고 할 때 — items 는 "
                                "Epic 1개(type='Epic', epic_name 에 짧은 단축어)"},
        "structure": {
            "type": "string",
            "enum": ["single_task", "task_with_subtasks", "multiple_tasks", "new_epic"],
            "description": (
                "이번 초안의 **구조 판단**(knowledge/04 '어떤 구조로 만들 것인가'). "
                "기본값은 single_task 이고, 올라가려면 근거가 있어야 한다.\n"
                "· single_task = 산출물 하나, 한 사람이 며칠 안에 끝냄\n"
                "· task_with_subtasks = 산출물은 하나인데 작업이 여러 사람·여러 대상으로 나뉨\n"
                "· multiple_tasks = 산출물이 여럿이고 완료 시점·검증 주체가 다름(모듈이 다름)\n"
                "· new_epic = **네 조건을 전부** 만족할 때만 — ①2 스프린트 이상 ②서로 다른 "
                "모듈의 Task 3개 이상 ③담을 기존 Epic 을 찾아봤고 없다 ④사용자가 별도 진척 "
                "보고 단위로 관리하려 한다. 하나라도 불확실하면 격상하지 말고 기존 Epic 아래 "
                "Task 로 두어라(느낌은 근거가 아니다)"),
        },
        "structure_source": {
            "type": "string", "enum": ["user_specified", "inferred"],
            "description": (
                "그 구조를 **누가 정했나**. user_specified = 사용자가 형태를 말했다"
                "('에픽으로 만들자', '서브태스크로 쪼개줘', '테스크 하나만'). "
                "inferred = 사용자는 할 일만 말했고 형태는 네가 판단했다. "
                "판단한 것이면 사용자가 다르게 생각할 수 있다 — 갈림이 크면 확인을 받는다"),
        },
        "structure_why": {
            "type": "string",
            "description": "그 구조를 고른 이유 한 줄 — 판정 신호를 사실로 적는다. 예: "
                           "'토픽 3개 전환은 같은 산출물의 분량 분할이라 Sub-Task', "
                           "'2주 규모·단일 모듈이라 Epic 격상 보류, DL-101 아래 Task'",
        },
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
                      "정한다) — 단 **사용자가 누구에게 맡길지 말했으면 그대로 적는다**"
                      "('성능 측정은 x1402' 처럼 지정한 것을 비우면 지시를 버리는 셈이다). "
                      "기한은 사용자가 말한 것을 쓰고, 없으면 비워 둔다.\n"
                      "- ★ **items 가 빈 배열인 채로 끝내지 마라.** 조사에서 비슷한 티켓이 "
                      "나왔든 정보가 조금 모자라든, 지금 아는 것으로 초안을 만들고 미확정은 "
                      "rationale 에 적는다. 질문만 내고 초안이 0건이면 사용자는 아무것도 "
                      "승인할 수 없다 — 위임받고 아무것도 안 한 셈이다."
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
- 사용자가 코멘트도 남기라고 했으면 comment 에 그 내용을 담는다.
- ★ **댓글만 남기라는 요청이면 도구를 부르지 마라.** change 에 key 와 comment 만 채우면
  끝이다 — 전이·옵션 조회는 댓글과 무관하다(실측: 도구 10번 헤매고 확인 질문으로 샜다).
- ★ "진행해도 괜찮으신가요?" 류의 **허락 질문 금지.** 승인 카드가 곧 그 확인이다 —
  계획을 완성해서 내면 사용자가 카드에서 승인/취소한다."""
        else:
            goal = """아래 요청을 실행 가능한 티켓 초안으로 만들어라. 정보가 모자라면 **초안 대신 질문**을 내라.
- ★ 먼저 **구조를 정한다**(structure + structure_why): 단일 Task / Task+Sub-Task /
  여러 Task / 새 Epic. 기본은 단일 Task 이고, 올라가려면 한 줄로 댈 근거가 있어야 한다.
  Epic 격상은 네 조건(2 스프린트↑·다른 모듈 Task 3개↑·담을 기존 Epic 없음·별도 보고 단위)을
  **전부** 만족할 때만 — 하나라도 불확실하면 기존 Epic 아래 Task 로 두고 보류 사유를 남긴다.
- ★ 쪼갤 실행 단위는 각 항목의 **children 에 실제 Sub-Task 로** 적는다(승인 한 번으로 부모
  생성 후 이어 붙는다). 본문에 '후속 Sub-Task 후보'라고 글로만 적지 마라 — 티켓이 되지 않는다.
- ★ **부모가 이미 있으면 children 이 아니라 mode="subtask" 다.** "DL-9090 에 서브태스크
  추가해줘", "DL-9095 를 쪼개줘" 처럼 **실재하는 티켓을 지목**했으면 그 티켓이 부모다 —
  감싸는 새 Task 를 만들지 말고, items 를 Sub-Task 로 내고 각 항목의 parent 에 그 키를 적어라
  (새 Task 를 만들면 사용자가 말한 티켓은 그대로 두고 엉뚱한 껍데기가 하나 더 생긴다).
  여러 티켓에 각각 붙이라고 하면(“DL-9093 이랑 DL-9094 둘 다”) **항목마다 parent 를 달리** 한다.
- ★ items 에는 Task/Story/Bug 만 담는다(Sub-Task 는 children 자리다)."""
        # 형태를 사용자가 말했는지 **코드가 판정해** 알려 준다 — 같은 문장을 모델이 매번
        # 다르게 읽지 않도록. 말했으면 그대로 따르고, 열려 있으면 판단하되 갈림이 크면
        # 시스템이 확인 질문을 붙인다(모델이 임의로 되묻지 않게).
        shape, word = shape_hint(state)
        if shape:
            goal += (f"\n- ★ 사용자가 만들 **형태를 말했다**('{word}' → {shape}). 그대로 따르고 "
                     "structure_source 를 \"user_specified\" 로 적어라. 다른 형태를 권하지 마라.")
        elif not defaults:
            goal += ("\n- ★ 사용자는 **할 일만 말했고 형태는 열려 있다**. 네가 판단하되 "
                     "structure_source 를 \"inferred\" 로 적어라 — 확인이 필요하면 시스템이 "
                     "형태 확인 질문을 자동으로 붙인다(네가 따로 묻지 마라).")
        ev = "\n".join(f"- {e.get('key','')} {e.get('title','')} — {e.get('why','')}"
                       for e in (state.get("evidence") or []))
        data = wrap_data(
            data_block("Historian 이 정리한 현재 상황", state.get("situation")),
            data_block("근거 티켓", ev),
            data_block("배치 재료 (코드가 조회함 — Epic·컴포넌트·라벨은 이 안에서 고른다)",
                       _placement_material(state)),
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
- **Epic 은 항목마다 고른다** — 그 산출물이 기여하는 이니셔티브로. 모듈이 다르면 Epic 도
  다른 게 정상이다. 아래 '배치 재료'의 후보 중에서만 고르고, 마땅한 게 없거나 여럿이면
  questions(kind=choice, field=epic, 보기에 "없음(최상위)" 포함)로 물어라.
- **컴포넌트는 하나만**(배치 재료의 실값). 두 모듈에 걸치면 티켓을 나눈다 —
  컴포넌트가 둘이면 워크로드가 이중 계상된다.
- **라벨은 기존 것 우선**(배치 재료 목록) — 새 라벨을 붙일 수는 있지만 목록에 없으면
  승인 카드에 '신규 라벨'로 표시돼 사용자가 판단한다. 오탈자·동의어를 새로 만들지 마라.
- **분업이 필요한 큰 일**: 산출물이 여럿이면 Task 를 나누고(각각 담당 한 명),
  산출물 하나를 여럿이 나눠 하는 것이면 그 Task 의 **children 에 Sub-Task 로** 적는다.
- 이미 같은 일이 진행 중이면 새로 만들지 말고 questions 로 사용자 판단을 구한다 — 단,
  **사용자가 '알아서'라고 했다면 묻지 말고 초안을 내고** rationale 에 "기존 DL-x 와 겹칠 수
  있음"을 적는다(위임했는데 질문으로 되돌리면 아무것도 안 만들어진다).
- ★ **새 일을 만들라는 요청에 기존 티켓 변경 계획(change)을 내지 마라.** 조사에서 비슷한
  티켓이 나왔다고 그 티켓의 제목·본문을 고치는 것은 사용자가 부탁한 일이 아니다 —
  관련 티켓은 초안의 '참고'에 적고, 새 티켓은 새로 만든다(실측: 새 작업 3건을 요청했는데
  기존 티켓 하나를 수정하겠다고 답해 초안이 0건이 됐다).{force_rule}

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
        # 모델이 parent 를 비운 채 Sub-Task 를 내는 일이 잦다 — 사용자가 "DL-9090 밑에"
        # 라고 지목했으면 그 키가 부모다(실재는 조사에서 이미 확인됐다). **모드와 무관하게**
        # 채운다: mode=subtask 로 내면서 parent 만 빠뜨리면 검증에서 통째로 반려돼
        # "만들겠습니다" 라고 말해 놓고 초안이 0건이 된다(실측: PAR1).
        named = [k for k in (state.get("mentioned_keys") or []) if _ticket_exists(k)]
        if named:
            for i in items:
                if (i.get("type") or "").lower().startswith("sub") or mode == "subtask":
                    if not str(i.get("parent") or "").strip():
                        i["parent"] = named[0]

        # ── 지목한 티켓이 부모다: 껍데기 Task 를 만들지 않는다 ─────────────
        # "DL-9090 에 서브태스크 추가해줘" 는 그 티켓 **아래**에 붙이라는 뜻인데, 모델이
        # 감싸는 새 Task 를 만들고 그 밑에 children 을 다는 일이 잦다(실측 SUB1~3) —
        # 그러면 사용자가 말한 티켓은 그대로 두고 엉뚱한 껍데기가 하나 더 생긴다.
        if named and mode == "task" and len(items) == 1 and items[0].get("children")                 and _asks_subtasks(state):
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

        if mode == "task":
            subs = [i for i in items if (i.get("type") or "").lower().startswith("sub")]
            # ★ 전부 Sub-Task 이고 부모가 실재하면 **모드를 승격**한다 — 사용자가 부모를
            #   지목했는데 mode 만 task 로 잘못 낸 경우다(버리면 초안이 0건이 된다).
            if subs and len(subs) == len(items) and all(_ticket_exists(i.get("parent")) for i in subs):
                mode = "subtask"
                out["mode"] = "subtask"
            elif subs:
                items = [i for i in items if i not in subs]
                names = ", ".join(d.get("summary", "") for d in subs)
                extra_note = f"(Sub-Task {len(subs)}건은 부모 생성 후 별도 승인으로 붙인다: {names})"
                out["rationale"] = ((out.get("rationale") or "") + "\n" + extra_note).strip()
        turns = (state.get("turns") or 0) + 1
        # 되묻기 상한을 넘겼는데도 질문만 냈다면 질문을 버린다 — 영원히 안 끝나는 대화를 막는다.
        if qs and turns > MAX_REFINE_TURNS:
            qs = []
        # ★ 사용자가 "알아서" 라고 했으면 **묻지 않는다.** 명령서에 그렇게 적어 두었는데도
        #   모델이 되물어 초안이 0건으로 끝나는 일이 반복됐다(실측: 지목한 Epic 이 있는데도
        #   2개를 되물었다). 지시를 코드로 보장한다 — 초안이 있으면 질문을 버린다.
        if qs and items and _said_defaults(state):
            asked = "; ".join(str(q.get("question", ""))[:40] for q in qs[:3])
            out["rationale"] = ((out.get("rationale") or "")
                                + f"\n(사용자가 '알아서'라고 해서 기본값으로 채웠다: {asked})").strip()
            qs = []
        # 초안 관련 인터뷰의 마지막엔 항상 **자유 의견** 질문 하나를 붙인다(사용자 요청) —
        # 객관식 보기가 못 담는 계획·우려를 받아낼 출구. 코드가 붙이므로 모델이 잊지 못한다.
        if qs and not any(q.get("kind") == "text" and "자유" in q.get("question", "") for q in qs):
            qs.append({"question": "그 밖에 반영할 의견이나 원하는 진행 방식이 있으면 자유롭게 "
                                   "적어 주세요 (없으면 건너뛰어도 됩니다)",
                       "kind": "text", "options": [], "field": ""})
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

        # 구조 판단은 **드러내 놓고** 싣는다 — 숨은 판단은 매번 달라지고 검증도 못 한다.
        # Epic 격상은 보수적으로: 새 Epic 을 고르고도 조건을 못 채웠으면(단일 모듈·소규모)
        # 코드가 되돌리지는 않되(사용자가 명시적으로 원했을 수 있다) 근거를 남기게 강제한다.
        structure = out.get("structure") or ""
        why = (out.get("structure_why") or "").strip()
        src = out.get("structure_source") or ""
        said_shape, _word = shape_hint(state)
        if said_shape:                      # 사용자가 말한 것은 판단이 아니다 — 코드가 확정한다
            src = "user_specified"
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
        if structure and why and why not in (draft["rationale"] or ""):
            draft["rationale"] = (draft["rationale"] + f"\n(구조: {structure} — {why})").strip()

        # ── References 자동 첨부 — 조사 결과를 티켓에 박제한다.
        # 대화가 끝나면 Historian 의 조사는 증발하지만, 티켓 description 에 남기면 동적 RAG 가
        # 다음 조사에서 그걸 다시 수확한다(지식이 복리로 쌓인다). 습관을 프롬프트에 맡기지 않고
        # 코드가 보장한다 — 모델이 적었으면 그대로 두고, 안 적었으면 붙인다.
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

        # ── Epic Link 는 **실재하는 Epic** 이어야 한다 ─────────────────────
        # 실측: 사용자가 "기존 에픽 중 맞는 걸로 붙여줘"라고 했는데 모델이 Task(DL-9072)를
        # 에픽이라 답하고 초안에는 아예 안 실었다. 타입 확인은 판단이 아니라 조회다.
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
            # Epic 의 모듈과 티켓의 컴포넌트가 다르면 둘 중 하나가 틀린 것이다. 어느 쪽인지는
            # 사람이 판단할 일이라 고치지 않고 **알린다**(조용히 붙이면 남의 진척률이 오염된다).
            em = _epic_module(ek)
            comps = [str(c) for c in (it.get("components") or []) if str(c).strip()]
            if em and comps and em != comps[0]:
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n(확인 필요: {ek} 는 {em} 모듈 Epic 인데 이 티켓은 "
                                      f"{comps[0]} 컴포넌트다)").strip()

        # 컴포넌트는 하나만 — 둘이면 워크로드가 이중 계상된다(knowledge/03).
        for it in items:
            comps = [str(c) for c in (it.get("components") or []) if str(c).strip()]
            if len(comps) > 1:
                it["components"] = comps[:1]
                out["rationale"] = ((out.get("rationale") or "")
                                    + f"\n({comps[0]} 만 남겼다 — 컴포넌트가 둘이면 워크로드가 "
                                      f"이중 계상된다. {', '.join(comps[1:])} 몫은 별도 티켓으로 "
                                      "나누는 것이 맞다)").strip()

        # ── 분량 분할 Sub-Task 는 골고루 ───────────────────────────────
        # "사람 나눠서" 라고 말한 일을 한 사람에게 몰아 주면 쪼갠 의미가 없다. 프롬프트로
        # 지시하되, 몰아준 경우 코드가 되돌린다(누구에게 줄지는 Assigner 의 근거를 존중해
        # 이미 배정된 사람들 안에서만 돌린다 — 새 사람을 지어내지 않는다).
        for it in items:
            kids = [c for c in (it.get("children") or []) if isinstance(c, dict)]
            owners = [str(c.get("assignee") or "").strip() for c in kids]
            named = [o for o in owners if o]
            if len(kids) >= 3 and len(set(named)) == 1 and len(named) == len(kids):
                pool = _module_pool(it, named[0])
                if len(pool) > 1:
                    for i, c in enumerate(kids):
                        c["assignee"] = pool[i % len(pool)]
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
        # "사람 나눠서" 라고 한 일에 담당이 하나도 없으면 나눈 의미가 없다. Assigner 는
        # 상위 items 만 보므로(자식은 그 뒤에 생긴다) 여기서 코드가 채운다 — 사용자가
        # 지정한 자식 담당은 건드리지 않고, **빈 것만** 모듈 로스터로 돌린다.
        for it in items:
            kids = [c for c in (it.get("children") or []) if isinstance(c, dict)]
            empty = [c for c in kids if not str(c.get("assignee") or "").strip()]
            if not kids or not empty:
                continue
            taken = [str(c.get("assignee")).strip() for c in kids
                     if str(c.get("assignee") or "").strip()]
            pool = [u for u in _module_pool(it, taken[0] if taken else "") if u]
            if not pool:
                continue
            order = [u for u in pool if u not in taken] or pool
            for n, c in enumerate(empty):
                c["assignee"] = order[n % len(order)]
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(나눠 맡도록 자식 담당을 모듈 인력에 배분했다 — "
                                  "승인 화면에서 바꿀 수 있다)").strip()

        # ── 구조 판단과 산출이 어긋나면 알린다 ──────────────────────────
        # "Sub-Task 로 나눈다"고 해 놓고 children 이 없으면 그건 판단이 아니라 말뿐이다.
        if structure == "task_with_subtasks" and not any(i.get("children") for i in items):
            out["rationale"] = ((out.get("rationale") or "")
                                + "\n(확인 필요: 나눠서 진행한다고 판단했는데 Sub-Task 가 "
                                  "없다 — 한 티켓으로 둘지 쪼갤지 정해야 한다)").strip()

        # 우선순위 표기 정규화 — 모델은 "P3" 라고 줄여 쓰고 Jira 는 "P3-Minor" 만 받는다.
        # Reviewer 가 반려하면 재작성 왕복 하나가 통째로 날아가고, 한도 소진이면 그 지적이
        # 사용자에게 떠넘겨진다(실측: "P3는 적절한 우선순위가 아닙니다"가 답변에 노출).
        # 판단이 아니라 표기 문제다 — 코드가 정규화한다.
        _PRI = {"P0": "P0-Blocker", "P1": "P1-Critical", "P2": "P2-Major",
                "P3": "P3-Minor", "P4": "P4-Trivial",
                "BLOCKER": "P0-Blocker", "CRITICAL": "P1-Critical", "MAJOR": "P2-Major",
                "MINOR": "P3-Minor", "TRIVIAL": "P4-Trivial"}
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
                                             "labels", "description")
                      if k in change and change[k] is not None}
            if str(fields.get("priority") or "").strip():
                p = str(fields["priority"]).strip()
                fields["priority"] = _PRI.get(p.upper(), p)
            cmt = (change.get("comment") or "").strip()
            # 댓글만 남기는 것도 유효한 계획이다 — "이 내용 DL-x 에 댓글로 남겨줘"가 실사용에 있다.
            if fields or cmt:
                plan = {"key": str(change["key"]).strip(), "changes": fields,
                        "comment": cmt, "why": out.get("rationale") or ""}

        # 가드들이 out["rationale"] 에 덧붙인 경고(Epic 불일치·컴포넌트 정리 등)를 초안에 반영한다
        # — draft 는 items 를 참조로 공유하지만 rationale 은 문자열이라 여기서 맞춰 줘야 한다.
        draft["rationale"] = out.get("rationale") or draft.get("rationale") or ""
        if structure and why and why not in draft["rationale"]:
            draft["rationale"] = (draft["rationale"] + f"\n(구조: {structure} — {why})").strip()

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
            bits.append(f"\n    설명: {str(it['description'])[:150]}")
        rows.append("  ".join(bits))
    return f"mode={draft.get('mode')}\n" + "\n".join(rows)


def as_bulk_items(draft: dict) -> list:
    """초안 → `validate_ticket_plan` / `create_tickets` 가 받는 형태.

    `epic` 이 빈 문자열이면 **`None` 으로 바꾼다** — 규칙상 "최상위로 두겠다"는 명시가 필요하고,
    빈 문자열은 그 명시로 인정되지 않는다.
    """
    mode = (draft or {}).get("mode") or "task"
    if mode == "epic":
        # Epic 은 bulk 생성 대상이 아니다 — 화면·검증 표시용 한 줄만 만든다.
        # 실행은 Operator 가 create_epic 도구로 한다(승인 지문은 epic_payload 가 정의).
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
    **부모 index** 로 묶어 두고 Operator 가 부모 생성 결과 키로 치환한다.
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


def _module_pool(item: dict, fallback: str) -> list:
    """이 티켓 모듈의 실 인력. 분량 분할을 돌릴 때 **지어내지 않기 위해** 로스터를 쓴다."""
    try:
        from app.infra.settings import load_people
        roster = load_people() or {}
        for comp in (item.get("components") or []):
            ids = [str(x) for x in (roster.get(str(comp)) or []) if str(x)]
            if ids:
                return ids
        # 컴포넌트를 못 믿겠으면 그 사람이 속한 모듈로
        for ids in roster.values():
            if fallback in (ids or []):
                return [str(x) for x in ids]
    except Exception:
        pass
    return [fallback] if fallback else []


def _placement_material(state) -> str:
    """배치 재료 — Epic 후보·허용 컴포넌트·기존 라벨을 **코드가 미리 조회**해 준다.

    도구로 두면 모델이 부를 때만 보이고, 안 부르면 지어낸다(실측: Task 를 Epic 이라 답하고
    초안엔 안 실었다). 반복 조회는 판단이 아니므로 코드가 한다.
    """
    parts = []
    try:
        rows = _epic_options(state)
        if rows:
            parts.append("Epic 후보 (여기서 고른다. 모듈이 다르면 항목마다 다른 Epic 이 정상. "
                         "마땅한 게 없으면 questions 로 물어라):\n"
                         + "\n".join('- {} [{}] "{}"'.format(r["key"], r.get("module") or "-",
                                                             r.get("summary", ""))
                                     for r in rows[:10]))
    except Exception:
        pass
    try:
        from app.agent.tools.write_tools import list_ticket_options
        opts = list_ticket_options.invoke({"kind": ""}) or {}
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
    """사용자가 "알아서/기본값/맡길게" 라고 했나 — 되묻기를 끄는 신호."""
    said = conversation(state)
    return any(w in said for w in ("알아서", "기본값", "맡길게", "맡기겠", "네가 정해", "아무거나"))


def _ticket_exists(key) -> bool:
    k = str(key or "").strip()
    if not k:
        return False
    try:
        from app.agent.tools._ctx import client
        return bool((client().get_issue(k) or {}).get("key"))
    except Exception:
        return False


def _known_components() -> set:
    try:
        from app.agent.tools.write_tools import list_ticket_options
        return {str(x) for x in ((list_ticket_options.invoke({"kind": "components"}) or {})
                                 .get("components") or [])}
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


def _asks_subtasks(state) -> bool:
    """"서브태스크로 쪼개줘 / 하위 작업 추가해줘" 처럼 **자식을 붙여 달라**는 요청인가."""
    said = last_user_text(state)
    return any(w in said for w in ("서브태스크", "서브 태스크", "subtask", "sub-task",
                                   "하위 작업", "하위작업", "쪼개", "나눠서 붙"))


# ── 사용자가 형태를 말했나, 열려 있나 ───────────────────────────────
# 같은 "만들어줘"라도 둘은 완전히 다른 상황이다. 형태를 말했으면 그대로 따르는 것이 맞고,
# 열려 있으면 우리가 판단하되 **갈림이 크면 확인을 받는** 것이 맞다. 이 판정을 모델에게
# 맡기면 흔들리므로(같은 문장에 다른 답), 낱말로 하는 판정은 코드가 한다.
_SHAPE_WORDS = (
    ("new_epic", ("에픽으로", "epic 으로", "에픽 만들", "에픽으로 크게", "이니셔티브")),
    ("subtask", ("서브태스크", "서브 태스크", "sub-task", "subtask", "하위 작업", "하위작업",
                 "쪼개", "분할")),
    ("multiple_tasks", ("각각 티켓", "티켓 여러", "테스크 여러", "따로따로", "나눠서 만들")),
    ("single_task", ("하나만", "한 건만", "티켓 하나", "테스크 하나", "단일")),
)


def shape_hint(state) -> tuple:
    """(사용자가 말한 형태 | "", 근거 낱말). 말하지 않았으면 열려 있는 것이다."""
    said = last_user_text(state)
    for kind, words in _SHAPE_WORDS:
        for w in words:
            if w in said:
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
