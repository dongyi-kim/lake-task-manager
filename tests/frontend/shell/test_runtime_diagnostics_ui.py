"""Real Settings diagnostics surface with local HTTP fixtures and no user data."""
from frontend.shell.test_workspace_admin_ui import admin_ui, static_server  # noqa: F401


def test_settings_browses_empty_error_and_records_with_download_and_focus(admin_ui, static_server):
    page, _state, _requests, _held, expect = admin_ui
    page.add_style_tag(url=static_server + "/styles/agent.css")
    response = {"status": 200, "value": {"files": [], "records": [], "truncated": False}}

    def diagnostics(route):
        route.fulfill(status=response["status"], json=response["value"])

    page.route("**/api/app/diagnostics", diagnostics)
    page.get_by_role("button", name="설정", exact=True).click()
    page.get_by_role("button", name="오류 기록 보기 →", exact=True).click()
    dialog = page.get_by_role("dialog", name="오류 기록", exact=True)
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("status")).to_have_text("저장된 오류·복구 기록이 없습니다.")
    expect(page.get_by_role("button", name="오류 기록 닫기", exact=True)).to_be_focused()
    response["status"] = 503
    response["value"] = {"error": "fixture unavailable"}
    dialog.get_by_role("button", name="새로고침", exact=True).click()
    expect(dialog.get_by_role("alert")).to_contain_text("새로고침해 주세요")
    response["status"] = 200
    response["value"] = {"files": [{"name": "worker.jsonl", "bytes": 200}], "truncated": True,
        "records": [{"at": "2026-11-28T00:00:00Z", "event": "runtime_stalled", "pid": 12,
                     "file": "worker.jsonl", "resources": {"processBytes": 200}}]}
    dialog.get_by_role("button", name="새로고침", exact=True).click()
    expect(dialog.locator("summary")).to_contain_text("응답 지연")
    expect(dialog.get_by_role("link", name="worker.jsonl 다운로드", exact=True)).to_have_attribute(
        "href", "/api/app/diagnostics/worker.jsonl/download")
    dialog.locator("summary").click()
    expect(dialog.locator("pre")).to_contain_text('"processBytes": 200')
    page.set_viewport_size({"width": 390, "height": 844})
    assert dialog.bounding_box()["width"] <= 390
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(page.get_by_role("button", name="설정", exact=True)).to_be_focused()
