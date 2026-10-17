"""Exploratory synthetic Editor component evaluation, never an LTM activation gate.

The only selectable inputs are the repository-owned five-case adapter. This bypasses
no production runtime policy: it calls the already bounded gateway in a separate
process, without app state, tools, history, provider Store, or approval capability.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_knowledge_evals import (  # noqa: E402
    _credential_free_dev_api_base, _destination_identity, _preflight,
    _require_network_handoff,
)
from app.agent.maxis import credentials, profiles  # noqa: E402
from app.agent.maxis.contracts import (  # noqa: E402
    EditorDraftV1, EvidencePackV1, EvidenceScopeV1, EvidenceSourceV1, RoleContextV1,
    canonical_payload_schema,
)
from app.agent.maxis.context_guard import (  # noqa: E402
    _SENSITIVE_PATTERNS, canonical_evidence_pack_json, forbidden_authority_key,
    validate_pack_safety,
)
from app.agent.maxis.knowledge_eval import (  # noqa: E402
    evaluation_source_file_sha256, make_client, safe_remote_error,
)
from app.agent.maxis.errors import evaluation_failure_diagnostics  # noqa: E402
from app.agent.maxis.response_guard import ResponseGuard  # noqa: E402
from app.agent.maxis.registry import load_registry  # noqa: E402
from app.agent.maxis.telemetry import request_observation  # noqa: E402
from tools.agent_eval_protocol import (  # noqa: E402
    build_run_metadata, quantitative_metrics, reserve_raw_result_path, write_raw_result,
)

COMPONENT_VERSION = '4.0.0'


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def build_pack(spec: dict, manifest: str, request_id: str, observed: datetime,
               query_policy: str = 'current') -> EvidencePackV1:
    from tools.maxis_editor_eval_cases import project_case, validate_case_spec
    validate_case_spec(spec)
    projection = project_case(spec)
    # The component must preserve the explicit target/mode/seed, just as the app
    # carries those fields in Editor's bounded Role context. They are requests,
    # never external facts. Both candidates receive this exact same projection.
    intent = json.dumps(projection['input'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    query = 'Canonical LTM role editor_author. Process this guarded user intent:\n' + projection['instruction']
    if query_policy == 'role-terms':
        from app.agent.maxis.retrieval_query import enrich_role_query
        query = enrich_role_query(query, 'editor_author', spec['payload_schema'])
    elif query_policy != 'current':
        raise ValueError('unknown retrieval query policy')
    schema, schema_hash = canonical_payload_schema(spec['payload_schema'])
    sources = [EvidenceSourceV1(**row, retrieved_at=observed, updated_at=observed)
               for row in projection['evidence']]
    sources.append(EvidenceSourceV1(
        source_id='eval.editor.intent', kind='ltm', canonical_id='ltm:user-intent:editor-eval',
        title='Synthetic author request and existing seed', scope='synthetic-evaluation',
        content=intent, content_sha256=hashlib.sha256(intent.encode('utf-8')).hexdigest(),
        retrieved_at=observed, updated_at=observed,
    ))
    pack = EvidencePackV1(
        request_id=request_id, use_case='editor_author', query=query,
        pseudonymous_user='ltm_' + hashlib.sha256(request_id.encode()).hexdigest()[:16],
        requested_at=observed, scope=EvidenceScopeV1(), sources=tuple(sources),
        role_context=(RoleContextV1(context_id='editor_request', name='editor_request',
            content_json=intent, content_sha256=hashlib.sha256(intent.encode()).hexdigest(),
            observed_at=observed),),
        freshness='fresh', partial=False, flags=('synthetic_evaluation',),
        knowledge_manifest_sha256=manifest, payload_schema_json=schema,
        payload_schema_sha256=schema_hash,
    )
    if len(pack.query.encode('utf-8')) > 1024 or len(canonical_evidence_pack_json(pack).encode('utf-8')) > 6144:
        raise ValueError('synthetic editor pack exceeds reviewed wire budget')
    validate_pack_safety(pack)
    return pack


def safe_answer(answer: str) -> dict:
    """Retain actual synthetic text, but never the release sentinel/authority material."""
    try:
        value = json.loads(answer)
    except (ValueError, TypeError):
        return {'parseError': True, 'answerSha256': hashlib.sha256(answer.encode()).hexdigest()}
    if not isinstance(value, dict):
        return {'parseError': True}
    proof = value.get('knowledge_proof')
    # Redact the complete proof wherever it occurs, including embedded whitespace.
    def redact(item):
        if isinstance(item, str):
            if isinstance(proof, str) and proof:
                item = item.replace(proof, '[REDACTED_SENTINEL]')
            item = re.sub(r'LTM_RELEASE_SENTINEL_V1=[^\s"<>]+', '[REDACTED_SENTINEL]', item)
            for _, pattern in _SENSITIVE_PATTERNS:
                item = pattern.sub('[REDACTED_CREDENTIAL]', item)
            return item
        if isinstance(item, list):
            return [redact(v) for v in item]
        if isinstance(item, dict):
            return {redact(k): redact(v) for k, v in item.items() if not forbidden_authority_key(k) and k not in {
                'knowledge_proof', 'api_key', 'authorization', 'approval_token', 'reasoning', 'reasoning_content',
            }}
        return item
    return redact(value)


def metrics_for(rows: list[dict]) -> dict:
    observed = [r.get('usage') for r in rows]
    complete = all(isinstance(u, dict) and all(
        type(u.get(key)) is int and u[key] >= 0
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
    ) for u in observed)
    metrics = quantitative_metrics(
        attempts=len(rows), duration_seconds=sum(r.get('latencyMs', 0) for r in rows) / 1000,
        calls=sum(r.get('responseReceived') is True for r in rows),
        prompt_tokens=sum((u or {}).get('prompt_tokens', 0) for u in observed),
        completion_tokens=sum((u or {}).get('completion_tokens', 0) for u in observed),
        total_tokens=sum((u or {}).get('total_tokens', 0) for u in observed),
        cached_tokens=0, cost_usd=None,
    )
    metrics['cachedTokens'] = None  # Dify usage projection does not observe caching.
    metrics['tokenUsageComplete'] = bool(rows) and complete
    metrics['callsMeaning'] = 'received Dify responses, not internal model step count'
    metrics['nativeLlmCalls'] = None  # No native step-count observation on this surface.
    if not complete:
        for key in ('promptTokens', 'completionTokens', 'totalTokens'):
            metrics[key] = None
    return metrics


def render_component(payload: dict, person_map: dict | None = None) -> dict:
    """Normalize markup and resolve only supplied synthetic person tokens, offline.

    No app ticket badge helper is used: it would consult the user's Jira scopes.
    Ticket/document links remain the model's output and are directly reviewable.
    """
    from app.agent.references import normalize_editor_markup
    draft = EditorDraftV1.model_validate_json(json.dumps(payload), strict=True)
    if draft.unsupported_claims:
        return {'ok': False, 'contentConflict': True, 'html': ''}
    normalized = normalize_editor_markup(draft.content_html)
    if not normalized.ok:
        return {'ok': False, 'contentConflict': True, 'html': ''}
    people = person_map or {}

    class SyntheticMentions(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.parts = []

        def handle_starttag(self, tag, attrs):
            self.parts.append(self.get_starttag_text())

        handle_startendtag = handle_starttag

        def handle_endtag(self, tag):
            self.parts.append(f'</{tag}>')

        def handle_data(self, data):
            def replace(match):
                token = match.group(0)
                if token not in people:
                    raise ValueError('unknown synthetic person reference')
                person = html.escape(people[token], quote=True)
                return f'<span data-type="mention" data-id="{person}">@{person}</span>'
            self.parts.append(re.sub(r'\[\[person:[^\]]+\]\]', replace, data))

        def handle_entityref(self, name):
            self.parts.append(f'&{name};')

        def handle_charref(self, name):
            self.parts.append(f'&#{name};')

    parser = SyntheticMentions()
    parser.feed(normalized.html)
    parser.close()
    return {'ok': True, 'html': ''.join(parser.parts), 'references': [],
            'rendererScope': 'normalization-plus-synthetic-mentions-no-reference-resolver'}


def _source_binding() -> dict:
    sources = evaluation_source_file_sha256()
    for relative in (
        'tools/maxis_editor_eval_cases.py', 'deploy/maxis-dify/run_editor_evals.py',
        'deploy/maxis-dify/launch_editor_evals.py', 'deploy/maxis-dify/prompts/editor_author.md',
        'app/agent/maxis/retrieval_query.py',
        'app/agent/maxis/errors.py', 'app/agent/maxis/telemetry.py',
    ):
        sources[relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    return sources


def run(label: str) -> Path:
    identity = _require_network_handoff()
    if not hmac.compare_digest(identity, _destination_identity(_credential_free_dev_api_base())):
        raise ValueError('destination changed before profile access')
    profile, deployment, _, _ = _preflight()
    if not hmac.compare_digest(identity, _destination_identity(profile['base_url'])):
        raise ValueError('destination changed before request')
    from tools.maxis_editor_eval_cases import load_cases, check_output
    cases = load_cases()
    observed = datetime.now(timezone.utc)
    group = 'maxis-editor-' + observed.strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    os.environ.update({
        'LTM_EVAL_RUN_GROUP_ID': group, 'LTM_EVAL_RUN_KIND': 'exploratory',
        'LTM_EVAL_REPETITIONS': '1', 'LTM_EVAL_REPEAT_INDEX': '1',
        'LAKE_AGENT_PROVIDER': 'dify', 'LTM_EVAL_EFFECTIVE_PROVIDER_IDENTITY_SHA256': identity,
        'LTM_EVAL_DATA_PROFILE': 'repository-synthetic-editor-component-v1',
        'LTM_EVAL_RUNTIME_PROFILE': 'dev-inactive-stateless-component-v1',
        'LTM_EVAL_CACHE_POLICY': 'no-client-history-no-local-state',
        'LTM_EVAL_PROCESS_ISOLATION': 'separate-process-stateless-fresh-pack-each-case',
        'LTM_EVAL_RETRY_POLICY': 'no-retry-no-fallback-all-attempts-retained',
        'LTM_EVAL_CANDIDATE_ORDER_INDEX': '1' if label == 'baseline' else '2',
        'LTM_AGENT_STRUCTURED_OUTPUT_BACKEND': 'dify',
        'LTM_AGENT_STRUCTURED_OUTPUT_FALLBACK': 'forbid',
    })
    # Validate every canonical case and byte budget before reading an app key.
    query_policy = 'role-terms'
    packs = [build_pack(c, deployment['knowledge_manifest_sha256'],
                        'eval_' + uuid.uuid4().hex[:24], observed, query_policy) for c in cases]
    sources = _source_binding()
    binding = load_registry().resolve_role('editor_author')
    workflow_version = profile['workflows']['editor_author']['version']
    from tools.agent_eval_review_specs import review_specs
    suite_review, case_review = review_specs('editor')
    metadata = build_run_metadata(
        suite='maxis-editor-component', battery_version=COMPONENT_VERSION,
        cases=[{'id': c['case_id'], **c} for c in cases],
        selected_case_ids=[c['case_id'] for c in cases],
        model='gpt-5.4-mini', simple_model='not-used',
        prompt_version=profile['workflow_bundle_version'],
        suite_review_elements=suite_review,
        case_review_specs={c['case_id']: case_review[c['case_id']] for c in cases},
    )
    metadata['componentScope'] = 'EditorDraft plus pure markup; not full compose or UI'
    metadata['comparableToSourceSuite'] = False
    metadata['maxisBinding'] = {
        'deployment': deployment, 'sourceFiles': sources,
        'publishedWorkflowId': workflow_version, 'profileFingerprint': profile['fingerprint'],
        'retrievalQueryPolicy': 'structured-context-' + query_policy,
    }
    metadata['benchmarkKey'] = _digest({'base': metadata['benchmarkKey'], 'componentHarness': {
        key: value for key, value in sources.items() if key in {
            'tools/maxis_editor_eval_cases.py', 'deploy/maxis-dify/run_editor_evals.py',
            'deploy/maxis-dify/launch_editor_evals.py',
            'app/agent/maxis/errors.py', 'app/agent/maxis/telemetry.py',
        }
    }})
    metadata['candidateKey'] = _digest({'base': metadata['candidateKey'], 'maxis': metadata['maxisBinding']})
    metadata['comparabilityKey'] = _digest({k: metadata[k] for k in ('benchmarkKey', 'candidateKey')})
    metadata['qualificationEligible'] = False
    metadata['qualificationIneligibilityReasons'].append('exploratory synthetic component only')
    path = ROOT / '.cache/agent-evaluation' / group / (label + '.json')
    reserve_raw_result_path(path)
    artifact = {'metadata': metadata, 'label': label, 'cases': [], 'complete': False,
                'semanticCorrectnessAssessed': False, 'releaseEligible': False}
    write_raw_result(path, artifact)
    api_key = credentials.get_for('dev', 'editor_author')
    for case, pack in zip(cases, packs):
        row = {'caseId': case['case_id'], 'input': case, 'pack': pack.model_dump(mode='json'),
               'response': {}, 'rendered': {}, 'automaticPass': False, 'errorCode': '',
               'nativeLlmCalls': None}
        started = time.perf_counter()
        client = make_client(profile, binding)
        stage, observation = 'gateway', None
        try:
            body, status_code, gateway_ms, expected_dataset, breaker = client._request_validated_chat_bytes(
                binding, api_key=api_key, query=pack.query, user=pack.pseudonymous_user,
                inputs={'context_pack': pack, 'role_id': pack.use_case, 'correlation_id': pack.request_id,
                        'output_contract': binding.output_contract, 'workflow_version': workflow_version,
                        'knowledge_version': profile['knowledge_version']},
                workflow_id=workflow_version, expected_knowledge_dataset_id=profile['knowledge_dataset_id'])
            observation = request_observation(transport_status='succeeded', http_status=status_code,
                latency_ms=gateway_ms, workflow_id=workflow_version)
            breaker.record_success()
            stage = 'response_decode'
            from app.agent.maxis.client import _strict_json_loads, _retrieval_summary
            from app.agent.maxis.knowledge_eval import _usage_observation
            raw = _strict_json_loads(body)
            observation = request_observation(transport_status='succeeded', http_status=status_code,
                latency_ms=gateway_ms, workflow_id=workflow_version, raw=raw)
            stage = 'response_envelope'
            if status_code != 200 or not isinstance(raw, dict) or raw.get('event') != 'message' or not isinstance(raw.get('answer'), str):
                raise ValueError('invalid Dify response')
            row['responseReceived'] = True
            answer = raw['answer']
            row['response'] = safe_answer(answer)
            row['answerSha256'] = hashlib.sha256(answer.encode()).hexdigest()
            remote_metadata = raw.get('metadata') if isinstance(raw.get('metadata'), dict) else {}
            usage_values = _usage_observation(remote_metadata)
            if all(value is not None for value in usage_values):
                row['usage'] = dict(zip(('prompt_tokens', 'completion_tokens', 'total_tokens'), usage_values))
            stage = 'retrieval_validation'
            row['retrieval'] = _retrieval_summary(remote_metadata, expected_dataset_id=expected_dataset).model_dump()
            stage = 'response_guard'
            remote, _ = ResponseGuard().validate_remote_role(answer,
                expected_role_id='editor_author', payload_model=pack.payload_schema(), evidence=pack,
                expected_knowledge_manifest_sha256=pack.knowledge_manifest_sha256)
            stage = 'binding_validation'
            proof_ok = hmac.compare_digest(hashlib.sha256(remote.knowledge_proof.encode()).hexdigest(),
                                           profile['knowledge_sentinel_sha256'])
            if remote.workflow_version != workflow_version or remote.knowledge_version != profile['knowledge_version'] or not proof_ok:
                raise ValueError('remote binding mismatch')
            row['contractOk'] = True
            stage = 'rendering'
            from tools.maxis_editor_eval_cases import project_case
            row['rendered'] = render_component(remote.payload, project_case(case)['person_map']) if remote.status == 'ok' else {
                'ok': False, 'needsInfo': remote.status == 'needs_input', 'error': list(remote.missing_inputs), 'html': ''}
            stage = 'output_check'
            row['checks'] = check_output(case['case_id'], row['rendered'])
            row['automaticPass'] = bool(row['checks']['automaticPass'])
        except Exception as exc:
            row['errorCode'] = safe_remote_error(exc)
            row['failure'] = evaluation_failure_diagnostics(exc, stage=stage,
                correlation_id=pack.request_id, response_telemetry=observation)
        row['latencyMs'] = round((time.perf_counter() - started) * 1000)
        row['metrics'] = metrics_for([row])
        artifact['cases'].append(row)
        artifact['metrics'] = metrics_for(artifact['cases'])
        write_raw_result(path, artifact)
        print(json.dumps({'caseId': row['caseId'], 'pass': row['automaticPass'],
                          'status': row['response'].get('status'), 'errorCode': row['errorCode']}), flush=True)
    artifact['bindingUnchanged'] = sources == _source_binding() and profiles.resolve('dev') == profile
    artifact['complete'] = True
    artifact['automaticPassCount'] = sum(c['automaticPass'] for c in artifact['cases'])
    write_raw_result(path, artifact)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', choices=('baseline', 'candidate'), required=True)
    args = parser.parse_args(argv)
    try:
        path = run(args.label)
        print(json.dumps({'artifact': str(path.relative_to(ROOT))}), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({'errorCode': safe_remote_error(exc), 'complete': False}), file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
