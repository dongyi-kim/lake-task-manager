
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

import sys
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.auth.base import SessionExpired, UpstreamUnavailable
from app.infra.cache import Cache
from app.jira.jira_client import JiraClient
from app.infra.settings import STATIC_DIR, get_settings, load_people
from app.routes.dashboards import build_dashboard_router
from app.routes.mytasks import build_mytasks_router
from app.routes.resources import build_resource_router
from app.routes.ticket_commands import (
    FieldsBody as _FieldsBody,
    build_ticket_command_router,
    update_fields_response,
)
from app.routes.tickets import build_ticket_router


app = FastAPI(title="Lake Task Manager")

_settings = get_settings()
_cache = Cache(_settings.cache_db_path, dead_ttl=_settings.cache_dead_ttl_seconds)
_cache.purge()      # 시작 시 1회 — 지난 실행에서 쌓인 죽은 캐시 행·오래된 스냅샷 정리(디스크 안정화)
_client = JiraClient(_settings, _cache)
_client._wire_cache()      # 캐시에 재검증 규칙·회로차단기 연결(호출부는 그대로)

# 앱 창 모드(run.py)에서 SSO 로그인을 '같은 창'으로 처리하기 위한 in-process 신호.
#   run.py 가 _window_login=True 로 설정하고 _login_requested 를 폴링해 앱 창에서 로그인 구동.
_login_requested = threading.Event()
_window_login = False

# ── 앱 창 제어 브리지 (단일 인스턴스 실행) ──────────────────────────────────
# 창은 run.py 가 소유(Playwright)하지만, "이미 떠 있으면 포커스 / 없으면 새 창" 판정과
# 재실행 신호의 **단일 원천은 백엔드**다. run.py 가 open hook 을 등록하고 창 수를 보고하며,
# 창 스레드는 focus 요청을 자기 스레드에서 폴링해 bring_to_front 한다(Playwright 는 스레드 고정이라
# 다른 스레드에서 창을 못 만진다 — 그래서 이벤트만 넘기고 실제 조작은 창 루프가 한다).
_app_ctrl = {"open_hook": None, "restart_hook": None, "quit_hook": None, "hotkey_hook": None, "live": 0,
             "focus": threading.Event(), "lock": threading.Lock()}


def set_open_window_hook(fn):
    """run.py(_run_tray)가 '새 앱 창 열기' 동작을 등록."""
    _app_ctrl["open_hook"] = fn


def set_restart_hook(fn):
    """run.py(_run_tray)가 '업데이트 후 재시작' 동작을 등록(트레이 메뉴와 동일 경로)."""
    _app_ctrl["restart_hook"] = fn


def request_restart():
    """UI '업데이트' 버튼 → 트레이의 업데이트+재시작을 트리거. 반환 action: restart | none."""
    hook = _app_ctrl["restart_hook"]
    if not hook:
        return {"action": "none"}         # 트레이 모드가 아니면 스스로 재시작할 수 없다
    try:
        hook()
        return {"action": "restart"}
    except Exception:
        return {"action": "error"}


def set_quit_hook(fn):
    """run.py(_run_tray)가 '조용히 종료(재시작 없이)' 동작을 등록 — 트레이 [종료]와 동일 경로.
    새 인스턴스(run.bat)가 '옛 버전이 떠 있으니 너는 빠져라' 로 부를 때 쓴다."""
    _app_ctrl["quit_hook"] = fn


def set_hotkey_hook(fn):
    """run.py(_run_tray)가 '빠른 열기 단축키 재등록' 동작을 등록 — 설정에서 조합을 바꾸면 즉시 반영."""
    _app_ctrl["hotkey_hook"] = fn


def request_quit():
    """이 인스턴스를 **깨끗하게 종료**시킨다(재시작 없이). 새 인스턴스가 이어받는다.
    반환 action: quit | none. 실제 종료는 hook 이 별도 스레드에서 처리(응답을 먼저 보낸 뒤 종료)."""
    hook = _app_ctrl["quit_hook"]
    if not hook:
        return {"action": "none"}         # 트레이 모드가 아니면(창만) 스스로 못 끈다
    try:
        # 응답 먼저 돌려주고 잠깐 뒤 종료 — 호출자(새 run.py)가 200 을 받고 포트 해제를 기다린다.
        def _later():
            import time as _t
            _t.sleep(0.4)
            try:
                hook()
            except Exception:
                pass
        threading.Thread(target=_later, name="app-quit", daemon=True).start()
        return {"action": "quit"}
    except Exception:
        return {"action": "error"}


def note_window_opened():
    with _app_ctrl["lock"]:
        _app_ctrl["live"] += 1


def note_window_closed():
    with _app_ctrl["lock"]:
        _app_ctrl["live"] = max(0, _app_ctrl["live"] - 1)


def live_window_count():
    with _app_ctrl["lock"]:
        return _app_ctrl["live"]


def request_focus_or_open():
    """이미 열린 창이 있으면 포커스 요청, 없으면 새 창 hook 실행 — 재실행/트레이/엔드포인트 공용.
    반환 action: focus(기존 창 앞으로) | open(새 창) | none(창 미관리 모드) | error."""
    if live_window_count() > 0:
        _app_ctrl["focus"].set()               # 창 루프가 폴링해 앞으로 가져온다
        return {"action": "focus"}
    hook = _app_ctrl["open_hook"]
    if not hook:
        return {"action": "none"}               # plain 모드 등 — 창을 관리하지 않는다
    try:
        hook()
        return {"action": "open"}
    except Exception:
        return {"action": "error"}


def consume_focus_request():
    """창 루프가 **자기 스레드에서** 호출 — 포커스 요청이 있었으면 True(그리고 소비)."""
    if _app_ctrl["focus"].is_set():
        _app_ctrl["focus"].clear()
        return True
    return False


@app.post("/api/app/open")
def api_app_open():
    """다른 실행 인스턴스(런처)가 '창을 띄우거나 포커스' 요청 — 단일 인스턴스 동작의 진입점."""
    return request_focus_or_open()


# ── 업데이트 확인 (배포 repo 가 원격보다 뒤처졌나) ─────────────────────────
from app.infra.settings import APP_ROOT as _APP_ROOT        # noqa: E402
from app.infra.version import pinned_rev                    # noqa: E402
from app.infra.update_check import UpdateChecker            # noqa: E402

_updater = UpdateChecker(_APP_ROOT)
_updater.start()


