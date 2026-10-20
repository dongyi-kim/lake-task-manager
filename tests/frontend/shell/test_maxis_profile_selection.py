"""Profile-first settings with in-memory APIs, never real MAXIS requests."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from support.paths import STATIC_ROOT


@pytest.fixture
def profile_ui():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    workflows = ("agent_core", "editor_author", "pmo_brief")
    profiles = [
        {"name": name, "stage": name, "active": False, "enabled": False, "verified": True,
         "baseUrl": f"https://{name}.example/v1", "effectiveHost": f"{name}.example",
         "dataPolicy": "synthetic_only" if name == "dev" else "internal_read_only",
         "workflowBundleVersion": "fixture-bundle", "knowledgeVersion": "ltm-public-knowledge.v1",
         "knowledgeDatasetId": "11111111-1111-4111-8111-111111111111",
         "knowledgeSentinelSha256": "a" * 64, "configError": "",
         "remoteLogPolicyId": "fixture-policy", "remoteLogMode": "redacted", "remoteLogRetentionDays": 0,
         "effectiveRemoteLogPolicyId": "fixture-policy", "effectiveRemoteLogMode": "redacted",
         "effectiveRemoteLogRetentionDays": 0, "qualification": {"valid": True},
         "workflows": {key: {"binding": key, "version": "11111111-1111-4111-8111-111111111111"}
                       for key in workflows},
         "credentials": {key: {"configured": True, "source": "dpapi"} for key in workflows},
         "lastProbe": {"at": "2026-01-01T00:00:00Z", "ok": True, "workflows": {}}}
        for name in ("dev", "prod")
    ]
    state = {"canManage": True, "activeProfile": "", "profiles": profiles,
             "environmentOverrides": {}, "routes": dict.fromkeys(workflows, "local"),
             "lastRemoteObservations": {"state": "none_recorded", "observations": []}}
    runtime = {key: {"engine": "local", "profile": "", "ready": True}
               for key in ("conversation", "editor")}
    calls, errors = [], []
    markup = """<!doctype html><html><head><title>LTM MAXIS profile fixture</title>
