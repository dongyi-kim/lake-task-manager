"""Pseudo-source cleanup preserves literal Markdown source bytes."""
from langchain_core.messages import HumanMessage
import pytest

from app.agent.workflow.agents import result_integrator as result


@pytest.mark.parametrize("marker", ["```", "~~~", "````", "~~~~"])
@pytest.mark.parametrize("indent", ["", " ", "   "])
@pytest.mark.parametrize("closed", [True, False])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_source_rows_preserves_fence_bytes(marker, indent, closed, newline):
    block = newline.join([indent + marker + "text", "검토합니다.", "print(message)",
                          "", "", "[1] 사용자 입력", "- [1-a] 첨부 발췌", "### 근거"])
    if closed:
        block += newline + indent + marker + newline
    assert result._drop_direct_input_source_rows(block) == block


@pytest.mark.parametrize("block", [
    "```text\n### 근거\n\n### 참조\n```",
    "~~~text\n[1] [사용자 입력](verified URL)\n- [1-a] 원문\n~~~",
    "````text\n```\n[1] 사용자 입력\n- 원문\n````",
    "~~~text\n~~~not-a-close\n[1] 사용자 입력\n~~~",
    "    [1] 사용자 입력\n    - [1-a] 원문\n\n\n",
    "\t[1] 사용자 입력\n\t- [1-a] 원문\n",
    "[1] `사용자 입력`\n",
])
def test_source_like_literal_rows_are_opaque(block):
    assert result._drop_direct_input_source_rows(block) == block


@pytest.mark.parametrize("pseudo", [
    "[1] 사용자 입력", "[1] 첨부 문서 발췌", "[1] [사용자 입력](verified URL)",
    "[1] [followup-notes.docx](verified URL)",
])
def test_same_source_rows_outside_literal_are_removed(pseudo):
    text = ("### 결론\n\n검토 필요\n\n### 근거\n\n" + pseudo
            + "\n- [1-a] 표본 확인\n\n[2] [공식 문서](https://example.com/spec)")
    got = result._drop_direct_input_source_rows(text)
    assert pseudo not in got and "표본 확인" not in got
    assert got == "### 결론\n\n검토 필요\n\n### 근거\n\n[2] [공식 문서](https://example.com/spec)"


@pytest.mark.parametrize("heading", ["근거", "참조"])
def test_empty_pseudo_source_heading_is_removed_only_outside_literal(heading):
    plain = "### 결론\n\n검토 필요\n\n### " + heading + "\n\n[1] 사용자 입력\n- 원문"
    assert result._drop_direct_input_source_rows(plain) == "### 결론\n\n검토 필요"
    block = "~~~text\n### " + heading + "\n\n[1] 사용자 입력\n- 원문\n~~~"
    assert result._drop_direct_input_source_rows(block) == block


def test_literal_interrupts_source_children_without_suppressing_later_prose():
    block = "~~~text\n[1] 사용자 입력\n- [1-a] 원문\n\n\n~~~\n"
    text = "[1] 사용자 입력\n- 지워질 행\n" + block + "- 유지할 행\n[2] 첨부 문서 발췌\n- 지워질 행"
    assert result._drop_direct_input_source_rows(text) == block + "- 유지할 행"


def test_heading_before_literal_still_has_content():
    block = "~~~text\n[1] 사용자 입력\n~~~"
    assert result._drop_direct_input_source_rows("### 근거\n\n" + block) == "### 근거\n\n" + block


def test_ordinary_prose_blank_line_cleanup_is_unchanged():
    assert result._drop_direct_input_source_rows("  검토 필요\n\n\n추가 확인\n ") == "검토 필요\n\n추가 확인"


@pytest.mark.parametrize("literal_child", ["- [1-a] `field_name`", "    - [1-a] literal sample"])
def test_literal_child_does_not_release_remaining_pseudo_source_children(literal_child):
    value = "[1] 사용자 입력\n" + literal_child + "\n- [1-b] 이 행은 계속 삭제 대상\n[2] https://example.com/spec"
    assert result._drop_direct_input_source_rows(value) == literal_child + "\n[2] https://example.com/spec"


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('literal', [
    '   ~~~~text\n keep  α  \n\n\n   ~~~~~  ',
    '   ````text\n keep  α  \n~~~\n keep  β  ',
    '\tkeep  ω  \n\tkeep  e\u0301  ',
    'prefix ``literal\n### original``',
])
@pytest.mark.parametrize('edges', [('\n', ''), ('', '\n\n'), ('\n\n\n', '\n\n\n')])
def test_literal_noop_keeps_original_outer_separators(newline, literal, edges):
    raw = (edges[0] + literal + edges[1]).replace('\n', newline)
    assert result._drop_direct_input_source_rows(raw) == raw


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('raw', [
    '   ~~~~text\n keep  α  \n\n\n   ~~~~~  \n\n',
    '\n   ````text\n keep  α  \n~~~\n keep  β  ',
])
def test_noop_outer_separators_survive_ordinary_apply(newline, raw):
    raw = raw.replace('\n', newline)
    state = {'intent': 'ask', 'request_text': '검토 요청',
             'messages': [HumanMessage(content='검토 요청')]}
    assert result.ResultIntegrator().apply_routed(state, {'text': raw})['reply'] == raw


