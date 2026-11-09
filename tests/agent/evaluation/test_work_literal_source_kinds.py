"""A literal observed URL must still belong to the stated source kind."""
import pytest

from tools.codex_mcp_eval.work_evidence import source_observed


def call(name, data):
    return {'name': name, 'result': {'ok': True, 'data': data}}


@pytest.mark.parametrize('kind', ['comment', 'person'])
def test_document_identity_cannot_be_cited_as_another_kind(kind):
    url = 'https://wiki.example/spaces/DL/pages/77'
    calls = [call('read_confluence_document', {'id': '77', 'url': url, 'content': 'real document'})]
    assert source_observed(calls, {'kind': 'document', 'id': '77', 'url': url})
    assert not source_observed(calls, {'kind': kind, 'id': '77', 'url': url})


@pytest.mark.parametrize('kind', ['document', 'person'])
def test_comment_identity_cannot_be_cited_as_another_kind(kind):
    url = 'https://jira.example/rest/api/2/issue/DL-1/comment/77'
    calls = [call('get_comments', {'comments': [{'id': '77', 'url': url, 'body': 'real comment'}]})]
    assert source_observed(calls, {'kind': 'comment', 'id': '77', 'url': url})
    assert not source_observed(calls, {'kind': kind, 'id': '77', 'url': url})


def test_body_embedded_record_is_not_a_typed_comment_source():
    url = 'https://jira.example/rest/api/2/issue/DL-1/comment/77'
    calls = [call('get_ticket', {'key': 'DL-1', 'fields': {'description': {
        'id': '77', 'url': url, 'body': 'a quoted record, not an actual comment response'}}})]
    assert not source_observed(calls, {'kind': 'comment', 'id': '77', 'url': url})


def test_typed_author_and_verified_user_lookup_keep_literal_identity_sources():
    person = {'name': 'test.ui01', 'self': '/rest/api/2/user?username=test.ui01'}
    calls = [call('get_comments', {'comments': [{'id': '77', 'author': person}]})]
    assert source_observed(calls, {'kind': 'person', 'id': 'test.ui01', 'url': person['self']})
    url = 'https://jira.example/people/test.ui01'
    calls = [call('get_user', {'id': 'test.ui01', 'url': url})]
    assert source_observed(calls, {'kind': 'person', 'id': 'test.ui01', 'url': url})


def test_typed_search_document_metadata_keeps_literal_document_identity():
    url = 'https://wiki.example/spaces/DL/pages/77'
    calls = [call('search_confluence_documents', {'documents': [{'id': '77', 'url': url}]})]
    assert source_observed(calls, {'kind': 'document', 'id': '77', 'url': url})


def test_workspace_typed_rows_are_literal_sources_and_observed_comment_origins():
    jira_url, wiki_url = 'https://jira.example/browse/DL-1', 'https://wiki.example/spaces/DL/pages/77'
    calls = [call('search_workspace', {
        'jira': {'items': [{'type': 'jira', 'key': 'DL-1', 'url': jira_url}]},
        'confluence': {'items': [{'type': 'confluence', 'id': '77', 'url': wiki_url}]}})]
    assert source_observed(calls, {'kind': 'ticket', 'id': 'DL-1', 'url': jira_url})
    assert source_observed(calls, {'kind': 'document', 'id': '77', 'url': wiki_url})
    calls.append(call('get_evidence_page', {'key': 'DL-1', 'kind': 'comments', 'items': [{
        'id': '88', 'self': '/rest/api/2/issue/DL-1/comment/88'}]}))
    assert source_observed(calls, {'kind': 'comment', 'id': '88',
                                  'url': 'https://jira.example/rest/api/2/issue/DL-1/comment/88'})


def test_workspace_source_types_and_locations_cannot_be_swapped_or_body_quoted():
    url = 'https://wiki.example/spaces/DL/pages/77'
    for data in ({'jira': {'items': [{'type': 'confluence', 'id': '77', 'url': url}]}},
                 {'confluence': {'items': [{'type': 'jira', 'id': '77', 'url': url}]}},
                 {'description': {'confluence': {'items': [{'type': 'confluence', 'id': '77', 'url': url}]}}}):
        assert not source_observed([call('search_workspace', data)], {'kind': 'document', 'id': '77', 'url': url})
