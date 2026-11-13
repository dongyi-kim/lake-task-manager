"""Actual tab navigation: active-only reads and existing durable draft ownership."""
import re
from urllib.parse import urlsplit

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401
from frontend.shell.test_confluence_browser_ui import install_documents


def test_tab_strip_keyboard_overflow_close_guard_and_zoom(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    page.route('**/tabs-fixture', lambda route: route.fulfill(content_type='text/html', body='''
      <html data-theme="light"><head><link rel="stylesheet" href="/styles/tokens.css">
      <link rel="stylesheet" href="/styles/base.css"><link rel="stylesheet" href="/styles/open-item-tabs.css"></head>
      <body><main id="tabs"></main><section id="body" role="tabpanel"></section></body></html>'''))
    page.goto(base + '/tabs-fixture')
    page.evaluate("""async () => {
      const {createApp,reactive}=await import('/vendor/vue.esm-browser.prod.js');
      const {createOpenItemTabs}=await import('/lib/openItemTabs.js');
      const {default:OpenItemTabs}=await import('/components/ui/OpenItemTabs.js');
      window.tabActions=[];
      const store=createOpenItemTabs({kind:'task',reactive});
      for(let i=1;i<=35;i++)store.open({id:'DL-'+i,title:'긴 한국어 업무 제목 '+i});
      store.activate('DL-1');
      window.tabStrip=createApp({components:{OpenItemTabs},data:()=>({store,acceptClose:false}),methods:{
        activate(id){window.tabActions.push(id);this.store.activate(id);},
        close(id){if(this.acceptClose)this.store.close(id);}},
        template:'<OpenItemTabs :items="store.state.items" :active-id="store.state.activeId" panel-id="body" @activate="activate" @close="close"/>'
      }).mount('#tabs');
    }""")
    page.set_viewport_size({'width': 1280, 'height': 900})
    tabs = page.get_by_role('tab')
    expect(tabs).to_have_count(35)
    assert page.locator('.oit-list').evaluate('el=>el.scrollWidth>el.clientWidth')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    tabs.first.focus(); tabs.first.press('End')
    expect(tabs.last).to_be_focused()
    assert page.evaluate('window.tabActions') == []
    page.keyboard.press('Enter')
    assert page.evaluate('window.tabActions') == ['DL-35']
    page.keyboard.press('Delete')
    expect(tabs).to_have_count(35)
    expect(tabs.last).to_be_focused()
    page.evaluate('window.tabStrip.acceptClose=true')
    page.keyboard.press('Delete')
    expect(tabs).to_have_count(34)
    expect(tabs.last).to_be_focused()
    expect(page.locator('[role=tab][aria-selected=true]')).to_have_count(1)
    expect(tabs.last).to_have_attribute('aria-selected', 'true')
    page.emulate_media(reduced_motion='reduce')
    page.evaluate("document.body.style.zoom='2'")
    page.evaluate("window.tabStrip.store.activate('DL-1')")
    expect(tabs.first).to_have_attribute('aria-selected', 'true')
    page.evaluate("window.tabStrip.store.activate('DL-18')")
    middle = page.locator('[role=tab][data-item-id="DL-18"]')
    expect(middle).to_have_attribute('aria-selected', 'true')
    assert middle.evaluate('el=>{const r=el.getBoundingClientRect(),list=el.closest(".oit-list").getBoundingClientRect();return r.left>=list.left-1 && r.right<=list.right+1}')
    middle.focus(); middle.press('ArrowRight')
    expect(page.locator('[role=tab][data-item-id="DL-19"]')).to_be_focused()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert page.locator('[role=tab][data-item-id="DL-19"]').evaluate("el=>getComputedStyle(el).outlineStyle") != 'none'
    assert page.locator('.oit-item').first.evaluate('el=>getComputedStyle(el).transitionDuration') == '0s'
    assert not errors, errors


def open_ticket(page, key):
    page.evaluate("key => window.dispatchEvent(new CustomEvent('lake-open-ticket',{detail:{key}}))", key)
    from playwright.sync_api import expect
    expect(page.locator('.tkt-dlg .tkt-bar')).to_contain_text(key)
    page.locator('.tkt-dlg .tkt-meta .fe-v').first.wait_for()


def test_ticket_tabs_restore_metadata_without_prefetch_and_deduplicate(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    install_documents(page)
    page.goto(base + '/#/mytasks')
    page.locator('.mt-card').first.wait_for()
    open_ticket(page, 'DL-9007')
    open_ticket(page, 'DL-9001')
    strip = page.locator('.tkt-dlg .open-item-tabs')
    expect(strip.get_by_role('tab')).to_have_count(2)
    strip.locator('[role=tab][data-item-id="DL-9007"]').click()
    expect(page.locator('.tkt-dlg .tkt-bar')).to_contain_text('DL-9007')
    expect(strip.get_by_role('tab')).to_have_count(2)
    page.locator('.tkt-dlg').get_by_role('button', name='닫기', exact=True).click()
    reads = []
    page.on('request', lambda request: reads.append(request.url)
            if urlsplit(request.url).path in ['/api/ticket/DL-9001', '/api/ticket/DL-9007'] else None)
    page.reload()
    strip = page.locator('.open-item-tabs')
    expect(strip.get_by_role('tab')).to_have_count(2)
    expect(page.locator('.tkt-dlg')).to_have_count(0)
    assert reads == []
    first = strip.get_by_role('tab').first
    first.focus(); first.press('End')
    expect(strip.get_by_role('tab').last).to_be_focused()
    assert reads == [], 'Moving tab focus must not fetch a body'
    page.keyboard.press('Enter')
    expect(page.locator('.tkt-dlg')).to_be_visible()
    page.locator('.tkt-dlg .tkt-meta .fe-v').first.wait_for()
    inactive = page.locator('.tkt-dlg .oit-item').filter(has=page.locator('[role=tab][data-item-id="DL-9007"]'))
    loaded = list(reads)
    inactive.locator('.oit-close').click()
    expect(page.locator('.tkt-dlg [role=tab]')).to_have_count(1)
    expect(page.locator('.tkt-dlg .tkt-bar')).to_contain_text('DL-9001')
    assert reads == loaded
    last = page.locator('.tkt-dlg [role=tab]')
    last.focus(); last.press('Delete')
    expect(page.locator('.tkt-dlg')).to_have_count(0)
    expect(page.locator('.open-item-tabs')).to_have_count(0)
    assert page.evaluate("!!document.activeElement.closest('.mytasks, header.top')")
    assert not errors, errors


def test_ticket_switch_keeps_comment_draft_and_blocks_failed_durable_save(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    page.goto(base + '/#/mytasks')
    page.locator('.mt-card').first.wait_for()
    open_ticket(page, 'DL-9007')
    page.get_by_role('button', name='＋ 댓글 달기', exact=True).click()
    editor = page.locator('.tkt-compose-editor .ProseMirror[contenteditable=true]')
    editor.fill('탭을 바꾸어도 보관할 댓글 초안')
    open_ticket(page, 'DL-9001')
    page.locator('.tkt-dlg .open-item-tabs [role=tab][data-item-id="DL-9007"]').click()
    page.get_by_role('button', name='＋ 댓글 달기', exact=True).click()
    expect(editor).to_contain_text('탭을 바꾸어도 보관할 댓글 초안')
    page.evaluate("""() => {
      window.originalTabDraftTransaction = IDBDatabase.prototype.transaction;
      IDBDatabase.prototype.transaction = function(...args) {
        const tx = window.originalTabDraftTransaction.apply(this, args);
        if (args[1] === 'readwrite') queueMicrotask(() => tx.abort());
        return tx;
      };
    }""")
    editor.fill('저장 실패한 내용도 현재 화면에 보존')
    page.locator('.tkt-compose-editor [role=alert]').wait_for()
    target = page.locator('.tkt-dlg .open-item-tabs [role=tab][data-item-id="DL-9001"]')
    target.focus(); target.press('Enter')
    expect(page.locator('.tkt-dlg .tkt-bar')).to_contain_text('DL-9007')
    expect(editor).to_contain_text('저장 실패한 내용도 현재 화면에 보존')
    expect(target).to_be_focused()
    page.evaluate('() => { IDBDatabase.prototype.transaction = window.originalTabDraftTransaction; }')
    page.locator('.tkt-dlg .open-item-tabs [role=tab][data-item-id="DL-9001"]').click()
    expect(page.locator('.tkt-dlg .tkt-bar')).to_contain_text('DL-9001')
    assert not errors, errors


def test_document_tabs_restore_only_selected_body_and_keep_separate_ticket_list(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    state = install_documents(page)
    page.goto(base + '/#/confluence/12')
    page.locator('.conf-document > h1').wait_for()
    page.evaluate("location.hash='#/confluence/22'")
    expect(page.locator('.conf-document > h1')).to_have_text('아직 펼치지 않은 적재 설계')
    strip = page.locator('.open-item-tabs')
    expect(strip.get_by_role('tab')).to_have_count(2)
    strip.locator('[role=tab][data-item-id="12"]').click()
    expect(page.locator('.conf-document > h1')).to_have_text('적재 가이드')
    state['reads'].clear()
    page.reload()
    expect(page.locator('.conf-document > h1')).to_have_text('적재 가이드')
    expect(strip.get_by_role('tab')).to_have_count(2)
    assert {item[0] for item in state['reads']} <= {'12'}
    page.locator('nav.tabs a[href="#/mytasks"]').click()
    page.locator('.mt-card').first.wait_for()
    expect(page.locator('.open-item-tabs')).to_have_count(0)
    assert not errors, errors


def test_document_tab_and_hash_navigation_preserve_failed_then_saved_draft(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    install_documents(page)
    page.goto(base + '/#/confluence/22')
    page.locator('.conf-document > h1').wait_for()
    page.evaluate("location.hash='#/confluence/12'")
    expect(page.locator('.conf-document > h1')).to_have_text('적재 가이드')
    page.get_by_role('button', name='＋ 새 문서', exact=True).click()
    editor = page.locator('.conf-composer .ProseMirror[contenteditable=true]')
    editor.wait_for()
    page.get_by_role('textbox', name='문서 제목', exact=True).fill('탭 이동 전 보관할 문서')
    page.locator('.open-item-tabs [role=tab][data-item-id="12"]').click()
    expect(editor).to_be_visible()
    page.evaluate("""() => {
      window.originalDocumentTransaction=IDBDatabase.prototype.transaction;
      IDBDatabase.prototype.transaction=function(...args){
        const tx=window.originalDocumentTransaction.apply(this,args);
        if(args[1]==='readwrite')queueMicrotask(()=>tx.abort());return tx;
      };
    }""")
    editor.fill('저장 실패 중에는 문서와 입력을 유지')
    page.locator('.conf-composer [role=alert]').first.wait_for()
    page.evaluate("location.hash='#/confluence/22'")
    expect(page).to_have_url(re.compile(r'#/confluence/12$'))
    expect(editor).to_contain_text('저장 실패 중에는 문서와 입력을 유지')
    target = page.locator('.open-item-tabs [role=tab][data-item-id="22"]')
    target.focus(); target.press('Enter')
    expect(target).to_be_focused()
    expect(page).to_have_url(re.compile(r'#/confluence/12$'))
    page.evaluate('() => { IDBDatabase.prototype.transaction=window.originalDocumentTransaction; }')
    page.locator('.open-item-tabs [role=tab][data-item-id="22"]').click()
    expect(page.locator('.conf-document > h1')).to_have_text('아직 펼치지 않은 적재 설계')
    page.get_by_role('button', name='＋ 새 문서', exact=True).click()
    expect(editor).to_contain_text('저장 실패 중에는 문서와 입력을 유지')
    expect(page.get_by_role('textbox', name='문서 제목', exact=True)).to_have_value('탭 이동 전 보관할 문서')
    assert not errors, errors
