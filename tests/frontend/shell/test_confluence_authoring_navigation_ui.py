"""Live editor outline and explicit parent creation; all writes use disposable mock."""
from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401
from frontend.shell.test_confluence_authoring_ui import open_composer


def test_live_editor_outline_tracks_edits_scroll_and_responsive_navigation(editor_browser):
    page,base,errors,_=editor_browser
    editor=open_composer(page,base)
    toc=page.get_by_role('navigation',name='본문 목차',exact=True)
    toc.get_by_text('본문에 헤딩을 추가하면 여기에 표시됩니다.',exact=True).wait_for()
    reads=[]
    page.on('request',lambda req: reads.append(req.url) if '/api/confluence/' in req.url else None)
    editor.click()
    page.keyboard.type('# Overview'); page.keyboard.press('Enter')
    for i in range(16):
        page.keyboard.insert_text('Outline content '+str(i)); page.keyboard.press('Enter')
    page.keyboard.type('## Details'); page.keyboard.press('Enter')
    page.keyboard.insert_text('Details content')
    toc.get_by_role('button',name='Overview',exact=True).wait_for()
    toc.get_by_role('button',name='Details',exact=True).click()
    page.wait_for_function("document.querySelector('.conf-composer .ProseMirror').editor.state.selection.$from.parent.textContent==='Details'")
    page.wait_for_function("document.querySelector('.conf-toc [aria-current=location]')?.textContent==='Details'")
    page.keyboard.press('End'); page.keyboard.insert_text(' updated')
    toc.get_by_role('button',name='Details updated',exact=True).wait_for()
    assert toc.get_by_role('button',name='Details',exact=True).count() == 0
    editor.hover(); page.mouse.wheel(0,-10000)
    page.wait_for_function("document.querySelector('.conf-toc [aria-current=location]')?.textContent==='Overview'")
    toc.get_by_role('button',name='Overview',exact=True).click()
    page.wait_for_function("document.querySelector('.conf-composer .ProseMirror').editor.state.selection.$from.parent.textContent==='Overview'")
    assert editor.locator('h1').evaluate("el=>{const host=el.closest('.cmt-ed-host').getBoundingClientRect(),box=el.getBoundingClientRect();return box.top>=host.top-1 && box.bottom<=host.bottom}")
    separator=page.get_by_role('separator',name='목차 너비 조절',exact=True)
    before=page.locator('.conf-toc').bounding_box()['width']; separator.press('ArrowLeft')
    assert page.locator('.conf-toc').bounding_box()['width'] > before
    page.get_by_role('button',name='목차 접기',exact=True).click()
    toc.wait_for(state='hidden')
    page.get_by_role('button',name='목차',exact=True).click(); toc.wait_for()
    assert not reads  # local outline changes never request document data
    page.set_viewport_size({'width':700,'height':900})
    page.get_by_role('button',name='목차',exact=True).click()
    page.get_by_role('dialog',name='이 문서의 목차',exact=True).wait_for()
    toc.get_by_role('button',name='Details updated',exact=True).click()
    toc.wait_for(state='hidden')
    page.wait_for_function("document.querySelector('.conf-composer .ProseMirror').editor.state.selection.$from.parent.textContent==='Details updated'")
    page.wait_for_function("document.activeElement===document.querySelector('.conf-composer .ProseMirror')")
    page.keyboard.press('Control+Alt+3')
    editor.locator('h3',has_text='Details updated').wait_for()
    assert page.locator('.conf-workspace').evaluate('el=>el.scrollWidth<=el.clientWidth+2')
    page.get_by_role('button',name='닫기 · 초안 보관',exact=True).click()
    page.get_by_role('button',name='＋ 새 문서',exact=True).click()
    page.get_by_role('button',name='목차',exact=True).click()
    toc.get_by_role('button',name='Details updated',exact=True).wait_for()
    assert toc.get_by_role('button',name='Details updated',exact=True).evaluate("el=>el.style.getPropertyValue('--level')") == '2'
    toc.get_by_role('button',name='Details updated',exact=True).click()
    page.wait_for_function("document.querySelector('.conf-composer .ProseMirror').editor.state.selection.$from.parent.textContent==='Details updated'")
    page.wait_for_function("document.activeElement===document.querySelector('.conf-composer .ProseMirror')")
    page.keyboard.press('Control+Alt+0')
    editor.locator('p',has_text='Details updated').wait_for()
    page.get_by_role('button',name='목차',exact=True).click()
    assert toc.get_by_role('button',name='Details updated',exact=True).count() == 0
    assert not errors


def test_tree_context_creation_uses_clicked_parent_and_preserves_other_draft(editor_browser):
    page,base,errors,_=editor_browser
    editor=open_composer(page,base)
    page.get_by_role('textbox',name='문서 제목',exact=True).fill('Other draft')
    editor.fill('Keep the original document draft')
    page.get_by_role('button',name='닫기 · 초안 보관',exact=True).click()
    tree=page.get_by_role('navigation',name='문서 트리',exact=True)
    target=tree.get_by_role('button',name='[UI 검증] 페이지네이션 · 운영 문서 55개',exact=True)
    target.click(button='right')
    menu=page.get_by_role('menu',name='문서 메뉴',exact=True)
    menu.wait_for(); page.keyboard.press('Escape'); menu.wait_for(state='hidden')
    target.press('Shift+F10'); menu.wait_for()
    page.get_by_role('heading',name='[UI 검증] Confluence 운영 가이드',exact=True).click()
    menu.wait_for(state='hidden')
    page.set_viewport_size({'width':700,'height':900})
    page.get_by_role('button',name='문서 탐색',exact=True).click()
    page.get_by_role('dialog',name='문서 탐색',exact=True).wait_for()
    target.click(button='right')
    menu.get_by_role('menuitem',name='＋ 하위 문서 만들기',exact=True).click()
    editor=page.locator('.conf-composer .ProseMirror'); editor.wait_for()
    assert page.get_by_label('새 문서 위치',exact=True).input_value() == '890100000010'
    assert page.get_by_role('textbox',name='문서 제목',exact=True).input_value() == ''
    assert editor.inner_text().strip() == ''
    page.get_by_role('textbox',name='문서 제목',exact=True).fill('Context child')
    editor.fill('Child body')
    creates=[]
    page.on('request',lambda req: creates.append(req.post_data_json) if req.method=='POST' and req.url==base+'/api/confluence/pages' else None)
    page.get_by_role('button',name='발행',exact=True).click()
    page.locator('.conf-document > h1').filter(has_text='Context child').wait_for()
    assert len(creates)==1 and creates[0]['parent_id']=='890100000010'
    page.locator('.conf-breadcrumb').get_by_role('button',name='[UI 검증] 페이지네이션 · 운영 문서 55개',exact=True).wait_for()
    page.get_by_role('button',name='＋ 새 문서',exact=True).click()
    page.locator('.conf-composer .ProseMirror').get_by_text('Keep the original document draft',exact=True).wait_for()
    assert page.get_by_role('textbox',name='문서 제목',exact=True).input_value()=='Other draft'
    assert not errors
