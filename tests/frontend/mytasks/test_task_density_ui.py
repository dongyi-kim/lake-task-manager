"""Real 24/48/200-child parents: filtering, folds, reachability and compact geometry."""
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401


# Count alone misses CSS display:none, max-height/overflow clipping and covered rows.
# Scroll every row through the real document; test its title center by hit-testing.
_REACHABILITY = r"""rows => {
  const failures = [], heights = [];
  for (const row of rows) {
    row.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
    const box = row.getBoundingClientRect(), title = row.querySelector('.mt-title');
    const text = title?.getBoundingClientRect(), style = getComputedStyle(row);
    heights.push(box.height);
    if (!text || box.height < 24 || box.height > 40 || text.width < 40 ||
        text.top < box.top - 1 || text.bottom > box.bottom + 1 ||
        style.display === 'none' || style.visibility === 'hidden') {
      failures.push({key:row.dataset.key,reason:'row/title geometry',height:box.height}); continue;
    }
    for (let ancestor = row.parentElement; ancestor; ancestor = ancestor.parentElement) {
      const css = getComputedStyle(ancestor), clip = ancestor.getBoundingClientRect();
      if ((/hidden|clip|auto|scroll/.test(css.overflowX) &&
           (box.left < clip.left - 2 || box.right > clip.right + 2)) ||
          (/hidden|clip|auto|scroll/.test(css.overflowY) &&
           (box.top < clip.top - 2 || box.bottom > clip.bottom + 2))) {
        failures.push({key:row.dataset.key,reason:'ancestor clipping',ancestor:ancestor.className}); break;
      }
    }
    const x = text.left + text.width / 2, y = text.top + text.height / 2;
    const hit = document.elementFromPoint(x,y);
    if (x < 0 || x >= innerWidth || y < 0 || y >= innerHeight || !hit || !row.contains(hit))
      failures.push({key:row.dataset.key,reason:'not reachable at title center',hit:hit?.className});
  }
  return {failures, minHeight:Math.min(...heights),maxHeight:Math.max(...heights)};
}"""


def _assert_no_document_overflow(page):
    result = page.evaluate("""() => ({
      width:document.documentElement.clientWidth,
      document:document.documentElement.scrollWidth,body:document.body.scrollWidth
    })""")
    assert result["document"] <= result["width"] + 2, result
    assert result["body"] <= result["width"] + 2, result


