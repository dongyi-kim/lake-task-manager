"""Exercise real macro menus and native-storage edit/publish/read round trips."""
import re
import uuid
import xml.etree.ElementTree as ET

from frontend.editor.test_editor_roundtrip_ui import editor_browser, PNG_1X1  # noqa: F401
from frontend.shell.test_confluence_authoring_ui import open_composer
from routes.test_confluence_macros import macro
from app.mock.confluence_world import PAGINATION_TITLE

AC = '{http://atlassian.com/content}'


def create(page, base, title, body, parent=None):
    response = page.request.post(base+'/api/confluence/pages', headers={'Origin':base,'X-LTM-Confluence':'1'},
        data={'space':'DL','title':title,'body':body,'parent_id':parent,'mutation_id':str(uuid.uuid4())})
    assert response.ok, response.text()
    return response.json()['id']


def stored(page, base, cid):
    response = page.request.get(base+f'/api/confluence/pages/{cid}/source')
    assert response.ok, response.text()
    return response.json()


def tree(storage):
    return ET.fromstring('<root xmlns:ac="http://atlassian.com/content" xmlns:ri="http://atlassian.com/resource/identifier">'+storage+'</root>')


def test_every_native_macro_slash_command_opens_settings_and_inserts(editor_browser):
    page,base,errors,_ = editor_browser
    editor = open_composer(page,base)
    names = ['expand','panel','info','note','tip','warning','excerpt','include','excerpt-include','pagetree','children','toc','toc-zone','attachments','status','anchor','noformat','code']
    for name in names:
        editor.click(); page.keyboard.press('Control+A'); page.keyboard.press('Control+A'); page.keyboard.press('Backspace')
        page.keyboard.insert_text('/'+name)
        page.locator('.sl-item').filter(has=page.locator('.sl-k',has_text=re.compile('^/'+name+'$'))).click()
        dialog = page.get_by_role('dialog',name='Confluence 매크로 설정')
        dialog.wait_for()
        if name in ('include','excerpt-include'):
            dialog.get_by_placeholder('문서 제목 검색').fill('운영 가이드')
            dialog.locator('.conf-macro-result').first.click()
        dialog.get_by_role('button',name='삽입',exact=True).click()
        editor.locator('[data-conf-macro="'+name+'"]').wait_for()
        editor.locator('[data-conf-macro="'+name+'"] > .conf-macro-settings').click()
        dialog.get_by_role('button',name='적용',exact=True).click()
        assert not page.locator('.conf-macro-dialog').count()
    editor.fill('/hr')
    page.locator('.sl-item').filter(has=page.locator('.sl-k',has_text='/hr')).click()
    assert editor.locator('hr').count() == 1
    assert not errors


