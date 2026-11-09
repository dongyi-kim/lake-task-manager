"""A complete verified Task -> leaf SubTask membership needs no duplicate traversal."""
import copy

import pytest

from tools.codex_mcp_eval.work_evidence import optional_count_progress


def evidence():
    children = ['DL-94011', 'DL-94012']
    def call(name, data, args=None):
        return {'name': name, 'arguments': args or {}, 'result': {
            'ok': True, 'partial': False, 'missing': 0, 'cache': {'state': 'fresh'}, 'data': data}}
    return [call('get_ticket', {'key': 'DL-94010', 'type': 'Task', 'statusCategory': 'inprogress'}),
            call('get_children', {'children': [{'key': key, 'isSubtask': True} for key in children]}, {'key': 'DL-94010'}),
            call('get_tickets', {'complete': True, 'missingKeys': [], 'issues': [
                {'key': key, 'fields': {'issuetype': {'name': 'Sub-Task', 'subtask': True},
                                      'parent': {'key': 'DL-94010'},
                                      'status': {'statusCategory': {'key': category}}}}
                for key, category in zip(children, ['done', 'indeterminate'])]})]


def test_verified_leaf_membership_accepts_child_and_all_ticket_count_ratios():
    calls = evidence()
    assert optional_count_progress(calls, {'done': 1, 'total': 2, 'percent': 50}, '하위 티켓 수 기준 완료율 50%(1/2건)')
    assert optional_count_progress(calls, {'done': 1, 'total': 3, 'percent': 100/3}, '전체 티켓 수 기준 1/3=33.3%')


def test_root_metadata_can_also_come_from_batch_without_single_ticket_read():
    calls = evidence()
    calls[0]['name'] = 'get_tickets'
    calls[0]['result']['data'] = {'issues': [{'key': 'DL-94010', 'fields': {
        'issuetype': {'name': 'Task', 'subtask': False}, 'status': {'statusCategory': {'key': 'indeterminate'}}}}]}
    assert optional_count_progress(calls, {'done': 1, 'total': 2, 'percent': 50}, '하위 티켓 수 기준 1/2=50%')


def test_typed_direct_children_already_prove_leaf_status_without_extra_body_reads():
    calls = evidence()[:2]
    for row, category in zip(calls[1]['result']['data']['children'], ['done', 'inprogress']):
        row['statusCategory'] = category
    assert optional_count_progress(calls, {'done': 1, 'total': 2, 'percent': 50}, '하위 티켓 수 기준 1/2=50%')


@pytest.mark.parametrize('defect', ['no_membership', 'partial', 'stale', 'unknown_cache', 'missing',
                                  'duplicate', 'capped', 'nonleaf_badge', 'nonleaf_raw', 'wrong_parent',
                                  'missing_child', 'epic_root', 'status_conflict'])
def test_incomplete_or_unverified_leaf_memberships_do_not_ground_progress(defect):
    calls = evidence()
    membership, batch = calls[1]['result'], calls[2]['result']['data']
    if defect == 'no_membership':
        calls.pop(1)
    elif defect in {'partial', 'missing'}:
        membership[defect] = True if defect == 'partial' else 1
    elif defect in {'stale', 'unknown_cache'}:
        membership['cache']['state'] = 'stale' if defect == 'stale' else 'unknown'
    elif defect == 'duplicate':
        membership['data']['children'].append(copy.deepcopy(membership['data']['children'][0]))
    elif defect == 'capped':
        membership['data']['children'] = [{'key': f'DL-{i}', 'isSubtask': True} for i in range(300)]
    elif defect == 'nonleaf_badge':
        membership['data']['children'][0]['isSubtask'] = False
    elif defect == 'nonleaf_raw':
        batch['issues'][0]['fields']['issuetype']['subtask'] = False
    elif defect == 'wrong_parent':
        batch['issues'][0]['fields']['parent']['key'] = 'DL-999'
    elif defect == 'missing_child':
        batch['issues'].pop()
    elif defect == 'epic_root':
        calls[0]['result']['data']['type'] = 'Epic'
    elif defect == 'status_conflict':
        calls.append({'name': 'get_ticket', 'result': {'ok': True, 'data': {
            'key': 'DL-94011', 'statusCategory': 'inprogress'}}})
    assert not optional_count_progress(calls, {'done': 1, 'total': 2, 'percent': 50}, '하위 티켓 수 기준 1/2=50%')
