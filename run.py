"""
Lake Task Manager — 로컬 실행 런처.

용도:
  1) 개발/일반:   python run.py               (uvicorn + 앱 창 자동 오픈, 창 닫으면 종료)
  2) prod SSO:    python run.py login         (사내 SSO 1회 로그인 후 세션 저장)

앱 창 모드(기본): uvicorn 을 백그라운드 스레드로 띄우고, Playwright Chromium 을 '앱 창'으로
열어 http://localhost:PORT 를 보여준다. **그 창을 닫으면 서버·프로세스·런처(run.bat)가 함께 종료**
→ 강제종료 불필요. (playwright 미설치 또는 LAKE_NO_WINDOW=1 이면 기본 브라우저+수동종료로 폴백)

설정은 exe(또는 repo) 옆 config/jira.yml, 매핑은 config/*.yaml.
"""

import os
import sys
import threading
import time

import uvicorn

from app.settings import APP_ROOT, get_settings


def _sso_login(s):
    from app.auth.sso_session import login
    login(s.jira_base, s.jira_state_path)


def _serve_bg(s):
    """uvicorn 을 백그라운드(데몬) 스레드로 기동하고, 준비될 때까지 대기 후 server 반환."""
    config = uvicorn.Config("app.main:app", host=s.app_host, port=s.app_port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if getattr(server, "started", False):
            break
        time.sleep(0.1)
    return server


def _do_login_in_window(s, page, context, appmain, timeout=300):
    """[prod] 같은 앱 창에서 사내 SSO 로그인 구동: Jira 로 이동 → 인증 감지 → 세션 저장 → 앱 복귀."""
    from app.auth.sso_session import _authed
    appmain._login_requested.clear()
    base = s.jira_base.rstrip("/")
    home = f"http://localhost:{s.app_port}/"
    try:
        page.goto(base, wait_until="domcontentloaded")
        deadline = time.monotonic() + timeout
        ok = False
        while time.monotonic() < deadline and not page.is_closed():
            if _authed(context, base):
                context.storage_state(path=appmain._client._state_path())   # 세션 저장
                appmain._client.reset_provider()                            # 백엔드가 새 세션 사용
                ok = True
                break
            page.wait_for_timeout(1500)
        print("[login] " + ("완료 — 세션 저장" if ok else "미완료(시간초과/취소)"))
    except Exception:
        pass
    finally:
        try:
            if not page.is_closed():
                page.goto(home, wait_until="domcontentloaded")             # 앱으로 복귀
        except Exception:
            pass


def _run_app_window(s, headless=False):
    """앱 창(Playwright Chromium, --app 모드=주소창 없는 앱 창)으로 실행. 로그인도 같은 창에서.
    창 닫으면 서버 종료 후 반환. 프로필은 임시폴더(정리)라 실행 폴더(cwd)에 아무것도 안 남긴다."""
    import shutil
    import tempfile
    url = f"http://localhost:{s.app_port}/"
    server = _serve_bg(s)
    import app.main as appmain
    appmain._window_login = True                       # /api/login 이 이 창에서 로그인하도록
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    udd = tempfile.mkdtemp(prefix="ltm-appwin-")       # 앱 창 전용 임시 프로필 (cwd 오염 방지)
    context = p.chromium.launch_persistent_context(
        user_data_dir=udd,
        headless=headless,
        ignore_default_args=["--enable-automation"],   # '자동화 제어 중' 안내바 제거
        args=["--no-first-run", "--no-default-browser-check", f"--app={url}"],   # 앱 모드
    )
    page = context.pages[0] if context.pages else context.wait_for_event("page")
    try:
        page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    print(f"Lake Task Manager - {url}  (env={s.jira_env})  [이 창을 닫으면 종료됩니다]")
    # 창이 닫힐 때까지 대기. 로그인 요청(_login_requested)이 오면 같은 창에서 SSO 구동.
    try:
        while not page.is_closed():
            if appmain._login_requested.is_set():
                _do_login_in_window(s, page, context, appmain)
            page.wait_for_timeout(500)
    except Exception:
        pass
    try:
        context.close()
    except Exception:
        pass
    try:
        p.stop()
    except Exception:
        pass
    server.should_exit = True
    shutil.rmtree(udd, ignore_errors=True)             # 임시 프로필 정리


def _run_plain(s):
    """폴백: 기본 브라우저로 열고 서버는 블로킹 실행(종료는 Ctrl+C 또는 창 닫기)."""
    import webbrowser
    url = f"http://localhost:{s.app_port}/"
    print(f"Lake Task Manager - {url}  (env={s.jira_env})  [종료: Ctrl+C]")
    threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    uvicorn.run("app.main:app", host=s.app_host, port=s.app_port, log_level="info")


def main():
    # Windows 콘솔(cp949)에서도 유니코드 출력이 크래시하지 않도록 utf-8 로 강제.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    s = get_settings()

    # prod SSO 1회 로그인:  python run.py login
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        _sso_login(s)
        return

    if s.jira_env == "prod":
        state = APP_ROOT / s.jira_state_path
        if not state.exists():
            print(f"[prod] SSO 세션이 없습니다({state}). 앱 화면의 'SSO 로그인' 버튼을 누르세요.")

    # 앱 창 모드 (playwright 있고, 끄지 않았으면). 창 닫히면 전체 종료.
    use_window = os.getenv("LAKE_NO_WINDOW") not in ("1", "true", "True")
    if use_window:
        try:
            import playwright.sync_api  # noqa: F401
        except Exception:
            use_window = False
    if use_window:
        _run_app_window(s)
        os._exit(0)          # 남은 스레드까지 확실히 정리하고 즉시 종료(=run.bat 도 함께 끝)
    else:
        _run_plain(s)


if __name__ == "__main__":
    main()
