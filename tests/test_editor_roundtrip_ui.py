"""TipTap 에디터의 실제 브라우저 왕복 회귀 테스트.

정적 문자열 검사나 html_to_wiki 단위 테스트만으로는 다음 실측 회귀를 잡지 못했다.

* 붙여넣은 이미지가 최초 게시에는 보이지만 수정 진입에서 깨짐
* 멘션이 수정 진입에서 회색 평문이 되고 재게시 뒤 파란 링크로 바뀜
* async 멘션 검색 중 이전 기본 추천이 남고 Escape 뒤 ``사용자 없음`` 팝업이 고착
* TipTap 업그레이드 뒤 표·체크박스·콜아웃처럼 schema가 있는 노드가 수정 시 사라짐

기존 DL-9001(본문)·DL-9007(댓글)을 사용한다. 앱은 테스트별 별도 프로세스로 띄우므로
본문·댓글·첨부 mutation은 프로세스 종료와 함께 사라지고 다음 테스트에서는 world가 초기화된다.
Playwright/Chromium이 없는 최소 CI 환경에서는 기존 test_ui_boot와 같은 정책으로 skip한다.
"""
from __future__ import annotations

import base64
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTION_TICKET = "DL-9001"
COMMENT_TICKET = "DL-9007"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

FORMAT_SELECTORS = {
    "h1": "h1",
    "h2": "h2",
    "h3": "h3",
    "h4": "h4",
    "bold": "strong",
    "italic": "em",
    "strike": "s, del, strike",
    "inline_code": "p code",
    "bullet": "ul:not([data-type='taskList']) > li",
    "ordered": "ol > li",
    "task": "input[type='checkbox']",
    "quote": "blockquote",
    "code_block": "pre code",
    "table_header": "table th",
    "table_cell": "table td",
    "info": ".callout-info",
    "note": ".callout-note",
    "tip": ".callout-tip",
    "success": ".callout-success",
    "warning": ".callout-warning",
    "error": ".callout-error",
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, proc: subprocess.Popen, timeout: float = 30) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"UI test server exited early ({proc.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:  # noqa: S310 — localhost fixture
                if response.status == 200:
                    return
        except Exception:  # noqa: BLE001 — 기동 중 연결 거부는 정상
            time.sleep(0.1)
    raise RuntimeError("UI test server did not become healthy")


@pytest.fixture()
def editor_browser(request, tmp_path):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="playwright 미설치 — 브라우저 UI 개발 테스트")
    # 대부분의 에디터 회귀는 지연과 무관하므로 기본 0ms. 응답 역전·검색 중 피드백처럼
    # 시간창 자체가 검증 대상인 테스트만 indirect parametrization으로 지연을 명시한다.
    latency_ms = int(getattr(request, "param", 0))
    port = _free_port()
    run_dir = tmp_path / "editor-roundtrip-ui"
    run_dir.mkdir()
    upload_path = run_dir / "file-picker.png"
    upload_path.write_bytes(PNG_1X1)
    env = os.environ.copy()
    env.update({
        "JIRA_ENV": "mock",
        "LAKE_NO_WINDOW": "1",
        "LAKE_MOCK_LATENCY_MS": str(latency_ms),
        "CACHE_DB_PATH": str(run_dir / "cache.sqlite3"),
    })
    log_path = run_dir / "server.log"
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    browser = None
    try:
        _wait_http(f"http://127.0.0.1:{port}/api/health", proc)
        with sync_playwright.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as bundled_exc:  # noqa: BLE001 — 번들 Chromium이 없으면 설치 브라우저 폴백
                browser = None
                for executable in (
                    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                ):
                    if not executable.exists():
                        continue
                    try:
                        browser = playwright.chromium.launch(executable_path=str(executable))
                        break
                    except Exception:  # noqa: BLE001 — 다음 설치 브라우저 시도
                        pass
                if browser is None:
                    pytest.skip(f"chromium 없음: {str(bundled_exc)[:120]}")
            page = browser.new_page(viewport={"width": 1720, "height": 1080})
            page.set_default_timeout(20_000)
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)[:400]))
            page.on("response", lambda response: errors.append(
                f"HTTP {response.status} {response.url}")
                if (response.status >= 400 and "/api/" in response.url
                    and "/api/favicon" not in response.url) else None)
            try:
                yield page, f"http://127.0.0.1:{port}", errors, upload_path
            finally:
                if browser is not None:
                    browser.close()
                    browser = None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        log.close()


