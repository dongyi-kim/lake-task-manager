"""Planner — 무엇을 원하는 요청인지 가른다. 그래프의 첫 분기가 여기서 정해진다.

"DL-118 어떻게 됐어?"와 "CDC 도입해야 해"는 들어가야 할 길이 완전히 다르다. 전자는 찾아서
답하면 끝이고, 후자는 조사→구체화→담당자→검증→생성까지 간다. 이걸 매번 전 경로로 태우면
느리고 비싸다.

**분류를 Structured Output 으로 받는다.** "이건 업무 계획 요청 같습니다"라는 자유 서술을 받아
정규식으로 긁으면, 모델이 말투를 바꾸는 날 조용히 오분류된다. enum 으로 강제하면 그럴 일이 없다.
"""

from __future__ import annotations

from app.agent.workflow.agents.base import StructuredAgent
from app.agent.prompts.roles import SYSTEM_PLANNER
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import (AgentState, Intent, Node, conversation,
                                      last_user_text, note)

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [Intent.ASK, Intent.PLAN_WORK, Intent.REPORT_BUG, Intent.MY_DAY,
                     Intent.PROGRESS, Intent.ACTIVITY, Intent.MODIFY, Intent.CHITCHAT],
            "description": (
                "ask=이미 있는 것에 대해 물음(이력·경위) / "
                "plan_work=새 업무를 시작하려 함(티켓 트리까지) / "
                "report_bug=버그·장애를 발견했다고 알림(Bug 티켓 생성까지) / "
                "my_day=자기가 오늘/이번주 뭘 해야 하는지 물음 / "
                "progress=Epic·모듈·WBS 의 진척도/현황, 또는 **팀 상태 점검**"
                "(정체·오래 업데이트 없는·마감 지난·미배정 티켓이 있는지) / "
                "activity=특정 **사람**이 최근 무엇을 했는지 물음 / "
                "modify=기존 티켓의 담당자·마감 등을 바꾸려 함 / chitchat=업무 요청 아님"),
        },
        "keywords": {
            "type": "array", "items": {"type": "string"},
            "description": "검색에 쓸 핵심어 2~5개. 원문을 그대로 넣지 말고 명사구로 뽑아라. "
                           "약어와 풀어쓴 말을 함께 넣으면 좋다(예: CDC, 변경데이터캡처, 실시간 수집). "
                           "★ 테이블·Job·제품 이름 같은 **식별자는 원형 그대로** 한 덩어리로 넣어라 "
                           "— 'fdc.fdc_trace_summary_ic' 를 fdc/trace/summary 로 쪼개면 검색이 깨진다",
        },
        "module": {
            "type": "string",
            "enum": ["", "ETL", "Catalog", "Runtime", "Workbench", "DataOps", "DevOps"],
            "description": "짐작되는 모듈. 근거가 약하면 빈 문자열 — 틀린 모듈은 없느니만 못하다",
        },
        "mentioned_keys": {
            "type": "array", "items": {"type": "string"},
            "description": "사용자가 직접 언급한 티켓 키(DL-123 형식)만. 추측한 키는 넣지 마라",
        },
        "sufficient": {
            "type": "boolean",
            "description": ("되묻지 않고 바로 조사에 들어가도 될 만큼 요청이 구체적인가. "
                            "false 면 조사 **전에** 해석 확인·범위 질문이 먼저 나간다 — "
                            "처음 보는 기술 조합, 목적·범위가 막연한 신규 개발 요청"
                            "('~~하는 파이프라인을 개발해야 해' 한 줄)은 false 다. "
                            "티켓 키가 지목됐거나 범위·대상이 문장에 이미 있으면 true"),
        },
        "playbook": {
            "type": "string",
            "enum": ["", "epic_create", "task_create", "bug_report", "subtask_bulk",
                     "find_people", "find_tickets", "knowledge", "history", "workload",
                     "assign_fit", "asset_lookup"],
            "description": "요청이 전형적 패턴이면 해당 플레이북 — 사전 정의 플로우가 전 역할에 "
                           "주입돼 실수를 막는다. 애매하면 빈 문자열(자유 진행)",
        },
        "answer_depth": {
            "type": "string", "enum": ["brief", "explain"],
            "description": (
                "사용자가 원하는 답의 깊이. "
                "brief=값·결론만 원한다(무엇/언제/누구/얼마/어디 — '적재주기는?', '누가 담당?', "
                "'몇 건이야?', 목록 요청). "
                "explain=개념·배경·이유까지 원한다('왜', '어떻게 동작', '설명해줘', '정리해줘', "
                "'무슨 일이었는지', 처음 듣는 기술·용어를 물을 때). "
                "애매하면 brief — 사용자는 더 필요하면 다시 묻는다"),
        },
        "plan": {
            "type": "string",
            "description": "이 요청을 처리할 실행 계획 한 줄(2~4단계 화살표). "
                           "예: '사내 이력 검색 → 웹 기술 조사 → 초안 → 담당 추천'. "
                           "진행 표시로 사용자에게 보인다",
        },
    },
    "required": ["intent", "keywords", "sufficient"],
}


