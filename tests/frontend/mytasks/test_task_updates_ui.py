"""Real Tasks board: local update membership, grouping, and visual status cues."""
import copy
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401


@pytest.fixture()
def updates_board(editor_browser):
    page, base, errors, upload_path = editor_browser
    now = int(time.time() * 1000)
    hour = 60 * 60 * 1000
    activity = {}
    streams = []
    metadata_requests = []
    acknowledgments = []

    def row(key, *, changed=None, viewed=None, **extra):
        latest = now + changed * hour if changed is not None else None
        activity[key] = {"lastViewedAt": now + viewed * hour if viewed is not None else None,
                         "latestRelevantAt": latest, "latestCommentAt": None}
        return {"key": key, "title": "검증 업무 " + key, "type": "Task", "mine": True,
                "queryMatched": True, "statusCategory": "todo", "assignee": "UI픽스처01",
                "assigneeId": "test.ui01", "priRank": 2, "pri": "Medium", "dueDays": None,
                "updated": datetime.fromtimestamp(latest / 1000, timezone.utc).isoformat()
                if latest else None,
                "epic": "DL-9000", "hasSubs": False, "childrenPending": False,
                "childrenLoaded": True, **extra}

    def solo(key, **extra):
        atom = row(key, **extra)
        return {**atom, "atoms": [atom], "others": []}

    mixed = row("DL-100", changed=-200, hasSubs=True, kidsTotal=2, kidsDone=0, dueDays=14)
    mixed.update(atoms=[row("DL-101", changed=-2, viewed=-4, isSub=True, dueDays=14),
                        row("DL-102", changed=-2, viewed=-1, isSub=True,
                            statusCategory="inprogress", dueDays=14)], others=[])
    parent_only = row("DL-200", changed=-1, viewed=-4, hasSubs=True, kidsTotal=1, kidsDone=0)
    parent_only.update(atoms=[row("DL-201", changed=-200, isSub=True)], others=[])
    inherited = row("DL-310", changed=-200, hasSubs=True, kidsTotal=1, kidsDone=0,
                    dueDays=7, due="2026-11-12")
    inherited.update(atoms=[row("DL-311", changed=-200, isSub=True)], others=[])
    rolled_priority = row("DL-320", changed=-1, hasSubs=True, kidsTotal=1, kidsDone=0,
                          pri="Low", priRank=0)
    rolled_priority.update(atoms=[row("DL-321", changed=-200, isSub=True,
                                     pri="Blocker", priRank=0)], others=[])
    groups = [mixed, parent_only, inherited, rolled_priority,
              solo("DL-9007", changed=-1, viewed=-3, dueDays=2),
              solo("DL-301", changed=-2, viewed=-1, dueDays=14, pri="Low", priRank=3),
              solo("DL-302", changed=-200, dueDays=14),
              solo("DL-303"),
              solo("DL-304", changed=-1, dueDays=14, pri="High", priRank=1),
              solo("DL-305", changed=-1, viewed=-4, statusCategory="done", dueDays=-1,
                   resolved="2026-11-05T09:00:00+09:00", pri="Lowest", priRank=4),
              solo("DL-306", changed=-200, dueDays=7),
              solo("DL-307", changed=-200, dueDays=8),
              solo("DL-308", changed=-200),
              solo("DL-309", changed=-200, dueDays=-1)]
    groups += [solo(f"DL-{index}", changed=-200) for index in range(400, 445)]
    groups += [solo("DL-999", changed=-1)]
    model = {"groups": groups, "epics": [{"key": "DL-9000", "title": "UI 회귀", "pending": False}],
             "groupingMeta": {"revision": "updates", "wbs": [
                 {"id": "updates", "module": "TEST", "name": "업데이트 검증", "epics": ["DL-9000"]}]},
             "counts": {"total": 61, "overdue": 1, "today": 0, "week": 2, "done": 1}}

    def stream(route):
        streams.append(route.request.url)
        token = parse_qs(urlparse(route.request.url).query)["requestToken"][0]
        route.fulfill(content_type="application/x-ndjson", body=json.dumps({
            "contract": "task-snapshot.v1", "type": "snapshot", "requestToken": token,
            "sequence": 1, "done": True, "model": copy.deepcopy(model)}) + "\n")

    def metadata(route):
        keys = route.request.post_data_json["keys"]
        metadata_requests.append(keys)
        route.fulfill(json={"tickets": {key: copy.deepcopy(activity[key]) for key in keys if key in activity},
                            "observedAt": now})

    def acknowledge(route):
        body = route.request.post_data_json
        acknowledgments.append(body)
        key = body["key"]
        activity[key]["lastViewedAt"] = max(activity[key]["lastViewedAt"] or 0, body["viewedThrough"])
        route.fulfill(json={"key": key, "lastViewedAt": activity[key]["lastViewedAt"]})

    def detail(route):
        # Retain the actual fixture detail, but make its revision agree with the
        # board's synthetic activity. Mock's fixed18:00 may be in the future.
        response = route.fetch()
        view = response.json()
        view["updated"] = datetime.fromtimestamp(
            activity["DL-9007"]["latestRelevantAt"] / 1000, timezone.utc).isoformat()
        route.fulfill(response=response, json=view)

    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.route("https://cdn.jsdelivr.net/gh/orioncactus/pretendard/**",
               lambda route: route.fulfill(content_type="text/css", body=""))
    page.route("**/api/mytasks/stream?*", stream)
    page.route("**/api/tickets/activity", metadata)
    page.route("**/api/tickets/viewed", acknowledge)
    page.route(re.compile(r"/api/ticket/DL-9007(?:\?.*)?$"), detail)
    page.route("**/api/ticket/*/badge", lambda route: route.fulfill(json={
        "key": route.request.url.split("/")[-2], "title": "검증 업무", "type": "Task"}))
    page.add_init_script("if (!localStorage.getItem('mytasks.opts')) localStorage.setItem('mytasks.opts', JSON.stringify({groupBy:'sub',subView:'all'}))")
    page.add_init_script("localStorage.setItem('lake.guides.seen', JSON.stringify(['global-search-recent-1','quick-open-hotkey-1','browser-localhost-access-1','local-mcp-connect-1','refresh-fab-1']))")
    page.goto(base + "/#/mytasks")
    page.locator('.mt-card[data-key="DL-100"]').wait_for()
    yield {"page": page, "base": base, "errors": errors, "activity": activity,
           "streams": streams, "metadata_requests": metadata_requests, "now": now,
           "acknowledgments": acknowledgments,
           "screenshot_dir": upload_path.parent}


