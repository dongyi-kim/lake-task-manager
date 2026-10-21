"""Rendered wizard regression with browser-routed fixtures; no live server/API."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from support.paths import STATIC_ROOT


def test_selected_profile_setup_renders_and_saves_tokens_without_portal_password(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    documents = [{"id": "ticket-rules", "remoteName": "01-ticket-rules.txt", "allowedRoles": ["auditor"]}]
    run = {"runId": "a" * 32, "sourceCurrent": True, "documents": documents,
           "sourceDigest": "b" * 64, "knowledgeVersion": "ltm-knowledge-2026.10",
           "workflowBundleVersion": "ltm-agent-test", "knowledgeSentinelSha256": "c" * 64,
           "bundleReady": True, "candidateSaved": True}
    received = []
    errors = []
    imported_model = {
        "plugin_unique_identifier": "team/provider:fixture",
        "provider": "team/provider", "name": "project-selected-model",
        "completion_params": {"temperature": 0.3, "max_tokens": 2048},
    }
    markup = """<!doctype html><html><head><title>LTM MAXIS setup fixture</title>
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
                endpoint = path.split("?", 1)[0]
                body = ({"source": {"documents": documents, "knowledgeVersion": "ltm-knowledge-2026.10"}, "currentRun": run}
                        if endpoint.endswith("/setup") else
                        {"ok": True, "model": imported_model}
                        if endpoint.endswith("/setup/model") else
                        {"canManage": True, "environmentOverrides": {}, "profiles": [
                            {"name": "profile-team", "displayName": "검토용 MAXIS", "isLegacy": False,
                             "executionPolicy": "qualified", "verified": True, "workflows": {}, "credentials": {}}]}
                        if path.endswith("/maxis/status") else
                        {"valid": False, "reason": "서명된 평가 검증 대기"}
                        if endpoint.endswith("/qualification/status") else
                        {"ok": True, "baseUrl": "https://team.example/v1"})
                route.fulfill(content_type="application/json", body=json.dumps(body))

        page.route("http://127.0.0.1:4457/**", respond)
        page.goto("http://127.0.0.1:4457/")
        page.get_by_role("button", name="MAXIS 설정 추가", exact=True).click()
        assert page.locator(".mx-setup").count() == 0
        page.get_by_role("button", name="선택한 설정의 서버 자산 준비", exact=True).click()
        try:
            page.get_by_role("heading", name="저장소 파일로 MAXIS 설정").wait_for(timeout=5000)
        except Exception:
            pytest.fail("MAXIS setup did not render: " + repr(errors) + " / " + page.locator("#app").inner_html()[:1800])
        assert page.get_by_text("저장 위치: 검토용 MAXIS", exact=True).is_visible()
        assert page.title() == "LTM MAXIS setup fixture"
        assert not any(word in page.locator(".mx-setup").inner_text() for word in ("PROD", "DEV"))
        guide = page.get_by_role("link", name="MAXIS 설정·검수 가이드 열기")
        assert guide.get_attribute("href") == "/api/agent/maxis/setup/guide?profile=profile-team"
        assert guide.get_attribute("target") == "_blank"
        assert "noopener" in guide.get_attribute("rel")
        page.get_by_role("button", name="2 · 자산 연결").click()
        page.get_by_placeholder("https://service.example/v1").fill("https://team.example/v1")
        page.get_by_text("이 주소가 token을 전송할 실제 MAXIS 호스트임을 확인했습니다.").click()
        page.get_by_role("button", name="이 PC에 정확한 MAXIS 호스트 등록").click()
        page.get_by_text("이 PC의 MAXIS Service API 호스트를 등록했습니다.", exact=False).wait_for()
        assert received[-1] == ("/api/agent/maxis/setup/host?profile=profile-team",
                               {"baseUrl": "https://team.example/v1", "confirmed": True})
        page.get_by_label("승인된 모델 설정 YAML 가져오기", exact=True).set_input_files({
            "name": "approved-model.yaml", "mimeType": "application/yaml",
            "buffer": b"synthetic export is parsed by the fixture",
        })
        page.get_by_text("모델 식별자와 허용된 생성 설정을 가져왔습니다.", exact=False).wait_for()
        assert page.get_by_label("승인된 모델 이름", exact=True).input_value() == "project-selected-model"
        page.get_by_text("모델 생성 설정", exact=True).click()
        params = page.get_by_label("모델 생성 설정 JSON", exact=True)
        assert json.loads(params.input_value()) == imported_model["completion_params"]
        page.get_by_role("button", name="Chatflow 3개 생성·다운로드", exact=True).click()
        page.get_by_text("선택한 서버 자산에 연결한 세 Chatflow 파일", exact=False).wait_for()
        bundle = [body for path, body in received if path.startswith("/api/agent/maxis/setup/bundle")]
        assert bundle[-1]["model"] == imported_model
        params.fill("[]")
        page.get_by_role("button", name="Chatflow 3개 생성·다운로드", exact=True).click()
        page.get_by_text("모델 생성 설정은 JSON 객체로 입력하세요.", exact=False).wait_for()
        assert len([body for path, body in received if path.startswith("/api/agent/maxis/setup/bundle")]) == 1
        page.get_by_role("button", name="모델 서버 기본값 사용", exact=True).click()
        assert params.input_value() == "{}"
        page.get_by_role("button", name="Chatflow 3개 생성·다운로드", exact=True).click()
        page.get_by_text("선택한 서버 자산에 연결한 세 Chatflow 파일", exact=False).wait_for()
        bundle = [body for path, body in received if path.startswith("/api/agent/maxis/setup/bundle")]
        assert bundle[-1]["model"]["completion_params"] == {}
        page.get_by_role("button", name="3 · 게시와 token").click()
        token_fields = page.locator('.mx-setup input[type="password"]')
        assert token_fields.count() == 2
        token_fields.nth(0).fill("synthetic-app-token")
        page.get_by_role("button", name="token을 Windows DPAPI에 저장").click()
        page.get_by_text("현재 Windows 사용자의 DPAPI에 token을 저장했습니다.", exact=False).wait_for()
        assert token_fields.nth(0).input_value() == ""
        assert received[-1] == ("/api/agent/maxis/credentials/profile-team", {"agentCoreApiKey": "synthetic-app-token"})
        assert not page.locator('input[autocomplete="current-password"]').count()
        page.get_by_role("button", name="4 · 검증과 적용").click()
        assert page.get_by_role("button", name="이 설정을 Agent·에디터에 사용").is_disabled()
        assert "sign --profile profile-team" in page.locator(".mx-setup pre").text_content()
        screenshot_dir = Path(os.getenv("LTM_UI_SCREENSHOTS") or tmp_path)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot_dir / "maxis-setup-desktop.png"))
        page.set_viewport_size({"width": 420, "height": 860})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(screenshot_dir / "maxis-setup-mobile.png"))
        assert not errors, errors
        browser.close()
