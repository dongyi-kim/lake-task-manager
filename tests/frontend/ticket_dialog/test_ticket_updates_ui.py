"""Actual TicketDialog open/read/close lifecycle with deterministic local response races."""
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest

from frontend.shell.test_local_mcp_ui import static_server  # noqa: F401
from support.browser import launch_chromium


@pytest.fixture
def updates_dialog(static_server):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as driver:
        browser = launch_chromium(driver)
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        day = 86400000
        iso = lambda value: datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
        state = {"seen": now - 3 * day, "latest": now - day, "comment_at": now - day,
                 "fail_detail": False, "fail_comments": False, "hold_comments": False,
                 "held": [], "acks": [], "fail_ack": False, "comments_fetched_at": now, "aggregate_fetched_at": now,
                 "fail_fresh_comments": False, "fresh_comment_calls": 0,
                 "hold_fresh_comments": False, "hold_revalidate_comments": False, "comments_complete": True,
                 "comments_fresh_until": None,
                 "full_comments": None, "hold_complete": False, "complete_calls": 0,
                 "detail": {"key": "DL-1", "summary": "업데이트 확인 티켓", "type": "Task",
                            "subtask": False, "status": "In Progress", "statusCategory": "inprogress",
                            "updated": iso(now - day), "created": iso(now - 10 * day),
                            "descriptionHtml": "<p>읽을 본문</p>", "components": [], "labels": [],
                            "assignee": "김담당", "assigneeId": "owner", "url": "https://jira.local/browse/DL-1"},
                 "comments": [
                     {"id": "edited", "author": "김댓글", "authorId": "author", "date": iso(now - 10 * day),
                      "updated": iso(now - day), "html": "<p>수정된 기존 댓글</p>"},
                     {"id": "seen", "author": "김댓글", "authorId": "author", "date": iso(now - 4 * day),
                      "html": "<p>이미 읽은 댓글</p>"}],
                 "timeline": [
                     {"kind": "status", "date": iso(now - 2 * day), "author": "김담당", "from": "Open", "to": "In Progress"},
                     {"kind": "status", "date": iso(now - 4 * day), "author": "김담당", "from": "Backlog", "to": "Open"},
                     {"kind": "worklog", "date": iso(now - day), "author": "김담당", "field": "time spent", "to": "1h"}],
                 "now": now, "iso": iso}
        errors, failed, startup_requests, console_errors = [], [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("requestfailed", lambda request: startup_requests.append((request.url, request.failure)))
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: failed.append((response.status, urlsplit(response.url).path))
                if response.status >= 400 and "/api/" in response.url else None)

        def route_api(route):
            path = urlsplit(route.request.url).path
            if path == "/api/tickets/activity":
                route.fulfill(json={"scope": "fixture", "observedAt": state["now"], "tickets": {
                    "DL-1": {"lastViewedAt": state["seen"], "latestRelevantAt": state["latest"],
                             "latestCommentAt": state["comment_at"]}}})
            elif path == "/api/tickets/viewed":
                body = route.request.post_data_json
                assert route.request.headers.get("x-ltm-local") == "1"
                if state["fail_ack"]:
                    route.fulfill(status=503, json={"detail": "local store busy"})
                    return
                state["acks"].append(body)
                state["seen"] = max(state["seen"] or 0, body["viewedThrough"])
                route.fulfill(json={"scope": "fixture", "key": "DL-1", "lastViewedAt": state["seen"]})
            elif path == "/api/ticket/DL-1":
                route.fulfill(status=503, json={"detail": "detail unavailable"}) if state["fail_detail"] else route.fulfill(json=state["detail"])
            elif path == "/api/issue/DL-1/comments/complete":
                state["complete_calls"] += 1
                if state["hold_complete"]:
                    state["held"].append(route)
                else:
                    rows = state["full_comments"] if state["full_comments"] is not None else state["comments"]
                    route.fulfill(json={"comments": rows, "total": len(rows), "returned": len(rows),
                        "complete": True, "hasMore": False, "fetchedAt": state["now"] / 1000},
                        headers={"X-LTM-Comments-Fetched-At": str(state["now"] / 1000)})
            elif path == "/api/issue/DL-1/comments":
                if state["hold_revalidate_comments"] and "revalidate=1" in route.request.url and "freshAfter=" not in route.request.url:
                    state["held"].append(route)
                    return
                if "freshAfter=" in route.request.url:
                    state["fresh_comment_calls"] += 1
                    if state["fail_fresh_comments"]:
                        route.fulfill(status=503, json={"detail": "fresh comments unavailable"})
                        return
                    if state["hold_fresh_comments"]:
                        state["held"].append(route)
                        return
                    state["comments_fetched_at"] = state["now"]
                if state["hold_comments"]:
                    state["held"].append(route)
                elif state["fail_comments"]:
                    route.fulfill(status=503, json={"detail": "comments unavailable"})
                else:
                    route.fulfill(json=state["comments"], headers={
                        "X-LTM-Comments-Complete": "1" if state["comments_complete"] else "0",
                        **({"X-LTM-Fresh-Until": str(state["comments_fresh_until"] / 1000)}
                           if state["comments_fresh_until"] is not None else {}),
                        "X-LTM-Fetched-At": str(state["aggregate_fetched_at"] / 1000),
                        "X-LTM-Comments-Fetched-At": str(state["comments_fetched_at"] / 1000)})
            elif path.endswith("/timeline"):
                route.fulfill(json=state["timeline"])
            elif path.endswith("/editmeta"):
                route.fulfill(json={})
            elif path == "/api/me":
                route.fulfill(json={"id": "owner", "name": "김담당", "known": True})
            elif path == "/api/health":
                route.fulfill(json={"env": "mock", "jiraBase": "https://jira.local", "projectKey": "DL"})
            else:
                route.fulfill(json=[])

        page.route("**/api/**", route_api)
        page.route("**/fonts.css", lambda route: route.fulfill(body="", content_type="text/css"))
        page.route(static_server + "/", lambda route: route.fulfill(content_type="text/html", body="""
          <!doctype html><html data-theme="light"><head><meta charset="utf-8">
          <link rel="stylesheet" href="/styles/tokens.css"><link rel="stylesheet" href="/styles/base.css">
          <link rel="stylesheet" href="/styles/components.css"><link rel="stylesheet" href="/styles/ticket.css">
          </head><body><div id="app"></div><script type="module">
          import {createApp} from '/vendor/vue.esm-browser.prod.js';
          import TicketDialog from '/components/ui/TicketDialog.js';
          window.harness=createApp({components:{TicketDialog},data:()=>({open:false}),
            template:`<button @click="open=true">티켓 열기</button><TicketDialog v-if="open" ref="ticket"
              key-id="DL-1" @close="open=false" />`}).mount('#app');
          </script></body></html>"""))
        page.clock.install(time=now / 1000)
        page.goto(static_server + "/")
        try:
            try:
                page.wait_for_function("() => !!window.harness", timeout=15000)
            except pw.Error as error:
                raise AssertionError({"stage": "fixture bootstrap", "url": page.url,
                    "body": page.locator("body").inner_text()[:1000], "pageErrors": errors,
                    "consoleErrors": console_errors, "requestFailures": startup_requests}) from error
            yield page, state, failed, pw.expect
            assert not errors, errors
        finally:
            browser.close()


