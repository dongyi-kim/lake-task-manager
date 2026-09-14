"""Network-free regressions for the repository-bound DEV component runner."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

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
