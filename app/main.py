
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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import rollup, search, vit, workload
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


def _build_rev():
    """실행 중인 코드의 git 커밋 — 앱이 최신 배포본인지 눈으로 확인하기 위함.
    (배포 pull·재시작을 깜빡해 옛 코드가 도는 경우가 잦다.)"""
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=3).decode().strip()
    except Exception:
        return "(unknown)"


_BUILD_REV = _build_rev()


# ── 개발자용 진단(dev tools) — 지금은 전부 열림. 노출 제어는 나중에 역할 훅(devtools.enabled)으로 ──
from app import devtools as _devtools   # noqa: E402


def _probe_result(label, fn, kind=None, full=False):
    """fn() 상류 호출 결과에서 **필요한 필드만**(값 마스킹) 추려 돌려준다.
    kind 가 있으면 digest(결과배열 위치 + 첫 항목 핵심 구조)만. full=1 이면 전체 스키마도."""
    try:
        raw = fn()
    except Exception as e:
        err = {"label": label, "rev": _BUILD_REV, "error": str(e)}
        st = getattr(e, "status", None)
        if st is not None:
            err["status"] = st
            err["body_preview"] = getattr(e, "body", "")
            if st == 403:
                err["hint"] = ("403 — XSRF 또는 권한. 헤더는 붙였으니, 앱을 최신으로 재시작했는지 확인. "
                               "그래도 403 이면 이 경로에 code search 권한이 없거나 엔드포인트가 다를 수 있음.")
        return err
    out = {"label": label, "rev": _BUILD_REV}
    if kind:
        out["digest"] = _devtools.bitbucket_digest(raw, kind)
    if full or not kind:
        out["schema"] = _devtools.schema_of(raw)
    return out


if _devtools.enabled(_settings, "bitbucket_probe"):
    # 사내 Bitbucket 실제 응답 형태를 확인(값 마스킹). code/repo 검색 mock 을 실물에 맞추기 위한 일회성.
    _BB = (_settings.bitbucket_base or "").rstrip("/")

    @app.get("/api/dev/bitbucket/repos")
    def _dev_bb_repos(name: str = "", limit: int = 3, full: bool = False):
        base = _BB
        return _probe_result(
            "GET /rest/api/1.0/repos",
            lambda: _client.provider.get_json(base + "/rest/api/1.0/repos",
                                              params={"name": name, "limit": limit}),
            kind="repo", full=full)

    @app.get("/api/dev/bitbucket/diag")
    def _dev_bb_diag():
        """XSRF 진단 — Bitbucket 도메인 쿠키 이름·전송 헤더. code search 403 원인 파악용."""
        prov = _client.provider
        if not hasattr(prov, "_diag_write"):
            return {"note": "이 provider(로컬 basic/mock)는 쿠키 진단 미지원 — prod SSO 에서만."}
        return {"rev": _BUILD_REV, "diag": prov._diag_write(_BB + "/rest/search/latest/search")}

    @app.get("/api/dev/bitbucket/code")
    def _dev_bb_code(q: str = "test", limit: int = 3, full: bool = False):
        base = _BB
        body = {"query": q, "entities": {"code": {"start": 0, "limit": limit}}}
        return _probe_result(
            "POST /rest/search/latest/search",
            lambda: _client.provider.post_json(base + "/rest/search/latest/search", body),
            kind="code", full=full)

    # 설정 메뉴가 노출할 dev API 목록(경로 + 설명). 실제 라우트와 손으로 맞춘다.
    _DEV_ENDPOINTS = [
        {"path": "/api/dev/sso", "label": "SSO 인증 상태", "method": "GET"},
        {"path": "/api/dev/bitbucket/diag", "label": "Bitbucket XSRF 쿠키 진단", "method": "GET"},
        {"path": "/api/dev/bitbucket/repos?limit=3", "label": "Bitbucket 저장소 검색(구조)", "method": "GET"},
        {"path": "/api/dev/bitbucket/code?q=test", "label": "Bitbucket 코드 검색(구조)", "method": "GET"},
        {"path": "/api/dev/tools", "label": "dev tools 목록", "method": "GET"},
    ]

    @app.get("/api/dev/tools")
    def _dev_tools_list():
        return {"enabled": sorted(_settings.dev_tools),
                "available": _devtools.DEV_TOOLS,
                "endpoints": _DEV_ENDPOINTS,
                "bitbucket_base": _BB or "(미설정)"}