def test_native_storage_edit_publish_render_preserves_macros_and_attachments(editor_browser):
    page,base,errors,_ = editor_browser
    open_composer(page,base)
    page.get_by_role('button',name='초안 삭제',exact=True).click()
    source_id = create(page,base,'Macro source',macro('excerpt','<p>Excerpt payload</p>',{'hidden':'true'})+'<p>Included text</p>')
    source = '<h1 id="intro">Guide</h1>'+macro('toc')+macro('expand','<h2>Expanded section</h2><p>Editable content</p>',{'title':'Details'})
    source += '<p><strong>Native bold</strong> <em>Native italic</em> <code>Native inline</code></p><p style="text-align:right"><span style="color:#dc2626;background-color:#fef08a">Native color</span></p>'
    source += '<table><tbody><tr><th>Native header</th></tr><tr><td>Native cell</td></tr></tbody></table>'
    source += '<p><ac:link ac:anchor="checkpoint"><ac:link-body><strong>Native anchor label</strong></ac:link-body></ac:link></p>'
    source += '<h2><ac:link ac:anchor="checkpoint"><ac:plain-text-link-body><![CDATA[Linked heading]]></ac:plain-text-link-body></ac:link></h2><h2><a href="https://example.com">Badge heading</a></h2>'
    source += ''.join(macro(name,'<p>'+name+' content</p>',{'title':name}) for name in ['panel','info','note','tip','warning','excerpt','toc-zone'])
    source += macro('status',params={'title':'READY','colour':'Green','subtle':'true'})+macro('anchor',params={'':'checkpoint'})
    source += macro('include',params={'':'Macro source'})+macro('excerpt-include',params={'':'Macro source'})
    source += ''.join(macro(name,params={'root':'@self'} if name=='pagetree' else {}) for name in ['pagetree','children','attachments'])
    source += macro('code','<![CDATA[select <tag>\n  x & y;]]>',{'language':'sql','linenumbers':'true','firstline':'8'},plain=True)
    source += macro('noformat','<![CDATA[  untouched &nbsp; <!DOCTYPE html>\nspacing ]]]]><![CDATA[> preserved]]>',plain=True)
    source += '<ac:task-list><ac:task><ac:task-id>42</ac:task-id><ac:task-status>complete</ac:task-status><ac:task-body>Existing task</ac:task-body></ac:task></ac:task-list>'
    source += '<ac:structured-macro ac:name="third-party" ac:macro-id="keep-id"><ac:parameter ac:name="future"><ri:page ri:content-title="Future"/></ac:parameter><ac:rich-text-body><p>KEEP OPAQUE</p></ac:rich-text-body></ac:structured-macro>'
    source += '<p><ac:image><ri:attachment ri:filename="pixel.png"/></ac:image></p><hr/><p>After</p>'
    cid = create(page,base,'Macro roundtrip',source)
    response=page.request.post(base+f'/api/confluence/pages/{cid}/attachments',headers={'Origin':base,'X-LTM-Confluence':'1'},multipart={'mutation_id':str(uuid.uuid4()),'file':{'name':'pixel.png','mimeType':'image/png','buffer':PNG_1X1}})
    assert response.ok,response.text()
    create(page,base,'Macro child','<p>Child</p>',cid)
    page.goto(base+'/#/confluence/'+cid)
    body=page.locator('.conf-document-body')
    body.locator('summary',has_text='Details').click()
    body.get_by_text('Editable content',exact=True).wait_for()
    body.get_by_text('Included text',exact=True).wait_for()
    body.locator('.conf-macro-tree-row a',has_text='Macro child').first.wait_for()
    body.locator('a',has_text='pixel.png').wait_for()
    assert body.locator('.conf-inline-toc a').count() >= 2
    assert body.locator('.conf-code-lines').inner_text().startswith('8\n9')
    body.locator('img').evaluate('el => el.decode()')
    assert body.locator('table td').inner_text() == 'Native cell'
    assert body.locator('a[href="#conf-anchor-checkpoint"] strong').inner_text() == 'Native anchor label'
    page.get_by_role('button',name='수정',exact=True).click()
    editor=page.locator('.conf-composer .ProseMirror')
    editor.locator('table td').wait_for()
    page.get_by_role('navigation',name='본문 목차',exact=True).get_by_role('button',name='Expanded section',exact=True).click()
    page.wait_for_function("document.querySelector('.conf-composer .ProseMirror').editor.state.selection.$from.parent.textContent==='Expanded section'")
    toc=page.get_by_role('navigation',name='본문 목차',exact=True)
    assert toc.get_by_role('button',name='Linked heading',exact=True).count() == 1
    assert toc.get_by_role('button',name='Badge heading',exact=True).count() == 1
    assert editor.locator('strong').filter(has_text='Native bold').count() == 1
    editor.locator('table td').fill('Native cell edited')
    editor.locator('[data-conf-macro="expand"] .conf-macro-settings').click()
    dialog=page.get_by_role('dialog',name='Confluence 매크로 설정')
    dialog.get_by_label('제목',exact=True).fill('Updated details')
    dialog.get_by_role('button',name='적용',exact=True).click()
    editor.locator('[data-conf-macro="resource:image"] img').evaluate('el => el.decode()')
    editor.get_by_text('After',exact=True).click()
    assert page.evaluate('getSelection().anchorNode.textContent') == 'After'
    page.wait_for_function("document.querySelector('.conf-composer .ProseMirror').editor.state.selection.$from.parent.textContent === 'After'")
    page.keyboard.press('End'); page.keyboard.press('Enter'); page.keyboard.insert_text('/checkbox')
    assert editor.locator('h1').inner_text() == 'Guide', editor.inner_html()
    page.locator('.sl-item').filter(has=page.locator('.sl-k',has_text='/checkbox')).click()
    page.keyboard.insert_text('New task')
    page.get_by_role('button',name='발행',exact=True).click()
    page.wait_for_function("() => document.querySelector('.cmt-ed-msg.solo') || [...document.querySelectorAll('.conf-document-body summary')].some(n=>n.textContent.includes('Updated details'))")
    assert not page.locator('.cmt-ed-msg.solo').count(), page.locator('.cmt-ed-msg.solo').all_text_contents()
    page.locator('.conf-document-body summary',has_text='Updated details').wait_for(timeout=30000)
    updated=stored(page,base,cid)
    assert updated['version']['number']==2
    root=tree(updated['storage']); native=list(root.iter(AC+'structured-macro'))
    assert len(native)==19
    unknown=next(node for node in native if node.get(AC+'name')=='third-party')
    assert unknown.get(AC+'macro-id')=='keep-id' and 'KEEP OPAQUE' in ''.join(unknown.itertext())
    code=next(node for node in native if node.get(AC+'name')=='code')
    assert code.findtext(AC+'plain-text-body')=='select <tag>\n  x & y;'
    assert '<![CDATA[select <tag>' in updated['storage']
    assert root.find('h1') is not None, updated['storage']
    assert root.find('h1').get('id') == 'intro'
    assert [item.text for item in root.iter(AC+'task-id')] == ['42','43']
    noformat=next(node for node in native if node.get(AC+'name')=='noformat')
    assert noformat.findtext(AC+'plain-text-body') == '  untouched &nbsp; <!DOCTYPE html>\nspacing ]]> preserved'
    assert page.locator('.conf-document-body img').evaluate('el=>el.complete && el.naturalWidth>0')
    assert page.locator('.conf-document-body table td').inner_text() == 'Native cell edited'
    page.get_by_role('button',name='수정',exact=True).click()
    page.wait_for_function("() => document.querySelector('.conf-composer .ProseMirror') || document.querySelector('.conf-composer .conf-error')")
    assert not page.locator('.conf-composer .conf-error').count(), page.locator('.conf-composer').inner_text() + '\n' + updated['storage']
    assert page.locator('.conf-composer .ProseMirror [data-conf-macro="expand"]').count(), page.locator('.conf-composer .ProseMirror').inner_html() + '\nSOURCE: ' + updated['storage']
    page.locator('.conf-composer .ProseMirror [data-conf-macro="expand"]').wait_for()
    page.get_by_role('button',name='발행',exact=True).click()
    page.locator('.conf-document-body summary',has_text='Updated details').wait_for()
    assert len(list(tree(stored(page,base,cid)['storage']).iter(AC+'structured-macro')))==19
    assert page.locator('.conf-document-body table td').inner_text() == 'Native cell edited'
    assert page.locator('.conf-document-body a[href="#conf-anchor-checkpoint"] strong').inner_text() == 'Native anchor label'
    assert page.locator('.conf-document-body').get_by_text('Native color',exact=True).evaluate('el=>getComputedStyle(el).color') == 'rgb(220, 38, 38)'
    assert not errors


