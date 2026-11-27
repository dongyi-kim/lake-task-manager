"""Check owned Chromium exit evidence without starting LTM, Jira, or an HTTP server."""
import os
import sys

import pytest

from app.launcher.window_recovery import BrowserOwnerProbe


@pytest.mark.skipif(sys.platform != "win32", reason="Owned browser exit-code probe uses Windows handles")
def test_owned_browser_normal_close_and_forced_exit_have_distinct_evidence(tmp_path):
    api = pytest.importorskip("playwright.sync_api")
    win32api = pytest.importorskip("win32api")
    probes = []
    try:
        with api.sync_playwright() as playwright:
            for mode in ("normal", "terminated"):
                try:
                    context = playwright.chromium.launch_persistent_context(
                        str(tmp_path / mode), headless=True,
                        **({"executable_path": os.environ["LTM_BROWSER_EXECUTABLE"]}
                           if os.environ.get("LTM_BROWSER_EXECUTABLE") else {}))
                except api.Error as error:
                    if "Executable doesn't exist" in str(error):
                        pytest.skip("Optional Chromium binary is not installed")
                    raise
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("data:text/html,<title>Owned browser probe</title>")
                probe = BrowserOwnerProbe(playwright, context)
                probes.append(probe)
                assert probe._handle is not None, "The launched browser must supply a queryable owned PID"
                if mode == "normal":
                    context.close()
                    assert probe._browser_exit() == 0
                    assert probe.failure_reason() is None
                else:
                    session = context.browser.new_browser_cdp_session()
                    processes = session.send("SystemInfo.getProcessInfo")["processInfo"]
                    browser_pid = int(next(item["id"] for item in processes if item["type"] == "browser"))
                    session.detach()
                    # This is the exact PID returned by the test's own launched
                    # Chromium, never a process discovered by name or port.
                    handle = win32api.OpenProcess(0x0001, False, browser_pid)
                    try:
                        with context.expect_event("close", timeout=5000):
                            win32api.TerminateProcess(handle, 17)
                    finally:
                        win32api.CloseHandle(handle)
                    assert probe._browser_exit() == 17
                    assert probe.failure_reason() == "browser_crash"
            # Playwright's driver still lives during both browser close cases.
            assert all(not probe.cleanup_confirmed() for probe in probes)
        assert all(probe.cleanup_confirmed() for probe in probes)
    finally:
        for probe in probes:
            probe.close()