def _open_ticket(page, base: str, key: str) -> None:
    page.goto(f"{base}/browse/{key}?ui-roundtrip=1", wait_until="load", timeout=45_000)
    page.wait_for_function("() => !!window.__lakeUp", timeout=20_000)
    page.get_by_text(key, exact=True).first.wait_for(state="visible")
    page.locator(".tkt-main").wait_for(state="visible")


def _rich_html(prefix: str, *, section: bool) -> str:
    divider = (f'<div class="sec-title-node">{prefix}-SECTION</div>'
               f'<p>{prefix}-SECTION-BODY</p>') if section else ""
    return "".join([
        f"<h1>{prefix}-H1</h1><h2>{prefix}-H2</h2>",
        f"<h3>{prefix}-H3</h3><h4>{prefix}-H4</h4>",
        f"<p>{prefix}-PLAIN <strong>{prefix}-BOLD</strong> ",
        f"<em>{prefix}-ITALIC</em> <s>{prefix}-STRIKE</s> ",
        f"<code>{prefix}-INLINE-count(*)</code></p>",
        f'<p style="text-align:center"><span style="font-family:serif;color:#dc2626;',
        f'background-color:#fef08a">{prefix}-STYLE</span></p>',
        f"<ul><li>{prefix}-BULLET</li></ul><ol><li>{prefix}-ORDERED</li></ol>",
        '<ul data-type="taskList">'
        '<li data-type="taskItem" data-checked="true"><label><input type="checkbox" checked></label>'
        f"<div>{prefix}-TASK-DONE</div></li>"
        '<li data-type="taskItem" data-checked="false"><label><input type="checkbox"></label>'
        f"<div>{prefix}-TASK-TODO</div></li></ul>",
        f"<blockquote><p>{prefix}-QUOTE</p></blockquote>",
        f'<pre><code class="language-sql">{prefix}-SQL select count(*) from dual</code></pre>',
        f'<div class="callout callout-info"><p>{prefix}-INFO</p></div>',
        f'<div class="callout callout-note"><p>{prefix}-NOTE</p></div>',
        f'<div class="callout callout-tip"><p>{prefix}-TIP</p></div>',
        f'<div class="callout callout-success"><p>{prefix}-SUCCESS</p></div>',
        f'<div class="callout callout-warning"><p>{prefix}-WARNING</p></div>',
        f'<div class="callout callout-error"><p>{prefix}-ERROR</p></div>',
        '<table><tbody><tr><th>종류</th><th>값</th></tr><tr>'
        f"<td>{prefix}-TABLE</td><td><code>{prefix}-CELL-CODE</code></td></tr></tbody></table>",
        f'<p><a href="https://example.com/{prefix.lower()}">{prefix}-WEB-LINK</a> '
        f'<a href="/browse/DL-9001">{prefix}-JIRA-LINK</a> '
        '<span class="mention" data-type="mention" data-id="test.ui02" '
        f'data-label="UI픽스처02">@UI픽스처02</span> {prefix}-MENTION</p>',
        divider,
    ])


