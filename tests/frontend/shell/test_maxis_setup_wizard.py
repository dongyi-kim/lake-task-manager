"""Rendered wizard regression with browser-routed fixtures; no live server/API."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from support.paths import STATIC_ROOT


def test_prod_setup_renders_and_saves_tokens_without_portal_password(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    documents = [{"id": "ticket-rules", "remoteName": "01-ticket-rules.txt", "allowedRoles": ["auditor"]}]
    run = {"runId": "a" * 32, "sourceCurrent": True, "documents": documents,
           "sourceDigest": "b" * 64, "knowledgeVersion": "ltm-knowledge-2026.10",
           "workflowBundleVersion": "ltm-agent-test", "knowledgeSentinelSha256": "c" * 64,
           "bundleReady": True, "candidateSaved": True}
    received = []
    errors = []
    markup = """<!doctype html><html><head><title>LTM PROD setup fixture</title>
<link rel="stylesheet" href="/static/styles/tokens.css"><link rel="stylesheet" href="/static/styles/base.css">
<link rel="stylesheet" href="/static/styles/components.css"><link rel="stylesheet" href="/static/styles/agent.css"></head>
<body><div id="app"></div><script type="module">
import {createApp} from '/static/vendor/vue.esm-browser.prod.js';
import Dialog from '/static/components/ui/AgentSettingsDialog.js';
createApp(Dialog,{initialMode:'maxis'}).mount('#app');</script></body></html>"""
    # The Browser plugin is not available in this session. The existing Playwright
    # dependency renders the real component; every request is fulfilled in memory.
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception:
            browser = None
            for executable in (Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                               Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")):
                if executable.is_file():
                    try:
                        browser = playwright.chromium.launch(executable_path=str(executable))
                        break
                    except Exception:
                        pass
            if browser is None:
                pytest.skip("Bundled or installed Chromium is unavailable")
        page = browser.new_page(viewport={"width": 900, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type in {"error", "warning"} else None)

        def respond(route):
            path = route.request.url.split("127.0.0.1:4457", 1)[-1]
            if path == "/":
                route.fulfill(content_type="text/html", body=markup)
            elif path.startswith("/static/"):
                target = STATIC_ROOT / path[len("/static/"):]
                route.fulfill(content_type="text/css" if target.suffix == ".css" else "text/javascript",
                              body=target.read_text(encoding="utf-8"))
            else:
                if route.request.method != "GET":
                    received.append((path, route.request.post_data_json))
                body = ({"source": {"documents": documents, "knowledgeVersion": "ltm-knowledge-2026.10"}, "currentRun": run}
                        if path.endswith("/setup") else
                        {"canManage": True, "environmentOverrides": {}, "profiles": [{"name": "prod", "stage": "prod", "verified": True, "workflows": {}, "credentials": {}}]}
                        if path.endswith("/maxis/status") else
                        {"valid": False, "reason": "서명된 PROD 평가 검증 대기"}
                        if path.endswith("/qualification/status") else {"ok": True})
                route.fulfill(content_type="application/json", body=json.dumps(body))

        page.route("http://127.0.0.1:4457/**", respond)
        page.goto("http://127.0.0.1:4457/")
        try:
            page.get_by_role("heading", name="PROD MAXIS 시작하기").wait_for(timeout=5000)
        except Exception:
            pytest.fail("PROD setup did not render: " + repr(errors) + " / " + page.locator("#app").inner_html()[:1800])
        assert page.title() == "LTM PROD setup fixture"
        guide = page.get_by_role("link", name="PROD 설정·검수·데모 가이드 열기")
        assert guide.get_attribute("href") == "/api/agent/maxis/setup/guide"
        assert guide.get_attribute("target") == "_blank"
        assert "noopener" in guide.get_attribute("rel")
        page.get_by_role("button", name="2 · 자산 연결").click()
        page.get_by_placeholder("https://service.example/v1").fill("https://prod.example/v1")
        page.get_by_text("이 주소가 token을 전송할 실제 PROD 호스트임을 확인했습니다.").click()
        page.get_by_role("button", name="이 PC에 정확한 PROD 호스트 등록").click()
        page.get_by_text("이 PC의 PROD Service API 호스트를 등록했습니다.", exact=False).wait_for()
        page.get_by_role("button", name="3 · 게시와 token").click()
        token_fields = page.locator('.mx-setup input[type="password"]')
        assert token_fields.count() == 2
        token_fields.nth(0).fill("synthetic-app-token")
        page.get_by_role("button", name="token을 Windows DPAPI에 저장").click()
        page.get_by_text("현재 Windows 사용자의 DPAPI에 token을 저장했습니다.", exact=False).wait_for()
        assert token_fields.nth(0).input_value() == ""
        assert received[-1] == ("/api/agent/maxis/credentials/prod", {"agentCoreApiKey": "synthetic-app-token"})
        assert not page.locator('input[autocomplete="current-password"]').count()
        page.get_by_role("button", name="4 · 검증과 적용").click()
        assert page.get_by_role("button", name="Agent·에디터에 PROD MAXIS 적용").is_disabled()
        screenshot_dir = Path(os.getenv("LTM_UI_SCREENSHOTS") or tmp_path)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot_dir / "prod-setup-desktop.png"))
        page.set_viewport_size({"width": 420, "height": 860})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(screenshot_dir / "prod-setup-mobile.png"))
        assert not errors, errors
        browser.close()
