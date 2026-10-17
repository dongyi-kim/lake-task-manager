"""Case outcomes exercise the real runner and guards, with one fake transport."""
from __future__ import annotations

import json

import pytest

from test_role_eval_runner import (  # Reuse the closed runner/transport fixtures.
    MANIFEST, _identity, _profile, _raw, _run_setup, _transport, runner,
)


def _case(runner, case_id):
    return runner.select_cases([case_id])[0]


def _attempt_case(runner, monkeypatch, case_id, **response):
    case = _case(runner, case_id)
    binding = runner.load_registry().resolve_role(case['role_id'])
    raw = _raw(role=case['role_id'],
               workflow_version=_profile()['workflows'][binding.key]['version'], **response)
    client, requests, _ = _transport(runner, monkeypatch, raw=raw)
    row = runner.attempt(case, _profile(), {'knowledge_manifest_sha256': MANIFEST},
                         _identity(case['role_id']))
    assert len(requests) == 1 and client.closed  # No judge, repair or extra request.
    return row


@pytest.mark.parametrize('case_id', [
    'role-request', 'role-query', 'role-knowledge', 'role-work',
    'role-auditor', 'role-result', 'role-portfolio', 'role-editor',
])
def test_valid_empty_block_is_not_positive_output(runner, monkeypatch, case_id):
    row = _attempt_case(runner, monkeypatch, case_id, status='blocked', missing_inputs=[])
    assert row['automaticContractPass'] is True
    assert row['outcome'] == 'unexpected_blocked'
    assert row['outcomeContractPass'] is False


@pytest.mark.parametrize('case_id', ['role-overflow', 'role-stale', 'role-missing'])
def test_expected_block_is_a_boundary_result_not_task_output(runner, monkeypatch, case_id):
    row = _attempt_case(runner, monkeypatch, case_id, status='blocked', missing_inputs=[])
    assert row['automaticContractPass'] is True
    assert row['outcome'] == 'expected_boundary'
    assert row['outcomeContractPass'] is True


@pytest.mark.parametrize('case_id,expected', [('role-work', False), ('role-missing', True)])
def test_explicit_clarification_stays_distinct_from_output(runner, monkeypatch, case_id, expected):
    row = _attempt_case(runner, monkeypatch, case_id, status='needs_input',
                        missing_inputs=['Bug의 유효한 부모 티켓을 확인해 주세요'])
    assert row['outcome'] == 'needs_input' and row['clarificationPresent'] is True
    assert row['outcomeContractPass'] is expected


def test_whitespace_is_not_a_clarification(runner, monkeypatch):
    row = _attempt_case(runner, monkeypatch, 'role-missing', status='needs_input',
                        missing_inputs=['   '])
    assert row['outcomeContractPass'] is False
    assert row['clarificationPresent'] is False


@pytest.mark.parametrize('payload', [{}, {'wrong': 'field'}, {'text': ''}])
def test_ok_with_invalid_payload_cannot_pass_outcome(runner, monkeypatch, payload):
    row = _attempt_case(runner, monkeypatch, 'role-result', status='ok',
                        missing_inputs=[], payload=payload)
    assert row['automaticContractPass'] is False
    assert row['outcome'] == 'invalid_contract' and row['outcomeContractPass'] is False


def _grounded_text(runner):
    case = _case(runner, 'role-result')
    _, pack = runner.build(case, {'knowledge_manifest_sha256': MANIFEST})
    source = next(source for source in pack.sources if source.kind == 'jira')
    text = '배치 주기를 2시간에서 30분으로 변경'
    return dict(status='ok', missing_inputs=[], payload={'text': text},
                claims=[{'claim_id': 'claim-1', 'kind': 'fact', 'text': text,
                         'citation_ids': ['cite-1']}],
                citations=[{'citation_id': 'cite-1', 'source_id': source.source_id}],
                payload_claims=[{'path': '/text', 'claim_ids': ['claim-1']}])


