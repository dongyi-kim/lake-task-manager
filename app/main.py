"""
Lake Task Manager — FastAPI 진입점.
- /api/wbs            : 기능1 WBS Dashboard (wbs_config.yaml + Jira Epic 진척률, 캐시)
- /api/vit            : 기능2 PMO_VIT 현안 (모듈별 그룹 + 직계 하위 티켓 + 자손 트리/코멘트 lazy)
- /api/workload       : 기능3 인력 워크로드 (모듈별 인력 진행중/최근7일 완료 수)
- /api/workload/{user}: 기능3 인력 상세 (진행중/최근7일 완료 티켓 리스트 — 프론트 [+] 확장)
- /api/activity/{user}: 인력 최근 Jira/Confluence 활동 요약 (캐시, 현재 UI 미사용/보존)
- /api/health         : 헬스체크
- /api/refresh        : 캐시 무효화(수동 갱신)
- /                   : 정적 프론트 (app/static)

JIRA_ENV=mock 이면 Jira 없이 결정적 데이터로 전체가 구동된다.
"""

import threading

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import rollup, vit, workload
from .auth.base import SessionExpired
from .cache import Cache
from .jira_client import JiraClient
from .settings import STATIC_DIR, get_settings, load_plan, load_people

app = FastAPI(title="Lake Task Manager")

_settings = get_settings()
_cache = Cache(_settings.cache_db_path)
_client = JiraClient(_settings, _cache)

# 앱 창 모드(run.py)에서 SSO 로그인을 '같은 창'으로 처리하기 위한 in-process 신호.
#   run.py 가 _window_login=True 로 설정하고 _login_requested 를 폴링해 앱 창에서 로그인 구동.
_login_requested = threading.Event()
_window_login = False


@app.exception_handler(SessionExpired)
def _on_session_expired(request: Request, exc: SessionExpired):
    """세션 없음(LoginRequired)·만료(SessionExpired) → 500 대신 401 + needLogin 플래그.
    프론트가 로그인 오버레이를 띄운다."""
    return JSONResponse(
        status_code=401,
        content={"needLogin": True, "env": _settings.jira_env, "detail": str(exc)})


@app.get("/api/health")
def health():
    return {"status": "ok", "env": _settings.jira_env, "projectKey": _settings.project_key,
            "needLogin": _client.needs_login()}


@app.post("/api/login")
def api_login():
    """[prod] SSO 로그인.
    - 앱 창 모드(run.py): 같은 창에서 로그인하도록 신호만 보내고 즉시 반환(pending).
    - 그 외(폴백): 별도 Chromium 창을 띄워 로그인 폴링(login_wait).
    mock/local 은 로그인 불필요 → 즉시 ok."""
    if _settings.jira_env != "prod":
        return {"ok": True, "note": f"env={_settings.jira_env}: 로그인 불필요"}
    if _window_login:
        _login_requested.set()                       # run.py 가 앱 창에서 구동
        return {"ok": True, "pending": True}
    ok = _client.login()
    return JSONResponse({"ok": ok}, status_code=200 if ok else 504)


@app.get("/api/wbs")
def api_wbs():
    plan = load_plan()
    epic_prog = _client.epic_progress_map(plan)
    data = rollup.build(plan, epic_prog)
    # 주의: Epic→Task→Sub-Task 트리는 여기서 미리 안 긁는다(lazy).
    #       프론트가 Epic 을 펼칠 때만 GET /api/epic/{key}/tree 로 가져간다.
    # 진척 스냅샷 기록 (기능2/3 시계열 뒷받침)
    _cache.add_snapshot("pmo", plan.get("project_key", "LAKE"), data["rollup"]["pmo"])
    return JSONResponse(data)


@app.get("/api/epic/{epic_key}/tree")
def api_epic_tree(epic_key: str):
    """WBS Gantt 지연 로딩 — Epic 을 펼칠 때 그 Epic 의 자식(Task/Story/Bug) + Sub-Task 트리만 반환."""
    return JSONResponse(_client.epic_tree(epic_key))


@app.get("/api/epic/{epic_key}/progress")
def api_epic_progress(epic_key: str):
    """단일 Epic 진척률 리소스 (doneSp/totalSp/mockSp/progressPct/name)."""
    return JSONResponse(_client.epic_progress_one(epic_key))


@app.get("/api/issue/{key}")
def api_issue(key: str):
    """범용 단일 티켓 리소스 — 요약·타입·상태·일정·SP + Sub-Task."""
    node = _client.issue_detail(key)
    if node is None:
        return JSONResponse({"error": "Issue Does Not Exist", "key": key}, status_code=404)
    return JSONResponse(node)


@app.get("/api/issue/{key}/comments")
def api_issue_comments(key: str):
    """범용 단일 티켓 코멘트 리소스."""
    return JSONResponse(_client.issue_comments(key))


@app.get("/api/img")
def api_img(u: str):
    """이미지 프록시 — 인증(SSO) 세션으로 사내 Jira/CDN 이미지를 받아 same-origin 으로 반환.
    (localhost 페이지가 크로스오리진 이미지를 직접 못 불러오는 문제 해결. 허용 호스트만.)"""
    data, ctype = _client.fetch_media(u)
    if data is None:
        return JSONResponse({"error": "이미지 없음 또는 허용되지 않은 호스트", "u": u}, status_code=404)
    media = (ctype or "application/octet-stream").split(";")[0].strip()
    return Response(content=data, media_type=media,
                    headers={"Cache-Control": "private, max-age=300"})


@app.get("/api/ticket/{key}")
def api_ticket(key: str):
    """티켓 상세 다이얼로그 — 요약·상태·담당/보고·일정·라벨·컴포넌트 + 정화된 description(HTML)."""
    view = _client.ticket_view(key)
    if view is None:
        return JSONResponse({"error": "Issue Does Not Exist", "key": key}, status_code=404)
    return JSONResponse(view)


@app.get("/api/vit/{key}")
def api_vit_detail(key: str):
    """단일 현안 상세 — 자손 트리 + 코멘트 (프론트 [자세히] 지연 로딩)."""
    plan = load_plan()
    return JSONResponse(vit.vit_detail(_client, plan, load_people(), key))


@app.get("/api/vit")
def api_vit():
    plan = load_plan()
    return JSONResponse(vit.build_vit(_client, plan, load_people(), jira_base=_settings.jira_base))


@app.get("/api/workload")
def api_workload():
    plan = load_plan()
    return JSONResponse(workload.build_workload(_client, plan, load_people(), jira_base=_settings.jira_base))


@app.get("/api/workload/{user}")
def api_workload_tickets(user: str):
    """인력 상세 — 진행중 / 최근7일 완료 티켓 리스트 (프론트 [+] 확장)."""
    return JSONResponse(_client.workload_tickets(user))


@app.get("/api/activity/{user}")
def api_activity(user: str):
    return JSONResponse(_client.activity(user))


@app.post("/api/refresh")
def api_refresh():
    _cache.invalidate()          # 전체 캐시 무효화 (epic/workload/activity)
    return {"status": "refreshed"}


# 정적 프론트 (마지막에 마운트 — /api 라우트가 우선)
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
