"""Profile-first settings with in-memory APIs, never real MAXIS requests."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from support.paths import STATIC_ROOT


@pytest.fixture
def profile_ui():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    workflows = ("agent_core", "editor_author", "pmo_brief")
    profiles = [
        {"name": name, "displayName": label, "isLegacy": True,
         "executionPolicy": "connected", "active": False, "enabled": False, "verified": True,
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
        for name, label in (("dev", "기존 MAXIS"), ("prod", "팀 MAXIS"))
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
window.savedCount=0; window.closedCount=0;
createApp({components:{Dialog},data:()=>({open:true}),
methods:{saved(result){window.savedCount++;window.appliedRuntime=result?.runtime;},
closed(){window.closedCount++;this.open=false;}},
template:`<Dialog v-if="open" initial-mode="maxis" @saved="saved" @close="closed" />
<p v-else>설정 적용 완료</p>`}).mount('#app');
</script></body></html>"""
    agent_markup = markup[:markup.index("<script type=")] + """<script type="module">
import {createApp} from '/static/vendor/vue.esm-browser.prod.js';
import AgentView from '/static/components/views/AgentView.js';
createApp(AgentView).mount('#app');</script></body></html>"""
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
            url = urlsplit(route.request.url)
            path = url.path
            request_path = path + ("?" + url.query if url.query else "")
            if path in {"/", "/agent"}:
                route.fulfill(content_type="text/html", body=agent_markup if path == "/agent" else markup)
                return
            if path.startswith(("/static/", "/vendor/")):
                target = STATIC_ROOT / (path[len("/static/"):] if path.startswith("/static/")
                                        else path.lstrip("/"))
                route.fulfill(content_type="text/css" if target.suffix == ".css" else "text/javascript",
                              body=target.read_text(encoding="utf-8"))
                return
            calls.append((route.request.method, request_path))
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
                body = {"configs": [], "runtime": runtime, "llmReady": False,
                        "llmReason": "사용할 연결 설정을 추가하고 적용하세요."}
            elif path == "/api/prefs":
                body = {"agentEnabled": True}
            elif path == "/api/options/priorities":
                body = []
            elif path == "/api/health":
                body = {"env": "mock", "status": "ok"}
            elif path == "/api/agent/maxis/profiles" and route.request.method == "POST":
                name = "profile-new"
                profiles.append({"name": name, "displayName": route.request.post_data_json["displayName"],
                                 "isLegacy": False, "executionPolicy": "connected",
                                 "baseUrl": "", "workflows": {}, "credentials": {}})
                body = {"ok": True, "name": name, **state}
            elif path.startswith("/api/agent/maxis/profiles/") and route.request.method == "PUT":
                name = path.rsplit("/", 1)[-1]
                patch = route.request.post_data_json
                state.setdefault("profilePatches", []).append(patch)
                selected = next(row for row in profiles if row["name"] == name)
                selected.update(patch)
                if set(patch) - {"displayName"}:
                    selected.update(verified=False, enabled=False)
                    for row in runtime.values():
                        if row["profile"] == name:
                            row.update(ready=False, reason="연결을 다시 확인하세요.")
                else:
                    for row in runtime.values():
                        if row["profile"] == name:
                            row["profileLabel"] = selected["displayName"]
                body = {"ok": True, **{k: v for k, v in state.items() if k != "canManage"}}
            elif path.startswith("/api/agent/maxis/profiles/") and path.endswith("/apply"):
                if state.get("failActivation"):
                    body = {"ok": False, "error": "fixture activation denied"}
                elif state.get("badRuntime"):
                    body = {"ok": True, "activeProfile": "dev", "runtime": runtime}
                else:
                    name = path.split("/")[-2]
                    state["activeProfile"] = name
                    for row in profiles:
                        row.update(active=row["name"] == name, enabled=row["name"] == name)
                    requested = route.request.post_data_json["workflows"]
                    for key in requested:
                        state["routes"][key] = "maxis"
                    label = next(row["displayName"] for row in profiles if row["name"] == name)
                    runtime.update({key: {"engine": "maxis", "profile": name, "profileLabel": label,
                                          "ready": True} for key in runtime})
                    body = {"ok": True, "activeProfile": name, "runtime": runtime,
                            "workflows": requested}
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
    assert "기존 MAXIS" in page.locator('[data-profile="dev"]').inner_text()
    assert not any(word in page.locator(".mx-panel").inner_text() for word in ("PROD", "DEV"))
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


def test_using_profile_applies_both_features_and_closes_parent_dialog(profile_ui):
    page, state, runtime, calls = profile_ui
    open_profiles(page)
    page.locator('[data-profile="prod"]').click()
    page.locator('[data-profile="dev"]').click()
    assert not any(method != "GET" for method, _ in calls)
    page.get_by_role("button", name="이 설정 사용", exact=True).click()
    page.wait_for_function("window.savedCount > 0")
    assert state["activeProfile"] == "dev"
    assert [(method, path) for method, path in calls if method != "GET"] == [
        ("POST", "/api/agent/maxis/profiles/dev/apply")]
    assert state["routes"] == {"agent_core": "maxis", "editor_author": "maxis", "pmo_brief": "local"}
    assert all(row["ready"] and row["profile"] == "dev" for row in runtime.values())
    assert page.evaluate("window.closedCount") == 1
    assert page.locator(".ag-config-dlg").count() == 0


