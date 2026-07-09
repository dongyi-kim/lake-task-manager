"""
운영 인증 — Playwright storage_state(SSO 세션) 재사용. (../../jira_test.py 방식)

핵심: **Playwright 번들 Chromium** 을 쓴다(설치된 회사 Chrome 이 아니라).
      회사 관리형 Chrome 은 정책으로 자동화/인증서 흐름을 막아 SSO 가 깨진다 →
      정책과 무관한 깨끗한 Chromium 에서 사람이 직접 로그인하는 데모 방식이 안정적.
      Chromium 은 `playwright install chromium` 으로 준비된다(런처가 자동 처리).

동작:
  1) 로그인 1회: headed Chromium 으로 사람이 사내 SSO/인증서 로그인 → storage_state 파일 저장.
     python run.py login   (또는 화면의 'SSO 로그인' 버튼)
  2) 이후: 저장된 세션을 headless Chromium 으로 로드해 context.request 로 REST 호출(쿠키/헤더 상속).
  3) 세션 만료(수 시간~하루) → 1) 재실행.

playwright 는 prod 전용 의존(requirements-sso.txt). import 는 지연.
"""

import sys

from .base import AuthProvider, LoginRequired, SessionExpired


def _launch(p, headless):
    """Playwright 번들 Chromium 으로 실행. 없으면 'playwright install chromium' 안내."""
    try:
        return p.chromium.launch(headless=headless)
    except Exception as e:
        raise RuntimeError(
            "Chromium 을 실행하지 못했습니다. 'playwright install chromium' 이 필요합니다. "
            "(런처 exe 는 이를 자동 수행합니다)\n원인: " + str(e))


class SsoSessionProvider(AuthProvider):
    def __init__(self, base, state_path, user_agent=None):
        self.base = base.rstrip("/")
        # 세션 파일이 없으면 브라우저를 띄우기 전에 명확히 실패 → 라우트가 needLogin 으로 안내.
        import os
        if not state_path or not os.path.exists(state_path):
            raise LoginRequired(f"SSO 세션 파일이 없습니다({state_path}). 최초 로그인이 필요합니다.")
        from playwright.sync_api import sync_playwright   # 지연 import
        self._p = sync_playwright().start()
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


def _authed(context, base):
    """현재 컨텍스트가 인증됐는지 — /myself 200 + name(비익명) 이면 True."""
    try:
        resp = context.request.get(base + "/rest/api/2/myself")
        if resp.status == 200:
            body = resp.json()
            name = body.get("name") or body.get("key")
            return bool(name) and name != "anonymous"
    except Exception:
        pass
    return False


def login(base, state_path):
    """[CLI] headed Chromium 으로 수동 SSO 로그인 후 세션 저장 (터미널 Enter 대기)."""
    from playwright.sync_api import sync_playwright
    base = base.rstrip("/")
    with sync_playwright() as p:
        browser = _launch(p, headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(base, wait_until="domcontentloaded")
        input(">>> 사내 SSO/인증서 로그인을 끝까지 완료한 뒤, 이 창에서 Enter: ")
        context.storage_state(path=state_path)
        browser.close()
        print(f"세션 저장 완료: {state_path}")


def login_wait(base, state_path, timeout=300, poll=2.0):
    """[웹/자동] headed Chromium 을 띄우고, 로그인 완료를 폴링으로 감지해 세션 저장.

    터미널 Enter 없이 동작 → 웹 버튼(/api/login)에서 호출 가능.
    사용자가 브라우저에서 SSO 를 끝내면 /myself 200 을 감지하고 storage_state 저장 후 닫는다.
    반환: 성공 True / 타임아웃 False.
    """
    import time
    from playwright.sync_api import sync_playwright
    base = base.rstrip("/")
    with sync_playwright() as p:
        browser = _launch(p, headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(base, wait_until="domcontentloaded")
        deadline = time.monotonic() + timeout
        ok = False
        while time.monotonic() < deadline:
            if _authed(context, base):
                ok = True
                break
            time.sleep(poll)
        if ok:
            context.storage_state(path=state_path)
        browser.close()
        return ok


if __name__ == "__main__":
    from ..settings import get_settings
    s = get_settings()
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        login(s.jira_base, s.jira_state_path)
    else:
        print("사용: python -m app.auth.sso_session login")