def _visible_keys(page):
    return set(page.locator('.mt-card[data-key]:visible').evaluate_all(
        "rows => rows.map(row => row.dataset.key)"))


def _total(page):
    return int(page.locator('.mt-tile', has_text="전체").locator('b').inner_text())


def _assert_blue_edges(card):
    from playwright.sync_api import expect

    accent = card.evaluate("""el => {
      const probe=document.createElement('i'); probe.style.color='var(--accent)'; el.append(probe);
      const accent=getComputedStyle(probe).color; probe.remove();
      return accent;
    }""")
    for side in ("top", "right", "bottom"):
        expect(card).to_have_css(f"border-{side}-color", accent)
    expect(card).to_have_css("box-shadow", re.compile(r"(?:.*inset){3}"))


def test_updates_filter_precedes_paging_and_compact_layout_without_requery(updates_board):
    ctx = updates_board
    page = ctx["page"]
    control = page.get_by_label("업데이트 필터", exact=True)
    assert control.locator("option:checked").inner_text() == "모두"
    assert control.locator("option").all_text_contents() == ["있음", "없음", "모두"]
    assert control.bounding_box()["y"] > page.get_by_label("마감기한 필터", exact=True).bounding_box()["y"]
    panel = page.locator('.mt-options-panel')
    assert panel.get_by_text("필터 조건", exact=True).is_visible()
    assert panel.get_by_text("보기 옵션", exact=True).count() >= 1
    page.locator('.mt-axis-more-btn').wait_for()
    assert "DL-999" not in _visible_keys(page)
    requests = len(ctx["streams"])

    control.select_option(label="있음")
    page.locator('.mt-card[data-key="DL-999"]').wait_for()
    assert _visible_keys(page) == {"DL-100", "DL-101", "DL-200", "DL-320", "DL-9007", "DL-304", "DL-305", "DL-999"}
    assert _total(page) == 7, "context parent does not inflate the matching atomic count"
    assert page.locator('.mt-axis-more-btn').count() == 0
    assert page.locator('.mt-compact-flow', has=page.locator('.mt-card[data-key="DL-100"]')).count() == 1
    parent_only = page.locator('.mt-compact-flow', has=page.locator('.mt-card[data-key="DL-200"]'))
    assert parent_only.count() == 1
    assert "현재 필터에 해당하는 SubTask 없음" in parent_only.inner_text()
    assert "동기화 중" not in parent_only.inner_text()
    assert len(ctx["streams"]) == requests

    # The same predicate drives every grouping mode, not just one renderer.
    group_by = page.locator('.mt-opt', has_text="그룹화").locator('select')
    group_by.select_option("none")
    assert _visible_keys(page) == {"DL-101", "DL-200", "DL-320", "DL-9007", "DL-304", "DL-305", "DL-999"}
    for mode in ("epic", "wbs", "sub"):
        group_by.select_option(mode)
        assert "DL-100" in _visible_keys(page)
        assert _total(page) == 7
    assert len(ctx["streams"]) == requests

    priority = page.get_by_label("우선순위 필터", exact=True)
    priority.select_option("name:Medium")
    assert _visible_keys(page) == {"DL-100", "DL-101", "DL-200", "DL-9007", "DL-304", "DL-999"}
    assert _total(page) == 5, "threshold includes more urgent ranks, excludes Low/Lowest"
    priority.select_option("name:High")
    assert _visible_keys(page) == {"DL-304"} and _total(page) == 1
    assert "DL-320" not in _visible_keys(page), "Low parent must not inherit a child's rolled-up Blocker rank"
    priority.select_option("")

    page.get_by_label("마감기한 필터", exact=True).select_option("soon")
    assert _visible_keys(page) == {"DL-9007"} and _total(page) == 1
    page.get_by_label("마감기한 필터", exact=True).select_option("")
    control.select_option(label="없음")
    page.locator('.mt-card[data-key="DL-102"]').wait_for()
    assert "DL-101" not in _visible_keys(page) and "DL-9007" not in _visible_keys(page)
    assert "DL-301" in _visible_keys(page) and "DL-302" in _visible_keys(page)
    assert "DL-303" in _visible_keys(page), "missing event timestamps are not invented"
    assert page.locator('.mt-gcard2 .mt-card.parent[data-key="DL-100"]').count() == 1
    assert len(ctx["streams"]) == requests

    page.reload()
    page.locator('.mt-card[data-key="DL-102"]').wait_for()
    assert control.locator("option:checked").inner_text() == "없음"
    requests = len(ctx["streams"])
    control.select_option(label="모두")
    assert page.locator('.mt-axis-more-btn').count() == 1
    assert len(ctx["streams"]) == requests
    assert not ctx["errors"], ctx["errors"]