def test_concurrent_revision_rejects_editor_overwrite_and_preserves_draft(editor_browser):
    page,base,errors,_=editor_browser
    open_composer(page,base)
    page.get_by_role('button',name='초안 삭제',exact=True).click()
    cid=create(page,base,'Concurrent macro',macro('expand','<p>Original</p>'))
    page.goto(base+'/#/confluence/'+cid)
    page.get_by_role('button',name='수정',exact=True).click()
    editor=page.locator('.conf-composer .ProseMirror'); editor.wait_for()
    latest=page.request.put(base+f'/api/confluence/pages/{cid}',headers={'Origin':base,'X-LTM-Confluence':'1'},data={'title':'Concurrent macro','body':'<p>Remote edit</p>','expected_version':1,'mutation_id':str(uuid.uuid4())})
    assert latest.ok,latest.text()
    page.get_by_role('button',name='발행',exact=True).click()
    page.locator('.cmt-ed-msg.solo').wait_for()
    assert 'Remote edit' in stored(page,base,cid)['storage']
    assert editor.locator('[data-conf-macro="expand"]').count()==1
    assert all('409' in error for error in errors)


def test_restored_edit_draft_keeps_its_revision_and_cannot_overwrite_newer_page(editor_browser):
    page,base,errors,_=editor_browser
    open_composer(page,base); page.get_by_role('button',name='초안 삭제',exact=True).click()
    cid=create(page,base,'Draft revision','<p>Original</p>')
    page.goto(base+'/#/confluence/'+cid); page.get_by_role('button',name='수정',exact=True).click()
    page.locator('.conf-composer .ProseMirror').fill('My unsaved draft')
    page.get_by_role('button',name='닫기 · 초안 보관',exact=True).click()
    response=page.request.put(base+f'/api/confluence/pages/{cid}',headers={'Origin':base,'X-LTM-Confluence':'1'},data={'title':'Draft revision','body':'<p>Remote revision</p>','expected_version':1,'mutation_id':str(uuid.uuid4())})
    assert response.ok
    page.get_by_role('button',name='수정',exact=True).click()
    page.locator('.conf-composer .ProseMirror').get_by_text('My unsaved draft').wait_for()
    assert page.get_by_role('button',name='발행',exact=True).is_disabled()
    assert '서버 문서가 변경' in page.locator('.conf-composer [role="alert"]').inner_text()
    assert stored(page,base,cid)['storage']=='<p>Remote revision</p>' and not errors


