"""Actual shared-editor paste semantics and completion-form regressions (no latency)."""
import base64

import pytest

from frontend.editor.test_editor_roundtrip_ui import editor_browser, _open_ticket
from frontend.editor.test_draft_lifecycle_ui import _mount


def _paste(editor, text, html=""):
    editor.evaluate("""(el, value) => {
      el.focus(); const data = new DataTransfer();
      data.setData('text/plain', value.text);
      if (value.html) data.setData('text/html', value.html);
      el.dispatchEvent(new ClipboardEvent('paste', {bubbles:true, cancelable:true, clipboardData:data}));
    }""", {"text": text, "html": html})


def _replace(page, editor, text, html=""):
    editor.click(); page.keyboard.press("Control+A"); page.keyboard.press("Backspace")
    _paste(editor, text, html)


def _theme_colors(page, host):
    for theme in ("light", "dark"):
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        colors = host.evaluate("""el => {
          const sample = document.createElement('span'); document.body.append(sample);
          sample.style.backgroundColor='var(--panel-2)'; sample.style.color='var(--text)';
          const expected = getComputedStyle(sample);
          const bg=expected.backgroundColor, fg=expected.color; sample.remove();
          return [...el.querySelectorAll('pre')].map(pre => ({bg, fg,
            actual:getComputedStyle(pre).backgroundColor, color:getComputedStyle(pre).color,
            codeBg:getComputedStyle(pre.querySelector('code')).backgroundColor}));
        }""")
        assert colors
        assert all(c["actual"] == c["bg"] and c["color"] == c["fg"]
                   and c["codeBg"] == "rgba(0, 0, 0, 0)" for c in colors), colors


@pytest.mark.parametrize("editor_browser", [{"format": "html"}], indirect=True)
def test_code_paste_preserves_html_fences_and_code_context(editor_browser):
    page, base, errors, upload = editor_browser
    page.goto(base)
    props = {"ticket": "DL-9007", "transition": {"id": "31", "name": "완료", "toCategory": "done", "fields": []}}
    _mount(page, "TransitionDialog", props)
    editor = page.locator(".trx .ProseMirror")
    editor.wait_for()
    _paste(editor, "if True:\n\tprint(1)", '<pre><code class="language-python">if True:\n\tprint(1)</code></pre>')
    assert editor.locator("table").count() == 0
    assert editor.locator("pre code").inner_text() == "if True:\n\tprint(1)"
    assert "language-python" in editor.locator("pre code").get_attribute("class")
    # Pasting within an existing code block must remain literal, including Markdown and tabs.
    editor.locator("pre code").click(); page.keyboard.press("End")
    literal = "\n```sql\nSELECT\t1\n```"
    _paste(editor, literal)
    assert editor.locator("pre").count() == 1
    assert editor.locator("table").count() == 0
    assert literal in editor.locator("pre code").inner_text()
    # Tiptap already supports typing a fence + Enter: protect that path without a second extension.
    page.evaluate("window.draftVm.$refs.ed._ed.commands.clearContent(false)")
    editor.click(); editor.press_sequentially("```sql"); editor.press("Enter")
    assert editor.locator("pre code.language-sql").count() == 1
    # Invalid/incomplete fences stay literal; multiple complete blocks preserve surrounding prose.
    result = page.evaluate(r"""async () => {
      const {fencedCodeNodes} = await import('/components/editor/editorClipboard.js');
      const mixed=fencedCodeNodes('앞 문장\r\n```sql\r\nSELECT\t1\r\n```\r\n중간\r\n~~~js\r\nconst x = "<p>";\r\n~~~\r\n뒤 문장');
      return {mixed, unmatched:fencedCodeNodes('```sql\nSELECT 1\n~~ ?'),
        inline:fencedCodeNodes('이 문장의 ``` 는 기호'),
        long:fencedCodeNodes('````txt\n```\n````'), empty:fencedCodeNodes('```\n```')};
    }""")
    assert [n["type"] for n in result["mixed"]] == ["paragraph", "codeBlock", "paragraph", "codeBlock", "paragraph"]
    assert result["mixed"][1]["content"][0]["text"] == "SELECT\t1"
    assert result["mixed"][3]["content"][0]["text"] == 'const x = "<p>";'
    assert result["unmatched"][0]["type"] == "paragraph" and result["inline"] is None
    assert result["long"][0]["content"][0]["text"] == "```"
    assert result["empty"][0]["type"] == "codeBlock"
    _theme_colors(page, page.locator(".trx"))
    assert page.locator(".trx").evaluate("el => getComputedStyle(el).backgroundColor") == page.locator(".trx .cmt-editor").evaluate("el => getComputedStyle(el).backgroundColor")
    # Non-text document elements are real content; empty HTML/whitespace is not.
    classification = page.evaluate("""async () => {
      const {hasEditorContent} = await import('/components/editor/editorHtml.js');
      return ['<p><br></p>', '<p> &nbsp; &#8203;</p>', '<table><tr><td></td></tr></table>',
        '<img src="blob:test">', '<p><input type="checkbox"></p>'].map(hasEditorContent);
    }""")
    assert classification == [False, False, True, True, True]
    # HTML copied from a rendered rich document must beat its fenced/tabbed text
    # alternative without dropping formatting, links or an embedded image.
    page.evaluate("window.draftVm.$refs.ed._ed.commands.clearContent(false)")
    page.locator(".trx input[type=file]").set_input_files(upload)
    image = editor.locator(".img-wrap img")
    image.wait_for()
    source = image.get_attribute("src")
    page.evaluate("window.draftVm.$refs.ed._ed.commands.clearContent(false)")
    _paste(editor, "```sql\nSELECT\t1\n```", '<p><strong>서식 보존</strong> '
           '<a href="https://example.com/paste">링크 보존</a></p><p><img src="' + source + '"></p>')
    assert editor.locator("strong").inner_text() == "서식 보존"
    assert editor.locator('a[href="https://example.com/paste"]').count() == 1
    assert editor.locator(".img-wrap img").count() == 1
    assert editor.locator("pre, table").count() == 0
    # Browser screenshot/image clipboard can contain HTML and a PNG file together.
    # Keep the file upload path rather than retaining the HTML's remote image URL.
    page.evaluate("window.draftVm.$refs.ed._ed.commands.clearContent(false)")
    editor.evaluate("""(el, encoded) => {
      el.focus(); const data = new DataTransfer();
      data.setData('text/html', '<img src="https://example.com/clipboard.png">');
      data.items.add(new File([Uint8Array.from(atob(encoded), c => c.charCodeAt(0))],
        'mixed-clipboard.png', {type:'image/png'}));
      el.dispatchEvent(new ClipboardEvent('paste', {bubbles:true, cancelable:true, clipboardData:data}));
    }""", base64.b64encode(upload.read_bytes()).decode("ascii"))
    image = editor.locator(".img-wrap img")
    image.wait_for()
    assert image.get_attribute("src").startswith("blob:")
    assert not errors