def test_urgency_is_next_to_due_and_update_styling_keeps_semantic_edges(updates_board):
    page = updates_board["page"]
    page.mouse.move(500, 20)
    urgent = page.locator('.mt-card[data-key="DL-9007"]')
    urgent.locator('.tc-update-mark').wait_for()
    assert "update-unread" in urgent.get_attribute("class")
    urgent.locator('.tc-when').get_by_text("🔥", exact=True).wait_for()
    for key in ("DL-9007", "DL-306", "DL-309", "DL-311"):
        card = page.locator(f'.mt-card[data-key="{key}"]')
        due = card.locator('.tc-when')
        assert "🔥" in due.inner_text()
        assert due.locator('b').evaluate("el => Number(getComputedStyle(el).fontWeight) >= 700")
        assert due.locator('b').evaluate("el => { const c=getComputedStyle(el).color.match(/\\d+/g).map(Number); return c[0]>c[1] && c[0]>c[2]; }")
        flame = due.get_by_text("🔥", exact=True)
        assert flame.evaluate("el => { const s=getComputedStyle(el);return s.borderLeftWidth==='0px' && s.borderRightWidth==='0px'; }")
        assert flame.bounding_box()["x"] >= due.locator('b').bounding_box()["x"] + due.locator('b').bounding_box()["width"]
        assert card.locator(':scope > .tc-hot').count() == 0, "old corner is reserved for the update indicator"
    assert "↑" in page.locator('.mt-card[data-key="DL-311"] .tc-when').inner_text()
    for key in ("DL-305", "DL-307", "DL-308"):
        assert "🔥" not in page.locator(f'.mt-card[data-key="{key}"] .tc-when').inner_text()

    # Update emphasis may replace urgency's three border colors, never Epic's rail.
    old = page.locator('.mt-card[data-key="DL-301"]')
    assert urgent.evaluate("el => getComputedStyle(el).borderLeftColor") == old.evaluate("el => getComputedStyle(el).borderLeftColor")
    for key in ("DL-9007", "DL-101", "DL-200", "DL-305"):
        _assert_blue_edges(page.locator(f'.mt-card[data-key="{key}"]'))
    child = page.locator('.mt-card[data-key="DL-101"]')
    assert child.evaluate("el => getComputedStyle(el).borderLeftColor") == page.locator(
        '.mt-card[data-key="DL-102"]').evaluate("el => getComputedStyle(el).borderLeftColor")
    for selector in ('.mt-key', '.mt-title', '.mt-owner'):
        assert urgent.locator(selector).evaluate("el => Number(getComputedStyle(el).fontWeight) >= 700")
    assert page.locator('.mt-card[data-key="DL-305"] .mt-title').evaluate(
        "el => getComputedStyle(el).textDecorationLine.includes('line-through')")
    output = updates_board["screenshot_dir"]
    page.screenshot(path=str(output / "task-updates-desktop.png"), animations="disabled")
    page.get_by_role("button", name="설정", exact=True).click()
    page.get_by_role("switch", name="다크/라이트", exact=True).click()
    page.keyboard.press("Escape")
    assert page.locator('html').get_attribute("data-theme") == "dark"
    page.mouse.move(500, 20)
    page.screenshot(path=str(output / "task-updates-dark.png"), animations="disabled")
    for key in ("DL-9007", "DL-101", "DL-200", "DL-305"):
        _assert_blue_edges(page.locator(f'.mt-card[data-key="{key}"]'))
    urgent.hover()
    page.screenshot(path=str(output / "task-updates-hover.png"), animations="disabled")
    _assert_blue_edges(urgent)
    page.get_by_role("button", name="설정", exact=True).click()
    page.get_by_role("switch", name="다크/라이트", exact=True).click()
    page.keyboard.press("Escape")
    page.set_viewport_size({"width": 720, "height": 1000})
    page.get_by_role("button", name="보기 옵션 접기", exact=True).click()
    page.locator('.mytasks.ax-v').wait_for()
    assert "🔥" in page.locator('.mt-card[data-key="DL-9007"] .tc-when').inner_text()
    assert page.locator('.mt-card[data-key="DL-9007"] .tc-update-mark').count() == 1
    page.mouse.move(700, 10)
    page.screenshot(path=str(output / "task-updates-narrow.png"), animations="disabled")
    _assert_blue_edges(page.locator('.mt-card.parent[data-key="DL-200"]:visible'))
    assert page.locator('.mt-board').evaluate("el => el.getBoundingClientRect().right <= window.innerWidth + 1")
    assert not updates_board["errors"], updates_board["errors"]