def test_agent_parent_unblocks_without_local_llm_after_apply(profile_ui, tmp_path):
    page, state, runtime, calls = profile_ui
    state["profiles"][0]["verified"] = False
    runtime.update({key: {"engine": "local", "profile": "", "ready": False,
                         "reason": "사용할 연결 설정을 추가하고 적용하세요.",
                         "nextAction": "maxis_settings"} for key in runtime})
    page.goto("http://127.0.0.1:4457/agent")
    page.get_by_text("⚠ AI 를 쓸 수 없습니다", exact=False).wait_for()
    page.get_by_role("button", name="MAXIS 설정", exact=True).click()
    page.get_by_role("button", name="이 설정 사용", exact=True).click()
    page.locator(".ag-config-dlg").wait_for(state="detached")
    page.locator(".agent-input-rich .ProseMirror").wait_for()
    assert page.locator(".agent-llmoff").count() == 0
    assert "기존 MAXIS" in page.locator(".agent-prov").inner_text()
    assert page.get_by_text("LLM API 가 하나 이상", exact=False).count() == 0
    assert [(method, path) for method, path in calls if method != "GET"] == [
        ("POST", "/api/agent/maxis/profiles/dev/apply")]
    target = Path(os.getenv("LTM_UI_SCREENSHOTS") or tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target / "agent-after-apply.png"))


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
    assert page.evaluate("window.closedCount") == 0
    assert page.get_by_text("현재 사용 중 · MAXIS", exact=True).count() == 0


def test_incomplete_apply_response_keeps_settings_open(profile_ui):
    page, state, _, _ = profile_ui
    state["badRuntime"] = True
    open_profiles(page)
    page.get_by_role("button", name="이 설정 사용", exact=True).click()
    page.get_by_text("MAXIS 적용 후 Agent·에디터 실행 상태를 확인하지 못했습니다.", exact=False).wait_for()
    assert page.evaluate("window.closedCount") == 0
    assert page.evaluate("window.savedCount") == 0
    assert page.locator(".ag-config-dlg").is_visible()


def test_add_creates_named_profile_without_reusing_legacy_slot(profile_ui):
    page, state, _, calls = profile_ui
    state["profiles"].append({"name": "internal-pilot", "isLegacy": True,
                              "displayName": "빈 연결", "baseUrl": "", "credentials": {}})
    open_profiles(page)
    assert page.locator('[data-profile="internal-pilot"]').count() == 0
    page.get_by_role("button", name="MAXIS 설정 추가", exact=True).click()
    page.get_by_label("새 설정 이름", exact=True).fill("다른 MAXIS")
    page.get_by_role("button", name="설정 추가", exact=True).click()
    page.locator('[data-profile="profile-new"]').wait_for()
    assert page.get_by_label("설정 이름", exact=True).input_value() == "다른 MAXIS"
    assert page.locator('[data-profile="dev"]').is_visible()
    assert page.locator('[data-profile="prod"]').is_visible()
    assert [(method, path) for method, path in calls if method != "GET"] == [
        ("POST", "/api/agent/maxis/profiles")]


def test_renaming_active_profile_sends_only_label_and_keeps_runtime_ready(profile_ui):
    page, state, runtime, calls = profile_ui
    state["activeProfile"] = "dev"
    state["profiles"][0].update(active=True, enabled=True)
    state["routes"].update(agent_core="maxis", editor_author="maxis")
    runtime.update({key: {"engine": "maxis", "profile": "dev", "profileLabel": "기존 MAXIS",
                         "ready": True} for key in runtime})
    open_profiles(page)
    page.get_by_text("현재 사용 중 · MAXIS", exact=True).first.wait_for()
    page.get_by_text("연결 설정 편집", exact=True).click()
    page.get_by_label("설정 이름", exact=True).fill("내 MAXIS")
    page.get_by_role("button", name="후보 프로필 저장", exact=True).click()
    page.wait_for_function("window.savedCount === 1")
    assert state["profilePatches"] == [{"displayName": "내 MAXIS"}]
    assert state["profiles"][0]["enabled"] and state["profiles"][0]["verified"]
    assert all(row["ready"] and row["profileLabel"] == "내 MAXIS" for row in runtime.values())
    assert page.get_by_text("현재 사용 중 · MAXIS", exact=True).count() == 2
    assert "내 MAXIS" in page.locator('[data-profile="dev"]').inner_text()
    assert [(method, path) for method, path in calls if method != "GET"] == [
        ("PUT", "/api/agent/maxis/profiles/dev")]
    page.get_by_role("button", name="후보 프로필 저장", exact=True).click()
    page.get_by_text("변경한 프로필 설정이 없습니다.", exact=True).wait_for()
    assert len(state["profilePatches"]) == 1


