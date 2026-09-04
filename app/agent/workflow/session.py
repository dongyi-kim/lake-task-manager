"""agent/workflow/session.py — 그래프를 굴리는 바깥 API. 라우트는 이것만 부른다.

여기가 하는 일은 셋이다.

  · **대화를 잇는다** — `thread_id` 로 Checkpointer 에 State 를 맡긴다. 되묻기가 가능해지는 이유.
  · **관측을 붙인다** — Langfuse callback과 `langfuse_session_id` metadata를 모든 실행에 단다.
    한 대화가 한 세션으로 묶여야 트레이스를 읽을 수 있다.
  · **승인 대기를 노출한다** — 그래프가 ActionExecutor 앞에서 멈췄다는 사실과, 무엇을 승인해야 하는지를
    화면이 알 수 있는 형태로 돌려준다.

질의·응답 원문과 생성 payload는 애플리케이션 로그에 남기지 않는다. 폐쇄망에서도 correlation id,
길이, 성공 여부, 호출량 같은 비내용 metadata만 기록해 감사 ledger와 연결한다.
"""

from __future__ import annotations

import re as _re
import copy as _copy

from dataclasses import dataclass
import logging
import uuid

from langchain_core.messages import HumanMessage

from app.agent import approval
from app.agent import config as _cfg
from app.agent import usage as _usage
from app.agent.workflow.graph import get_graph
from app.agent.workflow.continuation import (
    build_continuation_contract,
    capture_continuation_decisions,
    has_continuation_cue,
    has_interview_answer,
    has_multi_field_refinement,
    has_typed_continuation_contract,
    merge_continuation_decisions,
)
from app.agent.workflow.state import (
    ROLE_ENGINES_RESET, ROLE_QUESTIONS_RESET, ROLE_SIDECARS_RESET,
    ROLE_TELEMETRY_RESET, TRACE_RESET,
    Node, Role, as_dict,
)
from app.agent.workflow.typed_fast_path import zero_typed_repair_budget
from app.agent.workflow.question_receipt import (
    ReceiptClaim,
    claim_question_receipt,
    finish_question_receipt,
    issue_question_challenge,
    question_turn_lock,
    release_question_receipt,
)

log = logging.getLogger("agent.chat")

RECURSION_LIMIT = 40        # 왕복 상한을 코드로 걸어 두었지만, 그래프 차원의 안전망도 둔다


def new_thread() -> str:
    return uuid.uuid4().hex[:16]


def _config(thread_id: str, meter=None) -> dict:
    cbs = _cfg.callbacks(session_id=thread_id)
    h = _usage.callback(meter) if meter is not None else None
    if h:
        cbs = cbs + [h]
    return {"configurable": {"thread_id": thread_id},
            "callbacks": cbs,
            "metadata": {"langfuse_session_id": thread_id},
            "recursion_limit": RECURSION_LIMIT}


def _guard(text: str):
    """보내기 **전에** 센다. 응답을 받아야 알 수 있다면 이미 늦다.

    사용자가 로그 10만 줄을 붙여 넣었을 때 할 일은 "비쌌습니다"가 아니라 보내지 않는 것이다.
    """
    over, n = _usage.too_long(text or "", _cfg.chat_model() or "gpt-4o-mini")
    if over:
        return (f"입력이 너무 깁니다({n:,} 토큰). {_usage.MAX_INPUT_TOKENS:,} 토큰 이하로 줄여 주세요 — "
                "긴 로그나 문서는 붙여 넣지 말고 티켓 키나 문서 제목으로 알려 주시면 제가 찾아봅니다.")
    return None


_IDENTITY_CACHE = {"at": 0.0, "val": None}
_IDENTITY_TTL = 300        # 사용자·모듈 소속은 대화 중에 안 바뀐다 — 5분이면 충분히 신선하다


def _identity() -> str:
    """'내가 누구인가' — 검증된 최소 정체성만 모든 역할의 시스템 프롬프트에 실린다.

    "내 모듈", "나한테 맞는 일" 같은 말은 정체를 알아야 해석된다. 매 역할이 whoami 를
    부르게 하는 대신 세션 시작에 코드가 한 번 해석해 State 로 준다(사용자 요청).

    ★ 프로세스 캐시(5분) — 이 해석이 사용자 조회·로스터 스캔을 매 턴 다시 했고, 그게
    턴 시작 오버헤드의 절반이었다(타임라인 실측: 첫 이벤트까지 ~4.6s 중 2~3s).
    """
    import time as _t
    if _IDENTITY_CACHE["val"] is not None and _t.time() - _IDENTITY_CACHE["at"] < _IDENTITY_TTL:
        return _IDENTITY_CACHE["val"]
    try:
        from app.agent.tools._ctx import client
        from app.infra.settings import load_people
        me = (client().current_user() or {})
        uid = me.get("name") or me.get("key") or ""
        if not uid:
            return ""
        mods = [m for m, ids in (load_people() or {}).items() if uid in (ids or [])]
        val = ("## Verified Current User Context\n"
               f"- user_id: `{uid}`\n"
               f"- modules: {', '.join(mods) if mods else 'not configured'}\n"
               "Use this only for self-references. Render any user-facing person occurrence as "
               f"`{{{{mention:{uid}}}}}`; do not echo a display name plus ID.")
        import time as _t2
        _IDENTITY_CACHE.update(at=_t2.time(), val=val)
        return val
    except Exception:
        return ""


def _detect_role() -> str:
    """매니저 여부는 선택이 아니라 사실이다 — 세션 설정과 로그인 사용자로 판별한다.

    판별 실패(비로그인·설정 없음)는 MEMBER 로 — 낮은 권한이 안전한 기본값이고,
    매니저 전용 도구는 어차피 도구 안의 게이트가 한 번 더 막는다.
    """
    try:
        from app.agent.tools.pmo_tools import _is_manager
        return Role.MANAGER if _is_manager() else Role.MEMBER
    except Exception:
        return Role.MEMBER


_KEY_RE = _re.compile(r"(?<![A-Z0-9])[A-Z][A-Z0-9]+-\d+(?![A-Z0-9-])", _re.I)


def _recent_keys(text: str) -> list:
    """이번 발화에 나온 티켓 키. 누적은 `set_person_context` 가 한다(최근 8건까지)."""
    return _KEY_RE.findall(str(text or ""))


def _initial(thread_id, text, user_role, user_id) -> dict:
    from app.agent.tools import set_thread
    from app.agent.tools.people_tools import set_person_context
    set_thread(thread_id)       # 쓰기 도구가 자기 대화를 안다(모델이 남의 thread 를 못 적게)
    # ★ 이름 해석에 쓸 **가까운 맥락** — 이 대화에서 방금 본 티켓들(사용자 지시).
    #   "이다은"처럼 이름만 대면, 그 티켓에 얽힌 사람이 우선이다. thread 가 바뀌면 잊는다.
    set_person_context(thread_id, _recent_keys(text))
    return {"messages": [HumanMessage(content=text)], "thread_id": thread_id,
            "user_role": user_role or _detect_role(), "user_id": user_id or "",
            "user_identity": _identity(),
            # 새 턴이 시작되면 지난 턴의 승인·실행 결과는 지운다 — 안 지우면 옛 토큰으로
            # result_integrator 가 다시 '승인 대기'로 흘러간다.
            "approval_token": "", "comment_token": "", "result": {},
            "execution_receipt": {}, "change_audit_binding": {}, "revisions": 0,
            "repair_budget": zero_typed_repair_budget(),
            "runtime_role_engines": ROLE_ENGINES_RESET,
            "runtime_role_telemetry": ROLE_TELEMETRY_RESET,
            "runtime_role_sidecars": ROLE_SIDECARS_RESET,
            "runtime_role_questions": ROLE_QUESTIONS_RESET,
            "runtime_last_role": "", "runtime_last_role_engine": "",
            # trace 는 리듀서 필드라 [] 대입으로는 안 비워진다 — 리셋 신호를 앞에 싣는다.
            "trace": [TRACE_RESET], "change_plan": {}, "questions": []}


_CONTEXT_SWITCH = _re.compile(
    r"(?:이건|이거|그건|그거).{0,8}(?:그만|취소)|완전히\s*다른|"
    r"잠깐\s*다른|최종\s*요청|(?:댓글|변경|요청).{0,8}(?:도\s*)?취소|"
    r"(?:대신|다시\s+.+?돌아갈)", _re.I,
)

_FIELD_REPLACEMENT = _re.compile(
    r"(?:Epic|에픽|상위|부모|담당(?:자)?|assignee|owner|마감(?:일)?|기한|due(?:\s*date)?|"
    r"범위|단계|우선순위|priority).{0,40}\s대신(?:\s|$)",
    _re.I,
)

_HARD_CONTEXT_SWITCH = _re.compile(
    r"(?:이건|이거|그건|그거).{0,8}(?:그만|취소)|완전히\s*다른|잠깐\s*다른|"
    r"최종\s*요청|(?:변경|요청).{0,8}(?:도\s*)?취소|다시\s+.+?돌아갈",
    _re.I,
)

_PARENT_CREATION_ANSWER = _re.compile(
    r"(?:적합|마땅|관련).{0,24}(?:없|없으면).{0,24}(?:새\s*(?:Epic|에픽)|"
    r"(?:Epic|에픽).{0,8}새)|^\s*새\s*(?:Epic|에픽)(?:을|를|으로|로)?\s*"
    r"(?:만들|생성|진행|선택)?",
    _re.I,
)

_DRAFT_REFINEMENT_FIELDS = (
    _re.compile(r"(?:상위|부모|기존)\s*(?:Epic|에픽)|(?:Epic|에픽)\s*(?:선택|연결|하위)|"
                r"(?:Epic|에픽)\s*대신\s*(?:최상위|단일|Task|태스크|테스크)", _re.I),
    _re.compile(r"(?:범위|단계|phase|stage)\s*(?:은|는|을|를|으로|로|:|=)|"
                r"\d+\s*차\s*(?:로|까지)", _re.I),
    _re.compile(r"(?:마감|기한|due(?:\s*date)?|deadline)\s*(?:은|는|을|를|로|:|=)|"
                r"\d{4}-\d{2}-\d{2}\s*(?:까지|로)", _re.I),
    _re.compile(r"(?:담당(?:자)?|배정|assignee|owner)\s*(?:은|는|을|를|로|:|=)|"
                r"미할당\s*(?:으로|로)", _re.I),
    _re.compile(r"(?:우선순위|priority)\s*(?:은|는|을|를|로|:|=)", _re.I),
    _re.compile(r"(?:제목|본문|설명|완료\s*조건|DoD|acceptance\s+criteria)\s*"
                r"(?:은|는|에|을|를|로|:|=).{0,40}(?:추가|수정|바꿔|변경|정리)", _re.I),
)

_EXPLICIT_NEW_WORK = _re.compile(
    r"(?:새|별도|다른)\s*(?:Epic|에픽|Task|테스크|티켓|작업).{0,16}(?:만들|생성|추가)|"
    r"(?:Epic|에픽|Task|테스크|티켓|작업)(?:을|를)?\s*(?:새로|별도로|따로)"
    r".{0,12}(?:만들|생성|추가)",
    _re.I,
)

