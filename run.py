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


def _run_app_window(s, headless=False):
    """앱 창(Playwright Chromium)으로 실행. 창을 닫으면 서버 종료 후 반환."""
    url = f"http://localhost:{s.app_port}/"
    server = _serve_bg(s)
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=headless,
        ignore_default_args=["--enable-automation"],   # '자동화 제어 중' 안내바 제거
        args=["--no-first-run", "--no-default-browser-check"],
    )
    page = browser.new_page(no_viewport=True)          # 창 크기에 맞춰 렌더
    page.goto(url, wait_until="domcontentloaded")
    print(f"Lake Task Manager - {url}  (env={s.jira_env})  [이 창을 닫으면 종료됩니다]")
    # 창(페이지)이 닫힐 때까지 대기 — wait_for_timeout 이 이벤트를 펌프하고, 닫히면 예외.
    try:
        while browser.is_connected():
            page.wait_for_timeout(500)
    except Exception:
        pass
    try:
        p.stop()
    except Exception:
        pass
    server.should_exit = True


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
