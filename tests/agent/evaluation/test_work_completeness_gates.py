"""Correct oracle values never substitute for observed complete membership."""
from copy import deepcopy
from types import SimpleNamespace

import anyio
from mcp import Client
import pytest

from support.evaluation_tmp import external_tmp_path as tmp_path
from tools.agent_eval_isolation import NETWORK_PREFLIGHT_MARKER
from tools.codex_mcp_eval import recovery_runner, work_cases
from tools.codex_mcp_eval.work_fixture import WorkFixture


def test_exact_oracle_ratio_needs_hierarchy_beyond_actual_known_key_batch(tmp_path):
    with WorkFixture(tmp_path / 'scope', port=0, today='2026-11-09') as fixture:
        async def exercise():
            async with Client(fixture.runtime.server) as client:
                keys = ['DL-94010', 'DL-94011', 'DL-94012']
                result = await client.call_tool('get_tickets', {'keys': keys})
                assert not result.is_error
                data = result.structured_content['data']
                assert data['complete'] and data['returned'] == 3
                reply = '하위 티켓 전체 2건 중 1건 완료, 티켓 수 기준 50%입니다.'
                answer = {'resultKeys': keys, 'reportedTotal': 3, 'progress': {
                    'done': 1, 'total': 2, 'percent': 50}, 'operations': [], 'sources': [{
                        'kind': 'ticket', 'id': keys[0], 'url': data['issues'][0]['url']}]}
                output = {'ok': True, 'reply': reply, 'structuredAnswer': answer}
                case = next(row for row in work_cases.CASES if row['id'] == 'WORK-R04-normal')
                check = lambda: work_cases.check(case, output, fixture.tool_calls, fixture.ground_truth())
                assert not check()['pass'], 'Known-key rows do not prove exhaustive child membership'
                children = await client.call_tool('get_children', {'key': keys[0]})
                assert not children.is_error
                assert check()['pass'], check()
        anyio.run(exercise)


def test_r04_requires_direct_denominator_semantics_as_well_as_arithmetic():
    _, specs = work_cases.review_contract()
    for identifier in ('WORK-R04-normal', 'WORK-R04-boundary'):
        assert 'progress_denominator' in {element['id'] for element in specs[identifier]['elements']}


QUERY = ('labels = work_research_fixture AND key in '
         '(DL-94001,DL-94002,DL-94006,DL-94007,DL-94017,DL-94018) ORDER BY key ASC')


def membership_calls(keys, *, snapshot, page_size=2):
    from app.jira.jql import compile_jql
    where, order = QUERY.split(' ORDER BY ')
    canonical = compile_jql(f'project in ("DL") AND ({where}) ORDER BY {order}').canonical
    calls = []
    for start in range(0, len(keys), page_size):
        rows = [{'key': key} for key in keys[start:start + page_size]]
        end = start + len(rows)
        calls.append({'name': 'search_issues', 'arguments': {'jql': QUERY, 'start_at': start,
            'snapshot_id': snapshot if start else None, 'max_results': page_size}, 'result': {
                'ok': True, 'partial': False, 'missing': 0,
                'cache': {'state': 'fresh', 'invalidated': False}, 'data': {
                    'canonicalJql': canonical, 'snapshotId': snapshot, 'startAt': start,
                    'maxResults': page_size, 'returned': len(rows), 'total': len(keys),
                    'issues': rows, 'hasMore': end < len(keys),
                    'nextStartAt': end if end < len(keys) else None}}})
    return calls


@pytest.mark.parametrize('mode', ['uninterrupted', 'restarted'])
@pytest.mark.parametrize('defect', [None, 'none', 'first_page', 'wrong_query', 'mixed_snapshot',
                                  'partial', 'stale', 'duplicate_key', 'one_batch', 'repeat_first',
                                  'new_first', 'conflict_first', 'total_conflict'])
def test_recovery_orchestration_requires_current_complete_query_population(tmp_path, monkeypatch, mode, defect):
    """Stub only phase/business work: the runner must add its independent population gate.

    Actual MCP phase/provenance/file behavior remains covered by test_work_recovery.
    Here a perfect business answer cannot hide missing or incoherent population evidence.
    """
    monkeypatch.setenv(NETWORK_PREFLIGHT_MARKER, '1')
    old_keys = ['DL-94001', 'DL-94002', 'DL-94006', 'DL-94007', 'DL-94017']
    keys = old_keys[:-1] + ['DL-94018']
    current = membership_calls(keys, snapshot='current', page_size=100 if defect == 'one_batch' else 2)
    if defect == 'none':
        current = []
    elif defect == 'first_page':
        current = current[:1]
    elif defect == 'wrong_query':
        for call in current:
            call['result']['data']['canonicalJql'] = 'project = DL ORDER BY key ASC'
    elif defect == 'mixed_snapshot':
        current[1]['result']['data']['snapshotId'] = 'another'
    elif defect == 'partial':
        current[1]['result']['partial'] = True
    elif defect == 'stale':
        current[1]['result']['cache']['state'] = 'stale'
    elif defect == 'duplicate_key':
        current[1]['result']['data']['issues'][0]['key'] = keys[0]
    elif defect in {'repeat_first', 'new_first', 'conflict_first', 'total_conflict'}:
        repeated = deepcopy(current[0])
        if defect == 'new_first':
            repeated['result']['data']['snapshotId'] = 'new-source'
        elif defect == 'conflict_first':
            repeated['result']['data']['issues'][0]['key'] = 'DL-999'
        elif defect == 'total_conflict':
            repeated['result']['data']['total'] += 1
        current.append(repeated)
    current.append({'name': 'get_tickets', 'arguments': {'keys': keys}, 'result': {
        'ok': True, 'data': {'issues': [{'key': key} for key in keys], 'complete': True}}})
    oracle = {'cases': {'WORK-R02-boundary': {'keys': keys}}}

    def phase(directory, index, options, binary, cli_run, files_root, controller, revision, factory):
        first = controller is not None
        calls = membership_calls(old_keys, snapshot='old') if first else current
        if controller:
            controller._trigger('unit-test boundary')
        return {'output': {'ok': not first, 'intentionalInterruption': first, 'reply': 'correct facts',
                    'structuredAnswer': {'resultKeys': keys, 'reportedTotal': 1, 'progress': None},
                    'durationSeconds': 1, 'usage': {}},
                'mcpToolCalls': deepcopy(calls), 'persistedCalls': deepcopy(calls),
                'fileCalls': [{'name': 'read_file', 'ok': True, 'result': {'content': '{}'}}],
                'skillReads': [{'uri': 'skill://ltm-work-reporting/SKILL.md'}], 'upstreamCalls': [],
                'isolation': {'worldUnchanged': True, 'providerStoreUnchanged': True}}, oracle

    monkeypatch.setattr(recovery_runner, '_phase', phase)
    battery = SimpleNamespace(OUTPUT_SCHEMA={}, check=lambda *args: {'pass': True, 'failures': []})
    options = SimpleNamespace(recovery_scenario='source-changed', recovery_mode=mode)
    record = recovery_runner.run_recovery_case({'id': 'WORK-R02-boundary'}, options, 'stub',
        tmp_path / 'run', None, battery, artifact_root=tmp_path)
    if defect in (None, 'one_batch', 'repeat_first'):
        assert record['automaticPass'], record['automaticContract']
    else:
        assert not record['automaticPass'], 'An oracle-correct answer needs current complete JQL membership'