# A failed PLAN_WORK turn has no draft/question to prove that the next speech act belongs to
# it.  In that narrow state, ``Task 만들어줘`` is an independent creation request even when
# the same sentence also happens to carry two field-shaped values.
_INDEPENDENT_WORK_CREATION = _re.compile(
    r"(?:Epic|에픽|Task|태스크|테스크|티켓|작업)(?:은|는|이|가|을|를|으로|로)?"
    r"[^.!?\n]{0,24}(?:만들|생성|등록|발행)|"
    r"(?:만들|생성|등록|발행)[^.!?\n]{0,24}"
    r"(?:Epic|에픽|Task|태스크|테스크|티켓|작업)",
    _re.I,
)

_FIELD_ONLY_REFINEMENT_START = _re.compile(
    r"^\s*(?:(?:그리고|그럼|그러면|알아서|계속)\s*)*(?:기존\s*)?"
    r"(?:상위|부모|Epic|에픽|범위|단계|phase|stage|마감|기한|due(?:\s*date)?|"
    r"담당(?:자)?|배정|assignee|owner|우선순위|priority|제목|본문|설명|"
    r"완료\s*조건|DoD|acceptance\s+criteria)",
    _re.I,
)

_NON_FIELD_REQUEST = _re.compile(
    r"(?:알려\s*줘|조회(?:해|해줘)|검색(?:해|해줘)|찾아\s*줘|요약(?:해|해줘)|"
    r"설명(?:해|해줘)|분석(?:해|해줘)|누가|왜|어떻게)",
    _re.I,
)

# A typed interview answer can share one utterance with a second read request.  Keep this
# narrower than ``_INDEPENDENT_REQUEST``: generic imperatives such as ``계속해줘`` are common
# continuation cues, while these shapes name a new status/progress question whose omission
# would silently freeze the old write DAG.
_INDEPENDENT_READ_FOLLOWUP = _re.compile(
    r"(?:현재|지금|최신)?\s*(?:진행\s*)?(?:상황|현황|상태)"
    r".{0,24}(?:\?|어때|알려|보여|조회|확인)|"
    r"(?:무엇|뭐)(?:이|가)?\s*.{0,24}(?:남았|남아)|"
    r"(?:남은|미완료)\s*.{0,24}(?:무엇|뭐|알려|보여|확인)",
    _re.I,
)

_INDEPENDENT_REQUEST = _re.compile(
    r"(?:해\s*줘|해주세요|해줘|해봐|알려\s*줘|보여\s*줘|찾아\s*줘|추천(?:해|해줘)|"
    r"조회(?:해|해줘)|정리(?:해|해줘)|설명(?:해|해줘)|만들(?:어|어줘)|생성(?:해|해줘)|"
    r"수정(?:해|해줘)|바꿔\s*줘)|"
    r"(?:누가|누구|뭐|무엇|어떤|왜|언제|어디|어떻게|몇\s*명|현황|상태)"
    r".{0,50}(?:\?|궁금|알려|찾|확인|조회|보여|정리)|"
    # A new topic can be a declarative speech act rather than an imperative. Without these
    # predicates, ``서울 날씨가 궁금해`` and ``운영회의는 취소됐어`` were mistaken for terse
    # answers to an unrelated pending field question and inherited the stale request plan.
    r"[^?!\n]{1,60}(?:은|는|이|가)\s*(?:궁금(?:해|합니다|한데)|"
    r"(?:취소|연기|변경|완료|종료)(?:됐|되었|했)(?:어|다|습니다|대)?)|"
    r"(?:있어|없어|됐어|됐나|어때)\s*\?",
    _re.I,
)

_SIMPLE_INTERVIEW_ANSWER = _re.compile(
    r"^\s*(?:응|네|예|아니|아니요|맞아|맞습니다|그대로|없어|없습니다|모르겠|"
    r"알아서|미할당|그걸로|이걸로|첫\s*번째|두\s*번째|세\s*번째|[1-3]\s*번)"
    r"(?:\s*(?:해줘|진행해줘|선택해줘))?[.!]?\s*$",
    _re.I,
)

_OUTCOME_REFINEMENT = _re.compile(
    r"^\s*(?:기존\s*)?(?:댓글|코멘트|Bug|Story|Feature|Improvement|Task|Sub-?Task|"
    r"Epic|버그|스토리|태스크|테스크|서브\s*태스크|에픽|티켓|문서|보고서|조사|리서치)"
    r"(?:은|는|을|를|\s|내용|대상|범위|제목)*.{0,50}"
    r"(?:빼|제외|취소|삭제|없애|하지\s*마|말고|대신|바꿔|수정|변경|교체)",
    _re.I,
)

_LEGACY_STRUCTURE_QUESTION = _re.compile(
    r"(?:단일|여러|복수|다수|최상위)\s*(?:Task|태스크|테스크)|"
    r"(?:Task|태스크|테스크).{0,16}(?:Sub-?Task|서브\s*태스크|서브\s*테스크)|"
    r"(?:Epic|에픽)\s*(?:으로|아래|하위|상위|부모)|"
    r"(?:티켓|이슈|Task|태스크|테스크)\s*(?:계층|구조|형태)|"
    r"(?:구조|형태)(?:를|로)\s*.{0,16}(?:진행|나누|나눠|분할|쪼개|합치|합쳐|통합)",
    _re.I,
)
_LEGACY_STRUCTURE_OPTION = _re.compile(
    r"(?:단일|하나의?)\s*(?:Task|태스크|테스크)|"
    r"(?:여러|복수|다수|최상위|\d+\s*(?:건|개)?)\s*(?:Task|태스크|테스크)|"
    r"(?:Task|태스크|테스크)\s*(?:하나\s*)?(?:\+|와|과|/|또는)\s*"
    r"(?:단계별\s*)?(?:Sub-?Task|서브\s*태스크|서브\s*테스크)|"
    r"(?:Sub-?Task|서브\s*태스크|서브\s*테스크)\s*(?:\+|와|과|/)\s*"
    r"(?:Task|태스크|테스크)|"
    r"(?:^|\s)(?:Epic|에픽)(?:\s|$|으로|로)",
    _re.I,
)


def _legacy_blank_structure_choice(question: dict) -> bool:
    """Recognize only the bounded pre-metadata structure preference card."""
    if (str(question.get("field") or "").strip()
            or "required_input" in question
            or str(question.get("why_required") or "").strip()):
        return False
    text = str(question.get("question") or "").strip()
    options = [str(value or "").strip() for value in (question.get("options") or [])
               if str(value or "").strip()]
    is_choice = str(question.get("kind") or "").strip().casefold() == "choice"
    option_shapes = [value for value in options if _LEGACY_STRUCTURE_OPTION.search(value)]
    return bool((is_choice or options) and (
        _LEGACY_STRUCTURE_QUESTION.search(text)
        or (len(options) >= 2 and len(option_shapes) == len(options))
    ))


def _pending_questions_are_optional(asked: list[dict]) -> bool:
    """Return true only when every pending prompt is explicitly or structurally optional.

    Current cards must explicitly set ``required_input=false``. Legacy structure-preference
    cards used an empty field and omitted the flag, so accept only their bounded question/option
    grammar. A blank field by itself conveys no ownership or optionality and fails closed.
    """
    if not asked:
        return False
    for question in asked:
        required = question.get("required_input")
        if required is True or str(question.get("why_required") or "").strip():
            return False
        if required is False or _legacy_blank_structure_choice(question):
            continue
        return False
    return True


def _looks_like_answer_to_questions(utterance: str, asked: list[dict]) -> bool:
    """Recognize bounded answer shapes before treating a pending interview as sticky."""
    text = str(utterance or "").strip()
    if not text:
        return False
    if _SIMPLE_INTERVIEW_ANSWER.fullmatch(text):
        return True
    if any(str(option or "").strip() and str(option).strip().casefold() in text.casefold()
           for question in asked for option in (question.get("options") or [])):
        return True

    for question in asked:
        field = " ".join((str(question.get("field") or ""),
                          str(question.get("question") or ""))).casefold()
        if any(token in field for token in ("parent", "epic", "에픽", "상위", "부모")):
            if (_PARENT_CREATION_ANSWER.search(text)
                    or _re.search(r"\b[A-Z][A-Z0-9]+-\d+\b|(?:기존|새)\s*(?:Epic|에픽)",
                                  text, _re.I)):
                return True
        if any(token in field for token in ("due", "date", "deadline", "마감", "기한")):
            if _re.search(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|(?:월|화|수|목|금|토|일)요일|"
                          r"(?:마감|기한).{0,12}(?:없|제거|취소)", text, _re.I):
                return True
            if _re.fullmatch(
                    r"\s*(?:오늘|내일|모레|이번\s*주|다음\s*주|다다음\s*주)"
                    r"(?:\s*(?:월|화|수|목|금|토|일)요일)?(?:까지)?(?:로)?"
                    r"(?:\s*(?:해줘|진행해줘))?[.!]?\s*", text, _re.I):
                return True
        if any(token in field for token in ("structure", "shape", "type", "구조", "유형")):
            if _re.fullmatch(r"\s*(?:(?:단일|최상위)\s*)?(?:Epic|에픽|Task|태스크|테스크|"
                             r"Sub-?Task|서브\s*태스크)(?:\s*(?:하나|구조|로|으로))*"
                             r"(?:\s*(?:구성|선택|진행)?해줘)?[.!]?\s*", text, _re.I):
                return True
        if any(token in field for token in ("term", "acronym", "meaning", "용어", "뜻", "약어")):
            if _re.fullmatch(r"\s*.{1,40}(?:은|는|이란|란)\s*.{1,100}[.!]?\s*", text, _re.S):
                return True
        if any(token in field for token in ("person", "assignee", "owner", "reviewer", "담당", "사람", "누구")):
            if _re.fullmatch(r"\s*(?:@?[가-힣]{2,5}(?:님|TL|M|차장|책임|매니저)?|"
                             r"(?:skcc\.)?[a-z]{1,3}\d{2,8}|미할당)"
                             r"(?:\s*(?:이야|입니다|로|으로))?"
                             r"(?:\s*(?:배정|지정)?해줘)?[.!]?\s*",
                             text, _re.I):
                return True
        if any(token in field for token in ("target", "table", "entity", "document", "ticket",
                                             "대상", "테이블", "문서", "티켓")):
            # A target answer is a value-shaped phrase, not another request sentence.  This
            # accepts identifiers and ``X로 해줘`` while rejecting ``Puffin 이력을 요약해줘``.
            if (_re.fullmatch(r"\s*[A-Za-z][A-Za-z0-9_.-]{1,119}"
                              r"(?:\s*(?:이야|입니다|로|으로)(?:\s*(?:해줘|진행해줘))?)?"
                              r"[.!]?\s*", text, _re.I)
                    or _re.fullmatch(r"\s*[^?!\n]{1,60}(?:이야|입니다|말한\s*거야|"
                                     r"(?:로|으로)\s*(?:(?:선택|지정|진행)?해줘))[.!]?\s*", text)):
                return True
    if _re.fullmatch(r"\s*[^?!\n]{1,60}(?:로|으로)\s*"
                     r"(?:(?:선택|지정|배정|구성|진행)?해줘)[.!]?\s*", text):
        return True
    # A legacy question without a typed field provides no safe way to distinguish a terse
    # answer from an unrelated Korean noun-style request. Fail closed as a new turn instead
    # of executing the stale write plan; typed field/choice shapes above remain continuations.
    return False