@pytest.mark.parametrize("child_count,width", [(24, 1280), (48, 1440), (200, 1720)])
def test_large_parent_preserves_every_filtered_child_and_access(editor_browser, child_count, width):
    from playwright.sync_api import expect

    page, base, errors, upload_path = editor_browser
    page.set_viewport_size({"width": width, "height": 1080})
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.route("https://cdn.jsdelivr.net/gh/orioncactus/pretendard/**",
               lambda route: route.fulfill(content_type="text/css", body=""))
    page.route("**/api/ticket/*/badge", lambda route: route.fulfill(json={
        "key": route.request.url.split("/")[-2], "title": "Density fixture", "type": "Sub-task"}))

    children = [{"key": f"DL-{10001 + i}",
                 "title": f"밀도 자손 {i + 1:03d} · 긴 한글 제목과 소유자 표시가 겹치지 않는지 확인하는 데이터 적재 점검",
                 "type": "Sub-task", "mine": True, "queryMatched": True,
                 "statusCategory": "todo", "assignee": "밀도 담당자", "assigneeId": "test.ui01",
                 "priRank": 2, "pri": "Medium", "dueDays": -1, "due": "2026-01-01",
                 "hasSubs": False, "childrenPending": False, "childrenLoaded": True}
                for i in range(child_count)]
    assert len({child["key"] for child in children}) == child_count
    parent = {"key": "DL-10000", "title": f"자손 {child_count}개인 실제 부모 사례",
              "type": "Task", "mine": True, "queryMatched": True, "statusCategory": "todo",
              "assignee": "부모 담당자", "assigneeId": "test.ui01", "priRank": 3, "pri": "Low",
              "dueDays": None, "hasSubs": True, "kidsTotal": child_count, "kidsDone": 0,
              "childrenPending": False, "childrenLoaded": True, "atoms": children, "others": []}
    model = {"groups": [parent], "epics": [],
             "counts": {"total": child_count + 1, "done": 0, "overdue": child_count, "today": 0, "week": 0}}
    requests = []

    def stream(route):
        requests.append(route.request.url)
        token = parse_qs(urlparse(route.request.url).query)["requestToken"][0]
        route.fulfill(content_type="application/x-ndjson", body=json.dumps({
            "contract": "task-snapshot.v1", "type": "snapshot", "requestToken": token,
            "sequence": 1, "done": True, "model": model}) + "\n")

    page.add_init_script("localStorage.setItem('mytasks.opts',JSON.stringify({groupBy:'sub',subView:'collapsed'}))")
    page.route("**/api/mytasks/stream?*", stream)
    page.goto(base + "/#/mytasks")
    flow = page.locator(".mt-compact-flow")
    flow.locator('.mt-card[data-key="DL-10000"]').wait_for()
    page.locator(".guide-bub").wait_for()
    page.locator(".guide-x").click()
    expect(page.locator(".mytasks.ax-h")).to_have_count(1)
    footer = flow.locator(".mt-subfoot")
    expect(footer).to_have_attribute("aria-expanded", "false")
    expect(flow.locator(".mt-compact-children")).to_have_count(0)
    initial_requests = len(requests)
    prefs = page.evaluate("localStorage.getItem('mytasks.opts')")
    last_key = children[-1]["key"]

    # Search must include every child before folding/paging, including the final child.
    page.keyboard.press("Control+f")
    query = page.get_by_role("textbox", name="카드에서 찾기")
    query.fill("밀도 자손")
    expect(page.locator(".mt-find-count")).to_have_text(f"1 / {child_count}")
    query.fill(last_key)
    found = page.locator(f'.mt-find-current[data-key="{last_key}"]')
    found.wait_for(state="visible")
    expect(page.locator(".mt-find-count")).to_have_text("1 / 1")
    reachable = found.evaluate_all(_REACHABILITY)
    assert not reachable["failures"], reachable
    _assert_no_document_overflow(page)
    query.press("Escape")
    expect(footer).to_have_attribute("aria-expanded", "false")
    assert page.evaluate("localStorage.getItem('mytasks.opts')") == prefs

    # Parent folding and the separate CSS peek/more affordance remain independently usable.
    footer.click()
    expect(footer).to_have_attribute("aria-expanded", "true")
    lane = flow.locator(".mt-compact-children")
    expect(lane.locator(".mt-card")).to_have_count(child_count)
    expect(lane).to_have_class(re.compile(r"\bfold-peek\b"))
    assert not lane.locator(f'[data-key="{last_key}"]').is_visible()
    lane.get_by_role("button", name=re.compile(r"^\+\d+개 더$")).click()
    expect(lane).not_to_have_class(re.compile(r"\bfold-peek\b"))
    expect(lane.locator(".mt-card:visible")).to_have_count(child_count)
    geometry = lane.locator(".mt-card").evaluate_all(_REACHABILITY)
    assert not geometry["failures"], geometry
    assert 24 <= geometry["minHeight"] <= geometry["maxHeight"] <= 40
    parent_height = flow.locator('.mt-compact-head > .mt-card.two').bounding_box()["height"]
    assert 40 <= parent_height <= 68
    _assert_no_document_overflow(page)

    # Filters operate on all descendants, not a five-row peek or a rendered page.
    priority = page.get_by_role("combobox", name="우선순위 필터", exact=True)
    due = page.get_by_role("combobox", name="마감기한 필터", exact=True)
    priority.select_option("name:Medium")
    due.select_option("overdue")
    page.locator(".mt-options-heading").click()  # Move focus out of the filter before Ctrl+F.
    page.keyboard.press("Control+f")
    query.fill("밀도 자손")
    expect(page.locator(".mt-find-count")).to_have_text(f"1 / {child_count}")
    due.select_option("none")
    expect(page.locator(".mt-find-count")).to_have_text("0 / 0")
    due.select_option("overdue")
    expect(page.locator(".mt-find-count")).to_have_text(f"1 / {child_count}")
    query.fill(last_key)
    found.wait_for(state="visible")
    assert not found.evaluate_all(_REACHABILITY)["failures"]
    query.press("Escape")
    priority.select_option("")
    due.select_option("")
    footer.click()
    expect(footer).to_have_attribute("aria-expanded", "false")
    expect(flow.locator(".mt-compact-children")).to_have_count(0)
    footer.click()
    expect(footer).to_have_attribute("aria-expanded", "true")
    lane.locator(f'[data-key="{last_key}"]').scroll_into_view_if_needed()
    assert not lane.locator(f'[data-key="{last_key}"]').evaluate_all(_REACHABILITY)["failures"]
    assert len(requests) == initial_requests
    _assert_no_document_overflow(page)
    (upload_path.parent / f"task-density-{child_count}.json").write_text(json.dumps({
        "children": child_count, "width": width, "parentHeight": parent_height,
        "rowGeometry": geometry, "streamRequests": len(requests), "errors": errors,
    }, ensure_ascii=False), encoding="utf-8")
    assert not errors
