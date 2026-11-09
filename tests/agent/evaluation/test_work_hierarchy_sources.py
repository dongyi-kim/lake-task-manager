"""Comment origins come from explicit typed issue metadata, never arbitrary nested URLs."""
from urllib.parse import urljoin

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
import pytest

from support.evaluation_tmp import external_tmp_path as tmp_path
from tools.codex_mcp_eval.work_evidence import optional_count_progress, source_observed
from tools.codex_mcp_eval.work_fixture import WorkFixture

KEY, NUMBER, COMMENT = 'DL-94008', '1974554802', '61008'
REST = 'https://rest.example/rest/api/2/issue/' + KEY
SOURCE = {'kind': 'comment', 'id': COMMENT, 'url': REST + '/comment/' + COMMENT}


def call(name, data):
    return {'name': name, 'result': {'ok': True, 'partial': False, 'missing': 0, 'data': data}}


def evidence():
    issue = {'key': KEY, 'id': NUMBER, 'self': REST, 'fields': {'issuetype': {'name': 'Task'}}}
    return [call('get_descendants', {'rootKey': KEY, 'issues': [{'key': KEY, 'issue': issue}]}),
            call('get_evidence_page', {'key': KEY, 'kind': 'comments', 'items': [
                {'id': COMMENT, 'self': '/rest/api/2/issue/' + KEY + '/comment/' + COMMENT}]})]


def test_descendant_raw_issue_is_an_observed_rest_origin():
    assert source_observed(evidence(), SOURCE)


def test_literal_ticket_self_is_valid_only_for_its_observed_key():
    calls = evidence()
    assert source_observed(calls, {'kind': 'ticket', 'id': KEY, 'url': REST})
    assert not source_observed(calls, {'kind': 'ticket', 'id': NUMBER, 'url': REST})
    calls[0]['result']['data']['issues'][0]['issue']['self'] = REST.replace(KEY, NUMBER)
    assert source_observed(calls, {'kind': 'ticket', 'id': KEY, 'url': REST.replace(KEY, NUMBER)})
    assert not source_observed(calls, {'kind': 'ticket', 'id': KEY, 'url': REST})


@pytest.mark.parametrize('bad_self', [REST + '?token=private', REST + '#other',
    REST.replace('rest.example', 'secret@rest.example'), REST.replace(KEY, 'DL-99999')])
def test_literal_ticket_self_keeps_endpoint_and_credential_guards(bad_self):
    calls = evidence()
    calls[0]['result']['data']['issues'][0]['issue']['self'] = bad_self
    assert not source_observed(calls, {'kind': 'ticket', 'id': KEY, 'url': bad_self})


def test_explicit_child_badge_url_is_an_observed_origin_but_key_alone_is_not():
    calls = evidence()
    calls[0] = call('get_children', {'children': [{'key': KEY, 'url': 'https://rest.example/browse/' + KEY}]})
    assert source_observed(calls, SOURCE)
    calls[0]['result']['data']['children'][0].pop('url')
    assert not source_observed(calls, SOURCE)


def test_partial_hierarchy_can_ground_a_returned_citation_but_not_whole_progress():
    calls = evidence()
    calls[0]['result']['partial'] = True
    calls[0]['result']['data'].update(complete=False, partial=True, total=2, returned=1,
                                    hasMore=False, missingTotal=1, includeRoot=True, startAt=0)
    assert source_observed(calls, SOURCE)
    assert not optional_count_progress(calls, {'done': 1, 'total': 2, 'percent': 50}, '전체 하위 50%')


@pytest.mark.parametrize('defect', ['wrapper_key', 'raw_key', 'missing_raw', 'failed_origin', 'origin_error',
    'wrong_origin_path', 'wrong_ticket', 'description_origin', 'parent_origin', 'unknown_tool',
    'no_record_self', 'failed_comment', 'credentials', 'query', 'fragment', 'foreign_comment', 'wrong_comment'])
def test_unverified_or_unrelated_metadata_cannot_sponsor_a_comment_url(defect):
    calls = evidence()
    wrapper = calls[0]['result']['data']['issues'][0]
    raw = wrapper['issue']
    record = calls[1]['result']['data']['items'][0]
    if defect == 'wrapper_key':
        wrapper['key'] = 'DL-99999'
    elif defect == 'raw_key':
        raw['key'] = 'DL-99999'
    elif defect == 'missing_raw':
        wrapper.pop('issue')
    elif defect == 'failed_origin':
        calls[0]['result']['ok'] = False
    elif defect == 'origin_error':
        calls[0]['isError'] = True
    elif defect == 'wrong_origin_path':
        raw['self'] = 'https://rest.example/rest/api/2/issue/DL-99999'
    elif defect == 'wrong_ticket':
        wrapper['key'] = raw['key'] = 'DL-99999'
        raw['self'] = 'https://rest.example/rest/api/2/issue/DL-99999'
    elif defect in {'description_origin', 'parent_origin'}:
        raw.pop('self')
        raw['fields']['description' if defect == 'description_origin' else 'parent'] = {
            'key': KEY, 'id': NUMBER, 'self': REST}
    elif defect == 'unknown_tool':
        calls[0]['name'] = 'untyped_example'
    elif defect == 'no_record_self':
        record.pop('self')
    elif defect == 'failed_comment':
        calls[1]['result']['ok'] = False
    elif defect == 'credentials':
        raw['self'] = REST.replace('rest.example', 'secret@rest.example')
    elif defect == 'query':
        raw['self'] += '?token=private'
    elif defect == 'fragment':
        raw['self'] += '#other'
    elif defect == 'foreign_comment':
        record['self'] = SOURCE['url'].replace('rest.example', 'foreign.example')
    elif defect == 'wrong_comment':
        record['self'] = record['self'].replace(COMMENT, '99999')
    assert not source_observed(calls, SOURCE)


def test_real_mcp_descendant_and_relative_comment_self_share_the_observed_origin(tmp_path):
    with WorkFixture(tmp_path / 'case', today='2026-11-09', port=0, skill_condition='candidate') as fixture:
        async def exercise():
            async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + fixture.token}) as http:
                async with Client(streamable_http_client(fixture.url, http_client=http)) as client:
                    hierarchy = await client.call_tool('get_descendants', {'root_key': KEY, 'include_root': True})
                    rows = hierarchy.structured_content['data']['issues']
                    origin = next(row['issue']['self'] for row in rows if row['key'] == KEY)
                    assert source_observed(fixture.tool_calls, {'kind': 'ticket', 'id': KEY, 'url': origin})
                    comments = await client.call_tool('get_evidence_page', {'key': KEY, 'kind': 'comments'})
                    for row in comments.structured_content['data']['items']:
                        assert source_observed(fixture.tool_calls, {'kind': 'comment', 'id': row['id'],
                                                                    'url': urljoin(origin, row['self'])})
        anyio.run(exercise)
