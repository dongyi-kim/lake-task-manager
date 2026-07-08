"""
Lake Task Manager — FastAPI 진입점.
- /api/wbs            : 기능1 WBS Dashboard (plan.yaml + Jira Epic 진척률, 캐시)
- /api/vit            : 기능2 PMO_VIT 현안 (Component별 그룹 + 자손 트리/소식/코멘트)
- /api/workload       : 기능3 인력 워크로드 (모듈별 인력 진행중/최근7일 완료 수)
- /api/activity/{user}: 기능3 인력 최근 Jira/Confluence 활동 요약 (캐시)
- /api/health         : 헬스체크
- /api/refresh        : 캐시 무효화(수동 갱신)
- /                   : 정적 프론트 (app/static)

JIRA_ENV=mock 이면 Jira 없이 결정적 데이터로 전체가 구동된다.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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
    """[prod] 설치된 Chrome 을 띄워 SSO 로그인(폴링 감지) 후 세션 저장.
    mock/local 은 로그인 불필요 → 즉시 ok."""
    if _settings.jira_env != "prod":
        return {"ok": True, "note": f"env={_settings.jira_env}: 로그인 불필요"}
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
