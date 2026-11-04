"""Real Vue card rendering with deterministic empty/partial child snapshots."""
import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401


@pytest.mark.parametrize("partial", [False, True], ids=["filtered-empty-DL-5518", "retry-exhausted"])
@pytest.mark.parametrize("width", [1720, 720], ids=["wide", "narrow"])
def test_finished_child_jobs_do_not_leave_empty_wide_spinners(editor_browser, partial, width):
    page, base, errors, _ = editor_browser
    page.set_viewport_size({"width": width, "height": 1080})
    group = {
        "key": "DL-5518", "title": "필터 결과 확인", "type": "Task", "mine": True,
        "statusCategory": "todo", "assignee": "담당자", "assigneeId": "admin",
        "priRank": 2, "pri": "Medium", "dueDays": None, "hasSubs": True,
        "atoms": [], "others": [], "kidsTotal": 3, "kidsDone": 0,
        "childrenPending": partial, "childrenLoaded": not partial,
    }
    solo = {**group, "key": "DL-5519", "title": "단독 Task", "hasSubs": False,
            "childrenPending": False, "childrenLoaded": True}
    solo["atoms"] = [{k: v for k, v in solo.items() if k not in ("atoms", "others")}]
    model = {"syncId": "ui-sync", "groups": [group, solo], "epics": [], "counts": {
        "total": 0, "overdue": 0, "today": 0, "week": 0, "done": 0}}
    calls = []

    def stream(route):
        token = parse_qs(urlparse(route.request.url).query)["requestToken"][0]
        route.fulfill(content_type="application/x-ndjson", body=json.dumps({
            "contract": "task-snapshot.v1", "type": "snapshot", "requestToken": token,
            "sequence": 1, "done": True, "model": model}) + "\n")

    def children(route):
        calls.append(route.request.url)
        snapshot = copy.deepcopy(model)
        snapshot["snapshotSequence"] = len(calls)
        route.fulfill(json={"contract": "task-snapshot.v1", "type": "snapshot",
                            "syncId": "ui-sync", "sequence": len(calls), "model": snapshot,
                            "retryable": True, "missing": [{"kind": "transport"}]})

    page.route("**/api/mytasks/stream?*", stream)
    page.route("**/api/mytasks/sync/ui-sync/group/*", children)
    page.goto(base + "/#/mytasks")
    page.locator('.mt-card[data-key="DL-5518"]:visible').wait_for()
    if partial:
        page.get_by_text("일부 SubTask 미수신", exact=True).filter(visible=True).wait_for()
        assert len(calls) == 3
    else:
        page.get_by_text("현재 필터에 해당하는 SubTask 없음", exact=True).filter(visible=True).wait_for()
        assert not calls
        assert page.locator(".mt-compact-flow" if width > 900 else ".mt-gcard2:visible").count() == 1
        assert page.locator(".mt-compact-children").count() == 0
        assert page.locator(".mt-gbody.one:visible").count() == 0
        widths = page.evaluate("""() => ({
          card:[...document.querySelectorAll('.mt-compact-flow, .mt-gcard2')]
            .find(el=>el.getClientRects().length).getBoundingClientRect().width,
          column:document.querySelector('.mt-card[data-key="DL-5519"]').getBoundingClientRect().width})""")
        assert abs(widths["card"] - widths["column"]) <= 1
    assert page.locator(".mt-subfoot-sync i").count() == 0
    assert not errors
