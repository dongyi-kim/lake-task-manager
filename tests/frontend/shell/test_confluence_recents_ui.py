"""Real SQLite history shared by the document tree and global search."""
import json
import os
import re
from pathlib import Path

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401
from frontend.shell.test_confluence_browser_ui import install_documents


def test_shared_recent_menu_migration_search_and_tree_emphasis(editor_browser):
    from playwright.sync_api import expect
    page,base,errors,_=editor_browser
    install_documents(page)
    for cid in range(101,114):
        assert page.request.post(base+'/api/recent',data={'url':f'https://docs.example/wiki/spaces/ETL/pages/{cid}/title',
            'kind':'confluence','title':f'문서 {cid}','data':{'pageId':str(cid),'space':'ETL'}}).ok
    page.add_init_script("if(!sessionStorage.getItem('legacy-seeded')){sessionStorage.setItem('legacy-seeded','1');localStorage.setItem('ltm.confluence.recent:https://docs.example/wiki',"+json.dumps(json.dumps([
        {'id':'12','title':'예전 가이드','space':'ETL','openedAt':1000000}]))+");}")
    page.goto(base+'/#/confluence')
    recent=page.get_by_role('region',name='최근 열어본 문서',exact=True)
    expect(recent.locator('.conf-tree-title')).to_have_count(10)
    expect(recent.locator('.conf-tree-title').first).to_have_text('문서 113')
    page.wait_for_function("localStorage.getItem('ltm.confluence.recent:https://docs.example/wiki')===null")
    # The older (11th+) document is still bold in the actual tree.
    nav=page.get_by_role('navigation',name='문서 트리',exact=True)
    old=nav.locator('[data-page-id="12"]')
    expect(old).to_have_class(re.compile('is-recent'))
    assert int(old.locator('.conf-tree-title').evaluate('e=>getComputedStyle(e).fontWeight'))>=700
    assert old.locator('.conf-tree-star').count()==0
    old.locator('.conf-tree-title').click()
    expect(page.locator('.conf-document h1')).to_have_text('적재 가이드')
    expect(recent.locator('.conf-tree-title').first).to_have_text('적재 가이드')
    page.keyboard.press('/')
    search=page.locator('.sr-ov')
    expect(search).to_be_visible()
    expect(search.locator('.sr-recent').first).to_contain_text('적재 가이드')
    search.locator('.sr-recent').filter(has_text='문서 113').click()
    expect(page).to_have_url(base+'/#/confluence/113')
    expect(page.locator('.conf-document h1')).to_have_text('아직 펼치지 않은 적재 설계')
    expect(recent.locator('.conf-tree-title').first).to_have_text('아직 펼치지 않은 적재 설계')
    rows=page.request.get(base+'/api/recent?kind=confluence&limit=100').json()
    assert len(rows)==14 and len({r['url'] for r in rows})==14
    assert rows[0]['pageId']=='113'
    page.keyboard.press('/')
    search.locator('.sr-recent').filter(has_text='적재 가이드').get_by_role('button',name='목록에서 지우기').click()
    page.keyboard.press('Escape')
    expect(old).not_to_have_class(re.compile('is-recent'))
    # No per-browser list is required to restore the same SQLite history.
    page.evaluate("localStorage.removeItem('recent.items')")
    page.reload()
    expect(recent.locator('.conf-tree-title')).to_have_count(10)
    expect(recent.locator('.conf-tree-title').first).to_have_text('아직 펼치지 않은 적재 설계')
    expect(old).not_to_have_class(re.compile('is-recent'))
    destination=os.environ.get('LTM_UI_EVIDENCE')
    if destination:
        page.screenshot(path=str(Path(destination)/'confluence-shared-recents.png'))
    assert not errors,errors
