"""
Lake Task Manager — 로컬 실행 런처.

용도:
  1) 개발/일반:   python run.py               (uvicorn 기동 + 브라우저 자동 오픈)
  2) 단일 exe:    PyInstaller 진입점 (lake.spec). 최종 사용자는 exe 더블클릭만.
  3) prod SSO:    python run.py login  /  lake-task-manager.exe login
                  → 설치된 Chrome 으로 사내 SSO 1회 로그인 후 세션 저장. 이후 일반 실행.

설정은 exe(또는 repo) 옆의 .env, 매핑은 config/*.yaml.
"""

import sys
import threading
import time
import webbrowser

import uvicorn

from app.settings import APP_ROOT, get_settings


def _open_browser(url, delay=1.5):
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _sso_login(s):
    from app.auth.sso_session import login
    login(s.jira_base, s.jira_state_path)


def main():
    s = get_settings()

    # prod SSO 1회 로그인:  run.py login  /  exe login
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        _sso_login(s)
        return

    # prod 인데 세션 파일이 없으면 안내(로그인 먼저)
    if s.jira_env == "prod":
        state = APP_ROOT / s.jira_state_path
        if not state.exists():
            print(f"[prod] SSO 세션이 없습니다({state}). 먼저 로그인:  <실행파일> login")

    url = f"http://localhost:{s.app_port}/"
    print(f"Lake Task Manager — {url}  (env={s.jira_env})")
    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    uvicorn.run("app.main:app", host=s.app_host, port=s.app_port, log_level="info")


if __name__ == "__main__":
    main()