def _start_auth_keepalive():
    """Confluence·Bitbucket 세션을 **미리** 살려 둔다 (prod 전용, 백그라운드).

    SSO 쿠키는 도메인별로 따로 만료된다 — Jira 는 멀쩡한데 Confluence 만 죽는 일이 흔하다.
    지금까지는 검색이 401 을 맞고 나서야 갱신을 시도했고, 그 갱신이 실패하면 스로틀 때문에
    한동안 재시도조차 안 해서 '검색을 켜면 Confluence 결과가 한참 안 나온다' 가 됐다.
    사람이 쓰기 전에 뒤에서 데워 두면 그 순간 자체가 사라진다.

    첫 확인은 조금 늦춘다 — 기동 직후엔 화면이 쓸 조회가 몰리는데, 거기에 인증 왕복을
    얹으면 첫 화면이 그만큼 늦어진다."""
    import threading

    def loop():
        import time as _t
        _t.sleep(20)                                   # 기동 직후는 양보
        while True:
            try:
                _client.keepalive_auth()
            except Exception:
                pass                                   # 유지 작업이 앱을 방해하면 안 된다
            _t.sleep(_client.KEEPALIVE_EVERY)

    threading.Thread(target=loop, name="auth-keepalive", daemon=True).start()


if _settings.jira_env == "prod":
    _start_auth_keepalive()


@app.get("/api/update")
def api_update():
    """배포 repo 의 업데이트 가능 여부. {available, behind, current, ok, checkedAt}. 즉답(캐시)."""
    return _updater.get()


@app.post("/api/app/update-restart")
def api_app_update_restart():
    """UI '업데이트' → 트레이의 '업데이트 후 재시작'을 실행(git pull + 재기동). 트레이 모드에서만."""
    return request_restart()


@app.get("/api/app/rev")
def api_app_rev():
    """실행 중인 이 인스턴스의 코드 커밋(짧은 해시). 새 run.bat 이 '떠 있는 게 최신인가' 판정에 쓴다."""
    return {"rev": _BUILD_REV}


class _RevealBody(BaseModel):
    path: str


