"""Real-browser document navigation with deterministic upstream-shaped responses."""
import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest

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
        elif path.endswith("/tree") or path.endswith('/snapshot') or path.endswith('/branch'):
            data = {"results": [
                {"id": "12", "title": "적재 가이드", "ancestors": []},
                {"id": "90", "title": "홈페이지 밖 루트 문서", "ancestors": []},
                {"id": "13", "title": "하위 운영 문서", "ancestors": [{"id": "12", "title": "적재 가이드"}]},
                {"id": "22", "title": "아직 펼치지 않은 적재 설계", "ancestors": [{"id": "12", "title": "적재 가이드"}]},
            ], "size": 4, "nextStart": None}
            parents = {str(r['ancestors'][-1]['id']) for r in data['results'] if r.get('ancestors')}
            for row in data['results']:
                row['hasChildren'] = row['id'] in parents
                row['space'] = {'key': args.get('space', ['ETL'])[0]}
            if path.endswith('/snapshot'):
                data = {'batches': [{'parent': '', 'start': 0, **data}], 'jobs': []}
            elif path.endswith('/branch'):
                parent = args.get('parent', [''])[0]
                data['results'] = [r for r in data['results'] if str((r.get('ancestors') or [{}])[-1].get('id', '')) == parent]
                data.update(parent=parent, start=0)
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


