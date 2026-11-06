"""Completed ticket work time remains visible with the timeline panel folded."""
from frontend.editor.test_editor_roundtrip_ui import editor_browser, _open_ticket  # noqa: F401


def test_completed_ticket_work_time_is_clear_without_extra_worklog_requests(editor_browser):
    from playwright.sync_api import expect

    page, base, errors, upload = editor_browser
    state = {"seconds": 5400}
    requested = []
    page.on("request", lambda request: requested.append(request.url))
    page.add_init_script("localStorage.setItem('tkt.tlHidden','1')")

    def ticket(route):
        response = route.fetch()
        value = response.json()
        value.update(statusCategory="done", timeSpentSeconds=state["seconds"])
        route.fulfill(response=response, json=value)

    page.route("**/api/ticket/DL-9007", ticket)
    for seconds, label in [(5400, "1시간 30분"), (90000, "25시간"), (0, "0분"), (None, "기록 없음")]:
        state["seconds"] = seconds
        _open_ticket(page, base, "DL-9007")
        field = page.locator('.tkt-work-time')
        expect(field).to_be_visible()
        expect(field).to_have_text("누적 작업시간" + label)
        assert "하위 티켓 시간은 포함하지 않습니다" in field.get_attribute("title")
        if seconds == 5400:
            page.screenshot(path=str(upload.parent / "completed-ticket-work-time.png"))
    assert not any("/worklog" in url for url in requested)
    assert not errors
