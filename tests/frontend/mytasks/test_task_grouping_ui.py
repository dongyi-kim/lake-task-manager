"""Real Vue section grouping, global pagination, and incremental DOM reuse."""
import copy
import json
from urllib.parse import parse_qs, urlparse

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401


def test_grouping_preserves_bundles_paging_and_cards_without_requery(editor_browser):
    page, base, errors, _ = editor_browser

    def task(key, epic=None, **extra):
        row = {"key": key, "title": "Task " + key, "type": "Task", "mine": True,
               "statusCategory": "todo", "assignee": "담당자", "assigneeId": "admin",
               "priRank": 2, "pri": "Medium", "dueDays": None, "epic": epic,
               "hasSubs": False, "childrenPending": False, "childrenLoaded": True, **extra}
        return {**row, "atoms": [row], "others": []}

    parent = task("DL-100", "DL-9001", hasSubs=True, kidsTotal=2, kidsDone=0)
    parent["atoms"] = [task("DL-101")["atoms"][0],
                       task("DL-102", statusCategory="inprogress")["atoms"][0]]
    groups = [parent] + [task(f"DL-{i}", "DL-9001") for i in range(200, 243)]
    groups += [task("DL-400", "DL-9002"), task("DL-500", voc=True), task("DL-501")]
    model = {"groups": groups, "epics": [{"key": key, "title": title, "pending": False}
             for key, title in [("DL-9001", "Alpha"), ("DL-9002", "Beta")]],
             "groupingMeta": {"revision": "fixture", "wbs": [
                 {"id": "A", "module": "First", "name": "First WBS", "epics": ["DL-9001"]},
                 {"id": "B", "module": "Second", "name": "Second WBS", "epics": ["DL-9001", "DL-9002"]}]},
             "counts": {"total": 48, "overdue": 0, "today": 0, "week": 0, "done": 0}}
    requests = []

    def stream(route):
        requests.append(route.request.url)
        token = parse_qs(urlparse(route.request.url).query)["requestToken"][0]
        route.fulfill(content_type="application/x-ndjson", body=json.dumps({
            "contract": "task-snapshot.v1", "type": "snapshot", "requestToken": token,
            "sequence": 1, "done": True, "model": copy.deepcopy(model)}) + "\n")

    page.add_init_script("localStorage.setItem('mytasks.opts', JSON.stringify({groupBy:'wbs',subView:'all'}))")
    page.route("**/api/mytasks/stream?*", stream)
    # Layout changes can leave the pointer over a synthetic card and open its hover badge.
    page.route("**/api/ticket/*/badge", lambda route: route.fulfill(json={
        "key": route.request.url.split("/")[-2], "title": "Fixture Task", "type": "Task"}))
    page.goto(base + "/#/mytasks")
    first = page.locator('[data-task-section="wbs:A"]')
    first.locator('.mt-card[data-key="DL-100"]').wait_for()
    assert "DL-9001 다른 소속: Second · Second WBS" in first.inner_text()
    assert page.locator('.mt-card[data-key="DL-100"]:visible').count() == 1
    assert first.locator('.mt-card[data-key="DL-102"]').count() == 1
    assert page.locator('.mt-axis-more-btn').count() == 1
    assert "40 /" in page.locator('.mt-axis-more-btn').inner_text()
    page.locator('.mt-axis-more-btn').click()
    page.locator('[data-task-section="wbs:B"] .mt-card[data-key="DL-400"]').wait_for()
    assert page.locator('[data-task-section="__unmapped__"] .mt-card:visible').count() == 2
    assert page.locator('.mt-axis-more-btn').count() == 0
    initial_requests = len(requests)

    select = page.locator('.mt-opt', has_text="그룹화").locator('select')
    for mode, section in [("epic", "DL-9001"), ("sub", "__all__"), ("none", "__all__"), ("wbs", "wbs:A")]:
        select.select_option(mode)
        page.locator(f'[data-task-section="{section}"] .mt-card').first.wait_for()
        assert len(requests) == initial_requests, "layout changes must not fetch Jira/Task data"
    page.set_viewport_size({"width": 720, "height": 900})
    page.locator('.mytasks.ax-v').wait_for()
    assert first.locator('.mt-band').count() == 3
    assert first.locator('.mt-band.c-inprogress .mt-card[data-key="DL-102"]').is_visible()
    page.set_viewport_size({"width": 1720, "height": 1080})
    page.locator('.mytasks.ax-h').wait_for()

    # An unrelated changed ticket must not replace an unchanged card or its section DOM.
    anchor = first.locator('.mt-card[data-key="DL-200"]').element_handle()
    model["groups"][2]["title"] = model["groups"][2]["atoms"][0]["title"] = "Updated Task"
    with page.expect_response("**/api/mytasks/stream?*"):
        page.evaluate("window.dispatchEvent(new CustomEvent('ticket-changed', {detail:{key:'DL-201'}}))")
    page.locator('.mt-card[data-key="DL-201"]', has_text="Updated Task").wait_for()
    assert anchor.evaluate("el => el.isConnected && el === document.querySelector('.mt-card[data-key=\"DL-200\"]')")
    print("WBS/Epic/Task/none, 40-item paging, shared membership, responsive sections, unchanged DOM: passed")
    assert not errors
