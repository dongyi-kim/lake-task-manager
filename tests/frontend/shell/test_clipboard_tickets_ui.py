"""Real AppRoot routing and prompt UI; clipboard contents are a page-local fake."""
from frontend.editor.test_editor_roundtrip_ui import editor_browser


def test_clipboard_prompt_does_not_fetch_until_open_and_keeps_editor_draft(editor_browser):
    page, base, errors, _ = editor_browser
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.add_init_script("""(() => {
      window.testClipboardText = '';
      const clipboard = new EventTarget();
      window.clipboardReads = 0;
      clipboard.readText = async () => { window.clipboardReads++; return window.testClipboardText; };
      Object.defineProperty(navigator, 'clipboard', {value:clipboard});
      const permission = new EventTarget(); permission.state='granted';
      Object.defineProperty(navigator, 'permissions', {value:{query:async()=>permission}});
    })();""")
    page.goto(base)
    page.wait_for_function("() => window.__lakeUp")
    page.locator(".search-trig").wait_for()
    page.evaluate("""() => {
      const input=document.createElement('textarea'); input.id='clipboard-draft';
      input.value='작성 중인 내용'; document.body.append(input); input.focus();
      window.testClipboardText='http://127.0.0.1:8080/browse/DL-9001 '
        + 'http://127.0.0.1:8080/browse/DL-9007 http://127.0.0.1:8080/browse/DL-9001';
      window.dispatchEvent(new Event('focus'));
    }""")
    prompt = page.get_by_role("region", name="클립보드 Jira 링크")
    prompt.wait_for()
    assert prompt.locator("option").all_text_contents() == ["DL-9001", "DL-9007"]
    assert page.locator("#clipboard-draft").evaluate("el=>el===document.activeElement")
    assert not any("DL-9001" in url or "DL-9007" in url for url in requests)
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 900})
        box = prompt.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width
        assert prompt.get_by_role("button", name="열기", exact=True).is_visible()
    prompt.get_by_role("button", name="닫기", exact=True).click()
    reads = page.evaluate("window.clipboardReads")
    page.evaluate("() => window.dispatchEvent(new Event('focus'))")
    page.wait_for_function("reads => window.clipboardReads > reads", arg=reads)
    assert prompt.count() == 0
    page.evaluate("""() => {
      window.testClipboardText='http://127.0.0.1:8080/browse/DL-9001';
      window.dispatchEvent(new Event('focus'));
    }""")
    prompt.wait_for()
    prompt.get_by_role("button", name="열기", exact=True).click()
    page.wait_for_function("() => !!document.querySelector('.tkt-ov')")
    page.wait_for_function("() => document.querySelector('.tkt-ov')?.textContent.includes('DL-9001')")
    assert page.locator("#clipboard-draft").input_value() == "작성 중인 내용"
    assert any("DL-9001" in url for url in requests)
    assert prompt.count() == 0
    page.set_viewport_size({"width": 1440, "height": 900})
    page.get_by_role("button", name="＋ 댓글 달기").click()
    editor = page.locator(".tkt-compose-editor .ProseMirror[contenteditable=true]")
    editor.fill("클립보드 링크 안내 중 작성한 댓글 초안")
    page.evaluate("""() => {
      window.testClipboardText='http://127.0.0.1:8080/browse/DL-9007';
      window.dispatchEvent(new Event('focus'));
    }""")
    prompt.wait_for()
    assert editor.evaluate("el=>el===document.activeElement")
    assert "댓글 초안" in editor.inner_text()
    prompt.get_by_role("button", name="열기", exact=True).click()
    page.wait_for_function("() => document.querySelector('.tkt-ov')?.textContent.includes('DL-9007')")
    page.evaluate("""() => {
      window.testClipboardText='http://127.0.0.1:8080/browse/DL-9001';
      window.dispatchEvent(new Event('focus'));
    }""")
    prompt.wait_for()
    prompt.get_by_role("button", name="열기", exact=True).click()
    page.wait_for_function("() => document.querySelector('.tkt-ov')?.textContent.includes('DL-9001')")
    page.get_by_role("button", name="＋ 댓글 달기").click()
    page.wait_for_function("() => document.querySelector('.tkt-compose-editor .ProseMirror')?.textContent.includes('댓글 초안')")
    assert not errors, errors
