"""agent/workflow/session.py — 그래프를 굴리는 바깥 API. 라우트는 이것만 부른다.

여기가 하는 일은 셋이다.

  · **대화를 잇는다** — `thread_id` 로 Checkpointer 에 State 를 맡긴다. 되묻기가 가능해지는 이유.
  · **관측을 붙인다** — Langfuse `CallbackHandler(session_id=thread_id)` 를 모든 실행에 단다.
    한 대화가 한 세션으로 묶여야 트레이스를 읽을 수 있다.
  · **승인 대기를 노출한다** — 그래프가 ActionExecutor 앞에서 멈췄다는 사실과, 무엇을 승인해야 하는지를
    화면이 알 수 있는 형태로 돌려준다.

**질의·응답은 파일로도 남긴다.** Langfuse 가 없는 환경(폐쇄망·미설정)에서도 무엇을 물었고 무엇을
답했는지는 남아야 한다. 관측 도구가 없다고 기록이 없어지면 사고가 났을 때 아무것도 못 본다.
"""

from __future__ import annotations

import re as _re
import copy as _copy

import logging
import uuid

from langchain_core.messages import HumanMessage

from app.agent import approval
from app.agent import config as _cfg
from app.agent import usage as _usage
from app.agent.workflow.graph import get_graph
from app.agent.workflow.state import TRACE_RESET, Node, Role, as_dict

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
    """'내가 누구인가' — 현재 사용자 정체 한 줄. 모든 역할의 시스템 프롬프트에 실린다.

    "내 모듈", "나한테 맞는 일" 같은 말은 정체를 알아야 해석된다. 매 역할이 whoami 를
    부르게 하는 대신 세션 시작에 코드가 한 번 해석해 State 로 준다(사용자 요청).

    ★ 프로세스 캐시(5분) — 이 해석이 사용자 조회·로스터 스캔을 매 턴 다시 했고, 그게
    턴 시작 오버헤드의 절반이었다(타임라인 실측: 첫 이벤트까지 ~4.6s 중 2~3s).
    """
    import time as _t
    if _IDENTITY_CACHE["val"] is not None and _t.time() - _IDENTITY_CACHE["at"] < _IDENTITY_TTL:
        return _IDENTITY_CACHE["val"]
    try:
        from app.agent.tools._ctx import client, settings
        from app.domain.search import search_users
        from app.infra.settings import is_manager, load_people
        me = (client().current_user() or {})
        uid = me.get("name") or me.get("key") or ""
        if not uid:
            return ""
        name = ""
        for u in (search_users(client(), settings(), uid, 5) or []):
            if str(u.get("id") or "") == uid:
                name = u.get("name") or ""
                break
        mods = [m for m, ids in (load_people() or {}).items() if uid in (ids or [])]
        mgr = bool(is_manager(settings(), me))
        val = (f"The current user is {name or uid} ({uid})"
               + (f", member of module(s): {', '.join(mods)}" if mods else "")
               + (", and IS a manager." if mgr else ", not a manager."))
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


_KEY_RE = _re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


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
            "approval_token": "", "comment_token": "", "result": {}, "revisions": 0,
            # trace 는 리듀서 필드라 [] 대입으로는 안 비워진다 — 리셋 신호를 앞에 싣는다.
            "trace": [TRACE_RESET], "change_plan": {}, "questions": []}


_CONTEXT_SWITCH = _re.compile(
    r"(?:이건|이거|그건|그거).{0,8}(?:그만|취소)|완전히\s*다른|"
    r"잠깐\s*다른|최종\s*요청|(?:댓글|변경|요청).{0,8}(?:도\s*)?취소|"
    r"(?:대신|다시\s+.+?돌아갈)", _re.I,
)

_TURN_DERIVED_EMPTY = {
    "intent": "", "playbook": "", "keywords": [], "module": "", "mentioned_keys": [],
    "sufficient": False, "answer_depth": "", "request_plan": {},
    "query_plan": {}, "query_results": [], "query_artifacts": {},
    "assignment_completion": {}, "bulk_targets": [],
    "pre_survey": "", "seed_map": "", "web_context": "", "topic_dossier": "",
    "situation": "", "evidence": [], "related_docs": [], "epic_candidate": "",
    "already_exists": False, "pmo_findings": [], "group_activity": "",
    "ticket_progress": "", "person_work_snapshot": {}, "daily_priority_snapshot": {},
    "knowledge_brief": {}, "pmo_caution": "",
    "interpretation": "", "structure_plan": [], "structure_ok": False,
    "structure_notes": [], "draft": {}, "assignments": [], "review": {},
    "reply": "", "error": "", "turns": 0,
}


