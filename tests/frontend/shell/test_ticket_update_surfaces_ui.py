"""Shared read acknowledgments update compact ticket surfaces without reloading Jira data."""
from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401


def test_workload_vit_and_wbs_share_read_state_without_reloading_rows(editor_browser):
    page, base, errors, upload_path = editor_browser
    requests = []
    page.on("request", lambda request: requests.append(request.url) if "/api/" in request.url else None)
    page.goto(base + "/#/workload", wait_until="load")
    page.wait_for_function("() => !!window.__lakeUp")
    page.locator(".wl-view .wl-modsel").select_option("TEST")
    page.locator('.wl-view .prow:has(.pname[title="test.ui01"]) .plus').click()
    workload = page.locator(".wl-view .wtk[data-key]").first
    workload.wait_for(state="visible")

    def verify_row(row, name):
        page.wait_for_load_state("networkidle")
        key = row.get_attribute("data-key")
        row = page.locator(f".{name}-view .ticket-update-row[data-key='{key}']").first
        viewed_through = page.evaluate("""async key => {
          const updates = await import('/lib/ticketUpdates.js');
          const through = Date.now() - 60000;
          updates.ingestTicketUpdates({tickets:{[key]:{latestRelevantAt:through}}});
          return through;
        }""", key)
        page.wait_for_function("selector => document.querySelector(selector)?.classList.contains('update-unread')",
                               arg=f".{name}-view .ticket-update-row[data-key='{key}']")
        before = row.bounding_box()
        mark = row.locator(".tc-update-mark")
        mark.wait_for(state="visible")
        assert mark.bounding_box()["height"] <= 20
        page.screenshot(path=str(upload_path.parent / f"{name}-unread.png"))
        row.evaluate("element => { window.__unreadRow = element; }")
        start = len(requests)
        assert page.evaluate("""async ({key, through}) => {
          const updates = await import('/lib/ticketUpdates.js');
          return updates.acknowledgeTicketUpdates(key, through);
        }""", {"key": key, "through": viewed_through})
        page.wait_for_function("() => !window.__unreadRow.classList.contains('update-unread')")
        assert row.evaluate("element => element === window.__unreadRow && element.isConnected")
        assert row.bounding_box()["height"] == before["height"]
        assert not row.locator(".tc-update-mark:visible").count()
        data_paths = {"wl": ("/api/workload",), "vit": ("/api/vit",),
                      "wbs": ("/api/wbs", "/api/epic/")}[name]
        assert not [url for url in requests[start:] if any(path in url for path in data_paths)]

    verify_row(workload, "wl")
    page.goto(base + "/#/vit", wait_until="load")
    vit = page.locator(".vit-view .c-info.ticket-update-row").first
    vit.wait_for(state="visible")
    verify_row(vit, "vit")
    page.goto(base + "/#/wbs", wait_until="load")
    page.locator(".wbs-view").get_by_role("button", name="전체 Epic 펼치기").click()
    epic = page.locator(".wbs-view .lrow.epic[data-key]").first
    epic.wait_for(state="visible")
    color = page.locator(".wbs-view .epic-bar").first.evaluate("element => getComputedStyle(element).backgroundColor")
    verify_row(epic, "wbs")
    assert page.locator(".wbs-view .epic-bar").first.evaluate("element => getComputedStyle(element).backgroundColor") == color
    assert not errors
