"""Real Vue section grouping, global pagination, and incremental DOM reuse."""
import copy
import json
from urllib.parse import parse_qs, urlparse

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401


def test_grouping_preserves_bundles_paging_and_cards_without_requery(editor_browser):
    page, base, errors, upload_path = editor_browser
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    # Network-free UI checks use the app's system-font fallback, not a CDN request.
    page.route("https://cdn.jsdelivr.net/gh/orioncactus/pretendard/**",
               lambda route: route.fulfill(content_type="text/css", body=""))

    def task(key, epic=None, **extra):
        row = {"key": key, "title": "Task " + key, "type": "Task", "mine": True,
               "queryMatched": True,
               "statusCategory": "todo", "assignee": "담당자", "assigneeId": "admin",
               "priRank": 2, "pri": "Medium", "dueDays": None, "epic": epic,
               "hasSubs": False, "childrenPending": False, "childrenLoaded": True, **extra}
        return {**row, "atoms": [row], "others": []}

    parent = task("DL-100", "DL-9001", hasSubs=True, kidsTotal=2, kidsDone=0, dueDays=14)
    parent["atoms"] = [task("DL-101", pri="High", priRank=1)["atoms"][0],
                       task("DL-102", statusCategory="inprogress", dueDays=2)["atoms"][0]]
    groups = [parent] + [task(f"DL-{i}", "DL-9001") for i in range(200, 243)]
    groups += [task("DL-400", "DL-9002", statusCategory="inprogress", pri="High", priRank=1, dueDays=-2),
               task("DL-500", voc=True), task("DL-501", pri="", priRank=999)]
    compact = task("DL-600", "DL-9002", hasSubs=True, kidsTotal=1, kidsDone=0)
    compact["atoms"] = [task("DL-601")["atoms"][0]]
    groups.append(compact)
    model = {"groups": groups, "epics": [{"key": key, "title": title, "pending": False}
             for key, title in [("DL-9001", "Alpha — 여러 모듈이 함께 진행하는 아주 긴 이름의 Epic 구현과 검증"),
                                ("DL-9002", "Beta")]],
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

    page.add_init_script("if (!localStorage.getItem('mytasks.opts')) localStorage.setItem('mytasks.opts', JSON.stringify({groupBy:'wbs',subView:'all'}))")
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

    # Status is a board-wide setting. Group headers only identify lanes, while
    # the one side-panel control set must update every lane without another request.
    header = page.locator('.mt-board-head.mt-headrow')
    assert header.count() == 1
    assert page.locator('[data-task-section] .mt-board-head').count() == 0
    assert header.locator('.mt-status-heading').count() == 3
    assert header.locator('button, select, input').count() == 0
    assert page.locator('.mt-section-header [aria-expanded]').count() == 0
    controls = page.locator('.mt-status-controls')
    assert controls.count() == 1
    assert controls.locator('[data-status-control]').count() == 3
    options_panel = page.locator('.mt-options-panel')
    options_content = options_panel.locator('.mt-options-content')
    assert options_panel.count() == 1 and options_content.is_visible()
    assert page.title() == "Lake Task Manager"
    epic_choices = options_panel.locator('.mt-epic-check > span').all_text_contents()
    assert epic_choices[:2] == ["모든 Epic", "사용자 VoC"]
    assert epic_choices[2].startswith("Alpha") and epic_choices[3] == "Beta"
    panel_box = options_panel.bounding_box()
    assert panel_box["x"] == 0
    assert abs(panel_box["y"] + panel_box["height"] - 1080) < 1
    assert abs(panel_box["y"] - page.locator('header.top').bounding_box()["height"]) < 1
    fold_box = page.get_by_role('button', name="보기 옵션 접기", exact=True).bounding_box()
    assert abs(fold_box["x"] + fold_box["width"] / 2 - panel_box["width"]) < 2
    assert abs(fold_box["y"] + fold_box["height"] / 2 - panel_box["y"] - panel_box["height"] / 2) < 1

    # Drag, keyboard and reset all resize the same panel without querying Jira.
    grip = page.get_by_role('separator', name="보기 옵션 너비 조절", exact=True)
    grip_box = grip.bounding_box()
    page.mouse.move(grip_box["x"] + 3, grip_box["y"] + 80)
    page.mouse.down()
    page.mouse.move(grip_box["x"] + 103, grip_box["y"] + 80, steps=5)
    page.mouse.up()
    assert abs(options_panel.bounding_box()["width"] - 320) < 2
    assert page.evaluate("JSON.parse(localStorage.getItem('mytasks.opts')).optionsWidth") == 320
    assert page.evaluate("document.body.style.userSelect") == ""
    grip.press("End")
    assert options_panel.bounding_box()["width"] == 460
    grip.dblclick(position={"x": 3, "y": 80})
    assert options_panel.bounding_box()["width"] == 220
    grip.press("ArrowRight")
    assert options_panel.bounding_box()["width"] == 236
    # Losing focus mid-drag releases pointer handlers and leaves a usable panel.
    grip_box = grip.bounding_box()
    page.mouse.move(grip_box["x"] + 3, grip_box["y"] + 80)
    page.mouse.down()
    page.mouse.move(grip_box["x"] + 87, grip_box["y"] + 80)
    page.evaluate("window.dispatchEvent(new Event('blur'))")
    page.mouse.up()
    assert options_panel.bounding_box()["width"] == 320
    assert page.evaluate("document.body.style.cursor") == ""
    assert len(requests) == initial_requests

    epic_label = options_panel.locator('.mt-epic-check[title^="Alpha"] > span')
    assert epic_label.evaluate("el => { const s=getComputedStyle(el); return s.whiteSpace === 'nowrap' && s.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth; }")
    assert epic_label.bounding_box()["height"] < 20
    page.evaluate("document.activeElement.blur(); window.scrollTo(0, 0)")
    page.locator('.mt-options-heading').click()
    page.mouse.move(500, 20)
    page.screenshot(path=str(upload_path.parent / "task-sidebar-desktop.png"))
    board_width = page.locator('.mt-board').bounding_box()["width"]
    page.get_by_role('button', name="보기 옵션 접기", exact=True).click()
    options_content.wait_for(state="hidden")
    assert options_panel.bounding_box()["width"] == 30
    page.wait_for_function("width => document.querySelector('.mt-board').getBoundingClientRect().width > width + 100", arg=board_width)
    assert len(requests) == initial_requests
    header.locator('.mt-status-heading').first.click()
    page.mouse.move(500, 20)
    page.screenshot(path=str(upload_path.parent / "task-sidebar-collapsed.png"))
    page.get_by_role('button', name="보기 옵션 펼치기", exact=True).click()
    options_content.wait_for(state="visible")
    assert len(requests) == initial_requests
    assert options_panel.locator('.mt-opt', has_text="그룹화").locator('select').input_value() == "wbs"
    progress = controls.get_by_role('checkbox', name="진행 중 표시", exact=True)
    progress.uncheck()
    assert not first.locator('.mt-card[data-key="DL-102"]').is_visible()
    assert not page.locator('.mt-card[data-key="DL-400"]').is_visible()
    progress.check()
    assert first.locator('.mt-card[data-key="DL-102"]').is_visible()
    assert page.locator('.mt-card[data-key="DL-400"]').is_visible()
    assert len(requests) == initial_requests
    for key in ("DL-101", "DL-601"):
        row = page.locator(f'.mt-card.tkt[data-key="{key}"]')
        assert row.count() == 1 and row.bounding_box()["height"] < 40

    select = page.locator('.mt-opt', has_text="그룹화").locator('select')
    priority = options_panel.get_by_label("우선순위 필터", exact=True)
    due = options_panel.get_by_label("마감기한 필터", exact=True)

    def visible_keys():
        return set(page.locator('.mt-card[data-key]:visible').evaluate_all(
            "rows => rows.map(row => row.dataset.key)"))

    def total_count():
        return int(page.locator('.mt-tile', has_text="전체").locator('b').inner_text())

    # Local filters apply to atomic cards. A nonmatching parent remains context
    # for its matching child, but must not inflate counts or appear in flat mode.
    priority.select_option("name:High")
    assert visible_keys() == {"DL-100", "DL-101", "DL-400"}
    assert total_count() == 2
    assert header.locator('.c-todo b').inner_text() == "1"
    assert header.locator('.c-inprogress b').inner_text() == "1"
    due.select_option("calm")
    assert visible_keys() == {"DL-100", "DL-101"}, "child inherits its parent's +14-day due date"
    assert total_count() == 1
    select.select_option("none")
    assert visible_keys() == {"DL-101"}
    assert total_count() == 1
    select.select_option("wbs")
    due.select_option("soon")
    assert visible_keys() == set() and total_count() == 0
    priority.select_option("name:Medium")
    assert visible_keys() == {"DL-100", "DL-102"}
    assert total_count() == 1
    assert page.locator('.mt-tile.week b').inner_text() == "1"
    due.select_option("calm")
    assert visible_keys() == {"DL-100", "DL-101"}, "Medium or higher retains the High child"
    assert total_count() == 1 and header.locator('.c-todo b').inner_text() == "1"
    select.select_option("none")
    assert visible_keys() == {"DL-101"} and total_count() == 1
    select.select_option("wbs")
    priority.select_option("name:High")
    due.select_option("overdue")
    assert visible_keys() == {"DL-400"}
    assert total_count() == 1
    assert page.locator('.mt-tile.over b').inner_text() == "1"
    priority.select_option("__none__")
    due.select_option("none")
    assert visible_keys() == {"DL-501"}
    assert total_count() == 1
    priority.select_option("")
    due.select_option("")
    assert page.locator('.mt-axis-more-btn').count() == 1, "changing local filters resets pagination"
    page.locator('.mt-axis-more-btn').click()
    assert "DL-601" in visible_keys()
    assert len(requests) == initial_requests, "priority/due/grouping are local display filters"

    for mode, section in [("epic", "DL-9001"), ("sub", "__all__"), ("none", "__all__"), ("wbs", "wbs:A")]:
        select.select_option(mode)
        page.locator(f'[data-task-section="{section}"] .mt-card').first.wait_for()
        assert len(requests) == initial_requests, "layout changes must not fetch Jira/Task data"
    # Axis follows the board's available width, including sidebar width, rather
    # than viewport width alone. Both transitions retain data and stay query-free.
    page.set_viewport_size({"width": 1100, "height": 900})
    page.locator('.mytasks.ax-v').wait_for()
    page.get_by_role('button', name="보기 옵션 접기", exact=True).click()
    page.locator('.mytasks.ax-h').wait_for()
    page.get_by_role('button', name="보기 옵션 펼치기", exact=True).click()
    page.locator('.mytasks.ax-v').wait_for()
    assert len(requests) == initial_requests
    page.set_viewport_size({"width": 720, "height": 900})
    page.locator('.mytasks.ax-v').wait_for()
    assert first.locator('.mt-band').count() == 3
    assert first.locator('.mt-band.c-inprogress .mt-card[data-key="DL-102"]').is_visible()
    assert first.locator('.mt-bandh-w button, .mt-bandh-w select').count() == 0
    assert header.count() == 1
    # Mobile options overlay the board instead of taking its grid track. An
    # open fixed drawer must not leave the board in the narrow toggle column.
    page.set_viewport_size({"width": 390, "height": 900})
    mobile_board_width = page.locator('.mt-board').bounding_box()["width"]
    assert mobile_board_width > 240
    page.get_by_role('button', name="보기 옵션 접기", exact=True).click()
    options_content.wait_for(state="hidden")
    assert abs(page.locator('.mt-board').bounding_box()["width"] - mobile_board_width) < 2
    page.get_by_role('button', name="보기 옵션 펼치기", exact=True).click()
    options_content.wait_for(state="visible")
    assert abs(page.locator('.mt-board').bounding_box()["width"] - mobile_board_width) < 2
    assert options_panel.bounding_box()["x"] == 0
    assert abs(options_panel.bounding_box()["y"] + options_panel.bounding_box()["height"] - 900) < 1
    page.locator('.mt-options-heading').click()
    page.screenshot(path=str(upload_path.parent / "task-sidebar-mobile.png"))
    page.set_viewport_size({"width": 320, "height": 900})
    page.wait_for_function("document.querySelector('.mt-options-panel').getBoundingClientRect().width <= 272")
    assert page.evaluate("JSON.parse(localStorage.getItem('mytasks.opts')).optionsWidth") == 320
    page.set_viewport_size({"width": 390, "height": 900})
    progress.uncheck()
    assert not first.locator('.mt-band.c-inprogress .mt-card[data-key="DL-102"]').is_visible()
    assert not page.locator('.mt-card[data-key="DL-400"]').is_visible()
    assert len(requests) == initial_requests
    page.set_viewport_size({"width": 1720, "height": 1080})
    page.locator('.mytasks.ax-h').wait_for()
    assert not progress.is_checked(), "responsive layout must preserve shared folding"
    progress.check()
    assert first.locator('.mt-card[data-key="DL-102"]').is_visible()

    # Period is a query scope, unlike grouping/folding. Its sole control sends
    # the chosen range and updates the shared status summary.
    with page.expect_response("**/api/mytasks/stream?*"):
        controls.get_by_label("최근 완료 조회 기간", exact=True).select_option("1m")
    assert parse_qs(urlparse(requests[-1]).query)["doneFilter"] == ["1m"]
    assert controls.get_by_label("최근 완료 조회 기간", exact=True).input_value() == "1m"

    # An unrelated changed ticket must not replace an unchanged card or its section DOM.
    anchor = first.locator('.mt-card[data-key="DL-200"]').element_handle()
    model["groups"][2]["title"] = model["groups"][2]["atoms"][0]["title"] = "Updated Task"
    with page.expect_response("**/api/mytasks/stream?*"):
        page.evaluate("window.dispatchEvent(new CustomEvent('ticket-changed', {detail:{key:'DL-201'}}))")
    page.locator('.mt-card[data-key="DL-201"]', has_text="Updated Task").wait_for()
    assert anchor.evaluate("el => el.isConnected && el === document.querySelector('.mt-card[data-key=\"DL-200\"]')")

    # The side panel's visibility is a durable UI preference, independent of
    # selected filters. Reopening it after reload must not reset those choices.
    priority.select_option("name:High")
    due.select_option("calm")
    progress.uncheck()
    page.get_by_role('button', name="보기 옵션 접기", exact=True).click()
    assert page.evaluate("JSON.parse(localStorage.getItem('mytasks.opts')).optionsClosed") is True
    page.reload()
    first.locator('.mt-card[data-key="DL-100"]').wait_for()
    options_content.wait_for(state="hidden")
    after_reload_requests = len(requests)
    page.get_by_role('button', name="보기 옵션 펼치기", exact=True).click()
    options_content.wait_for(state="visible")
    assert options_panel.bounding_box()["width"] == 320
    assert select.input_value() == "wbs"
    assert controls.get_by_label("최근 완료 조회 기간", exact=True).input_value() == "1m"
    assert not progress.is_checked()
    assert priority.input_value() == "name:High" and due.input_value() == "calm"
    assert visible_keys() == {"DL-100", "DL-101"}
    assert total_count() == 1
    priority.select_option("")
    due.select_option("")
    assert len(requests) == after_reload_requests

    # Ownership does not imply membership in the original query. A long-resolved
    # context parent outside its date/JQL window must not return as a local match.
    # Give the High child its own near deadline so the cumulative priority filter
    # leaves only the parent's calm deadline in this scenario.
    parent["atoms"][0]["dueDays"] = 2
    with page.expect_response("**/api/mytasks/stream?*"):
        page.evaluate("window.dispatchEvent(new CustomEvent('ticket-changed', {detail:{key:'DL-101'}}))")
    priority.select_option("name:Medium")
    due.select_option("calm")
    assert visible_keys() == {"DL-100"} and total_count() == 1
    parent.update(queryMatched=False, statusCategory="done", status="Resolved",
                  resolved="2025-01-01T00:00:00.000+0000")
    with page.expect_response("**/api/mytasks/stream?*"):
        page.evaluate("window.dispatchEvent(new CustomEvent('ticket-changed', {detail:{key:'DL-100'}}))")
    page.locator('.mt-card[data-key="DL-100"]').wait_for(state="hidden")
    assert visible_keys() == set() and total_count() == 0
    select.select_option("none")
    assert visible_keys() == set() and total_count() == 0
    grip_box = grip.bounding_box()
    page.mouse.move(grip_box["x"] + 3, grip_box["y"] + 80)
    page.mouse.down()
    page.goto(base + "/#/")
    page.locator('.mytasks').wait_for(state="detached")
    page.mouse.up()
    assert page.evaluate("document.body.style.userSelect") == ""
    assert page.evaluate("document.body.style.cursor") == ""
    print("Local priority/deadline filtering and counts, side options/persistence, shared status controls, grouping/paging, responsive sections, unchanged DOM: passed")
    assert not errors
