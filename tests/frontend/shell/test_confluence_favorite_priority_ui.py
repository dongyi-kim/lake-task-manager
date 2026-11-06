"""Search stays local while favorites and the complete index arrive incrementally."""
import json
from urllib.parse import parse_qs, urlsplit

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401
from frontend.shell.test_confluence_browser_ui import install_documents


def test_favorite_path_appears_early_and_search_does_not_pause_or_reset_index(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    install_documents(page)
    favorite={'id':'104','title':'2026 ETL 업무','space':'ETL'}
    page.add_init_script("localStorage.setItem('ltm.confluence.favorites:https://docs.example/wiki',"+json.dumps(json.dumps([favorite]))+");")
    roots=[{'id':'101','title':'Project','ancestors':[],'hasChildren':True,'space':{'key':'ETL'}}]
    ancestors=[{'id':'101','title':'Project'},{'id':'102','title':'2026'},{'id':'103','title':'ETL'}]
    anchor={**favorite,'ancestors':ancestors,'space':{'key':'ETL'}}
    leaf={'id':'108','title':'Deep Guide','space':{'key':'ETL'},'ancestors':[*ancestors,{'id':'104','title':favorite['title']},
        {'id':'105','title':'Design'},{'id':'106','title':'Detail'},{'id':'107','title':'Review'}]}
    held, calls = [], []
    def tree(route):
        url=urlsplit(route.request.url); args=parse_qs(url.query)
        calls.append((url.path,int(args.get('start',[0])[0])))
        if url.path.endswith('/snapshot'):
            route.fulfill(json={'batches':[],'jobs':[{'parent':'','start':0,'depth':0}]})
        elif url.path.endswith('/favorite'):
            route.fulfill(json={'results':[anchor,leaf],'nextStart':None})
        elif url.path.endswith('/branch'):
            route.fulfill(json={'parent':'','start':0,'results':roots,'nextStart':None})
        else:
            held.append(route)
    page.route('**/api/confluence/tree?*',tree)
    page.route('**/api/confluence/tree/**',tree)
    page.goto(base+'/#/confluence')
    search=page.get_by_role('textbox',name='문서 트리 검색')
    nav=page.get_by_role('navigation',name='문서 트리',exact=True)
    search.fill('d E e P g U i D e')
    expect(nav.locator('[data-page-id="108"].is-match')).to_be_visible()
    expect(nav).to_have_attribute('aria-busy','true')
    assert held and [c for c in calls if not c[0].endswith('/snapshot')][:2]==[('/api/confluence/tree/branch',0),('/api/confluence/tree/favorite',0)]
    snapshots=sum(path.endswith('/snapshot') for path,_ in calls)  # main tree + saved-list SQLite snapshots
    nav.locator('[data-page-id="108"] .conf-tree-title').click()
    expect(page.locator('.conf-document')).to_be_visible()
    assert len([path for path,_ in calls if path.endswith('/branch')])==1
    for text in ['unknown','deep','', 'd e e p g u i d e']:
        search.fill(text)
    held[0].fulfill(json={'results':roots,'nextStart':500})
    page.wait_for_function("document.querySelector('.conf-tree').getAttribute('aria-busy')==='true'")
    with page.expect_request('**/api/confluence/tree?*start=1000*'):
        # Wait for the queued next batch through the browser's request event loop.
        page.wait_for_timeout(50)
        assert len(held)==2
        held[1].fulfill(json={'results':[anchor],'nextStart':1000})
    search.fill('DEEP GUIDE')
    expect(nav.locator('[data-page-id="108"].is-match')).to_be_visible()
    page.wait_for_timeout(50)
    held[2].fulfill(json={'results':[leaf],'nextStart':None})
    expect(nav).to_have_attribute('aria-busy','false')
    expect(nav.locator('[data-page-id="108"].is-match')).to_be_visible()
    assert len([c for c in calls if c[0].endswith('/snapshot')])==snapshots
    assert [s for path,s in calls if path=='/api/confluence/tree']==[0,500,1000]
    assert not errors, errors