def _replace_with_rich_content(page, editor, prefix: str, *, section: bool,
                               image_name: str, upload_path: Path) -> None:
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    html = _rich_html(prefix, section=section)
    editor.evaluate(
        """(el, html) => {
          el.focus();
          const data = new DataTransfer();
          data.setData('text/html', html);
          data.setData('text/plain', el.textContent || 'rich content');
          el.dispatchEvent(new ClipboardEvent('paste', {
            bubbles: true, cancelable: true, clipboardData: data,
          }));
        }""",
        html,
    )
    editor.get_by_text(f"{prefix}-PLAIN", exact=False).wait_for()
    # 1) 실제 이미지 버튼 → OS file chooser 경로.
    shell = editor.locator("xpath=ancestor::div[contains(@class,'cmt-editor')][1]")
    with page.expect_file_chooser() as chooser_info:
        shell.locator("button[title='이미지']").click()
    chooser_info.value.set_files(str(upload_path))
    editor.locator("img[src^='blob:']").first.wait_for(state="visible")

    # 2) 클립보드 이미지 붙여넣기 경로.
    editor.evaluate(
        """(el, payload) => {
          el.focus();
          const bytes = Uint8Array.from(atob(payload.base64), c => c.charCodeAt(0));
          const data = new DataTransfer();
          data.items.add(new File([bytes], payload.name, {type: 'image/png'}));
          el.dispatchEvent(new ClipboardEvent('paste', {
            bubbles: true, cancelable: true, clipboardData: data,
          }));
        }""",
        {"base64": base64.b64encode(PNG_1X1).decode("ascii"), "name": "clipboard-" + image_name},
    )
    editor.locator("img[src^='blob:']").nth(1).wait_for(state="visible")

    # 3) 사용자가 파일을 에디터 위에 끌어 놓는 DragEvent 경로.
    drag_data = page.evaluate_handle(
        """payload => {
          const bytes = Uint8Array.from(atob(payload.base64), c => c.charCodeAt(0));
          const data = new DataTransfer();
          data.items.add(new File([bytes], payload.name, {type: 'image/png'}));
          return data;
        }""",
        {"base64": base64.b64encode(PNG_1X1).decode("ascii"), "name": "drag-" + image_name},
    )
    editor.dispatch_event("dragenter", {"dataTransfer": drag_data})
    editor.dispatch_event("dragover", {"dataTransfer": drag_data})
    editor.dispatch_event("drop", {"dataTransfer": drag_data})
    editor.locator("img[src^='blob:']").nth(2).wait_for(state="visible")
    _assert_editor_schema(editor, prefix, expect_blob=True, full_styles=True)


def _assert_editor_schema(editor, prefix: str, *, expect_blob: bool = False,
                          full_styles: bool = False) -> None:
    for name, selector in FORMAT_SELECTORS.items():
        assert editor.locator(selector).count() >= 1, f"editor lost {name}: {prefix}"
    assert editor.get_by_text(f"{prefix}-PLAIN", exact=False).count() == 1
    assert editor.locator("span[data-type='mention'][data-id='test.ui02']").count() == 1
    assert editor.locator("a[href='/browse/DL-9001']").count() == 1
    style = editor.evaluate(
        """(el, needle) => {
          const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
          let node;
          while ((node = walker.nextNode())) {
            if ((node.data || '').trim() !== needle) continue;
            const parent = node.parentElement;
            const computed = getComputedStyle(parent);
            return {attr: parent.getAttribute('style') || '', color: computed.color,
                    background: computed.backgroundColor, font: computed.fontFamily};
          }
          return null;
        }""",
        f"{prefix}-STYLE",
    )
    assert style is not None, editor.inner_html()[:12000]
    assert style["color"] == "rgb(220, 38, 38)"
    if full_styles:
        assert style["background"] == "rgb(254, 240, 138)"
        assert "serif" in style["font"].lower()
        assert "text-align" in (editor.get_by_text(f"{prefix}-STYLE", exact=True)
                                .locator("xpath=ancestor::p[1]").get_attribute("style") or "")
    if expect_blob:
        assert editor.locator("img[src^='blob:']").count() >= 3


