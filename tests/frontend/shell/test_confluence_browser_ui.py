"""Real-browser document navigation with deterministic upstream-shaped responses."""
import json
from urllib.parse import parse_qs, urlsplit

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401


def install_documents(page):
    page.add_init_script("localStorage.setItem('lake.guides.seen', JSON.stringify(['global-search-recent-1','quick-open-hotkey-1','browser-localhost-access-1','local-mcp-connect-1','refresh-fab-1']))")
    state = {"spaces_fail": False, "revision_fail": False, "reads": []}
    def route(request):
        url = urlsplit(request.request.url)
        args = parse_qs(url.query)
        path = url.path
        data = None
        if path.endswith("/config"):
            data = {"base": "https://docs.example/wiki", "spaces": ["ETL", "OPS"]}
        elif path.endswith("/spaces"):
            if state["spaces_fail"]:
                request.fulfill(status=503, json={"service": "confluence", "error": "Confluence 연결 대기"}); return
            data = {"results": [{"id": 1, "key": "ETL", "name": "ETL 문서"}, {"id": 2, "key": "OPS", "name": "운영"}], "size": 2}
        elif path.endswith("/search"):
            data = {"results": [{"content": {"id": "22", "title": "아직 펼치지 않은 적재 설계",
                    "ancestors": [{"id": "12", "title": "적재 가이드"}], "space": {"key": "ETL"}}}], "size": 1}
        elif path.endswith("/pages"):
            parent = args.get("parent", [""])[0]
            if parent:
                data = {"results": [{"id": "13", "title": "하위 운영 문서"}] if parent == "12" else [], "size": 1 if parent == "12" else 0}
            else:
                data = {"results": [{"id": "12", "title": "적재 가이드"}, {"id": "90", "title": "홈페이지 밖 루트 문서"}], "size": 2}
        elif path.endswith("/revision"):
            if state["revision_fail"]:
                request.fulfill(status=503, json={"service": "confluence", "error": "최신 확인 실패"}); return
            data = {"pageId": "12", "latestPublishedVersion": 3, "lastModifiedAt": "2026-11-05T01:00:00Z",
                    "lastModifiedBy": {"displayName": "김담당"}, "checkedAt": 1793840400, "isLatest": False, "draftState": "unknown"}
        else:
            cid = path.rsplit("/", 1)[-1]
            version = int(args.get("version", [2])[0])
            state["reads"].append((cid, version))
            data = {"id": cid, "title": "적재 가이드" if cid == "12" else "아직 펼치지 않은 적재 설계",
                    "version": {"number": version, "when": "2026-11-04T01:00:00Z", "by": {"displayName": "김담당"}},
                    "space": {"key": "ETL", "name": "ETL 문서"}, "ancestors": [],
                    "url": f"https://docs.example/wiki/spaces/ETL/pages/{cid}/guide",
                    "html": f"<h2>버전 {version} 본문</h2>" + "<p>프로젝트의 데이터 적재 절차를 확인합니다.</p>" * 12
                        + "<h3>공통 절차</h3>" + "<p>절차를 확인합니다.</p>" * 12
                        + "<h3>공통 절차</h3>" + "<p>추가 절차를 확인합니다.</p>" * 12
                        + f"<h4>버전 {version} 검증</h4>" + "<p>검증 결과를 확인합니다.</p>" * 6}
        request.fulfill(status=200, content_type="application/json", body=json.dumps(data, ensure_ascii=False))
    page.route("**/api/confluence/**", route)
    return state