def test_grounded_schema_valid_payload_is_observed_not_quality_scored(runner, monkeypatch):
    row = _attempt_case(runner, monkeypatch, 'role-result', **_grounded_text(runner))
    assert row['errorCode'] == ''
    assert row['automaticContractPass'] is True
    assert row['outcome'] == 'payload_returned' and row['outcomeContractPass'] is True
    metrics, observations = runner.metrics([row])
    assert metrics['outcomes'] == {'payload_returned': 1}
    assert observations['semanticCorrectnessAssessed'] is False
    assert observations['usableCompletions'] is None


def test_guard_valid_whitespace_payload_has_no_declared_output(runner, monkeypatch):
    response = _grounded_text(runner)
    response.update(payload={'text': '   '}, payload_claims=[])
    row = _attempt_case(runner, monkeypatch, 'role-result', **response)
    assert row['automaticContractPass'] is True
    assert row['outcome'] == 'empty_output' and row['outcomeContractPass'] is False


def test_unknown_remote_status_fails_the_actual_guard(runner, monkeypatch):
    row = _attempt_case(runner, monkeypatch, 'role-result', status='unknown', missing_inputs=[])
    assert row['automaticContractPass'] is False
    assert row['outcome'] == 'invalid_contract' and row['outcomeContractPass'] is False


def test_guard_valid_audit_index_without_finding_is_empty(runner, monkeypatch):
    _, pack = runner.build(_case(runner, 'role-auditor'), {'knowledge_manifest_sha256': MANIFEST})
    source = next(source for source in pack.sources if source.kind == 'jira')
    row = _attempt_case(runner, monkeypatch, 'role-auditor', status='ok', missing_inputs=[],
                        payload={'grounded': True, 'rule_compliant': True, 'answers_request': True,
                                 'problems': [{'index': 0}]},
                        claims=[{'claim_id': 'claim-1', 'kind': 'fact', 'text': source.title,
                                 'citation_ids': ['cite-1']}],
                        citations=[{'citation_id': 'cite-1', 'source_id': source.source_id}])
    assert row['automaticContractPass'] is True
    assert row['outcome'] == 'empty_output' and row['outcomeContractPass'] is False


@pytest.mark.parametrize('case_id,payload', [
    ('role-request', {'intent': 'plan_work', 'keywords': [], 'sufficient': True,
                      'request_questions': [], 'requested_effects': [], 'target_selectors': [],
                      'tasks': [{'id': 'bug', 'kind': 'ticket', 'instruction': '', 'depends_on': [],
                                 'write_intent': True, 'completion_criteria': ['']}]}),
    ('role-work', {'questions': [], 'mode': 'task', 'items': [
        {'summary': '', 'type': 'Bug', 'background': '', 'scope_in': [''], 'dod': ['']}]}),
])
def test_guard_valid_control_shell_has_no_narrative_output(runner, monkeypatch, case_id, payload):
    _, pack = runner.build(_case(runner, case_id), {'knowledge_manifest_sha256': MANIFEST})
    source = next((source for source in pack.sources if source.kind == 'jira'), pack.sources[0])
    row = _attempt_case(runner, monkeypatch, case_id, status='ok', missing_inputs=[], payload=payload,
                        claims=[{'claim_id': 'claim-1', 'kind': 'recommendation', 'text': source.title,
                                 'citation_ids': ['cite-1']}],
                        citations=[{'citation_id': 'cite-1', 'source_id': source.source_id}])
    assert row['automaticContractPass'] is True
    assert row['outcome'] == 'empty_output' and row['outcomeContractPass'] is False


def test_guard_valid_request_question_is_classified_as_clarification(runner, monkeypatch):
    _, pack = runner.build(_case(runner, 'role-request'), {'knowledge_manifest_sha256': MANIFEST})
    question = '작업 범위를 확인해 주세요'
    row = _attempt_case(runner, monkeypatch, 'role-request', status='ok', missing_inputs=[],
                        payload={'intent': 'plan_work', 'keywords': [], 'sufficient': False,
                                 'request_questions': [{'question': question, 'field': 'scope'}],
                                 'requested_effects': [], 'target_selectors': []},
                        claims=[{'claim_id': 'claim-1', 'kind': 'recommendation', 'text': question,
                                 'citation_ids': ['cite-1']}],
                        citations=[{'citation_id': 'cite-1', 'source_id': pack.sources[0].source_id}],
                        payload_claims=[{'path': '/request_questions/0/question', 'claim_ids': ['claim-1']}])
    assert row['automaticContractPass'] is True
    assert row['outcome'] == 'needs_input' and row['clarificationPresent'] is True
    assert row['outcomeContractPass'] is False


