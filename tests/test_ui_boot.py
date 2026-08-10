"""앱이 **실제 브라우저에서 뜨는가**. 정적 검사로는 절대 대체되지 않는 자리다.

왜 생겼나 (실측 사고, 2026-08-10 — 사용자 제보 "앱 시작하면 '화면을 불러오지 못했습니다'
뜨고 앱이 안 뜬다"):

  Vue 컴포넌트의 template 은 JS 백틱 문자열이다. 그 안의 HTML 주석에 설명하려고
  백틱을 썼다 —  `.tkt` 뱃지는 …  — 그 순간 **문자열이 거기서 끝나고** 뒤가
  `"…".tkt(…)` 로 읽혔다. 결과는 `TypeError: … .tkt is not a function`,
  Vue 는 마운트조차 못 하고 화면이 통째로 비었다.

  그런데 **문법은 성립한다.** esprima 파서(test_static_assets.test_javascript_parses)는
  이 파일을 아무 불평 없이 통과시켰다 — 끊긴 문자열 + 프로퍼티 접근은 합법적인 JS다.
  깨지는 것은 파싱이 아니라 **실행**이고, 실행을 보는 것은 브라우저뿐이다.

  같은 자리에 이미 "이 주석에 백틱을 쓰지 마라"는 경고가 **1067줄에 있었다**. 경고는
  읽는 사람에게만 걸린다. 그래서 코드가 걸도록 옮긴다 — 아래 두 검사가 그것이다.

검사 둘:
  ① 브라우저 기동 — 실제 Chromium 으로 `/` 를 열어 `window.__lakeUp` 이 서는지 본다.
     이 한 줄이 "마운트했는가"의 정의다(index.html 의 자가복구 스크립트와 같은 판정).
  ② 백틱 금지 — template 안 HTML 주석에 **날 백틱**이 있으면 그 자리에서 막는다.
     ①이 못 도는 환경(플레이라이트·크로미움 없음)에서도 이 부류는 잡힌다.

playwright/chromium 이 없으면 ①은 건너뛴다 — 개발 의존성이지 실행 의존성이 아니다.
"""
from __future__ import annotations

import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


# ── ② 정적 — 날 백틱은 template 을 그 자리에서 끊는다 ────────────────────
@pytest.mark.parametrize(
    "path", sorted(p for p in STATIC.rglob("*.js") if "vendor" not in p.parts),
    ids=lambda p: str(p.relative_to(STATIC.parent.parent)))
def test_no_raw_backtick_inside_html_comments(path: Path):
    """HTML 주석 안의 날 백틱 금지 — 설명하려다 template 을 끊는다(실측 사고).

    쓰고 싶으면 이스케이프(\\`)한다. 이미 그렇게 쓰인 자리가 있고(1067줄), 그 옆에서
    같은 실수가 났다 — 사람은 옆줄의 경고를 안 읽는다.
    """
    src = path.read_text(encoding="utf-8")
    bad = []
    for m in re.finditer(r"<!--.*?-->", src, re.S):
        body = m.group(0)
        # 이스케이프된 백틱(\`)은 문자열을 안 끊는다 — 지우고 나서 본다.
        if "`" in body.replace("\\`", ""):
            bad.append((src[:m.start()].count("\n") + 1, body[:70].replace("\n", " ")))
    assert not bad, (f"{path.name}: HTML 주석 안 날 백틱 — template 이 여기서 끊긴다. "
                     f"이스케이프하거나 빼라: {bad[:3]}")


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
        except Exception as e:                # noqa: BLE001 — 크로미움 미설치도 스킵
            pytest.skip(f"chromium 없음: {str(e)[:80]}")
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
        finally:
            browser.close()
    assert up, (f"앱이 마운트되지 않았다. 화면: {body!r}\n"
                f"페이지 오류: {errors[:2]}")
    assert not errors, f"콘솔 페이지 오류: {errors[:2]}"
