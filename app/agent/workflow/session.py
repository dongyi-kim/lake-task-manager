"""agent/workflow/session.py — 그래프를 굴리는 바깥 API. 라우트는 이것만 부른다.

여기가 하는 일은 셋이다.

  · **대화를 잇는다** — `thread_id` 로 Checkpointer 에 State 를 맡긴다. 되묻기가 가능해지는 이유.
  · **관측을 붙인다** — Langfuse `CallbackHandler(session_id=thread_id)` 를 모든 실행에 단다.
    한 대화가 한 세션으로 묶여야 트레이스를 읽을 수 있다.
  · **승인 대기를 노출한다** — 그래프가 Operator 앞에서 멈췄다는 사실과, 무엇을 승인해야 하는지를
    화면이 알 수 있는 형태로 돌려준다.

**질의·응답은 파일로도 남긴다.** Langfuse 가 없는 환경(폐쇄망·미설정)에서도 무엇을 물었고 무엇을
답했는지는 남아야 한다. 관측 도구가 없다고 기록이 없어지면 사고가 났을 때 아무것도 못 본다.
"""

from __future__ import annotations

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


def _identity() -> str:
    """'내가 누구인가' — 현재 사용자 정체 한 줄. 모든 역할의 시스템 프롬프트에 실린다.

    "내 모듈", "나한테 맞는 일" 같은 말은 정체를 알아야 해석된다. 매 역할이 whoami 를
    부르게 하는 대신 세션 시작에 코드가 한 번 해석해 State 로 준다(사용자 요청).
    """
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
        return (f"The current user is {name or uid} ({uid})"
                + (f", member of module(s): {', '.join(mods)}" if mods else "")
                + (", and IS a manager." if mgr else ", not a manager."))
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


def _initial(thread_id, text, user_role, user_id) -> dict:
    from app.agent.tools import set_thread
    set_thread(thread_id)       # 쓰기 도구가 자기 대화를 안다(모델이 남의 thread 를 못 적게)
    return {"messages": [HumanMessage(content=text)], "thread_id": thread_id,
            "user_role": user_role or _detect_role(), "user_id": user_id or "",
            "user_identity": _identity(),
            # 새 턴이 시작되면 지난 턴의 승인·실행 결과는 지운다 — 안 지우면 옛 토큰으로
            # responder 가 다시 '승인 대기'로 흘러간다.
            "approval_token": "", "comment_token": "", "result": {}, "revisions": 0,
            # trace 는 리듀서 필드라 [] 대입으로는 안 비워진다 — 리셋 신호를 앞에 싣는다.
            "trace": [TRACE_RESET], "change_plan": {}, "questions": []}


def ask(text: str, thread_id: str = "", user_role: str = "", user_id: str = "") -> dict:
    """한 턴 굴린다. 승인이 필요한 지점에서 멈추면 `pending` 이 채워져 돌아온다."""
    tid = thread_id or new_thread()
    too_long = _guard(text)
    if too_long:
        return {"thread_id": tid, "ok": False, "reply": too_long, "error": too_long, "trace": []}
    log.info("[%s] Q: %s", tid, (text or "")[:500])
    meter = _usage.Meter()
    state = get_graph().invoke(_initial(tid, text, user_role, user_id), _config(tid, meter))
    out = _shape(tid, state)
    out["usage"] = meter.snapshot()
    log.info("[%s] A: %s", tid, (out.get("reply") or "")[:1000])
    log.info("[%s] 사용량: %s", tid, out["usage"])
    return out


