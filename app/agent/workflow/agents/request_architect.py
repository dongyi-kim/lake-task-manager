"""Request Architect — 무엇을 원하는 요청인지 가른다. 그래프의 첫 분기가 여기서 정해진다.

"DL-118 어떻게 됐어?"와 "CDC 도입해야 해"는 들어가야 할 길이 완전히 다르다. 전자는 찾아서
답하면 끝이고, 후자는 조사→구체화→담당자→검증→생성까지 간다. 이걸 매번 전 경로로 태우면
느리고 비싸다.

**분류를 Structured Output 으로 받는다.** "이건 업무 계획 요청 같습니다"라는 자유 서술을 받아
정규식으로 긁으면, 모델이 말투를 바꾸는 날 조용히 오분류된다. enum 으로 강제하면 그럴 일이 없다.
"""

from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

from app.agent.workflow.agents.base import StructuredAgent
from app.agent.prompts.roles import SYSTEM_REQUEST_ARCHITECT
from app.agent.workflow.continuation import (
    build_continuation_contract,
    has_current_continuation_decision,
    has_typed_continuation_contract,
    merge_continuation_decisions,
)
from app.agent.workflow.contracts import (
    QuestionContract, QuestionReceiptProjection, RequestQuestion,
)
from app.agent.workflow.effect_contract import issue_requested_update_effects
from app.agent.workflow.prompts import persona
from app.agent.workflow.state import (AgentState, Intent, Node, conversation,
                                      last_user_text, note)
from app.agent.workflow.typed_fast_path import (
    evaluate_typed_fast_path,
    typed_fast_path_note,
)
from app.agent.workflow.question_receipt import digest_value

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
            "type": "array", "maxItems": 5,
            "items": {"type": "string", "maxLength": 120},
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
        "goal": {"type": "string", "maxLength": 240,
                 "description": "One sentence covering the compound request's end result."},
        "tasks": {
            "type": "array", "minItems": 1, "maxItems": 6,
            "items": {"type": "object", "properties": {
                "id": {"type": "string", "maxLength": 60},
                "kind": {"type": "string", "enum": [
                    "query", "research", "analyze", "plan", "ticket", "comment", "write", "respond"]},
                "instruction": {"type": "string", "maxLength": 280},
                "depends_on": {"type": "array", "maxItems": 5,
                               "items": {"type": "string", "maxLength": 60}},
                "write_intent": {"type": "boolean"},
                "completion_criteria": {"type": "array", "minItems": 1, "maxItems": 3,
                                        "items": {"type": "string", "maxLength": 160}},
            }, "required": ["id", "kind", "instruction", "depends_on", "write_intent",
                            "completion_criteria"], "additionalProperties": False},
            "description": ("User-requested deliverables/actions only, not the Agent's internal research, "
                            "analysis, validation, approval, or response stages. A simple request has exactly "
                            "one task; decompose only genuinely compound requests."),
        },
        "blocking_questions": {"type": "array", "maxItems": 3,
                               "items": {"type": "string", "maxLength": 240},
                               "description": "Only questions whose answers materially change the result."},
        "request_questions": {
            "type": "array", "maxItems": 3,
            "items": RequestQuestion.model_json_schema(),
            "description": ("Missing inputs: target/action only when the object/operation is "
                             "absent; scope/acceptance/other never block."),
        },
        "requested_effects": {
            "type": "array", "maxItems": 3, "uniqueItems": True,
            "items": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "pattern": r"^[A-Z][A-Z0-9]{1,9}-\d+$"},
                    "field": {"type": "string", "enum": ["priority", "duedate", "summary"]},
                    "value": {"type": "string", "minLength": 1, "maxLength": 240},
                    "literal": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                "required": ["target", "field", "value", "literal"],
                "additionalProperties": False,
            },
            "description": (
                "Exact scalar mutations only for modify requests. Emit final canonical values "
                "and copy its exact raw value span into literal for explicit current-user "
                "targets; omit ambiguous, inferred, unsupported, or multi-valued fields."
            ),
        },
        "assumptions": {"type": "array", "maxItems": 5,
                        "items": {"type": "string", "maxLength": 200}},
        "plan": {
            "type": "string", "maxLength": 200,
            "description": "One concise Korean progress line with two to four steps joined by arrows.",
        },
    },
    "required": ["intent", "keywords", "sufficient", "request_questions",
                 "requested_effects"],
}


# 후속 턴의 지시대명사("그럼 마감 위험은?")는 앞 턴의 대상을 가리킨다. 사용자가 키를
# 다시 대지 않으므로 mentioned_keys 가 비고, 그러면 조사 대상이 사라져 **프로젝트 전체**를
# 답한다(실측: DL-9090 진척을 묻고 "마감까지 위험한 건?"에 무관한 티켓 3건을 나열).
import copy as _copy
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
    asked = last_user_text(state)
    # These forms explicitly request rationale, mechanism, or history.  A smaller routing
    # model occasionally returned brief even though no judgment is needed to detect them.
    if _re.search(r"왜|원인|어떻게|히스토리|이력|뭐고|무엇이고|설명해|배경", asked, _re.I):
        return "explain"
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


def _explicit_user_effects(text: str) -> set[str]:
    """Return independently visible outcomes explicitly requested by the user.

    The Request Architect model occasionally described our internal pipeline (query ->
    analysis -> draft -> response) as four user tasks.  Those are execution stages, not
    four deliverables.  This small lexical boundary does not plan the work; it only tells
    us whether preserving a multi-task model DAG is justified by multiple visible effects.
    """
    patterns = {
        "research": r"조사|리서치|검색|찾아|확인|분석|비교|히스토리|이력",
        "ticket": r"(?:티켓|태스크|테스크|이슈|에픽|Epic|Task).{0,18}(?:생성|만들|등록|산출|올려)",
        "comment": r"(?:댓글|코멘트).{0,18}(?:작성|남겨|달아|알려)",
        "modify": r"(?:제목|필드|본문|설명|담당자|마감|기한|상태).{0,18}(?:수정|변경|바꿔|교체)",
        "document": r"(?:문서|보고서|회의록|요약|브리핑).{0,18}(?:작성|정리|만들|산출)",
    }
    return {name for name, pattern in patterns.items() if _re.search(pattern, text, _re.I)}


def _task_outcome_effect(task: dict) -> str:
    """Project a typed atomic task to its independently visible user effect."""
    kind = str((task or {}).get("kind") or "").strip().casefold()
    if kind in {"query", "research", "analyze"}:
        return "research"
    if kind == "comment":
        return "comment"
    if kind == "write":
        return "document"
    if kind in {"plan", "ticket", "modify"}:
        return "ticket"
    return ""


def _task_outcome_signature(task: dict) -> tuple[str, str]:
    """Return the stable semantic identity used only to suppress echoed outcomes."""
    return (
        _task_outcome_effect(task),
        " ".join(str((task or {}).get("instruction") or "").casefold().split()),
    )