def _open(page):
    page.get_by_role("button", name="티켓 열기", exact=True).click()
    page.get_by_text("읽을 본문", exact=True).wait_for()


def _close(page):
    page.locator(".tb-btn.close").click()
    page.locator(".tb-btn.close").wait_for(state="detached")


def test_edited_comment_and_major_history_keep_frozen_highlights_until_reopen(updates_dialog):
    page, state, failed, expect = updates_dialog
    _open(page)
    expect(page.locator(".tkt-cmt-h.update-unread")).to_have_count(1)
    expect(page.locator(".tl-body.update-unread")).to_have_count(1)
    expect(page.get_by_text("새 업데이트", exact=True)).to_have_count(1)
    page.wait_for_function("async () => !(await import('/lib/ticketUpdates.js')).isTicketUnread('DL-1')")
    assert state["acks"] == [{"key": "DL-1", "viewedThrough": state["latest"]}]
    # Shared card state is read, but the opened dialog retains its previous baseline to explain changes.
    expect(page.locator(".tkt-cmt-h.update-unread")).to_have_count(1)
    rail = page.locator(".tkt-cmt").first.evaluate("el=>getComputedStyle(el).borderLeftColor")
    dot = page.locator(".tl-dot.k-status").first.evaluate("el=>getComputedStyle(el).backgroundColor")
    _close(page)
    _open(page)
    expect(page.locator(".tkt-cmt-h.update-unread")).to_have_count(0)
    expect(page.locator(".tl-body.update-unread")).to_have_count(0)
    assert page.locator(".tkt-cmt").first.evaluate("el=>getComputedStyle(el).borderLeftColor") == rail
    assert page.locator(".tl-dot.k-status").first.evaluate("el=>getComputedStyle(el).backgroundColor") == dot
    assert not failed


