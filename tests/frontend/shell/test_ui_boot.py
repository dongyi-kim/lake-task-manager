"""실제 브라우저에서 앱·Agent·MyTasks가 마운트되는지 확인한다.

문법적으로 올바른 JavaScript도 실행 중 예외로 Vue 마운트에 실패할 수 있으므로
브라우저 회귀를 유지한다. HTML 주석 백틱과 template div 균형 검사는
static_assets/test_integrity.py의 파일별 기계적 검사에서 함께 실행한다.
playwright/chromium이 없는 개발 환경에서는 브라우저 검사만 건너뛴다.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("JIRA_ENV", "mock")

from support.paths import STATIC_ROOT
from frontend.static_assets.support import template_literal

STATIC = STATIC_ROOT


@pytest.mark.parametrize("relative", [
    "components/agent/agentViewTemplate.js",
    "components/editor/commentEditorTemplate.js",
])
def test_extracted_component_templates_are_discoverable(relative: str):
    source = (STATIC / relative).read_text(encoding="utf-8")
    body = template_literal(source)
    assert body is not None and "<div" in body


# ── ① 브라우저 — 진짜로 뜨는가 ────────────────────────────────────────
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    uvicorn = pytest.importorskip("uvicorn")
    from app.main import app
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):                      # 최대 20초 — 첫 기동은 색인 로드가 있다
        if getattr(server, "started", False):
            break
        time.sleep(0.1)
    if not getattr(server, "started", False):
        pytest.skip("테스트용 서버가 뜨지 않았다")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=15)


def test_the_app_actually_mounts_in_a_browser(live_server):
    """`window.__lakeUp` 이 서는가 = Vue 가 마운트했는가.

    실측 사고: 마운트 실패는 서버 로그에도, 파이썬 테스트에도, 파서에도 안 남는다.
    사용자 화면에만 '화면을 불러오지 못했습니다' 로 남는다.
    """
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="playwright 미설치 — 개발 의존성").sync_playwright
    errors: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as bundled_exc:      # noqa: BLE001 — 설치된 Chrome/Edge로 한 번 더 검증
            browser = None
            for executable in (
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            ):
                if not executable.exists():
                    continue
                try:
                    browser = p.chromium.launch(executable_path=str(executable))
                    break
                except Exception:             # noqa: BLE001 — 다음 설치 브라우저 시도
                    pass
            if browser is None:
                pytest.skip(f"chromium 없음: {str(bundled_exc)[:80]}")
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))
        try:
            page.goto(live_server + "/", wait_until="load", timeout=30000)
            try:
                page.wait_for_function("() => !!window.__lakeUp", timeout=15000)
                up = True
            except Exception:                 # noqa: BLE001 — 안 떴다는 사실만 쓴다
                up = False
            body = page.inner_text("body")[:200]
            page.goto(live_server + "/#/ai", wait_until="load", timeout=30000)
            try:
                page.locator(".agentview").wait_for(state="visible", timeout=15000)
                page.wait_for_timeout(500)       # prefs/status 응답 뒤 updated() 훅 오류까지 수집
                agent_up = True
            except Exception:                 # noqa: BLE001 — 외부 template import 회귀 판정
                agent_up = False
            agent_body = page.inner_text("body")[:200]
            page.goto(live_server + "/#/mytasks", wait_until="load", timeout=30000)
            try:
                page.locator(".mytasks").wait_for(state="visible", timeout=15000)
                page.locator(".mt-card").first.wait_for(state="visible", timeout=15000)
                mytasks_before_reload = page.locator(".mt-card").count()
                page.reload(wait_until="load", timeout=30000)
                page.locator(".mt-card").first.wait_for(state="visible", timeout=15000)
                mytasks_after_reload = page.locator(".mt-card").count()
                mytasks_up = True
            except Exception:                 # noqa: BLE001 — 실제 데이터 도착 후 렌더 예외 판정
                mytasks_up = False
                mytasks_before_reload = 0
                mytasks_after_reload = 0
            mytasks_body = page.inner_text("body")[:200]
            transforms = page.evaluate("""async () => {
              const mod = await import('/components/agent/contentTransforms.js');
              return {
                rich: mod.richEditorToText(
                  '<p>Hello <span data-type="mention" data-id="u1">@Alice</span> '
                  + '<a href="https://example.com">Doc</a></p>'),
                text: mod.draftDescriptionText('<h3>Scope</h3><p>Body</p>'),
                safe: mod.sanitizeDraftDescription(
                  '<p onclick="bad()">ok</p><script>bad()</script>'
                  + '<a href="javascript:bad()">x</a>'),
              };
            }""")
        finally:
            browser.close()
    assert up, (f"앱이 마운트되지 않았다. 화면: {body!r}\n"
                f"페이지 오류: {errors[:2]}")
    assert agent_up, (f"Agent 화면이 마운트되지 않았다. 화면: {agent_body!r}\n"
                      f"페이지 오류: {errors[:2]}")
    assert mytasks_up, (f"My Tasks가 새로고침 후 렌더되지 않았다. 화면: {mytasks_body!r}\n"
                        f"페이지 오류: {errors[:4]}")
    assert mytasks_before_reload > 0 and mytasks_after_reload > 0
    assert "@Alice(u1)" in transforms["rich"]
    assert "[Doc](https://example.com)" in transforms["rich"]
    assert "■ Scope" in transforms["text"]
    assert "script" not in transforms["safe"].lower()
    assert "onclick" not in transforms["safe"].lower()
    assert "javascript:" not in transforms["safe"].lower()
    assert not errors, f"콘솔 페이지 오류: {errors[:2]}"