def test_tree_pagination_retry_unloaded_search_and_include_refresh(editor_browser):
    page,base,errors,_=editor_browser
    open_composer(page,base); page.get_by_role('button',name='초안 삭제',exact=True).click()
    src=create(page,base,'Live source','<p>Before source update</p>')
    cid=create(page,base,'Dynamic macros',macro('include',params={'':'Live source'})+macro('pagetree',params={'root':PAGINATION_TITLE,'searchBox':'true','expandCollapseAll':'true'}))
    child=create(page,base,'Unloaded needle','<p>Nested</p>','890100001055')
    failed=[]
    def fail_second(route):
        if 'parent=890100000010' in route.request.url and 'start=50' in route.request.url and not failed:
            failed.append(True); route.fulfill(status=503,json={'error':'controlled retry','service':'confluence'})
        else: route.continue_()
    page.route('**/api/confluence/pages?**',fail_second)
    page.goto(base+'/#/confluence/'+cid)
    widget=page.locator('.conf-macro-tree')
    widget.get_by_role('button',name='다시 시도',exact=True).click()
    widget.locator('a',has_text='운영 문서 55').wait_for()
    assert widget.locator('.conf-macro-tree-row').count()==55
    widget.get_by_label('트리 문서 검색').fill('Unloaded needle')
    widget.locator('a',has_text='Unloaded needle').wait_for()
    widget.get_by_label('트리 문서 검색').fill('')
    page.request.put(base+f'/api/confluence/pages/{src}',headers={'Origin':base,'X-LTM-Confluence':'1'},data={'title':'Live source','body':'<p>After source update</p>','expected_version':1,'mutation_id':str(uuid.uuid4())})
    page.get_by_role('button',name='최신 여부 확인',exact=True).click()
    page.locator('.conf-document-body').get_by_text('After source update',exact=True).wait_for()
    assert 'v1' in page.locator('.conf-document-meta').inner_text()
    page.get_by_role('link',name='Task',exact=True).click()
    page.locator('.conf-macro-tree').wait_for(state='hidden')
    assert all('503' in error for error in errors)
