"""Find real rendered cards beyond folds/pages without changing saved layout."""
import json
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401
from frontend.editor.test_draft_lifecycle_ui import _mount


def test_find_reveals_hidden_cards_and_restores_layout(editor_browser):
    page, base, errors, _ = editor_browser

    def task(number, **extra):
        row = {"key": f"DL-{number}", "title": f"한글 Task {number}", "type": "Task", "mine": True,
               "statusCategory": "todo", "assignee": "담당자", "assigneeId": "admin",
               "priRank": 2, "pri": "Medium", "dueDays": None, "epic": "DL-9001",
               "hasSubs": False, "childrenPending": False, "childrenLoaded": True, **extra}
        return {**row, "atoms": [row], "others": []}

    parent = task(100, hasSubs=True, kidsTotal=8, kidsDone=0, statusCategory="inprogress")
    parent["atoms"] = [task(i)["atoms"][0] for i in range(101, 109)]
    model = {"groups": [parent] + [task(i) for i in range(200, 243)],
             "epics": [{"key": "DL-9001", "title": "공통 Epic", "pending": False}],
             "groupingMeta": {"wbs": [{"id": "A", "name": "WBS A", "epics": ["DL-9001"]}]},
             "counts": {"total": 52, "done": 0, "overdue": 0, "today": 0, "week": 0}}
    requests = []

    def stream(route):
        requests.append(route.request.url)
        token = parse_qs(urlparse(route.request.url).query)["requestToken"][0]
        route.fulfill(content_type="application/x-ndjson", body=json.dumps({
            "contract": "task-snapshot.v1", "type": "snapshot", "requestToken": token,
            "sequence": 1, "done": True, "model": model}) + "\n")

    page.add_init_script("localStorage.setItem('mytasks.opts', JSON.stringify({groupBy:'wbs',subView:'collapsed'}))")
    page.route("**/api/mytasks/stream?*", stream)
    page.route("**/api/ticket/*/badge", lambda route: route.fulfill(json={
        "key": route.request.url.split("/")[-2], "title": "Fixture Task", "type": "Task"}))
    page.goto(base + "/#/mytasks")
    page.locator('.mt-card[data-key="DL-100"]:visible').wait_for()
    # The onboarding bubble has role=dialog but is nonmodal and must not block find.
    page.locator('.guide-bub').wait_for()
    original_prefs = page.evaluate("localStorage.getItem('mytasks.opts')")
    initial_requests = len(requests)
    page.keyboard.press("Control+f")
    query = page.get_by_role("textbox", name="카드에서 찾기")
    query.fill("DL-108")
    page.locator('.mt-find-current[data-key="DL-108"]:visible').wait_for()
    assert page.locator('.mt-find-count').inner_text() == "1 / 1"
    assert page.locator('.mt-gcard2.folded:visible').count() == 0
    query.press("Escape")
    page.locator('.mt-find').wait_for(state="hidden")
    page.locator('.mt-gcard2.folded:visible').wait_for()
    assert page.locator('.mt-card[data-key="DL-108"]:visible').count() == 0
    assert page.evaluate("localStorage.getItem('mytasks.opts')") == original_prefs

    # A result beyond global status pagination is revealed without clicking 'more'.
    page.keyboard.press("Control+f")
    query.fill("DL-24")
    page.locator('.mt-find-current[data-key="DL-240"]:visible').wait_for()
    assert page.locator('.mt-find-count').inner_text() == "1 / 3"
    query.press("Shift+Enter")
    page.locator('.mt-find-current[data-key="DL-242"]:visible').wait_for()
    query.press("Enter")
    page.locator('.mt-find-current[data-key="DL-240"]:visible').wait_for()
    query.press("Escape")
    page.locator('.mt-find').wait_for(state="hidden")
    assert page.locator('.mt-card[data-key="DL-240"]:visible').count() == 0
    assert len(requests) == initial_requests

    # All grouping modes and narrow layout use the same model search.
    select = page.locator('.mt-opt', has_text="그룹화").locator('select')
    for mode in ["epic", "sub", "none", "wbs"]:
        select.select_option(mode)
        section = {"epic": "DL-9001", "wbs": "wbs:A"}.get(mode, "__all__")
        page.locator(f'[data-task-section="{section}"]').wait_for()
        expect(page.locator('.mt-gcard2:visible')).to_have_count(0 if mode == "none" else 1)
        page.keyboard.press("Control+f")
        query.fill("한글 Task 108")
        page.locator('.mt-find-current[data-key="DL-108"]:visible').wait_for()
        query.press("Escape")
        page.locator('.mt-find').wait_for(state="hidden")
    page.set_viewport_size({"width": 720, "height": 900})
    page.locator('.mytasks.ax-v').wait_for()
    band = page.locator('.mt-band.c-todo')
    band.locator('.mt-bandh').click()
    expect(band).to_have_class("mt-band c-todo closed")
    narrow_prefs = page.evaluate("localStorage.getItem('mytasks.opts')")
    page.keyboard.press("Control+f")
    query.fill("DL-108")
    page.locator('.mt-find-current[data-key="DL-108"]:visible').wait_for()
    expect(band).not_to_have_class("mt-band c-todo closed")
    query.press("Escape")
    page.locator('.mt-find').wait_for(state="hidden")
    expect(band).to_have_class("mt-band c-todo closed")
    assert page.evaluate("localStorage.getItem('mytasks.opts')") == narrow_prefs
    page.keyboard.press("Control+f")
    query.fill("newly arrived")
    expect(page.locator('.mt-find-count')).to_have_text("0 / 0")
    model["groups"].append(task(999, title="newly arrived"))
    with page.expect_response("**/api/mytasks/stream?*"):
        page.evaluate("window.dispatchEvent(new CustomEvent('ticket-changed', {detail:{key:'DL-999'}}))")
    page.locator('.mt-find-current[data-key="DL-999"]:visible').wait_for()
    assert page.locator('.mt-find-count').inner_text() == "1 / 1"
    assert not errors


