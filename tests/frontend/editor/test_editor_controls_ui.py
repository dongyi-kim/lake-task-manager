"""Control-driven editor round trip. Only repetitive plain paragraphs are prepared by paste."""
import re
import struct
import zlib

import pytest

from frontend.editor.test_editor_roundtrip_ui import editor_browser, _open_ticket


SLASH = ["code", "table", "markdown_table", "checkbox", "quote", "info", "note", "tip",
         "success", "warning", "error", "jira", "confluence", "file"]
TITLES = ["굵게", "기울임", "취소선", "인라인 코드", "글자색", "배경색(형광펜)",
          "불릿", "번호", "체크박스(할 일)", "인용", "코드블록", "정보 콜아웃 {info}",
          "노트 콜아웃 {note}", "팁 콜아웃 {tip}", "경고 콜아웃 {warning}",
          "왼쪽 정렬", "가운데 정렬", "오른쪽 정렬", "글꼴", "이미지",
          "표 삽입 — 행·열 선택", "마크다운 표 붙여넣기 → 변환", "에디터 최대화"]
CALLOUT_KINDS = ("info", "note", "tip", "success", "warning", "error")
JIRA_CALLOUT = {"info": "info", "note": "grey", "tip": "idea", "success": "idea",
                "warning": "warning", "error": "warning"}


def _assert_jira_callout_storage(page, base, key, surface, marker, *, legacy=False):
    """Use actual upstream HTML, without LTM CSS, so a class-only export cannot pass."""
    response = page.request.get(f"{base}/__editor_test__/stored/{key}")
    assert response.ok, response.text()
    stored = response.json()
    source = stored["description"] if surface == "description" else next(
        comment["body"] for comment in stored["comments"] if marker in comment["body"])
    panels = page.evaluate("""html => {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      return [...doc.querySelectorAll('div')]
        .filter(el => [...el.classList].some(c => c.startsWith('jePanel_')))
        .map(el => el.outerHTML);
    }""", source)
    assert panels, source
    standalone = page.context.browser.new_page()
    try:
        standalone.set_content("<!doctype html><html><body>" + "".join(panels) + "</body></html>")
        assert standalone.locator("style, link[rel=stylesheet]").count() == 0
        for kind in CALLOUT_KINDS:
            native = standalone.locator(".callout-" + kind + ".jePanel_" + JIRA_CALLOUT[kind])
            assert native.count(), (kind, source)
        for panel in standalone.locator("body > div").all():
            assert panel.evaluate("""el => {
              const s = getComputedStyle(el);
              return el.style.border && el.style.padding &&
                parseFloat(s.borderTopWidth) > 0 && ['solid', 'dashed'].includes(s.borderTopStyle) &&
                s.borderTopColor !== 'rgba(0, 0, 0, 0)' &&
                parseFloat(s.paddingLeft) > 0 && parseFloat(s.paddingTop) > 0;
            }"""), panel.evaluate("el => el.outerHTML")
        assert standalone.locator(".jePanel_info").first.evaluate(
            "el => getComputedStyle(el).borderTopColor") == "rgb(158, 182, 212)"
        assert standalone.locator(".jePanel_info").first.evaluate(
            "el => { const s=getComputedStyle(el); return parseFloat(s.paddingLeft)>parseFloat(s.paddingRight); }")
        if legacy:
            assert standalone.locator(".jePanel_dashed", has_text="JIRA-dashed").count() == 1
            assert standalone.locator(".jePanel_grey", has_text="UNKNOWN-PRESERVED").count() == 1
    finally:
        standalone.close()


def _select(editor, text):
    target = editor.get_by_text(text, exact=True)
    target.scroll_into_view_if_needed()
    target.evaluate("""el => {
      el.closest('.ProseMirror').focus();
      const range = document.createRange(); range.selectNodeContents(el);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      document.dispatchEvent(new Event('selectionchange'));
    }""")
    assert editor.page.evaluate("window.getSelection().toString()") == text


def _button(shell, title):
    return shell.get_by_title(title, exact=True)


def _markdown_table(page, label):
    page.locator(".mdt-src").fill(f"| {label}-HEAD | value |\n| --- | --- |\n| {label}-CELL | body |")
    page.locator(".mdt-f").get_by_role("button", name="삽입", exact=True).click()