if _devtools.enabled(_settings, "sso_status"):
    @app.get("/api/dev/sso")
    def _dev_sso_status():
        """각 서비스 인증 상태 — 지금 provider 세션으로 실제 호출해 확인.
        로그인 순회 후 Confluence 만 X 로 뜨면 그 도메인 SSO 가 안 붙은 것."""
        rows = []
        from app.auth.sso_session import _extract_user
        # 설정 여부와 무관하게 **세 서비스 모두** 표시(미설정이면 그 사실을 알린다).
        for svc in getattr(_settings, "services", []):
            name, base, paths = svc["name"], svc["base"], svc["paths"]
            if not svc.get("configured"):
                rows.append({"service": name, "base": "", "authenticated": False,
                             "configured": False, "detail": "미설정 (config 의 base 필요)"})
                continue
            ok, detail = False, ""
            for path in paths:
                try:
                    body = _client.provider.get_json(base.rstrip("/") + path)
                    who = _extract_user(body)     # 인증 필요 엔드포인트가 200 이면 인증됨
                    ok = True
                    detail = f"{path} → {who}" if who else f"{path} → 200(인증됨)"
                    break
                except Exception as e:
                    detail = f"{path}: {getattr(e, 'status', '')} {str(e)[:80]}".strip()
            rows.append({"service": name, "base": base, "authenticated": ok,
                         "configured": True, "detail": detail})
        return {"targets": rows,
                "note": "authenticated=false 인 서비스는 그 도메인 SSO 로그인이 안 된 것. "
                        "상단 [SSO 로그인] 재실행 시 그 창에서 해당 서비스에 직접 로그인."}


@app.get("/api/health")
def health():
    return {"status": "ok", "env": _settings.jira_env, "projectKey": _settings.project_key,
            "needLogin": _client.needs_login(), "rev": _BUILD_REV,
            "devTools": sorted(_settings.dev_tools)}


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


@app.get("/api/search")
def api_search(q: str = "", scope: str = "scoped", limit: int = 8):
    """통합 검색 — Jira(JQL text~) + Confluence(CQL) + Bitbucket(mock) 병렬. scope=scoped|all."""
    return JSONResponse(search.search_all(_client, _settings, q, scope, limit))


