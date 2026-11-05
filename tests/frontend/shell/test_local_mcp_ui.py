"""Real SettingsMenu + MCP dialog in Chromium with local management API fixtures.

Browser plugin not available in the authoring environment; use the repository's Playwright
workflow. No Jira, real token, credentials file, injected latency, or external API is used.
"""
import json
import os
import socket
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from support.paths import STATIC_ROOT


@pytest.fixture(scope="module")
def static_server():
    class StaticServer(ThreadingHTTPServer):
        # A TicketDialog imports dozens of ESM assets concurrently. Windows refuses queued
        # connections beyond the default backlog of five before the test UI can even mount.
        request_queue_size = 128

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = StaticServer(("127.0.0.1", 0), partial(Handler, directory=str(STATIC_ROOT)))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


@pytest.fixture
def ui(static_server):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except pw.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Optional Chromium binary is not installed")
            raise
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors, requests, held = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text)
                if message.type == "error" and "status of 503" not in message.text else None)
        state = {"enabled": True, "token": "fixture-only-secret-1", "fail_load": False, "hold": False}

        def status():
            return {"enabled": state["enabled"], "ready": state["enabled"],
                    "url": static_server + "/mcp", "jiraState": "ready", "tokenMasked": "••••••••"}

        def api(route):
            request = route.request
            path = urlsplit(request.url).path
            body = request.post_data_json if request.post_data else None
            if path.startswith("/api/mcp/"):
                requests.append((request.method, path, body, request.headers))
                if request.method != "GET":
                    assert request.headers.get("x-ltm-local") == "1"
                    assert request.headers.get("content-type") == "application/json"
                if path.endswith("/credentials"):
                    if state["hold"]:
                        held.append(route)
                        return
                    if body["format"] == "token":
                        text = state["token"]
                    elif body["format"] == "http":
                        text = json.dumps({"mcpServers": {"lake-task-manager": {
                            "url": status()["url"], "headers": {"Authorization": "Bearer " + state["token"]}}}})
                    else:
                        text = json.dumps({"mcpServers": {"lake-task-manager": {
                            "command": "fixture-python", "args": ["fixture-stdio.py"],
                            "env": {"LTM_MCP_URL": status()["url"], "LTM_MCP_TOKEN": state["token"]}}}})
                    route.fulfill(json={"text": text})
                    return
                if path.endswith("/rotate"):
                    state["token"] = "fixture-only-secret-2"
                elif request.method == "PUT":
                    state["enabled"] = body["enabled"]
                if state["fail_load"] and request.method == "GET":
                    route.fulfill(status=503, json={"detail": "fixture connection unavailable"})
                else:
                    route.fulfill(json=status())
            elif path == "/api/me":
                route.fulfill(json={"id": "regular-user", "known": True, "manager": False})
            elif path == "/api/prefs":
                route.fulfill(json={"agentEnabled": False, "bitbucketConfigured": False})
            elif path.startswith("/api/dev/sso/"):
                route.fulfill(json={"configured": False})
            else:
                route.fulfill(json={})

        page.route("**/api/**", api)
        # The optional CDN font is unrelated to MCP; exercise the app's offline system font.
        page.route("**/fonts.css", lambda route: route.fulfill(content_type="text/css", body=""))
        page.route(static_server + "/", lambda route: route.fulfill(content_type="text/html", body="""
            <!doctype html><html lang="ko" data-theme="light"><head><meta charset="UTF-8">
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <title>Lake Task Manager — MCP 설정</title>
            <link rel="stylesheet" href="/fonts.css"><link rel="stylesheet" href="/styles/tokens.css">
            <link rel="stylesheet" href="/styles/base.css"><link rel="stylesheet" href="/styles/components.css">
            <link rel="stylesheet" href="/styles/agent.css"><link rel="stylesheet" href="/styles/local-mcp.css">
            </head><body><div id="app" style="position:fixed;right:24px;top:24px"></div>
            <script type="module">
              import { createApp } from '/vendor/vue.esm-browser.prod.js';
              import SettingsMenu from '/components/ui/SettingsMenu.js';
              createApp(SettingsMenu).mount('#app');
            </script></body></html>"""))
        page.add_init_script("""window.__copied = [];
            Object.defineProperty(navigator, 'clipboard', {configurable:true,
                value:{writeText:async text => {window.__copied.push(text);}}});""")
        page.goto(static_server + "/")
        page.get_by_role("button", name="설정", exact=True).click()
        page.get_by_role("button", name="에이전트 연결 설정 →", exact=True).click()
        pw.expect(page.get_by_role("dialog", name="로컬 MCP 연결")).to_be_visible()
        pw.expect(page.get_by_label("서버 주소", exact=True)).to_have_value(static_server + "/mcp")
        try:
            yield page, state, requests, held, pw.expect
            assert not errors, errors
        finally:
            browser.close()


