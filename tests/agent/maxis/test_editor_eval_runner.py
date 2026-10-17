"""Network-free regressions for the repository-bound DEV component runner."""
import importlib.util
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'deploy/maxis-dify'))
    spec = importlib.util.spec_from_file_location('editor_component_runner_test',
                                                ROOT / 'deploy/maxis-dify/run_editor_evals.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_direct_run_never_opens_credentials(runner, monkeypatch):
    monkeypatch.delenv('LTM_EVAL_NETWORK_PREFLIGHTED', raising=False)
    monkeypatch.setattr(runner, '_preflight', lambda: pytest.fail('must reject before profile/key'))
    with pytest.raises(Exception, match='network_authorization_required'):
        runner.run('baseline')


def test_all_native_cases_fit_guarded_wire_budget(runner):
    from tools.maxis_editor_eval_cases import load_cases
    from app.agent.maxis.context_guard import validate_pack_safety
    for case in load_cases():
        pack = runner.build_pack(case, 'a' * 64, 'eval_test', datetime.now(timezone.utc))
        validate_pack_safety(pack)
        assert pack.use_case == 'editor_author'
        assert pack.payload_schema()['properties']['mode']['const'] == case['input']['kind']
        assert json.loads(pack.sources[-1].content) == case['input']


def test_mutated_case_rejected_before_pack_build(runner):
    from tools.maxis_editor_eval_cases import load_case
    case = load_case('CMP2')
    case['instruction'] = 'private user data'
    with pytest.raises(ValueError, match='snapshot'):
        runner.build_pack(case, 'a' * 64, 'eval_test', datetime.now(timezone.utc))


def test_raw_answer_excludes_sentinel_and_reasoning(runner):
    marker = 'LTM_RELEASE_SENTINEL_V1=synthetic test proof'
    raw = runner.safe_answer(json.dumps({'knowledge_proof': marker,
        'payload': {'content_html': '<p>' + marker + '</p>'},
        'reasoning_content': 'hidden trace', 'status': 'blocked', marker: marker,
        'apiKey': 'synthetic-credential-only', 'Access-Token': 'credential',
        'other': 'Bearer abcdefghijklmnopqrstuvwxyz'}))
    encoded = json.dumps(raw)
    assert 'synthetic test proof' not in encoded
    assert 'knowledge_proof' not in encoded
    assert 'hidden trace' not in encoded
    assert 'synthetic-credential-only' not in encoded
    assert 'abcdefghijklmnopqrstuvwxyz' not in encoded


def test_retrieval_marker_cannot_pass_real_editor_schema(runner):
    with pytest.raises(Exception):
        runner.render_component({'status': 'retrieved'})


def test_renderer_never_reads_live_settings(runner, monkeypatch):
    from app.infra import settings
    monkeypatch.setattr(settings, 'get_settings', lambda *a, **kw: pytest.fail('live settings read'))
    payload = {'contract': 'editor-draft.v1', 'mode': 'comment',
               'content_html': '<p>[[person:1234567890abcdef]] DL-9090</p>',
               'source_ids': [], 'unsupported_claims': [], 'warnings': []}
    result = runner.render_component(payload, {'[[person:1234567890abcdef]]': 'skcc.x1402'})
    assert 'data-type="mention"' in result['html']
    assert 'DL-9090' in result['html']
    assert result == runner.render_component(payload, {'[[person:1234567890abcdef]]': 'skcc.x1402'})


def test_mixed_metrics_do_not_hide_failed_attempts(runner):
    rows = [{'latencyMs': 1000, 'responseReceived': True, 'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}},
            {'latencyMs': 2000, 'errorCode': 'timeout'}]
    metrics = runner.metrics_for(rows)
    assert metrics['attempts'] == 2 and metrics['durationSeconds'] == 3
    assert metrics['calls'] == 1 and metrics['totalTokens'] is None
    assert metrics['costUsd'] is None and metrics['cachedTokens'] is None
    assert metrics['nativeLlmCalls'] is None


def test_received_response_without_usage_still_counts(runner):
    metrics = runner.metrics_for([{'latencyMs': 10, 'responseReceived': True}])
    assert metrics['calls'] == 1
    assert metrics['totalTokens'] is None


def test_observed_zero_usage_is_not_missing(runner):
    metrics = runner.metrics_for([{'latencyMs': 10, 'responseReceived': True,
        'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}])
    assert metrics['calls'] == 1
    assert metrics['tokenUsageComplete'] is True
    assert metrics['totalTokens'] == 0


def test_structured_request_keeps_target_mode_and_seed(runner):
    from tools.maxis_editor_eval_cases import load_case, project_case
    case = load_case('CMP3')
    pack = runner.build_pack(case, 'a' * 64, 'eval_test', datetime.now(timezone.utc), 'role-terms')
    assert pack.role_context[0].name == 'editor_request'
    assert json.loads(pack.role_context[0].content_json) == project_case(case)['input']
    assert pack.query.startswith('Canonical LTM role editor_author. Process this guarded user intent:\n이어서 완성해줘')


def _failed_run(runner, monkeypatch, tmp_path, error):
    from tools import maxis_editor_eval_cases
    profile = {'base_url': 'https://dify.example.invalid/v1', 'fingerprint': 'a' * 32,
        'knowledge_version': 'synthetic-knowledge-v1', 'knowledge_dataset_id': '',
        'workflow_bundle_version': 'synthetic-bundle-v1',
        'workflows': {'editor_author': {'version': '11111111-1111-4111-8111-111111111111'}}}
    endpoint = runner._destination_identity(profile['base_url'])
    monkeypatch.setattr(runner, '_require_network_handoff', lambda: endpoint)
    monkeypatch.setattr(runner, '_credential_free_dev_api_base', lambda: profile['base_url'])
    monkeypatch.setattr(runner, '_preflight', lambda: (profile, {'knowledge_manifest_sha256': 'a' * 64}, None, None))
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(runner, 'os', SimpleNamespace(environ=dict(os.environ)))
    monkeypatch.setattr(runner, '_source_binding', lambda: {'app/agent/maxis/errors.py': 'a' * 64})
    monkeypatch.setattr(runner.profiles, 'resolve', lambda stage: profile)
    monkeypatch.setattr(runner.credentials, 'get_for', lambda *args: 'synthetic-key')
    monkeypatch.setattr(runner, 'build_run_metadata', lambda **kw: {
        'benchmarkKey': 'benchmark', 'candidateKey': 'candidate', 'qualificationIneligibilityReasons': []})
    case = maxis_editor_eval_cases.load_case('CMP2')
    monkeypatch.setattr(maxis_editor_eval_cases, 'load_cases', lambda: [case])

    class Client:
        def _request_validated_chat_bytes(self, *args, **kwargs):
            if error is not None:
                raise error
            return b'{"event":"error","answer":"synthetic-private-body"}', 200, 12, '', self

        def record_success(self):
            pass

    monkeypatch.setattr(runner, 'make_client', lambda *args: Client())
    path = runner.run('baseline')
    return json.loads(path.read_text(encoding='utf-8'))


@pytest.mark.parametrize('status', [400, 422, 401, 403])
def test_editor_http_failure_preserves_status_without_error_body(runner, monkeypatch, tmp_path, status):
    from app.agent.maxis.errors import classify_http_status
    from app.agent.maxis.telemetry import request_observation
    error = classify_http_status(status)
    error.telemetry = request_observation(transport_status='failed', http_status=status, latency_ms=12)
    result = _failed_run(runner, monkeypatch, tmp_path, error)
    row = result['cases'][0]
    assert row['failure']['stage'] == 'http_response' and row['failure']['httpStatus'] == status
    assert row['failure']['requestIdSha256'] == hashlib.sha256(row['pack']['request_id'].encode()).hexdigest()
    assert row['nativeLlmCalls'] is None and row['failure']['nativeModelCallCount'] is None
    assert not row['automaticPass'] and not result['releaseEligible']
    assert row['metrics']['calls'] == 0 and row['metrics']['nativeLlmCalls'] is None
    assert 'synthetic-key' not in json.dumps(result)


def test_editor_local_bad_request_is_distinct_from_server_rejection(runner, monkeypatch, tmp_path):
    from app.agent.maxis.errors import MaxisErrorCode, MaxisGatewayError
    result = _failed_run(runner, monkeypatch, tmp_path, MaxisGatewayError(MaxisErrorCode.BAD_REQUEST))
    failure = result['cases'][0]['failure']
    assert failure['stage'] == 'local_validation' and failure['httpStatus'] is None
    assert failure['transportStatus'] == 'not_attempted' and failure['nativeModelCallCount'] is None


def test_editor_bad_envelope_retains_http_observation_without_body(runner, monkeypatch, tmp_path):
    result = _failed_run(runner, monkeypatch, tmp_path, None)
    failure = result['cases'][0]['failure']
    assert failure['stage'] == 'response_envelope' and failure['httpStatus'] == 200
    assert failure['transportStatus'] == 'succeeded' and failure['nativeModelCallCount'] is None
    assert 'synthetic-private-body' not in json.dumps(result)