@app.get("/api/img")
def api_img(u: str):
    """이미지 프록시 — 인증(SSO) 세션으로 사내 Jira/CDN 이미지를 받아 same-origin 으로 반환.
    (localhost 페이지가 크로스오리진 이미지를 직접 못 불러오는 문제 해결. 허용 호스트만.)"""
    data, ctype = _client.fetch_media(u)
    if data is None:
        return JSONResponse({"error": "이미지 없음 또는 허용되지 않은 호스트", "u": u}, status_code=404)
    media = (ctype or "application/octet-stream").split(";")[0].strip()
    # 첨부는 id 기준 불변 → 길게 캐시(확대 시 재요청 없이 브라우저 캐시 히트).
    return Response(content=data, media_type=media,
                    headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/ticket/{key}")
def api_ticket(key: str):
    """티켓 상세 다이얼로그 — 요약·상태·담당/보고·일정·라벨·컴포넌트 + 정화된 description(HTML)."""
    view = _client.ticket_view(key)
    if view is None:
        return JSONResponse({"error": "Issue Does Not Exist", "key": key}, status_code=404)
    return JSONResponse(view)


@app.get("/api/ticket/{key}/badge")
def api_ticket_badge(key: str):
    """티켓 인라인 뱃지 — 요약/타입/상태/담당자 (description/comment 내 Jira 링크 뱃지용, 경량)."""
    b = _client.ticket_badge(key)
    if b is None:
        return JSONResponse({"error": "Issue Does Not Exist", "key": key}, status_code=404)
    return JSONResponse(b)


@app.get("/api/avatar/{user}")
def api_avatar(user: str):
    """사용자 프로필 이미지 — 인증 세션으로 받아 same-origin 반환. 없으면 404(프론트가 기본 아이콘).
    아바타는 거의 안 바뀌므로 브라우저 캐시를 아주 길게(30일, immutable) 준다."""
    data, ctype = _client.user_avatar(user)
    if data is None:
        return JSONResponse({"error": "no avatar", "user": user}, status_code=404)
    media = (ctype or "image/png").split(";")[0].strip()
    return Response(content=data, media_type=media,
                    headers={"Cache-Control": "private, max-age=2592000, immutable"})


@app.get("/api/ticket/{key}/ancestors")
def api_ticket_ancestors(key: str):
    """계보 스파인 — 조상 체인(epic·parent) + 각 조상 진척률. 티켓단위 캐시 재사용."""
    return JSONResponse(_client.ticket_ancestors(key))


@app.get("/api/ticket/{key}/timeline")
def api_ticket_timeline(key: str):
    """티켓 타임라인 — 생성/상태/담당자/해결/댓글 등 중요 이력만(설명 수정 등 잡음 제외)."""
    return JSONResponse(_client.ticket_timeline(key))


@app.get("/api/ticket/{key}/attachments")
def api_ticket_attachments(key: str):
    """첨부파일 목록(이미지는 프록시 URL 포함)."""
    return JSONResponse(_client.ticket_attachments(key))


@app.get("/api/ticket/{key}/documents")
def api_ticket_documents(key: str):
    """관련 문서 — 설명·코멘트에서 언급된 Confluence 문서."""
    return JSONResponse(_client.ticket_documents(key))


@app.get("/api/ticket/{key}/children")
def api_ticket_children(key: str):
    """하위 Task — 직계 자식(Epic→자식 / 그 외→Sub-Task)."""
    return JSONResponse(_client.ticket_children(key))


@app.get("/api/ticket/{key}/related")
def api_ticket_related(key: str):
    """관련 Task — 이슈 링크(relates to 등) + 설명·코멘트에서 언급된 티켓."""
    return JSONResponse(_client.ticket_related(key))


@app.get("/api/ticket/{key}/siblings")
def api_ticket_siblings(key: str):
    """계보 스파인 — 형제 티켓(같은 부모/Epic). 현재 티켓 포함(current=true)."""
    return JSONResponse(_client.ticket_siblings(key))


@app.get("/api/vit/shell")
def api_vit_shell():
    """현안 골격 — 모듈 목록·모듈별 건수(트리 조립 없음). 프론트가 뼈대를 먼저 그린다."""
    plan = load_plan()
    return JSONResponse(vit.build_vit_shell(_client, plan, load_people(), jira_base=_settings.jira_base))


@app.get("/api/vit/module/{module}")
def api_vit_module(module: str):
    """현안 — 모듈 하나만. 프론트가 모듈별로 병렬 호출해 도착하는 대로 렌더한다."""
    plan = load_plan()
    return JSONResponse(vit.build_vit_module(_client, plan, load_people(), module))


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


@app.get("/api/workload/shell")
def api_workload_shell():
    """워크로드 골격 — 모듈·인원 수만(Jira 조회 없음)."""
    plan = load_plan()
    return JSONResponse(workload.build_workload_shell(_client, plan, load_people(),
                                                      jira_base=_settings.jira_base))


@app.get("/api/workload/module/{module}")
def api_workload_module(module: str):
    """워크로드 — 모듈 하나만(모듈별 병렬 호출용)."""
    plan = load_plan()
    return JSONResponse(workload.build_workload_module(_client, plan, load_people(), module))


@app.get("/api/workload/{user}/{bucket}")
def api_workload_bucket(user: str, bucket: str):
    """인력 상세의 한 버킷(open|inProgress|done7d) — 세 리스트를 각각 병렬 로딩."""
    rows = _client.workload_bucket(user, bucket)
    if rows is None:
        return JSONResponse({"error": "unknown bucket", "bucket": bucket}, status_code=404)
    return JSONResponse(rows)


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


# 티켓 단독 페이지 — Jira 와 같은 /browse/{key} URL. SPA 진입점을 그대로 돌려주고
# 어떤 티켓인지는 프론트가 경로에서 읽는다(서버 렌더링 없음).
# 정적 마운트("/") 보다 **먼저** 선언해야 마운트에 먹히지 않는다.
@app.get("/browse/{key}")
def browse_ticket(key: str):
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="frontend not built")
    return FileResponse(str(index))


# 정적 프론트 (마지막에 마운트 — /api 라우트가 우선)
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
