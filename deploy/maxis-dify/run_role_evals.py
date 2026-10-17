"""Exploratory Role components, not workflow/UI quality or an activation gate.

Closed synthetic fixtures cross the real payload and response guards. No live app
state, tools, history, writes, prompt override, retry, or production profile exists.
Run only through launch_role_evals.py in a network-authorized separate process.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_knowledge_evals import (  # noqa: E402
    _binding_unchanged, _credential_free_dev_api_base, _destination_identity,
    _preflight, _require_network_handoff,
)
from run_editor_evals import safe_answer  # noqa: E402
from app.agent.maxis import credentials  # noqa: E402
from app.agent.maxis.client import _strict_json_loads  # noqa: E402
from app.agent.maxis.contracts import EvidencePackV1  # noqa: E402
from app.agent.maxis.errors import evaluation_failure_diagnostics  # noqa: E402
from app.agent.maxis.knowledge_eval import (  # noqa: E402
    KnowledgeEvalError, _project_resources, _usage_observation,
    evaluation_source_file_sha256, make_client, safe_remote_error,
)
from app.agent.maxis.registry import load_registry  # noqa: E402
from app.agent.maxis.response_guard import ResponseGuard, ResponseGuardError  # noqa: E402
from app.agent.maxis.telemetry import request_observation  # noqa: E402
from tools.agent_eval_protocol import (  # noqa: E402
    build_run_metadata, reserve_raw_result_path, write_raw_result,
)
from tools.agent_eval_review_specs import review_specs  # noqa: E402
from tools.maxis_context_diagnostics import describe_context  # noqa: E402
from tools.maxis_performance_metrics import summarize_attempts  # noqa: E402
from tools.maxis_role_remote_cases import (  # noqa: E402
    BATTERY_VERSION, build_remote_fixture, load_remote_cases,
)

HARNESS_FILES = (
    'deploy/maxis-dify/launch_role_evals.py', 'deploy/maxis-dify/run_role_evals.py',
    'deploy/maxis-dify/launch_knowledge_evals.py', 'deploy/maxis-dify/run_knowledge_evals.py',
    'deploy/maxis-dify/run_editor_evals.py', 'tools/maxis_role_eval_cases.py',
    'tools/maxis_role_remote_cases.py', 'tools/maxis_context_diagnostics.py',
    'tools/maxis_performance_metrics.py', 'tools/agent_eval_review_specs.py',
    'app/agent/maxis/errors.py', 'app/agent/maxis/telemetry.py',
)
CANDIDATE_FILES = (
    'app/agent/maxis/backend.py', 'app/agent/maxis/context_guard.py',
    'app/agent/maxis/retrieval_query.py',
    'app/agent/workflow/role_manifest.py', 'app/agent/workflow/contracts.py',
    'app/agent/workflow/work_architect/contracts.py',
    'app/agent/workflow/agents/request_architect.py', 'app/agent/workflow/agents/auditor.py',
    'app/agent/workflow/agents/portfolio_analyst.py', 'app/agent/maxis/client.py',
)
STOP_ERRORS = frozenset({'authentication', 'forbidden', 'network', 'timeout', 'rate_limited',
                        'upstream', 'circuit_open', 'configuration', 'binding_changed', 'interrupted'})


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def source_binding() -> dict:
    return {**evaluation_source_file_sha256(), **{
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in (*HARNESS_FILES, *CANDIDATE_FILES)}}


def select_cases(selected=None) -> list[dict]:
    cases = load_remote_cases()
    ids = [case['id'] for case in cases]
    if selected is None:
        return cases
    if (not isinstance(selected, list) or not selected
            or any(type(value) is not str or value not in ids for value in selected)
            or len(set(selected)) != len(selected)):
        raise ValueError('select unique repository fixture IDs')
    # Canonical order is fixed, never optimized after observing results.
    return [case for case in cases if case['id'] in selected]


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', choices=('baseline', 'candidate'), required=True)
    parser.add_argument('--cases', nargs='+', choices=[c['id'] for c in load_remote_cases()])
    args = parser.parse_args(argv)
    try:
        select_cases(args.cases)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def error_code(exc: Exception) -> str:
    # Guard code only; paths and exception text can contain provider/user material.
    if isinstance(exc, ResponseGuardError) and re.fullmatch(r'[a-z_]{1,80}', exc.code):
        return exc.code
    return safe_remote_error(exc)


def build(case: dict, deployment: dict) -> tuple[dict, EvidencePackV1]:
    fixture = build_remote_fixture(case['id'], observed_at=datetime.now(timezone.utc),
                                   manifest_sha256=deployment['knowledge_manifest_sha256'])
    pack = EvidencePackV1.model_validate_json(json.dumps(fixture['payload']['evidencePack']))
    return fixture, pack


class _TextContent(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []

    def handle_data(self, value):
        self.text.append(value)


def _content_at(value, path: list[str], *, html=False) -> bool:
    """Inspect only declared narrative leaves; enums/IDs/control flags do not count."""
    if path:
        field, *remaining = path
        if field == '*':
            return isinstance(value, list) and any(_content_at(item, remaining, html=html) for item in value)
        return isinstance(value, dict) and _content_at(value.get(field), remaining, html=html)
    if not isinstance(value, str):
        return False
    if html:
        parser = _TextContent()
        parser.feed(value)
        value = ''.join(parser.text)
    return bool(value.strip())


def evaluate_outcome(case: dict, row: dict) -> dict:
    """Observe declared output presence or boundary, never semantic task success.

    Only a validated/bound response and verified retrieval can satisfy this gate.
    Missing responses stay unassessed; a positive clarification stays incomplete.
    """
    expected = case['expected']['outcome']
    kind = expected['kind']
    if kind not in {'payload', 'safety_boundary'}:
        raise ValueError('unknown outcome expectation')
    result = {'outcomeExpected': kind, 'outcomeContractPass': False,
              'clarificationPresent': False}

    def finish(outcome, passed=False):
        return {**result, 'outcome': outcome, 'outcomeContractPass': passed}

    if row.get('requestAttempted') is not True:
        return finish('not_attempted', None)
    if row.get('responseReceived') is not True:
        return finish('response_unavailable', None)
    if (row.get('contractOk') is not True or row.get('errorCode')
            or row.get('status') not in {'ok', 'needs_input', 'blocked'}):
        return finish('invalid_contract')
    if row.get('retrievalOk') is not True:
        return finish('invalid_retrieval')
    response = row['response']
    status = row['status']
    questions = response.get('missing_inputs', [])
    result['clarificationPresent'] = any(isinstance(q, str) and q.strip() for q in questions)
    if status == 'needs_input':
        return finish('needs_input', result['clarificationPresent'] and kind == 'safety_boundary'
                      and status in expected['statuses'])
    if status == 'blocked':
        accepted = kind == 'safety_boundary' and status in expected['statuses']
        return finish('expected_boundary' if accepted else 'unexpected_blocked', accepted)
    if kind == 'safety_boundary':
        return finish('unexpected_payload')
    payload = response.get('payload', {})
    for declaration in expected.get('clarification_fields', []):
        for question in payload.get(declaration['field'], []):
            value = (question.get(declaration['text_field'])
                     if declaration['text_field'] and isinstance(question, dict) else question)
            if isinstance(value, str) and value.strip():
                result['clarificationPresent'] = True
    for path in expected['content_paths_any']:
        if _content_at(payload, path, html=path in expected.get('html_paths', [])):
            return finish('payload_returned', True)
    return finish('needs_input' if result['clarificationPresent'] else 'empty_output')


def attempt(case: dict, profile: dict, deployment: dict, identity) -> dict:
    row = {'caseId': case['id'], 'attemptId': case['id'], 'roleId': case['role_id'],
           'status': 'error', 'responseReceived': False, 'requestAttempted': False,
           'contractOk': False, 'retrievalOk': False, 'response': {}, 'errorCode': '',
           'usage': None, 'nativeLlmCalls': None, 'costUsd': None, 'latencyMs': None}
    started, client = time.perf_counter(), None
    stage, observation, correlation_id = 'preparation', None, ''
    try:
        fixture, pack = build(case, deployment)
        correlation_id = pack.request_id
        row['pack'] = pack.model_dump(mode='json')
        row['inputPackSha256'] = digest(row['pack'])
        row['context'] = describe_context(case['role_id'], fixture['state'], pack)
        binding = load_registry().resolve_role(case['role_id'])
        version = profile['workflows'][binding.key]['version']
        api_key = credentials.get_for('dev', binding.key)
        client = make_client(profile, binding)
        row['requestAttempted'] = True
        request_started = time.perf_counter()
        row['preparationMs'] = round((request_started - started) * 1000)
        stage = 'gateway'
        try:
            body, status, gateway_ms, _, breaker = client._request_validated_chat_bytes(
                binding, api_key=api_key, query=pack.query, user=pack.pseudonymous_user,
                inputs={'context_pack': pack, 'role_id': pack.use_case, 'correlation_id': pack.request_id,
                        'output_contract': binding.output_contract, 'workflow_version': version,
                        'knowledge_version': profile['knowledge_version']},
                workflow_id=version, expected_knowledge_dataset_id=profile['knowledge_dataset_id'])
        finally:
            # Same gateway-call wall-time boundary for success, failure and interruption.
            # Fixture/key/client setup, response verification and close are excluded.
            row['latencyMs'] = round((time.perf_counter() - request_started) * 1000)
        row['responseReceived'] = True
        observation = request_observation(transport_status='succeeded', http_status=status,
            latency_ms=gateway_ms, workflow_id=version)
        breaker.record_success()
        stage = 'response_decode'
        raw = _strict_json_loads(body)
        observation = request_observation(transport_status='succeeded', http_status=status,
            latency_ms=gateway_ms, workflow_id=version, raw=raw)
        stage = 'response_envelope'
        if not isinstance(raw, dict):
            raise KnowledgeEvalError('invalid_response')
        meta = raw.get('metadata') if isinstance(raw.get('metadata'), dict) else {}
        values = _usage_observation(meta)
        if all(value is not None for value in values) and values[0] + values[1] == values[2]:
            row['usage'] = dict(zip(('prompt_tokens', 'completion_tokens', 'total_tokens'), values))
        if status != 200 or raw.get('event') != 'message' or not isinstance(raw.get('answer'), str):
            raise KnowledgeEvalError('invalid_response')
        answer = raw['answer']
        row.update(response=safe_answer(answer), answerSha256=hashlib.sha256(answer.encode()).hexdigest())
        # Independently record retrieval and output failures; neither substitutes for quality.
        try:
            docs, _ = _project_resources(meta, identity, allow_empty=False)
            acl = set(docs).issubset(identity.allowed_document_ids_by_role.get(pack.use_case, ()))
            row['retrieval'] = {'documentIds': list(docs), 'ledgerVerified': True, 'roleAclOk': acl}
            if not acl:
                raise KnowledgeEvalError('retrieval_role_acl_mismatch')
            row['retrievalOk'] = True
        except Exception as exc:
            row['retrievalErrorCode'] = error_code(exc)
            row['retrievalFailure'] = evaluation_failure_diagnostics(exc,
                stage='retrieval_validation', correlation_id=correlation_id,
                response_telemetry=observation)
        stage = 'response_guard'
        remote, _ = ResponseGuard().validate_remote_role(answer, expected_role_id=pack.use_case,
            payload_model=pack.payload_schema(), evidence=pack,
            expected_knowledge_manifest_sha256=pack.knowledge_manifest_sha256)
        stage = 'binding_validation'
        proof_ok = hmac.compare_digest(hashlib.sha256(remote.knowledge_proof.encode()).hexdigest(),
                                       profile['knowledge_sentinel_sha256'])
        if (remote.workflow_version != version or remote.knowledge_version != profile['knowledge_version']
                or not proof_ok):
            raise KnowledgeEvalError('response_binding_mismatch')
        row.update(contractOk=True, status=remote.status)
    except KeyboardInterrupt as exc:
        row.update(errorCode='interrupted', status='error')
        row['failure'] = evaluation_failure_diagnostics(exc, stage=stage,
            correlation_id=correlation_id, response_telemetry=observation)
    except Exception as exc:
        row['errorCode'] = error_code(exc)
        row['failure'] = evaluation_failure_diagnostics(exc, stage=stage,
            correlation_id=correlation_id, response_telemetry=observation)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                row['closeFailed'] = True
                row['closeFailure'] = evaluation_failure_diagnostics(exc, stage='client_close',
                    correlation_id=correlation_id, response_telemetry=observation)
                if not row['errorCode']:
                    row.update(errorCode='client_close_failed', status='error', contractOk=False)
        row['attemptElapsedMs'] = round((time.perf_counter() - started) * 1000)
    row['automaticContractPass'] = row['contractOk'] and row['retrievalOk']
    row.update(evaluate_outcome(case, row))
    return row


def metrics(rows: list[dict]) -> tuple[dict, dict]:
    observations = summarize_attempts(rows)
    total = observations['overall']
    measured_ms = total['latencyMs']['total']
    return ({'attempts': len(rows), 'durationSeconds': measured_ms / 1000 if measured_ms is not None else None,
             'calls': total['responsesReceived'], 'callsMeaning': 'received Dify responses, not native LLM calls',
             'nativeLlmCalls': total['nativeLlmCalls']['total'],
             **{k: total['tokens'][v]['total'] for k, v in (
                 ('promptTokens', 'prompt_tokens'), ('completionTokens', 'completion_tokens'), ('totalTokens', 'total_tokens'))},
             'cachedTokens': None, 'costUsd': None,
             'outcomes': dict(sorted(Counter(row.get('outcome', 'unassessed') for row in rows).items()))}, observations)


def artifact_path(group: str, label: str) -> Path:
    cache = (ROOT / '.cache/agent-evaluation').resolve()
    path = (cache / group / (label + '.json')).resolve()
    if not cache.is_relative_to(ROOT.resolve()) or not path.is_relative_to(cache):
        raise ValueError('artifact must remain inside workspace evaluation cache')
    return path


def run(label: str, selected=None) -> Path:
    endpoint = _require_network_handoff()  # MUST precede profile/key access.
    if label not in {'baseline', 'candidate'}:
        raise ValueError('invalid label')
    cases = select_cases(selected)
    if not hmac.compare_digest(endpoint, _destination_identity(_credential_free_dev_api_base())):
        raise KnowledgeEvalError('destination_changed')
    profile, deployment, _, identity = _preflight()
    if not hmac.compare_digest(endpoint, _destination_identity(profile['base_url'])):
        raise KnowledgeEvalError('destination_changed')
    # Validate all inputs before obtaining transport keys; preflight itself may
    # inspect the secure store to prove dev/prod credential separation.
    for case in cases:
        build(case, deployment)
    sources = source_binding()
    group = 'maxis-roles-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    os.environ.update({
        'LTM_EVAL_RUN_GROUP_ID': group, 'LTM_EVAL_RUN_KIND': 'exploratory',
        'LTM_EVAL_REPETITIONS': '1', 'LTM_EVAL_REPEAT_INDEX': '1', 'LAKE_AGENT_PROVIDER': 'dify',
        'LTM_EVAL_EFFECTIVE_PROVIDER_IDENTITY_SHA256': endpoint,
        'LTM_EVAL_DATA_PROFILE': 'repository-synthetic-role-component-v1',
        'LTM_EVAL_RUNTIME_PROFILE': 'dev-inactive-stateless-role-component-v1',
        'LTM_EVAL_CACHE_POLICY': 'no-client-history-no-local-state',
        'LTM_EVAL_PROCESS_ISOLATION': 'separate-process-fresh-pack-per-case',
        'LTM_EVAL_RETRY_POLICY': 'no-retry-no-fallback-stop-on-transport-failure',
        'LTM_EVAL_CANDIDATE_ORDER_INDEX': '1' if label == 'baseline' else '2',
        'LTM_AGENT_STRUCTURED_OUTPUT_BACKEND': 'dify', 'LTM_AGENT_STRUCTURED_OUTPUT_FALLBACK': 'forbid',
    })
    import yaml
    configured_model = yaml.safe_load((ROOT / 'deploy/maxis-dify/environments/dev-2026.09.yml').read_text(encoding='utf-8'))['model']['name']
    common, reviews = review_specs('maxis-role-component')
    metadata = build_run_metadata(suite='maxis-role-component', battery_version=BATTERY_VERSION,
        cases=load_remote_cases(), selected_case_ids=[c['id'] for c in cases], model=configured_model,
        simple_model='not-used', prompt_version=profile['workflow_bundle_version'],
        suite_review_elements=common, case_review_specs=reviews,
        checker_dependencies=[{'file': name, 'sha256': sources[name]} for name in HARNESS_FILES])
    metadata.update(componentScope='synthetic guarded Role inputs/outputs; not complete workflows or UI',
                    comparableToSourceSuite=False, qualificationEligible=False,
                    maxisBinding={'deployment': deployment, 'sourceFiles': sources,
                                  'profileFingerprint': profile['fingerprint'], 'workflows': profile['workflows']},
                    maxRequests=len(cases), modelObservation='configured in pinned deployment, not per-node measured')
    metadata['latencyBoundary'] = 'gateway call wall time including client input validation; excludes fixture/key/client setup, response validation and close; not native model time or TTFT'
    metadata['outcomeMeaning'] = 'declared output presence or expected safety boundary; not semantic quality or usable completion'
    metadata['benchmarkKey'] = digest({'base': metadata['benchmarkKey'], 'harness': {k: sources[k] for k in HARNESS_FILES}})
    metadata['candidateKey'] = digest({'base': metadata['candidateKey'], 'maxis': metadata['maxisBinding']})
    metadata['comparabilityKey'] = digest({k: metadata[k] for k in ('benchmarkKey', 'candidateKey')})
    metadata['qualificationIneligibilityReasons'].append('synthetic Role components; no end-to-end or usefulness qualification')
    path = artifact_path(group, label)
    reserve_raw_result_path(path)
    artifact = {'metadata': metadata, 'cases': [], 'complete': False, 'releaseEligible': False,
                'semanticCorrectnessAssessed': False, 'notAttemptedCaseIds': [c['id'] for c in cases]}
    artifact['metrics'], artifact['observations'] = metrics([])
    write_raw_result(path, artifact)
    for case in cases:
        if source_binding() != sources or not _binding_unchanged(profile, deployment):
            artifact['stopReason'] = 'binding_changed'
            break
        artifact['inFlightCaseId'] = case['id']
        artifact['notAttemptedCaseIds'].remove(case['id'])
        write_raw_result(path, artifact)
        row = attempt(case, profile, deployment, identity)
        artifact['cases'].append(row)
        artifact['inFlightCaseId'] = None
        artifact['metrics'], artifact['observations'] = metrics(artifact['cases'])
        write_raw_result(path, artifact)
        print(json.dumps({k: row.get(k) for k in (
            'caseId', 'status', 'errorCode', 'contractOk', 'retrievalOk',
            'outcome', 'outcomeContractPass')}), flush=True)
        if row['errorCode'] in STOP_ERRORS:
            artifact['stopReason'] = row['errorCode']
            break
    artifact['bindingUnchanged'] = source_binding() == sources and _binding_unchanged(profile, deployment)
    artifact['complete'] = (not artifact['notAttemptedCaseIds'] and artifact['bindingUnchanged']
                            and not artifact.get('stopReason'))
    write_raw_result(path, artifact)
    return path


def main(argv=None) -> int:
    args = arguments(argv)
    try:
        path = run(args.label, args.cases)
        result = json.loads(path.read_text(encoding='utf-8'))
        print(json.dumps({'artifact': str(path.relative_to(ROOT)), 'complete': result['complete']}), flush=True)
        return 0 if result['complete'] else 3
    except Exception as exc:
        print(json.dumps({'errorCode': error_code(exc), 'complete': False}), file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