def test_retrieval_failure_cannot_pass_outcome(runner, monkeypatch):
    raw = _raw(**_grounded_text(runner), role='result_integrator')
    raw['metadata'].pop('retriever_resources')
    _transport(runner, monkeypatch, raw=raw)
    case = _case(runner, 'role-result')
    row = runner.attempt(case, _profile(), {'knowledge_manifest_sha256': MANIFEST},
                         _identity(case['role_id']))
    assert row['contractOk'] is True and row['retrievalOk'] is False
    assert row['outcome'] == 'invalid_retrieval' and row['outcomeContractPass'] is False


def test_errors_are_unassessed_and_pending_cases_are_not_counted(runner, monkeypatch, tmp_path):
    from app.agent.maxis.knowledge_eval import KnowledgeEvalError

    _run_setup(runner, monkeypatch, tmp_path)
    client, requests, _ = _transport(runner, monkeypatch, error=KnowledgeEvalError('timeout'))
    path = runner.run('baseline', ['role-request', 'role-query'])
    result = json.loads(path.read_text(encoding='utf-8'))
    assert len(requests) == 1 and client.closed
    assert result['cases'][0]['outcome'] == 'response_unavailable'
    assert result['cases'][0]['outcomeContractPass'] is None
    assert result['notAttemptedCaseIds'] == ['role-query']
    assert result['metrics']['outcomes'] == {'response_unavailable': 1}
    assert result['complete'] is False and result['releaseEligible'] is False
    assert result['semanticCorrectnessAssessed'] is False


def test_preparation_failure_is_not_an_observed_response(runner, monkeypatch):
    monkeypatch.setattr(runner, 'build', lambda *args: (_ for _ in ()).throw(ValueError()))
    case = _case(runner, 'role-result')
    row = runner.attempt(case, _profile(), {'knowledge_manifest_sha256': MANIFEST}, None)
    assert row['outcome'] == 'not_attempted' and row['outcomeContractPass'] is None


@pytest.mark.parametrize('case_id,payload,expected', [
    ('role-query', {'reads': []}, 'empty_output'),
    ('role-query', {'reads': [{}]}, 'empty_output'),
    ('role-query', {'reads': [{'source': 'jira', 'subject': '', 'where': '', 'exhaustive': True}]}, 'empty_output'),
    ('role-result', {'text': '   '}, 'empty_output'),
    ('role-editor', {'content_html': '<p> </p>'}, 'empty_output'),
    ('role-auditor', {'grounded': True, 'problems': [], 'summary': ''}, 'empty_output'),
    ('role-auditor', {'problems': [{'index': 0, 'check': 'rule', 'message': '', 'fix': ''}]}, 'empty_output'),
    ('role-request', {'tasks': [{'id': 'bug', 'kind': 'ticket', 'instruction': ' ', 'completion_criteria': [''],
                               'write_intent': True}]}, 'empty_output'),
    ('role-work', {'items': [{'type': 'Bug', 'summary': '', 'background': '', 'scope_in': [''], 'dod': ['']}]}, 'empty_output'),
    ('role-work', {'items': [], 'questions': [{'question': '부모 티켓을 확인해 주세요'}]}, 'needs_input'),
    ('role-work', {'items': [], 'questions': [{'question': ' ', 'kind': 'text'}]}, 'empty_output'),
    ('role-request', {'request_questions': [{'question': '작업 대상을 확인해 주세요'}]}, 'needs_input'),
    ('role-request', {'blocking_questions': ['작업 대상을 확인해 주세요']}, 'needs_input'),
    ('role-request', {'request_questions': [{'question': ' ', 'kind': 'target'}]}, 'empty_output'),
])
def test_declared_content_fields_exclude_empty_control_shells(runner, case_id, payload, expected):
    row = {'requestAttempted': True, 'responseReceived': True, 'contractOk': True,
           'retrievalOk': True, 'status': 'ok', 'errorCode': '',
           'response': {'status': 'ok', 'payload': payload}}
    result = runner.evaluate_outcome(_case(runner, case_id), row)
    assert result['outcome'] == expected and result['outcomeContractPass'] is False