<link rel="stylesheet" href="/static/styles/tokens.css">
<link rel="stylesheet" href="/static/styles/base.css">
<link rel="stylesheet" href="/static/styles/components.css">
<link rel="stylesheet" href="/static/styles/agent.css"></head>
<body><div id="app"></div><script type="module">
import {createApp} from '/static/vendor/vue.esm-browser.prod.js';
import Dialog from '/static/components/ui/AgentSettingsDialog.js';
window.savedCount=0;
createApp(Dialog,{initialMode:'maxis',onSaved:()=>window.savedCount++}).mount('#app');
</script></body></html>"""
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            browser = None
            for executable in (Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                               Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")):
                if executable.is_file():
                    browser = p.chromium.launch(executable_path=str(executable))
                    break
            if browser is None:
                pytest.skip("Chromium unavailable")
        page = browser.new_page(viewport={"width": 1100, "height": 1000})
        page.set_default_timeout(5000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text)
                if message.type in {"error", "warning"} else None)

        def respond(route):
            path = route.request.url.split("127.0.0.1:4457", 1)[-1]
            if path == "/":
                route.fulfill(content_type="text/html", body=markup)
                return
            if path.startswith("/static/"):
                target = STATIC_ROOT / path[len("/static/"):]
                route.fulfill(content_type="text/css" if target.suffix == ".css" else "text/javascript",
                              body=target.read_text(encoding="utf-8"))
                return
            calls.append((route.request.method, path))
            body = {"ok": True}
            if path == "/api/agent/maxis/status":
                if state.pop("failNextStatus", False):
                    state["expectedStatusError"] = True
                    route.fulfill(status=503, content_type="application/json",
                                  body=json.dumps({"detail": "fixture status unavailable"}))
                    return
                body = state
            elif path == "/api/agent/maxis/routes":
                state["routes"] = route.request.post_data_json["routes"]
                body = {"ok": True, **{k: v for k, v in state.items() if k != "canManage"}}
            elif path == "/api/agent/maxis/kill-switch":
                state["killSwitch"] = route.request.post_data_json["enabled"]
                body = {"ok": True, **{k: v for k, v in state.items() if k != "canManage"}}
            elif path == "/api/agent/runtime/status":
                body = runtime
            elif path == "/api/agent/status":
                body = {"configs": [], "runtime": runtime}
            elif path == "/api/health":
                body = {"env": "mock", "status": "ok"}
            elif path.startswith("/api/agent/maxis/activate/"):
                if state.get("failActivation"):
                    body = {"ok": False, "error": "fixture activation denied"}
                else:
                    name = path.rsplit("/", 1)[-1]
                    state["activeProfile"] = name
                    for row in profiles:
                        row.update(active=row["name"] == name, enabled=row["name"] == name)
                    body = state
            elif path == "/api/agent/maxis/setup":
                body = {"source": {"documents": [], "knowledgeVersion": "ltm-public-knowledge.v1"},
                        "currentRun": None}
            elif path == "/api/agent/maxis/qualification/status":
                body = {"valid": False, "reason": "fixture qualification required"}
            route.fulfill(content_type="application/json", body=json.dumps(body))

        page.route("**/*", respond)
        yield page, state, runtime, calls
        unexpected = [error for error in errors if not (
            state.get("expectedStatusError") and
            error == "Failed to load resource: the server responded with a status of 503 (Service Unavailable)")]
        assert not unexpected, unexpected
        browser.close()


def open_profiles(page):
    page.goto("http://127.0.0.1:4457/")
    page.locator('[data-profile="dev"]').wait_for()
    assert page.title() == "LTM MAXIS profile fixture"
    assert page.url == "http://127.0.0.1:4457/"


def test_existing_profile_is_first_without_prod_onboarding(profile_ui, tmp_path):
    page, state, _, calls = profile_ui
    state["activeProfile"] = "dev"
    state["profiles"][0].update(active=True, enabled=True)
    open_profiles(page)
    assert page.get_by_role("button", name="이 설정 사용", exact=True).is_visible()
    assert page.get_by_text("PROD MAXIS 시작하기", exact=True).count() == 0
    assert page.get_by_text("환경별 고급 설정 · 기존 연결 확인", exact=True).count() == 0
    assert page.locator(".mx-setup").count() == 0
    assert not any("/setup" in path or "/qualification" in path for _, path in calls)
    assert not any(method != "GET" for method, _ in calls)
    assert page.get_by_text("현재 사용 중 · MAXIS", exact=True).count() == 0
    target = Path(os.getenv("LTM_UI_SCREENSHOTS") or tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target / "profiles-desktop.png"))
    page.set_viewport_size({"width": 420, "height": 860})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.get_by_role("button", name="이 설정 사용", exact=True).is_visible()
    page.screenshot(path=str(target / "profiles-mobile.png"))


def test_selecting_and_using_profile_are_distinct_and_refresh_parent(profile_ui):
    page, state, _, calls = profile_ui
    open_profiles(page)
    page.locator('[data-profile="prod"]').click()
    page.locator('[data-profile="dev"]').click()
    assert not any(method != "GET" for method, _ in calls)
    page.get_by_role("button", name="이 설정 사용", exact=True).click()
    page.wait_for_function("window.savedCount > 0")
    assert state["activeProfile"] == "dev"
    assert [(method, path) for method, path in calls if method != "GET"] == [
        ("POST", "/api/agent/maxis/activate/dev")]
    assert set(state["routes"].values()) == {"local"}
    assert page.get_by_text("현재 사용 중 · MAXIS", exact=True).count() == 0


def test_only_actual_profile_runtime_gets_currently_in_use_badge(profile_ui):
    page, state, runtime, _ = profile_ui
    state["activeProfile"] = "prod"
    state["profiles"][1].update(active=True, enabled=True)
    state["routes"] = {key: "maxis" for key in state["routes"]}
    runtime.update({key: {"engine": "maxis", "profile": "prod", "ready": True}
                    for key in runtime})
    open_profiles(page)
    page.get_by_text("현재 사용 중 · MAXIS", exact=True).first.wait_for()


def test_manager_denial_disables_use_without_api_mutation(profile_ui):
    page, state, _, calls = profile_ui
    state["canManage"] = False
    open_profiles(page)
    assert page.get_by_role("button", name="이 설정 사용", exact=True).is_disabled()
    assert not any(method != "GET" for method, _ in calls)


def test_unsaved_profile_input_cannot_be_applied(profile_ui):
    page, _, _, calls = profile_ui
    open_profiles(page)
    page.get_by_text("연결 설정 편집", exact=True).click()
    page.locator('input[placeholder="https://maxis.example/v1"]').fill("https://changed.example/v1")
    assert page.get_by_role("button", name="이 설정 사용", exact=True).is_disabled()
    assert not any(method != "GET" for method, _ in calls)


def test_failed_activation_does_not_emit_saved_or_show_usage(profile_ui):
    page, state, _, _ = profile_ui
    state["failActivation"] = True
    open_profiles(page)
    page.get_by_role("button", name="이 설정 사용", exact=True).click()
    page.get_by_text("fixture activation denied", exact=True).wait_for()
    assert page.evaluate("window.savedCount") == 0
    assert state["activeProfile"] == ""
    assert page.get_by_text("현재 사용 중 · MAXIS", exact=True).count() == 0


def test_route_save_reloads_management_permission(profile_ui):
    page, state, _, _ = profile_ui
    open_profiles(page)
    page.get_by_text("기능별 실행 경로와 긴급 중단", exact=True).click()
    routes = page.locator("details").filter(
        has=page.get_by_text("기능별 실행 경로와 긴급 중단", exact=True))
    routes.locator("select").first.select_option("shadow")
    page.get_by_role("button", name="실행 경로 저장", exact=True).click()
    page.wait_for_function("window.savedCount === 1")
    assert state["routes"]["agent_core"] == "shadow"
    assert page.get_by_role("button", name="실행 경로 저장", exact=True).is_enabled()
    assert page.get_by_role("switch").is_enabled()
    assert page.get_by_role("button", name="연결 확인", exact=True).is_enabled()


def test_kill_toggle_keeps_permission_and_unsaved_inputs(profile_ui):
    page, state, _, _ = profile_ui
    open_profiles(page)
    page.get_by_text("연결 설정 편집", exact=True).click()
    base = page.get_by_placeholder("https://maxis.example/v1", exact=True)
    base.fill("https://unsaved.example/v1")
    page.get_by_text("API Key 관리", exact=True).click()
    key = page.locator('.mx-key-row input[type="password"]').first
    key.fill("synthetic-unsaved-key")
    page.get_by_text("기능별 실행 경로와 긴급 중단", exact=True).click()
    toggle = page.get_by_role("switch")
    toggle.click()
    page.wait_for_function("window.savedCount === 1")
    assert state["killSwitch"] is True
    assert base.input_value() == "https://unsaved.example/v1"
    assert key.input_value() == "synthetic-unsaved-key"
    assert toggle.is_enabled()
    toggle.click()
    page.wait_for_function("window.savedCount === 2")
    assert state["killSwitch"] is False
    assert toggle.is_enabled()


def test_repository_setup_survives_navigation_without_saving(profile_ui):
    page, _, _, calls = profile_ui
    open_profiles(page)
    add = page.get_by_role("button", name="MAXIS 설정 추가", exact=True)
    add.click()
    page.get_by_role("button", name="저장소 파일로 PROD 설정", exact=True).click()
    page.get_by_role("button", name="3 · 게시와 token", exact=True).click()
    token = page.locator('.mx-setup input[type="password"]').first
    token.fill("synthetic-draft-token")
    add.click()
    assert token.input_value() == "synthetic-draft-token"
    page.get_by_role("button", name="저장된 MAXIS 설정", exact=True).click()
    assert page.locator(".mx-setup").is_hidden()
    add.click()
    assert token.input_value() == "synthetic-draft-token"
    assert not any(method != "GET" for method, _ in calls)


def test_failed_refresh_locks_changes_and_retry_keeps_draft(profile_ui):
    page, state, _, _ = profile_ui
    open_profiles(page)
    page.get_by_text("API Key 관리", exact=True).click()
    key = page.locator('.mx-key-row input[type="password"]').first
    key.fill("synthetic-draft-key")
    page.get_by_text("기능별 실행 경로와 긴급 중단", exact=True).click()
    state["failNextStatus"] = True
    page.get_by_role("switch").click()
    retry = page.get_by_role("button", name="설정 상태 다시 불러오기", exact=True)
    retry.wait_for()
    assert page.get_by_role("switch").is_disabled()
    assert key.input_value() == "synthetic-draft-key"
    retry.click()
    retry.wait_for(state="hidden")
    assert page.get_by_role("switch").is_enabled()
    assert key.input_value() == "synthetic-draft-key"
    state["failNextStatus"] = True
    page.get_by_role("switch").click()
    retry.wait_for()
    page.get_by_role("button", name="변경 취소", exact=True).click()
    assert key.input_value() == ""
    assert page.get_by_role("switch").is_disabled()
    retry.click()
    retry.wait_for(state="hidden")
    assert page.get_by_role("switch").is_enabled()