def _create_controls(page, editor, surface, upload):
    shell = editor.locator("xpath=ancestor::div[contains(@class,'cmt-editor')][1]")
    editor.wait_for(state="visible")
    inventory = shell.locator(".cmt-tb").first.locator("button[title]").evaluate_all("nodes => nodes.map(n => n.title)")
    assert set(TITLES).issubset(inventory)
    assert any(title.startswith("글 스타일") for title in inventory)
    assert any(title.startswith("링크 뱃지") for title in inventory)
    assert any(title.startswith("AI 자동완성") for title in inventory)
    assert len(inventory) == len(TITLES) + 3, inventory  # New controls require explicit coverage.
    slash_ids = SLASH + (["divider"] if surface == "description" else [])
    labels = ["PLAIN", "BOLD", "ITALIC", "STRIKE", "INLINE", "COLOR", "BG", "FONT", "CLEAR",
              "H1", "H2", "H3", "STYLE-QUOTE", "STYLE-CODE", "BULLET", "ORDERED", "TASK",
              "QUOTE", "CODE", "INFO", "NOTE", "TIP", "WARNING", "LEFT", "CENTER", "RIGHT",
              "TABLE", "DELETE-TABLE", "MD", "LINK", "MENTION", "IMAGE", "SHORT", "END"]
    labels += ["SLASH-" + command for command in slash_ids]
    plain = "".join("<p>UI-" + label + "</p>" for label in labels)
    editor.click()
    page.keyboard.press("Control+A")
    editor.evaluate("""(el, html) => {
      const dt = new DataTransfer(); dt.setData('text/html', html); dt.setData('text/plain','plain paragraphs');
      el.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:dt}));
    }""", plain)
    for label, title in [("BOLD", "굵게"), ("ITALIC", "기울임"), ("STRIKE", "취소선"), ("INLINE", "인라인 코드"),
                         ("BULLET", "불릿"), ("ORDERED", "번호"), ("TASK", "체크박스(할 일)"),
                         ("QUOTE", "인용"), ("CODE", "코드블록"), ("INFO", "정보 콜아웃 {info}"),
                         ("NOTE", "노트 콜아웃 {note}"), ("TIP", "팁 콜아웃 {tip}"), ("WARNING", "경고 콜아웃 {warning}"),
                         ("LEFT", "왼쪽 정렬"), ("CENTER", "가운데 정렬"), ("RIGHT", "오른쪽 정렬")]:
        _select(editor, "UI-" + label)
        _button(shell, title).click()
        if label == "CODE":
            shell.locator(".cmt-langsel").select_option("python")
    assert editor.locator("input[type=checkbox]").count(), editor.inner_html()
    editor.locator("input[type=checkbox]").first.check()
    for label, style in [("H1", "제목 1"), ("H2", "제목 2"), ("H3", "제목 3"),
                         ("STYLE-QUOTE", "인용"), ("STYLE-CODE", "코드 블록"),
                         ("CLEAR", "모든 스타일 제거"), ("PLAIN", "본문")]:
        _select(editor, "UI-" + label)
        shell.locator("button[title^='글 스타일']").click()
        shell.locator(".tb-style-i").filter(has=page.get_by_text(style, exact=True)).click()
    _select(editor, "UI-COLOR")
    _button(shell, "글자색").click()
    shell.locator(".tb-sw-pop").get_by_title("빨강", exact=True).click()
    _select(editor, "UI-BG")
    _button(shell, "배경색(형광펜)").click()
    shell.locator(".tb-sw-pop").get_by_title("노랑", exact=True).click()
    _select(editor, "UI-FONT")
    _button(shell, "글꼴").click()
    shell.get_by_text("코딩 글꼴(고정폭)", exact=True).click()
    _select(editor, "UI-CLEAR")
    _button(shell, "글꼴").click()
    shell.get_by_text("기본 글꼴", exact=True).click()
    _select(editor, "UI-LINK")
    shell.locator("button[title^='링크 뱃지']").click()
    page.locator(".badge-edit .be-h").fill("https://example.com/ui-controls")
    page.locator(".badge-edit .be-ok").click()
    _select(editor, "UI-MD")
    _button(shell, "마크다운 표 붙여넣기 → 변환").click()
    _markdown_table(page, "UI-MD")
    for label in ["TABLE", "DELETE-TABLE"]:
        _select(editor, "UI-" + label)
        _button(shell, "표 삽입 — 행·열 선택").click()
        shell.locator(".tp-cell").nth(9).click()  # 2x2
        if label == "DELETE-TABLE":
            _button(shell, "표 삭제").click()
        else:
            for title in ["왼쪽에 열 추가", "오른쪽에 열 추가", "열 삭제", "위에 행 추가", "아래에 행 추가", "행 삭제", "헤더 행 토글"]:
                _button(shell, title).click()
            page.keyboard.insert_text("UI-TABLE-CELL")
    _select(editor, "UI-MENTION")
    page.keyboard.insert_text("@")
    page.locator(".mention-popup .mn-item").first.click()
    _select(editor, "UI-SHORT")
    page.keyboard.insert_text("DL-9002 ")
    _select(editor, "UI-IMAGE")
    with page.expect_file_chooser() as chooser:
        _button(shell, "이미지").click()
    chooser.value.set_files(upload)
    image = editor.locator(".img-wrap img").last
    image.click()
    for title in ["작게 (폭 25%)", "보통 (폭 50%)", "크게 (폭 75%)", "가득 (폭 100%)", "원본 크기로"]:
        image.click()
        _button(shell, title).click()
    _button(shell, "에디터 최대화").click()
    shell.get_by_title("최대화 해제", exact=True).click()
    shell.locator("button[title^='AI 자동완성']").click()
    shell.locator(".ai-pop").get_by_role("button", name=re.compile("^(취소|닫기)$")).click()

    discovered = None
    for command in slash_ids:
        _select(editor, "UI-SLASH-" + command)
        page.keyboard.insert_text("/")
        popup = page.locator(".slash-popup")
        popup.locator(".sl-item").first.wait_for()
        if discovered is None:
            discovered = [text.removeprefix("/") for text in popup.locator(".sl-k").all_inner_texts()]
            assert set(discovered) == set(slash_ids)
        action = popup.locator(".sl-item").filter(has=page.get_by_text("/" + command, exact=True))
        if command == "file":
            with page.expect_file_chooser() as chooser:
                action.click()
            chooser.value.set_files(upload)
        else:
            action.click()
        if command == "table":
            shell.locator(".tp-cell").nth(9).click()
            page.keyboard.insert_text("SLASH-TABLE-CELL")
        elif command == "markdown_table":
            _markdown_table(page, "SLASH-MD")
        elif command == "jira":
            page.locator(".lp-input").first.fill("DL-9001")
            page.locator(".lp-item").filter(has_text="DL-9001").first.click()
        elif command == "confluence":
            page.locator(".lp-input").first.fill("https://confluence.corp.example/spaces/DL/pages/42013/설계+노트")
            page.locator(".lp-input.sm").fill("UI-DOCUMENT")
            page.get_by_role("button", name="이 URL 첨부", exact=True).click()
        elif command != "file":
            page.keyboard.insert_text("SLASH-" + command)
    return {"toolbar": inventory, "slash": discovered}


