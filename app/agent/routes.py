"""agent/routes.py — `/api/agent/*`. 화면과 그래프 사이의 유일한 통로.

**라우터를 따로 둔 이유** — 나머지 라우트는 `main.py` 에 `@app.get` 으로 붙지만 여기는
`APIRouter` 다. 에이전트는 **선택 설치**라(`requirements-agent.txt`) langchain 이 없으면 라우트가
아예 붙지 않아야 하고, 그러려면 등록 자체가 조건부여야 한다. `main.py` 안에 두면
"import 는 됐는데 부르면 죽는" 라우트가 생긴다.

**스트리밍이 필요한 이유** — 조사에 십수 초가 걸린다. 그동안 빈 화면을 보여 주면 사용자는
멈춘 줄 안다. SSE 로 "지금 무엇을 하는 중"을 흘려보낸다.

**비밀은 한 방향으로만 흐른다.** 저장은 받고, 조회는 마스킹된 것만 준다. 화면이 키 원문을
다시 받을 일은 없다 — 받아야 할 이유가 없고, 받는 순간 새는 경로가 하나 늘어난다.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.agent import config as _cfg
from app.agent import secrets as _secrets

log = logging.getLogger("agent.api")
router = APIRouter(prefix="/api/agent")


class _ChatBody(BaseModel):
    text: str
    threadId: str = ""
    role: str = ""                 # 비우면 서버가 매니저 여부로 판별(테스트용 override 만 남김)
    userId: str = ""


class _ApproveBody(BaseModel):
    threadId: str
    token: str
    overrides: dict = None         # {"assignees": {"0": "skcc.x1042"}} — 카드에서 고른 담당자


class _SettingsBody(BaseModel):
    """설정 패널이 보내는 것. **모두 선택** — 준 것만 바꾼다(빈 문자열은 '지우기')."""
    provider: str = None
    chatModel: str = None
    chatModelSimple: str = None    # 간단한 역할(의도 분류·실행) 전용 모델. 빈 문자열 = 안 나눔
    embedModel: str = None
    apiVersion: str = None
    userPrompt: str = None         # 사용자별 시스템 프롬프트 추가분(로컬 prefs 저장)
    secrets: dict = None           # {"aoaiApiKey": "...", ...} — 저장만, 조회는 마스킹


# ── 상태 · 설정 ────────────────────────────────────────────────────
@router.get("/status")
def api_status():
    """설정 패널이 처음 여는 것. 비밀값 원문은 절대 싣지 않는다."""
    return JSONResponse(_cfg.status())


@router.put("/settings")
def api_settings(body: _SettingsBody):
    """설정 저장. 비밀 아닌 것은 prefs 로, 비밀은 별도 파일로 나눠 넣는다.

    저장 뒤 **그래프를 버린다** — provider 나 모델이 바뀌었는데 예전 LLM 을 쥔 그래프가 살아
    있으면 설정 화면과 실제 동작이 어긋난다. 대화 기록도 함께 사라지는 것이 맞다.
    """
    patch = {}
    for field, key in (("provider", "agentProvider"), ("apiVersion", "agentApiVersion"),
                       ("userPrompt", "agentUserPrompt")):
        v = getattr(body, field)
        if v is not None:
            patch[key] = v.strip()
    # 모델 이름은 provider 마다 자리가 다르다 — 지금 provider 의 자리에 넣는다.
    p = (body.provider or _cfg.provider()).strip().lower()
    slot = {"aoai": ("agentAoaiChat", "agentAoaiEmbed", "agentAoaiChatSimple"),
            "openai": ("agentOpenaiChat", "agentOpenaiEmbed", "agentOpenaiChatSimple"),
            "openai_compat": ("agentCompatChat", "agentCompatEmbed", "agentCompatChatSimple")}.get(p)
    if slot:
        if body.chatModel is not None:
            patch[slot[0]] = body.chatModel.strip()
        if body.embedModel is not None:
            patch[slot[1]] = body.embedModel.strip()
        if body.chatModelSimple is not None:
            patch[slot[2]] = body.chatModelSimple.strip()
    if patch:
        try:
            from app.infra import prefs
            prefs.save(patch)
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"설정 저장 실패: {str(e)[:200]}"},
                                status_code=500)

    if body.secrets:
        try:
            _secrets.save(body.secrets)
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"키 저장 실패: {str(e)[:200]}"},
                                status_code=500)

    _reset_runtime()
    return JSONResponse({"ok": True, **_cfg.status()})


@router.post("/probe")
def api_probe():
    """연결 테스트. chat·embeddings 를 실제로 한 번씩 부른다.

    실패를 삼키지 않는다 — 어디서 막혔는지(설정 누락 / 네트워크 / 인증)가 보여야 고칠 수 있다.
    """
    r = _cfg.probe()
    log.info("연결 테스트 provider=%s ok=%s", r.get("provider"), r.get("ok"))
    return JSONResponse(r)


@router.get("/models")
def api_models():
    """지금 provider 에서 쓸 수 있는 모델/배포 목록 — 설정 콤보박스 재료.

    실패해도 200 이다(빈 목록 + 사유). 목록은 참고이지 제약이 아니라서, 조회가 막힌 환경에서도
    화면은 자유 입력으로 계속 쓸 수 있어야 한다.
    """
    return JSONResponse(_cfg.list_models())


@router.get("/index")
def api_index_stats():
    """RAG 색인 현황. 규칙 문서가 몇 조각인지, 티켓을 몇 건 쌓았는지."""
    out = {}
    try:
        from app.agent.retrieval import static_index
        out["static"] = static_index.stats()
    except Exception as e:
        out["static"] = {"error": str(e)[:200]}
    try:
        from app.agent.retrieval import dynamic_index
        out["dynamic"] = dynamic_index.stats()
    except Exception as e:
        out["dynamic"] = {"error": str(e)[:200]}
    return JSONResponse(out)


@router.post("/index/reset")
def api_index_reset():
    """색인을 버린다. 임베딩 모델을 바꿨는데 이상한 결과가 나올 때의 탈출구."""
    from app.agent.retrieval import dynamic_index
    dynamic_index.reset()
    return JSONResponse({"ok": True})


# ── 대화 ───────────────────────────────────────────────────────────
@router.post("/chat")
def api_chat(body: _ChatBody):
    """한 턴. 승인이 필요하면 `pending` 이 실려 돌아온다(스트리밍이 필요 없는 호출용)."""
    from app.agent.workflow import session
    if not (body.text or "").strip():
        return JSONResponse({"ok": False, "error": "할 말을 입력하세요."}, status_code=400)
    return JSONResponse(session.ask(body.text, body.threadId, body.role, body.userId))


@router.post("/chat/stream")
def api_chat_stream(body: _ChatBody):
    """SSE. 조사에 십수 초가 걸리므로 진행 상황을 흘려보낸다."""
    from app.agent.workflow import session
    if not (body.text or "").strip():
        return JSONResponse({"ok": False, "error": "할 말을 입력하세요."}, status_code=400)

    def gen():
        for ev in session.stream(body.text, body.threadId, body.role, body.userId):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    # Nginx 등 중간 버퍼가 SSE 를 모아 두면 스트리밍의 의미가 없다 — 꺼 달라고 명시한다.
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class _StopBody(BaseModel):
    threadId: str = ""


@router.post("/chat/stop")
def api_chat_stop(body: _StopBody):
    """진행 중인 턴 중단. 화면의 ■ 버튼이 부른다 — 스트림만 끊으면 서버는 계속 일한다."""
    from app.agent.workflow import session
    return JSONResponse({"ok": session.request_stop(body.threadId)})


class _ComposeBody(BaseModel):
    ticketKey: str = ""
    kind: str = "comment"            # comment | description | transition
    prompt: str = ""
    seedHtml: str = ""               # 사용자가 쓰던 글(시드로 쓸지는 화면이 정한다)
    userId: str = ""


@router.post("/compose")
def api_compose(body: _ComposeBody):
    """에디터 자동완성 — 본문/코멘트 초안을 만들어 **에디터에 꽂을 HTML** 로 돌려준다.

    쓰기가 아니다(승인 토큰 없음). 저장은 사용자가 에디터에서 누른다.
    """
    from app.agent import compose as _compose
    r = _compose.compose(body.ticketKey, body.kind, body.prompt, body.seedHtml, body.userId)
    return JSONResponse(r, status_code=200 if r.get("ok") else 400)


@router.get("/snapshot/{thread_id}")
def api_snapshot(thread_id: str):
    """새로고침한 화면이 대화를 복원한다."""
    from app.agent.workflow import session
    return JSONResponse(session.snapshot(thread_id))


@router.post("/approve")
def api_approve(body: _ApproveBody):
    """사용자가 승인 카드를 눌렀다. 여기서부터 쓰기가 일어난다."""
    from app.agent.workflow import session
    return JSONResponse(session.resume(body.threadId, body.token, body.overrides))


@router.post("/cancel")
def api_cancel(body: _ApproveBody):
    from app.agent.workflow import session
    return JSONResponse(session.cancel(body.threadId, body.token))


def _reset_runtime():
    """설정이 바뀌면 LLM 을 쥐고 있는 것들을 전부 놓는다."""
    try:
        from app.agent.workflow import graph
        graph.reset()
    except Exception:
        pass
    try:
        from app.agent.retrieval import dynamic_index, static_index
        static_index._cached.update(sig=None, store=None)
        dynamic_index._cached.update(sig=None, store=None)
    except Exception:
        pass


def install(app) -> tuple[bool, str]:
    """`main.py` 가 부른다. **설치돼 있을 때만** 라우트가 붙는다.

    반환값을 화면이 볼 수 있게 돌려준다 — 왜 에이전트 탭이 없는지 사용자가 알아야 한다.
    """
    ok, why = _cfg.available()
    if not ok:
        log.info("에이전트 라우트 미등록 — %s", why)
        return False, why
    app.include_router(router)
    log.info("에이전트 라우트 등록 (provider=%s)", _cfg.provider())
    return True, ""
