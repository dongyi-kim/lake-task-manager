"""Actual management forms and SettingsMenu in Chromium; no production config or network.

Browser plugin not available in the authoring environment; reuse the local static fixture.
"""
import copy
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from frontend.shell.test_local_mcp_ui import static_server  # noqa: F401
from support.browser import launch_chromium


@pytest.fixture
def admin_ui(static_server):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as playwright:
        browser = launch_chromium(playwright)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors, requests, held = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text)
                if message.type == "error" and not any(code in message.text for code in
                   ("status of 403", "status of 409", "status of 503")) else None)
        state = {"manager": False, "me_calls": 0, "hold_me": False, "me_held": [],
                 "health": {}, "api_calls": [], "hold_auth": False, "auth_held": [],
                 "fail": None, "hold": False, "config": {
            "revision": "1", "projectKey": "DL", "tasks": [{"id": "workbench-1", "module": "Workbench",
                "name": "쿼리 워크스페이스", "start": "2026-01-01", "end": "2026-12-31",
                "epics": [{"key": "DL-110", "weight": 7}, {"key": "DL-109", "weight": 3}]}],
            "people": {"Workbench": ["skcc.x1402", "skcc.x1450"]}}}

        def api(route):
            request = route.request
            path = urlsplit(request.url).path
            state["api_calls"].append(path)
            if path.startswith("/api/workspace/config"):
                body = request.post_data_json if request.post_data else None
                requests.append((request.method, path, body, request.headers))
                if state["hold"]:
                    held.append(route)
                elif state["fail"]:
                    code, message = state["fail"]
                    route.fulfill(status=code, json={"detail": message})
                elif request.method == "PUT":
                    assert request.headers.get("x-ltm-local") == "1"
                    assert request.headers.get("content-type") == "application/json"
                    assert body["revision"] == state["config"]["revision"]
                    kind = path.rsplit("/", 1)[-1]
                    field = "tasks" if kind == "wbs" else "people"
                    assert set(body) == {"revision", field}  # Never save the other tab implicitly.
                    state["config"][field] = copy.deepcopy(body[field])
                    state["config"]["revision"] = str(int(state["config"]["revision"]) + 1)
                    route.fulfill(json=state["config"])
                else:
                    route.fulfill(json=state["config"])
            elif path == "/api/me":
                state["me_calls"] += 1
                if state["hold_me"]:
                    state["me_held"].append(route)
                else:
                    route.fulfill(json={"id": "fixture-user", "known": True, "manager": state["manager"]})
            elif path == "/api/prefs":
                route.fulfill(json={"agentEnabled": False, "bitbucketConfigured": False})
            elif path == "/api/health":
                route.fulfill(json=state["health"])
            elif path == "/api/auth/probe":
                if state["hold_auth"]:
                    state["auth_held"].append(route)
                else:
                    route.fulfill(json={"ok": False, "needLogin": True})
            elif path.startswith("/api/dev/sso/"):
                route.fulfill(json={"configured": False})
            else:
                route.fulfill(json={})

        page.route("**/api/**", api)
        page.route("**/fonts.css", lambda route: route.fulfill(content_type="text/css", body=""))
        page.route(static_server + "/", lambda route: route.fulfill(content_type="text/html", body="""
          <!doctype html><html lang="ko" data-theme="light"><head><meta charset="UTF-8">
          <meta name="viewport" content="width=device-width,initial-scale=1">
          <title>Lake Task Manager — 인력 · WBS 관리</title>
          <link rel="stylesheet" href="/styles/tokens.css"><link rel="stylesheet" href="/styles/base.css">
          <link rel="stylesheet" href="/styles/components.css"><link rel="stylesheet" href="/styles/workspace-admin.css">
          </head><body><div id="app" style="position:fixed;right:24px;top:24px"></div>
          <script type="module">
            import {createApp} from '/vendor/vue.esm-browser.prod.js';
            import SettingsMenu from '/components/ui/SettingsMenu.js';
            window.configEvents=[];
            window.addEventListener('workspace-config-updated', e => configEvents.push(e.detail));
            createApp(SettingsMenu).mount('#app');
          </script></body></html>"""))
        page.goto(static_server + "/")
        try:
            yield page, state, requests, held, pw.expect
            assert not errors, errors
        finally:
            browser.close()


