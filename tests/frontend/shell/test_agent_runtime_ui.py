"""Rendered feature readiness with no live LLM/API or development server."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from support.paths import STATIC_ROOT


@pytest.mark.parametrize("surface", ["agent", "editor"])
def test_maxis_feature_readiness_ignores_missing_local_llm_and_refreshes(tmp_path, surface):
    # Browser plugin/browser skill is absent; use the installed Playwright runtime.
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    import_line = ("import View from '/static/components/views/AgentView.js';"
                   if surface == "agent" else
                   "import View from '/static/components/ui/CommentEditor.js';")
    setup = ("View.components={...View.components,CommentEditor:{template:'<div>업무 입력</div>'}};"
             "createApp(View).mount('#app');" if surface == "agent" else
             "View.mounted=function(){this.ready=true;};"
             "createApp(View,{ticketKey:'__new__',submitFn:async()=>{}}).mount('#app');")
    markup = ("<!doctype html><html><head><title>LTM MAXIS runtime fixture</title>"
              "<link rel='stylesheet' href='/static/styles/tokens.css'>"
              "<link rel='stylesheet' href='/static/styles/base.css'>"
              "<link rel='stylesheet' href='/static/styles/components.css'>"
              "<link rel='stylesheet' href='/static/styles/agent.css'></head>"
              "<body><div id='app'></div><script type='module'>"
              "import {createApp} from '/static/vendor/vue.esm-browser.prod.js';"
              + import_line + setup + "</script></body></html>")
    errors = []
    state = {"ready": True, "hold": surface == "editor"}
    held = []
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
                pytest.skip("Installed Chromium is unavailable")
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.set_default_timeout(10000)
        page.on("pageerror", lambda error: errors.append(str(error)))

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
            if path == "/api/agent/status" and state["hold"]:
                held.append(route)
                return
            feature = {"engine": "maxis", "profile": "prod", "ready": state["ready"],
                       "reason": "" if state["ready"] else "MAXIS 연결을 다시 확인하세요.",
                       "reasonCode": "" if state["ready"] else "maxis_runtime_unavailable",
                       "nextAction": "maxis_settings"}
            data = ({"agentEnabled": True} if path == "/api/prefs" else
                    {"llmReady": False, "llmReason": "로컬 LLM 없음",
                     "runtime": {"conversation": feature, "editor": feature}}
                    if path == "/api/agent/status" else {})
            route.fulfill(content_type="application/json", body=json.dumps(data))

        page.route("http://127.0.0.1:4457/**", respond)
        page.goto("http://127.0.0.1:4457/")
        assert page.title() == "LTM MAXIS runtime fixture"
        if surface == "agent":
            page.locator(".agent-input-rich").wait_for()
            assert "MAXIS / Dify" in page.locator(".agent-prov").inner_text()
            assert not page.locator(".agent-llmoff").count()
            page.screenshot(path=str(tmp_path / "maxis-agent-ready.png"))
            page.get_by_role("button", name="⚙ 설정", exact=True).click()
            state["ready"] = False
            page.locator(".ag-dlg .ag-x").click()
            page.get_by_text("MAXIS 연결을 다시 확인하세요.", exact=False).wait_for()
            assert not page.locator(".agent-input-rich").count()
        else:
            page.locator(".tb-ai").click()
            page.locator(".ai-in").wait_for()
            page.get_by_text("연결 확인 중…", exact=True).wait_for()
            assert page.get_by_role("button", name="생성", exact=True).is_disabled()
            page.locator(".ai-in").fill("초안 작성")
            page.locator(".ai-in").press("Enter")
            assert not page.locator(".ai-err").count()
            page.keyboard.press("Escape")
            page.locator(".tb-ai").click()
            page.get_by_text("연결 확인 중…", exact=True).wait_for()
            state["hold"] = False
            assert len(held) == 2
            respond(held[1])
            from playwright.sync_api import expect
            expect(page.get_by_role("button", name="생성", exact=True)).to_be_enabled()
            state["ready"] = False
            respond(held[0])
            page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
            expect(page.get_by_role("button", name="생성", exact=True)).to_be_enabled()
            assert not page.locator(".ai-err").count()
            page.screenshot(path=str(tmp_path / "maxis-editor-ready.png"))
            page.keyboard.press("Escape")
            held.clear()
            state["hold"] = True
            page.locator(".tb-ai").click()
            page.get_by_text("연결 확인 중…", exact=True).wait_for()
            page.keyboard.press("Escape")
            page.locator(".tb-ai").click()
            page.get_by_text("연결 확인 중…", exact=True).wait_for()
            state["hold"] = False
            assert len(held) == 2
            respond(held[1])
            state["ready"] = True
            respond(held[0])
            page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
            page.get_by_text("MAXIS 연결을 다시 확인하세요.", exact=False).wait_for()
            assert not page.locator(".ai-in").count()
        assert "로컬 LLM 없음" not in page.locator("body").inner_text()
        assert not errors, errors
        browser.close()