# 후속 턴의 지시대명사("그럼 마감 위험은?")는 앞 턴의 대상을 가리킨다. 사용자가 키를
# 다시 대지 않으므로 mentioned_keys 가 비고, 그러면 조사 대상이 사라져 **프로젝트 전체**를
# 답한다(실측: DL-9090 진척을 묻고 "마감까지 위험한 건?"에 무관한 티켓 3건을 나열).
import re as _re

# 지시대명사는 **낱말 경계로** 잡는다 — 맨 "그"로 부분일치를 하면 '카탈로그'가 걸린다(실측).
_ANAPHORA = _re.compile(
    r"(?:^|\s)(그|그거|그건|그럼|그러면|이거|이건|저거|거기|얘|해당|추가로|또)(?:\s|$|[은는이가을를에])"
    r"|남은|남는|위험|리스크|블로커|막힌")


def _carry_depth(state, out) -> str:
    """답변 깊이는 **대화 단위로 잇는다** — 한 번 설명형이면 그 대화는 설명형이다.

    깊이는 여태 매 턴 **마지막 발화만** 보고 다시 정해졌다. 그런데 우리가 확인 질문을 낸
    다음 턴에서 사용자가 하는 말은 대개 보기 하나다 — 그건 새 질문이 아니라 **우리 질문에
    대한 답**인데, 분류기에는 값 질문처럼 보인다.

    실측(배터리 DATA13): "fdc flat trace ic 데이터 히스토리 정리"(explain) → 표기 확인
    질문 → 사용자가 "fdc.fdc_trace_summary_ic" 를 고르자 그 턴이 brief 로 떨어졌고,
    Responder 의 "물어본 것만 답하라" 지시가 연표를 눌러 티켓 8건 중 2건만 남았다.
    재료(topic_dossier)에는 연표가 그대로 있었는데도 그랬다.

    **올리는 쪽으로만 붙인다.** explain 이 과했으면 사용자가 다음 턴에 좁히면 되지만,
    brief 로 떨어지면 물어본 것이 아예 답에서 사라진다 — 되돌릴 기회가 없다.
    """
    now = out.get("answer_depth") or "brief"
    return "explain" if "explain" in (now, str(state.get("answer_depth") or "")) else "brief"


def _carry_keys(state, out) -> list:
    """이번 턴이 댄 키가 우선. 없으면 **앞 턴의 대상을 이어받는다**(후속 질문일 때만).

    티켓 키 **형식만** 통과시킨다 — 스키마에 'DL-123 형식만'이라고 적어도 모델이 사번
    (skcc.x1450)을 넣었고, 그 오염이 modify 빠른 경로를 태워 조사를 통째로 건너뛰었다
    (실측 M2: 재배분 후보 사전취합이 실행될 기회조차 없었다)."""
    import re as _re
    keys = [k for k in (out.get("mentioned_keys") or [])
            if _re.match(r"^[A-Z][A-Z0-9]{1,9}-\d+$", str(k).strip())]
    if keys:
        return keys
    prev = [k for k in (state.get("mentioned_keys") or []) if str(k).strip()]
    if not prev or not (state.get("turns") or state.get("situation")
                        or state.get("ticket_progress")):
        return []
    asked = last_user_text(state).strip()
    # 짧은 되물음이거나 지시대명사가 있으면 같은 대상 이야기다. 새 주제를 길게 말했으면 아니다.
    if len(asked) <= 40 or _ANAPHORA.search(asked):
        return prev
    return []