def _assert_reopened_images(editor) -> None:
    images = editor.locator("img[src*='/api/img?u=']")
    assert images.count() >= 3, editor.inner_html()[-8000:]
    for index in range(images.count()):
        image = images.nth(index)
        image.evaluate(
            "img => new Promise(resolve => { if (img.complete) resolve(); "
            "else { img.onload=resolve; img.onerror=resolve; } })")
        assert image.evaluate("img => img.naturalWidth") > 0, f"reopened image {index} is broken"
    else:
        images = editor.locator("img[src*='/api/img?u=']")
        assert images.count() >= 3
        for index in range(images.count()):
            image = images.nth(index)
            image.wait_for(state="visible")
            image.evaluate("img => new Promise(resolve => { if (img.complete) resolve(); else { img.onload=resolve; img.onerror=resolve; } })")
            assert image.evaluate("img => img.naturalWidth") > 0, f"edit image {index} is broken"


def _assert_rendered(scope, prefix: str, *, expect_section: bool = False) -> None:
    for name, selector in FORMAT_SELECTORS.items():
        assert scope.locator(selector).count() >= 1, (
            f"render lost {name}: {prefix}\n{scope.inner_html()[:12000]}")
    assert scope.get_by_text(f"{prefix}-PLAIN", exact=False).count() >= 1
    assert scope.locator(".mention-badge, a.user-hover[href*='ViewProfile.jspa']").count() >= 1
    assert scope.locator("a[href='https://example.com/%s']" % prefix.lower()).count() == 1
    images = scope.locator("img[src*='/api/img?u=']")
    assert images.count() >= 3
    for index in range(images.count()):
        image = images.nth(index)
        image.evaluate("img => new Promise(resolve => { if (img.complete) resolve(); else { img.onload=resolve; img.onerror=resolve; } })")
        assert image.evaluate("img => img.naturalWidth") > 0, f"render image {index} is broken"
    for selector in ("table", "table th", "table td"):
        border = scope.locator(selector).first.evaluate(
            "el => { const s=getComputedStyle(el); return [s.borderTopWidth,s.borderTopStyle,s.borderTopColor]; }")
        assert border[0] != "0px" and border[1] != "none"
        assert border[2] not in ("transparent", "rgba(0, 0, 0, 0)")
    if expect_section:
        assert scope.locator(".tkt-desc-box p", has_text=f"{prefix}-SECTION-BODY").count() == 1


def _assert_mention_popup_lifecycle(page, editor) -> None:
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    editor.press_sequentially("@강", delay=180)
    popup = page.locator(".mention-popup")
    candidate = popup.locator(".mn-item", has_text="강수아")
    candidate.wait_for(state="visible", timeout=15_000)
    assert "정한울" not in popup.inner_text(), "stale recent user remained in final results"
    page.keyboard.press("Escape")
    popup.wait_for(state="detached", timeout=3_000)
    assert page.get_by_text("사용자 없음", exact=True).count() == 0

    # 실제 검색 결과를 선택해 TipTap mention 노드가 생성되는 경로도 거친다.
    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    editor.press_sequentially("@강수아", delay=120)
    candidate = page.locator(".mention-popup .mn-item", has_text="강수아")
    candidate.wait_for(state="visible", timeout=15_000)
    candidate.click()
    assert editor.locator("span[data-type='mention'][data-id='skcc.i2088']").count() == 1


def _append_second_publish(page, editor, marker: str) -> None:
    editor.click()
    page.keyboard.press("Control+End")
    page.keyboard.press("Enter")
    page.keyboard.type(marker)