@pytest.mark.parametrize('source', ['[1] 사용자 입력', '[1] [followup-notes.docx](verified URL)'])
def test_real_source_deletion_still_runs_with_literal_present(source):
    literal = '~~~text\n[1] 사용자 입력\n~~~\n'
    raw = source + '\n- removed child\n' + literal + '\n### 근거\n'
    assert result._drop_direct_input_source_rows(raw) == literal


def test_real_empty_heading_removal_still_runs_with_literal_present():
    literal = '~~~text\nkeep  bytes\n~~~\n'
    assert result._drop_direct_input_source_rows(literal + '\n### 참조\n') == literal


def test_actual_prose_blankline_normalization_still_executes_next_to_literal():
    literal = '~~~text\n keep  bytes  \n~~~\n'
    raw = '  검토 필요\n\n\n추가 확인\n\n' + literal
    assert result._drop_direct_input_source_rows(raw) == '검토 필요\n\n추가 확인\n\n' + literal


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('title,url', [
    ('specification.pdf', 'https://example.test/specification.pdf'),
    ('release-notes.docx', 'https://example.test/release-notes.docx'),
    ('문서', 'https://wiki.example.test/spaces/ACME/pages/1'),
    ('메모', '/spaces/ACME/pages/2'),
    ('사용자 입력', 'https://example.test/user-input'),
])
def test_linked_document_row_is_not_classified_from_its_title(newline, title, url):
    raw = newline.join(['### 근거', f'[1] [{title}]({url})', '- [1-a] 실제 source'])
    assert result._is_direct_input_pseudo_source({'title': title, 'url': url}) is False
    # Prose newline normalization is the existing owner contract, separate from identity.
    assert result._drop_direct_input_source_rows(raw) == raw.replace('\r\n', '\n')


@pytest.mark.parametrize('pseudo', [
    '사용자 입력', '첨부 문서 발췌', 'specification.pdf', 'notes.docx',
    '[사용자 입력](verified URL)', '[specification.pdf](verified URL)',
    '[release-notes.docx]( VeRiFiEd UrL )', '[문서](verified URL)',
])
def test_unlinked_or_placeholder_document_still_loses_its_pseudo_children(pseudo):
    raw = '### 근거\n[1] ' + pseudo + '\n- [1-a] 사용자 입력 child\n\n'
    assert result._drop_direct_input_source_rows(raw) == ''


@pytest.mark.parametrize('pseudo', ['사용자 입력', 'notes.docx', '[notes.docx](verified URL)'])
@pytest.mark.parametrize('title,url', [
    ('specification.pdf', 'https://example.test/specification.pdf'),
    ('문서', '/spaces/ACME/pages/1'),
])
def test_real_linked_source_resets_pseudo_child_deletion(pseudo, title, url):
    real = f'[2] [{title}]({url})\n- [2-a] 실제 source child'
    raw = '### 근거\n[1] ' + pseudo + '\n- [1-a] 삭제 대상\n' + real
    assert result._drop_direct_input_source_rows(raw) == '### 근거\n' + real


@pytest.mark.parametrize('literal', [
    '~~~text\n[1] [specification.pdf](verified URL)\n- literal child\n~~~\n',
    '```text\n[1] 사용자 입력\n[2] [specification.pdf](https://example.test/specification.pdf)\n```\n',
    '    [1] [specification.pdf](verified URL)\n    - literal child\n',
    '[1] `사용자 입력`\n',
])
def test_source_classification_does_not_open_code_literals(literal):
    assert result._drop_direct_input_source_rows(literal) == literal


def test_pseudo_deletion_remains_active_between_real_source_and_literal():
    real = '[1] [specification.pdf](https://example.test/specification.pdf)\n- [1-a] 실제 child'
    literal = '~~~text\n[3] [notes.docx](verified URL)\n- literal child\n~~~\n'
    raw = '### 근거\n' + real + '\n[2] 사용자 입력\n- [2-a] 삭제 대상\n' + literal
    assert result._drop_direct_input_source_rows(raw) == '### 근거\n' + real + '\n' + literal


