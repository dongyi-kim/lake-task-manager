"""Rendered LoginOverlay retry lifecycle. Browser plugin not available; use Playwright.

The flow under test is: show login -> cancelled pending attempt -> enabled retry ->
late old status -> new authentication success without a page reload or duplicate login.
"""
import copy
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from frontend.shell.test_local_mcp_ui import static_server  # noqa: F401


@pytest.fixture
def login_ui(static_server):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as driver:
        options = {"headless": True}
        if os.environ.get("LTM_BROWSER_EXECUTABLE"):
            options["executable_path"] = os.environ["LTM_BROWSER_EXECUTABLE"]
        try:
            browser = driver.chromium.launch(**options)
        except pw.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Optional Chromium binary is not installed")
            raise
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors, calls, held = [], [], []
        state = {"needLogin": True, "down": False, "attempt": {"state": "idle"}, "hold": False,
                 "counter": 0, "immediateCompletion": None}
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        def api(route):
            path = urlsplit(route.request.url).path
            calls.append(path)
            if path == "/api/health":
                route.fulfill(json={"needLogin": False, "hasCache": False})
            elif path == "/api/login":
                state["counter"] += 1
                state["needLogin"] = True
                state["attempt"] = {"service": "jira", "attemptId": state["counter"],
                                    "state": "running", "pending": True}
                if state["immediateCompletion"] is not None:
                    state["attempt"].update(state="completed", pending=False,
                                            succeeded=state["immediateCompletion"])
                route.fulfill(json={"ok": True, "started": True, **state["attempt"]})
            elif path == "/api/status":
                payload = {"needLogin": state["needLogin"], "down": state["down"],
                           "loginAttempt": copy.deepcopy(state["attempt"])}
                if state["hold"]:
                    held.append((route, payload))
                else:
                    route.fulfill(json=payload)
            else:
                route.fulfill(json={})
        page.route("**/api/**", api)
        page.route("**/fonts.css", lambda route: route.fulfill(content_type="text/css", body=""))
        page.route(static_server + "/", lambda route: route.fulfill(content_type="text/html", body="""
          <!doctype html><html lang="ko"><head><meta charset="UTF-8">
          <meta name="viewport" content="width=device-width,initial-scale=1">
          <title>Lake Task Manager — 로그인 복구</title>
          <link rel="stylesheet" href="/styles/tokens.css"><link rel="stylesheet" href="/styles/base.css">
          <link rel="stylesheet" href="/styles/components.css"></head><body><div id="app"></div>
          <script type="module">
            import {createApp} from '/vendor/vue.esm-browser.prod.js';
            import LoginOverlay from '/components/ui/LoginOverlay.js';
            window.__authOK=0;
            window.addEventListener('auth-ok',()=>window.__authOK++);
            createApp({components:{LoginOverlay},template:`<main><h1>작성 중인 티켓</h1>
              <textarea aria-label="작성 중인 내용">초안 유지</textarea>
              <button @click="$refs.login.doLogin()">로그인 화면 열기</button>
              <LoginOverlay ref="login" /></main>`}).mount('#app');
          </script></body></html>"""))
        page.clock.install()
        page.goto(static_server + "/")
        pw.expect(page.get_by_role("heading", name="작성 중인 티켓")).to_be_visible()
        try:
            yield page, state, calls, held, pw.expect
            assert not errors, errors
        finally:
            browser.close()


def _evidence(page, name):
    if os.environ.get("LTM_UI_EVIDENCE_DIR"):
        target = Path(os.environ["LTM_UI_EVIDENCE_DIR"])
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / name), full_page=False)


def test_cancelled_login_reenables_retry_and_old_status_cannot_finish_new_attempt(login_ui, static_server):
    page, state, calls, held, expect = login_ui
    assert page.url == static_server + "/" and "로그인 복구" in page.title()
    page.get_by_role("button", name="로그인 화면 열기").click()
    expect(page.get_by_role("button", name="로그인 진행 중…")).to_be_disabled()
    assert calls.count("/api/login") == 1
    state["attempt"].update(state="completed", pending=False, succeeded=False)
    page.clock.fast_forward(2501)
    expect(page.get_by_role("button", name="다시 시도")).to_be_enabled()
    expect(page.get_by_text("로그인이 완료되지 않았습니다(시간 초과/취소). 다시 시도하세요.")).to_be_visible()
    _evidence(page, "login-retry-desktop.png")

    # Expired cooldown and repeated auth-required events cannot reopen a cancelled login.
    page.clock.fast_forward(125000)
    page.evaluate("window.dispatchEvent(new CustomEvent('need-login'))")
    expect(page.get_by_role("button", name="다시 시도")).to_be_enabled()
    assert calls.count("/api/login") == 1
    page.set_viewport_size({"width": 390, "height": 844})
    _evidence(page, "login-retry-mobile.png")

    # Hold an old successful status response, then explicitly start a new login before it returns.
    state["needLogin"], state["hold"] = False, True
    state["attempt"]["succeeded"] = True
    page.clock.fast_forward(2501)
    page.wait_for_function("true")  # let the scheduled fetch reach the route fixture
    assert held
    page.get_by_role("button", name="다시 시도").click()
    expect(page.get_by_role("button", name="로그인 진행 중…")).to_be_disabled()
    assert calls.count("/api/login") == 2
    for route, payload in held:
        route.fulfill(json=payload)
    held.clear()
    state["hold"] = False
    page.evaluate("window.dispatchEvent(new CustomEvent('auth-login-state', {detail:{attemptId:1,state:'completed',succeeded:false}}))")
    expect(page.get_by_role("button", name="로그인 진행 중…")).to_be_disabled()
    assert page.evaluate("window.__authOK") == 0

    state["needLogin"] = False
    state["attempt"].update(state="completed", pending=False, succeeded=True)
    state["down"] = True
    page.clock.fast_forward(2501)
    expect(page.get_by_role("button", name="로그인 진행 중…")).to_be_disabled()
    assert page.evaluate("window.__authOK") == 0
    state["down"] = False
    page.clock.fast_forward(2501)
    expect(page.locator(".login-notice")).to_have_count(0)
    expect(page.get_by_label("작성 중인 내용")).to_have_value("초안 유지")
    assert page.evaluate("window.__authOK") == 1
    assert calls.count("/api/login") == 2


@pytest.mark.parametrize("succeeded", [False, True])
def test_immediate_completed_login_response_never_substitutes_for_auth_confirmation(login_ui, succeeded):
    page, state, calls, _held, expect = login_ui
    state["immediateCompletion"] = succeeded
    page.get_by_role("button", name="로그인 화면 열기").click()
    if succeeded:
        expect(page.get_by_role("button", name="로그인 진행 중…")).to_be_disabled()
    else:
        expect(page.get_by_role("button", name="다시 시도")).to_be_enabled()
        expect(page.get_by_text("로그인이 완료되지 않았습니다(시간 초과/취소). 다시 시도하세요.")).to_be_visible()
    assert calls.count("/api/login") == 1
    assert page.evaluate("window.__authOK") == 0
    page.clock.fast_forward(2501)
    expect(page.locator(".login-notice")).to_be_visible()
    assert page.evaluate("window.__authOK") == 0
    if succeeded:
        state["needLogin"] = False
        page.clock.fast_forward(2501)
        expect(page.locator(".login-notice")).to_have_count(0)
        assert page.evaluate("window.__authOK") == 1
    else:
        expect(page.get_by_role("button", name="다시 시도")).to_be_enabled()
    assert calls.count("/api/login") == 1