def test_description_and_comment_all_formats_survive_two_ui_publish_cycles(editor_browser):
    page, base, errors, upload_path = editor_browser
    _open_ticket(page, base, DESCRIPTION_TICKET)

    # ── 본문: 작성 → 제출 → 렌더 → 수정 → 제출 → 렌더 ──
    page.locator("button.sec-edit").first.click()
    desc_editor = page.locator(".tkt-desc-edit .ProseMirror")
    desc_editor.wait_for(state="visible")
    _assert_mention_popup_lifecycle(page, desc_editor)
    _replace_with_rich_content(page, desc_editor, "DESC", section=True,
                               image_name="description-roundtrip.png", upload_path=upload_path)
    page.locator(".tkt-desc-edit-f button.primary").click()
    page.locator(".tkt-desc-edit").wait_for(state="detached", timeout=20_000)
    page.get_by_text("DESC-PLAIN", exact=False).first.wait_for()
    _assert_rendered(page.locator(".tkt-main"), "DESC", expect_section=True)

    page.locator("button.sec-edit").first.click()
    desc_editor = page.locator(".tkt-desc-edit .ProseMirror")
    desc_editor.wait_for(state="visible")
    _assert_editor_schema(desc_editor, "DESC")
    _assert_reopened_images(desc_editor)
    _append_second_publish(page, desc_editor, "DESC-SECOND-PUBLISH")
    page.locator(".tkt-desc-edit-f button.primary").click()
    page.locator(".tkt-desc-edit").wait_for(state="detached", timeout=20_000)
    page.get_by_text("DESC-SECOND-PUBLISH", exact=False).first.wait_for()
    _assert_rendered(page.locator(".tkt-main"), "DESC", expect_section=True)

    # ── 댓글: 작성 → 제출 → 렌더 → 수정 → 제출 → 렌더 ──
    _open_ticket(page, base, COMMENT_TICKET)
    page.get_by_role("button", name="＋ 댓글 달기").click()
    comment_editor = page.locator(".tkt-compose-editor .ProseMirror")
    comment_editor.wait_for(state="visible")
    _assert_mention_popup_lifecycle(page, comment_editor)
    _replace_with_rich_content(page, comment_editor, "COMMENT", section=False,
                               image_name="comment-roundtrip.png", upload_path=upload_path)
    page.locator(".tkt-compose-editor button.cmt-ed-btn.primary").click()
    page.locator(".tkt-compose-editor").wait_for(state="detached", timeout=20_000)
    comment_body = page.locator(".tkt-cmt-b", has_text="COMMENT-PLAIN")
    comment_body.wait_for(state="visible")
    _assert_rendered(comment_body, "COMMENT")

    comment_row = page.locator(".tkt-cmt", has=comment_body)
    comment_row.get_by_role("button", name="수정", exact=True).click()
    # 본문이 에디터로 교체되면 `has=comment_body` 조건은 더 이상 성립하지 않는다.
    comment_editor = page.locator(".tkt-comments .tkt-cmt .ProseMirror")
    page.wait_for_function(
        "() => !!document.querySelector('.tkt-cmt .ProseMirror') || "
        "!!document.querySelector('.tkt-cmt-err')?.textContent.trim()",
        timeout=45_000,
    )
    assert comment_editor.is_visible(), (
        f"comment editor did not open; errors={errors}; "
        f"ui={page.locator('.tkt-cmt-err').all_inner_texts()}")
    _assert_editor_schema(comment_editor, "COMMENT")
    _assert_reopened_images(comment_editor)
    _append_second_publish(page, comment_editor, "COMMENT-SECOND-PUBLISH")
    page.locator(".tkt-comments .tkt-cmt .cmt-ed-bar > button.primary").click()
    comment_editor.wait_for(state="detached", timeout=20_000)
    comment_body = page.locator(".tkt-cmt-b", has_text="COMMENT-SECOND-PUBLISH")
    comment_body.wait_for(state="visible")
    _assert_rendered(comment_body, "COMMENT")

    assert not errors, f"browser page errors: {errors}"