_PARALLEL_OUTCOMES = _re.compile(r"(?:각각|각\s*(?:한|1)\s*(?:건|개|곳))", _re.I)
_TYPED_ARTIFACT = _re.compile(
    r"(?<![A-Za-z])(?:Bug|Story|Feature|Improvement|Task|Sub-?Task|Epic)"
    r"(?=$|[\s,.;:!?()]|[은는이가을를와과및도])|"
    r"(?:버그|스토리|피처|임프로브먼트|태스크|테스크|서브\s*태스크|에픽|티켓|"
    r"댓글|코멘트|문서|보고서)",
    _re.I,
)
_OUTCOME_ACTION = _re.compile(r"(?:만들|생성|등록|작성|남겨|달아|산출|올려)", _re.I)
_EXPLICIT_OUTCOME_COUNT = _re.compile(
    r"(?P<count>[2-6]|두|세|네|다섯|여섯)\s*(?:건|개)", _re.I)
_COUNT_VALUE = {"두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6}


def _parallel_outcome_count(text: str) -> int:
    """Return a user-grounded outcome count, or zero when multiplicity is ambiguous."""
    artifact_matches = list(_TYPED_ARTIFACT.finditer(text))
    jira_targets = _re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", text)
    explicit = _EXPLICIT_OUTCOME_COUNT.search(text)
    if explicit and artifact_matches:
        # The count must syntactically belong to the artifact (``Task 2건`` or
        # ``2건의 Task``). A later attribute count such as ``Task 완료 조건 2개`` is not
        # evidence for two independent tickets.
        attached = any(
            (match.end() <= explicit.start()
             and bool(_re.fullmatch(
                 r"\s*(?:(?:은|는|이|가|을|를)\s*)?(?:(?:정확히|총)\s*)?",
                 text[match.end():explicit.start()], _re.I)))
            or (explicit.end() <= match.start()
                and bool(_re.fullmatch(r"\s*(?:의\s*)?",
                                       text[explicit.end():match.start()], _re.I)))
            for match in artifact_matches)
        if attached:
            raw = explicit.group("count")
            return int(raw) if raw.isdigit() else _COUNT_VALUE.get(raw, 0)

    # ``Bug 1건과 Story 1건 만들어줘`` has one shared action but two independently counted
    # deliverable clauses.  Count only a unit immediately attached to each typed artifact.
    per_item = sum(bool(_re.match(
        r"\s*(?:은|는|이|가|을|를|와|과|및|,)?\s*(?:1|한)\s*(?:건|개)",
        text[match.end():match.end() + 18], _re.I)) for match in artifact_matches)
    if per_item >= 2:
        return per_item

    candidates = max(len(artifact_matches), len(jira_targets))
    if candidates >= 2 and _PARALLEL_OUTCOMES.search(text):
        return candidates
    action_count = len(_OUTCOME_ACTION.findall(text))
    if candidates >= 2 and action_count >= 2:
        return min(candidates, action_count)
    return 0


def _has_explicit_parallel_outcomes(user_text: str, tasks: list[dict]) -> bool:
    """Recognize multiple user-owned outcomes even when their effect type is identical.

    A set of effects cannot distinguish ``Bug + Story`` from one generic ticket. Preserve the
    model's atomic tasks only when the user also supplied a high-precision multiplicity marker,
    exact count, or repeated artifact actions. A single compound expression whose analysis and
    fix are merely content of one Task therefore still collapses.
    """
    text = str(user_text or "")
    if len(tasks) < 2:
        return False
    task_effects = {_task_outcome_effect(task) for task in tasks}
    task_effects.discard("")
    # This branch exists only for multiple outcomes of one effect type. Mixed-effect plans
    # must be grounded by the ordinary explicit-effect contract above.
    if len(task_effects) != 1:
        return False
    expected = _parallel_outcome_count(text)
    return expected >= 2 and len(tasks) == expected


def _compact_request_tasks(out: dict, user_text: str, intent: str, *,
                           pin_single_write: bool = True) -> list[dict]:
    """Keep a model DAG only for genuinely compound, independently checkable outcomes."""
    tasks = [task for task in (out.get("tasks") or []) if isinstance(task, dict)]
    effects = _explicit_user_effects(user_text)
    if len(tasks) <= 1:
        # A small structured model sometimes describes the Agent's first internal step
        # (``query`` or ``research``) instead of the user's requested new-work outcome.
        # One plan_work request is still one writable planning outcome; acquisition remains
        # an implementation detail of the graph.
        if intent == Intent.PLAN_WORK and tasks and tasks[0].get("kind") in {
                "query", "research", "analyze", "respond"}:
            task = dict(tasks[0])
            task["kind"] = "plan"
            task["instruction"] = user_text
            task["write_intent"] = True
            return [task]
        if (pin_single_write and tasks and _is_write_outcome(tasks[0])
                and str(user_text or "").strip()):
            # A one-outcome plan has no semantic decomposition for the model to add.  Its
            # instruction is the downstream authority boundary, so keep the exact current
            # request—including explicit acceptance/safety constraints—and do not promote
            # model-authored examples, assumptions, or delegated implementation choices to
            # user requirements.  Multi-outcome requests retain their atomic model mapping.
            task = dict(tasks[0])
            task["instruction"] = str(user_text).strip()
            return [task]
        return tasks
    task_effects = {_task_outcome_effect(task) for task in tasks}
    task_effects.discard("")
    explicit_task_effects = set(effects)
    if "modify" in explicit_task_effects:
        explicit_task_effects.add("ticket")
    if (len(task_effects & explicit_task_effects) >= 2
            or _has_explicit_parallel_outcomes(user_text, tasks)):
        return tasks

    write_intent = intent in Intent.DRAFTS_TICKETS or bool(
        effects & {"ticket", "comment", "modify"})
    if "comment" in effects:
        kind = "comment"
    elif effects & {"ticket", "modify"}:
        kind = "ticket"
    elif "document" in effects:
        kind = "write"
    elif effects & {"research"}:
        kind = "research"
    elif intent == Intent.PLAN_WORK:
        kind = "plan"
    elif intent in Intent.NEEDS_RESEARCH:
        kind = "query"
    else:
        kind = "respond"
    goal = str(out.get("goal") or user_text).strip()
    return [{
        "id": "task-1",
        "kind": kind,
        "instruction": user_text,
        "depends_on": [],
        "write_intent": write_intent,
        "completion_criteria": [
            (goal[:120] + "에 필요한 결과를 확인 가능한 형태로 제시한다")[:160],
        ],
    }]


_WRITE_OUTCOME_KINDS = {"plan", "ticket", "write", "comment", "modify"}


def _is_write_outcome(task) -> bool:
    return bool(isinstance(task, dict) and (
        task.get("write_intent") is True
        or str(task.get("kind") or "").strip().casefold() in _WRITE_OUTCOME_KINDS
    ))


_OUTCOME_LABELS = {
    "comment": r"(?:댓글|코멘트)",
    "ticket": r"(?:Bug|Story|Feature|Improvement|Task|Sub-?Task|Epic|버그|스토리|피처|"
              r"태스크|테스크|서브\s*태스크|에픽|티켓|이슈)",
    "research": r"(?:조사|리서치|검색|분석|이력\s*확인)",
    "document": r"(?:문서|보고서|회의록|브리핑)",
}
_OUTCOME_REMOVE_ACTION = r"(?:빼|제외|취소|삭제|없애|하지\s*마|안\s*(?:해|할))"
_OUTCOME_CHANGE_ACTION = r"(?:바꿔|수정|변경|교체)"
_OUTCOME_ADD_COUNT = r"(?:하나|한\s*(?:건|개)|1\s*(?:건|개)?)"
_OUTCOME_ADD_ACTION = r"(?:만들|생성|등록|작성|남겨|달아|산출|올려)"


def _continuation_outcome_additions(text: str) -> list[str]:
    """Return ordered, explicitly typed outcome effects that the user wants to add.

    ``더`` alone is only an adverb and must never inherit a stale plan. Addition requires a
    visible artifact plus a singular count and either the additive particle ``도`` with a
    creation action, or ``하나 더`` ending at an action/punctuation/field boundary. The latter
    boundary rejects ambiguous prose such as ``Task 하나 더 자세히 설명해줘``.
    """
    value = str(text or "")
    matches: list[tuple[int, str]] = []
    field_boundary = (
        r"(?:범위|단계|phase|stage|마감|기한|due(?:\s*date)?|deadline|"
        r"담당(?:자)?|assignee|owner|우선순위|priority|제목|본문|설명|완료\s*조건)"
    )
    for effect, label in _OUTCOME_LABELS.items():
        one_more = _re.finditer(
            fr"(?:{label})(?:은|는|이|가|을|를)?\s*{_OUTCOME_ADD_COUNT}\s*더"
            fr"(?=\s*(?:$|[,.!?;:\n]|{_OUTCOME_ADD_ACTION}|{field_boundary}))",
            value, _re.I,
        )
        also_create = _re.finditer(
            fr"(?:{label})\s*도\s*{_OUTCOME_ADD_COUNT}\s*(?:더\s*)?"
            fr"{_OUTCOME_ADD_ACTION}",
            value, _re.I,
        )
        matches.extend((match.start(), effect) for match in one_more)
        matches.extend((match.start(), effect) for match in also_create)
    ordered: list[str] = []
    for _, effect in sorted(matches):
        if effect not in ordered:
            ordered.append(effect)
    return ordered


def _continuation_outcome_directive(text: str) -> dict:
    """Parse only explicit, typed outcome addition/removal/replacement instructions.

    Pronouns such as ``그건 빼줘`` intentionally produce no directive: mapping one pronoun to
    one of several writes is semantic and the authoritative prior plan must win when ambiguous.
    """
    value = str(text or "")
    remove, only, change = set(), set(), set()
    add = _continuation_outcome_additions(value)
    for effect, label in _OUTCOME_LABELS.items():
        explicit_remove = _re.search(
            fr"(?:{label})(?:은|는|을|를)?(?:\s|내용|대상|작업)*.{{0,24}}"
            fr"{_OUTCOME_REMOVE_ACTION}", value, _re.I)
        immediate_alternative = _re.search(
            fr"(?:{label})(?:은|는|을|를)?\s*(?:말고|대신)(?:\s|$)", value, _re.I)
        if explicit_remove or immediate_alternative:
            remove.add(effect)
        if _re.search(fr"(?:{label})(?:은|는|을|를)?\s*만(?:\s|으로|$)", value, _re.I):
            only.add(effect)
        if (effect not in remove
                and _re.search(fr"(?:{label})(?:\s*(?:내용|대상|범위|결론|제목))?(?:은|는|을|를)?"
                               fr".{{0,40}}{_OUTCOME_CHANGE_ACTION}", value, _re.I)):
            change.add(effect)
    return {"remove": remove, "only": only, "change": change, "add": add} \
        if remove or only or change or add else {}


def _authoritative_continuation_plan(state: dict) -> dict:
    """Return the prior user-outcome plan only across an explicit session continuation.

    Research and terminology interviews can stop before WorkArchitect increments ``turns``.
    The next RequestArchitect call sees a short answer rather than the compound request and a
    smaller model may replace two requested writes with one control task.  Session's explicit
    boundary is authoritative here: a new topic/cancellation clears ``turn_continuation`` and
    ``request_plan`` before this role runs, while a true continuation keeps the whole atomic DAG.
    """
    if not state.get("turn_continuation"):
        return {}
    plan = state.get("request_plan") or {}
    tasks = [task for task in (plan.get("tasks") or []) if isinstance(task, dict)] \
        if isinstance(plan, dict) else []
    if not tasks or not any(_is_write_outcome(task) for task in tasks):
        return {}
    fixed = _copy.deepcopy(plan)
    fixed["tasks"] = _copy.deepcopy(tasks)
    return fixed


def _authoritative_read_continuation_plan(state: dict) -> dict:
    """Return a read-only outcome DAG across a typed interview answer.

    The existing continuation guard historically protected writes only.  A short target or
    terminology answer could therefore turn ``summarize these decisions`` into a fresh Task
    creation when the classifier focused on words such as ``계속해줘``.  The typed Session
    contract proves both the boundary and the effect family; arbitrary message history does not.
    """
    if not state.get("turn_continuation"):
        return {}
    if not has_typed_continuation_contract(state.get("continuation_contract")):
        return {}
    contract = build_continuation_contract(
        state, existing=state.get("continuation_contract"),
    )
    if contract.get("action") != "read":
        return {}
    plan = state.get("request_plan") or {}
    tasks = [task for task in (plan.get("tasks") or []) if isinstance(task, dict)] \
        if isinstance(plan, dict) else []
    if not tasks or any(_is_write_outcome(task) for task in tasks):
        return {}
    fixed = _copy.deepcopy(plan)
    fixed["tasks"] = _copy.deepcopy(tasks)
    return fixed


def _continuation_write_intent(state: dict, fallback: str) -> str:
    """Keep the routing intent paired with an authoritative prior write plan."""
    plan = _authoritative_continuation_plan(state)
    if not plan:
        return fallback
    prior = str(state.get("intent") or "")
    if prior in Intent.DRAFTS_TICKETS:
        return prior
    # Legacy checkpoints may predate session intent preservation.  A ticket/plan/write outcome
    # is new-work planning; a comment-only mutation is modification.  Never infer a read intent
    # from the interview answer because that would strand the preserved write outcomes.
    kinds = {str(task.get("kind") or "").strip().casefold()
             for task in plan.get("tasks") or [] if _is_write_outcome(task)}
    return Intent.PLAN_WORK if kinds & {"plan", "ticket", "write"} else Intent.MODIFY


def _legacy_followup_write_outcome(state: dict, tasks: list[dict], intent: str) -> list[dict]:
    """Repair a legacy continuation that has no prior typed RequestPlan."""
    established_continuation = bool(state.get("turn_continuation") or state.get("questions"))
    if intent not in Intent.DRAFTS_TICKETS or not established_continuation:
        return tasks
    original = str(state.get("request_text") or "").strip()
    if not original:
        return tasks
    write_indices = [index for index, task in enumerate(tasks or []) if _is_write_outcome(task)]
    if len(write_indices) != 1:
        return tasks
    fixed = [dict(task) if isinstance(task, dict) else task for task in tasks]
    fixed[write_indices[0]]["instruction"] = original
    fixed[write_indices[0]]["write_intent"] = True
    return fixed


def _project_followup_outcomes(state: dict, tasks: list[dict], intent: str) -> tuple[list[dict], bool]:
    """Overlay an explicit bounded typed diff on the authoritative prior outcome DAG."""
    prior_plan = _authoritative_continuation_plan(state)
    if not prior_plan:
        return _legacy_followup_write_outcome(state, tasks, intent), False

    prior_tasks = _copy.deepcopy((prior_plan.get("tasks") or [])[:6])
    directive = _continuation_outcome_directive(last_user_text(state))
    if not directive:
        return prior_tasks, False

    remove = set(directive.get("remove") or [])
    only = set(directive.get("only") or [])
    change = set(directive.get("change") or []) - remove
    add = list(dict.fromkeys(directive.get("add") or []))
    projected = []
    for task in prior_tasks:
        effect = _task_outcome_effect(task)
        if effect in remove or (only and effect not in only):
            continue
        projected.append(task)
    changed = projected != prior_tasks

    # A typed replacement updates exactly one matching prior outcome with exactly one matching
    # current model task. Preserve the stable source id and dependency boundary; otherwise the
    # mapping is ambiguous and that effect remains unchanged.
    for effect in change:
        prior_indexes = [index for index, task in enumerate(projected)
                         if _task_outcome_effect(task) == effect]
        replacements = [task for task in tasks[:6]
                        if isinstance(task, dict) and _task_outcome_effect(task) == effect]
        if len(prior_indexes) != 1 or len(replacements) != 1:
            continue
        index = prior_indexes[0]
        old = projected[index]
        replacement = _copy.deepcopy(replacements[0])
        replacement["id"] = old.get("id")
        replacement["depends_on"] = _copy.deepcopy(old.get("depends_on") or [])
        replacement["write_intent"] = bool(old.get("write_intent") or
                                             _is_write_outcome(replacement))
        projected[index] = replacement
        changed = changed or replacement != old

    # ``Task만`` may explicitly replace a comment-only prior plan. Add one model-projected task
    # only when the requested type and the current typed task are both unambiguous.
    for effect in only:
        if any(_task_outcome_effect(task) == effect for task in projected):
            continue
        replacements = [task for task in tasks[:6]
                        if isinstance(task, dict) and _task_outcome_effect(task) == effect]
        if len(replacements) == 1 and len(projected) < 6:
            projected.append(_copy.deepcopy(replacements[0]))
            changed = True

    # An explicit ``Task 하나 더`` / ``Task도 하나 만들어줘`` adds one independently visible
    # outcome after the preserved DAG. The semantic model authors only the new outcome; code
    # owns stable prior ids/order and rejects ambiguous, duplicate, or over-capacity additions.
    for effect in add:
        existing_signatures = {_task_outcome_signature(task) for task in projected
                               if isinstance(task, dict)}
        replacements = [task for task in tasks[:6]
                        if (isinstance(task, dict)
                            and _task_outcome_effect(task) == effect
                            and _task_outcome_signature(task) not in existing_signatures)]
        if len(replacements) != 1 or len(projected) >= 6:
            continue
        candidate = _copy.deepcopy(replacements[0])
        if not _task_outcome_signature(candidate)[1]:
            continue
        existing_ids = {str(task.get("id") or "").strip()
                        for task in projected if isinstance(task, dict)}
        candidate_id = str(candidate.get("id") or "").strip() or f"followup-{effect}"
        if candidate_id in existing_ids:
            base = candidate_id
            suffix = 2
            while f"{base}-{suffix}" in existing_ids:
                suffix += 1
            candidate_id = f"{base}-{suffix}"
        candidate["id"] = candidate_id
        projected.append(candidate)
        changed = True

    if not projected and (remove or only):
        projected = [{
            "id": "continuation-cancel", "kind": "respond",
            "instruction": last_user_text(state), "depends_on": [],
            "write_intent": False, "completion_criteria": ["취소된 산출물을 다시 실행하지 않는다"],
        }]
        changed = True

    valid_ids = {str(task.get("id") or "") for task in projected if isinstance(task, dict)}
    for task in projected:
        if isinstance(task, dict):
            task["depends_on"] = [str(value) for value in (task.get("depends_on") or [])
                                  if str(value) in valid_ids and str(value) != str(task.get("id") or "")]
    return projected[:6], changed


def _preserve_followup_write_outcome(state: dict, tasks: list[dict], intent: str) -> list[dict]:
    """Keep the prior DAG, except for a high-confidence typed outcome diff.

    A reply such as ``Epic은 골라줘, 마감은 ...`` refines *how* to execute the
    already-frozen creation request; it is not the requested artifact by itself.  Small
    models repeatedly replaced the atomic write task with that control-only reply, so the
    downstream requested-outcome contract no longer contained the object being created.  If
    the prior typed plan exists, preserve its complete task DAG—including non-write outcomes
    that a write depends on—rather than trying to remap a short answer semantically.  The
    original single-write repair remains only for legacy states without a request plan.
    """
    return _project_followup_outcomes(state, tasks, intent)[0]


_EPIC_SELECTION = _re.compile(
    r"(?:Epic|에픽)\s*(?:은|는|을|를)?\s*(?:네가\s*)?(?:골라|선택|찾아|정해)"
    r"|(?:골라|선택|찾아|정해).{0,12}(?:Epic|에픽)",
    _re.I,
)
_EPIC_CREATION = _re.compile(
    r"(?:새(?:로운)?\s*)?(?:Epic|에픽)\s*(?:을|를)?\s*"
    r"(?:새로\s*)?(?:생성|만들|등록)",
    _re.I,
)
_FALLBACK_CREATION = _re.compile(
    r"(?:없으면|없을\s*경우|찾지\s*못하면|적합한\s*(?:것|Epic|에픽)?이?\s*없으면)"
    r".{0,24}(?:새로\s*)?(?:생성|만들|등록)",
    _re.I,
)
_EPIC_CREATION_PHRASE = _re.compile(
    r"(?:새(?:로운)?\s*)?(?:Epic|에픽)\s*(?:을|를)?\s*"
    r"(?:새로\s*)?(?:생성(?:하기|해|함)?|만들(?:기|어|어야|자)?|등록(?:하기|해|함)?)",
    _re.I,
)


# A continuation may contain only typed execution fields whose meaning is already fixed by
# code. In that narrow case re-running RequestArchitect asks the model to rediscover the
# authoritative RequestPlan from a short control message, adding latency and sometimes losing
# its subject. These patterns intentionally cover only parent placement, an exact ISO due date,
# and a numeric phase ordinal. Free-form scope, relative dates, owners, titles, descriptions,
# and outcome mutations still need semantic classification.
_FAST_PARENT_SELECTION_FIELD = _re.compile(
    r"(?:(?:관련|적합한|기존)\s+)*(?:상위\s+)?(?:Epic|에픽)\s*(?:은|는|을|를)?\s*"
    r"(?:네가\s*)?(?:알아서\s*)?(?:골라|선택|찾아|정해)(?:\s*(?:줘|주세요|해줘))?",
    _re.I,
)
_FAST_EXACT_PARENT_FIELD = _re.compile(
    r"(?:부모|상위)\s*(?:Epic|에픽)\s*(?:은|는|을|를|로|:|=)?\s*"
    r"(?P<key>(?<![A-Z0-9])[A-Z][A-Z0-9]{1,9}-\d+(?![A-Z0-9]))"
    r"\s*(?:으로|로|을|를|에)?\s*"
    r"(?:연결|지정|선택|붙여)(?:\s*(?:해줘|해주세요|줘))?",
    _re.I,
)
_FAST_TOP_LEVEL_FIELD = _re.compile(
    r"(?:최상위\s*(?:Task|태스크|테스크)(?:로|으로)?(?:\s*(?:진행|구성))?"
    r"|(?:부모|상위\s*(?:Epic|에픽)|Epic|에픽)\s*(?:은|는|을|를)?\s*"
    r"(?:없음|없이|빼|제외))(?:\s*(?:해줘|해주세요|진행해줘))?",
    _re.I,
)
_FAST_DUE_FIELD = _re.compile(
    r"(?:(?:마감(?:일)?|기한|due(?:\s*date)?|deadline)\s*"
    r"(?:은|는|을|를|로|:|=)?\s*(?P<prefix>\d{4}-\d{2}-\d{2})\s*(?:까지|으로|로)?"
    r"|(?P<suffix>\d{4}-\d{2}-\d{2})\s*(?:까지|를?\s*마감(?:일)?로|를?\s*기한으로))",
    _re.I,
)
_FAST_PHASE_FIELD = _re.compile(
    r"(?:(?:범위|단계|phase|stage)\s*(?:은|는|을|를|로|:|=)?\s*)?"
    r"(?:(?:최소|우선)\s*)?(?:기능\s*)?(?P<ordinal>\d{1,3}\s*차)\s*"
    r"(?P<action>설계|기획|구현|개발|적용|검증|테스트|측정|배포|전환|범위|단계)?"
    r"\s*(?:까지|로)?",
    _re.I,
)
_FAST_REFINEMENT_GLUE = _re.compile(
    r"(?:[\s,.;:!?/()\[\]{}]+|그리고|그럼|그러면|또|추가로|알아서|"
    r"이대로|그대로|계속|진행(?:해주세요|해줘|해)?|부탁(?:드립니다|해줘|해)?)",
    _re.I,
)


def _valid_iso_date(value: str) -> bool:
    try:
        from datetime import date
        date.fromisoformat(str(value or ""))
        return True
    except (TypeError, ValueError):
        return False


def _typed_continuation_refinement(text: str) -> dict:
    """Return a completely parsed field-only refinement, otherwise an empty dict.

    Every non-punctuation character must belong to one recognized field or harmless discourse
    glue. Conflicting/duplicate values fail closed. Requiring at least two distinct fields
    mirrors Session's no-question continuation boundary and prevents a stray date or ``1차``
    from inheriting an old write request.
    """
    value = str(text or "").strip()
    if not value or len(value) > 320 or _continuation_outcome_directive(value):
        return {}

    spans: list[tuple[int, int, str, str]] = []
    for match in _FAST_PARENT_SELECTION_FIELD.finditer(value):
        spans.append((match.start(), match.end(), "parent", "select_existing"))
    for match in _FAST_EXACT_PARENT_FIELD.finditer(value):
        spans.append((match.start(), match.end(), "parent", match.group("key").upper()))
    for match in _FAST_TOP_LEVEL_FIELD.finditer(value):
        spans.append((match.start(), match.end(), "parent", "top_level"))
    for match in _FAST_DUE_FIELD.finditer(value):
        due = match.group("prefix") or match.group("suffix") or ""
        if not _valid_iso_date(due):
            return {}
        spans.append((match.start(), match.end(), "duedate", due))
    for match in _FAST_PHASE_FIELD.finditer(value):
        ordinal = _re.sub(r"\s+", "", match.group("ordinal") or "")
        spans.append((match.start(), match.end(), "phase", ordinal))

    spans.sort(key=lambda row: (row[0], row[1]))
    fields: dict[str, str] = {}
    covered = [False] * len(value)
    for start, end, field, parsed in spans:
        if any(covered[start:end]):
            return {}
        if field in fields:  # repeated fields can conceal alternatives or contradictions
            return {}
        fields[field] = parsed
        for index in range(start, end):
            covered[index] = True

    if len(fields) < 2:
        return {}
    remainder = "".join(" " if covered[index] else char for index, char in enumerate(value))
    remainder = _FAST_REFINEMENT_GLUE.sub("", remainder)
    if remainder.strip():
        return {}
    return fields


_EXECUTION_ACTION_FAMILIES = (
    ("design", ("설계", "기획")),
    ("implementation", ("구현", "개발", "적용")),
    ("validation", ("검증", "테스트", "측정")),
    ("deployment", ("배포", "전환")),
)


def _execution_action_families(text: str) -> set[str]:
    """Return generic execution-stage families explicitly named in outcome text."""
    value = str(text or "")
    return {
        family
        for family, words in _EXECUTION_ACTION_FAMILIES
        if any(word in value for word in words)
    }


def _phase_action_family(text: str) -> str:
    """Return the explicit action attached to a typed phase clause, if any."""
    matches = list(_FAST_PHASE_FIELD.finditer(str(text or "")))
    if len(matches) != 1:
        return ""
    families = _execution_action_families(matches[0].group("action") or "")
    return next(iter(families)) if len(families) == 1 else ""


def _request_plan_action_families(plan: dict) -> set[str]:
    """Read action identity from the authoritative non-comment write outcomes.

    A phase overlay applies to planned artifacts, not to an accompanying notification comment.
    Missing or mixed action identity is intentionally not guessed: an explicit new stage must go
    through semantic RequestArchitect in that case.
    """
    families: set[str] = set()
    eligible = []
    for task in (plan.get("tasks") or []) if isinstance(plan, dict) else []:
        if not _is_write_outcome(task):
            continue
        if str(task.get("kind") or "").strip().casefold() == "comment":
            continue
        eligible.append(task)
        task_families = _execution_action_families(task.get("instruction") or "")
        if len(task_families) != 1:
            return set()
        families.update(task_families)
    return families if eligible else set()


def _refinement_compatible_with_plan(
    state: dict,
    fields: dict,
    prior_plan: dict,
    *,
    phase_action: str = "",
) -> bool:
    """Shared semantic guard for prose- and receipt-derived field overlays."""
    if phase_action and _request_plan_action_families(prior_plan) != {phase_action}:
        return False
    parent = str(fields.get("parent") or "")
    if not parent:
        return True
    original = str(state.get("request_text") or "").strip()
    if _EPIC_CREATION.search(original):
        return False
    if parent == "top_level" and _re.search(r"Sub-?Task|서브\s*태스크", original, _re.I):
        return False
    if parent in {"select_existing", "top_level"} and _EPIC_CREATION.search(str(prior_plan)):
        return False
    return True


def _has_verified_prior_work_context(state: dict) -> bool:
    """Require material work context before bypassing a semantic request classification."""
    ledger = state.get("materialized_ticket_sources") or {}
    if isinstance(ledger, dict) and any(
            isinstance(row, dict) and not row.get("error") and str(row.get("key") or "").strip()
            for row in ledger.get("ticketDetails") or []):
        return True
    draft = state.get("draft") or {}
    return bool((isinstance(draft, dict) and draft.get("items"))
                or isinstance(state.get("structure_plan"), list)
                and state.get("structure_plan"))


_QUESTION_RECEIPT_PROJECTION = TypeAdapter(QuestionReceiptProjection)
_QUESTION_RECEIPT_FAST_PATH_ID = "request.question_answer_receipt.v1"


def _question_receipt_fast_patch(state: dict) -> dict:
    """Apply only receipt fields with an existing lossless Work projector."""
    raw = state.get("question_receipt_projection")
    try:
        projection = _QUESTION_RECEIPT_PROJECTION.validate_python(raw, strict=True)
    except ValidationError:
        return {}
    prior_plan = _authoritative_continuation_plan(state)
    refinement = projection.request_refinement
    try:
        current_plan_digest = digest_value(prior_plan)
        current_continuation_digest = digest_value(
            state.get("continuation_contract") or {}
        )
    except (TypeError, ValueError):
        return {}
    decision = evaluate_typed_fast_path(
        _QUESTION_RECEIPT_FAST_PATH_ID,
        checks={
            "typed_projection": projection.authority
            == "session.question-answer-receipt.v1",
            "current_plan_binding": bool(prior_plan)
            and projection.request_plan_digest == current_plan_digest,
            "current_continuation_binding": projection.continuation_digest
            == current_continuation_digest,
            "complete_answer_set": projection.complete and not projection.remaining,
            "eligible_field_projectors": bool(refinement)
            and set(refinement).issubset({"duedate", "phase"}),
            "continuation_turn": state.get("turn_continuation") is True
            and str(state.get("intent") or "") == Intent.PLAN_WORK,
            "verified_work_context": bool(str(state.get("request_text") or "").strip())
            and _has_verified_prior_work_context(state),
        },
    )
    if not decision.complete:
        return {}

    mentioned = [str(value) for value in (state.get("mentioned_keys") or [])
                 if str(value).strip()]
    keywords = [str(value) for value in (state.get("keywords") or [])
                if str(value).strip()]
    depth = str(state.get("answer_depth") or "brief")
    labels = ", ".join(f"{key}={value}" for key, value in refinement.items())
    return {
        "intent": Intent.PLAN_WORK,
        "keywords": _copy.deepcopy(keywords),
        "module": str(state.get("module") or ""),
        "mentioned_keys": mentioned,
        "sufficient": bool(state.get("sufficient")),
        "playbook": str(state.get("playbook") or ""),
        "answer_depth": depth if depth in {"brief", "explain"} else "brief",
        "request_plan": _copy.deepcopy(prior_plan),
        "request_refinement": projection.request_refinement,
        "request_text": str(state.get("request_text") or "").strip(),
        "continuation_contract": _copy.deepcopy(state.get("continuation_contract") or {}),
        "questions": [],
        "trace": typed_fast_path_note(
            state, Node.REQUEST_ARCHITECT,
            f"검증된 질문 답변 필드 보정({labels}) · 모델 호출 생략", decision,
        ),
    }


def _field_refinement_fast_patch(state: dict) -> dict:
    """Reuse a verified PLAN_WORK contract for a fully typed continuation without an LLM."""
    if (state.get("turn_continuation") is not True
            or str(state.get("intent") or "") != Intent.PLAN_WORK
            or not str(state.get("request_text") or "").strip()
            or not _has_verified_prior_work_context(state)):
        return {}
    prior_plan = _authoritative_continuation_plan(state)
    if not prior_plan:
        return {}

    asked = last_user_text(state).strip()
    fields = _typed_continuation_refinement(asked)
    if not fields:
        return {}
    # ``1차 검증`` is not just an ordinal when the authoritative outcome is ``구현``.
    # The typed overlay intentionally stores only the ordinal, so bypass the model only when
    # the explicit stage belongs to the same generic action family as every affected outcome.
    # An ordinal-only clause (``1차까지``) carries no action change and remains fast.
    phase_action = _phase_action_family(asked)
    if not _refinement_compatible_with_plan(
            state, fields, prior_plan, phase_action=phase_action):
        return {}
    original = str(state.get("request_text") or "").strip()

    request_plan = _copy.deepcopy(prior_plan)
    raw_mentioned = state.get("mentioned_keys") or []
    mentioned = [str(key).strip().upper() for key in (
                 raw_mentioned if isinstance(raw_mentioned, (list, tuple)) else [])
                 if _re.fullmatch(r"[A-Z][A-Z0-9]{1,9}-\d+", str(key).strip(), _re.I)]
    parent = str(fields.get("parent") or "")
    if _re.fullmatch(r"[A-Z][A-Z0-9]{1,9}-\d+", parent, _re.I) and parent not in mentioned:
        mentioned.append(parent.upper())
    labels = ", ".join(f"{name}={value}" for name, value in fields.items())
    raw_keywords = state.get("keywords") or []
    keywords = [str(value) for value in (
        raw_keywords if isinstance(raw_keywords, (list, tuple)) else []) if str(value).strip()]
    depth = str(state.get("answer_depth") or "brief")
    patch = {
        "intent": Intent.PLAN_WORK,
        "keywords": _copy.deepcopy(keywords),
        "module": str(state.get("module") or ""),
        "mentioned_keys": mentioned,
        "sufficient": bool(state.get("sufficient")),
        "playbook": str(state.get("playbook") or ""),
        "answer_depth": depth if depth in {"brief", "explain"} else "brief",
        "request_plan": request_plan,
        "request_refinement": _copy.deepcopy(fields),
        "request_text": original,
        "trace": note(state, Node.REQUEST_ARCHITECT,
                      f"의도={Intent.PLAN_WORK} · 검증된 필드 보정({labels}) · 모델 호출 생략"),
    }
    contract = build_continuation_contract(
        {**state, **patch}, existing=state.get("continuation_contract"),
    )
    patch["continuation_contract"] = merge_continuation_decisions(contract, [
        {"field": name, "value": str(value), "source": "explicit_refinement"}
        for name, value in fields.items() if str(value).strip()
    ])
    return patch


def _selection_is_not_creation(text: str) -> bool:
    """Whether the user delegated selection of an existing Epic, not creation of one."""
    value = str(text or "")
    explicitly_creates = _EPIC_CREATION.search(value) or _FALLBACK_CREATION.search(value)
    return bool(_EPIC_SELECTION.search(value)) and not bool(explicitly_creates)


def _repair_delegated_selection_plan(plan: dict, grounded_request: str) -> dict:
    """Keep an existing-entity selection from being promoted to a creation mutation.

    The model may plan internal retrieval, but it cannot upgrade ``choose one for me``
    into a new Epic. This repair changes only explicit Epic-creation phrases and leaves
    the requested Task/new-work outcome intact.
    """
    if not _selection_is_not_creation(grounded_request):
        return plan

    def repair(value) -> str:
        return _EPIC_CREATION_PHRASE.sub("기존 Epic 선택", str(value or ""))

    fixed = dict(plan)
    fixed["goal"] = repair(fixed.get("goal"))
    fixed_tasks = []
    for raw in fixed.get("tasks") or []:
        if not isinstance(raw, dict):
            continue
        task = dict(raw)
        task["instruction"] = repair(task.get("instruction"))
        task["completion_criteria"] = [repair(row) for row in
                                       (task.get("completion_criteria") or [])]
        fixed_tasks.append(task)
    fixed["tasks"] = fixed_tasks
    fixed["blocking_questions"] = [repair(row) for row in
                                   (fixed.get("blocking_questions") or [])]
    fixed["request_questions"] = [
        {**row, "question": repair(row.get("question"))}
        for row in (fixed.get("request_questions") or []) if isinstance(row, dict)
    ]
    fixed["assumptions"] = [repair(row) for row in (fixed.get("assumptions") or [])]
    return fixed


_HARD_LITERAL = _re.compile(
    r"(?:[A-Z][A-Z0-9]{1,9}-\d+|(?:skcc\.)?[a-z]{1,3}\d{3,8}|"
    r"\d{4}[-./]\d{1,2}[-./]\d{1,2})",
    _re.I,
)
_WEEKDAY = _re.compile(r"(?:월|화|수|목|금|토|일)요일")


def _ground_request_assumptions(assumptions: list, grounded_request: str) -> list[str]:
    """Drop assumptions that invent hard identifiers, dates, users, or weekdays.

    Assumptions may explain a reversible interpretation, but a novel hard literal is not
    an assumption: downstream roles treat it as a fact. Preserve only rows whose hard
    literals and weekday labels already occur in human-authored request text.
    """
    grounded = str(grounded_request or "").casefold()
    kept = []
    for raw in assumptions or []:
        row = str(raw or "").strip()
        if not row:
            continue
        literals = [match.group(0).casefold() for match in _HARD_LITERAL.finditer(row)]
        weekdays = [match.group(0) for match in _WEEKDAY.finditer(row)]
        if any(literal not in grounded for literal in literals):
            continue
        if any(weekday not in grounded_request for weekday in weekdays):
            continue
        kept.append(row)
    return kept


def _validated_request_questions(value) -> list[dict]:
    """Validate bounded slot labels without interpreting their natural-language text."""
    rows: list[dict] = []
    for raw in (value or [])[:3]:
        try:
            rows.append(RequestQuestion.model_validate(raw).model_dump())
        except Exception:
            continue
    return rows


def _validated_requested_effects(value, allowed_targets, current_text: str) -> list[dict]:
    """Accept only the complete runtime-grounded mapping; partial authority is unsafe."""
    return issue_requested_update_effects(value, allowed_targets, current_text)


def _single_required_request_question(state: dict, request_plan: dict, *,
                                      intent: str, sufficient: bool) -> dict:
    """Project exactly one unresolved target/action on an atomic write plan."""
    tasks = [row for row in (request_plan.get("tasks") or []) if isinstance(row, dict)]
    if (intent != Intent.PLAN_WORK or sufficient or len(tasks) != 1
            or tasks[0].get("write_intent") is not True):
        return {}

    contract = state.get("continuation_contract") or {}
    resolved = ({
        str(row.get("field") or "").split(":", 1)[0].casefold()
        for row in (contract.get("decisions") or [])
        if isinstance(row, dict) and str(row.get("value") or "").strip()
    } if has_typed_continuation_contract(contract) else set())
    candidates = [
        row for row in _validated_request_questions(
            request_plan.get("request_questions") or [])
        if row["field"] in {"target", "action"} and row["field"] not in resolved
    ]
    fields = {row["field"] for row in candidates}
    if len(fields) != 1 or not candidates:
        return {}

    field = candidates[0]["field"]
    why = {
        "target": "생성할 작업의 대상을 식별할 수 없음",
        "action": "생성할 작업에서 수행할 행동을 식별할 수 없음",
    }[field]
    return QuestionContract(
        question=candidates[0]["question"], kind="text", options=[], field=field,
        ownership="user_required", required_input=True, why_required=why, fallback="",
    ).model_dump()


class RequestArchitect(StructuredAgent):
    name = Node.REQUEST_ARCHITECT

    def _run(self, state: AgentState) -> dict:
        receipt = _question_receipt_fast_patch(state)
        if receipt:
            return receipt
        fast = _field_refinement_fast_patch(state)
        if fast:
            return fast
        # Per-turn overlays are valid only when the deterministic grammar parsed the whole
        # continuation.  A semantic fallback—including an error patch—must explicitly clear
        # any value present in a legacy/directly-invoked state rather than inheriting it.
        patch = super()._run(state)
        patch["request_refinement"] = {}
        return patch

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
- `tasks` describe only distinct outcomes or mutations explicitly requested by the user. Do not
  restate the Agent pipeline as separate query, research, analysis, validation, approval, or response tasks.
- On a continuation that explicitly asks for one more typed artifact (`Task 하나 더`,
  `검증 Task도 하나 만들어줘`), return only that newly requested semantic outcome. Runtime
  appends it to the authoritative prior DAG; do not echo the prior outcomes as new tasks.
- A single request has exactly one task. Split only a genuinely compound request with independently
  checkable deliverables, and keep at most three concise completion criteria per task.
- A task instruction represents only the user's requested outcome and explicit constraints. Never
  turn an assumption, example, default, or delegated implementation choice into a required outcome.
- Always return `requested_effects`. For `modify`, emit a row only when the current user explicitly
  supplied one exact target and final canonical `priority`, ISO `duedate`, or quoted `summary` value.
  Copy the exact value substring from `Current User Message` into `literal`. Return `[]` for
  corrections, negation, multiple candidates, unclear targets, or inferred/unsupported fields.
- Always return `request_questions`: `target` only if the object is absent, `action` only if the
  operation is absent. A named concrete object means target is present. Classify optional boundaries,
  success details, and preferences as `scope`, `acceptance`, or `other`; never relabel them as
  target/action. Return `[]` when nothing is missing.
- Do not ask for technical details that Jira, Confluence, comments, or external research can recover.
- Write `goal`, `instruction`, `completion_criteria`, `request_questions.question`,
  `blocking_questions`, `assumptions`, and `plan` in Korean because they are user-visible or
  preserve the Korean request.

## Intent Examples

- `실시간 수집 방식을 새로 도입해야 한다` -> `plan_work`: start new work.
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
- `증분 수집 방식 검토가 왜 멈췄었지?` -> `ask`: historical rationale, not a progress metric.
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
- `증분 수집이 뭐고 우리는 어떻게 쓰고 있어?` -> `explain`.
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
        prior_write_plan = _authoritative_continuation_plan(state)
        prior_read_plan = _authoritative_read_continuation_plan(state)
        carried_contract = {}
        if has_typed_continuation_contract(state.get("continuation_contract")):
            carried_contract = build_continuation_contract(
                state, existing=state.get("continuation_contract"),
            )
        fallback_intent = out.get("intent") or Intent.PLAN_WORK
        asked = last_user_text(state)
        request_questions = _validated_request_questions(out.get("request_questions") or [])
        legacy_blocking_questions = [
            str(value).strip()[:240] for value in (out.get("blocking_questions") or [])
            if str(value).strip()
        ][:3]
        additive_outcome = bool(prior_write_plan and _continuation_outcome_additions(asked))
        outcome_directive = _continuation_outcome_directive(asked)
        current_typed_decision = has_current_continuation_decision(
            carried_contract, asked,
        )
        stable_subject = bool(
            state.get("turn_continuation") and carried_contract and not additive_outcome
        )
        authoritative_refinement = bool(
            stable_subject and (not outcome_directive or current_typed_decision)
        )
        plan_request_questions = (
            _validated_request_questions(prior_write_plan.get("request_questions") or [])
            if prior_write_plan and authoritative_refinement else request_questions
        )
        if stable_subject:
            kws = [str(k) for k in (state.get("keywords") or []) if str(k).strip()]
            for key in carried_contract.get("target_keys") or []:
                if key not in kws:
                    kws.append(key)
            module = str(state.get("module") or "")
        else:
            kws = [k for k in (out.get("keywords") or []) if str(k).strip()]
            module = out.get("module") or ""
        # For an additive continuation, keep the bounded raw model tasks until projection can
        # remove exact echoes of prior outcomes. Generic compaction would merge an echoed old
        # Task and the genuinely new Task into one synthetic ``task-1`` and lose the new id.
        if prior_read_plan or (prior_write_plan and authoritative_refinement):
            source_plan = prior_read_plan or prior_write_plan
            current_tasks = _copy.deepcopy(source_plan.get("tasks") or [])
        elif prior_write_plan and additive_outcome:
            current_tasks = [task for task in (out.get("tasks") or [])[:6]
                             if isinstance(task, dict)]
        else:
            current_tasks = _compact_request_tasks(
                out, asked, fallback_intent,
                # A compound continuation may return one replacement task whose model
                # instruction is the semantic value to splice into the prior DAG.  It is
                # not a new one-outcome authority boundary and must not be replaced by the
                # conversational edit directive (for example, "change the comment body").
                pin_single_write=not bool(prior_write_plan),
            )
        if prior_read_plan or (prior_write_plan and authoritative_refinement):
            planned_tasks, outcomes_changed = current_tasks, False
        else:
            planned_tasks, outcomes_changed = _project_followup_outcomes(
                state, current_tasks, fallback_intent)
        if prior_read_plan:
            contract = build_continuation_contract(
                state, existing=state.get("continuation_contract"),
            )
            intent = str(contract.get("intent") or state.get("intent") or Intent.ASK)
        elif prior_write_plan and (not outcomes_changed
                                 or any(_is_write_outcome(task) for task in planned_tasks)):
            intent = _continuation_write_intent(state, fallback_intent)
        elif prior_write_plan and outcomes_changed:
            # Removing the last write is an acknowledgement turn, not a new draft mutation.
            intent = (fallback_intent if fallback_intent not in Intent.DRAFTS_TICKETS
                      else Intent.CHITCHAT)
        else:
            intent = fallback_intent
        grounded_request = "\n".join(part for part in (
            str(state.get("request_text") or "").strip(), asked.strip()) if part)
        projected_goal = " · ".join(
            str(task.get("instruction") or "").strip()
            for task in planned_tasks if isinstance(task, dict) and task.get("instruction"))[:240]
        if prior_read_plan:
            request_plan = _copy.deepcopy(prior_read_plan)
        else:
            request_plan = _repair_delegated_selection_plan({
                "goal": (projected_goal if prior_write_plan and outcomes_changed else
                         (prior_write_plan.get("goal") if prior_write_plan else ""))
                        or out.get("goal") or asked,
                "tasks": planned_tasks or [{
                    "id": "task-1", "kind": "query" if intent in Intent.NEEDS_RESEARCH else "respond",
                    "instruction": asked, "depends_on": [],
                    "write_intent": intent in Intent.DRAFTS_TICKETS,
                    "completion_criteria": ["사용자 요청에 직접 답한다"],
                }],
                **({"request_questions": plan_request_questions}
                   if plan_request_questions else {}),
                # Keep the historical string surface for evaluation/checkpoint consumers,
                # but make the typed rows its canonical projection when available.
                "blocking_questions": (
                    [row["question"] for row in plan_request_questions]
                    if plan_request_questions else legacy_blocking_questions
                ),
                "assumptions": _ground_request_assumptions(
                    out.get("assumptions") or [], grounded_request),
            }, grounded_request)
        mentioned_keys = _carry_keys(state, out)
        if (state.get("turn_continuation")
                and carried_contract.get("action") in {"read", "comment", "update", "mixed"}
                and carried_contract.get("target_keys")):
            mentioned_keys = _copy.deepcopy(carried_contract["target_keys"])
        current_targets = {
            match.group(0).upper() for match in _re.finditer(
                r"(?<![A-Z0-9-])[A-Z][A-Z0-9]{1,9}-\d+(?![A-Z0-9-])", asked, _re.I,
            )
        }
        if state.get("turn_continuation") and carried_contract:
            current_targets.update(str(value).upper()
                                   for value in carried_contract.get("target_keys") or [])
        requested_effects = (_validated_requested_effects(
            out.get("requested_effects"), current_targets, asked,
        ) if intent == Intent.MODIFY else [])
        if requested_effects:
            request_plan["requested_effects"] = requested_effects
        else:
            request_plan.pop("requested_effects", None)
        patch = {
            "intent": intent,
            "keywords": kws,
            "module": module,
            "mentioned_keys": mentioned_keys,
            "sufficient": bool(out.get("sufficient")),
            "playbook": out.get("playbook") or "",
            "answer_depth": _carry_depth(state, out),
            "request_plan": request_plan,
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
        # Current-population audits are progress queries even when a small model mistakes
        # the action-like wording ("없는 것들") for new work.
        if intent in (Intent.ASK, Intent.PLAN_WORK) \
                and _re.search(r"진행\s*중", _req, _re.I) \
                and _re.search(r"업데이트|오래|며칠|기한|마감|미할당|담당자\s*없", _req, _re.I):
            intent = patch["intent"] = Intent.PROGRESS
            patch["playbook"] = "find_tickets"
        # A defect is still plan_work (Bug is a Task-tier issue_type, not an intent), but
        # pin the playbook when the user supplied a concrete failure symptom.
        if intent == Intent.PLAN_WORK \
                and _re.search(r"실패|오류|에러|깨짐|빈다|안\s*됨|동작하지", _req, _re.I):
            patch["playbook"] = "bug_report"
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
            # Session owns the turn boundary. Research/identity interviews can happen before
            # WorkArchitect increments ``turns``, so that counter cannot decide whether the
            # short answer replaces the frozen work request.
            follow_up = bool(state.get("turn_continuation")) \
                or (bool(prior) and (state.get("turns") or 0) > 0)
            if follow_up and (state.get("request_text") or "").strip():
                patch["request_text"] = str(state.get("request_text") or "").strip()
            else:
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
        if (state.get("turn_continuation")
                and carried_contract.get("root_request")):
            # The latest user message is only an answer/refinement.  Keep the stable root as
            # the explicit request boundary for read as well as write continuations.
            patch["request_text"] = carried_contract["root_request"]
        elif is_meeting_request(state) and _meeting_request:
            patch["request_text"] = _meeting_request

        # Persist one validated envelope for the next Session turn.  The request DAG remains
        # in ``request_plan``; this sidecar carries only stable effect/target identity and
        # user-authored typed decisions.
        patch["continuation_contract"] = build_continuation_contract(
            {**state, **patch}, existing=state.get("continuation_contract"),
        )

        direct_question = _single_required_request_question(
            {**state, **patch}, patch.get("request_plan") or {},
            intent=str(patch.get("intent") or ""),
            sufficient=bool(patch.get("sufficient")),
        )
        if direct_question:
            patch["questions"] = [direct_question]
            patch["trace"] = note(
                state, self.name,
                f"의도={patch['intent']} · 필수 {direct_question['field']} 입력 1건",
            )

        # Ticket shape is an Agent-owned, reversible design choice.  Stopping before retrieval
        # to ask that preference wasted a turn and prevented internal history from answering more
        # important technical gaps.  Research first; WorkArchitect may interview only for a
        # user-owned fact that still blocks a truthful payload after the evidence is available.
        return patch
