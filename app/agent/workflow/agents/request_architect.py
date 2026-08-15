"""Request Architect — 무엇을 원하는 요청인지 가른다. 그래프의 첫 분기가 여기서 정해진다.

"DL-118 어떻게 됐어?"와 "CDC 도입해야 해"는 들어가야 할 길이 완전히 다르다. 전자는 찾아서
답하면 끝이고, 후자는 조사→구체화→담당자→검증→생성까지 간다. 이걸 매번 전 경로로 태우면
느리고 비싸다.

**분류를 Structured Output 으로 받는다.** "이건 업무 계획 요청 같습니다"라는 자유 서술을 받아
정규식으로 긁으면, 모델이 말투를 바꾸는 날 조용히 오분류된다. enum 으로 강제하면 그럴 일이 없다.
"""

from __future__ import annotations

from app.agent.workflow.agents.base import StructuredAgent
from app.agent.prompts.roles import SYSTEM_REQUEST_ARCHITECT
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import (AgentState, Intent, Node, conversation,
                                      last_user_text, note)

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [Intent.ASK, Intent.PLAN_WORK, Intent.MY_DAY,
                     Intent.PROGRESS, Intent.ACTIVITY, Intent.MODIFY, Intent.CHITCHAT],
            "description": (
                "ask=question about existing facts, history, rationale, or knowledge; "
                "plan_work=start new work and potentially draft a ticket tree, including a defect report "
                "whose Task-tier issue_type is Bug; my_day=the user's priorities today or this week; "
                "progress=current Epic/module/WBS progress or team-state audit such as stale, overdue, or "
                "unassigned tickets; activity=what a specific person or roster recently did; "
                "modify=change an existing ticket; chitchat=no work request"),
        },
        "keywords": {
            "type": "array", "items": {"type": "string"},
            "description": "Two to five noun phrases for retrieval, not a copy of the full request. Include "
                           "an acronym and its Korean expansion when useful. Preserve identifiers such as a "
                           "table, Job, or product name as one exact token; never split "
                           "fdc.fdc_trace_summary_ic into fragments.",
        },
        "module": {
            "type": "string",
            "enum": ["", "ETL", "Catalog", "Runtime", "Workbench", "Observability",
                     "DataOps", "DevOps"],
            "description": "Most likely module. Return an empty string when evidence is weak.",
        },
        "mentioned_keys": {
            "type": "array", "items": {"type": "string"},
            "description": "Only ticket keys explicitly mentioned by the user, in DL-123 form. Never guess.",
        },
        "sufficient": {
            "type": "boolean",
            "description": ("Whether the request is specific enough to begin safe research without a prior "
                            "clarification. Use false for materially ambiguous new development with no goal "
                            "or scope. Use true when a ticket key or concrete target and scope are present."),
        },
        "playbook": {
            "type": "string",
            "enum": ["", "epic_create", "task_create", "bug_report", "subtask_bulk",
                     "find_people", "find_tickets", "knowledge", "history", "workload",
                     "assign_fit", "asset_lookup", "topic_research"],
            "description": "The matching standard playbook for a recognizable pattern; otherwise empty.",
        },
        "answer_depth": {
            "type": "string", "enum": ["brief", "explain"],
            "description": (
                "Requested answer depth. brief=the value, conclusion, count, owner, date, location, or list; "
                "explain=concept, background, mechanism, rationale, or history. Default to brief."),
        },
        "goal": {"type": "string", "description": "One sentence covering the compound request's end result."},
        "tasks": {
            "type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"},
                "kind": {"type": "string", "enum": [
                    "query", "research", "analyze", "plan", "ticket", "comment", "write", "respond"]},
                "instruction": {"type": "string"},
                "depends_on": {"type": "array", "items": {"type": "string"}},
                "write_intent": {"type": "boolean"},
                "completion_criteria": {"type": "array", "items": {"type": "string"}},
            }, "required": ["id", "kind", "instruction", "depends_on", "write_intent",
                            "completion_criteria"], "additionalProperties": False},
            "description": "An executable atomic-task DAG. A simple request still has one task.",
        },
        "blocking_questions": {"type": "array", "items": {"type": "string"},
                               "description": "Only questions whose answers materially change the result."},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "plan": {
            "type": "string",
            "description": "One concise Korean progress line with two to four steps joined by arrows.",
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
    ResultIntegrator 의 "물어본 것만 답하라" 지시가 연표를 눌러 티켓 8건 중 2건만 남았다.
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


class RequestArchitect(StructuredAgent):
    name = Node.REQUEST_ARCHITECT
    temperature = 0.0          # 분류는 흔들리면 안 된다
    tier = "simple"            # Few-shot 8예시가 실려서 분류는 저렴한 모델로 충분하다

    def system(self, state):
        return persona(state, SYSTEM_REQUEST_ARCHITECT, lite=True)   # 분류엔 축약판 — 호출당 1k+ 토큰 절감

    def task(self, state):
        # Few-shot — 경계가 애매한 갈래(ask↔progress↔activity, ask↔modify)를
        # 예시로 가른다. 규칙 문장보다 예시가 분류를 훨씬 안정시킨다(In-Context Learning).
        return f"""\
# Task

Classify what the user wants from the conversation, construct an atomic task plan, and extract retrieval keywords.

## Constraints

- `Current User Message` is authoritative for this turn. Use older conversation only to resolve
  explicit anaphora or an interview answer; never preserve an old intent, entity, or write target
  when the latest message starts or temporarily switches to another request.
- Keywords are retrieval noun phrases. Remove filler such as `해야 한다` and `관련해서`.
- Copy only ticket keys explicitly written by the user.
- Select a module only with strong evidence.
- Write `goal`, `instruction`, `completion_criteria`, `blocking_questions`, `assumptions`, and `plan` in Korean because they are user-visible or preserve the Korean request.

## Intent Examples

- `실시간 수집에 CDC를 도입해야 한다` -> `plan_work`: start new work.
- `데이터 거버넌스 에픽 하나 새로 만들자` -> `plan_work`: Epic creation is new work.
- `DL-1234 밑에 서브태스크 여러 개 만들어줘` -> `plan_work`: bulk Sub-Task creation.
- `적재 배치가 어젯밤부터 계속 실패한다` -> `plan_work` with `playbook="bug_report"`: a defect report creates a Task-tier Bug.
- `DL-101 어떻게 진행되고 있어?` -> `progress`: current ticket or Epic progress.
- `ETL 모듈 진척률 알려줘` -> `progress`.
- `ETL 마이그레이션 업무의 히스토리와 진척도, 최근 업데이트 알려줘` -> `ask`: history makes research the leading path; research may also collect progress.
- `진행중인 티켓 중 2일 이상 업데이트 없는 것들 있니?` -> `progress`: current-state aggregation; keep the two-day criterion.
- `나 오늘 뭐 해야 하지?` -> `my_day`.
- `내 모듈에 담당자 없는 업무 있으면 하나 하고 싶네` -> `my_day`: the user wants work they can take, not a team-wide progress report.
- `skcc.x1042 최근 3일간 뭐 했어?` -> `activity`: a person's activity.
- `DL-101 관련자들이 요즘 어떤 일들을 해?` -> `activity`: the subject is people's activity; retain the ticket key.
- `CDC 검토가 왜 멈췄었지?` -> `ask`: historical rationale, not a progress metric.
- `지난 분기에 성능 관련해서 어떤 논의가 있었어?` -> `ask`: past discussion and records.
- `DL-207을 x1103에게 맡기는 게 적절할까?` -> `ask`: evaluate assignment; no mutation was requested.
- `DL-207 담당자를 x1103 으로 바꿔줘` -> `modify`.
- `DL-207 마감을 다음 주로 미루고 사유도 코멘트로 남겨줘` -> `modify`: an existing-ticket field change is the primary effect.
- `fdc.fdc_trace_summary_ic 데이터의 현재 적재주기는?` -> `ask`: asset-property lookup, not progress.
- `yms.yms_lot_yield_daily 스키마랑 변경 히스토리 알려줘` -> `ask`.
- `fdc.fdc_trace_summary_ic 적재하는 job 이름이랑 작업자 누구야?` -> `ask`: look up recorded ownership, not that person's activity.
- `Schema Registry 우리 어떻게 쓰고 있고 호환성 정책은 뭐야?` -> `ask`: internal state of a named technology.

## Answer Depth Examples

- `fdc.fdc_trace_summary_ic 적재주기는?` -> `brief`.
- `DL-101 담당자 누구야?` -> `brief`.
- `이번 주 마감 지난 티켓 뭐 있어?` -> `brief` because the list is the answer.
- `CDC가 뭐고 우리는 어떻게 쓰고 있어?` -> `explain`.
- `적재 지연이 왜 났고 어떻게 해결했어?` -> `explain`.
- `Schema Registry 우리 어떻게 쓰고 있어?` -> `explain`.

## Korean Progress-Plan Patterns

- `plan_work`: internal history -> optional external research for a named technology -> clarification or draft -> assignee candidates -> validation -> approval
- `plan_work` Bug: duplicate symptom search -> reproduction confirmation -> Bug draft -> assignee candidates -> approval
- knowledge `ask`: internal lexical and semantic search -> external supplement -> concepts, internal context, and gaps
- assignment-fit `ask`: read ticket -> inspect candidate history and workload -> evidence-backed judgment
- asset or topic `ask`: trace exact-name mentions including comments -> inspect change history -> read document body -> establish current value
- `my_day`: retrieve own workload -> rank overdue, due-soon, and stale items -> recommend today's priorities
- `progress`: resolve target -> retrieve progress or condition through JQL -> report denominator rules
- `activity`: resolve roster -> collect every member's activity -> organize roster, module, and person layers
- `modify`: verify target ticket -> build change plan -> approval

## Conversation Data

{conversation(state)}

## Current User Message

{last_user_text(state)}"""

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
            "request_plan": {
                "goal": out.get("goal") or last_user_text(state),
                "tasks": out.get("tasks") or [{
                    "id": "task-1", "kind": "query" if intent in Intent.NEEDS_RESEARCH else "respond",
                    "instruction": last_user_text(state), "depends_on": [],
                    "write_intent": intent in Intent.DRAFTS_TICKETS,
                    "completion_criteria": ["사용자 요청에 직접 답한다"],
                }],
                "blocking_questions": out.get("blocking_questions") or [],
                "assumptions": out.get("assumptions") or [],
            },
            "trace": note(state, self.name,
                          f"의도={intent}"
                          + (f" · 계획: {str(out.get('plan'))[:80]}" if out.get("plan") else
                             f" 핵심어={', '.join(kws) or '없음'}")),
        }
        # ── 요약·브리핑 요청은 조회다 — "스탠드업 3줄 요약 만들어줘"가 plan_work 로
        # 분류되어 Epic 배치 인터뷰까지 갔다(실측). '만들어줘'의 대상이 글이면 ask.
        from app.agent.workflow.state import last_user_text as _lut
        _req = _lut(state)
        from app.agent.workflow.meeting_context import is_meeting_request, meeting_request_text
        _meeting_request = meeting_request_text(state)
        if is_meeting_request(state):
            # Commenting on or editing existing tickets is mutation, not new-work planning.
            # Keep this stable after the user answers an identity/term interview.
            if (_re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", _meeting_request)
                    and ("댓글" in _meeting_request or "코멘트" in _meeting_request)
                    and _re.search(r"알려|남겨|달아|작성", _meeting_request)):
                intent = patch["intent"] = Intent.MODIFY
            elif (_re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", _meeting_request)
                  and _re.search(r"수정|바꿔|변경|교체", _meeting_request)):
                intent = patch["intent"] = Intent.MODIFY
        # 특정 사람의 "지금 맡은 업무"는 과거 티켓 주제와 무관한 현재 할당 조회다.
        # 대화 전문을 본 분류기가 직전 progress 대상을 유지한 CTX4 회귀를 최신 발화로 고정한다.
        if (_re.search(r"(?:지금|현재).{0,15}(?:맡|담당|할당).{0,8}(?:업무|일|티켓|태스크)", _req)
                and (_re.search(r"@[가-힣]{2,5}", _req)
                     or _re.search(r"(?:skcc\.)?[a-z]{1,2}\d{2,6}", _req, _re.I)
                     or _re.search(r"[가-힣]{2,5}?\s*(?:님|TL|M|차장|책임|매니저)?(?:이|가|은|는)?\s*"
                                   r"(?:지금|현재)", _req, _re.I))):
            intent = patch["intent"] = Intent.ACTIVITY
            patch["mentioned_keys"] = []
            patch["module"] = ""
            patch["playbook"] = "find_people"
            patch["request_plan"] = {
                "goal": "지목한 사람의 현재 미완료 할당 업무를 확인한다",
                "tasks": [{"id": "current-person-work", "kind": "query", "instruction": _req,
                           "depends_on": [], "write_intent": False,
                           "completion_criteria": ["사람을 정확히 식별한다", "현재 미완료 할당만 제시한다"]}],
                "blocking_questions": [], "assumptions": [],
            }
        # "보안교육 Task 누가 미완료했나"는 사람의 최근 활동(workload)이 아니라
        # 주제와 일치하는 parent Task → 직계 Sub-Task 전수 집계다. 분류가 activity/progress로
        # 흔들리면 Query Runner 자체를 못 지나므로, 낱말로 확정 가능한 이 유형은 코드가 고정한다.
        from app.agent.workflow.assignment_completion import asks_incomplete_assignees
        if asks_incomplete_assignees(_req):
            intent = patch["intent"] = Intent.ASK
            patch["playbook"] = "find_tickets"
            patch["answer_depth"] = "brief"
            patch["request_plan"] = {
                "goal": "주제와 일치하는 Task의 미완료 Sub-Task 담당자를 빠짐없이 확인한다",
                "tasks": [{
                    "id": "incomplete-assignees", "kind": "query",
                    "instruction": _req, "depends_on": [], "write_intent": False,
                    "completion_criteria": [
                        "상위 Task를 확정한다",
                        "직계 Sub-Task 전체를 statusCategory로 판정한다",
                        "미완료 Sub-Task를 담당자별로 빠짐없이 제시한다",
                    ],
                }],
                "blocking_questions": [], "assumptions": [],
            }
        if intent == Intent.PLAN_WORK \
                and any(w in _req for w in ("요약", "브리핑", "정리해", "보고서")) \
                and not any(w in _req for w in ("티켓", "태스크", "테스크", "Task", "task",
                                                "이슈 등록", "에픽", "Epic")):
            # 모듈 현황 요약이면 집계(pmo)가 맞고, 지식·문서 요약이면 조사(ask)가 맞다.
            mods = ("ETL", "Catalog", "Runtime", "Workbench", "DataOps", "DevOps")
            intent = Intent.PROGRESS if any(m.lower() in _req.lower() for m in mods) \
                else Intent.ASK
            patch["intent"] = intent
        # 조사 결과 자체가 산출물인 요청은 ticket creation이 아니다. `적용 가능성` 같은 표현이
        # 있어도 "조사해줘"로 끝나면 read-only research이며, 명시적 티켓 생성까지 있을 때만
        # plan_work를 유지한다. 이 가드가 없으면 WorkArchitect 한 호출(약 10k tokens)과 불필요한
        # 승인 질문이 붙었다(S7 focused run).
        if intent == Intent.PLAN_WORK and any(w in _req for w in ("조사해", "조사해줘", "리서치해")) \
                and not any(w in _req for w in ("티켓", "태스크", "테스크", "Task", "task",
                                                "이슈 등록", "만들어", "생성해")):
            intent = patch["intent"] = Intent.ASK
            patch["playbook"] = patch.get("playbook") or "topic_research"
        # ── "내가 할 만한 일" 은 **내 일감**이지 진척 집계가 아니다 ────────────
        # 실측(REC9): "지금 내가 할 만한 일 추천해줘" 가 실행마다 my_day / progress 로
        # 갈렸다. 두 갈래는 지나는 노드와 재료가 통째로 달라서(내 일감 사전취합 vs 진척률),
        # 갈리는 순간 답의 성격이 바뀐다. **1인칭 + '할 일'** 이라는 낱말 판정은 흔들릴
        # 이유가 없는 종류라 코드가 확정한다(이 저장소의 규율: 낱말 판정은 코드가 한다).
        # '추천' 하나만으로는 판정하지 않는다 — "내가 만들 티켓 추천해줘"는 생성이다.
        # ── ★ 구조 합의 중의 "빼줘/합쳐줘"는 **티켓 변경이 아니다** ──────────────
        # 실측(STRUCT2): 뼈대를 제안한 다음 턴에 "좋아, 근데 문서화는 빼줘" 라고 하니
        # intent 가 modify 로 갔다 — 아직 **만들어지지도 않은** 티켓을 고치러 간 것이다.
        # 그 갈래로 새면 사용자의 수정은 초안에 반영되지 않고 변경 카드만 헛돈다.
        # 승인 대기 구조가 있는 동안의 발화는 그 구조에 대한 말로 본다.
        if intent == Intent.MODIFY and (state.get("structure_plan") or []) \
                and not state.get("structure_ok"):
            intent = patch["intent"] = Intent.PLAN_WORK

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
        if intent in Intent.DRAFTS_TICKETS:
            # ★ 후속 턴 판정에 **조사 결과(situation)만** 보면, 조사 전에 되묻는 흐름에서
            #   고정이 통째로 무너진다. 해석 확인 선행 턴(`6eb8812`)은 ResearchAnalyst 을 안 타고
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

        # Identity/term interview answers refine the same meeting action.  Never replace its
        # exact target, field list, ticket shape, or comment boundary with the short answer.
        if is_meeting_request(state) and _meeting_request:
            patch["request_text"] = _meeting_request

        # A multi-stage new build whose ticket shape is still open should not trigger Jira/web research
        # merely to ask the same structure preference afterward. Ask that cheap, reversible choice first;
        # the next turn keeps the original request and performs research for the selected shape. Delegated
        # defaults and an explicitly named shape continue without an interview.
        if intent == Intent.PLAN_WORK and not (state.get("turns") or 0) \
                and not (state.get("situation") or "").strip() \
                and not ((state.get("draft") or {}).get("items")):
            from app.agent.workflow.agents.work_architect import (BUILD_WORDS, _said_defaults,
                                                                  shape_hint)
            original = str(patch.get("request_text") or _req)
            if (any(word in original for word in BUILD_WORDS)
                    and not shape_hint(state)[0] and not _said_defaults(state)):
                patch["interpretation"] = (
                    "신규 구축 요청의 티켓 구조를 먼저 확정한 뒤 관련 이력과 기술 근거를 조사하는 "
                    "것으로 이해함")
                patch["questions"] = [{
                    "question": "여러 단계로 나뉠 수 있는 작업입니다. 어떤 티켓 구조로 진행할까요?",
                    "kind": "choice", "field": "structure", "required_input": False,
                    "why_required": "",
                    "options": ["Task 하나 + 단계별 Sub-Task (권장)", "단일 Task로 구성"],
                }]
        return patch