def test_confluence_tree_favorites_search_revision_and_responsive(editor_browser, tmp_path):
    page, base, errors, _ = editor_browser
    state = install_documents(page)
    page.goto(base + "/#/confluence")
    page.wait_for_function("() => !!window.__lakeUp")
    assert page.title() == "Lake Task Manager"
    page.get_by_role("link", name="Confluence", exact=True).wait_for()
    tree = page.get_by_role("navigation", name="문서 트리", exact=True)
    tree.get_by_role("button", name="홈페이지 밖 루트 문서", exact=True).wait_for()
    assert state["reads"] == []  # opening the navigation never fetches every body
    tree.get_by_role("button", name="적재 가이드 하위 문서", exact=True).click()
    tree.get_by_role("button", name="하위 운영 문서", exact=True).wait_for()
    tree.get_by_role("button", name="적재 가이드", exact=True).click()
    page.locator(".conf-document h1").wait_for()
    toc = page.get_by_role("navigation", name="본문 목차", exact=True)
    toc.get_by_role("button", name="버전 2 본문", exact=True).wait_for()
    assert toc.get_by_role("button").count() == 4  # outer document title is not duplicated
    ids = page.locator(".conf-document-body :is(h1,h2,h3,h4,h5,h6)").evaluate_all("els => els.map(el => el.id)")
    assert len(ids) == len(set(ids)) and any("공통-절차" in value for value in ids)
    toc.get_by_role("button", name="공통 절차", exact=True).nth(1).click()
    assert toc.get_by_role("button", name="공통 절차", exact=True).nth(1).get_attribute("aria-current") == "location"
    assert page.locator(".conf-reader").evaluate("el => el.scrollTop") > 0
    assert page.evaluate("window.scrollY") == 0
    assert page.evaluate("document.activeElement.id") == ids[2]
    page.locator(".conf-reader").evaluate("el => el.scrollTop = 0")
    page.wait_for_function("() => document.querySelector('.conf-toc button[aria-current]').textContent === '버전 2 본문'")
    page.get_by_role("button", name="즐겨찾기 추가", exact=True).click()
    page.get_by_role("region", name="즐겨찾기", exact=True).get_by_role("button", name="★ 적재 가이드").wait_for()
    assert state["reads"] == [("12", 2)]

    # Persisted favorites remain usable even if Space enumeration is unavailable.
    state["spaces_fail"] = True
    page.reload()
    page.get_by_role("region", name="즐겨찾기", exact=True).get_by_role("button", name="★ 적재 가이드").wait_for()
    page.get_by_text("Confluence 연결 대기", exact=False).wait_for()
    state["spaces_fail"] = False
    page.get_by_role("button", name="다시 시도", exact=True).click()
    tree.get_by_role("button", name="적재 가이드", exact=True).wait_for()

    page.get_by_label("문서 트리 검색", exact=True).fill("적재 설계")
    page.locator(".conf-search").get_by_role("button", name="검색", exact=True).click()
    result = tree.get_by_role("button", name="아직 펼치지 않은 적재 설계 적재 가이드")
    result.wait_for(); result.click()
    page.locator(".conf-document h1").filter(has_text="아직 펼치지 않은 적재 설계").wait_for()
    assert state["reads"][-1][0] == "22"
    page.get_by_role("region", name="즐겨찾기", exact=True).get_by_role("button", name="★ 적재 가이드").click()
    page.locator(".conf-document h1").filter(has_text="적재 가이드").wait_for()
    page.locator(".conf-reader").evaluate("el => el.scrollTop = 300")
    before = page.locator(".conf-reader").evaluate("el => el.scrollTop")
    read_count = len(state["reads"])
    page.get_by_role("button", name="최신 여부 확인", exact=True).click()
    page.locator(".conf-new-version").wait_for()
    assert "새 발행 버전 v3이 있습니다." in page.locator(".conf-new-version").inner_text()
    assert len(state["reads"]) == read_count
    assert page.locator(".conf-document-body h2").first.inner_text() == "버전 2 본문"
    page.get_by_role("button", name="새 버전 보기", exact=True).click()
    page.locator(".conf-document-body h2").filter(has_text="버전 3 본문").wait_for()
    assert abs(page.locator(".conf-reader").evaluate("el => el.scrollTop") - before) < 2
    assert state["reads"][-1] == ("12", 3)
    toc.get_by_role("button", name="버전 3 검증", exact=True).wait_for()
    assert toc.get_by_role("button", name="버전 2 검증", exact=True).count() == 0
    new_ids = page.locator(".conf-document-body :is(h1,h2,h3,h4,h5,h6)").evaluate_all("els => els.map(el => el.id)")
    assert ids[1:3] == new_ids[1:3]  # unchanged/duplicate headings retain stable unique anchors
    page.screenshot(path=str(tmp_path / "confluence-desktop.png"))

    state["revision_fail"] = True
    page.get_by_role("button", name="최신 여부 확인", exact=True).click()
    page.get_by_text("최신 확인 실패", exact=True).wait_for()
    assert page.locator(".conf-current").count() == 0
    assert page.locator(".conf-document-body h2").first.inner_text() == "버전 3 본문"
    page.get_by_role("button", name="즐겨찾기 해제", exact=True).click()
    assert page.get_by_role("region", name="즐겨찾기", exact=True).get_by_role("button").count() == 0
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="목차", exact=True).wait_for()
    assert not toc.is_visible()
    page.get_by_role("button", name="목차", exact=True).click()
    toc.get_by_role("button", name="버전 3 본문", exact=True).wait_for()
    toc.get_by_role("button", name="버전 3 본문", exact=True).press("Enter")
    assert not toc.is_visible()
    assert page.locator(".conf-workspace").evaluate("el => el.scrollWidth <= el.clientWidth + 2")
    page.screenshot(path=str(tmp_path / "confluence-mobile.png"))
    page.get_by_role("button", name="문서 탐색 접기", exact=True).click()
    page.get_by_role("button", name="문서 탐색", exact=True).click()
    unexpected = [error for error in errors if not (
        error.startswith("HTTP 503 ") and any(path in error for path in (
            "/api/confluence/spaces", "/api/confluence/pages/12/revision")))]
    assert not unexpected, unexpected


