"""The existing empty-operations contract also constrains structured generation."""
from copy import deepcopy

from jsonschema import Draft202012Validator

from tools.codex_mcp_eval import work_cases
from tools.codex_mcp_eval.work_data import build_work_data


def test_non_operational_answers_reject_unrequested_structured_counts():
    key = 'DL-94009'
    reply = '오늘 수행·댓글·완료 활동 없음'
    url = 'https://jira.example/browse/' + key
    calls = [{'name': 'get_ticket', 'result': {'ok': True, 'data': {
        'key': key, 'url': url, 'descriptionHtml': reply}}}]
    answer = {'resultKeys': [key], 'reportedTotal': 0, 'progress': None, 'operations': [],
              'sources': [{'kind': 'ticket', 'id': key, 'url': url}]}
    case = next(row for row in work_cases.CASES if row['id'] == 'WORK-R03-boundary')
    output = {'ok': True, 'reply': reply, 'structuredAnswer': answer}
    truth = build_work_data('2026-11-09')['oracle']
    assert work_cases.check(case, output, calls, truth)['pass']
    answer['operations'] = [{'category': 'unrequested', 'tickets': 999, 'voc': None,
                             'uniqueTables': None, 'workEvents': None}]
    assert not work_cases.check(case, output, calls, truth)['pass']


def test_case_schema_limits_only_non_operational_arrays_and_keeps_shared_schema_unchanged():
    assert hasattr(work_cases, 'schema_for_case')
    original = deepcopy(work_cases.OUTPUT_SCHEMA)
    payload = {'reply': '답변', 'resultKeys': [], 'reportedTotal': None, 'progress': None,
               'sources': [], 'operations': [{'category': 'new', 'tickets': 3, 'voc': 1,
                                              'uniqueTables': 2, 'workEvents': 2}]}
    for case in work_cases.CASES:
        schema = work_cases.schema_for_case(case)
        assert Draft202012Validator(schema).is_valid(payload) == (case['family'] == 'R07')
        empty = {**payload, 'operations': []}
        assert Draft202012Validator(schema).is_valid(empty)
    assert work_cases.OUTPUT_SCHEMA == original