def _has_independent_question_clause(utterance: str, asked: list[dict]) -> bool:
    """Detect a second question that no pending field owns, without enumerating its nouns."""
    clauses = [part.strip() for part in _re.split(
        r"(?<=[.!?])\s+|\s*(?:그리고|또한|그와 별개로)\s*",
        str(utterance or ""), flags=_re.I,
    ) if part.strip()]
    for clause in clauses:
        if "?" not in clause:
            continue
        if has_interview_answer(capture_continuation_decisions(clause, asked)):
            continue
        return True
    return False


_REFINEMENT_GENERIC_ANCHORS = {
    "poc", "phase", "stage", "dod", "acceptance", "criteria", "owner", "assignee",
    "scope", "deadline", "date", "title", "body", "description",
}


def _draft_refinement_subject_sets(text: str, prior: dict) -> tuple[set[str], set[str]]:
    """Return comparable high-precision subject anchors for a refinement boundary."""
    from app.agent.workflow.anchors import required_user_anchors

    original = str(prior.get("request_text") or "").strip()
    if not original:
        return set(), set()
    old = {value.casefold() for value in required_user_anchors(
        {"request_text": original, "messages": []}, include_latest=False)}
    new = {value.casefold() for value in required_user_anchors(
        {"request_text": str(text or ""), "messages": []}, include_latest=False)}
    old = {value for value in old
           if value not in _REFINEMENT_GENERIC_ANCHORS and not _re.fullmatch(r"\d+차", value)}
    new = {value for value in new
           if value not in _REFINEMENT_GENERIC_ANCHORS and not _re.fullmatch(r"\d+차", value)}
    return old, new


def _draft_refinement_changes_subject(text: str, prior: dict) -> bool:
    """Return true when a supposed field refinement introduces a different named topic."""
    old, new = _draft_refinement_subject_sets(text, prior)
    # A constraint-only follow-up may have no named subject.  Once it names a subject,
    # however, at least one stable original anchor must agree before stale work is inherited.
    return bool(new and not (new & old))


def _draft_refinement_repeats_subject(text: str, prior: dict) -> bool:
    old, new = _draft_refinement_subject_sets(text, prior)
    return bool(old and new and (old & new))


_TURN_DERIVED_EMPTY = {
    "intent": "", "playbook": "", "keywords": [], "module": "", "mentioned_keys": [],
    "sufficient": False, "answer_depth": "", "request_plan": {}, "request_refinement": {},
    "continuation_contract": {}, "question_receipt_projection": {},
    "query_plan": {}, "query_results": [], "query_artifacts": {},
    "materialized_ticket_sources": {},
    "assignment_completion": {}, "bulk_targets": [],
    "pre_survey": "", "seed_map": "", "web_context": "", "topic_dossier": "",
    "situation": "", "evidence": [], "related_docs": [], "epic_candidate": "",
    "already_exists": False, "pmo_findings": [], "group_activity": "",
    "ticket_progress": "", "person_work_snapshot": {}, "daily_priority_snapshot": {},
    "knowledge_brief": {}, "pmo_caution": "",
    "interpretation": "", "structure_plan": [], "structure_ok": False,
    "structure_notes": [], "draft": {}, "assignments": [], "review": {},
    "reply": "", "error": "", "turns": 0, "execution_receipt": {},
    "change_audit_binding": {},
    "query_evidence_attestations": {},
    "evidence_workspace": {}, "freshness": {}, "quality_gate": {},
    "repair_budget": zero_typed_repair_budget(),
}


def _bounded_materialized_ticket_sources(value) -> dict:
    """Copy only the verified, bounded ticket ledger across an interview boundary.

    The full query artifacts deliberately reset every turn.  QueryRunner's durable sidecar
    contains at most eight successful ``get_ticket`` details.  Re-validate that boundary here
    as well so a legacy/manual checkpoint cannot turn continuation into an unbounded context
    replay, and retain parent keys only when their opened detail is present.
    """
    raw = value if isinstance(value, dict) else {}
    parent_search_attempted = raw.get("parentCandidateSearchAttempted") is True
    details: list[dict] = []
    seen: set[str] = set()
    for row in raw.get("ticketDetails") or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        key = str(row.get("key") or "").strip().upper()
        if not key or key in seen:
            continue
        detail = _copy.deepcopy(row)
        detail["key"] = key
        details.append(detail)
        seen.add(key)
        if len(details) >= 8:
            break
    if not details and not parent_search_attempted:
        return {}
    parents: list[str] = []
    for raw_key in raw.get("parentCandidateKeys") or []:
        key = str(raw_key or "").strip().upper()
        if key in seen and key not in parents:
            parents.append(key)
    bounded = {"ticketDetails": details, "parentCandidateKeys": parents}
    if parent_search_attempted:
        bounded["parentCandidateSearchAttempted"] = True
    return bounded


def _same_write_outcome_replacement(text: str, prior: dict) -> bool:
    """Whether this turn replaces an effect on one already-typed mutation target."""
    from app.agent.workflow.agents.request_architect import _continuation_outcome_directive

    utterance = str(text or "")
    prior_contract = prior.get("continuation_contract") or {}
    prior_targets = [
        str(key or "").strip().upper()
        for key in (prior_contract.get("target_keys") or [])
        if _re.fullmatch(r"[A-Z][A-Z0-9]{1,9}-\d+", str(key or "").strip(), _re.I)
    ] if has_typed_continuation_contract(prior_contract) else []
    current_keys = set(_recent_keys(utterance))
    return bool(
        prior.get("request_plan")
        and str(prior_contract.get("action") or "") in {"comment", "update", "mixed"}
        and len(prior_targets) == 1
        and _continuation_outcome_directive(utterance)
        and (not current_keys or current_keys <= set(prior_targets))
        and not _EXPLICIT_NEW_WORK.search(utterance)
    )


def _is_interview_continuation(text: str, prior: dict) -> bool:
    """Preserve prior work only for an interview answer or a bounded draft refinement.

    A draft follow-up commonly supplies several missing fields without repeating the original
    work request (for example: parent Epic + first-phase scope + due date).  Treating that as a
    new topic discards the researched subject and lets control words become the new task title.
    Conversely, one generic field such as a date is not enough to inherit stale context.
    """
    utterance = str(text or "")
    # RequestArchitect owns the exact typed-add grammar. Reuse it here so the session boundary
    # and outcome projection cannot disagree about whether ``Task 하나 더`` extends the current
    # plan. The import is local because Session already imports the assembled graph at startup.
    from app.agent.workflow.agents.request_architect import (
        _continuation_outcome_additions,
        _typed_continuation_refinement,
    )
    adds_typed_outcome = bool(prior.get("request_plan")
                              and _continuation_outcome_additions(utterance))
    same_write_outcome_replacement = _same_write_outcome_replacement(utterance, prior)
    # A typed ``댓글 대신 ...`` can be an in-plan outcome edit, but an explicit whole-topic
    # switch must always clear stale work even when that phrase happens to begin with an
    # outcome label.
    if _HARD_CONTEXT_SWITCH.search(utterance) and not same_write_outcome_replacement:
        return False
    if (_CONTEXT_SWITCH.search(utterance)
            and not same_write_outcome_replacement
            and not _FIELD_REPLACEMENT.search(utterance)
            and not _OUTCOME_REFINEMENT.search(utterance)):
        return False

    asked = [q for q in (prior.get("questions") or []) if isinstance(q, dict)]
    # A full new request with a new explicit ticket is not an answer merely because the last turn asked.
    # The exception is a typed target/parent answer: replacing an old key with a new key is exactly
    # what that question owns, so the new key must reach the latest-wins continuation ledger.
    old_keys = set(prior.get("mentioned_keys") or [])
    new_keys = set(_recent_keys(utterance))
    if asked:
        typed_contract = has_typed_continuation_contract(
            prior.get("continuation_contract")
        )
        parent_choice = any(
            str(question.get("field") or "").strip().casefold() in {"parent", "epic"}
            for question in asked
        ) and bool(_PARENT_CREATION_ANSWER.search(utterance))
        if adds_typed_outcome:
            return True
        if _EXPLICIT_NEW_WORK.search(utterance):
            # "새 Epic을 만들까요?"에 대한 답은 기존 interview의 continuation이다.
            # 다른 target/question을 버리고 별도 work를 명령한 경우에는 stale state를 잇지 않는다.
            if not parent_choice:
                return False
        decisions = capture_continuation_decisions(utterance, asked)
        if new_keys and old_keys and not new_keys.issubset(old_keys):
            keyed_answer = any(
                isinstance(row, dict)
                and row.get("source") == "interview_answer"
                and str(row.get("field") or "").split(":", 1)[0].casefold()
                in {"target", "table", "entity", "document", "ticket", "parent", "epic"}
                and new_keys <= set(_recent_keys(str(row.get("value") or "")))
                for row in decisions
            )
            if not keyed_answer:
                return False
        if typed_contract and has_interview_answer(decisions):
            # ``Task를 만들어줘`` can be a fresh request or simply the user's answer plus
            # "continue the draft".  Only the explicit continuation cue permits the latter;
            # a new named work request still resets before any stale plan can execute.
            if (_INDEPENDENT_WORK_CREATION.search(utterance)
                    and not has_continuation_cue(utterance)
                    and not parent_choice):
                return False
            # One utterance can contain a valid field answer and a separate read/summary
            # request.  The current continuation envelope cannot execute both atomically;
            # carrying the old write DAG would silently discard the latter speech act.
            if (not has_continuation_cue(utterance)
                    and (_NON_FIELD_REQUEST.search(utterance)
                         or _INDEPENDENT_READ_FOLLOWUP.search(utterance)
                         or _has_independent_question_clause(utterance, asked))):
                return False
            return True
        if typed_contract and has_multi_field_refinement(decisions):
            if (_INDEPENDENT_WORK_CREATION.search(utterance)
                    or _NON_FIELD_REQUEST.search(utterance)):
                return False
            return True
        # A fully parsed execution-field answer can close an optional legacy structure prompt
        # without asking RequestArchitect to rediscover the frozen plan. Required target/person/
        # term questions always win, including checkpoints whose answer matcher would otherwise
        # mistake ``Epic 은 네가 골라줘`` for a term definition.
        typed_fields = _typed_continuation_refinement(utterance)
        if typed_fields:
            return _pending_questions_are_optional(asked)
        if (parent_choice or _OUTCOME_REFINEMENT.search(utterance)
                or _looks_like_answer_to_questions(utterance, asked)):
            return True
        # A complete speech act is a new request even when it reuses the prior subject and
        # contains neither an explicit cancellation nor a new Jira key.
        if _INDEPENDENT_REQUEST.search(utterance):
            return False
        # Unrecognized short text is not automatically an interview answer. Korean requests
        # commonly omit a verb (``보안 교육 현황 파악 부탁``), so preserving stale state here
        # is more dangerous than routing the utterance as a fresh request.
        return False

    if new_keys and old_keys and not new_keys.issubset(old_keys):
        return False

    if same_write_outcome_replacement:
        return True

    # When the previous turn already produced a draft/structure, preserve it only for a compact,
    # multi-field refinement.  Explicitly asking for separate/new work always wins.
    if adds_typed_outcome:
        return True
    if _EXPLICIT_NEW_WORK.search(utterance):
        return False
    # A typed edit to an already planned user-visible outcome is itself a continuation even
    # when the previous turn has not materialized a draft yet.  RequestArchitect applies the
    # bounded typed diff; resetting here would erase the authoritative compound outcome DAG
    # before that guard can run.
    if prior.get("request_plan") and _OUTCOME_REFINEMENT.search(utterance):
        return True
    # A complete create/register speech act is a new work root even when an old draft or
    # structure exists and the sentence happens to include two refinement-shaped fields.
    # Check this before the materialized-draft shortcut; otherwise ``Task 하나 만들어줘``
    # silently edits stale work merely because it also supplies scope and a due date.
    if _INDEPENDENT_WORK_CREATION.search(utterance):
        return False
    if _draft_refinement_changes_subject(utterance, prior):
        return False
    matched_fields = sum(bool(pattern.search(utterance)) for pattern in _DRAFT_REFINEMENT_FIELDS)
    if matched_fields < 2:
        return False
    if prior.get("draft") or prior.get("structure_plan"):
        return True

    # With no draft/question, accept only a field-shaped continuation. A full Task creation
    # speech act or an unrelated answer/query remains a new turn even if it embeds scope and
    # due-date values. A repeated stable technical subject is also safe; otherwise the utterance
    # must begin with a recognized field label (``Epic... 범위... 마감...``).
    if _NON_FIELD_REQUEST.search(utterance):
        return False
    if (not _FIELD_ONLY_REFINEMENT_START.search(utterance)
            and not _draft_refinement_repeats_subject(utterance, prior)):
        return False

    # Work can fail after Research has already materialized authoritative evidence (for
    # example, a structured projection exhausts its output budget). A compact multi-field
    # answer is still a refinement of that frozen write request even though no draft/question
    # survived. Require both a typed write plan and at least one successful bounded detail;
    # this keeps unrelated work from inheriting stale evidence merely because it also names
    # a date and scope.
    tasks = (prior.get("request_plan") or {}).get("tasks") or []
    has_write_intent = any(
        isinstance(task, dict) and bool(task.get("write_intent")) for task in tasks
    )
    ledger = _bounded_materialized_ticket_sources(
        prior.get("materialized_ticket_sources")
    )
    return (
        str(prior.get("intent") or "") == "plan_work"
        and has_write_intent
        and bool(ledger.get("ticketDetails"))
    )