@pytest.mark.parametrize("surface", ["description", "comment"])
@pytest.mark.parametrize("editor_browser", [{"format": "html"}], indirect=True)
def test_pasted_code_survives_two_publish_cycles_and_theme(editor_browser, surface):
    page, base, errors, _ = editor_browser
    key = "DL-9001" if surface == "description" else "DL-9007"
    _open_ticket(page, base, key)
    # Let unrelated initial candidate-list warming finish before this deliberately
    # fast mutation loop invalidates its JQL generation.
    page.wait_for_load_state("networkidle")
    if surface == "description":
        page.locator("button.sec-edit").first.click()
        editor = page.locator(".tkt-desc-edit .ProseMirror")
    else:
        page.get_by_role("button", name="＋ 댓글 달기").click()
        editor = page.locator(".tkt-compose-editor .ProseMirror")
    _replace(page, editor, 'CODE-PASTE 앞 문장\n```sql\nSELECT\t1\n```\n중간\n~~~python\nprint("<tag>")\n~~~\n뒤 문장')
    assert editor.locator("pre").count() == 2 and editor.locator("table").count() == 0
    page.keyboard.press("Control+End"); page.keyboard.press("Enter")
    _paste(editor, "if ok:\n\tpass", '<pre style="background-color:white;color:black"><code class="language-python">if ok:\n\tpass</code></pre>')
    for cycle in range(2):
        assert editor.locator("pre").count() == 3
        _theme_colors(page, editor)
        if cycle:
            page.keyboard.press("Control+End"); page.keyboard.press("Enter")
            page.keyboard.insert_text("CODE-PASTE 수정 완료")
        if surface == "description":
            page.locator(".tkt-desc-edit-f button.primary").click()
            page.locator(".tkt-desc-edit").wait_for(state="detached")
            rendered = page.locator(".tkt-main")
        else:
            editor.locator("xpath=ancestor::div[contains(@class,'cmt-editor')][1]").locator(".cmt-ed-bar button.primary").click()
            editor.wait_for(state="detached")
            rendered = page.locator(".tkt-cmt-b", has_text="CODE-PASTE 앞 문장")
        rendered.get_by_text("CODE-PASTE 앞 문장", exact=False).wait_for()
        codes = rendered.locator("pre code").all_text_contents()
        assert codes == ["SELECT\t1", 'print("<tag>")', "if ok:\n\tpass"]
        _theme_colors(page, rendered)
        page.wait_for_load_state("networkidle")
        if cycle == 0:
            if surface == "description":
                page.locator("button.sec-edit").first.click()
                editor = page.locator(".tkt-desc-edit .ProseMirror")
            else:
                page.locator(".tkt-cmt", has=rendered).get_by_role("button", name="수정", exact=True).click()
                editor = page.locator(".tkt-cmt .ProseMirror")
            editor.wait_for()
    assert not errors