def test_changed_nested_workflow_is_sent_without_unchanged_profile_fields(profile_ui):
    page, state, _, _ = profile_ui
    open_profiles(page)
    page.get_by_text("연결 설정 편집", exact=True).click()
    version = "22222222-2222-4222-8222-222222222222"
    page.get_by_label("게시 Workflow UUID", exact=True).nth(1).fill(version)
    page.get_by_role("button", name="후보 프로필 저장", exact=True).click()
    page.wait_for_function("window.savedCount === 1")
    assert list(state["profilePatches"][0]) == ["workflows"]
    assert state["profilePatches"][0]["workflows"]["editor_author"]["version"] == version
    assert state["profiles"][0]["verified"] is False


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
    page.get_by_role("button", name="선택한 설정의 서버 자산 준비", exact=True).click()
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


def test_repository_setup_applies_selected_profile_and_closes_dialog(profile_ui):
    page, state, runtime, calls = profile_ui
    state["profiles"][0]["workflows"]["pmo_brief"]["version"] = ""
    open_profiles(page)
    page.get_by_role("button", name="MAXIS 설정 추가", exact=True).click()
    page.get_by_role("button", name="선택한 설정의 서버 자산 준비", exact=True).click()
    page.get_by_role("button", name="4 · 검증과 적용", exact=True).click()
    page.get_by_role("button", name="이 설정을 Agent·에디터에 사용", exact=True).click()
    page.wait_for_function("window.closedCount === 1")
    assert page.locator(".ag-config-dlg").count() == 0
    assert runtime["conversation"]["profile"] == "dev"
    assert runtime["editor"]["ready"] is True
    assert state["routes"]["pmo_brief"] == "local"
    assert [(method, path) for method, path in calls if method != "GET"] == [
        ("POST", "/api/agent/maxis/profiles/dev/apply")]


def test_pending_wizard_token_blocks_both_apply_buttons_until_discarded(profile_ui):
    page, _, _, calls = profile_ui
    open_profiles(page)
    add = page.get_by_role("button", name="MAXIS 설정 추가", exact=True)
    add.click()
    page.get_by_role("button", name="선택한 설정의 서버 자산 준비", exact=True).click()
    page.get_by_role("button", name="3 · 게시와 token", exact=True).click()
    token = page.locator('.mx-setup input[type="password"]').first
    token.fill("synthetic-unsaved-token")
    page.get_by_role("button", name="4 · 검증과 적용", exact=True).click()
    assert page.get_by_role("button", name="이 설정을 Agent·에디터에 사용", exact=True).is_disabled()
    page.get_by_role("button", name="저장된 MAXIS 설정", exact=True).click()
    assert page.get_by_role("button", name="이 설정 사용", exact=True).is_disabled()
    assert page.evaluate("window.closedCount") == 0
    assert not any(method != "GET" for method, _ in calls)
    add.click()
    page.get_by_role("button", name="준비 변경 취소", exact=True).click()
    page.get_by_role("button", name="3 · 게시와 token", exact=True).click()
    assert token.input_value() == ""
    page.get_by_role("button", name="저장된 MAXIS 설정", exact=True).click()
    page.get_by_role("button", name="이 설정 사용", exact=True).click()
    page.wait_for_function("window.closedCount === 1")
    assert [(method, path) for method, path in calls if method != "GET"] == [
        ("POST", "/api/agent/maxis/profiles/dev/apply")]


def test_saving_wizard_token_keeps_other_pending_fields_and_apply_blocked(profile_ui):
    page, _, _, calls = profile_ui
    open_profiles(page)
    page.get_by_role("button", name="MAXIS 설정 추가", exact=True).click()
    page.get_by_role("button", name="선택한 설정의 서버 자산 준비", exact=True).click()
    page.get_by_role("button", name="3 · 게시와 token", exact=True).click()
    workflow = page.get_by_label("Agent Core 게시 Workflow UUID", exact=True)
    original = workflow.input_value()
    changed = "22222222-2222-4222-8222-222222222222"
    workflow.fill(changed)
    token = page.locator('.mx-setup input[type="password"]').first
    token.fill("synthetic-new-token")
    page.get_by_role("button", name="token을 Windows DPAPI에 저장", exact=True).click()
    page.get_by_text("현재 Windows 사용자의 DPAPI에 token을 저장했습니다.", exact=False).wait_for()
    assert token.input_value() == ""
    assert workflow.input_value() == changed
    page.get_by_role("button", name="4 · 검증과 적용", exact=True).click()
    assert page.get_by_role("button", name="이 설정을 Agent·에디터에 사용", exact=True).is_disabled()
    page.get_by_role("button", name="준비 변경 취소", exact=True).click()
    assert page.get_by_role("button", name="이 설정을 Agent·에디터에 사용", exact=True).is_enabled()
    page.get_by_role("button", name="3 · 게시와 token", exact=True).click()
    assert workflow.input_value() == original
    assert [(method, path) for method, path in calls if method != "GET"] == [
        ("PUT", "/api/agent/maxis/credentials/dev")]


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
