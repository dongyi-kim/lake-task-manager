"""Find real rendered cards beyond folds/pages without changing saved layout."""
import json
from urllib.parse import parse_qs, urlparse

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401
from frontend.editor.test_draft_lifecycle_ui import _mount


_EFFECTIVE_OPACITY = """element => {
  let opacity = 1;
  for (let node = element; node; node = node.parentElement) opacity *= Number(getComputedStyle(node).opacity);
  return opacity;
}"""


def _wait_opacity(page, selector, lower, upper):
    page.wait_for_function("""({selector, lower, upper}) => {
      const element = document.querySelector(selector);
      if (!element) return false;
      const opacity = (""" + _EFFECTIVE_OPACITY + """)(element);
      return opacity >= lower && opacity <= upper;
    }""", arg={"selector": selector, "lower": lower, "upper": upper})


def _assert_bottom_find(page):
    bar = page.locator('.mt-find')
    before = bar.bounding_box()
    viewport = page.evaluate("({width:innerWidth,height:innerHeight,x:scrollX,y:scrollY})")
    assert 0 <= viewport["height"] - before["y"] - before["height"] <= 32
    assert before["x"] >= 0 and before["x"] + before["width"] <= viewport["width"]
    # Exercise an actual scroll; a bottom-positioned element in document flow would move.
    page.evaluate("window.scrollTo(0, scrollY > 100 ? 0 : document.documentElement.scrollHeight)")
    page.wait_for_function("y => Math.abs(scrollY - y) > 50", arg=viewport["y"])
    after = bar.bounding_box()
    assert abs(after["y"] - before["y"]) < 1
    page.evaluate("({x,y}) => window.scrollTo(x,y)", viewport)


def _assert_clear_of_floating_actions(page):
    bar = page.locator('.mt-find').bounding_box()
    for selector in ['.fab-refresh', '.addfab']:
        action = page.locator(selector).bounding_box()
        assert (bar['x'] >= action['x'] + action['width'] or
                bar['x'] + bar['width'] <= action['x'] or
                bar['y'] >= action['y'] + action['height'] or
                bar['y'] + bar['height'] <= action['y']), selector