def _turn_start_patch(
    text: str,
    prior: dict,
    *,
    question_receipt_projection: dict | None = None,
    receipt_continuation_contract: dict | None = None,
    receipt_bound: bool = False,
) -> dict:
    """Separate per-turn working memory from durable conversation messages.

    LangGraph merges new input into the checkpoint.  Without explicit empty values, a new request inherits the
    previous topic dossier, draft, approval review, and PMO result.  Preserve research only while answering a
    blocking interview; every other turn receives a clean working set and a new request root.
    """
    typed_receipt = bool(question_receipt_projection)
    continuation = typed_receipt or receipt_bound or _is_interview_continuation(text, prior)
    from datetime import datetime as _datetime, timezone as _timezone

    current_correlation = str(prior.get("correlation_id") or "") if continuation else ""
    # A continuation keeps the same correlation id, but it is still a new observation
    # turn.  Reusing the first turn's timestamp makes freshly recomputed Role context
    # look stale once a user answers an interview more than the freshness window later.
    # Source rows retain their own attested retrieval timestamps, so refreshing this
    # Session-owned context timestamp cannot make cached connector evidence look fresh.
    turn_started_at = _datetime.now(_timezone.utc).isoformat()
    patch = _copy.deepcopy(_TURN_DERIVED_EMPTY)
    patch.update(turn_continuation=continuation,
                 turn_reset_reason="interview-answer" if continuation else "new-or-revised-request",
                 turn_attempt_id=uuid.uuid4().hex,
                 correlation_id=current_correlation or uuid.uuid4().hex,
                 correlation_started_at=turn_started_at)
    if continuation:
        # RequestArchitect owns these bounded planning fields.  A true continuation keeps
        # them so a deterministic field-only fast path can reuse the already classified
        # request without reconstructing its subject from the short answer.  Query plans and
        # query results deliberately stay in ``_TURN_DERIVED_EMPTY``: a changed parent/scope/
        # due field may require a fresh bounded query, and graph routing decides that below.
        for key in ("intent", "request_text", "request_plan", "playbook", "keywords",
                    "module", "mentioned_keys", "sufficient", "answer_depth",
                    "pre_survey", "seed_map", "web_context", "topic_dossier",
                    "situation", "evidence", "related_docs", "epic_candidate", "already_exists",
                    "bulk_targets", "materialized_ticket_sources", "structure_plan", "structure_ok",
                    "structure_notes", "draft", "turns"):
            if key in prior:
                patch[key] = (_bounded_materialized_ticket_sources(prior[key])
                              if key == "materialized_ticket_sources"
                              else _copy.deepcopy(prior[key]))
        contract = build_continuation_contract(
            {**prior, "turn_continuation": True},
            existing=prior.get("continuation_contract"),
            preserve_outcome_targets=_same_write_outcome_replacement(text, prior),
        )
        if typed_receipt:
            # The receipt boundary already projected and validated this contract exactly once.
            # Do not feed its bounded message back through the prose continuation parser.
            patch["continuation_contract"] = _copy.deepcopy(
                receipt_continuation_contract or {}
            )
            patch["question_receipt_projection"] = _copy.deepcopy(
                question_receipt_projection or {}
            )
        elif receipt_bound:
            # An authentic receipt proves this is the pending interview turn, but a field
            # without a lossless projector still belongs to semantic RequestArchitect.
            patch["continuation_contract"] = _copy.deepcopy(
                receipt_continuation_contract
                if receipt_continuation_contract is not None
                else prior.get("continuation_contract") or {}
            )
        elif contract:
            decisions = capture_continuation_decisions(
                text, [q for q in (prior.get("questions") or []) if isinstance(q, dict)],
            )
            patch["continuation_contract"] = merge_continuation_decisions(
                contract, decisions,
            )
        # ``bulk_targets`` is executable state consumed directly by WorkArchitect.  A
        # continuation may carry it only while the rebuilt typed contract independently
        # re-proves the same keys through the current write DAG/scoped decision.  Merely
        # copying an old list would let a background read key bypass the contract split.
        allowed_targets = {
            str(key or "").strip().upper()
            for key in (patch.get("continuation_contract") or {}).get("target_keys") or []
        }
        patch["bulk_targets"] = [
            str(key or "").strip().upper()
            for key in patch.get("bulk_targets") or []
            if str(key or "").strip().upper() in allowed_targets
        ]
    else:
        patch["request_text"] = str(text or "").strip()
    return patch


def _checkpoint_revision(snapshot) -> str:
    config = getattr(snapshot, "config", None) or {}
    configurable = config.get("configurable") if isinstance(config, dict) else {}
    return str((configurable or {}).get("checkpoint_id") or "").strip()


@dataclass(frozen=True)
class _PreparedTurn:
    initial: dict
    claim: ReceiptClaim | None
    checkpoint_revision: str
    error: str = ""


def _prepare_turn(
    graph,
    *,
    thread_id: str,
    text: str,
    user_role: str,
    user_id: str,
    question_receipt=None,
    engine: str = "",
    profile: str = "",
) -> _PreparedTurn:
    """Build one ask/stream input from the same checkpoint and turn-reset boundary."""
    try:
        prior_snapshot = graph.get_state(_config(thread_id))
        prior = dict(prior_snapshot.values or {})
    except Exception:
        prior_snapshot, prior = None, {}
    revision = _checkpoint_revision(prior_snapshot)
    claim: ReceiptClaim | None = None
    message_text = str(text or "")
    if question_receipt is not None:
        claim = claim_question_receipt(
            question_receipt, thread_id=thread_id, checkpoint_revision=revision,
            request_plan=prior.get("request_plan") or {},
            continuation_contract=prior.get("continuation_contract") or {},
        )
        if claim.status == "rejected":
            return _PreparedTurn(
                initial={}, claim=claim, checkpoint_revision=revision,
                error=("질문 답변이 만료되었거나 이미 제출되었습니다. "
                       "현재 질문을 다시 확인해 주세요."),
            )
        message_text = claim.message_text
    try:
        from app.agent.maxis.backend import prepare_thread_runtime

        runtime_pin = prepare_thread_runtime(prior, engine=engine, profile=profile)
        initial = _initial(thread_id, message_text, user_role, user_id)
        initial.update(runtime_pin)
        if claim and claim.owns_lease:
            initial.update(_turn_start_patch(
                message_text, prior,
                question_receipt_projection=(claim.projection
                                             if claim.status == "fast" else None),
                receipt_continuation_contract=claim.continuation_contract,
                receipt_bound=True,
            ))
        else:
            initial.update(_turn_start_patch(message_text, prior))
    except Exception as exc:
        if claim and claim.owns_lease:
            release_question_receipt(claim)
        from app.agent.maxis.backend import MaxisRuntimeError
        if isinstance(exc, MaxisRuntimeError):
            return _PreparedTurn(
                initial={}, claim=claim, checkpoint_revision=revision,
                error=str(exc)[:300],
            )
        raise
    return _PreparedTurn(
        initial=initial, claim=claim, checkpoint_revision=revision,
    )


def _finish_receipt(graph, thread_id: str, prepared: _PreparedTurn, *, success: bool) -> None:
    if not prepared.claim or not prepared.claim.owns_lease:
        return
    try:
        revision = _checkpoint_revision(graph.get_state(_config(thread_id)))
    except Exception:
        # Once graph execution was attempted, an unreadable checkpoint cannot prove that
        # nothing advanced.  A distinct sentinel makes finish fail closed to consumed.
        revision = "unknown-after-graph"
    finish_question_receipt(
        prepared.claim, success=success, checkpoint_revision=revision,
    )


def ask(text: str, thread_id: str = "", user_role: str = "", user_id: str = "",
        question_receipt=None, engine: str = "", profile: str = "") -> dict:
    """한 턴 굴린다. 승인이 필요한 지점에서 멈추면 `pending` 이 채워져 돌아온다."""
    tid = thread_id or new_thread()
    if question_receipt is not None and str(text or "").strip():
        return {"thread_id": tid, "ok": False, "reply": "",
                "error": "질문 답변과 새 메시지를 함께 보낼 수 없습니다.", "trace": []}
    too_long = _guard(text)
    if too_long:
        return {"thread_id": tid, "ok": False, "reply": too_long, "error": too_long, "trace": []}
    log.info("[%s] 요청 수신 chars=%d receipt=%s", tid, len(text or ""),
             bool(question_receipt))
    meter = _usage.Meter()
    graph = get_graph()
    with question_turn_lock(tid):
        prepared = _prepare_turn(
            graph, thread_id=tid, text=text, user_role=user_role, user_id=user_id,
            question_receipt=question_receipt, engine=engine, profile=profile,
        )
        if prepared.error:
            return {"thread_id": tid, "ok": False, "reply": "",
                    "error": prepared.error, "trace": []}
        try:
            state = graph.invoke(prepared.initial, _config(tid, meter))
        except Exception:
            _finish_receipt(graph, tid, prepared, success=False)
            raise
        _finish_receipt(graph, tid, prepared, success=True)
        try:
            snap = graph.get_state(_config(tid))
        except Exception:
            snap = None
        out = _shape(tid, state, snap)
    out["usage"] = _usage_with_remote(meter.snapshot(), state)
    log.info("[%s] 응답 완료 ok=%s reply_chars=%d pending=%s", tid,
             bool(out.get("ok")), len(out.get("reply") or ""), bool(out.get("pending")))
    log.info("[%s] 사용량: %s", tid, out["usage"])
    return out