def test_tree_search_highlights_hierarchy_without_search_requests(editor_browser):
    from playwright.sync_api import expect

    page, base, errors, _ = editor_browser
    state = install_documents(page)
    requests = []
    def metadata(route):
        if '/snapshot?' in route.request.url:
            route.fulfill(json={'batches': [{'parent': '', 'start': 0, 'results': [{'id':'12','title':'Data Lake Root','hasChildren':True},{'id':'90','title':'Unrelated document','hasChildren':False}], 'nextStart': None}], 'jobs': [{'parent': '12', 'start': 0, 'depth': 1}]}); return
        args = parse_qs(urlsplit(route.request.url).query)
        parent = args.get('parent', [''])[0]
        requests.append(parent)
        if not parent:
            data = {'results': [{'id': '12', 'title': 'Data Lake Root', 'hasChildren': True}, {'id': '90', 'title': 'Unrelated document', 'hasChildren': False}],
                    'parent': '', 'start': 0, 'nextStart': None}
        else:
            row = {'id': '22', 'title': 'Data Lake 적재 가이드', 'ancestors': [{'id': '12', 'title': 'Data Lake Root'}], 'hasChildren': False}
            data = {'results': [row, row] if parent == '12' else [], 'parent': parent, 'start': 0, 'nextStart': None}
        route.fulfill(json=data)
    page.route('**/api/confluence/tree/**', metadata)
    upstream_searches = []
    page.on('request', lambda request: upstream_searches.append(request.url) if '/api/confluence/search?' in request.url else None)
    page.goto(base + '/#/confluence')
    tree = page.get_by_role('navigation', name='문서 트리', exact=True)
    expect(tree).to_have_attribute('aria-busy', 'false')
    tree.get_by_role('button', name='Data Lake Root 하위 문서', exact=True).wait_for()
    assert tree.locator('[data-page-id="90"] .conf-tree-toggle').count() == 0
    assert tree.locator('[data-page-id="22"]').count() == 0
    for query in ['datalake', 'DATA   LAKE', 'ｄａｔａ　ｌａｋｅ', '적재가이드', '적 재 가 이 드']:
        page.get_by_label('문서 트리 검색', exact=True).fill(query)
        expect(tree.locator('.is-match')).to_have_count(1 if query.startswith('적') else 2)
        expect(tree.locator('[data-page-id="22"].is-match')).to_contain_text('Data Lake 적재 가이드')
        expect(tree.locator('[data-page-id="12"] .conf-tree-toggle')).to_have_attribute('aria-expanded', 'true')
        assert tree.locator('[data-page-id="22"]').evaluate("el => el.style.getPropertyValue('--depth')") == '1'
        expect(tree.get_by_role('button', name='Unrelated document', exact=True)).to_be_visible()
        assert tree.locator('[data-page-id="22"] .conf-tree-toggle').count() == 0
    assert requests == ['12'] and upstream_searches == []
    assert state['reads'] == []
    page.get_by_label('문서 트리 검색', exact=True).fill('datalake')
    page.get_by_role('button', name='다음 일치 문서').click()
    expect(tree.locator('[data-page-id="22"]')).to_have_class(re.compile('is-current-match'))
    page.get_by_role('button', name='이전 일치 문서').click()
    expect(tree.locator('[data-page-id="12"]')).to_have_class(re.compile('is-current-match'))
    tree.locator('[data-page-id="22"] .conf-tree-title').click()
    expect(page.locator('.conf-find-status')).to_contain_text('2 / 2개 일치')
    page.get_by_label('문서 트리 검색', exact=True).press('Escape')
    expect(tree.locator('.is-match')).to_have_count(0)
    expect(tree.locator('[data-page-id="22"]')).to_have_count(0)
    tree.get_by_role('button', name='Data Lake Root 하위 문서', exact=True).click()
    page.get_by_label('문서 트리 검색', exact=True).fill('not present')
    expect(page.locator('.conf-find-status')).to_contain_text('일치하는 제목 없음')
    expect(tree.locator('[data-page-id="22"]')).to_be_visible()
    page.get_by_role('button', name='트리 검색 지우기').click()
    expect(tree.locator('[data-page-id="22"]')).to_be_visible()
    controls = page.locator('.conf-navigation-controls').bounding_box()
    favorites = page.get_by_role('region', name='즐겨찾기', exact=True).bounding_box()
    assert controls['y'] + controls['height'] <= favorites['y']
    assert tree.locator('[data-page-id="22"]').bounding_box()['height'] <= 28
    assert not errors


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
    page.get_by_role("region", name="즐겨찾기", exact=True).get_by_role("button", name="적재 가이드", exact=True).wait_for()
    assert state["reads"] == [("12", 2)]

    # Persisted favorites remain usable even if Space enumeration is unavailable.
    state["spaces_fail"] = True
    page.reload()
    page.get_by_role("region", name="즐겨찾기", exact=True).get_by_role("button", name="적재 가이드", exact=True).wait_for()
    page.get_by_text("Confluence 연결 대기", exact=False).wait_for()
    state["spaces_fail"] = False
    page.get_by_role("button", name="다시 시도", exact=True).click()
    tree.get_by_role("button", name="적재 가이드", exact=True).wait_for()

    page.get_by_label("문서 트리 검색", exact=True).fill("적재 설계")
    result = tree.get_by_role("button", name="아직 펼치지 않은 적재 설계", exact=True)
    result.wait_for(); result.click()
    page.locator(".conf-document h1").filter(has_text="아직 펼치지 않은 적재 설계").wait_for()
    assert state["reads"][-1][0] == "22"
    page.get_by_role("region", name="즐겨찾기", exact=True).get_by_role("button", name="적재 가이드", exact=True).click()
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
    assert page.get_by_role("region", name="즐겨찾기", exact=True).locator(".conf-tree-title").count() == 0
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="목차", exact=True).wait_for()
    assert not toc.is_visible()
    page.get_by_role("button", name="목차", exact=True).click()
    toc.get_by_role("button", name="버전 3 본문", exact=True).wait_for()
    toc.get_by_role("button", name="버전 3 본문", exact=True).press("Enter")
    assert not toc.is_visible()
    assert page.locator(".conf-workspace").evaluate("el => el.scrollWidth <= el.clientWidth + 2")
    page.screenshot(path=str(tmp_path / "confluence-mobile.png"))
    page.get_by_role("button", name="문서 탐색", exact=True).click()
    page.get_by_role("dialog", name="문서 탐색", exact=True).wait_for()
    page.get_by_role("button", name="문서 탐색 접기", exact=True).click()
    unexpected = [error for error in errors if not (
        error.startswith("HTTP 503 ") and any(path in error for path in (
            "/api/confluence/spaces", "/api/confluence/pages/12/revision")))]
    assert not unexpected, unexpected


