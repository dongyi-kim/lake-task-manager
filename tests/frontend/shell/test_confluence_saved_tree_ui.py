"""Saved branches share the main tree's rows, cache and keyboard focus treatment."""
import json
import os
from pathlib import Path

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401
from frontend.shell.test_confluence_browser_ui import install_documents


def test_saved_document_branches_and_compact_search_focus(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    install_documents(page)
    records = [{'id': '12', 'title': '적재 가이드', 'space': 'ETL'}]
    page.add_init_script("localStorage.setItem('ltm.confluence.favorites:https://docs.example/wiki'," + json.dumps(json.dumps(records)) + ");"
                         "localStorage.setItem('ltm.confluence.recent:https://docs.example/wiki'," + json.dumps(json.dumps(records)) + ");")
    page.goto(base + '/#/confluence/12')
    expect(page.locator('.conf-document h1')).to_have_text('적재 가이드')
    nav = page.get_by_role('navigation', name='문서 트리', exact=True)
    expect(nav.locator('[data-page-id="12"].is-favorite')).to_be_visible()
    expect(nav.locator('[data-page-id="12"] .conf-tree-star')).to_be_visible()
    for title in ['즐겨찾기', '최근 열어본 문서']:
        section = page.locator('section.conf-local-list').filter(has=page.get_by_role('button', name=title, exact=True))
        section.get_by_role('button', name='적재 가이드 하위 문서', exact=True).click()
        expect(section.get_by_role('button', name='하위 운영 문서', exact=True)).to_be_visible()
        expect(section.locator('[data-page-id="13"] .conf-tree-toggle')).to_have_count(0)
        section.get_by_role('button', name='적재 가이드 하위 문서', exact=True).click()
        expect(section.get_by_role('button', name='하위 운영 문서', exact=True)).to_have_count(0)
        section.get_by_role('button', name='적재 가이드 하위 문서', exact=True).click()
    search = page.get_by_label('문서 트리 검색', exact=True)
    search.fill('적 재 가 이 드')
    assert search.bounding_box()['height'] <= 30
    style = search.evaluate("e=>({outline:getComputedStyle(e).outlineStyle,shadow:getComputedStyle(e).boxShadow,outer:getComputedStyle(e.parentElement).boxShadow})")
    assert style['outline'] == 'none' and style['shadow'] == 'none' and style['outer'] != 'none'
    assert int(nav.locator('[data-page-id="12"] .conf-tree-title').evaluate('e=>getComputedStyle(e).fontWeight')) >= 700
    expect(nav.locator('[data-page-id="12"] .conf-tree-star')).to_be_visible()
    destination = os.environ.get('LTM_UI_EVIDENCE')
    if destination:
        for theme in ['light', 'dark']:
            page.evaluate('(theme)=>document.documentElement.dataset.theme=theme', theme)
            page.screenshot(path=str(Path(destination) / f'confluence-navigation-{theme}.png'))
        page.set_viewport_size({'width': 700, 'height': 900})
        page.get_by_role('button', name='문서 탐색', exact=True).click()
        expect(search).to_be_visible()
        page.screenshot(path=str(Path(destination) / 'confluence-navigation-narrow.png'))
    assert not errors, errors


def test_favorite_in_another_space_expands_without_replacing_current_tree(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    install_documents(page)
    page.add_init_script("localStorage.setItem('ltm.confluence.favorites:https://docs.example/wiki', JSON.stringify([{id:'12',title:'적재 가이드',space:'ETL'}]))")
    def ops(route):
        route.fulfill(json={'batches': [{'parent':'','start':0,'nextStart':None,'results':[
            {'id':'900','title':'운영 Space 루트','hasChildren':False,'ancestors':[],'space':{'key':'OPS'}}]}], 'jobs':[]})
    page.route('**/api/confluence/tree/snapshot?space=OPS', ops)
    page.goto(base + '/#/confluence')
    page.get_by_label('Space', exact=True).select_option('OPS')
    nav = page.get_by_role('navigation', name='문서 트리', exact=True)
    expect(nav.get_by_role('button', name='운영 Space 루트', exact=True)).to_be_visible()
    favorite = page.get_by_role('region', name='즐겨찾기', exact=True)
    favorite.get_by_role('button', name='적재 가이드 하위 문서', exact=True).click()
    expect(favorite.get_by_role('button', name='하위 운영 문서', exact=True)).to_be_visible()
    expect(page.get_by_label('Space', exact=True)).to_have_value('OPS')
    expect(nav.locator('[data-page-id]')).to_have_count(1)
    assert not errors, errors