def resume(thread_id: str, token: str, overrides: dict = None) -> dict:
    """사용자가 승인했다. 멈춰 있던 자리(ActionExecutor)에서 다시 굴린다.

    승인 표시는 여기서 한다 — 그래야 토큰이 **이 대화의 것**인지 확인할 수 있다.
    `overrides["assignees"]` 는 사용자가 승인 카드에서 고른 담당자({항목번호: uid}) —
    추천을 그대로 받는 게 아니라 후보 중 고르거나 직접 지정할 수 있어야 한다(사용자 요청).
    승인 전에 스테이징 내용과 State 를 **같이** 고쳐 지문을 다시 묶는다.
    """
    # Approval resumes and new chat turns mutate the same checkpoint.  Serialize the whole
    # capability transaction with ask/stream so an old paused branch cannot race a fresh
    # turn and restore its prior turn nonce/artifacts after the new request has started.
    with question_turn_lock(thread_id):
        pinned = {}
        try:
            from app.agent.maxis.backend import verify_thread_runtime

            pinned = get_graph().get_state(_config(thread_id)).values or {}
            verify_thread_runtime(dict(pinned))
        except Exception as exc:
            from app.agent.maxis.backend import MaxisRuntimeError
            if isinstance(exc, MaxisRuntimeError):
                # A runtime/profile mismatch permanently invalidates the approval card.
                # Reverting settings later must not revive a proposal reviewed under an
                # older route, dataset or contract.  Revoke only capabilities owned by
                # this thread so a caller cannot cancel another thread's token.
                candidates = [token, str(pinned.get("comment_token") or "")]
                for candidate in candidates:
                    record = approval.peek(candidate) if candidate else None
                    if record and record.get("thread") == str(thread_id):
                        approval.reject(candidate)
                return {"thread_id": thread_id, "ok": False, "error": str(exc)[:300]}
            raise
        actor_ok, actor_error = approval.verify_actor(token, thread_id)
        if not actor_ok:
            return {"thread_id": thread_id, "ok": False, "error": actor_error}
        err = _apply_overrides(thread_id, token, overrides)
        if err:
            return {"thread_id": thread_id, "ok": False, "error": err}
        if not approval.approve(token, thread_id):
            return {"thread_id": thread_id, "ok": False,
                    "error": "승인 토큰이 이 대화의 것이 아니거나 만료되었습니다. 다시 요청하세요."}
        # 변경 카드에 코멘트가 함께 보였다면 그 토큰도 같은 승인에 묶인다(내용은 카드에 있었다).
        vals = {}
        try:
            vals = get_graph().get_state(_config(thread_id)).values or {}
            if vals.get("comment_token"):
                approval.approve(vals["comment_token"], thread_id)
        except Exception:
            pass
        change_binding = None
        try:
            from app.agent.maxis.backend import record_change_audit

            correlation_id = str(pinned.get("correlation_id") or "")
            if correlation_id:
                approved_record = approval.peek(token)
                change_binding = approval.change_audit_binding(
                    token, correlation_id=correlation_id, record=approved_record,
                )
            record_change_audit(
                dict(pinned), event="change.approved", status="succeeded",
                change_binding=change_binding,
            )
            if change_binding is not None:
                get_graph().update_state(
                    _config(thread_id), {"change_audit_binding": change_binding},
                )
                pinned = {**dict(pinned), "change_audit_binding": change_binding}
        except Exception as exc:
            # No external write has started yet. Revoke the capabilities rather than
            # allowing an unaudited mutation to proceed.
            approval.reject(token)
            try:
                if vals.get("comment_token"):
                    approval.reject(vals["comment_token"])
            except Exception:
                pass
            return {"thread_id": thread_id, "ok": False, "error": str(exc)[:300]}
        from app.agent.tools import set_thread
        set_thread(thread_id)
        log.info("[%s] 승인됨 — 실행 시작", thread_id)
        meter = _usage.Meter()
        # None = 멈춘 자리(ActionExecutor 앞)에서 이어서
        try:
            state = get_graph().invoke(None, _config(thread_id, meter))
            out = _shape(thread_id, state)
        except Exception:
            try:
                from app.agent.maxis.backend import record_change_audit
                failed_binding = (
                    approval.finalize_change_audit_binding(
                        change_binding, token=token, thread_id=thread_id,
                    )
                    if change_binding is not None else None
                )
                record_change_audit(
                    dict(pinned), event="change.execution", status="failed",
                    error_code="execution_exception",
                    change_binding=failed_binding,
                )
            except Exception:
                log.warning("[%s] execution 감사 기록 실패", thread_id)
            try:
                from app.agent.impact import note_review
                note_review(thread_id=thread_id, approval_token=token,
                            outcome="failed", overrides=overrides,
                            execution_succeeded=False)
            except Exception:
                log.warning("[%s] impact 기록 실패 outcome=failed", thread_id)
            raise
        out["usage"] = _usage_with_remote(meter.snapshot(), state)
        result = out.get("result") if isinstance(out.get("result"), dict) else {}
        succeeded = bool(not out.get("error") and not (result.get("failed") or []))
        try:
            from app.agent.maxis.backend import record_change_audit
            record_change_audit(
                dict(state or pinned), event="change.execution",
                status="succeeded" if succeeded else "failed",
                error_code="" if succeeded else "execution_failed",
                change_binding=(state or pinned).get("change_audit_binding"),
            )
        except Exception:
            # The write may already have happened; never invite a duplicate retry merely
            # because the append-only audit sink became unavailable afterward.
            log.warning("[%s] execution 감사 기록 실패", thread_id)
        try:
            from app.agent.impact import note_review
            note_review(thread_id=thread_id, approval_token=token,
                        outcome="approved", overrides=overrides,
                        execution_succeeded=succeeded)
        except Exception:
            log.warning("[%s] impact 기록 실패 outcome=approved", thread_id)
        log.info("[%s] 실행 완료 ok=%s created=%d failed=%d", thread_id,
                 bool(out.get("ok")), len(result.get("created") or []),
                 len(result.get("failed") or []))
        return out


# 카드에서 편집 가능한 항목 필드 — 여기 없는 키는 조용히 버린다(클라이언트를 믿지 않는다).
_EDITABLE = ("summary", "description", "labels", "duedate", "priority", "epic", "assignee")
_CHILD_EDITABLE = ("summary", "assignee", "duedate")


def _apply_overrides(thread_id: str, token: str, overrides: dict) -> str:
    """승인 카드의 편집(담당자 + 제목·본문·라벨·마감·우선순위·Epic + 자식)을 반영한다.
    성공이면 빈 문자열, 실패면 사용자에게 보일 오류 문구.

    보증 방식: **State draft 만 고치고**, 스테이징 payload 는 그 draft 로부터
    `as_bulk_items`/`child_items` 로 **재생성**한다(approval.amend_payload). 승인 지문과
    ActionExecutor 실행 인자가 같은 함수를 지나므로 어긋날 길이 없다 — 담당자만 다루던 시절의
    부분 patch 두 벌(payload/State)은 list·빈값 처리에서 갈라질 수 있었다.
    """
    ov = overrides or {}
    assignees = {str(k): str(v or "").strip() for k, v in (ov.get("assignees") or {}).items()}
    item_edits = {str(k): v for k, v in (ov.get("items") or {}).items() if isinstance(v, dict)}
    child_edits = {str(k): v for k, v in (ov.get("children") or {}).items() if isinstance(v, dict)}
    if not (assignees or item_edits or child_edits):
        return ""

    # ── 담당자 실재 검증 — 화면 자동완성은 편의일 뿐 보증이 아니다.
    from app.agent.tools._ctx import client, settings
    from app.domain.search import search_users
    uids = {u for u in assignees.values() if u}
    uids |= {str(v.get("assignee") or "").strip() for v in item_edits.values() if v.get("assignee")}
    uids |= {str(v.get("assignee") or "").strip() for v in child_edits.values() if v.get("assignee")}
    for uid in {u for u in uids if u}:
        try:
            found = search_users(client(), settings(), uid, 5) or []
        except Exception:
            found = []
        if not any(str(u.get("id") or "") == uid for u in found):
            return f"담당자 '{uid}' 를 찾을 수 없습니다. 사번(skcc.x1042 형식)을 확인하세요."

    try:
        vals = get_graph().get_state(_config(thread_id)).values or {}
        draft = dict(vals.get("draft") or {})
        items = [dict(it) for it in (draft.get("items") or [])]
        for it in items:
            if it.get("children"):
                it["children"] = [dict(c) for c in it["children"] if isinstance(c, dict)]

        def _set(row: dict, field: str, val):
            if field == "labels":
                vals_ = [str(x).strip() for x in (val if isinstance(val, list) else
                                                  str(val or "").split(",")) if str(x).strip()]
                if vals_:
                    row["labels"] = vals_
                else:
                    row.pop("labels", None)
                return
            sval = str(val or "").strip()
            if field == "duedate" and sval:
                import re as _re2
                if not _re2.match(r"^\d{4}-\d{2}-\d{2}$", sval):
                    raise ValueError(f"마감일 형식이 잘못되었습니다: {sval} (YYYY-MM-DD)")
            if sval:
                row[field] = sval
            else:
                row.pop(field, None)

        for i, uid in assignees.items():
            idx = int(i)
            if 0 <= idx < len(items):
                _set(items[idx], "assignee", uid)
        for i, patch in item_edits.items():
            idx = int(i)
            if not (0 <= idx < len(items)):
                return f"초안에 없는 항목 번호입니다: {idx}"
            for f in _EDITABLE:
                if f in patch:
                    _set(items[idx], f, patch[f])
        for ij, patch in child_edits.items():
            try:
                pi, ci = (int(x) for x in str(ij).split("-", 1))
            except ValueError:
                return f"자식 항목 번호가 잘못되었습니다: {ij}"
            kids = (items[pi].get("children") or []) if 0 <= pi < len(items) else []
            if not (0 <= ci < len(kids)):
                return f"초안에 없는 자식 항목입니다: {ij}"
            for f in _CHILD_EDITABLE:
                if f in patch:
                    _set(kids[ci], f, patch[f])

        new_draft = dict(draft, items=items)
        from app.agent.workflow.agents.work_architect import as_bulk_items, child_items
        bulk = as_bulk_items(new_draft)
        mode = new_draft.get("mode") or "task"
        # 편집 결과도 같은 규칙으로 검증한다 — 카드에서 고쳤다고 규칙을 통과한 것은 아니다.
        if mode != "epic":
            from app.domain.bulk import validate_bulk
            r = validate_bulk(mode, bulk, client().bulk_lookup())
            if not r.get("ok"):
                msgs = "; ".join(f"[{e.get('index')}] {e.get('field')}: {e.get('message')}"
                                 for e in (r.get("errors") or [])[:3])
                return f"수정한 내용이 규칙에 걸립니다 — {msgs}"
        if mode == "epic":
            from app.agent.workflow.agents.work_architect import epic_payload
            payload = epic_payload(new_draft)
        else:
            payload = {"mode": mode, "items": bulk}
            kids = [k for k in child_items(new_draft) if isinstance(k, dict) and k.get("summary")]
            if kids:
                payload["children"] = kids
        ok, why = approval.amend_payload(token, thread_id, payload)
        if not ok:
            return why
        get_graph().update_state(_config(thread_id), {"draft": new_draft})
        return ""
    except ValueError as e:
        return str(e)[:160]
    except Exception as e:
        return f"수정을 반영하지 못했습니다: {str(e)[:120]}"