def test_find_reveals_hidden_cards_and_restores_layout(editor_browser):
    from playwright.sync_api import expect

    page, base, errors, upload_path = editor_browser
    console_issues = []
    page.on('console', lambda message: console_issues.append({'text': message.text, 'location': message.location})
            if message.type in ('warning', 'error') else None)

    def task(number, **extra):
        subject = ['신규 적재 테이블 정의', '공통 모델 검토', '데이터 정합성 점검', '운영 배치 모니터링'][number % 4]
        row = {"key": f"DL-{number}", "title": f"{subject} · 한글 Task {number}", "type": "Task", "mine": True,
               "statusCategory": "todo", "assignee": "담당자", "assigneeId": "admin",
               "priRank": 2, "pri": "Medium", "dueDays": None, "epic": "DL-9001",
               "hasSubs": False, "childrenPending": False, "childrenLoaded": True, **extra}
        return {**row, "atoms": [row], "others": []}

    parent = task(100, hasSubs=True, kidsTotal=10, kidsDone=0, statusCategory="inprogress")
    parent["atoms"] = [task(i)["atoms"][0] for i in range(101, 109)]
    parent["others"] = [task(109, mine=False, title="관련 팀 정합성 확인")["atoms"][0],
                        task(110, mine=False, title="관련 팀 운영 배치 확인")["atoms"][0]]
    model = {"groups": [parent] + [task(i) for i in range(200, 243)] +
                      [task(i, statusCategory="inprogress") for i in range(601, 607)] +
                      [task(i, statusCategory="done") for i in range(701, 707)],
             "epics": [{"key": "DL-9001", "title": "데이터 적재 품질 개선", "pending": False}],
             "groupingMeta": {"wbs": [{"id": "A", "name": "적재 운영 안정화", "module": "TEST", "epics": ["DL-9001"]}]},
             "counts": {"total": 66, "done": 6, "overdue": 0, "today": 0, "week": 0}}
    requests = []

    def stream(route):
        requests.append(route.request.url)
        token = parse_qs(urlparse(route.request.url).query)["requestToken"][0]
        route.fulfill(content_type="application/x-ndjson", body=json.dumps({
            "contract": "task-snapshot.v1", "type": "snapshot", "requestToken": token,
            "sequence": 1, "done": True, "model": model}) + "\n")

    page.add_init_script("localStorage.setItem('mytasks.opts', JSON.stringify({groupBy:'wbs',subView:'collapsed'}))")
    page.route("**/api/mytasks/stream?*", stream)
    # External font availability is not part of this offline board regression.
    # Keep the installed font fallback, with every application console error still checked.
    page.route('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/packages/pretendard/dist/web/variable/pretendardvariable.css',
               lambda route: route.fulfill(content_type='text/css', body=''))
    page.route("**/api/ticket/*/badge", lambda route: route.fulfill(json={
        "key": route.request.url.split("/")[-2], "title": "Fixture Task", "type": "Task"}))
    page.goto(base + "/#/mytasks")
    page.locator('.mt-card[data-key="DL-100"]:visible').wait_for()
    # The onboarding bubble has role=dialog but is nonmodal and must not block find.
    page.locator('.guide-bub').wait_for()
    original_prefs = page.evaluate("localStorage.getItem('mytasks.opts')")
    initial_requests = len(requests)
    other_selector = '.mt-card[data-key="DL-200"]'
    other_opacity = page.locator(other_selector).evaluate(_EFFECTIVE_OPACITY)
    sidebar_opacity = page.locator('.mt-options-panel').evaluate(_EFFECTIVE_OPACITY)
    summary_opacity = page.locator('.mt-tiles').evaluate(_EFFECTIVE_OPACITY)
    page.keyboard.press("Control+f")
    query = page.get_by_role("textbox", name="카드에서 찾기")
    expect(page.locator('.mytasks.mt-find-active')).to_have_count(0)
    _wait_opacity(page, other_selector, other_opacity - .01, other_opacity + .01)
    query.fill("DL-108")
    page.locator('.mt-find-current[data-key="DL-108"]:visible').wait_for()
    expect(page.locator('.mytasks.mt-find-active')).to_have_count(1)
    # A child result must remain bright even when its parent card does not match.
    _wait_opacity(page, '.mt-find-current[data-key="DL-108"]', .95, 1)
    assert page.locator('.mt-gcard2 > .mt-gh').evaluate(
        'el => getComputedStyle(el).backgroundColor') == 'rgba(0, 0, 0, 0)'
    _wait_opacity(page, other_selector, 0, other_opacity * .7)
    _wait_opacity(page, '.mt-tiles', 0, summary_opacity * .9)
    _wait_opacity(page, '.mt-options-panel', sidebar_opacity - .01, sidebar_opacity + .01)
    sidebar_box = page.locator('.mt-options-panel').bounding_box()
    find_box = page.locator('.mt-find').bounding_box()
    assert find_box["x"] >= sidebar_box["x"] + sidebar_box["width"]
    _assert_bottom_find(page)
    page.screenshot(path=str(upload_path.parent / 'find-focus-desktop.png'), animations='disabled')
    assert page.get_by_role('separator', name="보기 옵션 너비 조절").is_visible()
    assert page.locator('.mt-find-count').inner_text() == "1 / 1"
    assert page.locator('.mt-gcard2.folded:visible').count() == 0
    query.fill('   ')
    expect(page.locator('.mytasks.mt-find-active')).to_have_count(0)
    _wait_opacity(page, other_selector, other_opacity - .01, other_opacity + .01)
    _wait_opacity(page, '.mt-tiles', summary_opacity - .01, summary_opacity + .01)
    query.fill('DL-108')
    page.locator('.mt-find-current[data-key="DL-108"]:visible').wait_for()
    query.press("Escape")
    page.locator('.mt-find').wait_for(state="hidden")
    expect(page.locator('.mytasks.mt-find-active')).to_have_count(0)
    _wait_opacity(page, other_selector, other_opacity - .01, other_opacity + .01)
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

    # Sidebar display filters change the same loaded search model without an upstream query.
    page.keyboard.press("Control+f")
    query.fill("DL-108")
    page.get_by_role('combobox', name="마감기한 필터").select_option('overdue')
    expect(page.locator('.mt-find-count')).to_have_text("0 / 0")
    page.get_by_role('combobox', name="마감기한 필터").select_option('')
    page.locator('.mt-find-current[data-key="DL-108"]:visible').wait_for()
    query.press("Escape")
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
    # Related children must obey the spotlight in both themes, without dimming their ancestor.
    page.keyboard.press("Control+f")
    query.fill("정합성")
    related_match = '.mt-card.rel.mt-find-match[data-key="DL-109"]'
    related_other = '.mt-card.rel[data-key="DL-110"]'
    _wait_opacity(page, related_match, .95, 1)
    _wait_opacity(page, related_other, 0, .3)
    assert page.locator(related_match).evaluate('el => getComputedStyle(el).backgroundColor') == page.locator(
        '.mt-find-current[data-key="DL-102"]').evaluate('el => getComputedStyle(el).backgroundColor')
    if page.locator('.guide-x').is_visible():
        page.locator('.guide-x').click()
    page.screenshot(path=str(upload_path.parent / 'find-focus-light.png'), animations='disabled')
    page.locator('.setmenu-trig').click()
    page.get_by_role('switch', name='다크/라이트', exact=True).click()
    page.locator('.setmenu-trig').click()
    page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
    _wait_opacity(page, related_match, .95, 1)
    _wait_opacity(page, related_other, 0, .3)
    assert page.locator(related_match).evaluate('el => getComputedStyle(el).backgroundColor') == page.locator(
        '.mt-find-current[data-key="DL-102"]').evaluate('el => getComputedStyle(el).backgroundColor')
    if page.locator('.guide-x').is_visible():
        page.locator('.guide-x').click()
    page.screenshot(path=str(upload_path.parent / 'find-focus-dark.png'), animations='disabled')
    page.locator('.setmenu-trig').click()
    page.get_by_role('switch', name='다크/라이트', exact=True).click()
    page.locator('.setmenu-trig').click()
    query.press('Escape')
    page.set_viewport_size({"width": 720, "height": 900})
    page.locator('.mytasks.ax-v').wait_for()
    band = page.locator('.mt-band.c-todo')
    page.locator('[data-status-control="todo"] input[type="checkbox"]').uncheck()
    page.get_by_role('button', name="보기 옵션 접기", exact=True).click()
    expect(band).to_have_class("mt-band c-todo closed")
    narrow_prefs = page.evaluate("localStorage.getItem('mytasks.opts')")
    page.keyboard.press("Control+f")
    query.fill("DL-108")
    page.locator('.mt-find-current[data-key="DL-108"]:visible').wait_for()
    _wait_opacity(page, '.mt-find-current[data-key="DL-108"]', .95, 1)
    _assert_bottom_find(page)
    page.screenshot(path=str(upload_path.parent / 'find-focus-narrow.png'), animations='disabled')
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
    page.set_viewport_size({"width": 380, "height": 800})
    query.fill('DL-24')
    page.locator('.mt-find-current[data-key="DL-240"]:visible').wait_for()
    _assert_bottom_find(page)
    _assert_clear_of_floating_actions(page)
    assert query.bounding_box()['width'] > 40
    page.get_by_role('button', name='다음 결과', exact=True).click()
    page.locator('.mt-find-current[data-key="DL-241"]:visible').wait_for()
    page.get_by_role('button', name='이전 결과', exact=True).click()
    page.locator('.mt-find-current[data-key="DL-240"]:visible').wait_for()
    query.fill('no matching fixture')
    expect(page.locator('.mt-find-count')).to_have_text('0 / 0')
    _assert_clear_of_floating_actions(page)
    query.fill('정합성')
    page.locator('.mt-find-current:visible').wait_for()
    page.screenshot(path=str(upload_path.parent / 'find-focus-380.png'), animations='disabled')
    page.locator('.mt-find button').last.click()
    page.locator('.mt-find').wait_for(state='hidden')
    expect(page.locator('.mytasks.mt-find-active')).to_have_count(0)
    assert page.url == base + '/#/mytasks'
    assert 'Lake Task Manager' in page.title()
    (upload_path.parent / 'find-focus-page.json').write_text(json.dumps({
        'url': page.url, 'title': page.title(), 'console': console_issues,
        'pageErrorsAndHttpErrors': errors, 'viewports': [1720, 720, 380],
    }, ensure_ascii=False), encoding='utf-8')
    assert not console_issues
    assert not errors


def test_find_does_not_intercept_dialog_editor_or_other_route(editor_browser):
    from playwright.sync_api import expect

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