def resume(thread_id: str, token: str, overrides: dict = None) -> dict:
    """사용자가 승인했다. 멈춰 있던 자리(Operator)에서 다시 굴린다.

    승인 표시는 여기서 한다 — 그래야 토큰이 **이 대화의 것**인지 확인할 수 있다.
    `overrides["assignees"]` 는 사용자가 승인 카드에서 고른 담당자({항목번호: uid}) —
    추천을 그대로 받는 게 아니라 후보 중 고르거나 직접 지정할 수 있어야 한다(사용자 요청).
    승인 전에 스테이징 내용과 State 를 **같이** 고쳐 지문을 다시 묶는다.
    """
    assignees = {str(k): str(v or "").strip()
                 for k, v in ((overrides or {}).get("assignees") or {}).items()}
    if assignees:
        # 실재 검증은 서버가 한다 — 화면 자동완성은 편의일 뿐 보증이 아니다.
        from app.agent.tools._ctx import client, settings
        from app.domain.search import search_users
        for uid in {u for u in assignees.values() if u}:
            try:
                found = search_users(client(), settings(), uid, 5) or []
            except Exception:
                found = []
            if not any(str(u.get("id") or "") == uid for u in found):
                return {"thread_id": thread_id, "ok": False,
                        "error": f"담당자 '{uid}' 를 찾을 수 없습니다. 사번(skcc.x1042 형식)을 확인하세요."}
        ok, why = approval.amend_assignees(token, thread_id, assignees)
        if not ok:
            return {"thread_id": thread_id, "ok": False, "error": why}
        # Operator 가 넘길 State 의 draft 에도 같은 값을 — 지문과 실행 인자가 일치해야 한다.
        try:
            vals = get_graph().get_state(_config(thread_id)).values or {}
            draft = dict(vals.get("draft") or {})
            items = [dict(it) for it in (draft.get("items") or [])]
            for i, uid in assignees.items():
                idx = int(i)
                if 0 <= idx < len(items):
                    if uid:
                        items[idx]["assignee"] = uid
                    else:
                        items[idx].pop("assignee", None)
            get_graph().update_state(_config(thread_id), {"draft": dict(draft, items=items)})
        except Exception as e:
            return {"thread_id": thread_id, "ok": False,
                    "error": f"담당자 변경을 반영하지 못했습니다: {str(e)[:120]}"}
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
    # None = 멈춘 자리(Operator 앞)에서 이어서
    state = get_graph().invoke(None, _config(thread_id, meter))
    out = _shape(thread_id, state)
    out["usage"] = meter.snapshot()
    log.info("[%s] 실행 결과: %s", thread_id, out.get("result"))
    return out


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


def _shape(thread_id: str, state: dict, snap=None) -> dict:
    """State → 화면이 쓰는 모양. **비밀도 원본 메시지도 싣지 않는다.**"""
    if snap is None:
        try:
            snap = get_graph().get_state(_config(thread_id))
        except Exception:
            snap = None
    waiting = bool(snap and Node.OPERATOR in (getattr(snap, "next", None) or ()))

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
        if plan.get("key"):
            out["pending"] = {"token": data["approval_token"], "action": "update_ticket",
                              "key": plan["key"], "changes": plan.get("changes") or {},
                              "comment": plan.get("comment") or "",
                              "rationale": plan.get("why") or ""}
        else:
            from app.agent.workflow.agents.refiner import as_bulk_items, child_items
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
    uids = set(_re.findall(r"\b(skcc\.[a-z]{1,2}[0-9]{2,6})\b", out.get("reply") or ""))
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
    yield {"type": "start", "thread_id": tid}
    try:
        for chunk in get_graph().stream(_initial(tid, text, user_role, user_id),
                                        _config(tid, meter),
                                        stream_mode="updates", subgraphs=True):
            for ev in _events(chunk):
                yield ev
    except Exception as e:
        log.exception("[%s] 그래프 실패", tid)
        yield {"type": "error", "message": str(e)[:300]}
    final = _shape(tid, dict((get_graph().get_state(_config(tid)).values or {})))
    final["usage"] = meter.snapshot()
    log.info("[%s] A(stream): %s", tid, (final.get("reply") or "")[:1000])
    log.info("[%s] 사용량: %s", tid, final["usage"])
    yield {"type": "final", **final}


def _events(chunk):
    """LangGraph 의 업데이트 청크 → 화면이 읽는 이벤트. 모양이 버전마다 조금씩 다르다."""
    payload = chunk[1] if isinstance(chunk, tuple) and len(chunk) == 2 else chunk
    if not isinstance(payload, dict):
        return
    from app.agent.workflow.state import Stage
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
    for node, patch in payload.items():
        if node == "think":
            # think 의 산출(AIMessage)에 다음에 부를 도구가 실려 있다 — 이름을 보여 준다.
            names = []
            for m in (patch or {}).get("messages") or []:
                for tc in (getattr(m, "tool_calls", None) or []):
                    names.append(_TOOL_KO.get(tc.get("name"), tc.get("name")))
            yield {"type": "step", "node": node,
                   "label": (" · ".join(names[:3])) if names else "다음 단계 판단 중"}
            continue
        if node == "act":
            yield {"type": "step", "node": node, "label": "도구 실행 결과 수신"}
            continue
        ev = {"type": "node", "node": node, "label": Stage.LABELS.get(node, node)}
        if isinstance(patch, dict) and patch.get("trace"):
            ev["note"] = (patch["trace"][-1] or {}).get("note", "")
        yield ev