@pytest.mark.parametrize("failure", ["detail", "comments"])
def test_failed_content_never_acknowledges_unseen_updates(updates_dialog, failure):
    page, state, failed, expect = updates_dialog
    state["fail_" + failure] = True
    page.get_by_role("button", name="티켓 열기", exact=True).click()
    expect(page.get_by_text(failure + " unavailable", exact=False).first).to_be_visible()
    assert state["acks"] == []
    _close(page)
    state["fail_" + failure] = False
    _open(page)
    page.wait_for_function("async () => !(await import('/lib/ticketUpdates.js')).isTicketUnread('DL-1')")
    assert len(state["acks"]) == 1
    assert failed == [(503, "/api/ticket/DL-1" if failure == "detail" else "/api/issue/DL-1/comments")]


def test_new_comment_arriving_after_open_stays_unread_and_failed_ack_retries(updates_dialog):
    page, state, failed, expect = updates_dialog
    state["hold_comments"] = True
    _open(page)
    expect(page.get_by_text("코멘트 불러오는 중…", exact=True)).to_be_visible()
    assert state["acks"] == []
    page.clock.fast_forward(10000)
    newer = state["now"] + 1000
    state["latest"] = newer
    page.evaluate("""async at => {
      const {ingestTicketUpdates}=await import('/lib/ticketUpdates.js');
      ingestTicketUpdates({scope:'fixture',tickets:{'DL-1':{latestRelevantAt:at,latestCommentAt:at}}});
    }""", newer)
    state["fail_ack"] = True
    for route in state["held"]:
        route.fulfill(json=state["comments"], headers={
            "X-LTM-Comments-Complete": "1" if state["comments_complete"] else "0",
            "X-LTM-Fetched-At": str(state["aggregate_fetched_at"] / 1000),
            "X-LTM-Comments-Fetched-At": str(state["comments_fetched_at"] / 1000)})
    state["held"].clear(); state["hold_comments"] = False
    expect(page.get_by_text("수정된 기존 댓글", exact=True)).to_be_visible()
    page.wait_for_function("() => window.harness.$refs.ticket._updateAckFailedAt > 0")
    assert state["acks"] == []
    state["fail_ack"] = False
    page.clock.fast_forward(5001)
    page.get_by_text("읽을 본문", exact=True).click()
    page.wait_for_function("() => window.harness.$refs.ticket._updateAckThrough > 0")
    assert state["acks"][0]["viewedThrough"] < newer
    assert page.evaluate("async()=> (await import('/lib/ticketUpdates.js')).isTicketUnread('DL-1')")
    assert failed == [(503, "/api/tickets/viewed")]


def test_hidden_timeline_does_not_prevent_read_ack(updates_dialog):
    page, state, failed, expect = updates_dialog
    page.evaluate("localStorage.setItem('tkt.tlHidden','1')")
    _open(page)
    expect(page.locator(".tl-show")).to_be_visible()
    page.wait_for_function("async()=> !(await import('/lib/ticketUpdates.js')).isTicketUnread('DL-1')")
    assert len(state["acks"]) == 1
    assert not failed


def test_stale_comments_refresh_failure_does_not_ack_fresh_detail(updates_dialog):
    page, state, failed, expect = updates_dialog
    state["comment_at"] = None  # opening metadata cannot prove whether the fresh issue change is a comment
    state["comments_fetched_at"] = state["latest"] - 1000
    state["comments"] = [state["comments"][1]]
    state["fail_fresh_comments"] = True
    _open(page)
    expect(page.get_by_text("이미 읽은 댓글", exact=True)).to_be_visible()
    page.wait_for_function("() => window.harness.$refs.ticket._updateCommentsFailedAt > 0")
    assert state["fresh_comment_calls"] == 1
    assert state["acks"] == []
    assert page.evaluate("async()=> (await import('/lib/ticketUpdates.js')).isTicketUnread('DL-1')")
    assert failed == [(503, "/api/issue/DL-1/comments")]


