"""Real new-document editor -> DC storage -> shared mock -> authenticated reader."""
import re

from frontend.editor.test_editor_roundtrip_ui import (
    editor_browser, _replace_with_rich_content, PNG_1X1,  # noqa: F401
)


def open_composer(page, base):
    page.add_init_script("localStorage.setItem('lake.guides.seen', JSON.stringify(['global-search-recent-1','quick-open-hotkey-1','browser-localhost-access-1','local-mcp-connect-1','refresh-fab-1']))")
    page.goto(base + "/#/confluence/890100000001")
    page.locator('.conf-document > h1').wait_for()
    page.get_by_role("button", name="＋ 새 문서", exact=True).click()
    editor = page.locator(".conf-composer .ProseMirror")
    editor.wait_for()
    return editor


def test_new_document_rich_editor_draft_attachments_and_numbering(editor_browser):
    page, base, errors, upload = editor_browser
    editor = open_composer(page, base)
    page.get_by_role("textbox", name="문서 제목", exact=True).fill("Confluence authoring roundtrip")
    _replace_with_rich_content(page, editor, "CONF", section=False, image_name="same.png", upload_path=upload)
    # Regular files take the same real file picker path, and survive along with images.
    with page.expect_file_chooser() as chooser:
        page.locator('.conf-composer button[title="이미지"]').click()
    chooser.value.set_files({"name": "checklist.txt", "mimeType": "text/plain", "buffer": b"document attachment"})
    page.locator('.conf-composer a[data-file]').wait_for()
    page.get_by_role("button", name="닫기 · 초안 보관", exact=True).click()
    page.goto(base + "/#/confluence/890100000002")
    page.locator(".conf-document h1").wait_for()
    page.get_by_role("button", name="＋ 새 문서", exact=True).click()
    editor = page.locator(".conf-composer .ProseMirror")
    editor.locator("img[src^='blob:']").nth(2).wait_for()
    assert page.get_by_role("textbox", name="문서 제목", exact=True).input_value() == "Confluence authoring roundtrip"
    assert page.get_by_label("새 문서 위치", exact=True).input_value() == "890100000001"
    assert "운영 가이드 아래" in page.get_by_label("새 문서 위치", exact=True).locator("option:checked").inner_text()
    assert editor.locator("h1").get_attribute("data-heading-number") == "1"
    assert editor.locator("h1").evaluate("el => getComputedStyle(el,'::before').position") == "static"
    assert editor.locator("h2").get_attribute("data-heading-number") == "1.1"
    page.get_by_role("checkbox", name=re.compile("헤딩 자동 번호")).uncheck()
    assert editor.locator("h1").get_attribute("data-heading-number") is None
    page.get_by_role("checkbox", name=re.compile("헤딩 자동 번호")).check()
    creates, jira_uploads = [], []
    page.on("request", lambda request: creates.append(request.post_data_json) if request.method == "POST" and request.url == base + "/api/confluence/pages" else None)
    page.on("request", lambda request: jira_uploads.append(request.url) if request.method == "POST" and "/api/ticket/" in request.url else None)
    page.get_by_role("button", name="발행", exact=True).click()
    page.locator(".conf-document h1").filter(has_text="Confluence authoring roundtrip").wait_for(timeout=30000)
    assert len(creates) == 1 and not jira_uploads
    assert creates[0]["parent_id"] == "890100000001"
    stored = creates[0]["body"]
    assert "<ac:image" in stored and "<ri:attachment" in stored and 'ac:name="code"' in stored
    assert "blob:" not in stored and "draft:" not in stored
    assert '<ri:user ri:userkey="conf-user-test.ui02"' in stored
    assert "<h1>1 CONF-H1</h1>" in stored
    body = page.locator(".conf-document-body")
    assert body.locator("table td").filter(has_text="CONF-TABLE").count() == 1
    assert body.locator("pre code").inner_text().startswith("CONF-SQL")
    assert body.locator('input[type="checkbox"]').count() == 2
    assert body.locator('.callout-info').filter(has_text="CONF-INFO").count() == 1
    assert body.locator('.callout-tip').filter(has_text="CONF-TIP").count() == 1
    assert body.locator('a[href*="/browse/DL-9001"]').count() == 1
    assert body.locator('a[href*="checklist.txt"]').count() == 1
    download = page.request.get(base + body.locator('a[href*="checklist.txt"]').get_attribute("href"))
    assert download.ok and download.body() == b"document attachment"
    assert "attachment; filename*=" in download.headers["content-disposition"]
    for img in body.locator("img").all():
        img.evaluate("el => el.decode()")
        assert img.evaluate("el => el.complete && el.naturalWidth > 0")
    assert body.locator("img").count() == 3
    page.reload()
    page.locator(".conf-document h1").filter(has_text="Confluence authoring roundtrip").wait_for()
    assert page.locator(".conf-document-body img").count() == 3
    page.get_by_role("button", name="＋ 새 문서", exact=True).click()
    page.locator(".conf-composer .ProseMirror").wait_for()
    assert page.get_by_role("textbox", name="문서 제목", exact=True).input_value() == ""
    assert page.locator(".conf-composer .ProseMirror").inner_text().strip() == ""
    assert not errors