def _assert_controls(scope, *, editing=False):
    for selector, sentinel in [("strong", "UI-BOLD"), ("em", "UI-ITALIC"), ("s,del,strike", "UI-STRIKE"),
                               ("code", "UI-INLINE"), ("h1", "UI-H1"), ("h2", "UI-H2"), ("h3", "UI-H3"),
                               ("ul li", "UI-BULLET"), ("ol li", "UI-ORDERED"), ("blockquote", "UI-QUOTE"),
                               ("pre", "UI-CODE")]:
        assert scope.locator(selector).filter(has_text=sentinel).count(), (selector, sentinel)
    for kind in ["info", "note", "tip", "success", "warning", "error"]:
        assert scope.locator(".callout-" + kind).count(), kind
    assert scope.get_by_text("UI-COLOR", exact=True).count(), scope.evaluate("el => { const h=el.innerHTML, p=h.indexOf('UI-COLOR'); return h.slice(Math.max(0,p-300),p+500); }")
    assert scope.get_by_text("UI-COLOR", exact=True).evaluate("el => getComputedStyle(el).color") == "rgb(220, 38, 38)"
    assert scope.get_by_text("UI-BG", exact=True).evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(254, 240, 138)"
    assert "monospace" in scope.get_by_text("UI-FONT", exact=True).evaluate("el => getComputedStyle(el).fontFamily")
    for label, alignment in [("LEFT", "left"), ("CENTER", "center"), ("RIGHT", "right")]:
        assert scope.get_by_text("UI-" + label, exact=True).evaluate("el => getComputedStyle(el).textAlign") == alignment
    assert scope.locator("table").count() >= 3
    assert scope.locator("input[type=checkbox]").count() >= 2
    assert scope.locator(".mention, [data-type=mention]").count() >= 1
    mention = scope.locator(".mention-badge").first
    assert mention.evaluate("el => getComputedStyle(el).verticalAlign") == "middle"
    avatar = mention.locator(".mention-av")
    assert "@" in avatar.text_content()  # Fallback remains underneath a loading/failed photo.
    assert avatar.evaluate("el => { const r=el.getBoundingClientRect(); return Math.abs(r.width-r.height)<1 && getComputedStyle(el).overflow==='hidden'; }")
    for photo in avatar.locator("img").all():
        assert photo.evaluate("el => { const r=el.getBoundingClientRect(), p=el.parentElement.getBoundingClientRect(); return getComputedStyle(el).objectFit==='cover' && Math.abs(r.top-p.top)<1 && Math.abs(r.left-p.left)<1; }")
    document = scope.locator("a[href*='/spaces/DL/pages/42013/']")
    assert document.count() >= 1
    assert ("web-badge" if editing else "conf-link") in (document.first.get_attribute("class") or "")
    images = scope.locator(".img-wrap img" if editing else "img[src*='/api/img']")
    assert images.count() >= 2
    for image in images.all():
        image.evaluate("img => new Promise(resolve => { if(img.complete) resolve(); else {img.onload=resolve;img.onerror=resolve;} })")
        assert image.evaluate("img => img.naturalWidth > 0")
    for cell in scope.locator("table th, table td").all()[:3]:
        assert cell.evaluate("el => { const s=getComputedStyle(el); return parseFloat(s.borderTopWidth)>0 && s.borderTopStyle!=='none' && s.borderTopColor!=='rgba(0, 0, 0, 0)'; }")