def cancel(thread_id: str, token: str) -> dict:
    """사용자가 거절했다. 토큰을 버린다 — 그래프는 멈춘 채로 두고 다음 발화로 이어 간다."""
    audit_state = {}
    try:
        audit_state = dict(get_graph().get_state(_config(thread_id)).values or {})
    except Exception:
        pass
    record = approval.peek(token)
    change_binding = None
    if record and str(record.get("thread") or "") == str(thread_id or ""):
        correlation_id = str(audit_state.get("correlation_id") or "")
        if correlation_id:
            try:
                # Capture the server-owned fingerprint and capability digest before reject()
                # destroys the one-use record.
                change_binding = approval.change_audit_binding(
                    token, correlation_id=correlation_id, record=record,
                )
            except Exception:
                change_binding = None
    rejected = approval.reject(token)
    if rejected and record and str(record.get("thread") or "") == str(thread_id or ""):
        try:
            from app.agent.maxis.backend import record_change_audit
            record_change_audit(
                audit_state, event="change.cancelled", status="blocked",
                error_code="user_cancelled",
                change_binding=change_binding,
            )
        except Exception:
            log.warning("[%s] cancellation 감사 기록 실패", thread_id)
    try:
        from app.agent.impact import note_review
        note_review(thread_id=thread_id, approval_token=token, outcome="cancelled")
    except Exception:
        log.warning("[%s] impact 기록 실패 outcome=cancelled", thread_id)
    log.info("[%s] 승인 거절", thread_id)
    return {"thread_id": thread_id, "ok": True, "cancelled": True}


def snapshot(thread_id: str) -> dict:
    """현재 State. 새로고침한 화면이 대화를 복원할 때 쓴다."""
    try:
        st = get_graph().get_state(_config(thread_id))
    except Exception:
        return {}
    return _shape(thread_id, dict(st.values or {}), st)


def evaluation_snapshot(thread_id: str) -> dict:
    """Return retrieval/research evidence for ignored local battery raw results.

    The user-facing API intentionally exposes only the shaped answer. A qualitative
    reviewer also needs to know what was searched: planned sources and queries,
    internal result artifacts, external attempts/URLs, and the claims ultimately used.
    This function is called only by manual evaluation harnesses; it omits messages,
    approval tokens, secrets, and provider configuration.
    """
    try:
        st = get_graph().get_state(_config(thread_id))
        data = as_dict(dict(st.values or {}))
    except Exception:
        return {}
    # The reviewer must see the same final evidence selection and deterministic
    # normalization as the rendered reply.  Keep the checkpoint itself immutable:
    # ``canonical_evaluation_evidence`` returns a bounded projection and never grants
    # model-authored prose source authority.
    from app.agent.workflow.agents.result_integrator import canonical_evaluation_evidence
    from app.agent.workflow.evidence_index import canonical_related_documents
    data["evidence"] = canonical_evaluation_evidence(data)
    data["related_docs"] = canonical_related_documents(
        data, data.get("related_docs") or [],
    )
    fields = {
        "requestPlan": "request_plan",
        "queryPlan": "query_plan",
        "queryResults": "query_results",
        "queryArtifacts": "query_artifacts",
        "preSurvey": "pre_survey",
        "seedMap": "seed_map",
        "webContext": "web_context",
        "topicDossier": "topic_dossier",
        "evidence": "evidence",
        "relatedDocs": "related_docs",
        "knowledgeBrief": "knowledge_brief",
        "trace": "trace",
    }
    return {public: data.get(internal) for public, internal in fields.items()
            if data.get(internal) not in (None, "", [], {})}


def _bounded_text(value, maximum: int = 500) -> str:
    return str(value or "").strip()[:maximum]


def _string_list(value, *, limit: int = 20, maximum: int = 500) -> list[str]:
    return [_bounded_text(item, maximum) for item in (value or [])[:limit]
            if _bounded_text(item, maximum)] if isinstance(value, list) else []


def _public_evidence_workspace(value) -> dict:
    """Project guarded evidence metadata without leaking source ``content`` or raw traces."""
    raw = value if isinstance(value, dict) else {}
    sources = []
    for source in (raw.get("sources") or [])[:20]:
        if not isinstance(source, dict):
            continue
        row = {
            "sourceId": _bounded_text(source.get("sourceId") or source.get("source_id"), 160),
            "kind": _bounded_text(source.get("kind"), 40),
            "canonicalId": _bounded_text(
                source.get("canonicalId") or source.get("canonical_id"), 240),
            "title": _bounded_text(source.get("title"), 300),
            "url": _bounded_text(source.get("url"), 1000),
            "retrievedAt": _bounded_text(
                source.get("retrievedAt") or source.get("retrieved_at"), 80),
            "updatedAt": _bounded_text(
                source.get("updatedAt") or source.get("updated_at"), 80),
            "partial": source.get("partial") is True,
            "truncated": source.get("truncated") is True,
        }
        # ``content``, snippets, remote ids and any unknown fields are intentionally absent.
        sources.append(row)

    facts = []
    for fact in (raw.get("facts") or [])[:50]:
        if not isinstance(fact, dict):
            continue
        facts.append({
            "text": _bounded_text(fact.get("text"), 800),
            "sourceIds": _string_list(
                fact.get("sourceIds") or fact.get("source_ids"), limit=8, maximum=160),
            "observedAt": _bounded_text(
                fact.get("observedAt") or fact.get("observed_at"), 80),
        })

    claims = []
    for claim in (raw.get("claims") or [])[:30]:
        if not isinstance(claim, dict):
            continue
        claims.append({
            "kind": _bounded_text(claim.get("kind"), 40),
            "text": _bounded_text(claim.get("text"), 800),
            "sourceIds": _string_list(
                claim.get("sourceIds") or claim.get("source_ids"), limit=8, maximum=160),
        })
    freshness = raw.get("freshness") if isinstance(raw.get("freshness"), dict) else {}
    return {
        "sources": sources,
        "facts": facts,
        "claims": claims,
        "missingInputs": _string_list(
            raw.get("missingInputs") or raw.get("missing_inputs"), limit=20, maximum=500),
        "warnings": _string_list(raw.get("warnings"), limit=20, maximum=500),
        "freshness": {
            "status": _bounded_text(freshness.get("status"), 40),
            "checkedAt": _bounded_text(
                freshness.get("checkedAt") or freshness.get("checked_at"), 80),
            "oldestSourceAt": _bounded_text(
                freshness.get("oldestSourceAt") or freshness.get("oldest_source_at"), 80),
            "maxAgeSeconds": freshness.get("maxAgeSeconds")
            if isinstance(freshness.get("maxAgeSeconds"), (int, float)) else None,
            "partial": freshness.get("partial") is True,
        },
        "partial": raw.get("partial") is True,
    }


def _public_quality_gate(value) -> dict:
    raw = value if isinstance(value, dict) else {}
    if not raw:
        return {}
    return {
        key: raw.get(key)
        for key in ("ok", "schemaValid", "citationValid", "freshnessValid", "numericParity")
        if isinstance(raw.get(key), (bool, int, float))
    } | {
        "errors": _string_list(raw.get("errors"), limit=20, maximum=300),
        "warnings": _string_list(raw.get("warnings"), limit=20, maximum=300),
    }


def _usage_with_remote(local_usage: dict, state: dict) -> dict:
    """Add validated MAXIS token/latency counters to the existing usage contract."""
    out = dict(local_usage or {})
    rows = state.get("runtime_role_telemetry") if isinstance(state, dict) else None
    if not isinstance(rows, dict):
        return out
    selected = {str(role): row for role, row in rows.items() if isinstance(row, dict)}
    if not selected:
        return out
    remote_calls = sum(max(0, int(row.get("calls") or 0)) for row in selected.values())
    prompt = sum(max(0, int(row.get("promptTokens") or 0)) for row in selected.values())
    completion = sum(
        max(0, int(row.get("completionTokens") or 0)) for row in selected.values()
    )
    out["calls"] = int(out.get("calls") or 0) + remote_calls
    out["promptTokens"] = int(out.get("promptTokens") or 0) + prompt
    out["completionTokens"] = int(out.get("completionTokens") or 0) + completion
    out["totalTokens"] = int(out.get("totalTokens") or 0) + prompt + completion
    out["remoteCalls"] = remote_calls
    if remote_calls and not out.get("model"):
        out["model"] = "maxis/dify"
    # Local provider pricing cannot price an internal MAXIS deployment.
    if remote_calls:
        out.pop("costUsd", None)
    by_node = {key: dict(value) for key, value in (out.get("byNode") or {}).items()
               if isinstance(value, dict)}
    details = [dict(row) for row in (out.get("callsDetail") or []) if isinstance(row, dict)]
    for role, row in sorted(selected.items()):
        calls = max(0, int(row.get("calls") or 0))
        tokens = max(0, int(row.get("promptTokens") or 0)) + max(
            0, int(row.get("completionTokens") or 0)
        )
        by_node[role] = {
            "calls": int((by_node.get(role) or {}).get("calls") or 0) + calls,
            "tokens": int((by_node.get(role) or {}).get("tokens") or 0) + tokens,
            "seconds": round(
                float((by_node.get(role) or {}).get("seconds") or 0)
                + max(0, int(row.get("latencyMs") or 0)) / 1000,
                3,
            ),
        }
        details.append({
            "node": role, "model": "maxis/dify", "engine": "maxis",
            "outputContract": "remote-role-result.v1",
            "executionLayer": "remote_semantic", "executionStage": "synthesis",
            "finishReason": "stop", "promptTokens": max(
                0, int(row.get("promptTokens") or 0)
            ),
            "completionTokens": max(0, int(row.get("completionTokens") or 0)),
            "seconds": round(max(0, int(row.get("latencyMs") or 0)) / 1000, 3),
            "calls": calls,
            "workflowBundleVersion": str(row.get("workflowBundleVersion") or "")[:120],
        })
    if by_node:
        out["byNode"] = by_node
    if details:
        out["callsDetail"] = details
    return out


