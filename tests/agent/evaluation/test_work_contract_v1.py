"""Full-run disagreements: evidence basis and semantic review stay distinct."""
import copy
from hashlib import sha256
import json

import pytest
from jsonschema import Draft202012Validator

from tools.codex_mcp_eval import work_cases
from tools.codex_mcp_eval.work_data import build_work_data
from tools.codex_mcp_eval.work_evidence import source_observed


ROOT, CHILD, DENIED, EPIC = 'DL-94010', 'DL-94011', 'DL-94012', 'DL-94000'


def call(name, data, arguments=None, *, partial=False):
    return {'name': name, 'arguments': arguments or {}, 'result': {'ok': True, 'partial': partial,
            'missing': 0, 'cache': {'state': 'fresh'}, 'data': data}}


def answer(keys, total, reply):
    value = {'reply': reply, 'resultKeys': keys, 'reportedTotal': total, 'progress': None,
             'operations': [], 'sources': [{'kind': 'ticket', 'id': keys[0],
                 'url': 'https://jira.example/browse/' + keys[0]}] if keys else []}
    return {'ok': True, 'reply': reply, 'structuredAnswer': value}


def check(identifier, output, calls, truth=None):
    case = next(row for row in work_cases.CASES if row['id'] == identifier)
    return work_cases.check(case, output, calls, truth or build_work_data('2026-11-09')['oracle'])


def test_zero_activity_keeps_the_inspected_excluded_ticket_as_evidence_basis():
    key = 'DL-94009'
    calls = [call('get_ticket', {'key': key, 'descriptionHtml': '오늘 수행·댓글·완료 활동 없음',
                               'url': 'https://jira.example/browse/' + key})]
    output = answer([key], 0, '검토한 DL-94009에서 오늘 기록된 활동은 0건입니다.')
    assert check('WORK-R03-boundary', output, calls)['pass']
    output['structuredAnswer']['reportedTotal'] = 1
    assert not check('WORK-R03-boundary', output, calls)['pass']


@pytest.mark.parametrize('identifier,extra', [
    ('WORK-R04-boundary', [EPIC]), ('WORK-R04-boundary', [DENIED]),
    ('WORK-R04-boundary', [EPIC, DENIED]), ('WORK-R11-boundary', [DENIED])])
def test_verified_wbs_context_and_missing_references_do_not_assert_body_or_count(identifier, extra):
    calls = [call('get_ticket', {'key': ROOT, 'epicKey': EPIC, 'summary': 'root',
                                'url': 'https://jira.example/browse/' + ROOT}),
             call('get_ticket', {'key': CHILD, 'summary': 'child'}),
             call('get_ticket', {'key': EPIC, 'summary': 'WBS ancestor'}),
             call('get_descendants', {'rootKey': ROOT, 'missing': [
                 {'key': DENIED, 'parentKey': ROOT, 'kind': 'permission'}]}, partial=True)]
    truth = build_work_data('2026-11-09')['oracle']
    truth['documentIds'] = {'일간 양식': '77'}
    content = '업무 | 실적 | 장애 | 다음 작업'
    calls.append(call('read_confluence_document', {'id': '77', 'content': content,
                      'version': {'number': 1}, 'offset': 0, 'totalCharacters': len(content), 'hasMore': False}))
    output = answer(sorted([ROOT, CHILD, *extra]), None, f'{DENIED} 권한 거부로 전체 건수·진척은 미확정입니다.')
    # Cite only the returned root URL; an unavailable reference is not an observed body.
    output['structuredAnswer']['sources'] = [{'kind': 'ticket', 'id': ROOT, 'url': 'https://jira.example/browse/' + ROOT}]
    assert check(identifier, output, calls, truth)['pass']
    unrelated = copy.deepcopy(calls)
    unrelated.append(call('get_ticket', {'key': 'DL-999', 'summary': 'unrelated'}))
    output['structuredAnswer']['resultKeys'].append('DL-999')
    assert not check(identifier, output, unrelated, truth)['pass']


def test_literal_person_self_is_evidence_but_wrong_identity_or_empty_url_is_not():
    url = '/rest/api/2/user?username=test.ui01'
    calls = [call('get_comments', {'comments': [{'id': '61013', 'author': {'name': 'test.ui01', 'self': url}}]})]
    assert source_observed(calls, {'kind': 'person', 'id': 'test.ui01', 'url': url})
    assert not source_observed(calls, {'kind': 'person', 'id': 'test.ui01물', 'url': url})
    assert not source_observed(calls, {'kind': 'person', 'id': 'test.ui01', 'url': ''})
    assert not source_observed([call('get_comments', {'comments': [{'id': '61013', 'self': '/rest/api/2/issue/DL-1/comment/61013'}]})],
                               {'kind': 'person', 'id': '61013', 'url': '/rest/api/2/issue/DL-1/comment/61013'})


def test_comment_source_can_use_only_fully_verified_reconstructed_json():
    record = {'id': '62100', 'body': 'cancelled', 'self': '/rest/api/2/issue/DL-1/comment/62100'}
    text = json.dumps(record)
    digest = sha256(text.encode()).hexdigest()
    calls = [call('get_ticket', {'key': 'DL-1', 'url': 'https://jira.example/browse/DL-1'})]
    for offset in range(0, len(text), 30):
        end = min(offset + 30, len(text))
        calls.append(call('get_evidence_page', {'key': 'DL-1', 'kind': 'comments', 'startAt': 0,
            'total': 1, 'sourceComplete': True, 'partial': False, 'hasMore': end < len(text), 'items': [{
                'id': '62100', 'sourceIndex': 0, 'recordOffset': offset, 'recordJson': text[offset:end],
                'totalCharacters': len(text), 'recordSha256': digest, 'nextRecordOffset': end if end < len(text) else None}]}))
    source = {'kind': 'comment', 'id': '62100', 'url': 'https://jira.example/rest/api/2/issue/DL-1/comment/62100'}
    assert source_observed(calls, source)
    assert not source_observed(calls[:2] + calls[3:], source)
    changed = copy.deepcopy(calls)
    changed[2]['result']['data']['items'][0]['recordSha256'] = 'changed'
    assert not source_observed(changed, source)


def test_korean_uncertainty_is_mandatory_direct_review_not_one_keyword():
    key = 'DL-94014'
    calls = [call('get_ticket', {'key': key, 'descriptionHtml': '코드 구현 참여를 의미하지 않음',
                                'url': 'https://jira.example/browse/' + key})]
    output = answer([key], None, '설계 리뷰만 수행했으며 실제 구현 건수는 미확정입니다.')
    result = check('WORK-R06-boundary', output, calls)
    assert result['pass'] and 'uncertainty_disclosure' in result['semanticReviewRequirements']
    _, specs = work_cases.review_contract()
    assert any(row['id'] == 'uncertainty_disclosure' for row in specs['WORK-R06-boundary']['elements'])
    output['structuredAnswer']['reportedTotal'] = 0
    assert not check('WORK-R06-boundary', output, calls)['pass']


def test_empty_citation_fillers_are_rejected_by_schema_and_scalar_unit_is_explicit():
    payload = answer([], None, '회사 확인이 필요합니다.')['structuredAnswer']
    payload['sources'] = [{'kind': 'person', 'id': 'known', 'url': ''}]
    assert list(Draft202012Validator(work_cases.OUTPUT_SCHEMA).iter_errors(payload))
    prompt = next(c for c in work_cases.CASES if c['id'] == 'WORK-R07-boundary')['prompts'][0]
    assert 'reportedTotal' in prompt and 'union' in prompt and '고유 테이블' in prompt