def test_other_old_cache_sources_do_not_trigger_repeated_comment_refresh(updates_dialog):
    page, state, failed, expect = updates_dialog
    state["aggregate_fetched_at"] = state["latest"] - 1000  # e.g. cached current-user identity
    _open(page)
    expect(page.get_by_text("수정된 기존 댓글", exact=True)).to_be_visible()
    page.wait_for_function("async()=> !(await import('/lib/ticketUpdates.js')).isTicketUnread('DL-1')")
    assert len(state["acks"]) == 1
    assert state["fresh_comment_calls"] == 0
    assert not failed


@pytest.mark.parametrize("reload_kind", ["ticket", "comments"])
def test_late_comments_refresh_cannot_overwrite_same_ticket_mutation(updates_dialog, reload_kind):
    page, state, failed, expect = updates_dialog
    state["comments_fetched_at"] = state["latest"] - 1000
    state["hold_fresh_comments"] = True
    _open(page)
    page.wait_for_function("() => !!window.harness.$refs.ticket._updateCommentsPending")
    expect(page.get_by_text("수정된 기존 댓글", exact=True)).to_be_visible()
    # A real mutation wrapper evicts both initial/full comment cache entries before its caller
    # reloads this same dialog. The superseded request can still resolve to its old caller.
    old_rows = [dict(row) for row in state["comments"]]
    state["comments"] = [{**old_rows[0], "html": "<p>방금 저장한 최신 댓글</p>"}]
    state["comments_fetched_at"] = state["now"]
    state["hold_fresh_comments"] = False
    page.evaluate("""async kind => {
      const {api}=await import('/lib/api.js');
      await api.commentUpdate('DL-1','edited','<p>방금 저장한 최신 댓글</p>');
      const dialog=window.harness.$refs.ticket;
      await (kind==='ticket' ? dialog.load(true,true) : dialog.reloadComments());
    }""", reload_kind)
    expect(page.get_by_text("방금 저장한 최신 댓글", exact=True)).to_be_visible()
    assert len(state["held"]) == 1
    state["held"].pop().fulfill(json=old_rows, headers={
        "X-LTM-Comments-Fetched-At": str(state["now"] / 1000), "X-LTM-Comments-Complete": "1"})
    page.wait_for_function("""async () => {
      const {api}=await import('/lib/api.js');
      return api.cached('/api/issue/DL-1/comments')?.[0]?.html.includes('방금 저장한');
    }""")
    # Pump the old request's resolve/finally microtasks before asserting the rendered result.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.get_by_text("방금 저장한 최신 댓글", exact=True)).to_be_visible()
    expect(page.get_by_text("수정된 기존 댓글", exact=True)).to_have_count(0)
    assert not failed