def _modify_controls(page, editor, upload):
    """Modify each group after edit recovery, using the same controls a user sees."""
    shell = editor.locator("xpath=ancestor::div[contains(@class,'cmt-editor')][1]")
    # Toggle marks off/on, preserving the named sentinel assertions in both render cycles.
    for label, title in [("BOLD", "굵게"), ("ITALIC", "기울임"), ("STRIKE", "취소선"), ("INLINE", "인라인 코드")]:
        for _ in range(2):
            _select(editor, "UI-" + label)
            _button(shell, title).click()
    for title, label, choices in [("글자색", "COLOR", ["파랑", "빨강"]),
                                  ("배경색(형광펜)", "BG", ["초록", "노랑"])]:
        for choice in choices:
            _select(editor, "UI-" + label)
            _button(shell, title).click()
            shell.locator(".tb-sw-pop").get_by_title(choice, exact=True).click()
    for choice in ["기본 글꼴", "코딩 글꼴(고정폭)"]:
        _select(editor, "UI-FONT")
        _button(shell, "글꼴").click()
        shell.get_by_text(choice, exact=True).click()
    _select(editor, "UI-STYLE-QUOTE")
    shell.locator("button[title^='글 스타일']").click()
    shell.locator(".tb-style-i").filter(has=page.get_by_text("제목 2", exact=True)).click()
    _select(editor, "UI-PLAIN")
    _button(shell, "오른쪽 정렬").click()
    # Toolbar focus restoration uses a frame callback. Focus the editing surface explicitly
    # before navigation so native Home collapses the text selection on every Chromium build.
    editor.focus()
    editor.press("Home")
    page.keyboard.insert_text("시작 ")
    editor.press("End")
    page.keyboard.insert_text(" 끝 SECOND")
    assert "UI-PLAIN" in editor.inner_text()
    for label in ["UI-BULLET", "UI-QUOTE", "UI-CODE", "UI-TABLE-CELL"]:
        _select(editor, label)
        page.keyboard.press("End")
        page.keyboard.insert_text(" SECOND")
        if label == "UI-BULLET":
            page.keyboard.press("Enter")
            page.keyboard.insert_text("추가한 목록 항목")
        if label == "UI-TABLE-CELL":
            _button(shell, "아래에 행 추가").click()
            _button(shell, "행 삭제").click()
    editor.locator("input[type=checkbox]").first.uncheck()
    for kind in ["info", "note", "tip", "success", "warning", "error"]:
        _select(editor, "SLASH-" + kind)
        page.keyboard.press("End")
        page.keyboard.insert_text(" SECOND")
    editor.locator("a[href='https://example.com/ui-controls']").dblclick()
    page.locator(".badge-edit .be-t").fill("수정한 외부 링크")
    page.locator(".badge-edit .be-h").fill("https://example.com/ui-controls-edited")
    page.locator(".badge-edit .be-ok").click()
    _select(editor, "UI-END")
    page.keyboard.press("End")
    page.keyboard.insert_text(" 중간 @")
    page.locator(".mention-popup .mn-item").first.click()
    page.keyboard.insert_text(" 이미지 옆 ")
    with page.expect_file_chooser() as chooser:
        _button(shell, "이미지").click()
    chooser.value.set_files(upload)
    _button(shell, "에디터 최대화").click()
    shell.get_by_title("최대화 해제", exact=True).click()