def test_find_does_not_intercept_dialog_editor_or_other_route(editor_browser):
    page, base, errors, _ = editor_browser
    page.goto(base + "/#/mytasks")
    page.locator('.mytasks').wait_for()
    # Observe after the app listener: a guarded shortcut must retain native behavior.
    page.evaluate("""window.addEventListener('keydown', e => {
      if (e.ctrlKey && e.key.toLowerCase() === 'f') window.findPrevented = e.defaultPrevented;
    })""")

    def native_find(target):
        target.focus()
        page.keyboard.press("Control+f")
        assert page.evaluate("window.findPrevented") is False
        expect(page.locator('.mt-find')).to_have_count(0)

    # TransitionDialog has no ARIA role; focus a button to exercise the modal guard.
    _mount(page, "TransitionDialog", {"ticket": "DL-9007", "transition": {
        "id": "31", "name": "완료", "toCategory": "done", "fields": []}})
    native_find(page.locator('.trx-x'))
    native_find(page.locator('.trx-ov .ProseMirror[contenteditable=true]'))
    page.locator('.trx-x').click()
    page.locator('.trx-ov').wait_for(state="hidden")

    _mount(page, "NewChildDialog", {"standalone": True})
    native_find(page.locator('.nk-f').get_by_role('button', name='취소', exact=True))
    page.locator('.nk-f').get_by_role('button', name='취소', exact=True).click()
    page.locator('.nk-ov').wait_for(state="hidden")

    # An ordinary editable region is protected even without a modal backdrop.
    page.evaluate("""() => {
      const editor = document.createElement('div'); editor.id = 'find-editor';
      editor.contentEditable = 'plaintext-only'; editor.textContent = '편집 중'; document.body.append(editor);
    }""")
    native_find(page.locator('#find-editor'))
    page.evaluate("document.querySelector('#find-editor').remove()")
    page.evaluate("location.hash = '#/home'")
    page.wait_for_function("() => !document.querySelector('.mytasks')?.getClientRects().length")
    page.locator('body').click(position={"x": 700, "y": 20})
    page.keyboard.press("Control+f")
    assert page.evaluate("window.findPrevented") is False
    expect(page.locator('.mt-find')).to_have_count(0)
    assert not errors