def test_confluence_docked_panel_resize_and_collapse_persist(editor_browser):
    page, base, errors, _ = editor_browser
    state = install_documents(page)
    page.set_viewport_size({"width": 1440, "height": 960})
    page.goto(base + "/#/confluence/12")
    page.locator(".conf-document h1").wait_for()
    sidebar = page.locator(".conf-sidebar")
    toc = page.locator(".conf-toc")
    reader = page.locator(".conf-reader")
    left = page.get_by_role("separator", name="문서 탐색 너비 조절", exact=True)
    right = page.get_by_role("separator", name="목차 너비 조절", exact=True)

    for handle, panel, movement in ((left, sidebar, 48), (right, toc, -40)):
        box = handle.bounding_box()
        before = panel.bounding_box()["width"]
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 100)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + movement, box["y"] + 100, steps=5)
        page.mouse.up()
        assert abs(panel.bounding_box()["width"] - before - abs(movement)) <= 2
        assert page.locator(".conf-resizing").count() == 0
    before_keys = (sidebar.bounding_box()["width"], toc.bounding_box()["width"])
    left.press("ArrowLeft")
    right.press("ArrowRight")
    widths = {"sidebarWidth": sidebar.bounding_box()["width"], "tocWidth": toc.bounding_box()["width"]}
    assert abs(widths["sidebarWidth"] - before_keys[0] + 16) <= 2
    assert abs(widths["tocWidth"] - before_keys[1] + 16) <= 2
    saved = page.evaluate("JSON.parse(localStorage.getItem('lake.confluence.layout.v1'))")
    assert all(abs(saved[key] - value) <= 2 for key, value in widths.items())

    # Folding changes only layout, preserving both the document and network work.
    body_width = reader.bounding_box()["width"]
    read_count = len(state["reads"])
    page.get_by_role("button", name="문서 탐색 접기", exact=True).click()
    page.get_by_role("button", name="목차 접기", exact=True).click()
    sidebar.wait_for(state="hidden")
    toc.wait_for(state="hidden")
    assert reader.bounding_box()["width"] > body_width + 500
    assert len(state["reads"]) == read_count
    page.reload()
    page.locator(".conf-document h1").wait_for()
    assert not sidebar.is_visible() and not toc.is_visible()
    page.get_by_role("button", name="문서 탐색", exact=True).click()
    page.get_by_role("button", name="목차", exact=True).click()
    sidebar.wait_for(); toc.wait_for()
    assert abs(sidebar.bounding_box()["width"] - widths["sidebarWidth"]) <= 2
    assert abs(toc.bounding_box()["width"] - widths["tocWidth"]) <= 2
    reader.press("PageDown")
    page.wait_for_function("document.querySelector('.conf-reader').scrollTop > 100")
    assert reader.evaluate("el => getComputedStyle(el,'::-webkit-scrollbar').height") == "7px"
    assert reader.evaluate("el => getComputedStyle(el,'::-webkit-scrollbar').width") == "0px"
    assert page.evaluate("window.scrollY") == 0
    assert not errors, errors


@pytest.mark.parametrize("width", [700, 390], ids=["narrow-window", "phone"])
def test_confluence_compact_drawers_leave_document_visible(editor_browser, width):
    page, base, errors, _ = editor_browser
    install_documents(page)
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(base + "/#/confluence/12")
    page.locator(".conf-compact .conf-document h1").wait_for()
    sidebar = page.get_by_role("dialog", name="문서 탐색", exact=True)
    toc = page.get_by_role("dialog", name="이 문서의 목차", exact=True)
    reader = page.locator(".conf-reader")
    assert not sidebar.is_visible() and not toc.is_visible()
    assert reader.bounding_box()["height"] > 844 * .65
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert page.evaluate("document.documentElement.scrollHeight <= innerHeight + 1")

    navigation = page.get_by_role("button", name="문서 탐색", exact=True)
    navigation.click()
    sidebar.wait_for()
    assert page.locator("dialog:modal").count() == 1
    page.keyboard.press("Shift+Tab")
    # Native dialogs may hand focus to browser chrome, but never to inert page controls.
    assert page.evaluate("document.activeElement === document.body || !!document.activeElement.closest('.conf-sidebar')")
    page.keyboard.press("Tab")
    assert page.evaluate("!!document.activeElement.closest('.conf-sidebar')")
    page.keyboard.press("Escape")
    sidebar.wait_for(state="hidden")
    assert navigation.evaluate("el => el === document.activeElement")

    navigation.click()
    sidebar.wait_for()
    box = sidebar.bounding_box()
    # The remaining body strip is a dismissible backdrop, not a second scroll surface.
    assert box["x"] + box["width"] < width - 5
    page.mouse.click(width - 4, 820)
    sidebar.wait_for(state="hidden")
    navigation.click()
    sidebar.get_by_role("navigation", name="문서 트리").get_by_role("button", name="적재 가이드", exact=True).click()
    sidebar.wait_for(state="hidden")

    page.get_by_role("button", name="목차", exact=True).click()
    toc.wait_for()
    assert page.locator("dialog:modal").count() == 1
    assert not sidebar.is_visible()
    toc.get_by_role("button", name="공통 절차", exact=True).nth(1).click()
    toc.wait_for(state="hidden")
    page.wait_for_function("document.activeElement.matches('.conf-document-body h3')")
    assert reader.evaluate("el => el.scrollTop") > 0
    assert page.evaluate("window.scrollY") == 0
    page.get_by_role("button", name="목차", exact=True).click()
    current = toc.locator("[aria-current='location']")
    assert current.inner_text() == "공통 절차"
    page.keyboard.press("Escape")
    toc.wait_for(state="hidden")

    # The native drawer must release its top layer before global search can take focus.
    navigation.click()
    tree_search = sidebar.get_by_label("문서 트리 검색", exact=True)
    tree_search.press("/")
    assert tree_search.input_value() == "/"
    assert sidebar.is_visible() and page.locator(".sr-input").count() == 0
    tree_search.press("Control+k")
    search = page.get_by_placeholder("Jira · Confluence · Bitbucket 통합 검색…", exact=True)
    search.wait_for()
    sidebar.wait_for(state="hidden")
    assert page.locator("dialog:modal").count() == 0
    assert search.evaluate("el => el === document.activeElement")
    search.press("Escape")
    search.wait_for(state="hidden")
    navigation.click()
    sidebar.get_by_role("button", name="문서 탐색 접기", exact=True).press("/")
    search.wait_for()
    sidebar.wait_for(state="hidden")
    assert page.locator("dialog:modal").count() == 0
    assert search.evaluate("el => el === document.activeElement")
    search.press("Escape")
    search.wait_for(state="hidden")

    # Compact overlays never overwrite the saved desktop dock preferences.
    page.set_viewport_size({"width": 1440, "height": 960})
    page.get_by_role("separator", name="문서 탐색 너비 조절", exact=True).wait_for()
    page.get_by_role("separator", name="목차 너비 조절", exact=True).wait_for()
    assert page.locator("dialog:modal").count() == 0
    assert not errors, errors


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