def test_unknown_status_and_expectation_fail_closed(runner):
    row = {'requestAttempted': True, 'responseReceived': True, 'contractOk': True,
           'retrievalOk': True, 'status': 'unknown', 'response': {'payload': {'text': 'output'}}}
    assert runner.evaluate_outcome(_case(runner, 'role-result'), row)['outcomeContractPass'] is False
    case = _case(runner, 'role-result')
    case['expected']['outcome']['kind'] = 'new-unhandled-expectation'
    with pytest.raises(ValueError, match='outcome'):
        runner.evaluate_outcome(case, row)


def test_complete_attempts_do_not_mean_outcome_acceptance(runner, monkeypatch, tmp_path, capsys):
    _run_setup(runner, monkeypatch, tmp_path)
    captured = {}

    def metadata(**kwargs):
        captured.update(kwargs)
        return {'benchmarkKey': 'benchmark', 'candidateKey': 'candidate',
                'qualificationIneligibilityReasons': []}

    monkeypatch.setattr(runner, 'build_run_metadata', metadata)
    monkeypatch.setattr(runner, '_preflight', lambda: (
        _profile(), {'knowledge_manifest_sha256': MANIFEST}, None, _identity('request_architect')))
    client, requests, _ = _transport(runner, monkeypatch, raw=_raw(status='blocked', missing_inputs=[]))
    path = runner.run('candidate', ['role-request'])
    result = json.loads(path.read_text(encoding='utf-8'))
    assert captured['battery_version'] == '2.0.0'
    dependencies = {item['file']: item['sha256'] for item in captured['checker_dependencies']}
    assert dependencies['deploy/maxis-dify/run_role_evals.py'] == 'c' * 64
    assert dependencies['tools/maxis_role_remote_cases.py'] == 'c' * 64
    assert set(dependencies) == set(runner.HARNESS_FILES)
    assert result['complete'] is True and result['notAttemptedCaseIds'] == []
    assert result['cases'][0]['automaticContractPass'] is True
    assert result['cases'][0]['outcomeContractPass'] is False
    assert result['metrics']['outcomes'] == {'unexpected_blocked': 1}
    assert result['semanticCorrectnessAssessed'] is False and result['releaseEligible'] is False
    printed = json.loads(capsys.readouterr().out)
    assert printed['outcome'] == 'unexpected_blocked' and printed['outcomeContractPass'] is False
    assert 'response' not in printed and len(requests) == 1 and client.closed


def test_outcome_cases_and_checker_source_both_bind_battery_manifest(runner):
    from tools.agent_eval_protocol import battery_manifest_sha256

    cases = runner.select_cases()
    base = battery_manifest_sha256(cases, checker_dependencies=[{'sha256': 'a' * 64}])
    assert base != battery_manifest_sha256(cases, checker_dependencies=[{'sha256': 'b' * 64}])
    cases[0]['expected']['outcome']['content_paths_any'] = [['changed_field']]
    assert base != battery_manifest_sha256(cases, checker_dependencies=[{'sha256': 'a' * 64}])


def test_declared_output_and_question_paths_exist_in_actual_wire_schemas(runner):
    for case in runner.select_cases():
        expected = case['expected']['outcome']
        if expected['kind'] == 'safety_boundary':
            assert set(expected['statuses']) == {'blocked', 'needs_input'}
            continue
        _, pack = runner.build(case, {'knowledge_manifest_sha256': MANIFEST})
        schema = pack.payload_schema()
        paths = list(expected['content_paths_any'])
        for declaration in expected.get('clarification_fields', []):
            paths.append([declaration['field'], '*'] + (
                [declaration['text_field']] if declaration['text_field'] else []))
        assert all(path in paths for path in expected.get('html_paths', []))
        for path in paths:
            node = schema
            for part in path:
                while '$ref' in node:
                    target = node['$ref']
                    assert target.startswith('#/')
                    node = schema
                    for segment in target[2:].split('/'):
                        node = node[segment]
                node = node['items'] if part == '*' else node['properties'][part]
            assert node['type'] == 'string', (case['id'], path)