def _open(page):
    page.get_by_role("button", name="설정", exact=True).click()
    page.get_by_role("button", name="인력 · WBS 관리 →", exact=True).click()
    page.get_by_label("Task 1 이름", exact=True).wait_for()


def _evidence(page, name):
    if os.environ.get("LTM_UI_EVIDENCE_DIR"):
        target = Path(os.environ["LTM_UI_EVIDENCE_DIR"])
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / name), full_page=False)


def test_wbs_and_people_save_independently_and_reopen(admin_ui, static_server):
    page, state, requests, _held, expect = admin_ui
    _open(page)
    assert page.url == static_server + "/" and "Lake Task Manager" in page.title()
    expect(page.get_by_role("dialog", name="인력 · WBS 관리")).to_be_visible()
    expect(page.get_by_role("button", name="WBS 저장", exact=True)).to_be_disabled()
    _evidence(page, "workspace-admin-desktop.png")
    page.get_by_label("Task 1 이름", exact=True).fill("새 WBS 이름")
    page.get_by_label("Task 1 Epic 1 가중치", exact=True).fill("4")
    page.get_by_role("button", name="Task 1 Epic 2 삭제", exact=True).click()
    page.get_by_role("button", name="인력", exact=True).click()
    page.get_by_label("모듈 1 사용자 ID", exact=True).fill("skcc.x1402\nskcc.x9999")
    page.get_by_role("button", name="인력 저장", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("인력 구성을 저장했습니다.")
    assert state["config"]["people"]["Workbench"] == ["skcc.x1402", "skcc.x9999"]
    assert state["config"]["tasks"][0]["name"] == "쿼리 워크스페이스"
    page.get_by_role("button", name="WBS Tasks · 미저장", exact=True).click()
    expect(page.get_by_label("Task 1 이름", exact=True)).to_have_value("새 WBS 이름")
    page.get_by_role("button", name="WBS 저장", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("WBS Task를 저장했습니다.")
    assert state["config"]["tasks"][0]["epics"] == [{"key": "DL-110", "weight": 4}]
    assert page.evaluate("configEvents") == [{"kind": "people", "revision": "2"}, {"kind": "wbs", "revision": "3"}]
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.get_by_role("button", name="설정", exact=True)).to_be_focused()
    _open(page)
    expect(page.get_by_label("Task 1 이름", exact=True)).to_have_value("새 WBS 이름")
    assert len([row for row in requests if row[0] == "PUT"]) == 2


def test_new_task_and_module_forms_validate_and_preserve_failed_writes(admin_ui):
    page, state, requests, _held, expect = admin_ui
    _open(page)
    page.get_by_role("button", name="+ WBS Task 추가", exact=True).click()
    expect(page.get_by_label("Task 2 이름", exact=True)).to_be_focused()
    page.get_by_role("button", name="WBS 저장", exact=True).click()
    assert not [row for row in requests if row[0] == "PUT"]  # Required inputs use native form validation.
    page.get_by_label("Task 2 이름", exact=True).fill("신규 계획")
    page.get_by_label("Task 2 시작일", exact=True).fill("2026-04-01")
    page.get_by_label("Task 2 종료일", exact=True).fill("2026-09-01")
    page.get_by_label("Task 2 Epic 1 키", exact=True).fill("DL-111")
    page.get_by_label("Task 2 Epic 1 가중치", exact=True).fill("0")
    page.get_by_role("button", name="WBS 저장", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Epic 가중치 합은 0보다 커야")
    assert not [row for row in requests if row[0] == "PUT"]
    page.get_by_label("Task 2 Epic 1 가중치", exact=True).fill("1")
    state["fail"] = (409, "다른 변경이 저장되었습니다. 입력은 유지됩니다.")
    page.get_by_role("button", name="WBS 저장", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text(state["fail"][1])
    expect(page.get_by_label("Task 2 이름", exact=True)).to_have_value("신규 계획")
    expect(page.get_by_role("button", name="입력 버리고 다시 불러오기", exact=True)).to_be_visible()
    state["fail"] = None
    page.get_by_role("button", name="WBS 저장", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("WBS Task를 저장했습니다.")
    assert state["config"]["tasks"][1]["id"].startswith("wbs-")
    page.get_by_role("button", name="인력", exact=True).click()
    page.get_by_role("button", name="+ 모듈 추가", exact=True).click()
    page.get_by_label("모듈 2 이름", exact=True).fill("Workbench")
    page.get_by_role("button", name="인력 저장", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("중복 등록")
    page.get_by_label("모듈 2 이름", exact=True).fill("New Module")
    page.get_by_label("모듈 2 사용자 ID", exact=True).fill("new.user\nother.user")
    state["fail"] = (503, "설정을 저장하지 못했습니다.")
    page.get_by_role("button", name="인력 저장", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text(state["fail"][1])
    expect(page.get_by_label("모듈 2 사용자 ID", exact=True)).to_have_value("new.user\nother.user")
    state["fail"] = None
    page.get_by_role("button", name="인력 저장", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("인력 구성을 저장했습니다.")
    assert state["config"]["people"]["New Module"] == ["new.user", "other.user"]


def test_deleting_final_task_updates_open_wbs_to_empty_state(admin_ui):
    from app.domain.rollup import build
    page, state, requests, _held, expect = admin_ui
    def dashboard(route):
        tasks = state["config"]["tasks"]
        plan = {"project_key": "DL", "modules": list(state["config"]["people"]), "wbs": tasks}
        route.fulfill(json=build(plan, {}))
    page.route("**/api/wbs", dashboard)
    page.evaluate("""async () => {
      const {createApp} = await import('/vendor/vue.esm-browser.prod.js');
      const Wbs = (await import('/components/views/WbsView.js')).default;
      const host=document.createElement('div'); host.id='wbs-test'; document.body.append(host);
      window.wbsTest=createApp(Wbs); window.wbsTest.mount(host);
    }""")
    expect(page.locator("#wbs-test .gantt")).to_be_visible()
    _open(page)
    page.get_by_role("button", name="Task 1 삭제", exact=True).click()
    page.get_by_role("button", name="WBS 저장", exact=True).click()
    expect(page.get_by_role("dialog").get_by_role("status")).to_have_text("WBS Task를 저장했습니다.")
    page.keyboard.press("Escape")
    expect(page.locator("#wbs-test .wbs-empty")).to_contain_text("등록된 WBS Task가 없습니다.")
    expect(page.locator("#wbs-test .gantt")).to_have_count(0)
    expect(page.locator("#wbs-test .err")).to_have_count(0)
    assert state["config"]["tasks"] == []
    assert next(row for row in requests if row[0] == "PUT")[2]["tasks"] == []


def test_local_navigation_and_settings_do_not_wait_for_jira_identity(admin_ui):
    page, state, requests, _held, expect = admin_ui
    state["hold_me"] = True
    routes = page.evaluate("""async () => {
      const { createApp } = await import('/vendor/vue.esm-browser.prod.js');
      const { default: Home } = await import('/components/views/HomeView.js');
      const { default: Root } = await import('/components/app-root.js');
      const { default: Ticket } = await import('/components/ui/TicketDialog.js');
      const host = document.createElement('main'); host.id = 'local-home'; document.body.append(host);
      createApp(Home).mount(host);
      return {
        tabs: Root.computed.tabs.call({manager:false}).map(t => t.k),
        wbs: Root.computed.view.call({route:'wbs', manager:false, allowed:false}).name,
        tools: Root.computed.view.call({route:'devtools', manager:false, allowed:false}).name,
        comments: Ticket.methods.canEdit.call({}, {id:'other-author', mine:false}),
      };
    }""")
    assert routes == {"tabs": ["ai", "wbs", "vit", "workload", "mytasks", "confluence"],
                      "wbs": "WbsView", "tools": "DevToolsView", "comments": True}
    expect(page.locator('#local-home a[href="#/wbs"]')).to_be_visible()
    expect(page.locator('#local-home a[href="#/workload"]')).to_be_visible()
    assert state["me_calls"] == 0  # Mounting home/settings no longer queries Jira to grant access.
    page.get_by_role("button", name="설정", exact=True).click()
    expect(page.get_by_role("button", name="인력 · WBS 관리 →", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="개발용 API 열기 →", exact=True)).to_be_visible()
    assert requests == []
    page.get_by_role("button", name="인력 · WBS 관리 →", exact=True).click()
    expect(page.get_by_label("Task 1 이름", exact=True)).to_have_value("쿼리 워크스페이스")
    assert state["me_calls"] == 0  # Even opening local settings needs no Jira identity request.


def test_no_jira_session_or_cache_never_blocks_local_shell_and_drafts(admin_ui):
    page, state, _requests, _held, expect = admin_ui
    state["health"] = {"needLogin": True, "hasCache": False, "env": "prod",
                       "jiraBase": "https://jira.local", "projectKey": "DL"}
    state["hold_auth"] = True
    page.evaluate("""async () => {
      document.querySelector('#app').remove();
      const {createApp} = await import('/vendor/vue.esm-browser.prod.js');
      const {default: Root} = await import('/components/app-root.js');
      const host=document.createElement('div'); host.id='local-shell'; document.body.append(host);
      window.localShell=createApp(Root); window.localShell.mount(host);
    }""")
    shell = page.locator("#local-shell")
    expect(shell.locator('.home a[href="#/wbs"]')).to_be_visible()
    expect(shell.get_by_role("button", name="티켓 추가", exact=True)).to_be_visible()
    shell.get_by_role("button", name="설정", exact=True).click()
    shell.get_by_role("button", name="인력 · WBS 관리 →", exact=True).click()
    expect(page.get_by_label("Task 1 이름", exact=True)).to_have_value("쿼리 워크스페이스")
    assert state["auth_held"]  # Local UI is live while silent renewal is still pending.
    assert "/api/login" not in state["api_calls"]
    assert state["me_calls"] == 0
    for route in state["auth_held"]:
        route.fulfill(json={"ok": False, "needLogin": True})
    state["auth_held"].clear()
    state["hold_auth"] = False
    expect(page.locator(".login-notice")).to_be_visible()
    assert page.locator(".login-ov").count() == 0
    assert "/api/login" not in state["api_calls"]
    page.get_by_label("Task 1 이름", exact=True).fill("인증 없이 로컬 수정")
    expect(page.get_by_label("Task 1 이름", exact=True)).to_have_value("인증 없이 로컬 수정")
    # A genuine rich editor remains usable while the same unauthenticated shell is mounted.
    page.evaluate("""async () => {
      const {createApp} = await import('/vendor/vue.esm-browser.prod.js');
      const {default: Editor} = await import('/components/ui/CommentEditor.js');
      const host=document.createElement('div'); host.id='local-draft'; document.body.append(host);
      window.localDraft=createApp(Editor,{draftKey:'offline-shell-draft',placeholder:'local draft'});
      window.localDraftVm=window.localDraft.mount(host);
    }""")
    editor = page.locator("#local-draft .ProseMirror[contenteditable=true]")
    editor.fill("Jira 세션과 무관한 로컬 초안")
    page.evaluate("window.localDraftVm.flushDraft()")
    expect(editor).to_have_text("Jira 세션과 무관한 로컬 초안")
    assert "/api/login" not in state["api_calls"]


def test_mobile_focus_and_closed_dialog_ignores_late_load(admin_ui):
    page, state, _requests, held, expect = admin_ui
    _open(page)
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    box = page.get_by_role("dialog").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert page.locator(".wa-body").evaluate("el => el.scrollWidth <= el.clientWidth")
    page.get_by_role("button", name="인력", exact=True).click()
    _evidence(page, "workspace-admin-mobile.png")
    page.get_by_role("button", name="닫기", exact=True).focus()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="인력 · WBS 관리 닫기", exact=True)).to_be_focused()
    state["hold"] = True
    page.get_by_role("button", name="다시 불러오기", exact=True).click()
    page.wait_for_function("document.querySelector('.wa-tabs button').disabled")
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert len(held) == 1
    held.pop().fulfill(json=state["config"])
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.get_by_role("button", name="설정", exact=True)).to_be_focused()
