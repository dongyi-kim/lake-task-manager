"""Deterministic audit of one preregistered arm/scenario/mode after direct review.

The caller supplies all repetitions, the original schedule's case IDs/order and
hashes of retained source snapshots. This never substitutes for direct reading or
claims that the complete A/B/recovery matrix, rollout or release is qualified.
"""
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
import re

from tools.agent_eval_protocol import (aggregate_human_reviews, load_protocol,
                                     specialized_review_spec_for_case)
from tools.codex_mcp_eval.runner import GATE


def case_record_sha256(record):
    return sha256(json.dumps(record, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode('utf-8')).hexdigest()


def audit_cohort(runs, reviews, *, evaluator, expected_case_ids, source_snapshot_hashes,
                 expected_order_by_repeat, expected_commit, expected_runtime, planned_repetitions=5):
    """Audit a complete cohort; baseline design validity is separate from quality."""
    design, quality = set(), set()
    expected = set(expected_case_ids)
    minimum = load_protocol()['qualification']['minimumRepetitions']
    if (not expected or len(expected) != len(expected_case_ids)
            or type(planned_repetitions) is not int or planned_repetitions < minimum
            or not re.fullmatch(r'[0-9a-f]{40}', expected_commit)
            or not {'model', 'reasoningEffort', 'skillCondition'} <= set(expected_runtime)):
        return {'designPassed': False, 'qualityPassed': False, 'qualificationClaim': False,
                'humanMean': None, 'failures': ['invalid_preregistered_design']}
    repetitions = set(range(1, planned_repetitions + 1))
    balance = Counter(expected_order_by_repeat.values())
    if (set(expected_order_by_repeat) != repetitions or set(balance) != {1, 2}
            or max(balance.values()) - min(balance.values()) > 1):
        design.add('invalid_preregistered_order')
    if len(runs) != planned_repetitions:
        design.add('incomplete_repetitions')
    identities, repeats, common = {}, [], {'benchmarkKey': set(), 'candidateKey': set(), 'candidateCommit': set()}
    jira_requests, automatic_pass = 0, 0
    for raw in runs:
        metadata = raw.get('evaluation') or {}
        run, battery = metadata.get('run') or {}, metadata.get('battery') or {}
        repeat = run.get('repeatIndex')
        order = run.get('candidateOrderIndex')
        if isinstance(order, str) and re.fullmatch(r'[1-9][0-9]*', order):
            order = int(order)
        repeats.append(repeat)
        if (metadata.get('qualificationEligible') is not True or run.get('runKind') != 'qualification'
                or type(repeat) is not int or type(order) is not int
                or run.get('repetitions') != planned_repetitions
                or order != expected_order_by_repeat.get(repeat)
                or run.get('gitStatusAvailable') is not True or run.get('candidateDirty') is not False):
            design.add('invalid_run_design')
        if run.get('candidateCommit') != expected_commit:
            design.add('preregistered_commit_mismatch')
        external = run.get('externalRuntime') or {}
        if (run.get('model') != expected_runtime['model']
                or ('model' in external and external['model'] != run.get('model')) or any(
                external.get(name) != value for name, value in expected_runtime.items() if name != 'model')):
            design.add('preregistered_runtime_mismatch')
        for name in common:
            value = run.get(name) if name == 'candidateCommit' else metadata.get(name)
            if not isinstance(value, str) or not value:
                design.add('missing_comparison_identity')
            else:
                common[name].add(value)
        if raw.get('qualityGate') != GATE:
            design.add('gate_contract_mismatch')
        rows, summary = raw.get('cases') or [], raw.get('summary') or {}
        selected = battery.get('selectedCaseIds') or []
        ids = [row.get('id') for row in rows]
        if (raw.get('executionComplete') is not True or raw.get('infrastructureError')
                or battery.get('fullCaseCount') != len(expected) or len(selected) != len(expected)
                or set(selected) != expected or len(ids) != len(expected) or set(ids) != expected
                or summary.get('completed') != len(expected) or summary.get('total') != len(expected)):
            design.add('incomplete_case_coverage')
        upstream = [call for row in rows for call in row.get('upstreamCalls') or []]
        counted = sum(call.get('service') == 'jira' for call in upstream)
        measurement = summary.get('jiraApi') or {}
        if (any(not isinstance(row.get('upstreamCalls'), list) for row in rows)
                or any(call.get('service') not in {'jira', 'confluence'} for call in upstream)
                or measurement.get('observed') is not True or type(measurement.get('requests')) is not int
                or measurement['requests'] != counted):
            design.add('jira_measurement_incomplete')
        else:
            jira_requests += counted
        passed = sum(row.get('automaticPass') is True for row in rows)
        if summary.get('automaticPass') != passed:
            design.add('automatic_summary_mismatch')
        automatic_pass += passed
        for row in rows:
            identity = (raw.get('suite'), row.get('id'), repeat)
            if identity in identities:
                design.add('duplicate_case_identity')
            spec = specialized_review_spec_for_case(battery.get('specializedReview') or {}, row['id'])
            identities[identity] = (row, spec)
            contract, isolation = row.get('automaticContract') or {}, row.get('isolation') or {}
            if row.get('automaticPass') is not True or contract.get('pass') is not True:
                quality.add('automatic_gate_failed')
            if row.get('automaticPass') != contract.get('pass'):
                design.add('automatic_record_mismatch')
            if (isolation.get('worldUnchanged') is not True or isolation.get('providerStoreUnchanged') is not True
                    or (row.get('output') or {}).get('forbiddenToolEvents')):
                quality.add('read_only_boundary_failed')
    if set(repeats) != repetitions or len(set(repeats)) != len(repeats):
        design.add('incomplete_or_duplicate_repetitions')
    if any(len(values) != 1 for values in common.values()):
        design.add('comparison_identity_mismatch')
    linked, reviewed = [], set()
    for review in reviews:
        identity = (review.get('suite'), review.get('caseId'), review.get('repeatIndex'))
        if identity in reviewed or identity not in identities:
            design.add('duplicate_or_foreign_review')
            continue
        reviewed.add(identity)
        record, spec = identities[identity]
        snapshot = source_snapshot_hashes.get(record.get('sourceSnapshot'))
        if (review.get('caseRecordSha256') != case_record_sha256(record) or not snapshot
                or review.get('sourceSnapshotSha256') != snapshot):
            design.add('review_source_mismatch')
            continue
        if review.get('specializedReviewSpec') != spec:
            design.add('review_spec_mismatch')
            continue
        excerpt = review.get('outputExcerpt')
        if not isinstance(excerpt, str) or not excerpt or excerpt not in (record.get('output') or {}).get('reply', ''):
            design.add('review_excerpt_mismatch')
            continue
        facts = review.get('materialFalseFacts')
        if not isinstance(facts, list) and (type(facts) is not int or facts < 0):
            design.add('missing_material_fact_audit')
        elif facts:
            quality.add('material_false_facts')
        required = set((record.get('automaticContract') or {}).get('semanticReviewRequirements') or [])
        declared = {element['id'] for element in spec['elements']}
        if not required <= declared:
            design.add('semantic_contract_missing')
        required |= declared & set(GATE['zeroMajorSemanticElements'])
        progress = ((record.get('output') or {}).get('structuredAnswer') or {}).get('progress')
        for name in required:
            result = (review.get('specializedReviewResults') or {}).get(name) or {}
            status = str(result.get('status') or '').strip().lower()
            if status == 'na' and (name == 'uncertainty_disclosure'
                    or name == 'progress_denominator' and progress is not None):
                design.add('required_semantic_review_skipped')
        linked.append(review)
    if reviewed != set(identities) or len(reviews) != len(identities):
        design.add('incomplete_review_coverage')
    aggregation = None
    exact_mean = None
    try:
        aggregation = aggregate_human_reviews(linked, evaluator=evaluator)
    except (TypeError, ValueError):
        design.add('invalid_direct_review')
    if aggregation:
        scores = [Decimal(str(item['caseScore'])) for item in aggregation['observations']]
        exact_mean = sum(scores) / len(scores)
        if sum(scores) < Decimal(str(GATE['humanMeanMinimum'])) * len(scores):
            quality.add('human_mean_below_gate')
        if aggregation['cappedFailureCount']:
            quality.add('critical_review_failure')
        for observation in aggregation['observations']:
            for name, result in observation['specializedReviewResults'].items():
                if name in GATE['zeroMajorSemanticElements'] and result['status'] == 'major':
                    quality.add('major_semantic:' + name)
    return {'designPassed': not design, 'qualityPassed': not design and not quality,
            'qualificationClaim': False, 'failures': sorted(design | quality),
            'expectedObservations': len(expected) * planned_repetitions, 'observations': len(identities),
            'automaticPass': automatic_pass, 'reviewedObservations': len(linked),
            'humanMean': aggregation['overallScore'] if aggregation else None,
            'humanMeanUnrounded': float(exact_mean) if exact_mean is not None else None,
            'jiraApiRequests': None if 'jira_measurement_incomplete' in design else jira_requests,
            'humanAggregation': aggregation}