@pytest.mark.parametrize('title', ['specification.pdf', '문서'])
def test_materialized_document_link_survives_ordinary_result_apply(title):
    import copy
    url = 'https://wiki.example.internal/spaces/ACME/pages/9/specification.pdf'
    state = {'intent': 'ask', 'request_text': '자료 요약', 'messages': [HumanMessage(content='자료 요약')],
             'evidence': [{'title': title, 'url': url, 'why': '원문에서 확인한 설정',
                           'observations': [{'text': '검토 설정 기록', 'source': 'document'}]}],
             'query_results': [{'id': 'document', 'source': 'confluence', 'result': {
                 'projectedDocumentBodies': [{'title': title, 'url': url, 'text': '검토 설정 기록'}]}}],
             'related_docs': [{'title': title, 'url': url}]}
    payload = {'text': '검토 설정 기록 [1]\n\n### 근거\n[1] [' + title + '](' + url + ')\n- [1-a] 검토 설정 기록'}
    before = copy.deepcopy((state, payload))
    output = result.ResultIntegrator().apply_routed(state, payload)
    assert (state, payload) == before
    assert url in output['reply'] and '검토 설정 기록' in output['reply']
    assert '(근거 확인 필요)' not in output['reply']


@pytest.mark.parametrize('title', ['specification.pdf', '문서'])
def test_source_row_survival_does_not_authorize_an_unacquired_external_link(title):
    url = 'https://example.test/specification.pdf'
    state = {'intent': 'ask', 'request_text': '자료 요약', 'messages': [HumanMessage(content='자료 요약')],
             'evidence': [{'title': title, 'url': url, 'why': '원문에서 확인한 설정',
                           'observations': [{'text': '검토 설정 기록', 'source': 'document'}]}]}
    payload = {'text': '검토 설정 기록 [1]\n\n### 근거\n[1] [' + title + '](' + url + ')\n- [1-a] 검토 설정 기록'}
    assert url not in result.ResultIntegrator().apply_routed(state, payload)['reply']


@pytest.mark.parametrize('destination', ['#', 'URL', '<URL>', 'not a URL'])
def test_non_url_placeholder_is_still_a_pseudo_source(destination, record_property):
    import json
    raw = '### 근거\n[1] [문서](' + destination + ')\n- [1-a] 사용자 제공 기록'
    got = result._drop_direct_input_source_rows(raw)
    record_property('observation', json.dumps({'input': raw, 'output': got}, ensure_ascii=False))
    assert got == ''


@pytest.mark.parametrize('destination', ['#', 'URL', '<URL>', 'not a URL'])
def test_pasted_input_does_not_become_numbered_placeholder_source(destination, record_property):
    import json
    request = '붙여넣은 문서 요약: 배치 검토 기록'
    state = {'intent': 'ask', 'request_text': request, 'messages': [HumanMessage(content=request)]}
    raw = '배치 검토 기록 [1]\n\n### 근거\n[1] [문서](' + destination + ')\n- [1-a] 배치 검토 기록'
    reply = result.ResultIntegrator().apply_routed(state, {'text': raw})['reply']
    record_property('observation', json.dumps({'input': raw, 'output': reply}, ensure_ascii=False))
    assert '[문서](' + destination + ')' not in reply
    assert '자동 검증 경고' not in reply


@pytest.mark.parametrize('destination', [
    'https://', 'https:// bad.example/spec.pdf', '/spaces/ACME',
    '/spaces/ACME/pages/not-an-id', '/pages/viewpage.action?pageId=URL',
    '/display/ACME', '//files.example.test/doc.pdf',
])
def test_pseudo_title_requires_a_supported_document_destination(destination):
    raw = '### 근거\n[1] [문서](' + destination + ')\n- [1-a] 붙여넣은 기록'
    assert result._drop_direct_input_source_rows(raw) == ''


@pytest.mark.parametrize('destination', [
    '/spaces/TEAM/pages/77/계획.pdf?download=1#page=2',
    '/display/TEAM/배치+검토', '/pages/viewpage.action?pageId=77#section',
])
def test_supported_relative_document_source_is_not_unlinked_input(destination):
    raw = '### 근거\n[1] [문서](' + destination + ')\n- [1-a] 실제 문서 기록'
    assert result._drop_direct_input_source_rows(raw) == raw
