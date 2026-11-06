"""Shared UI/MCP serialization contracts, including native Jira edit HTML."""
from html import escape
import json
import pytest

from app.content.confluence_editor import editor_storage
from app.content.confluence_xml import AC, RI, parse_storage
from app.content.editor_input import normalize_editor_html


def macro(name, config, body=''):
    return '<div data-conf-macro="' + name + '" data-conf-config="' + escape(json.dumps(config), quote=True) + '">' + body + '</div>'


def test_rich_editor_elements_become_native_storage_without_losing_values():
    value = editor_storage('''<h2>Manual</h2><h4>Child</h4>
        <p>선행 <input type="checkbox" checked>완료<input type="checkbox">미완료</p>
        <ul data-type="taskList"><li data-type="taskItem" data-checked="true"><label><input type="checkbox"></label><div><p>TipTap</p></div></li></ul>
        <span data-type="mention" data-id="u01">Name</span>
        <table><colgroup><col style="width:120px"><col style="width:80px"></colgroup><tbody><tr><td colspan="2" style="background-color:#fed">Cell</td></tr></tbody></table>
        <pre><code class="language-python">x &lt; y &amp;&amp; z ]]&gt;</code></pre>
        <div class="callout callout-success"><p>Good</p></div>
        <img src="draft:one" alt="Photo" width="300"><a href="draft:two">Report</a>
        <a href="/browse/DL-1">Ticket</a><script>bad()</script>''',
        assets=[{'token':'draft:one', 'filename':'one.png'}, {'token':'draft:two','filename':'two.pdf'}],
        users={'u01':'user-key'}, numbered=True, jira_base='https://jira.test')
    root = parse_storage(value)
    assert [x.text for x in root.iter(AC+'task-status')] == ['complete','incomplete','complete']
    assert len({x.text for x in root.iter(AC+'task-id')}) == 3
    assert [x.text for x in root.iter('h2')] == ['1 Manual']
    assert root.find('h4').text == '1.1 Child'
    assert root.find('.//'+RI+'user').get(RI+'userkey') == 'user-key'
    assert [x.get(RI+'filename') for x in root.iter(RI+'attachment')] == ['one.png','two.pdf']
    assert len(root.findall('.//col')) == 2 and root.find('.//td').text == 'Cell'
    assert 'border:1px solid' in root.find('.//td').get('style')
    assert root.find('.//'+AC+'plain-text-body').text == 'x < y && z ]]>'
    assert ']]]]><![CDATA[>' in value and 'bad()' not in value
    assert 'https://jira.test/browse/DL-1' in value


def test_edit_preserves_unknown_macros_and_ids_but_updates_known_parameters():
    raw = '<ac:task-list><ac:task><ac:task-id>9</ac:task-id><ac:task-status>complete</ac:task-status><ac:task-body>Old</ac:task-body></ac:task></ac:task-list>'
    known = '<ac:structured-macro ac:name="expand" ac:macro-id="stable"><ac:parameter ac:name="title">Old</ac:parameter><ac:rich-text-body><p>Old</p></ac:rich-text-body></ac:structured-macro>'
    value = editor_storage(macro('unknown', {'raw':raw}) + macro('expand', {'raw':known,'params':{'title':'New'}},
        '<div class="conf-macro-content"><p>New body</p></div>') + '<p><input type="checkbox">New task</p>')
    assert raw in value and 'Old</p>' not in value
    root = parse_storage(value)
    assert [x.text for x in root.iter(AC+'task-id')] == ['9','10']
    expanded = root.find(AC+'structured-macro')
    assert expanded.get(AC+'macro-id') == 'stable'
    assert expanded.find(AC+'parameter').text == 'New'
    assert expanded.find('.//p').text == 'New body'


def test_cleared_optional_page_reference_is_removed():
    raw = '<ac:structured-macro ac:name="pagetree"><ac:parameter ac:name="root">Old</ac:parameter></ac:structured-macro>'
    value = editor_storage(macro('pagetree', {'raw':raw, 'params':{'root':''}}))
    assert not list(parse_storage(value).iter(AC+'parameter'))


@pytest.mark.parametrize('value', ['<img src="blob:missing">', '<a href="draft:missing">File</a>',
    '<span data-type="mention" data-id="unknown">Name</span>', '<ac:structured-macro ac:name="toc" />',
    macro('include', {'params':{'':'  '}})])
def test_unresolved_references_fail_before_any_write(value):
    with pytest.raises(ValueError):
        editor_storage(value)


def test_jira_submit_normalization_is_idempotent_and_keeps_external_references():
    source = '<p><a href="http://127.0.0.1:4457/browse/DL-1">Ticket</a><a href="https://other.test/browse/DL-2">Other</a><code>&lt;img src="blob:example"&gt;</code></p>'
    result = normalize_editor_html(source, 'https://jira.test')
    assert 'https://jira.test/browse/DL-1' in result
    assert 'https://other.test/browse/DL-2' in result
    assert '&lt;img src="blob:example"&gt;' in result
    assert normalize_editor_html(result, 'https://jira.test') == result
    with pytest.raises(ValueError, match='업로드'):
        normalize_editor_html('<img src="blob:missing">')
