"""Native DC storage fixtures; cache delegation, reference permissions and safe HTML."""
from types import SimpleNamespace
from html import escape
import pytest

from app.services import confluence_macros as macros
from app.routes.confluence import document_html


def macro(name, body='', params=None, plain=False):
    values = ''.join(f'<ac:parameter ac:name="{key}">{escape(str(value))}</ac:parameter>' for key, value in (params or {}).items())
    tag = 'plain-text-body' if plain else 'rich-text-body'
    return f'<ac:structured-macro ac:name="{name}">{values}<ac:{tag}>{body}</ac:{tag}></ac:structured-macro>'


@pytest.fixture()
def renderer(monkeypatch):
    client = SimpleNamespace(s=SimpleNamespace(search_confluence_spaces=['ETL'], confluence_base='https://docs.example/wiki'))
    pages = {'1': {'id':'1', 'title':'Main', 'space':{'key':'ETL'}, 'version':{'number':1}},
             '2': {'id':'2', 'title':'Source', 'space':{'key':'ETL'}, 'version':{'number':1}}}
    sources = {'2': '<h2>Included heading</h2>' + macro('excerpt','<p>First excerpt</p>', {'hidden':'true'}) + macro('excerpt','<p>Second excerpt</p>')}
    calls = []
    def head(client, cid, **kwargs):
        calls.append(('head',cid,kwargs)); return pages[cid]
    def body(client, cid, **kwargs):
        calls.append(('body',cid,kwargs)); return {**pages[cid], 'body':{'storage':{'value':sources[cid]}}}
    monkeypatch.setattr(macros.reads,'head',head)
    monkeypatch.setattr(macros.reads,'body',body)
    monkeypatch.setattr(macros.reads,'cached_read',lambda *a, **k: {'results':[pages['2']]})
    return macros.MacroRenderer(client), pages, sources, calls


def test_native_blocks_and_sanitizer_preserve_behavior_without_scripts(renderer):
    from app.services.confluence_commands import storage_value
    from app.services.ticket_commands import CommandError
    render, pages, _, _ = renderer
    source = macro('expand', '<h2 id="nested">Nested</h2><p>Details</p>', {'title':'A < B'})
    source += ''.join(macro(name,'<p>Message</p>',{'title':name,**({'icon':'false'} if name!='panel' else {})}) for name in ['panel','info','note','tip','warning'])
    source += macro('status',params={'title':'READY','colour':'Green','subtle':'true'})
    source += macro('code','<![CDATA[select <tag>\n  x & y]]>',{'linenumbers':'true','firstline':'5'},plain=True)
    source += macro('noformat','<![CDATA[ a\n b &nbsp; <!DOCTYPE html> ]]>',plain=True) + macro('anchor',params={'':'here'})
    source += '<ac:link ac:anchor="here"><ac:plain-text-link-body>Jump</ac:plain-text-link-body></ac:link><hr/><script>evil()</script>'
    assert storage_value(source) == source
    with pytest.raises(CommandError):
        storage_value('<!DOCTYPE html><p>Not code</p>')
    html = document_html(render.render(pages['1'],source),render.client.s.confluence_base,'1',render.owners)
    assert '<details' in html and '<summary>A &lt; B</summary>' in html and 'id="nested"' in html
    assert 'conf-status-green conf-status-subtle' in html and 'READY' in html
    assert 'select &lt;tag&gt;\n  x &amp; y' in html and 'data-conf-code' in html
    assert ' b &amp;nbsp; ' in html
    assert '&lt;!DOCTYPE html&gt;' in html
    assert 'href="#conf-anchor-here"' in html and '<hr' in html and 'evil' not in html
    assert not render.warnings


def test_include_and_first_hidden_excerpt_use_shared_reads_and_latest_head(renderer):
    render,pages,sources,calls = renderer
    source = macro('include', params={'':'Source'}) + macro('excerpt-include',params={'':'Source','nopanel':'true'})
    html = render.render(pages['1'],source)
    assert html.count('Included heading') == 1 and html.count('First excerpt') == 1 and html.count('Second excerpt') == 1
    assert all(call[2]['representation'] == 'storage' for call in calls if call[0] == 'body')
    sources['2'] = '<p>NEW CONTENT</p>'
    refresh = macros.MacroRenderer(render.client,force=True)
    assert 'NEW CONTENT' in refresh.render(pages['1'],macro('include',params={'':'Source'}))
    assert ('head','2',{'force':True}) in calls


def test_unavailable_reference_cycle_and_unknown_macro_preserve_siblings(renderer,monkeypatch):
    render,pages,sources,_ = renderer
    sources['2'] = macro('include',params={'':'Source'})
    source = macro('include',params={'':'Source'}) + macro('future-plugin','<p>opaque</p>') + '<p>After</p>'
    assert 'After' in render.render(pages['1'],source) and len(render.warnings) == 2
    monkeypatch.setattr(macros.reads,'cached_read',lambda *a,**k: (_ for _ in ()).throw(AssertionError('must not query outside scope')))
    render.warnings.clear()
    html = render.render(pages['1'],macro('include',params={'':'OTHER:Secret'}) + '<p>Readable</p>')
    assert 'Readable' in html and 'Secret' not in html and '볼 수 없습니다' in html


def test_nested_image_owner_is_scoped_and_proxied(renderer):
    render,pages,sources,_ = renderer
    sources['2'] = '<ac:image><ri:attachment ri:filename="image.png"/></ac:image>'
    html = document_html(render.render(pages['1'],macro('include',params={'':'Source'})),render.client.s.confluence_base,'1',render.owners)
    assert '/api/confluence/pages/2/image?' in html
    assert 'https://docs.example' not in html


def test_dynamic_tree_toc_descriptors_and_xml_limits(renderer):
    render,pages,_,_ = renderer
    source = ''.join(macro(name, '<h2>Zone</h2>' if name == 'toc-zone' else '', {'root':'@self'} if name == 'pagetree' else {}) for name in ['toc','toc-zone','pagetree','children','attachments'])
    html = document_html(render.render(pages['1'],source),render.client.s.confluence_base,'1')
    assert html.count('data-conf-toc=') == 2 and html.count('data-conf-dynamic=') == 3
    for invalid in ['<!DOCTYPE x><p/>','<p>'*82+'</p>'*82,'x'*2000001]:
        with pytest.raises(ValueError): macros.parse_storage(invalid)
    html = document_html('<details onclick="evil()"><summary>Safe</summary><a href="javascript:evil()">x</a></details>',render.client.s.confluence_base,'1')
    assert 'onclick' not in html and 'javascript' not in html and '<summary>Safe</summary>' in html


def test_native_homepage_default_rich_links_and_noformat_setting(renderer,monkeypatch):
    render,pages,_,calls=renderer
    monkeypatch.setattr(macros.reads,'cached_read',lambda c,k,p,*a,**kw: {'homepage':{'id':'2'}} if '/space/' in p else {'results':[pages['2']]})
    html=render.render(pages['1'],macro('pagetree'))
    assert '&quot;pageId&quot;: &quot;2&quot;' in html
    source='<ac:link ac:anchor="section"><ri:page ri:content-title="Source"/><ac:link-body><strong>Custom label</strong></ac:link-body></ac:link>'
    html=render.render(pages['1'],source+macro('noformat','<![CDATA[a < b]]>',{'nopanel':'true'},plain=True))
    assert 'pageId=2#section' in html and '<strong>Custom label</strong>' in html and 'conf-no-panel' in html