def _is_interview_continuation(text: str, prior: dict) -> bool:
    """Keep expensive research only for an actual answer to our unresolved question."""
    asked = [q for q in (prior.get("questions") or []) if isinstance(q, dict)]
    if not asked or _CONTEXT_SWITCH.search(str(text or "")):
        return False
    # A full new request with a new explicit ticket is not an answer merely because the last turn asked.
    old_keys = set(prior.get("mentioned_keys") or [])
    new_keys = set(_recent_keys(text))
    if new_keys and old_keys and not new_keys.issubset(old_keys):
        return False
    return True


def _turn_start_patch(text: str, prior: dict) -> dict:
    """Separate per-turn working memory from durable conversation messages.

    LangGraph merges new input into the checkpoint.  Without explicit empty values, a new request inherits the
    previous topic dossier, draft, approval review, and PMO result.  Preserve research only while answering a
    blocking interview; every other turn receives a clean working set and a new request root.
    """
    continuation = _is_interview_continuation(text, prior)
    patch = _copy.deepcopy(_TURN_DERIVED_EMPTY)
    patch.update(turn_continuation=continuation,
                 turn_reset_reason="interview-answer" if continuation else "new-or-revised-request")
    if continuation:
        for key in ("request_text", "pre_survey", "seed_map", "web_context", "topic_dossier",
                    "situation", "evidence", "related_docs", "epic_candidate", "already_exists",
                    "bulk_targets", "structure_plan", "structure_ok", "structure_notes", "draft",
                    "turns"):
            if key in prior:
                patch[key] = prior[key]
    else:
        patch["request_text"] = str(text or "").strip()
    return patch


def ask(text: str, thread_id: str = "", user_role: str = "", user_id: str = "") -> dict:
    """한 턴 굴린다. 승인이 필요한 지점에서 멈추면 `pending` 이 채워져 돌아온다."""
    tid = thread_id or new_thread()
    too_long = _guard(text)
    if too_long:
        return {"thread_id": tid, "ok": False, "reply": too_long, "error": too_long, "trace": []}
    log.info("[%s] Q: %s", tid, (text or "")[:500])
    meter = _usage.Meter()
    graph = get_graph()
    try:
        prior = dict((graph.get_state(_config(tid)).values or {}))
    except Exception:
        prior = {}
    initial = _initial(tid, text, user_role, user_id)
    initial.update(_turn_start_patch(text, prior))
    state = graph.invoke(initial, _config(tid, meter))
    out = _shape(tid, state)
    out["usage"] = meter.snapshot()
    log.info("[%s] A: %s", tid, (out.get("reply") or "")[:1000])
    log.info("[%s] 사용량: %s", tid, out["usage"])
    return out


def resume(thread_id: str, token: str, overrides: dict = None) -> dict:
    """사용자가 승인했다. 멈춰 있던 자리(ActionExecutor)에서 다시 굴린다.

    승인 표시는 여기서 한다 — 그래야 토큰이 **이 대화의 것**인지 확인할 수 있다.
    `overrides["assignees"]` 는 사용자가 승인 카드에서 고른 담당자({항목번호: uid}) —
    추천을 그대로 받는 게 아니라 후보 중 고르거나 직접 지정할 수 있어야 한다(사용자 요청).
    승인 전에 스테이징 내용과 State 를 **같이** 고쳐 지문을 다시 묶는다.
    """
    err = _apply_overrides(thread_id, token, overrides)
    if err:
        return {"thread_id": thread_id, "ok": False, "error": err}
    if not approval.approve(token, thread_id):
        return {"thread_id": thread_id, "ok": False,
                "error": "승인 토큰이 이 대화의 것이 아니거나 만료되었습니다. 다시 요청하세요."}
    # 변경 카드에 코멘트가 함께 보였다면 그 토큰도 같은 승인에 묶인다(내용은 카드에 있었다).
    try:
        vals = get_graph().get_state(_config(thread_id)).values or {}
        if vals.get("comment_token"):
            approval.approve(vals["comment_token"], thread_id)
    except Exception:
        pass
    from app.agent.tools import set_thread
    set_thread(thread_id)
    log.info("[%s] 승인됨 — 실행 시작", thread_id)
    meter = _usage.Meter()
    # None = 멈춘 자리(ActionExecutor 앞)에서 이어서
    state = get_graph().invoke(None, _config(thread_id, meter))
    out = _shape(thread_id, state)
    out["usage"] = meter.snapshot()
    log.info("[%s] 실행 결과: %s", thread_id, out.get("result"))
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
    approval.reject(token)
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