@pytest.mark.parametrize("surface", ["description", "comment"])
@pytest.mark.parametrize("editor_browser", [{"format": "html"}], indirect=True)
def test_toolbar_and_all_slash_commands_survive_draft_and_publish_cycles(editor_browser, surface):
    page, base, errors, upload = editor_browser
    _open_ticket(page, base, "DL-9001" if surface == "description" else "DL-9007")
    if surface == "description":
        page.locator("button.sec-edit").first.click()
        selector = ".tkt-desc-edit .ProseMirror"
    else:
        page.get_by_role("button", name="＋ 댓글 달기").click()
        selector = ".tkt-compose-editor .ProseMirror"
    editor = page.locator(selector)
    # A visible image permits actual selection and resizing; the shared 1px fixture is all handle.
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    upload.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 80, 40, 8, 2, 0, 0, 0))
                       + chunk(b"IDAT", zlib.compress((b"\0" + b"\x40\xa0\xe0" * 80) * 40)) + chunk(b"IEND", b""))
    inventory = _create_controls(page, editor, surface, upload)
    _assert_controls(editor, editing=True)
    # The complete control-produced document must survive a deliberate draft close/reopen too.
    if surface == "description":
        page.locator(".tkt-desc-edit-f").get_by_role("button", name="취소", exact=True).click()
        page.locator("button.sec-edit").first.click()
    else:
        page.get_by_role("button", name="댓글 작성창 가리기").click()
        page.locator(".tkt-cmt-addbtn.draft").click()
    editor = page.locator(selector)
    editor.wait_for(state="visible")
    _assert_controls(editor, editing=True)
    for cycle in range(2):
        if cycle:
            _modify_controls(page, editor, upload)
        if surface == "description":
            page.locator(".tkt-desc-edit-f button.primary").click()
            page.locator(".tkt-desc-edit").wait_for(state="detached")
            rendered = page.locator(".tkt-main")
        else:
            editor.locator("xpath=ancestor::div[contains(@class,'cmt-editor')][1]").locator(".cmt-ed-bar button.primary").click()
            editor.wait_for(state="detached")
            rendered = page.locator(".tkt-cmt-b", has_text="UI-PLAIN")
            rendered.wait_for(state="visible")
        # Closing the editor precedes the asynchronous fresh ticket render.
        # Wait for this publish's content, not merely the always-mounted container.
        rendered.get_by_text("끝 SECOND" if cycle else "UI-PLAIN", exact=False).first.wait_for()
        _assert_controls(rendered)
        _assert_jira_callout_storage(
            page, base, "DL-9001" if surface == "description" else "DL-9007", surface, "UI-PLAIN")
        ticket_badge = rendered.locator(".jira-badge[data-key='DL-9001']").first
        ticket_badge.hover()
        page.locator(".reference-hover[role=tooltip]").wait_for(state="visible")
        if cycle == 0:
            if surface == "description":
                page.locator("button.sec-edit").first.click()
                editor = page.locator(selector)
            else:
                page.locator(".tkt-cmt", has=rendered).get_by_role("button", name="수정", exact=True).click()
                editor = page.locator(".tkt-cmt .ProseMirror")
            editor.wait_for(state="visible")
            _assert_controls(editor, editing=True)
            editor.locator(".jira-badge[href*='/browse/DL-9001']").first.hover()
            page.locator(".reference-hover[role=tooltip]").wait_for(state="visible")
    assert not errors, {"errors": errors, "inventory": inventory}