def _shape(thread_id: str, state: dict, snap=None) -> dict:
    """State → 화면이 쓰는 모양. **비밀도 원본 메시지도 싣지 않는다.**"""
    if snap is None:
        try:
            snap = get_graph().get_state(_config(thread_id))
        except Exception:
            snap = None
    waiting = bool(snap and Node.ACTION_EXECUTOR in (getattr(snap, "next", None) or ()))

    data = as_dict(state or {})
    from app.agent.workflow.execution_receipt import scrub_execution_sidecars
    public_result = scrub_execution_sidecars(
        data.get("result") or {},
        secrets_to_remove=(data.get("approval_token"), data.get("comment_token")),
    )
    out = {"thread_id": thread_id, "ok": not data.get("error"),
           "reply": data.get("reply") or "", "trace": data.get("trace") or [],
           "intent": data.get("intent") or "", "situation": data.get("situation") or "",
           "evidence": data.get("evidence") or [], "related_docs": data.get("related_docs") or [],
           "questions": data.get("questions") or [], "assignments": data.get("assignments") or [],
           "review": data.get("review") or {}, "result": public_result,
           "error": data.get("error") or ""}

    for public, internal in (
        ("engine", "runtime_engine"),
        ("profile", "runtime_profile"),
        ("workflowBundleVersion", "workflow_bundle_version"),
        ("knowledgeVersion", "knowledge_version"),
        ("knowledgeDatasetId", "knowledge_dataset_id"),
        ("knowledgeManifestSha256", "knowledge_manifest_sha256"),
        ("correlationId", "correlation_id"),
        ("lastRole", "runtime_last_role"),
        ("lastRoleEngine", "runtime_last_role_engine"),
    ):
        if data.get(internal) not in (None, ""):
            out[public] = data[internal]
    role_engines = data.get("runtime_role_engines")
    if isinstance(role_engines, dict):
        safe_role_engines = {
            str(role): str(engine)
            for role, engine in role_engines.items()
            if str(role) and str(engine) in {"local", "maxis"}
        }
        if safe_role_engines:
            out["roleEngines"] = safe_role_engines
    workspace = _public_evidence_workspace(data.get("evidence_workspace"))
    if workspace.get("sources") or workspace.get("facts") or workspace.get("claims") \
            or workspace.get("missingInputs") or workspace.get("warnings") \
            or workspace.get("partial"):
        out["evidenceWorkspace"] = workspace
    if isinstance(data.get("freshness"), dict) and data.get("freshness"):
        freshness = _public_evidence_workspace({"freshness": data["freshness"]})["freshness"]
        out["freshness"] = freshness
    quality_gate = _public_quality_gate(data.get("quality_gate"))
    if quality_gate:
        out["qualityGate"] = quality_gate

    challenge = issue_question_challenge(
        thread_id=thread_id,
        checkpoint_revision=_checkpoint_revision(snap),
        request_plan=data.get("request_plan") or {},
        continuation_contract=data.get("continuation_contract") or {},
        questions=data.get("questions") or [],
    )
    if challenge:
        identities = challenge.get("questions") or []
        out["questions"] = [
            {**dict(question), "question_id": identity.get("question_id", "")}
            for question, identity in zip(out["questions"], identities)
            if isinstance(question, dict) and isinstance(identity, dict)
        ]
        out["questionReceipt"] = challenge

    # 승인 카드 — 무엇을 승인하는지가 화면과 토큰에 **같은 내용**으로 담겨야 한다.
    if waiting and data.get("approval_token"):
        plan = data.get("change_plan") or {}
        if plan.get("key") and (plan.get("transition") or {}).get("id"):
            # 전이·링크는 update 카드 UI 를 재사용한다 — changes dict 가 곧 표시 행이다.
            out["pending"] = {"token": data["approval_token"], "action": "update_ticket",
                              "key": plan["key"],
                              "changes": {"status": plan["transition"].get("name") or ""},
                              "comment": plan.get("comment") or "",
                              "rationale": plan.get("why") or ""}
        elif plan.get("key") and (plan.get("link") or {}).get("other"):
            lk = plan["link"]
            out["pending"] = {"token": data["approval_token"], "action": "update_ticket",
                              "key": plan["key"],
                              "changes": {"link": f"{lk.get('relation')} → {lk['other']}"},
                              "comment": plan.get("comment") or "",
                              "rationale": plan.get("why") or ""}
        elif plan.get("keys"):
            # 조건 일괄 수정 — 대상 전부와 공통 변경이 카드에 보여야 승인이 의미가 있다.
            # ★ 코멘트도 함께 싣는다 — **코멘트만 남기는 일괄**이 있고(사용자 요청),
            #   그때 카드에 아무것도 안 보이면 무엇을 승인하는지 알 수 없다.
            #   `comments` 는 티켓별 미리보기(멘션이 티켓마다 다르다).
            comment_only = not (plan.get("changes") or {}) and bool(
                plan.get("comments") or str(plan.get("comment") or "").strip())
            out["pending"] = {"token": data["approval_token"],
                              "action": "add_ticket_comments" if comment_only else "update_tickets",
                              "keys": plan["keys"], "changes": plan.get("changes") or {},
                              "comment": plan.get("comment") or "",
                              "comments": plan.get("comments") or [],
                              "rationale": plan.get("why") or ""}
        elif plan.get("key"):
            comment_only = not (plan.get("changes") or {}) and bool(
                str(plan.get("comment") or "").strip())
            out["pending"] = {"token": data["approval_token"],
                              "action": "add_ticket_comment" if comment_only else "update_ticket",
                              "key": plan["key"], "changes": plan.get("changes") or {},
                              "comment": plan.get("comment") or "",
                              "rationale": plan.get("why") or ""}
        else:
            from app.agent.workflow.agents.work_architect import as_bulk_items, child_items
            draft = data.get("draft") or {}
            out["pending"] = {"token": data["approval_token"], "action": "create_tickets",
                              "mode": draft.get("mode") or "task", "items": as_bulk_items(draft),
                              "rationale": draft.get("rationale") or ""}
            # 승인 화면은 **실제로 만들어질 것 전부**를 보여야 한다 — 자식 Sub-Task 가 카드에
            # 안 보이면 사용자는 부모 하나만 승인한 줄 안다(지문에는 이미 포함돼 있다).
            kids = child_items(draft)
            if kids:
                out["pending"]["children"] = kids
            # 구조 판단과 신규 라벨은 **사람이 검토할 거리**다. 숨기면 판단할 기회가 없다.
            for k in ("structure", "structure_why", "new_labels"):
                if draft.get(k):
                    out["pending"][k] = draft[k]

        pending = out.get("pending") or {}
        item_count = (len(pending.get("items") or []) + len(pending.get("children") or [])
                      if pending.get("action") == "create_tickets"
                      else len(pending.get("keys") or []) or 1)
        try:
            from app.agent.impact import note_draft
            role_engines = data.get("runtime_role_engines") or {}
            draft_engine = (
                str(role_engines.get("work_architect") or "")
                if isinstance(role_engines, dict) else ""
            )
            note_draft(
                thread_id=thread_id,
                approval_token=str(pending.get("token") or ""),
                item_count=item_count,
                engine=draft_engine or str(data.get("runtime_engine") or "local"),
                profile=str(data.get("runtime_profile") or ""),
                feature=str(pending.get("action") or "task_draft"),
                role="work_architect",
                workflow_bundle_version=str(data.get("workflow_bundle_version") or ""),
                knowledge_version=str(data.get("knowledge_version") or ""),
                knowledge_manifest_sha256=str(
                    data.get("knowledge_manifest_sha256") or ""
                ),
            )
        except Exception:
            log.warning("[%s] impact 초안 기록 실패", thread_id)

    # 생성 컨텍스트에서는 **작성 중인 초안**도 내려보낸다(승인 전 단계 포함) — 우측
    # 미리보기가 되묻기 라운드마다 갱신되며 자라는 것을 보여 준다(사용자 요청).
    from app.agent.workflow.state import Intent as _I
    if (data.get("intent") or "") in _I.DRAFTS_TICKETS and not out.get("pending"):
        items = (data.get("draft") or {}).get("items") or []
        if items:
            out["draft_items"] = items

    # 사람은 사번만 달랑 보내지 않는다 — 화면이 아바타+본명으로 그릴 수 있게 id→이름 지도를
    # 함께 싣는다(사용자 지적: "jira username만 딸랑 나오네"). 다른 화면들과 같은 포맷.
    out["people"] = _people_names(out)

    # 사용자가 JQL 을 요구했는데 답변에 쿼리가 안 실렸으면 **코드가** 붙인다 — 모델이
    # 표를 정리하며 쿼리 줄을 떨어뜨리는 일이 반복됐다(실측 3회). 쿼리는 재사용 자산이다.
    if out["reply"] and "JQL" not in out["reply"].upper():
        for f in (data.get("pmo_findings") or []):
            p = str(f.get("point") or "")
            if p.startswith("실행한 JQL"):
                out["reply"] += "\n\n" + p
                break

    # 한도·크레딧 오류는 원문(영어 JSON 덤프)이 아니라 **사람 말**로 — 사용자가 할 수 있는
    # 행동(잠시 후 재시도 / 크레딧 충전 / 간단 모델 설정)을 알려 준다(사용자 지적).
    if out["error"]:
        friendly = _friendly_error(out["error"])
        if friendly:
            out["error"] = friendly
            if not out["reply"]:
                out["reply"] = friendly
    return out


def _friendly_error(err: str) -> str:
    """LLM 공급자 오류 → 사용자 안내 문구. 모르는 오류는 그대로(숨기는 게 더 나쁘다)."""
    e = err or ""
    if "insufficient_quota" in e or "exceeded your current quota" in e:
        return ("⚠️ OpenAI 크레딧이 부족합니다. 결제 페이지에서 충전한 뒤 다시 시도해 주세요. "
                "(설정에서 다른 provider 로 바꿀 수도 있습니다)")
    if "429" in e or "Rate limit" in e or "rate_limit" in e or "tokens per min" in e:
        return ("⏳ 모델 사용량 한도(분당 토큰)에 걸렸습니다. 몇 초~1분 뒤 다시 시도해 주세요. "
                "자주 걸리면 설정에서 '간단한 역할 모델'에 가벼운 모델(gpt-4o-mini 등)을 "
                "지정하면 한도 여유가 커집니다.")
    if "context_length" in e or "maximum context length" in e:
        return ("⚠️ 요청이 모델의 컨텍스트 한도를 넘었습니다. 질문을 나누거나 붙여 넣은 "
                "자료를 줄여 주세요.")
    return ""


def _people_names(out: dict) -> dict:
    """응답에 등장하는 사번 전부의 본명 지도. 실패는 빈 지도 — 이름은 장식이지 조건이 아니다.

    답변 **본문 속** 사번도 긁는다 — 렌더러가 이 지도를 보고 사번을 프사+이름 칩으로
    바꾼다(사용자 지적: 채팅에 username 만 달랑 나온다). 지도에 없는 사번은 그대로 둔다.
    """
    import re as _re
    # ★ `\b` 는 **한글 앞에서 서지 않는다** — "skcc.x1042입니다" 가 매칭에서 빠져
    #   지도가 비고 화면에 사번이 날것으로 남았다(실측 U2). ASCII 경계로 본다.
    uids = set(_re.findall(r"(?<![0-9A-Za-z._])(skcc\.[a-z]{1,2}[0-9]{2,6})(?![0-9A-Za-z._])",
                           out.get("reply") or ""))
    for a in out.get("assignments") or []:
        uids.add(a.get("user") or "")
        for alt in a.get("alternates") or []:
            uids.add((alt or {}).get("user") or "")
    for it in (out.get("pending") or {}).get("items") or []:
        uids.add(it.get("assignee") or "")
    ch = (out.get("pending") or {}).get("changes") or {}
    uids.add(ch.get("assignee") or "")
    uids.discard("")
    if not uids:
        return {}
    names = {}
    try:
        from app.agent.tools._ctx import client, settings
        from app.domain.search import search_users
        c, s = client(), settings()
        for uid in list(uids)[:12]:
            for r in search_users(c, s, uid, 3) or []:
                if str(r.get("id") or "") == uid:
                    names[uid] = r.get("name") or ""
                    break
    except Exception:
        pass
    return names


_STOP: set[str] = set()          # 사용자가 중단을 누른 thread_id (단일 사용자 앱)