def test_closing_loaded_ticket_moves_it_between_update_filters_without_jira_reload(updates_board):
    ctx = updates_board
    page = ctx["page"]
    control = page.get_by_label("업데이트 필터", exact=True)
    control.select_option("yes")
    card = page.locator('.mt-card.update-unread[data-key="DL-9007"]')
    card.wait_for()
    requests = len(ctx["streams"])
    with page.expect_response("**/api/tickets/viewed"):
        card.click()
        dialog = page.locator('.tkt-dlg')
        dialog.locator('.tkt-comments .tkt-cmt').first.wait_for()
        dialog.get_by_role("button", name="닫기", exact=True).click()
    dialog.wait_for(state="hidden")
    page.locator('.mt-card[data-key="DL-9007"]').wait_for(state="hidden")
    assert len(ctx["acknowledgments"]) == 1
    assert ctx["acknowledgments"][0]["key"] == "DL-9007"
    assert len(ctx["streams"]) == requests
    control.select_option("no")
    card = page.locator('.mt-card[data-key="DL-9007"]')
    card.wait_for()
    assert "update-unread" not in card.get_attribute("class")
    assert card.locator('.tc-update-mark').count() == 0
    assert len(ctx["streams"]) == requests
    assert not ctx["errors"], ctx["errors"]