def _shape(thread_id: str, state: dict, snap=None) -> dict:
    """State → 화면이 쓰는 모양. **비밀도 원본 메시지도 싣지 않는다.**"""
    if snap is None:
        try:
            snap = get_graph().get_state(_config(thread_id))
        except Exception:
            snap = None
    waiting = bool(snap and Node.ACTION_EXECUTOR in (getattr(snap, "next", None) or ()))

    data = as_dict(state or {})
    out = {"thread_id": thread_id, "ok": not data.get("error"),
           "reply": data.get("reply") or "", "trace": data.get("trace") or [],
           "intent": data.get("intent") or "", "situation": data.get("situation") or "",
           "evidence": data.get("evidence") or [], "related_docs": data.get("related_docs") or [],
           "questions": data.get("questions") or [], "assignments": data.get("assignments") or [],
           "review": data.get("review") or {}, "result": data.get("result") or {},
           "error": data.get("error") or ""}

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
                              "comment": "", "rationale": plan.get("why") or ""}
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


def stream(text: str, thread_id: str = "", user_role: str = "", user_id: str = ""):
    """진행 상황을 흘려보낸다. 조사에 십수 초가 걸리는데 빈 화면을 보여 줄 수는 없다.

    `subgraphs=True` 를 쓰는 이유 — 역할 안에서 도구를 부르는 중이라는 것까지 보여야
    "멈춘 것"과 "일하는 중"이 구분된다.
    """
    tid = thread_id or new_thread()
    too_long = _guard(text)
    if too_long:
        yield {"type": "start", "thread_id": tid}
        yield {"type": "final", "thread_id": tid, "ok": False, "reply": too_long,
               "error": too_long, "trace": []}
        return
    log.info("[%s] Q(stream): %s", tid, (text or "")[:500])
    meter = _usage.Meter()
    _STOP.discard(tid)          # 새 턴은 깨끗한 상태에서 — 지난 중단 신호를 물려받지 않는다
    yield {"type": "start", "thread_id": tid}
    try:
        # updates(진행) + messages(토큰) 를 함께 받는다 — 최종 답이 통째로 도착하기를
        # 기다리면 ResultIntegrator 생성 시간(2~7초)이 전부 침묵이 된다. ResultIntegrator 의 토큰만
        # 흘리는 이유: 중간 역할(think·conclude)의 글은 사용자용 문장이 아니다.
        for item in get_graph().stream(_initial(tid, text, user_role, user_id),
                                       _config(tid, meter),
                                       stream_mode=["updates", "messages"], subgraphs=True):
            # subgraphs=True + 리스트 모드 → (ns, mode, payload)
            ns, mode, payload = (item if len(item) == 3 else ("", item[0], item[1]))
            # 중단 — 사용자가 멈추라고 했다. 지금 노드가 끝나는 경계에서 빠져나간다.
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
                # ★ Chunk 타입만 — 스트림이 끝나면 **완성 메시지**가 한 번 더 흘러온다
                #   (실측: 같은 답이 두 번 조립됐다). 조각과 완성본을 둘 다 받으면 두 배가 된다.
                if (node == Node.RESULT_INTEGRATOR and piece
                        and type(msg).__name__.endswith("Chunk")):
                    yield {"type": "token", "text": piece}
                continue
            for ev in _events(ns, payload):
                yield ev
    except Exception as e:
        log.exception("[%s] 그래프 실패", tid)
        yield {"type": "error", "message": str(e)[:300]}
    final = _shape(tid, dict((get_graph().get_state(_config(tid)).values or {})))
    final["usage"] = meter.snapshot()
    log.info("[%s] A(stream): %s", tid, (final.get("reply") or "")[:1000])
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
