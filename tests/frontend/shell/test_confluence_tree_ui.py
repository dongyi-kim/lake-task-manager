"""Tree-local finding and full-width reader regressions."""
from urllib.parse import parse_qs, urlsplit

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401
from frontend.shell.test_confluence_browser_ui import install_documents


def test_tree_keeps_all_pages_after_capped_empty_and_short_batches(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    install_documents(page)
    calls = []
    root = {'id': '12', 'title': 'Complete tree', 'ancestors': [], 'hasChildren': True}
    def tree(route):
        if '/snapshot?' in route.request.url:
            route.fulfill(json={'batches': [{'parent': '', 'start': 0, 'results': [root], 'nextStart': None}], 'jobs': [{'parent': '12', 'start': 0, 'depth': 1}]}); return
        args = parse_qs(urlsplit(route.request.url).query)
        start, parent = int(args['start'][0]), args.get('parent', [''])[0]
        if not parent:
            route.fulfill(json={'parent': '', 'start': 0, 'results': [root], 'nextStart': None}); return
        if parent != '12':
            route.fulfill(json={'parent': parent, 'start': start, 'results': [], 'nextStart': None}); return
        calls.append(start)
        if start == 0:
            rows, following = [], 100
        elif start == 100:
            rows, following = [], 200
        else:
            rows = [{'id': str(i), 'title': f'Page {i}', 'ancestors': [root], 'hasChildren': False}
                    for i in range(start, min(start + 25, 405))]
            following = start + 25 if start < 400 else None
        route.fulfill(json={'parent': parent, 'results': rows, 'size': len(rows), 'start': start, 'nextStart': following})
    page.route('**/api/confluence/tree/**', tree)
    page.goto(base + '/#/confluence')
    nav = page.get_by_role('navigation', name='문서 트리', exact=True)
    expect(page.locator('.conf-tree-caption')).to_contain_text('206')
    expect(nav).to_have_attribute('aria-busy', 'false')
    nav.get_by_role('button', name='Complete tree 하위 문서', exact=True).click()
    expect(nav.locator('[data-page-id]')).to_have_count(206)
    assert set(nav.locator('[data-page-id]').evaluate_all('rows=>rows.map(row=>row.dataset.pageId)')) == {'12', *map(str, range(200, 405))}
    page.get_by_label('문서 트리 검색', exact=True).fill('p A g E 404')
    expect(nav.locator('.is-match')).to_have_text('Page 404')
    nav.locator('.is-match .conf-tree-title').click()
    expect(page.locator('.conf-document')).to_be_visible()
    assert calls == [0, 100, *range(200, 401, 25)]
    assert not errors


def test_tree_partial_retry_and_space_switch_discard_late_metadata(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    install_documents(page)
    pending, calls = [], []
    def tree(route):
        if '/snapshot?' in route.request.url:
            route.fulfill(json={'batches': [{'parent': '', 'start': 0, 'results': [{'id':'12','title':'Root','hasChildren':True}], 'nextStart': None}], 'jobs': [{'parent': '12', 'start': 0, 'depth': 1}]}); return
        args = parse_qs(urlsplit(route.request.url).query)
        space, parent = args['space'][0], args.get('parent', [''])[0]
        calls.append((space, parent))
        if space == 'OPS':
            pending.append(route)
        elif not parent:
            route.fulfill(json={'parent': '', 'start': 0, 'results': [{'id': '12', 'title': 'Root', 'hasChildren': True}], 'nextStart': None})
        elif calls.count(('ETL', '12')) <= 2:
            route.fulfill(status=503, json={'service': 'confluence', 'error': '목록 연결 지연'})
        else:
            route.fulfill(json={'parent': parent, 'start': 0, 'results': [{'id': '22', 'title': 'Nested needle', 'hasChildren': False, 'ancestors': [{'id': '12', 'title': 'Root'}]}], 'nextStart': None})
    page.route('**/api/confluence/tree/**', tree)
    page.goto(base + '/#/confluence')
    nav = page.get_by_role('navigation', name='문서 트리', exact=True)
    page.get_by_role('button', name='문서 목록 다시 시도').wait_for()
    expect(nav.get_by_role('button', name='Root', exact=True)).to_be_visible()
    page.get_by_label('문서 트리 검색', exact=True).fill('nestedneedle')
    page.get_by_role('button', name='문서 목록 다시 시도').click()
    expect(nav.locator('.is-match')).to_have_text('Nested needle')
    assert calls == [('ETL', '12'), ('ETL', '12'), ('ETL', '12')]
    page.get_by_label('Space', exact=True).select_option('OPS')
    page.wait_for_function("document.querySelector('.conf-tree').getAttribute('aria-busy') === 'true'")
    # Wait for the deliberately held request without introducing blanket server latency.
    with page.expect_request('**/api/confluence/tree/snapshot?space=ETL'):
        page.get_by_label('Space', exact=True).select_option('ETL')
    expect(nav.locator('.is-match')).to_have_text('Nested needle')
    assert pending
    pending[0].fulfill(json={'results': [{'id': '999', 'title': 'Wrong Space needle'}], 'size': 1, 'nextStart': None})
    page.get_by_label('문서 트리 검색', exact=True).fill('needle')
    expect(nav.locator('.is-match')).to_have_count(1)
    expect(nav.locator('[data-page-id="999"]')).to_have_count(0)
    assert all('503' in error for error in errors), errors


def test_reader_uses_available_width_and_expand_inherits_body_style(editor_browser, tmp_path):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    install_documents(page)
    def document(route):
        route.fulfill(json={'id': '12', 'title': '넓은 문서와 접기 영역', 'space': {'key': 'ETL'},
            'version': {'number': 1}, 'html': '<h2>내용</h2><div style="color:rgb(180,30,40);font-family:serif">'
            '<p>바깥 문단</p><details class="conf-expand"><summary>참고 내용 펼치기</summary>'
            '<p>안쪽 문단</p><pre><code>' + ('wide_code_' * 100) + '</code></pre></details></div>'})
    page.route('**/api/confluence/pages/12', document)
    page.set_viewport_size({'width': 1920, 'height': 1080})
    page.goto(base + '/#/confluence/12')
    body = page.locator('.conf-document')
    body.get_by_text('참고 내용 펼치기', exact=True).click()
    outer = body.get_by_text('바깥 문단', exact=True)
    inner = body.get_by_text('안쪽 문단', exact=True)
    styles = "el => {const s=getComputedStyle(el);return [s.fontFamily,s.fontSize,s.color,s.lineHeight]}"
    assert inner.evaluate(styles) == outer.evaluate(styles)
    assert inner.evaluate('el => getComputedStyle(el).color') == 'rgb(180, 30, 40)'
    assert body.locator('summary').evaluate('el => getComputedStyle(el).fontSize') == '13px'
    reader = page.locator('.conf-reader')
    assert abs(body.bounding_box()['width'] - reader.bounding_box()['width'] + 40) <= 2
    code = body.locator('pre')
    assert code.evaluate('el => el.scrollWidth > el.clientWidth')
    code.evaluate('el => el.scrollLeft = 300')
    assert code.evaluate('el => el.scrollLeft') > 0
    assert code.evaluate('el => getComputedStyle(el).scrollbarWidth') == 'auto'
    assert code.evaluate("el => getComputedStyle(el,'::-webkit-scrollbar').display") == 'block'
    assert code.evaluate("el => getComputedStyle(el,'::-webkit-scrollbar').height") == '7px'
    page.screenshot(path=str(tmp_path / 'confluence-wide.png'))
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(page.locator('.conf-compact')).to_be_visible()
    assert body.bounding_box()['width'] >= reader.bounding_box()['width'] - 26
    assert page.locator('.conf-workspace').evaluate('el => el.scrollWidth <= el.clientWidth + 2')
    page.screenshot(path=str(tmp_path / 'confluence-narrow.png'))
    assert not errors