def request_stop(thread_id: str) -> bool:
    """진행 중인 턴을 멈춰 달라는 신호. 다음 노드 경계에서 스트림이 끊긴다.

    LangGraph 는 실행 중인 노드 하나를 중간에 잘라 내지 못한다 — 지금 도는 LLM 호출은
    끝까지 간다. 대신 **그다음 노드로 넘어가지 않는다**(체크포인터에는 거기까지가 남아
    이어서 물으면 그 지점부터다).
    """
    tid = (thread_id or "").strip()
    if not tid:
        return False
    _STOP.add(tid)
    log.info("[%s] 중단 요청", tid)
    return True


def stream(text: str, thread_id: str = "", user_role: str = "", user_id: str = "",
           question_receipt=None, engine: str = "", profile: str = ""):
    """진행 상황을 흘려보낸다. 조사에 십수 초가 걸리는데 빈 화면을 보여 줄 수는 없다.

    `subgraphs=True` 를 쓰는 이유 — 역할 안에서 도구를 부르는 중이라는 것까지 보여야
    "멈춘 것"과 "일하는 중"이 구분된다.
    """
    tid = thread_id or new_thread()
    if question_receipt is not None and str(text or "").strip():
        yield {"type": "start", "thread_id": tid}
        message = "질문 답변과 새 메시지를 함께 보낼 수 없습니다."
        yield {"type": "final", "thread_id": tid, "ok": False, "reply": "",
               "error": message, "trace": []}
        return
    too_long = _guard(text)
    if too_long:
        yield {"type": "start", "thread_id": tid}
        yield {"type": "final", "thread_id": tid, "ok": False, "reply": too_long,
               "error": too_long, "trace": []}
        return
    log.info("[%s] 스트림 요청 수신 chars=%d receipt=%s", tid, len(text or ""),
             bool(question_receipt))
    meter = _usage.Meter()
    yield {"type": "start", "thread_id": tid}
    with question_turn_lock(tid):
        _STOP.discard(tid)      # 같은 thread의 이전 turn이 끝난 뒤에만 stop 신호를 초기화한다
        graph = get_graph()
        prepared = _prepare_turn(
            graph, thread_id=tid, text=text, user_role=user_role, user_id=user_id,
            question_receipt=question_receipt, engine=engine, profile=profile,
        )
        if prepared.error:
            yield {"type": "final", "thread_id": tid, "ok": False, "reply": "",
                   "error": prepared.error, "trace": []}
            return
        completed = False
        try:
            # updates(진행) + messages(토큰) 를 함께 받는다 — 최종 답이 통째로 도착하기를
            # 기다리면 ResultIntegrator 생성 시간(2~7초)이 전부 침묵이 된다.
            for item in graph.stream(
                    prepared.initial, _config(tid, meter),
                    stream_mode=["updates", "messages"], subgraphs=True):
                ns, mode, payload = (item if len(item) == 3 else ("", item[0], item[1]))
                if tid in _STOP:
                    _STOP.discard(tid)
                    log.info("[%s] 중단됨", tid)
                    yield {"type": "stopped", "thread_id": tid,
                           "message": "요청하신 대로 중단했습니다. 여기까지 진행된 내용은 "
                                      "남아 있어 이어서 물으면 그 지점부터 계속합니다."}
                    return
                if mode == "messages":
                    msg, meta = payload
                    node = str((meta or {}).get("langgraph_node") or "")
                    piece = getattr(msg, "content", "") or ""
                    if (node == Node.RESULT_INTEGRATOR and piece
                            and type(msg).__name__.endswith("Chunk")):
                        yield {"type": "token", "text": piece}
                    continue
                for ev in _events(ns, payload):
                    yield ev
            completed = True
        except GeneratorExit:
            raise
        except Exception as e:
            log.exception("[%s] 그래프 실패", tid)
            yield {"type": "error", "message": str(e)[:300]}
        finally:
            _finish_receipt(graph, tid, prepared, success=completed)
        snap = graph.get_state(_config(tid))
        final = _shape(tid, dict((snap.values or {})), snap)
        final["usage"] = _usage_with_remote(meter.snapshot(), dict(snap.values or {}))
        log.info("[%s] 스트림 응답 완료 ok=%s reply_chars=%d pending=%s", tid,
                 bool(final.get("ok")), len(final.get("reply") or ""),
                 bool(final.get("pending")))
        log.info("[%s] 사용량: %s", tid, final["usage"])
        yield {"type": "final", **final}


_TOOL_KO = {  # 도구명 → 사람이 읽는 라벨. "도구 사용 중"만으로는 어디서 느린지 모른다(사용자 지적)
    "search_work_history": "사내 이력 검색", "deep_search": "의미 기반 재검색(RAG)",
    "find_mentions": "언급 추적(코멘트 원문까지)", "read_document": "문서 본문 열람",
    "get_ticket": "티켓 열람", "get_ticket_context": "연관 링크 추적",
    "map_ticket_neighborhood": "계보·주변 지도", "get_epic_tree": "Epic 트리 조회",
    "find_parent_epic": "상위 Epic 탐색", "run_jql": "JQL 실행",
    "search_rules": "사내 규칙·가이드 검색", "search_web": "웹 검색",
    "search_github": "GitHub 검색", "get_team_workload": "팀 워크로드 조회",
    "get_module_people": "모듈 로스터 조회", "get_person_profile": "인물 프로필 조회",
    "get_ticket_participants": "티켓 유관자 조회", "get_my_workload": "내 일감 조회",
    "get_progress": "진척률 계산", "find_stale_tickets": "정체 티켓 조회",
    "find_unassigned_tickets": "미배정 티켓 조회", "get_user_activity": "활동 내역 조회",
    "whoami": "사용자 확인", "list_ticket_options": "허용값 조회",
    "list_child_types": "하위 유형 조회", "validate_ticket_plan": "초안 검증",
    "create_tickets": "티켓 생성", "create_epic": "Epic 생성",
    "update_ticket": "티켓 변경", "add_ticket_comment": "코멘트 등록",
}


def _plan_for(intent: str) -> list:
    """의도 → 지날 단계 체크리스트. **코드가 안다** — 그래프 배선이 결정적이라 라우터와
    같은 지식을 여기 한 번 더 적는 것이고, UI 는 이걸 [ ]→[▸]→[✓] 로 채워 간다.
    실제로 안 지나는 단계(예: 첫 턴에 knowledge_curator 미경유)는 화면이 '건너뜀'으로 접는다.
    """
    from app.agent.workflow.state import Intent, Stage
    def _s(*nodes):
        return [{"id": n, "label": Stage.LABELS.get(n, n)} for n in nodes]
    if intent in Intent.DRAFTS_TICKETS:            # plan_work(버그 포함) / modify
        if intent == Intent.MODIFY:
            return _s(Node.REQUEST_ARCHITECT, Node.RESEARCH_ANALYST, Node.WORK_ARCHITECT, Node.RESULT_INTEGRATOR)
        return _s(Node.REQUEST_ARCHITECT, Node.RESEARCH_ANALYST, Node.WORK_ARCHITECT,
                  Node.PEOPLE_ADVISOR, Node.AUDITOR, Node.RESULT_INTEGRATOR)
    if intent in Intent.DIRECT_ANSWER:             # my_day / progress / activity
        return _s(Node.REQUEST_ARCHITECT, Node.PORTFOLIO_ANALYST, Node.RESULT_INTEGRATOR)
    if intent == Intent.ASK:
        return _s(Node.REQUEST_ARCHITECT, Node.RESEARCH_ANALYST, Node.KNOWLEDGE_CURATOR, Node.RESULT_INTEGRATOR)
    return _s(Node.REQUEST_ARCHITECT, Node.RESULT_INTEGRATOR)        # chitchat 등


def _arg_hint(tool_calls) -> str:
    """도구 호출 인자에서 사람이 읽을 한 조각 — 대개 검색어·티켓 키다."""
    for tc in tool_calls:
        for v in (tc.get("args") or {}).values():
            if isinstance(v, str) and len(v.strip()) > 2:
                return v.strip()[:48]
    return ""


def _result_hint(content) -> str:
    """도구 결과에서 사람이 읽을 한 조각 — URL > 티켓 키 > 앞부분 순으로 고른다."""
    import re as _re
    s = content if isinstance(content, str) else str(content or "")
    m = _re.search(r"https?://([^\s\"'\\)>\]]+)", s)
    if m:
        u = m.group(1)
        return u[:44] + ("…" if len(u) > 44 else "")
    keys = _re.findall(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b", s)
    if keys:
        seen = list(dict.fromkeys(keys))[:3]
        return ", ".join(seen)
    s = _re.sub(r"[\s{}\[\]\"']+", " ", s).strip()
    return (s[:44] + "…") if len(s) > 44 else s


def _events(ns, payload):
    """LangGraph 업데이트 → 화면 이벤트.

    화면의 최상위는 **플랜 단계**(의도별 체크리스트)이고, 서브그래프 안의 행위(도구 호출·
    결과)는 그 단계 밑에 중첩된다(사용자 피드백). `ns` 가 어느 단계 소속인지 알려 준다 —
    `("research_analyst:uuid",)` 형태라 앞 토막이 부모 노드명이다.
    """
    if not isinstance(payload, dict):
        return
    from collections import Counter

    from app.agent.workflow.state import Stage
    parent = ""
    if ns:
        try:
            parent = str(ns[0]).split(":")[0]
        except Exception:
            parent = ""
    for node, patch in payload.items():
        if node == "think":
            # think 의 산출(AIMessage)에 다음에 부를 도구가 실려 있다 — 이름과 인자를 보여 준다.
            calls = [tc for m in ((patch or {}).get("messages") or [])
                     for tc in (getattr(m, "tool_calls", None) or [])]
            if calls:
                cnt = Counter(_TOOL_KO.get(tc.get("name"), tc.get("name")) for tc in calls)
                label = " · ".join(f"{n} ×{c}" if c > 1 else n for n, c in cnt.items())[:80]
                yield {"type": "step", "parent": parent, "label": label,
                       "note": _arg_hint(calls)}
            else:
                yield {"type": "step", "parent": parent, "label": "조사 내용 정리 중"}
            continue
        if node == "act":
            # "도구 실행 결과 수신" 같은 기계 말 대신 — 무엇이 끝났고 무엇을 얻었는지.
            msgs = (patch or {}).get("messages") or []
            names = list(dict.fromkeys(
                _TOOL_KO.get(getattr(m, "name", "") or "", getattr(m, "name", "") or "도구")
                for m in msgs))
            hints = [h for h in (_result_hint(getattr(m, "content", "")) for m in msgs[:2]) if h]
            if names:
                yield {"type": "step", "parent": parent, "done": True,
                       "label": (" · ".join(names[:3]))[:80] + " 완료",
                       "note": " · ".join(hints)[:80]}
            continue
        if node not in Stage.LABELS:
            continue        # merge·propose·conclude 등 내부 배선은 사람에게 소음이다
        if node == Node.REQUEST_ARCHITECT and isinstance(patch, dict) and patch.get("intent"):
            # 의도가 정해졌다 = 앞으로 지날 단계가 정해졌다. 체크리스트를 먼저 내린다.
            yield {"type": "plan", "steps": _plan_for(patch["intent"])}
        ev = {"type": "node", "node": node, "label": Stage.LABELS[node]}
        if isinstance(patch, dict) and patch.get("trace"):
            ev["note"] = (patch["trace"][-1] or {}).get("note", "")
        yield ev