def test_confluence_local_lists_fold_and_remember_successful_reads(editor_browser):
    page, base, errors, _ = editor_browser
    state = install_documents(page)
    page.goto(base + "/#/confluence/12")
    recent = page.get_by_role("region", name="최근 열어본 문서", exact=True)
    recent.get_by_role("button", name="적재 가이드", exact=True).wait_for()
    page.get_by_role("button", name="즐겨찾기 추가", exact=True).click()
    favorites = page.get_by_role("region", name="즐겨찾기", exact=True)
    favorites.get_by_role("button", name="즐겨찾기", exact=True).click()
    assert not favorites.locator(".conf-tree-title").is_visible()
    before = list(state["reads"])
    recent.get_by_role("button", name="최근 열어본 문서", exact=True).click()
    assert not recent.locator(".conf-tree-title").is_visible()
    assert state["reads"] == before
    page.reload()
    page.locator(".conf-document h1").wait_for()
    assert recent.get_by_role("button", name="최근 열어본 문서", exact=True).get_attribute("aria-expanded") == "false"
    assert favorites.get_by_role("button", name="즐겨찾기", exact=True).get_attribute("aria-expanded") == "false"
    page.goto(base + "/#/confluence/22")
    page.locator(".conf-document h1").filter(has_text="아직 펼치지 않은 적재 설계").wait_for()
    recent.get_by_role("button", name="최근 열어본 문서", exact=True).click()
    assert recent.locator(".conf-tree-title").all_text_contents() == ["아직 펼치지 않은 적재 설계", "적재 가이드"]
    recent.get_by_role("button", name="적재 가이드", exact=True).click()
    page.locator(".conf-document h1").filter(has_text="적재 가이드").wait_for()
    assert recent.locator(".conf-tree-title").all_text_contents() == ["적재 가이드", "아직 펼치지 않은 적재 설계"]
    page.route("**/api/confluence/pages/404", lambda route: route.fulfill(status=404, json={"service": "confluence", "error": "찾을 수 없음"}))
    page.goto(base + "/#/confluence/404")
    page.get_by_text("찾을 수 없음", exact=False).wait_for()
    assert recent.locator(".conf-tree-title").count() == 2
    bounds = page.evaluate("""async () => {
      const {rememberDocument,readRecentDocuments} = await import('/lib/confluenceNavigation.js');
      let rows=[];
      for(let i=1;i<=25;i++) rows=rememberDocument('fixture-only',rows,{id:String(i),title:'Doc '+i,space:{key:'ETL'}});
      const result=readRecentDocuments('fixture-only');
      localStorage.removeItem('ltm.confluence.recent:fixture-only');
      return result.map(r=>r.id);
    }""")
    assert len(bounds) == 20 and bounds[0] == "25" and bounds[-1] == "6"
    assert not [error for error in errors if not (error.startswith("HTTP 404 ") and "/api/confluence/pages/404" in error)]


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
