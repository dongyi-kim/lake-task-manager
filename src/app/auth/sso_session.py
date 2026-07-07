"""
운영 인증 — Playwright storage_state(SSO 세션) 재사용. (../../jira_test.py 승격)

핵심: **설치된 Chrome 재사용** (`channel="chrome"`) → exe 에 Chromium(~150MB) 을 번들하지 않는다.
      Playwright 파이썬 라이브러리만 있으면 되고(브라우저 다운로드 불필요), 회사 PC의 Chrome 을 그대로 몬다.

동작:
  1) 최초 1회: headed Chrome 으로 사람이 사내 SSO/인증서 로그인 → storage_state 를 파일로 저장.
     python -m app.auth.sso_session login   (또는  lake-task-manager.exe login)
  2) 이후: 저장된 세션으로 context.request 호출(쿠키/헤더 상속). 계산 코드는 dev 와 동일.
  3) 세션 만료(수 시간~하루) → 1) 재실행.

playwright 는 선택 의존(requirements-sso.txt). import 는 지연.
"""

from .base import AuthProvider, SessionExpired

_CHANNEL = "chrome"          # 설치된 Chrome (Chromium 미번들). 안 되면 아래 폴백 안내.


def _launch(p, headless):
    """설치된 Chrome 로 실행. 없으면 명확한 안내와 함께 실패."""
    try:
        return p.chromium.launch(channel=_CHANNEL, headless=headless)
    except Exception as e:
        raise RuntimeError(
            "설치된 Chrome 을 실행하지 못했습니다. Chrome 이 설치되어 있는지 확인하세요. "
            "(Chromium 을 번들하지 않으므로 시스템 Chrome 이 필요합니다)\n원인: " + str(e))


class SsoSessionProvider(AuthProvider):
    def __init__(self, base, state_path, user_agent=None):
        from playwright.sync_api import sync_playwright   # 지연 import
        self.base = base.rstrip("/")
        self._p = sync_playwright().start()
        # 사내 SSO 는 headless 를 거부하기도 함 → 우선 headless, 로그인 때와 동일 UA 권장.
        self._browser = _launch(self._p, headless=True)
        ctx_kw = {"storage_state": state_path}
        if user_agent:
            ctx_kw["user_agent"] = user_agent
        self._context = self._browser.new_context(**ctx_kw)

    def _get(self, path, params):
        resp = self._context.request.get(self.base + path, params=params or {})
        if resp.status in (401, 403) or resp.status >= 500:
            raise SessionExpired(f"HTTP {resp.status} on {path} — 세션 만료 가능. login 재실행.")
        return resp

    def get_json(self, path, params=None):
        return self._get(path, params).json()

    def get_text(self, path, params=None):
        return self._get(path, params).text()

    def close(self):
        try:
            self._browser.close()
            self._p.stop()
        except Exception:
            pass


def login(base, state_path):
    """headed Chrome 으로 수동 SSO 로그인 후 세션 저장."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p, headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(base, wait_until="domcontentloaded")
        input(">>> 사내 SSO/인증서 로그인을 끝까지 완료한 뒤, 이 창에서 Enter: ")
        context.storage_state(path=state_path)
        browser.close()
        print(f"세션 저장 완료: {state_path}")


if __name__ == "__main__":
    import sys
    from ..settings import get_settings
    s = get_settings()
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        login(s.jira_base, s.jira_state_path)
    else:
        print("사용: python -m app.auth.sso_session login")