class Planner(StructuredAgent):
    name = Node.PLANNER
    temperature = 0.0          # 분류는 흔들리면 안 된다
    tier = "simple"            # Few-shot 8예시가 실려서 분류는 저렴한 모델로 충분하다

    def system(self, state):
        return persona(state, SYSTEM_PLANNER, lite=True)   # 분류엔 축약판 — 호출당 1k+ 토큰 절감

    def task(self, state):
        # Few-shot — 경계가 애매한 갈래(ask↔progress↔activity, plan_work↔report_bug)를
        # 예시로 가른다. 규칙 문장보다 예시가 분류를 훨씬 안정시킨다(In-Context Learning).
        return f"""\
# 명령서
아래 대화에서 사용자가 원하는 것을 분류하고, 검색에 쓸 핵심어를 뽑아라.

## 제약조건
- 핵심어는 **검색용**이다. "해야 한다", "관련해서" 같은 말은 빼고 명사구만 남긴다.
- 티켓 키는 사용자가 실제로 적은 것만 옮긴다.
- 모듈은 확신이 있을 때만 고른다.

## 분류 예시
- "실시간 수집에 CDC를 도입해야 한다" → plan_work (새 일을 벌인다)
- "데이터 거버넌스 에픽 하나 새로 만들자" → plan_work (Epic 생성도 새 일 벌이기다)
- "DL-1234 밑에 서브태스크 여러 개 만들어줘" → plan_work (벌크 Sub-Task 생성)
- "적재 배치가 어젯밤부터 계속 실패한다" → report_bug (깨진 것을 알린다)
- "DL-101 어떻게 진행되고 있어?" → progress (티켓·Epic 의 진척 상태)
- "ETL 모듈 진척률 알려줘" → progress
- "ETL 마이그레이션 업무의 히스토리와 진척도, 최근 업데이트 알려줘" → ask (★ **복합 질의는
  조사가 주도** — 히스토리·경위가 섞이면 ask 다. 진척 숫자는 조사 단계가 도구로 함께 확인한다)
- "진행중인 티켓 중 2일 이상 업데이트 없는 것들 있니?" → progress (★ 팀 상태 점검 —
  현재 상태를 **집계**하는 질문은 이력 조사가 아니라 progress 다. 기준일 숫자는 핵심어에 남긴다)
- "나 오늘 뭐 해야 하지?" → my_day (자기 할 일)
- "내 모듈에 담당자 없는 업무 있으면 하나 하고 싶네" → my_day (★ 자기가 집을 일을 찾는
  것 — '내/우리' 가 주어면 팀 집계(progress)가 아니라 my_day 다)
- "skcc.x1042 최근 3일간 뭐 했어?" → activity (**사람**의 활동)
- "DL-101 관련자들이 요즘 어떤 일들을 해?" → activity (★ 티켓이 언급돼도 묻는 것이
  **사람들의 활동**이면 activity — 티켓 키는 mentioned_keys 에 담는다)
- "CDC 검토가 왜 멈췄었지?" → ask (과거 경위를 묻는다 — 상태 숫자가 아니라 이야기)
- "지난 분기에 성능 관련해서 어떤 논의가 있었어?" → ask (★ progress 아님 — 진척률 숫자가
  아니라 **지나간 논의·기록**을 찾는 질문이다. "어디까지 왔어"만 progress 다)
- "DL-207을 x1103에게 맡기는 게 적절할까?" → ask (★ **판단을 묻는 것** — 바꿔 달라는
  게 아니다. '바꿔줘/지정해줘'가 있어야 modify 다)
- "DL-207 담당자를 x1103 으로 바꿔줘" → modify
- "DL-207 마감을 다음 주로 미루고 사유도 코멘트로 남겨줘" → modify (★ 코멘트 요청이 섞여도
  기존 티켓의 속성을 바꾸는 것이 본론이면 modify — plan_work 가 아니다)
- "fdc.fdc_trace_summary_ic 데이터의 현재 적재주기는?" → ask (★ **자산의 속성 조회는
  progress 가 아니다** — 진척률이 아니라 기록에 적힌 사실을 찾는 일이다)
- "yms.yms_lot_yield_daily 스키마랑 변경 히스토리 알려줘" → ask
- "fdc.fdc_trace_summary_ic 적재하는 job 이름이랑 작업자 누구야?" → ask (★ '누구'가 나와도
  activity 가 아니다 — 사람의 활동이 아니라 **기록에 적힌 담당**을 찾는다)
- "Schema Registry 우리 어떻게 쓰고 있고 호환성 정책은 뭐야?" → ask (특정 기술의 사내 현황)

## 답변 깊이(answer_depth) 예시
- "fdc.fdc_trace_summary_ic 적재주기는?" → brief (값 하나면 끝)
- "DL-101 담당자 누구야?" → brief
- "이번 주 마감 지난 티켓 뭐 있어?" → brief (목록이 답이다)
- "CDC가 뭐고 우리는 어떻게 쓰고 있어?" → explain (개념+맥락을 물었다)
- "적재 지연이 왜 났고 어떻게 해결했어?" → explain (경위를 물었다)
- "Schema Registry 우리 어떻게 쓰고 있어?" → explain ('어떻게'는 설명 요구다)

## 계획(plan) 예시 — 의도별 표준 플랜(상황 맞게 다듬어 써라)
- plan_work: "사내 이력 검색 → (신기술이면 웹 조사) → 되묻기/초안 → 담당 후보 → 검증 → 승인"
- report_bug: "같은 증상 Bug 검색 → 재현경로 확인 → Bug 초안 → 담당 후보 → 승인"
- ask(지식): "사내 이력+의미 검색 → 웹 보강 → 개념/우리 상황/공백 정리"
- ask(적합성): "티켓 열람 → 후보 이력·워크로드 확인 → 근거 판단"
- ask(자산·주제 조사): "이름으로 언급 추적(코멘트 포함) → 변경 이력 확인 → 문서 본문 → 현재 값 확정"
- my_day: "내 일감 조회 → 지연·마감·정체 순위 → 오늘 우선순위 제안"
- progress: "대상 확정 → 진척률/조건 조회(JQL) → 분모 규칙과 함께 보고"
- activity: "로스터 확정 → 전원 활동 취합 → 로스터/모듈/개인 3층 정리"
- modify: "대상 티켓 확인 → 변경 계획 → 승인"

## 대화
{conversation(state)}"""

    def schema(self):
        return SCHEMA

    def apply(self, state, out):
        intent = out.get("intent") or Intent.PLAN_WORK
        kws = [k for k in (out.get("keywords") or []) if str(k).strip()]
        patch = {
            "intent": intent,
            "keywords": kws,
            "module": out.get("module") or "",
            "mentioned_keys": _carry_keys(state, out),
            "sufficient": bool(out.get("sufficient")),
            "playbook": out.get("playbook") or "",
            "answer_depth": _carry_depth(state, out),
            "trace": note(state, self.name,
                          f"의도={intent}"
                          + (f" · 계획: {str(out.get('plan'))[:80]}" if out.get("plan") else
                             f" 핵심어={', '.join(kws) or '없음'}")),
        }
        # ── 요약·브리핑 요청은 조회다 — "스탠드업 3줄 요약 만들어줘"가 plan_work 로
        # 분류되어 Epic 배치 인터뷰까지 갔다(실측). '만들어줘'의 대상이 글이면 ask.
        from app.agent.workflow.state import last_user_text as _lut
        _req = _lut(state)
        if intent == Intent.PLAN_WORK \
                and any(w in _req for w in ("요약", "브리핑", "정리해", "보고서")) \
                and not any(w in _req for w in ("티켓", "태스크", "테스크", "Task", "task",
                                                "이슈 등록", "에픽", "Epic")):
            # 모듈 현황 요약이면 집계(pmo)가 맞고, 지식·문서 요약이면 조사(ask)가 맞다.
            mods = ("ETL", "Catalog", "Runtime", "Workbench", "DataOps", "DevOps")
            intent = Intent.PROGRESS if any(m.lower() in _req.lower() for m in mods) \
                else Intent.ASK
            patch["intent"] = intent
        # ── "내가 할 만한 일" 은 **내 일감**이지 진척 집계가 아니다 ────────────
        # 실측(REC9): "지금 내가 할 만한 일 추천해줘" 가 실행마다 my_day / progress 로
        # 갈렸다. 두 갈래는 지나는 노드와 재료가 통째로 달라서(내 일감 사전취합 vs 진척률),
        # 갈리는 순간 답의 성격이 바뀐다. **1인칭 + '할 일'** 이라는 낱말 판정은 흔들릴
        # 이유가 없는 종류라 코드가 확정한다(이 저장소의 규율: 낱말 판정은 코드가 한다).
        # '추천' 하나만으로는 판정하지 않는다 — "내가 만들 티켓 추천해줘"는 생성이다.
        # 1인칭 판정은 **낱말 단위**로 한다 — 부분 문자열로 보면 "하나 더"의 '나'가 걸린다.
        _ME = {"나", "내", "내가", "나는", "나도", "나한테", "제가", "저", "저는", "저도", "저한테"}
        _mine = any(t.strip("의,.?!·").split("의")[0] in _ME for t in _req.split())
        if intent in (Intent.PROGRESS, Intent.ASK) and _mine \
                and any(w in _req for w in ("할 만한", "할만한", "할 일", "할일",
                                            "뭐 하지", "뭐부터", "무엇부터", "뭘 해야")):
            intent = patch["intent"] = Intent.MY_DAY

        # ★ 원 요청 고정 — 생성 갈래의 **첫 요청 턴**의 문장을 보존한다. 후속 턴(질문 답변)
        #   에서는 덮지 않는다: 제목·본문의 주제는 끝까지 이 문장이다(실측: 이게 없어서
        #   Epic 본문의 주제가 초안을 잠식했다). 후속 턴 판정은 refine 직행 라우트와 같은
        #   기준(조사 결과가 있고 되묻기 턴이 지났다)을 쓴다 — 두 판정이 갈리면 안 된다.
        from app.agent.workflow.state import last_user_text
        if intent in Intent.DRAFTS_TICKETS:
            # ★ 후속 턴 판정에 **조사 결과(situation)만** 보면, 조사 전에 되묻는 흐름에서
            #   고정이 통째로 무너진다. 해석 확인 선행 턴(`6eb8812`)은 Historian 을 안 타고
            #   질문부터 내므로 situation 이 빈 채 2턴이 시작되고, 그러면 여기서 원 요청이
            #   **사용자의 답변으로 덮인다.** 실측 STARR1: request_text 가
            #   "Epic 은 네가 골라줘…" 로 바뀌면서 원 요청의 "파이프라인"이 사라졌고,
            #   그 낱말에 걸려 있던 다단계 분할 가드(BUILD_WORDS)가 조용히 꺼졌다 —
            #   초안이 단일 Task 로 뭉갠 채 나갔는데 어디에도 경고가 없었다.
            #   **우리가 뭔가를 물었으면(questions·interpretation) 그 다음 턴은 답변 턴이다.**
            prior = (state.get("questions") or []) or (state.get("interpretation") or "").strip() \
                or ((state.get("draft") or {}).get("items") or []) \
                or (state.get("situation") or "").strip()
            follow_up = bool(prior) and (state.get("turns") or 0) > 0
            if not follow_up or not (state.get("request_text") or "").strip():
                patch["request_text"] = last_user_text(state)
        elif not (state.get("request_text") or "").strip():
            # ★ 조회 갈래에도 원 요청을 고정한다. 이 장치는 생성 갈래에만 걸려 있었는데,
            #   **답의 성격을 원 요청이 정하는 것은 조회도 같다**: "…히스토리" 로 시작한
            #   대화에서 표기 확인 질문에 답하면 그 턴의 발화는 "fdc.… 말한거야" 뿐이라,
            #   request_text 가 거기로 폴백되며 '히스토리'가 사라진다(실측 DATA11 —
            #   연표 대신 현재 값 표가 나왔다. 같은 흐름의 DATA13 은 1턴 문구가 우연히
            #   explain 으로 분류돼 그쪽 경로로만 살아남았다).
            #   비어 있을 때만 채운다 — 대화 도중 주제가 바뀌어도 대상은 식별자·핵심어가
            #   따라가고, 여기서 남는 것은 "무엇을 묻는 대화인가"뿐이다.
            patch["request_text"] = last_user_text(state)
        return patch