@pytest.mark.parametrize("surface", ["description", "comment"])
@pytest.mark.parametrize("editor_browser", [{"format": "html"}], indirect=True)
def test_old_ltm_and_native_jira_callouts_survive_edit_and_republish(editor_browser, surface):
    from playwright.sync_api import expect

    page, base, errors, _upload = editor_browser
    key = "DL-9001" if surface == "description" else "DL-9007"
    # Seed old stored content, not editor state. New UI creation is covered above through all
    # toolbar/slash controls; this case specifically starts with pre-upgrade Jira data.
    cases = [("callout callout-" + kind, "OLD-LTM-" + kind, kind) for kind in CALLOUT_KINDS]
    cases += [("jePanel_" + native, "JIRA-" + native, kind) for native, kind in (
        ("info", "info"), ("warning", "warning"), ("idea", "tip"), ("grey", "note"), ("dashed", "note"))]
    html = "<p>LEGACY-CALLOUTS</p>" + "".join(
        f'<div class="{classes}"><p>{label}</p></div>' for classes, label, _kind in cases
    ) + '<div class="jePanel_custom"><p>UNKNOWN-PRESERVED</p></div>'
    html = html.replace('class="jePanel_info"',
                        'class="jePanel_info" style="border:1px solid #9eb6d4;padding: .5em 1em .5em 2.5em;"')
    response = page.request.post(f"{base}/__editor_test__/seed/{key}", data={surface: html})
    assert response.ok, response.text()
    # This is a format-compatibility test, not a mutation-vs-background-search race. Let the
    # app's startup candidate prewarm finish before writing its first very short fixture edit.
    with page.expect_response(lambda r: "/api/parent-task-candidates?" in r.url) as prewarm:
        _open_ticket(page, base, key)
    assert prewarm.value.ok, prewarm.value.text()
    for cycle in range(2):
        rendered = page.locator(".tkt-main") if surface == "description" else page.locator(
            ".tkt-cmt-b", has_text="LEGACY-CALLOUTS")
        rendered.wait_for(state="visible")
        rendered.get_by_text("LEGACY-CALLOUTS", exact=True).wait_for(state="visible")
        for _classes, label, kind in cases:
            assert rendered.locator(".callout-" + kind).filter(has_text=label).count() == 1
        assert rendered.locator(".callout-note", has_text="UNKNOWN-PRESERVED").count() == 1
        if surface == "description":
            page.locator("button.sec-edit").first.click()
            editor = page.locator(".tkt-desc-edit .ProseMirror")
        else:
            page.locator(".tkt-cmt", has=rendered).get_by_role("button", name="수정", exact=True).click()
            editor = page.locator(".tkt-cmt .ProseMirror")
        editor.wait_for(state="visible")
        if surface == "comment":
            submit = editor.locator("xpath=ancestor::div[contains(@class,'cmt-editor')][1]").locator(
                ".cmt-ed-bar button.primary")
            expect(submit, f"edit cycle={cycle}; notices={page.locator('.cmt-restored').all_inner_texts()}").to_be_enabled()
        for _classes, label, kind in cases:
            callout = editor.locator(".callout-" + kind).filter(has_text=label)
            assert callout.count() == 1
            _select(editor, label + (" EDIT1" if cycle else ""))
            page.keyboard.press("End")
            page.keyboard.insert_text(" EDIT" + str(cycle + 1))
            assert callout.get_by_text(label + (" EDIT1 EDIT2" if cycle else " EDIT1"), exact=True).count() == 1
        assert editor.locator(".callout-note", has_text="UNKNOWN-PRESERVED").count() == 1
        if surface == "description":
            page.locator(".tkt-desc-edit-f button.primary").click()
        else:
            submit.click()
        editor.wait_for(state="detached")
        _assert_jira_callout_storage(page, base, key, surface, "LEGACY-CALLOUTS", legacy=True)
        assert page.evaluate("""async () => {
          const d = await new Promise(resolve => { const r=indexedDB.open('ltm-drafts'); r.onsuccess=()=>resolve(r.result); });
          return await new Promise(resolve => { const r=d.transaction('drafts').objectStore('drafts').count(); r.onsuccess=()=>resolve(r.result); });
        }""") == 0, "Successful publication must not resurrect a conflicting draft"
    rendered = page.locator(".tkt-main") if surface == "description" else page.locator(
        ".tkt-cmt-b", has_text="LEGACY-CALLOUTS")
    rendered.get_by_text("OLD-LTM-info EDIT1 EDIT2", exact=True).wait_for(state="visible")
    for _classes, label, kind in cases:
        assert rendered.locator(".callout-" + kind).filter(has_text=f"{label} EDIT1 EDIT2").count() == 1
    assert not errors, errors