def test_unsupported_inline_image_blocks_publication_before_page_creation(editor_browser):
    page, base, errors, _ = editor_browser
    editor = open_composer(page, base)
    page.get_by_role("textbox", name="문서 제목", exact=True).fill("Unsupported inline image")
    editor.fill("Keep the draft available for correction")
    with page.expect_file_chooser() as chooser:
        page.locator('.conf-composer button[title="이미지"]').click()
    chooser.value.set_files({"name": "vector.svg", "mimeType": "image/svg+xml",
                            "buffer": b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" />'})
    editor.locator("img[src^='blob:']").wait_for()
    writes = []
    page.on("request", lambda request: writes.append(request.url) if request.method == "POST" and request.url == base + "/api/confluence/pages" else None)
    page.get_by_role("button", name="발행", exact=True).click()
    page.get_by_text(re.compile("본문 이미지는 PNG")).wait_for()
    assert not writes and editor.get_attribute("contenteditable") == "true"
    assert not errors


def test_failed_durable_save_blocks_every_write_and_expired_run_can_be_closed(editor_browser):
    page, base, errors, _ = editor_browser
    editor = open_composer(page, base)
    page.get_by_role("textbox", name="문서 제목", exact=True).fill("Durable evidence before publish")
    editor.fill("Never publish without a saved mutation id")
    writes = []
    page.on("request", lambda request: writes.append(request.url) if request.method == "POST" and request.url == base + "/api/confluence/pages" else None)
    page.evaluate("""() => {
      window.failedRunSaves = 0; window.rejectRunSave = true;
      const put = IDBObjectStore.prototype.put;
      IDBObjectStore.prototype.put = function(value, ...args) {
        if (value.run && window.rejectRunSave) { window.failedRunSaves++; throw new Error('fixture durable save failed'); }
        return put.call(this, value, ...args);
      };
    }""")
    page.get_by_role("button", name="발행", exact=True).click()
    page.wait_for_function("window.failedRunSaves === 1")
    page.get_by_role("button", name="발행 이어서 하기", exact=True).click()
    page.wait_for_function("window.failedRunSaves === 2")
    assert not writes
    page.evaluate("window.rejectRunSave = false")
    # Keep an uncertain attempt for the expiry path without sending to the mock upstream.
    page.route("**/api/confluence/pages", lambda route: route.abort() if route.request.method == "POST" else route.continue_())
    page.get_by_role("button", name="발행 이어서 하기", exact=True).click()
    page.wait_for_function("document.querySelector('.cmt-ed-msg')?.textContent.includes('fetch')")
    assert len(writes) == 1
    page.evaluate("""async () => {
      const {resolveDraftKey} = await import('/lib/draftIdentity.js');
      const {loadDraft, saveDraft} = await import('/lib/draft.js');
      const config = await (await fetch('/api/confluence/config')).json();
      const key = await resolveDraftKey('scoped:confluence:' + config.base + ':form');
      const draft = await loadDraft(key); draft.run.startedAt -= 8 * 86400000;
      await saveDraft(key, draft);
    }""")
    page.reload()
    page.get_by_role("button", name="＋ 새 문서", exact=True).click()
    page.locator(".conf-composer .ProseMirror").wait_for()
    page.get_by_role("button", name="발행 이어서 하기", exact=True).click()
    page.get_by_text(re.compile("재시도 보존 기간이 지났습니다")).wait_for()
    assert len(writes) == 1
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="결과 확인 완료 · 새 초안 시작", exact=True).click()
    page.wait_for_function("document.querySelector('.conf-compose-title')?.value === '' && !document.querySelector('.conf-compose-fields').disabled")
    assert page.locator(".conf-composer .ProseMirror").inner_text().strip() == ""
    assert page.get_by_text("종료한 발행 기록 (1)", exact=True).is_visible()
    assert not [error for error in errors if "ERR_FAILED" not in str(error)]


def test_partial_attachment_failure_resumes_after_reload_without_recreating_page(editor_browser):
    page, base, errors, upload = editor_browser
    editor = open_composer(page, base)
    page.get_by_role("textbox", name="문서 제목", exact=True).fill("Resume document attachments")
    editor.fill("Files remain in the durable draft")
    with page.expect_file_chooser() as chooser:
        page.locator('.conf-composer button[title="이미지"]').click()
    chooser.value.set_files([{"name": "same.png", "mimeType": "image/png", "buffer": PNG_1X1},
                            {"name": "same.png", "mimeType": "image/png", "buffer": PNG_1X1}])
    editor.locator("img").nth(1).wait_for()
    calls = {"create": 0, "upload": [], "fail": True}
    def uploads(route):
        calls["upload"].append(route.request.post_data_buffer)
        if calls["fail"] and len(calls["upload"]) == 2:
            route.fulfill(status=503, json={"error": "fixture upload unavailable", "service": "confluence"})
        else:
            route.continue_()
    page.route("**/api/confluence/pages/*/attachments", uploads)
    page.on("request", lambda request: calls.update(create=calls["create"] + 1) if request.method == "POST" and request.url == base + "/api/confluence/pages" else None)
    page.get_by_role("button", name="발행", exact=True).click()
    page.get_by_text(re.compile("문서는 발행되었습니다.*fixture upload unavailable")).wait_for()
    assert calls["create"] == 1
    page.reload()
    page.get_by_role("button", name="＋ 새 문서", exact=True).click()
    page.locator(".conf-composer .ProseMirror img").nth(1).wait_for()
    assert page.get_by_role("textbox", name="문서 제목", exact=True).is_disabled()
    calls["fail"] = False
    page.get_by_role("button", name="발행 이어서 하기", exact=True).click()
    page.locator(".conf-document h1").filter(has_text="Resume document attachments").wait_for(timeout=30000)
    assert calls["create"] == 1 and len(calls["upload"]) == 3
    for img in page.locator(".conf-document-body img").all():
        img.evaluate("el => el.decode()")
    assert not [error for error in errors if "503" not in str(error)]


def test_lost_creation_response_replays_same_request_without_duplicate_document(editor_browser):
    page, base, errors, _ = editor_browser
    editor = open_composer(page, base)
    page.get_by_role("textbox", name="문서 제목", exact=True).fill("Lost response recovery")
    editor.fill("Publish once even when the response is lost")
    writes, created = [], []
    def lose_first_response(route):
        if route.request.method != "POST":
            route.continue_(); return
        writes.append(route.request.post_data_json)
        if len(writes) == 1:
            result = route.fetch()
            assert result.ok, result.text()
            created.append(result.json()["id"])
            route.abort("failed")
        elif len(writes) == 2:
            route.fulfill(status=403, json={"error": "fixture permission preflight", "service": "confluence"})
        else:
            route.continue_()
    page.route("**/api/confluence/pages", lose_first_response)
    page.get_by_role("button", name="발행", exact=True).click()
    page.get_by_role("button", name="발행 이어서 하기", exact=True).wait_for()
    page.reload()
    page.get_by_role("button", name="＋ 새 문서", exact=True).click()
    page.locator(".conf-composer .ProseMirror").wait_for()
    page.get_by_role("button", name="발행 이어서 하기", exact=True).click()
    page.get_by_text("fixture permission preflight", exact=True).wait_for()
    assert page.get_by_role("textbox", name="문서 제목", exact=True).is_disabled()
    page.reload()
    page.get_by_role("button", name="＋ 새 문서", exact=True).click()
    page.locator(".conf-composer .ProseMirror").wait_for()
    page.get_by_role("button", name="발행 이어서 하기", exact=True).click()
    page.locator(".conf-document h1").filter(has_text="Lost response recovery").wait_for(timeout=30000)
    assert len(writes) == 3 and writes[0] == writes[1] == writes[2]
    assert page.url.endswith("/" + created[0])
    assert not [error for error in errors if "ERR_FAILED" not in str(error) and "403" not in str(error)]


def test_numbering_and_storage_adapter_keep_text_and_reject_unresolved_assets(editor_browser):
    page, base, errors, _ = editor_browser
    page.goto(base)
    result = page.evaluate("""async () => {
      const { outlineNumbers } = await import('/components/editor/headingNumbering.js');
      const { confluenceStorage } = await import('/lib/confluenceStorage.js');
      const rejected = [];
      for (const html of ['<img src="blob:missing">','<a href="draft:missing">file</a>',
          '<span data-type="mention" data-id="unknown">Name</span>']) {
        try { confluenceStorage(html); } catch (error) { rejected.push(error.message); }
      }
      return { sequence: outlineNumbers([2,4,4,3,2,1,2]), rejected,
        text: confluenceStorage('<h2 data-heading-number="99">10 Manual</h2><h4>Child</h4><pre><code>x &lt; y &amp;&amp; z ]]&gt;</code></pre><script>bad()</script>', {numbered:true}) };
    }""")
    assert result["sequence"] == ["1", "1.1", "1.2", "1.3", "2", "3", "3.1"]
    assert len(result["rejected"]) == 3
    assert "<h2>1 10 Manual</h2>" in result["text"] and "<h4>1.1 Child</h4>" in result["text"]
    assert "]]]]><![CDATA[>" in result["text"] and "bad()" not in result["text"]
    assert not errors