def test_confluence_cache_retention_setting_persists_and_restores(editor_browser):
    page, base, errors, _ = editor_browser
    page.goto(base)
    page.get_by_role("button", name="설정", exact=True).click()
    page.get_by_role("button", name="표시 보존 · 갱신 간격 설정 →", exact=True).click()
    dialog = page.get_by_role("dialog", name="캐시 설정")
    field = dialog.get_by_label("Confluence 버전별 원본 보관 기간", exact=True)
    assert field.input_value() == "30"
    field.fill("45")
    dialog.get_by_role("button", name="저장", exact=True).click()
    page.get_by_role("status").filter(has_text="저장했습니다").wait_for()
    assert page.request.get(base + "/api/prefs").json()["cachePolicy"]["confluenceBodyRetentionSeconds"] == 45 * 86400
    page.reload()
    page.get_by_role("button", name="설정", exact=True).click()
    page.get_by_role("button", name="표시 보존 · 갱신 간격 설정 →", exact=True).click()
    dialog = page.get_by_role("dialog", name="캐시 설정")
    assert dialog.get_by_label("Confluence 버전별 원본 보관 기간", exact=True).input_value() == "45"
    dialog.get_by_role("button", name="기본값 복원", exact=True).click()
    assert dialog.get_by_label("Confluence 버전별 원본 보관 기간", exact=True).input_value() == "30"
    dialog.get_by_role("button", name="저장", exact=True).click()
    page.get_by_role("status").filter(has_text="저장했습니다").wait_for()
    assert page.request.get(base + "/api/prefs").json()["cachePolicy"]["confluenceBodyRetentionSeconds"] == 30 * 86400
    assert not errors, errors


def test_unified_search_opens_in_app_and_confluence_auth_is_service_local(editor_browser):
    page, base, errors, _ = editor_browser
    install_documents(page)
    page.goto(base)
    page.wait_for_function("() => !!window.__lakeUp")
    result = page.evaluate("""async () => {
      const Search = (await import('/components/ui/SearchOverlay.js')).default;
      const emitted=[];
      Search.methods.pick.call({$emit:(...x)=>emitted.push(x),openExternal(){throw Error('external');}},
        {src:'confluence',it:{id:'12',url:'https://docs.example/wiki/spaces/ETL/pages/12/guide',title:'적재 가이드'}});
      return emitted;
    }""")
    assert ["close"] in result
    page.locator(".conf-document h1").wait_for()
    assert page.url.endswith("#/confluence/12")
    page.route("**/api/confluence/pages/12/revision?*", lambda route: route.fulfill(status=401, json={"service": "confluence", "error": "Confluence 인증 필요", "needLogin": True}))
    probes = []
    page.on("request", lambda req: probes.append(req.url) if "/api/auth/probe" in req.url else None)
    page.get_by_role("button", name="최신 여부 확인", exact=True).click()
    page.get_by_text("Confluence 인증 필요", exact=True).wait_for()
    assert probes == []
    assert page.locator(".conf-document").count() == 1  # auth outage preserves the visible read
    page.route("**/api/confluence/pages/12/revision?*", lambda route: route.fulfill(status=403, json={"service": "confluence", "error": "설정된 Space 범위 밖입니다."}))
    page.get_by_role("button", name="최신 여부 확인", exact=True).click()
    page.get_by_text("설정된 Space 범위 밖입니다.", exact=True).wait_for()
    assert page.locator(".conf-document").count() == 0
    assert page.locator(".conf-toc").count() == 0
    unexpected = [error for error in errors if not ((error.startswith("HTTP 401 ") or error.startswith("HTTP 403 ")) and "/api/confluence/pages/12/revision" in error)]
    assert not unexpected, unexpected


def test_confluence_mock_reads_through_http_and_shared_service(editor_browser):
    page, base, errors, _ = editor_browser
    page.add_init_script("localStorage.setItem('lake.guides.seen', JSON.stringify(['global-search-recent-1','quick-open-hotkey-1','browser-localhost-access-1','local-mcp-connect-1','refresh-fab-1']))")
    page.goto(base + "/#/confluence")
    tree = page.get_by_role("navigation", name="문서 트리", exact=True)
    first = tree.locator(".conf-tree-title").first
    first.wait_for()
    title = first.inner_text()
    first.click()
    page.locator(".conf-document > h1").filter(has_text=title).wait_for()
    page.get_by_role("button", name="최신 여부 확인", exact=True).click()
    page.locator(".conf-current").wait_for()
    assert "최신 발행 버전" in page.locator(".conf-current").inner_text()
    assert not errors, errors