@pytest.mark.parametrize("first_complete", [True, False])
def test_older_edited_comment_outside_first_twenty_is_shown_before_read_ack(updates_dialog, first_complete):
    page, state, failed, expect = updates_dialog
    day = 86400000
    rows = [{"id": str(index), "author": "김댓글", "authorId": "author",
             "date": state["iso"](state["now"] - (40 - index) * day),
             "html": f"<p>지난 댓글 {index}</p>"} for index in range(25)]
    rows[0] = {**rows[0], "updated": state["iso"](state["latest"]), "html": "<p>오래된 댓글의 새 수정</p>"}
    state["comments"] = rows[5:]  # a fresh latest-20 page omits the first-created comment's edit
    state["full_comments"] = rows
    state["comments_complete"] = False
    state["comment_at"] = None  # no prior full snapshot: issue.updated is the only cached signal
    state["hold_complete"] = True
    _open(page)
    expect(page.locator(".tkt-comments .tkt-cmt")).to_have_count(20)
    page.wait_for_function("() => !!window.harness.$refs.ticket._updateCommentsPending")
    assert state["acks"] == []
    assert state["complete_calls"] == 1
    route = state["held"].pop()
    if not first_complete:
        route.fulfill(json={"comments": rows[:10], "total": 25, "returned": 10, "complete": False,
                            "hasMore": True, "partial": True, "fetchedAt": None})
        page.wait_for_function("() => window.harness.$refs.ticket._updateCommentsFailedAt > 0")
        assert state["acks"] == []
        expect(page.locator(".tkt-comments .tkt-cmt")).to_have_count(20)
        state["hold_complete"] = False
        page.clock.fast_forward(5001)
        page.get_by_text("읽을 본문", exact=True).click()
    else:
        route.fulfill(json={"comments": rows, "total": 25, "returned": 25, "complete": True,
                            "hasMore": False, "fetchedAt": state["now"] / 1000},
                      headers={"X-LTM-Comments-Fetched-At": str(state["now"] / 1000)})
    expect(page.get_by_text("오래된 댓글의 새 수정", exact=True)).to_be_visible()
    expect(page.locator(".tkt-comments .tkt-cmt")).to_have_count(25)
    expect(page.locator(".tkt-cmt-h.update-unread")).to_have_count(1)
    page.wait_for_function("async()=> !(await import('/lib/ticketUpdates.js')).isTicketUnread('DL-1')")
    assert state["acks"] == [{"key": "DL-1", "viewedThrough": state["latest"]}]
    assert state["complete_calls"] == (1 if first_complete else 2)
    assert not failed


def test_successful_source_refresh_that_reveals_paging_continues_without_another_click(updates_dialog):
    page, state, failed, expect = updates_dialog
    state["comments_fetched_at"] = state["latest"] - 1000
    state["hold_fresh_comments"] = True
    _open(page)
    page.wait_for_function("() => !!window.harness.$refs.ticket._updateCommentsPending")
    state["comments_complete"] = False
    state["full_comments"] = state["comments"] + [{"id": "extra", "date": state["iso"](state["latest"]),
        "author": "추가작성자", "html": "<p>추가 페이지의 댓글</p>"}]
    state["held"].pop().fulfill(json=state["comments"], headers={
        "X-LTM-Comments-Fetched-At": str(state["now"] / 1000), "X-LTM-Comments-Complete": "0"})
    expect(page.get_by_text("추가 페이지의 댓글", exact=True)).to_be_visible()
    page.wait_for_function("async()=> !(await import('/lib/ticketUpdates.js')).isTicketUnread('DL-1')")
    assert state["complete_calls"] == 1
    assert len(state["acks"]) == 1
    assert not failed


@pytest.mark.parametrize("newer_page", [False, True])
def test_late_first_page_swr_keeps_complete_comments_and_refreshes_newer_source(updates_dialog, newer_page):
    page, state, failed, expect = updates_dialog
    state["comments_complete"] = False
    state["comments_fresh_until"] = state["now"] - 1000
    state["hold_revalidate_comments"] = True
    state["full_comments"] = state["comments"] + [{"id": "older", "date": state["iso"](state["latest"]),
        "author": "이전작성자", "html": "<p>첫 페이지 밖 댓글</p>"}]
    _open(page)
    expect(page.get_by_text("첫 페이지 밖 댓글", exact=True)).to_be_visible()
    page.wait_for_function("async()=> !(await import('/lib/ticketUpdates.js')).isTicketUnread('DL-1')")
    assert len(state["held"]) == 1
    assert state["complete_calls"] == 1
    if newer_page:
        page.clock.fast_forward(5000)
        state["now"] += 3000
        state["full_comments"] += [{"id": "newer", "date": state["iso"](state["now"] - 1000),
            "author": "새작성자", "html": "<p>열어둔 사이 새 댓글</p>"}]
    state["held"].pop().fulfill(json=state["comments"], headers={
        "X-LTM-Comments-Fetched-At": str(state["now"] / 1000), "X-LTM-Comments-Complete": "0",
        "X-LTM-Fresh-Until": str(state["now"] / 1000 + 600)})
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.get_by_text("첫 페이지 밖 댓글", exact=True)).to_be_visible()
    if newer_page:
        expect(page.get_by_text("열어둔 사이 새 댓글", exact=True)).to_be_visible()
        assert page.evaluate("async()=> (await import('/lib/ticketUpdates.js')).isTicketUnread('DL-1')")
    assert state["complete_calls"] == (2 if newer_page else 1)
    assert not failed