@pytest.mark.parametrize("editor_browser", [{"format": "html"}], indirect=True)
def test_imported_jira_code_inline_white_background_uses_active_theme(editor_browser):
    page, base, errors, _ = editor_browser
    response = page.request.put(base + "/api/ticket/DL-9001/fields", data={"descriptionHtml":
        '<pre class="jecodeblock" style="background-color:#fff;color:#000"><code style="background-color:white;color:black" class="language-python">print(1)</code></pre>'})
    assert response.ok
    _open_ticket(page, base, "DL-9001")
    page.locator(".tkt-main pre").wait_for()
    _theme_colors(page, page.locator(".tkt-main"))
    page.locator("button.sec-edit").first.click()
    page.locator(".tkt-desc-edit pre").wait_for()
    _theme_colors(page, page.locator(".tkt-desc-edit"))
    assert not errors

@pytest.mark.parametrize("body", ["empty", "whitespace", "rich"])
@pytest.mark.parametrize("editor_browser", [{"format": "html"}], indirect=True)
def test_completion_dialog_optional_comment_real_submit(editor_browser, body):
    page, base, errors, upload = editor_browser
    _open_ticket(page, base, "DL-9007")
    page.wait_for_load_state("networkidle")
    before = page.request.get(base + "/api/issue/DL-9007/comments").json()
    page.get_by_title("상태 변경", exact=True).click()
    page.locator(".fe-pop .fe-i").filter(has_text="Resolved").click()
    editor = page.locator(".trx .ProseMirror")
    editor.wait_for()
    assert "선택" in page.locator(".trx-f", has=page.locator(".ProseMirror")).inner_text()
    time_inputs = page.locator(".trx-time input")
    if time_inputs.count():
        time_inputs.nth(1).fill("1")
    if body == "whitespace":
        _paste(editor, " \n\t ", "<p>&nbsp; &#8203;</p><p><br></p>")
    elif body == "rich":
        _paste(editor, "완료 코드\n```sql\nSELECT\t1\n```\n완료 설명")
        page.keyboard.press("Control+End"); page.keyboard.press("Enter")
        _paste(editor, "", "<table><tr><td>완료 표</td></tr></table>")
        page.locator(".trx input[type=file]").set_input_files(upload)
        editor.locator(".img-wrap img").wait_for()
        _theme_colors(page, page.locator(".trx"))
    with page.expect_response(lambda r: "/api/ticket/DL-9007/transition" in r.url and r.request.method == "POST") as sent:
        page.locator(".trx-ok").click()
    response = sent.value
    assert response.ok, response.text()
    payload = response.request.post_data_json
    if body != "rich":
        assert payload["commentHtml"] == ""
    else:
        assert "<table" in payload["commentHtml"] and "<img" in payload["commentHtml"]
        assert "<pre" in payload["commentHtml"]
    page.locator(".trx").wait_for(state="detached")
    page.wait_for_load_state("networkidle")
    after = page.request.get(base + "/api/issue/DL-9007/comments").json()
    assert len(after) == len(before) + (1 if body == "rich" else 0), (before, after)
    page.get_by_title("상태 변경", exact=True).locator(".st-done").wait_for()
    if body == "rich":
        comment = page.locator(".tkt-cmt-b", has_text="완료 코드")
        comment.wait_for()
        assert comment.locator("pre code").inner_text() == "SELECT\t1"
        assert comment.locator("table").count() == 1
        image = comment.locator("img").first
        image.evaluate("img => new Promise(resolve => {if(img.complete) resolve(); else {img.onload=resolve; img.onerror=resolve;}})")
        assert image.evaluate("img => img.complete && img.naturalWidth > 0")
    assert not errors


def test_jira_required_transition_comment_still_blocks_empty(editor_browser):
    page, base, errors, _ = editor_browser
    page.goto(base)
    props = {"ticket": "DL-9007", "transition": {"id": "31", "name": "완료", "toCategory": "done",
        "fields": [{"id": "comment", "required": True}]}}
    _mount(page, "TransitionDialog", props)
    page.locator(".trx .ProseMirror").wait_for()
    requests = []
    page.on("request", lambda r: requests.append(r.url) if r.method == "POST" and "/transition" in r.url else None)
    page.locator(".trx-ok").click()
    page.get_by_text("내용을 입력하세요.", exact=True).wait_for()
    assert not requests and not errors