def _evidence(page, name):
    if os.environ.get("LTM_UI_EVIDENCE_DIR"):
        target = Path(os.environ["LTM_UI_EVIDENCE_DIR"])
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / name), full_page=False)


def test_regular_user_configures_copies_rotates_and_disables(ui, static_server):
    page, state, requests, _held, expect = ui
    assert page.url == static_server + "/" and "Lake Task Manager" in page.title()
    assert not any(path.endswith("/credentials") for _method, path, _body, _headers in requests)
    expect(page.get_by_label("가려진 연결 토큰")).to_have_value("••••••••")
    assert state["token"] not in page.locator("body").inner_text()
    page.get_by_role("button", name="닫기", exact=True).focus()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="로컬 MCP 설정 닫기", exact=True)).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(page.get_by_role("button", name="닫기", exact=True)).to_be_focused()
    _evidence(page, "mcp-desktop.png")

    page.get_by_role("button", name="주소 복사", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("서버 주소를 복사했습니다.")
    assert page.evaluate("window.__copied.at(-1)") == static_server + "/mcp"
    assert len(requests) == 1  # Address copy must never retrieve a bearer token.

    page.get_by_role("button", name="HTTP 설정 복사 · 인증 포함", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("인증 정보를 포함한 연결 설정을 복사했습니다.")
    copied = json.loads(page.evaluate("window.__copied.at(-1)"))["mcpServers"]["lake-task-manager"]
    assert copied["url"] == static_server + "/mcp"
    assert copied["headers"]["Authorization"] == "Bearer " + state["token"]
    page.get_by_role("button", name="stdio 설정 복사 · 인증 포함", exact=True).click()
    page.wait_for_function("window.__copied.length === 3")
    copied = json.loads(page.evaluate("window.__copied.at(-1)"))["mcpServers"]["lake-task-manager"]
    assert copied["env"]["LTM_MCP_TOKEN"] == state["token"]

    page.get_by_role("button", name="토큰 보기", exact=True).click()
    expect(page.get_by_label("연결 토큰 원문")).to_have_value(state["token"])
    page.get_by_role("button", name="토큰 가리기", exact=True).click()
    expect(page.get_by_label("가려진 연결 토큰")).to_have_value("••••••••")
    page.get_by_role("button", name="토큰 보기", exact=True).click()
    expect(page.get_by_label("연결 토큰 원문")).to_be_visible()
    page.get_by_role("button", name="토큰 재발급", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("재발급했습니다")
    expect(page.get_by_label("가려진 연결 토큰")).to_have_value("••••••••")
    page.get_by_role("switch", name="로컬 MCP 사용", exact=True).click()
    expect(page.get_by_role("switch", name="로컬 MCP 사용")).not_to_be_checked()
    expect(page.get_by_role("status")).to_have_text("로컬 MCP 연결을 껐습니다.")
    expect(page.get_by_role("button", name="토큰 재발급")).to_be_enabled()
    page.get_by_role("button", name="토큰 보기", exact=True).click()
    expect(page.get_by_label("연결 토큰 원문")).to_have_value("fixture-only-secret-2")
    assert "fixture-only-secret" not in page.evaluate("JSON.stringify({...localStorage, ...sessionStorage})")
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.get_by_role("button", name="설정", exact=True)).to_be_focused()


def test_copy_denied_offers_selectable_text_and_loading_failure_can_retry(ui):
    page, state, _requests, _held, expect = ui
    page.evaluate("""Object.defineProperty(navigator, 'clipboard', {value:{writeText:async () => {
        throw new Error('clipboard denied'); }}}); document.execCommand = () => false;""")
    page.get_by_role("button", name="HTTP 설정 복사 · 인증 포함", exact=True).click()
    manual = page.get_by_label("직접 복사할 내용")
    expect(manual).to_be_visible()
    assert state["token"] in manual.input_value()
    assert manual.evaluate("el => el.selectionEnd - el.selectionStart === el.value.length")
    page.get_by_role("button", name="토큰 재발급", exact=True).click()
    expect(manual).to_have_count(0)
    state["fail_load"] = True
    page.get_by_role("button", name="상태 새로고침", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text("fixture connection unavailable")
    expect(page.get_by_role("button", name="상태 새로고침", exact=True)).to_be_enabled()
    state["fail_load"] = False
    page.get_by_role("button", name="상태 새로고침", exact=True).click()
    expect(page.get_by_role("alert")).to_have_count(0)
    expect(page.get_by_label("가려진 연결 토큰")).to_be_visible()


def test_mobile_layout_and_close_ignore_late_secret_response(ui):
    page, state, _requests, held, expect = ui
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    box = page.get_by_role("dialog").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert page.locator(".mcp-body").evaluate("el => el.scrollWidth <= el.clientWidth")
    _evidence(page, "mcp-mobile.png")
    state["hold"] = True
    page.get_by_role("button", name="HTTP 설정 복사 · 인증 포함", exact=True).click()
    page.get_by_role("button", name="로컬 MCP 설정 닫기", exact=True).click()
    assert len(held) == 1
    held[0].fulfill(json={"text": "fixture-secret-from-retired-dialog"})
    page.get_by_role("button", name="설정", exact=True).click()
    page.get_by_role("button", name="에이전트 연결 설정 →", exact=True).click()
    expect(page.get_by_label("가려진 연결 토큰")).to_have_value("••••••••")
    assert page.evaluate("window.__copied") == []


def test_full_app_uses_actual_management_endpoints(tmp_path, monkeypatch):
    """The rendered app exports the real listener URL/token, rotates it, and gates MCP immediately."""
    import uvicorn
    from app import main
    from app.local_mcp.security import Credentials, LocalGuard

    assert main._settings.jira_env == "mock"  # Never reuse a prod session for browser verification.
    pw = pytest.importorskip("playwright.sync_api")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    runtime = main._local_mcp
    credentials = Credentials(tmp_path / "mcp-ui-secret.json")
    monkeypatch.setattr(runtime, "port", port)
    monkeypatch.setattr(runtime, "url", base + "/mcp")
    monkeypatch.setattr(runtime, "credentials", credentials)
    for route in main.app.routes:
        if isinstance(getattr(route, "app", None), LocalGuard):
            monkeypatch.setattr(route.app, "port", port)
            monkeypatch.setattr(route.app, "credentials", credentials)
    server = uvicorn.Server(uvicorn.Config(main.app, log_level="warning", proxy_headers=False))
    worker = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "Full mock app failed to start"
        with pw.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except pw.Error as exc:
                if "Executable doesn't exist" in str(exc):
                    pytest.skip("Optional Chromium binary is not installed")
                raise
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/fonts.css", lambda route: route.fulfill(content_type="text/css", body=""))
                page.add_init_script("""window.__copied = [];
                    Object.defineProperty(navigator, 'clipboard', {value:{writeText:async text => {
                      window.__copied.push(text);}}});""")
                page.goto(base + "/")
                page.wait_for_function("window.__lakeUp === 1")
                page.get_by_role("button", name="설정", exact=True).click()
                page.get_by_role("button", name="에이전트 연결 설정 →", exact=True).click()
                pw.expect(page.get_by_label("서버 주소", exact=True)).to_have_value(base + "/mcp")
                pw.expect(page.get_by_label("가려진 연결 토큰")).to_have_value("•••••••• (설정됨)")
                _evidence(page, "mcp-full-app.png")
                page.get_by_role("button", name="HTTP 설정 복사 · 인증 포함", exact=True).click()
                page.wait_for_function("window.__copied.length === 1")
                config = json.loads(page.evaluate("window.__copied[0]"))["mcpServers"]["lake-task-manager"]
                assert config["url"] == base + "/mcp"
                assert credentials.accepts(config["headers"]["Authorization"])
                safe_status = page.request.get(base + "/api/mcp/settings").json()
                assert safe_status["ready"] and "token" not in safe_status
                page.get_by_role("button", name="토큰 재발급", exact=True).click()
                pw.expect(page.locator(".mcp-body [role=status]")).to_contain_text("재발급했습니다")
                assert not credentials.accepts(config["headers"]["Authorization"])
                page.get_by_role("switch", name="로컬 MCP 사용", exact=True).click()
                pw.expect(page.get_by_role("switch", name="로컬 MCP 사용")).not_to_be_checked()
                assert not page.request.get(base + "/api/mcp/settings").json()["enabled"]
                assert page.request.post(base + "/mcp", data={}).status == 503
                page.get_by_role("switch", name="로컬 MCP 사용", exact=True).click()
                pw.expect(page.get_by_role("switch", name="로컬 MCP 사용")).to_be_checked()
                assert page.request.get(base + "/api/mcp/settings").json()["ready"]
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        worker.join(timeout=10)
        sock.close()