def test_existing_editor_regression_fixtures_render_in_browser(editor_browser):
    page, base, errors, _upload_path = editor_browser
    fixtures = [
        ("DL-9001", ("h2", "table", "pre code", "blockquote", ".callout-info", "img", ".mention-badge")),
        ("DL-9002", ("h1", "h2", "h3", "h4")),
        ("DL-9004", (".tkt-desc-box .jira-badge[data-key='DL-5005']",)),
        ("DL-9005", ("a[href*='confluence.corp.example']",)),
        ("DL-9006", (".fchip",)),
        ("DL-9007", (".tkt-cmt-b", ".mention-badge", ".tkt-cmt-b .jira-badge")),
        ("DL-9017", (".tkt-desc-box table", ".tkt-desc-box pre code", ".tkt-desc-box blockquote")),
        ("DL-9036", (".file-badge", ".fchip")),
    ]
    for key, checks in fixtures:
        errors.clear()
        _open_ticket(page, base, key)
        for selector in checks:
            page.locator(selector).first.wait_for(state="visible", timeout=15_000)
        assert not errors, f"{key} browser page errors: {errors}"


def test_comment_composer_uses_compact_scoped_height(editor_browser):
    """지연과 무관한 높이·폴딩 동작은 기본 0ms fixture에서 검증한다."""
    page, base, errors, _upload_path = editor_browser
    page.add_init_script(
        "localStorage.setItem('cmtEditorH', '700'); "
        "localStorage.removeItem('cmtEditorComposeH');")
    _open_ticket(page, base, COMMENT_TICKET)
    page.get_by_role("button", name="＋ 댓글 달기").click()

    editor = page.locator(".tkt-compose-editor .ProseMirror")
    host = page.locator(".tkt-compose-editor .cmt-ed-host")
    handle = page.get_by_role("separator", name="댓글 작성창 높이 조절")
    editor.wait_for(state="visible")
    handle.wait_for(state="visible")
    initial = host.evaluate("el => el.getBoundingClientRect().height")
    assert 175 <= initial <= 185, f"compact comment height expected 180px, got {initial}"
    assert page.evaluate("localStorage.getItem('cmtEditorH')") == "700"

    editor.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("댓글 높이 조절 초안")
    box = handle.bounding_box()
    assert box is not None
    # 중앙은 가리기 버튼이 경계 위에 겹친다. 실제 사용자가 잡는 좌측 경계 띠를 끈다.
    drag_x = box["x"] + min(80, box["width"] / 4)
    page.mouse.move(drag_x, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(drag_x, box["y"] - 80, steps=8)
    page.mouse.up()
    resized = host.evaluate("el => el.getBoundingClientRect().height")
    assert resized >= initial + 60, f"top resize did not grow editor: {initial} -> {resized}"
    assert int(page.evaluate("localStorage.getItem('cmtEditorComposeH')")) == round(resized)
    assert page.evaluate("localStorage.getItem('cmtEditorH')") == "700"

    page.get_by_role("button", name="댓글 작성창 가리기").click()
    folded = page.locator("button.tkt-cmt-addbtn.draft")
    folded.wait_for(state="visible")
    assert "댓글 높이 조절 초안" in folded.inner_text()
    folded.click()
    assert "댓글 높이 조절 초안" in editor.inner_text()

    reset_box = handle.bounding_box()
    assert reset_box is not None
    reset_x = reset_box["x"] + min(80, reset_box["width"] / 4)
    page.mouse.dblclick(reset_x, reset_box["y"] + reset_box["height"] / 2)
    reset = host.evaluate("el => el.getBoundingClientRect().height")
    assert 175 <= reset <= 185
    assert page.evaluate("localStorage.getItem('cmtEditorComposeH')") is None
    assert not errors, f"browser page errors: {errors}"


@pytest.mark.parametrize("editor_browser", [800], indirect=True)
def test_comment_mention_search_enriches_context_name_under_delay(editor_browser):
    """검색 중 피드백과 local→server 이름 보강만 800ms 지연을 주어 검증한다."""
    page, base, errors, _upload_path = editor_browser
    _open_ticket(page, base, COMMENT_TICKET)
    page.get_by_role("button", name="＋ 댓글 달기").click()
    editor = page.locator(".tkt-compose-editor .ProseMirror")
    editor.wait_for(state="visible")

    # 이 티켓 담당자는 로컬 후보에서 짧은 이름만 가진다. 검색 중에는 이전 후보를 숨기고,
    # 기존 검색 응답이 오면 같은 id의 full displayName으로 보강한다(별도 상세 호출 없음).
    editor.press_sequentially("@UI픽스처02", delay=120)
    page.get_by_text("사용자 검색 중…", exact=True).wait_for(state="visible", timeout=3_000)
    popup = page.locator(".mention-popup")
    assert popup.locator(".mn-item").count() == 0
    candidate = popup.locator(".mn-item", has_text="UI픽스처02")
    candidate.wait_for(state="visible", timeout=15_000)
    assert candidate.locator(".mn-nm").inner_text() == "UI픽스처02 TEST"
    page.keyboard.press("Escape")
    popup.wait_for(state="detached", timeout=3_000)
    assert page.get_by_text("사용자 없음", exact=True).count() == 0
    assert not errors, f"browser page errors: {errors}"


@pytest.mark.parametrize("editor_browser", [800], indirect=True)
def test_field_pickers_show_none_context_and_recent_items_before_delayed_results(editor_browser):
    """800ms Jira 지연 중에도 없음·티켓 관련자·최근 Epic은 첫 프레임에 보인다."""
    page, base, errors, _upload_path = editor_browser
    _open_ticket(page, base, "DL-9000")             # 최근 Epic을 UI 동작으로 기록
    _open_ticket(page, base, DESCRIPTION_TICKET)     # 최근 Task + 소속 Epic을 UI 동작으로 기록

    assignee = page.locator("button[title='담당자 수정']")
    assignee.wait_for(state="visible", timeout=30_000)
    started = time.monotonic()
    assignee.click()
    people = page.locator(".fe-pop.users")
    people.get_by_text("없음", exact=True).wait_for(state="visible", timeout=500)
    people.get_by_text("UI픽스처01 TEST", exact=True).wait_for(state="visible", timeout=500)
    people.get_by_text("없음", exact=True).click()
    people.wait_for(state="detached", timeout=500)       # 800ms 저장 요청보다 먼저 닫혀야 한다
    assert time.monotonic() - started < 0.75

    epic = page.locator("button[title='소속 Epic 수정']")
    epic.wait_for(state="visible")
    started = time.monotonic()
    epic.click()
    epic_popup = page.locator(".fe-pop.wide")
    epic_popup.get_by_text("없음", exact=True).wait_for(state="visible", timeout=500)
    epic_popup.get_by_text("DL-9000", exact=True).wait_for(state="visible", timeout=500)
    epic_popup.get_by_text("없음", exact=True).click()
    epic_popup.wait_for(state="detached", timeout=500)
    assert time.monotonic() - started < 0.75

    # 생성창 상위 Epic 선택도 서버 options보다 `Epic 없음`과 최근 Epic을 먼저 그린다.
    page.get_by_role("button", name="티켓 추가").click()
    page.get_by_role("button", name=re.compile(r"^Task 추가하기")).click()
    candidates = page.locator(".nk-cands")
    started = time.monotonic()
    candidates.get_by_text("Epic 없음", exact=True).wait_for(state="visible", timeout=500)
    candidates.get_by_text("DL-9000", exact=True).wait_for(state="visible", timeout=500)
    candidates.get_by_text("Epic 없음", exact=True).click()
    type_trigger = page.locator("button[title='티켓 타입 수정']")
    type_trigger.wait_for(state="visible", timeout=500)
    type_trigger.click()
    page.locator(".fe-pop .fe-i", has_text="Task").first.wait_for(state="visible", timeout=500)
    assert time.monotonic() - started < 0.75
    assert not errors, f"browser page errors: {errors}"