@app.post("/api/app/reveal")
def api_app_reveal(body: _RevealBody):
    """받은 파일을 **탐색기에서** 보여 준다(그 파일이 선택된 채로 폴더가 열린다).

    앱 창(Chromium)에는 다운로드 표시줄이 없어 저장 뒤 경로만 알림으로 띄웠는데, 경로를 읽고
    직접 탐색기를 여는 건 결국 사용자 몫이었다. 알림에서 바로 열게 한다.

    **'다운로드' 폴더 안의 파일만 연다.** 임의 경로를 열어 주는 창구가 되면, 로컬에 떠 있는
    이 서버로 아무 파일이나 지목하는 요청이 들어올 수 있다. 우리가 파일을 떨구는 곳은 거기
    하나뿐이라 이 제한으로 잃는 기능이 없다."""
    import subprocess
    from pathlib import Path
    try:
        p = Path(body.path).resolve()
        root = (Path.home() / "Downloads")
        root = (root if root.is_dir() else Path.home()).resolve()
        if not p.is_file() or root not in p.parents:
            raise HTTPException(status_code=400, detail="다운로드 폴더 안의 파일만 열 수 있습니다.")
        if sys.platform == "win32":
            # /select, 뒤에는 공백 없이 경로가 붙어야 한다(explorer 의 오래된 인자 규칙).
            subprocess.Popen(["explorer", f"/select,{p}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@app.get("/api/app/assets")
def api_app_assets():
    """프론트 정적 자원(js/css) 전체 목록 — **자가복구 새로고침**(index.html 의 감시자)이 쓴다.

    Chrome 의 일반 새로고침은 **본문서만 재검증하고 서브리소스는 캐시 규칙대로** 쓴다(2017 reload
    최적화). 그래서 옛 JS 가 캐시에 박히면 `location.reload()` 로는 안 풀리고 Ctrl+Shift+R 만 들었다.
    감시자는 이 목록을 `fetch(cache:"reload")` 로 한 번 훑어 **캐시 항목 자체를 새것으로 갈아끼운 뒤**
    새로고침한다 — 그게 강제 새로고침과 같은 효과다."""
    if not STATIC_DIR.exists():
        return {"assets": []}
    out = []
    for p in sorted(STATIC_DIR.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".js", ".css"):
            out.append("/" + p.relative_to(STATIC_DIR).as_posix())
    return {"assets": out}


@app.post("/api/app/quit")
def api_app_quit():
    """이 인스턴스를 **재시작 없이** 깨끗하게 종료 — 새 인스턴스(run.bat)가 이어받을 때.
    응답(200)을 먼저 보내고 잠시 뒤 종료하므로, 호출자는 200 을 받고 포트 해제를 기다리면 된다."""
    return request_quit()


@app.exception_handler(SessionExpired)
def _on_session_expired(request: Request, exc: SessionExpired):
    """세션 없음(LoginRequired)·만료(SessionExpired) → 500 대신 401 + needLogin 플래그.

    여기까지 왔다는 건 **캐시로도 못 막았다**는 뜻이다(캐시가 있으면 낡은 값으로 답했다).

    ★ 하지만 **개별 요청의 401 을 세션 만료로 단정하지 않는다.** XSRF 거절·특정 엔드포인트
      권한·상류 순간 오류도 401 로 온다. 그걸 만료로 처리하면 프론트가 로그인을 걸고
      인증 창이 뜬다(사용자가 겪은 '자꾸 크로미움 창 뜨는' 그것이다).
      /myself 로 한 번 확인해서 **세션이 정말 죽었을 때만** needLogin 을 켠다.
      살아 있으면 이 요청만 실패시킨다 — 화면은 그대로다.
    """
    if _settings.jira_env == "prod" and _client.session_alive():
        return JSONResponse(
            status_code=502,
            content={"error": "요청이 거절되었습니다(세션은 정상) — " + str(exc)[:160]})
    # 세션이 정말 죽었다 — 다음 호출들이 죽은 세션에 붙어 수십 초를 버리지 않게 표시하고,
    # **/api/status 도 이 사실을 알게 한다**(예전엔 세션 파일만 보고 '인증됨' 이라 답해,
    # 프론트 감시자가 거짓 auth-ok 를 쏘고 감시를 멈췄다 → 로그인해도 화면이 안 살아났다).
    _client.mark_session_dead(str(exc)[:120])
    return JSONResponse(
        status_code=401,
        content={"needLogin": True, "env": _settings.jira_env, "detail": str(exc)})


@app.exception_handler(UpstreamUnavailable)
def _on_upstream_unavailable(request: Request, exc: UpstreamUnavailable):
    """망/Playwright transport 장애는 인증 만료와 분리한다.

    여기서 ``session_alive()`` 를 다시 부르면 이미 굳은 큐를 한 번 더 기다리게 된다. 회로만
    잠깐 열어 캐시로 버티게 하고 503으로 즉시 돌려, localhost UI와 트레이는 계속 살린다.
    """
    _client.mark_upstream_down(str(exc)[:160])
    return JSONResponse(
        status_code=503,
        content={"error": "Jira 연결이 응답하지 않습니다. 앱은 계속 실행 중이며 잠시 후 자동 재시도합니다.",
                 "retryable": True, "detail": str(exc)[:200]})


def _build_rev():
    """실행 중인 코드의 버전(릴리즈 태그 우선) — 최신 배포본인지 눈으로 확인하기 위함.
    (배포·재시작을 깜빡해 옛 코드가 도는 경우가 잦다.)
    판정 로직은 run.py 와 **같은 함수**를 써야 한다 — version.code_rev 의 주석 참고."""
    from app.infra.version import code_rev
    return code_rev() or "(unknown)"


_BUILD_REV = _build_rev()


# ── 개발자용 진단(dev tools) — 지금은 전부 열림. 노출 제어는 나중에 역할 훅(devtools.enabled)으로 ──
from app.infra import devtools as _devtools   # noqa: E402


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
        _require_manager()
        base = _BB
        return _probe_result(
            "GET /rest/api/1.0/repos",
            lambda: _client.provider.get_json(base + "/rest/api/1.0/repos",
                                              params={"name": name, "limit": limit}),
            kind="repo", full=full)

    @app.get("/api/dev/bitbucket/diag")
    def _dev_bb_diag():
        """XSRF 진단 — Bitbucket 도메인 쿠키 이름·전송 헤더. code search 403 원인 파악용."""
        _require_manager()
        prov = _client.provider
        if not hasattr(prov, "_diag_write"):
            return {"note": "이 provider(로컬 basic/mock)는 쿠키 진단 미지원 — prod SSO 에서만."}
        return {"rev": _BUILD_REV, "diag": prov._diag_write(_BB + "/rest/search/latest/search")}

    @app.get("/api/dev/bitbucket/code")
    def _dev_bb_code(q: str = "test", limit: int = 3, full: bool = False):
        _require_manager()
        base = _BB
        body = {"query": q, "entities": {"code": {"start": 0, "limit": limit}}}
        return _probe_result(
            "POST /rest/search/latest/search",
            lambda: _client.provider.post_json(base + "/rest/search/latest/search", body),
            kind="code", full=full)


if _devtools.enabled(_settings, "cache_admin"):
    @app.post("/api/dev/cache/clear")
    def _dev_cache_clear():
        """전체 캐시를 비운다 — 배포 뒤 출력 형태가 바뀌었는데 SWR 이 옛 결과를 계속 낼 때.
        (개별 티켓만 털려면 /api/ticket/{key}/refresh.)"""
        _require_manager()
        _cache.invalidate()          # prefix 없이 = 전부
        return {"ok": True, "cleared": "all"}


# 설정 메뉴(Dev Tools 화면)가 노출할 dev API 목록(경로 + 설명). 실제 라우트와 손으로 맞춘다.
#   param       — 프론트가 입력칸을 띄우고 {param} 자리에 넣어 호출한다.
#   body        — POST 로 보낼 기본 JSON(없으면 {}).
#   note/danger — 화면 경고 문구 / 붉은 카드.
# ★ 각 항목은 **그 기능을 켠 블록과 같은 게이트** 아래 둔다. 예전엔 이 목록과 /api/dev/tools 가
#   bitbucket_probe 블록 안에 있어서, 그 하나만 꺼도 Dev Tools 화면이 통째로 죽었다.
_DEV_ENDPOINTS = [
    {"path": "/api/dev/sso", "label": "SSO 인증 상태", "method": "GET"},
    {"path": "/api/ticket/{key}/refresh", "label": "티켓 캐시 새로고침 (children/siblings/…)",
     "method": "POST", "param": "key", "placeholder": "예: DL-1234"},
]
if _devtools.enabled(_settings, "cache_admin"):
    _DEV_ENDPOINTS.append(
        {"path": "/api/dev/cache/clear", "label": "전체 캐시 비우기 (배포 뒤 SWR 옛 결과 강제 제거)",
         "method": "POST", "danger": True})
if _devtools.enabled(_settings, "bitbucket_probe"):
    _DEV_ENDPOINTS += [
        {"path": "/api/dev/bitbucket/diag", "label": "Bitbucket XSRF 쿠키 진단", "method": "GET"},
        {"path": "/api/dev/bitbucket/repos?limit=3", "label": "Bitbucket 저장소 검색(구조)", "method": "GET"},
        {"path": "/api/dev/bitbucket/code?q=test", "label": "Bitbucket 코드 검색(구조)", "method": "GET"},
    ]
if _devtools.enabled(_settings, "pat_probe"):
    _DEV_ENDPOINTS += [
        {"path": "/api/dev/pat/{service}", "label": "PAT 사용 가능 확인 (목록 조회 — 아무것도 만들지 않음)",
         "method": "GET", "param": "service", "placeholder": "jira 또는 confluence"},
        {"path": "/api/dev/auth/capabilities/{service}",
         "label": "상류 인증 수단 조사 (PAT / OAuth 1.0a) — 서버앱 전환 갈림길",
         "method": "GET", "param": "service", "placeholder": "jira 또는 confluence"},
        {"path": "/api/dev/auth/bearer/{service}",
         "label": "Keycloak 토큰을 REST 가 받아 주는가 (쿠키 없이 Bearer 만)",
         "method": "POST", "param": "service", "placeholder": "jira 또는 confluence",
         "note": "먼저 아래 body 의 token 에 사내 Keycloak 액세스 토큰을 넣어야 합니다. 저장하지 않습니다.",
         "body": {"token": ""}},
        {"path": "/api/dev/pat/{service}", "label": "PAT 발급 시도 (1일짜리로 발급 후 즉시 회수)",
         "method": "POST", "param": "service", "placeholder": "jira 또는 confluence", "danger": True,
         "note": "토큰 값은 앞 6자만 보여 주고 저장하지 않습니다. 회수 실패 시 응답에 표시됩니다.",
         "body": {"name": "lake-task-manager-probe", "days": 1, "cleanup": True}},
    ]
# LLM 진단 — 에이전트가 설치돼 있을 때만(라우트 자체가 그때만 붙는다).
# ★ Dev Tools 목록에 올리는 이유: 403·404 가 났을 때 **어느 호출의 어느 모델 이름**인지
#   보여 주는 것이 유일한 실마리다. 설정 화면의 '연결 확인'은 되고/안 되고만 말한다.
# (이 목록은 파일 위쪽에서 만들어진다 — 에이전트 설치는 맨 아래에서 붙으므로 그 결과를
#  여기서 못 본다. import 가능 여부만 미리 본다. config 모듈 자체는 langchain 없이도 뜬다.)
try:
    from app.agent import config as _agent_cfg
    _agent_ok = bool(_agent_cfg.available()[0])
except Exception:
    _agent_ok = False
if _agent_ok:
    _DEV_ENDPOINTS.append(
        {"path": "/api/agent/diagnose", "method": "GET",
         "label": "LLM 연결 해부 (대상 URL · 모델 3종 · 각 호출의 원문 오류)",
         "note": "키는 끝 4자만 보입니다. 채팅·간단한 역할·임베딩을 각각 한 번씩 부릅니다."})
_DEV_ENDPOINTS.append({"path": "/api/dev/tools", "label": "dev tools 목록", "method": "GET"})


@app.get("/api/dev/tools")
def _dev_tools_list():
    _require_manager()
    return {"enabled": sorted(_settings.dev_tools),
            "available": _devtools.DEV_TOOLS,
            "endpoints": _DEV_ENDPOINTS,
            "bitbucket_base": (_settings.bitbucket_base or "").rstrip("/") or "(미설정)"}


def _probe_service(svc):
    """서비스 하나의 인증 상태 — provider 세션으로 실제 호출. {service, base, authenticated, configured, detail}."""
    from app.auth.sso_session import _extract_user
    name, base, paths = svc["name"], svc["base"], svc["paths"]
    if not svc.get("configured"):
        return {"service": name, "base": "", "authenticated": False,
                "configured": False, "detail": "미설정 (config 의 base 필요)"}
    ok, detail = False, ""
    for path in paths:
        try:
            body = _client.provider.get_json(base.rstrip("/") + path, quiet=True)
            who = _extract_user(body)             # 인증 필요 엔드포인트가 200 이면 인증됨
            ok, detail = True, (f"{path} → {who}" if who else f"{path} → 200(인증됨)")
            break
        except Exception as e:
            detail = f"{path}: {getattr(e, 'status', '')} {type(e).__name__} {str(e)[:80]}".strip()
    return {"service": name, "base": base, "authenticated": ok, "configured": True, "detail": detail}


if _devtools.enabled(_settings, "pat_probe"):
    # ── 개인 액세스 토큰(PAT) 확인 ────────────────────────────────────────────
    # 지금 운영 인증은 **SSO 세션 재사용**이라 만료가 짧고(수 시간~하루) 무인 자동화가 안 된다.
    # PAT 가 열려 있으면 `PatAuthProvider` 하나만 더해 무인으로 승격할 수 있는데(AGENTS.md §11),
    # 사내 인스턴스에서 그 메뉴가 열려 있는지 **확인이 안 된 상태**다. 여기서 직접 찔러 본다.
    #
    # Jira/Confluence DC 8.14+ 는 같은 경로를 쓴다(경로 후보는 아래 _PAT_PROBES).
    #   GET    → 내 토큰 목록(읽기만 — 부작용 없음)
    #   POST   → 발급  { name, expirationDuration(일) }
    #   DELETE → 회수  /{tokenId}

    def _pat_base(service):
        for svc in getattr(_settings, "services", []):
            if svc["name"].lower() == (service or "").lower():
                if not svc.get("configured"):
                    raise HTTPException(status_code=400, detail=f"{svc['name']} base 가 config 에 없습니다.")
                return svc["name"], svc["base"].rstrip("/")
        raise HTTPException(status_code=404, detail=f"'{service}' 서비스를 모릅니다.")

    # 확인용 후보 경로 — **한 번에 다 찔러 본다.** 한 경로만 보고 404 를 받으면 "PAT 가 없다" 와
    # "우리가 엉뚱한 데를 쳤다"(context path·세션 만료·구버전 별칭)를 구분할 수 없다.
    #   sanity  — 이 base·세션으로 REST 가 되긴 하는지(여기서 실패면 PAT 얘기를 할 단계가 아니다)
    #   rest    — PAT REST 자원. latest 가 정석이고 1.0 은 옛 별칭
    #   ui      — 사람이 쓰는 PAT 화면. REST 가 404 인데 이건 200 이면 기능은 있고 REST 만 막힌 것
    _PAT_PROBES = {
        "jira": {"sanity": "/rest/api/2/myself",
                 "rest": ["/rest/pat/latest/tokens", "/rest/pat/1.0/tokens"],
                 "ui": ["/secure/ViewPersonalAccessTokens.jspa"]},
        "confluence": {"sanity": "/rest/api/user/current",
                       "rest": ["/rest/pat/latest/tokens", "/rest/pat/1.0/tokens"],
                       "ui": ["/plugins/personalaccesstokens/usertokens.action"]},
    }

    def _pat_try(url):
        """한 경로의 결과를 **상태코드 + 응답이 무엇이었는지**로 돌려준다.
        Jira 의 'Oops, You've found a dead link' 는 **HTML 404** 라 자원이 아예 없다는 뜻이고,
        JSON 404/403 은 자원은 있는데 막혔다는 뜻이다 — 대응이 달라 반드시 갈라야 한다."""
        try:
            body = _client.provider.get_json(url, quiet=True)
        except Exception as e:
            raw = (getattr(e, "body", "") or "")
            head = raw.lstrip()[:200]
            html = head.startswith("<") or "<html" in head.lower()
            out = {"status": getattr(e, "status", None), "ok": False,
                   "body": "HTML(오류 페이지)" if html else head[:160]}
            if html and ("dead link" in raw.lower() or "oops" in raw.lower()):
                out["meaning"] = "자원 없음 — Jira/Confluence 의 404 안내 페이지가 왔습니다(REST 자원 미등록)."
            elif out["status"] in (401, 403):
                out["meaning"] = "인증·권한에서 막힘(세션 또는 관리자 정책)."
            return out
        return {"status": 200, "ok": True,
                "body": type(body).__name__ + (f"[{len(body)}]" if isinstance(body, list) else "")}

    @app.get("/api/dev/pat/{service}")
    def _dev_pat_check(service: str):
        """PAT 를 **쓸 수 있는가**를 여러 경로로 한 번에 본다 — 전부 GET 이라 아무것도 만들지 않는다.

        하나라도 200 이면 supported. 전부 404 인데 sanity 가 200 이면 세션·주소는 멀쩡하고
        **그 인스턴스에 PAT 자원이 없는 것**이다(관리자가 껐거나 기능이 안 깔림)."""
        _require_manager()
        name, base = _pat_base(service)
        spec = _PAT_PROBES.get(name.lower())
        if not spec:
            raise HTTPException(status_code=400, detail=f"{name} 은 PAT 확인 대상이 아닙니다(jira·confluence).")

        sanity = _pat_try(base + spec["sanity"])
        rest = {p: _pat_try(base + p) for p in spec["rest"]}
        ui = {p: _pat_try(base + p) for p in spec["ui"]}

        live = next((p for p, r in rest.items() if r["ok"]), None)
        out = {"service": name, "base": base,
               "supported": bool(live), "restPath": live,
               "sanity": sanity, "rest": rest, "ui": ui}

        if live:
            body = _client.provider.get_json(base + live, quiet=True)
            items = body if isinstance(body, list) else (body or {}).get("values") or []
            # 토큰 이름·만료만. 토큰 값은 발급 순간에만 존재하고 목록엔 애초에 안 나온다.
            out["count"] = len(items)
            out["tokens"] = [{"id": t.get("id"), "name": t.get("name"),
                              "expiringAt": t.get("expiringAt"), "lastAccess": t.get("lastAccess")}
                             for t in items[:20]]
            out["verdict"] = f"PAT REST 사용 가능 ({live}). 무인 자동화(PatAuthProvider) 로 승격할 수 있습니다."
        elif not sanity["ok"]:
            out["verdict"] = ("이 base·세션으로는 REST 자체가 안 됩니다 — PAT 이전에 로그인/주소부터 "
                              "확인하세요(SSO 인증 상태 카드).")
        elif any(r["ok"] for r in ui.values()):
            out["verdict"] = ("PAT **화면은 있는데 REST 는 막혀 있습니다.** 화면에서 손으로 발급받아 쓰는 건 "
                              "되지만, 앱이 REST 로 발급하지는 못합니다.")
        else:
            out["verdict"] = ("이 인스턴스에 PAT 가 열려 있지 않습니다(REST·화면 모두 없음). "
                              "관리자가 껐거나 기능이 설치돼 있지 않습니다 — 승격하려면 관리자 요청이 필요합니다.")
        return out

    # ── 상류 인증 수단 조사 (서버앱 전환의 갈림길) ────────────────────────────────────
    # PAT 가 꺼져 있는 것이 확인된 뒤, 남은 후보는 둘이다.
    #   ① OAuth 1.0a (Application Link) — **사용자별** 자격. Jira 권한이 그대로 보존된다.
    #   ② 서비스 계정(basic) — 단순하지만 **모든 사용자가 한 계정 권한**으로 보게 된다.
    # 그리고 검증해야 할 가설이 하나 더 있다: 사내 Keycloak 토큰을 Jira REST 가 받아 주는가.
    # 받아 준다면 서버앱 전환이 통째로 쉬워지고, 안 받아 주면 ①/② 로 간다.

    _OAUTH_PATHS = ["/rest/applinks/1.0/manifest",          # applinks 플러그인 생존 확인(비관리자 OK)
                    "/plugins/servlet/oauth/request-token", # OAuth 1.0a 서블릿 존재
                    "/plugins/servlet/oauth/authorize"]

    @app.get("/api/dev/auth/capabilities/{service}")
    def _dev_auth_caps(service: str):
        """이 인스턴스가 **어떤 상류 인증을 받아 주는가**. 전부 GET — 아무것도 만들지 않는다."""
        _require_manager()
        name, base = _pat_base(service)
        pat = {p: _pat_try(base + p) for p in (_PAT_PROBES.get(name.lower(), {}).get("rest") or [])}
        oauth = {p: _pat_try(base + p) for p in _OAUTH_PATHS}
        out = {"service": name, "base": base,
               "pat": pat, "oauth1a": oauth,
               "patUsable": any(r["ok"] for r in pat.values()),
               "oauth1aLikely": any(r["ok"] or r.get("status") in (400, 401, 405)
                                    for r in oauth.values())}
        # 405/400/401 도 신호다 — '자원은 있는데 이 방식으로 부르면 안 된다' 는 뜻이라
        # 서블릿이 살아 있다는 증거다. 404(HTML)만이 '없다' 이다.
        if out["patUsable"]:
            out["verdict"] = "PAT 가 열려 있습니다 — PatAuthProvider 로 바로 승격 가능."
        elif out["oauth1aLikely"]:
            out["verdict"] = ("OAuth 1.0a 흔적이 있습니다 — 관리자에게 Application Link(incoming) "
                              "생성을 요청하면 **사용자별 자격**으로 서버앱 전환이 가능합니다.")
        else:
            out["verdict"] = ("PAT·OAuth 1.0a 둘 다 안 보입니다 — 서비스 계정(basic) 말고는 길이 없고, "
                              "그건 Jira 권한이 평평해지므로 보안 승인 사안입니다.")
        return out

    class _BearerBody(BaseModel):
        token: str = ""

    @app.post("/api/dev/auth/bearer/{service}")
    def _dev_auth_bearer(service: str, body: _BearerBody):
        """**사내 Keycloak 토큰을 Jira/Confluence REST 가 받아 주는가** — 이 하나가 갈림길이다.

        ★ 쿠키 없이 **맨 요청**으로 보낸다. SSO 세션이 실린 provider 로 보내면 성공했을 때
          그게 토큰 덕인지 쿠키 덕인지 알 수 없어, 실험의 의미가 사라진다.
        ★ 토큰은 **저장하지도 로그에 남기지도 않는다.** 응답에도 앞 6자만 돌려준다."""
        _require_manager()
        name, base = _pat_base(service)
        tok = (body.token or "").strip()
        if not tok:
            raise HTTPException(status_code=400, detail="token 이 비어 있습니다.")
        probe = (_PAT_PROBES.get(name.lower(), {}).get("sanity")) or "/rest/api/2/myself"
        import requests
        out = {"service": name, "path": probe, "tokenPrefix": tok[:6] + "…",
               "note": "쿠키 없이 Authorization 헤더만으로 보냈습니다. 토큰은 저장하지 않습니다."}
        try:
            r = requests.get(base + probe, timeout=20, allow_redirects=False,
                             headers={"Authorization": "Bearer " + tok,
                                      "Accept": "application/json"})
            out["status"] = r.status_code
            ct = (r.headers.get("content-type") or "")
            out["contentType"] = ct.split(";")[0]
            who = ""
            if "json" in ct:
                try:
                    j = r.json()
                    who = j.get("name") or j.get("key") or j.get("username") or ""
                except Exception:
                    who = ""
            out["user"] = who
            # 로그인 페이지로 302 하면 '거절' 이다(200 이 아니어도 헷갈리지 않게 밝힌다).
            out["accepted"] = bool(r.status_code == 200 and who)
            out["verdict"] = ("받아 줍니다 — 이 토큰으로 사용자별 REST 호출이 됩니다(서버앱 전환의 최단 경로)."
                              if out["accepted"] else
                              "거절했습니다 — Jira DC 는 REST 에서 IdP 토큰을 받지 않는 게 보통입니다. "
                              "OAuth 1.0a 나 서비스 계정으로 가야 합니다.")
        except Exception as e:
            out["status"] = None
            out["verdict"] = "요청 자체가 실패했습니다: %s %s" % (type(e).__name__, str(e)[:160])
        return out

    class _PatBody(BaseModel):
        name: str = "lake-task-manager-probe"
        days: int = 1                 # 시험 발급은 짧게 — 오래 사는 토큰을 실수로 남기지 않는다
        cleanup: bool = True          # 발급 직후 회수(기본) — 확인이 목적이지 사용이 아니다

    @app.post("/api/dev/pat/{service}")
    def _dev_pat_issue(service: str, body: _PatBody):
        """실제로 **발급해 본다.** 기본은 발급 직후 회수(cleanup) — 확인이 목적이라 흔적을 안 남긴다.

        ★ 토큰 값은 **발급 응답에만 한 번** 나오고 다시는 못 본다. 그래서 저장하지 않고,
          돌려줄 때도 앞 6자만 보낸다 — 화면·로그·캐시 어디에도 온전한 토큰이 남으면 안 된다.
          정말 쓰려면 cleanup=false 로 발급한 뒤 Jira 화면에서 직접 받아 쓰는 게 맞다."""
        _require_manager()
        name, base = _pat_base(service)
        spec = _PAT_PROBES.get(name.lower())
        if not spec:
            raise HTTPException(status_code=400, detail=f"{name} 은 PAT 확인 대상이 아닙니다(jira·confluence).")

        # **먼저 어느 경로가 살아 있는지 본다.** 고정 경로로 바로 POST 하면 404 하나만 돌아와
        # '기능이 없는 것'과 '경로가 다른 것'을 구분할 수 없다(실제로 그렇게 헷갈렸다).
        path = next((p for p in spec["rest"] if _pat_try(base + p)["ok"]), None)
        if not path:
            probe = _dev_pat_check(service)
            return {"service": name, "issued": False, "status": 404,
                    "detail": "PAT REST 자원이 없어 발급을 시도하지 않았습니다.",
                    "verdict": probe.get("verdict"), "probe": probe}
        try:
            r = _client.provider.post_json(
                base + path,
                {"name": body.name, "expirationDuration": max(1, int(body.days))}) or {}
        except Exception as e:
            return {"service": name, "issued": False, "path": path,
                    "status": getattr(e, "status", None),
                    "detail": f"{type(e).__name__} {str(e)[:200]}"}

        tok, tid = r.get("rawToken") or "", r.get("id")
        out = {"service": name, "issued": True, "id": tid, "name": r.get("name"),
               "expiringAt": r.get("expiringAt"),
               "tokenPrefix": (tok[:6] + "…") if tok else "",
               "note": "토큰 값은 발급 시 한 번만 나옵니다. 여기서는 앞 6자만 보여 주고 저장하지 않습니다."}
        if body.cleanup and tid:
            try:
                _client.provider.delete(base + path + "/" + str(tid))
                out["cleaned"] = True
            except Exception as e:
                # 못 지웠으면 **반드시 알린다** — 모르는 채로 살아 있는 토큰이 남는다.
                out["cleaned"] = False
                out["cleanupError"] = f"{type(e).__name__} {str(e)[:120]}"
        return out


if _devtools.enabled(_settings, "sso_status"):
    @app.get("/api/dev/sso")
    def _dev_sso_status():
        """각 서비스 인증 상태(전체). 개별 실시간 표시는 /api/dev/sso/{service} 를 병렬 호출.

        ★ 매니저 게이트를 걸지 말 것 — 인증 상태·로그인은 역할과 무관하다(누구나 로그인해야
          한다). 걸면 첫 실행에서 고리가 닫힌다: 세션 없음 → 매니저 판정 불가 → 403 →
          설정창엔 '오류' 만 뜨고 무엇을 해야 하는지 알 수 없다."""
        return {"targets": [_probe_service(svc) for svc in getattr(_settings, "services", [])],
                "note": "authenticated=false 인 서비스는 그 도메인 SSO 로그인이 안 된 것."}

    @app.get("/api/dev/sso/{service}")
    def _dev_sso_one(service: str):
        """서비스 하나만 인증 확인 — 설정창이 서비스별로 병렬 호출해 각각 실시간 렌더한다.
        (위와 같은 이유로 매니저 게이트 없음.)"""
        for svc in getattr(_settings, "services", []):
            if svc["name"].lower() == service.lower():
                return _probe_service(svc)
        return JSONResponse({"error": "unknown service", "service": service}, status_code=404)


def _session_user():
    """세션 사용자. 못 읽으면 {} — current_user() 는 예외를 삼키고 {} 를 준다."""
    try:
        return _client.current_user() or {}
    except Exception:
        return {}


def _is_manager(me=None):
    """세션 사용자가 매니저인가. 매니저 전용 화면(WBS·워크로드)의 단일 판정 지점.

    ★ '세션을 아직 못 읽음' 과 '매니저가 아님' 은 **다른 상태**다. 둘을 같이 취급하면
      prod 첫 실행처럼 세션이 아직 없는 동안 403 이 나고, 사용자는 로그인도 못 한 채
      "매니저 전용 화면입니다" 만 본다 — 권한 문제가 아니라 인증 문제인데.
      current_user() 는 실패를 예외가 아니라 **빈 dict** 로 알려주므로 그것으로 판단한다.
      모르면 막지 않는다: 정말 권한이 없으면 뒤의 데이터 호출이 401/403 을 내 로그인으로 이어진다.
    """
    from app.infra.settings import is_manager as _im
    if me is None:
        me = _session_user()
    if not (me or {}).get("id") and not (me or {}).get("name"):
        return True          # 세션 미확인 — 판정 불가이므로 막지 않는다
    return _im(_settings, me)


def _require_manager():
    """매니저 전용 API 게이트. 프론트에서 탭을 숨기지만 주소를 직접 치면 그만이라,
    데이터를 주는 쪽에서도 막는다(숨김은 접근 제어가 아니다)."""
    if not _is_manager():
        raise HTTPException(status_code=403, detail="매니저 전용 화면입니다.")


# ── 워크로드 스코프 ──────────────────────────────────────────────────────────
# 인력 워크로드는 매니저 전용이었지만, **비매니저도 '자기 모듈'은 볼 수 있게** 연다.
# 매니저 = 전체 모듈. 비매니저 = 자기가 속한 모듈만(사번→모듈 매핑). 게이트는 데이터 쪽에서 건다
# (프론트 숨김은 접근 제어가 아니다).
def _my_modules_or_all():
    """(restricted, module_set). 매니저면 (False, None)=제한 없음. 비매니저면 (True, {내 모듈…})."""
    me = _session_user()
    if _is_manager(me):
        return False, None
    try:
        from app.infra.settings import modules_of
        return True, set(modules_of(me.get("id") or me.get("name") or ""))
    except Exception:
        return True, set()


def _scoped_people():
    """워크로드용 people(모듈→인력) — 비매니저는 자기 모듈만, 매니저는 전체.
    빌더가 모듈 목록을 people 에서 뽑으므로(workload_modules) 여기서 거르면 출력이 그대로 좁아진다."""
    people = load_people()
    restricted, mods = _my_modules_or_all()
    if not restricted:
        return people
    return {m: v for m, v in people.items() if m in mods}


def _require_module_access(module):
    """비매니저가 자기 모듈이 아닌 모듈을 직접 조회하는 것을 막는다."""
    restricted, mods = _my_modules_or_all()
    if restricted and module not in mods:
        raise HTTPException(status_code=403, detail="이 모듈은 조회 권한이 없습니다.")


def _require_person_access(user):
    """비매니저가 자기 모듈 밖 인력의 상세를 직접 조회하는 것을 막는다(모듈 인력에 한함)."""
    restricted, mods = _my_modules_or_all()
    if not restricted:
        return
    u = (user or "").strip().lower()
    people = load_people()
    for m in mods:
        if any((pid or "").strip().lower() == u for pid in people.get(m, [])):
            return
    raise HTTPException(status_code=403, detail="이 인력은 조회 권한이 없습니다.")


@app.get("/api/health")
def health():
    # ★ health 는 프로세스/localhost 생존 확인이다. Jira·SSO 를 절대 타지 않는다.
    # 런처·브라우저 부팅·새로고침이 모두 이 경로를 쓰므로, 여기서 /myself 를 기다리면 상류 장애가
    # 곧 앱 전체 장애로 보인다. 실제 세션 실패는 데이터 요청과 비동기 /api/status 재확인이 판정한다.
    need = _client.needs_login()
    st = _client.upstream_state()
    return {"status": "ok", "env": _settings.jira_env, "projectKey": _settings.project_key,
            "needLogin": need, "rev": _BUILD_REV,
            # 이 사본이 특정 버전에 **묶여 있으면** 화면이 그렇게 말해야 한다. 안 그러면
            # "왜 업데이트가 안 되냐" 를 아무도 설명할 수 없다(옛 배포가 남긴 SHA 핀이 그랬다).
            "pinned": pinned_rev(_APP_ROOT),
            # 앱 URL(localhost/browse/KEY)을 붙여넣었을 때 실 Jira 주소로 바꾸기 위해 프론트에 노출
            "jiraBase": (_settings.jira_base or "").rstrip("/"),
            # 인증이 안 돼도 캐시가 있으면 화면은 띄운다 — 프론트가 이걸 보고 판단한다.
            "hasCache": st["hasCache"], "lastSyncAt": st["lastSyncAt"],
            "devTools": sorted(_settings.dev_tools)}


_ONLINE_CACHE = {"at": 0.0, "ok": False}


def _probe_online(timeout=1.2):
    """Jira 호스트에 **TCP 로 닿기만** 하는지. 인증을 타지 않으므로 싸고 빠르다.

    '오프라인' 과 '온라인인데 미인증' 은 사용자가 할 일이 다르다 — 전자는 기다리는 것 말곤
    없고, 후자는 로그인을 끝내면 된다. 화면이 둘을 같은 말로 뭉뚱그리면 안 된다.
    결과는 몇 초 캐시한다(상단 알림이 주기적으로 물어보므로).
    """
    import socket
    import time as _t
    from urllib.parse import urlparse
    if _t.time() - _ONLINE_CACHE["at"] < 5:
        return _ONLINE_CACHE["ok"]
    u = urlparse(_settings.jira_base or "")
    host, port = u.hostname, (u.port or (443 if u.scheme == "https" else 80))
    ok = False
    if host:
        try:
            with socket.create_connection((host, port), timeout):
                ok = True
        except Exception:
            ok = False
    _ONLINE_CACHE.update(at=_t.time(), ok=ok)
    return ok


@app.get("/api/status")
def api_status():
    """화면 상단 알림용 경량 상태 — **Jira 를 타지 않는다**(그래야 자주 물어도 된다).
    오프라인/인증 중인지, 지금 보고 있는 데이터가 언제 기준인지."""
    # 죽은 것으로 표시된 세션은 여기서 **뒤에서** 한 번씩 되살아났는지 본다. 이 함수는
    # 기다리지 않는다(스레드로 던진다) — 이 엔드포인트는 즉답이 규칙이다.
    # 이게 있어야 런처 창·다른 인스턴스로 로그인한 경우에도 화면이 스스로 살아난다.
    _client.session_recheck_async()
    st = _client.upstream_state()
    st["env"] = _settings.jira_env
    need_login = _client.needs_login()
    down = bool(st["down"])
    # mode — 화면 상단 알림이 이 하나로 갈린다.
    #   ok             정상
    #   offline        망이 안 닿는다 → 기다리는 수밖에 없다
    #   authenticating 망은 닿는데 세션이 없다 → 로그인이 진행 중이다
    if not need_login and not down:
        st["mode"] = "ok"
    else:
        online = _probe_online()
        if not online:
            st["mode"] = "offline"
        elif need_login:
            st["mode"] = "authenticating"
        else:
            st["mode"] = "degraded"       # 망은 닿지만 Jira/Playwright 응답이 멎음
    st["needLogin"] = need_login
    return JSONResponse(st)


class _PrefsBody(BaseModel):
    bitbucketEnabled: bool | None = None
    quickOpenHotkey: str | None = None


def _prefs_payload():
    return {"bitbucketEnabled": bool(_settings.bitbucket_enabled),
            "bitbucketConfigured": bool(_settings.bitbucket_base),
            "quickOpenHotkey": _settings.quick_open_hotkey,
            # 에이전트가 안 붙었으면 화면이 그 사실과 **이유**를 보여야 한다.
            # "버튼이 없다"만으로는 사용자가 설치가 빠진 건지 고장인지 알 수 없다.
            "agentEnabled": bool(globals().get("_AGENT_ON")),
            "agentReason": globals().get("_AGENT_WHY") or ""}


@app.get("/api/prefs")
def api_prefs_get():
    """사람이 화면에서 켜고 끄는 설정 — Bitbucket 연동 여부, 빠른 열기 단축키."""
    return JSONResponse(_prefs_payload())


@app.put("/api/prefs")
def api_prefs_put(body: _PrefsBody):
    if body.bitbucketEnabled is not None:
        _settings.set_bitbucket_enabled(body.bitbucketEnabled)
    if body.quickOpenHotkey is not None:
        _settings.set_quick_open_hotkey(body.quickOpenHotkey)
        hook = _app_ctrl.get("hotkey_hook")          # run.py 가 있으면 즉시 재등록(데스크톱 앱)
        if hook:
            try:
                hook(_settings.quick_open_hotkey)
            except Exception:
                pass
    return JSONResponse(_prefs_payload())


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


def _may_edit(key: str) -> bool:
    """이 티켓을 바꿀 수 있는가 — **내 티켓(담당/보고)이거나 매니저**.
    남의 티켓 상태를 아무나 바꾸면 그건 협업이 아니라 사고다. 매니저는 조율 책임이 있어 연다."""
    if _is_manager():
        return True
    me = (_session_user().get("id") or "").lower()
    if not me:
        return False                      # 세션을 모르면 남의 티켓일 수 있다 → 열지 않는다
    b = _client.ticket_badge(key) or {}
    return me in {(b.get("assigneeId") or "").lower(), (b.get("reporterId") or "").lower()}


def _require_edit(key: str):
    if not _may_edit(key):
        raise HTTPException(status_code=403,
                            detail="담당자·보고자 또는 매니저만 바꿀 수 있습니다.")


class _OpenBody(BaseModel):
    url: str = ""


@app.post("/api/open")
def api_open(body: _OpenBody):
    """URL 을 **기본 브라우저**로 연다.

    앱 창은 Playwright 가 띄운 Chromium 이라 window.open 으로 열면 그 자동화 창 안에 또 하나가
    뜬다 — 탭·즐겨찾기·확장이 없는 창이라 '새 창에서 열기' 의 목적(평소 쓰는 브라우저에서 보기)에
    맞지 않는다. 서버가 로컬에서 도니 OS 기본 브라우저를 직접 띄우는 게 맞다.

    ★ 아무 URL 이나 열지 않는다 — 앱 자신과 설정된 Jira/Confluence 만. 로컬 서버가 임의 URL 을
      여는 통로가 되면, 화면에 뜬 남의 링크 하나로 무엇이든 실행시킬 수 있게 된다.
    """
    url = (body.url or "").strip()
    allowed = [f"http://127.0.0.1:{_settings.app_port}", f"http://localhost:{_settings.app_port}"]
    for base in (_settings.jira_base, _settings.confluence_base):
        if base:
            allowed.append(base.rstrip("/"))
    if not any(url.startswith(a) for a in allowed):
        return JSONResponse({"ok": False, "error": "허용되지 않은 주소입니다."}, status_code=400)
    import webbrowser
    ok = webbrowser.open(url)
    return JSONResponse({"ok": bool(ok)})


def api_update_fields(key: str, body: _FieldsBody):
    return update_fields_response(_client, _settings, key, body)


@app.get("/api/me")
def api_me():
    """세션 사용자 — 본인 댓글(수정/삭제) 판정 + 매니저 여부 + 모듈(내 Task 필터용)."""
    me = dict(_session_user())
    me["known"] = bool(me.get("id") or me.get("name"))   # 세션을 읽었는가
    me["manager"] = _is_manager(me)
    # 모듈 목록 — Task 화면의 '모듈' 필터용. 캐시된 디렉토리에서(매 요청 config 안 읽음).
    try:
        from app.infra.settings import module_dir, modules_of
        _dir = module_dir()
        me["modules"] = modules_of(me.get("id") or me.get("name") or "")
        me["allModules"] = _dir["modules"]
        # 모듈 → 인력(사번) 매핑 — Task 화면이 **네트워크 없이** '이 티켓이 이 모듈 소속인가' 를
        # 판정(수정 후 퀵필터 이탈 즉시 반영)하는 데 쓴다. 팀 규모라 작다(디렉토리 캐시에서).
        me["moduleUsers"] = _dir.get("people") or {}
    except Exception:
        me["modules"] = []
        me["allModules"] = []
        me["moduleUsers"] = {}
    # jira.yml search 에 등록된 Jira 프로젝트 — Task 화면 Project 필터의 **기본 체크 대상**.
    # 여기 없는 프로젝트의 티켓은 기본 언체크(노이즈 감춤). 사용자가 콤보에서 켤 수 있다.
    me["searchProjects"] = list(getattr(_client.s, "search_jira_projects", []) or [])
    return JSONResponse(me)


app.include_router(build_resource_router(get_client=lambda: _client, cache=_cache, settings=_settings))
app.include_router(build_mytasks_router(get_client=lambda: _client))
app.include_router(build_ticket_router(get_client=lambda: _client))
app.include_router(build_ticket_command_router(
    get_client=lambda: _client,
    settings=_settings,
    may_edit=_may_edit,
    require_edit=_require_edit,
    session_user=_session_user,
))
app.include_router(build_dashboard_router(
    client=_client,
    cache=_cache,
    settings=_settings,
    scoped_people=_scoped_people,
    require_manager=_require_manager,
    require_module_access=_require_module_access,
    require_person_access=_require_person_access,
))


# 티켓 단독 페이지 — Jira 와 같은 /browse/{key} URL. SPA 진입점을 그대로 돌려주고
# 어떤 티켓인지는 프론트가 경로에서 읽는다(서버 렌더링 없음).
# 정적 마운트("/") 보다 **먼저** 선언해야 마운트에 먹히지 않는다.
@app.get("/browse/{key}")
def browse_ticket(key: str):
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="frontend not built")
    return FileResponse(str(index), headers={"Cache-Control": "no-cache"})


class _RevalidatedStatic(StaticFiles):
    """정적 파일에 `Cache-Control: no-cache` — 브라우저가 **매번 ETag 로 재검증**(안 바뀌면 304).

    Cache-Control 이 없으면 브라우저는 휴리스틱 캐시(Last-Modified 기반 추정 TTL)로 재검증 없이
    옛 JS/CSS 를 계속 써서, 배포(핀 이동) 후에도 강제 새로고침(Ctrl+Shift+R) 전까지 옛 UI 가 남았다.
    로컬 서버라 재검증(304) 비용은 무시 가능 — 업데이트 후 **첫 실행부터** 새 UI 가 뜬다."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


# ── AI 에이전트 (선택 설치) ────────────────────────────────────────────────
# 에이전트 의존이 import 될 때만 `/api/agent/*` 가 붙는다. 대시보드만 쓰는
# 사용자에게 langchain·faiss 200MB+ 를 강요하지 않는다(devtools 게이팅과 같은 방식).
# ★ 정적 마운트보다 **먼저** — 아래 mount("/") 가 모든 경로를 먹는다.
try:
    from app.agent.routes import install as _install_agent
    _AGENT_ON, _AGENT_WHY = _install_agent(app)
except Exception as _e:                    # import 자체가 깨져도 대시보드는 떠야 한다
    _AGENT_ON, _AGENT_WHY = False, f"에이전트를 불러오지 못했습니다: {str(_e)[:200]}"


# 정적 프론트 (마지막에 마운트 — /api 라우트가 우선)
if STATIC_DIR.exists():
    app.mount("/", _RevalidatedStatic(directory=str(STATIC_DIR), html=True), name="static")
