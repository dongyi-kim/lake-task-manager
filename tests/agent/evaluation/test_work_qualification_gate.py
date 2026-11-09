"""A high human mean cannot substitute for complete, linked, passing evidence."""
import copy
from hashlib import sha256
import json

import pytest

from tools.agent_eval_protocol import load_protocol
from tools.codex_mcp_eval.runner import GATE


EVALUATOR = {'agentFamily': 'codex', 'agentModel': 'test-direct-review',
             'directRawOutputReview': True, 'ltmLlmUsedAsJudge': False}
CASE = 'WORK-R10-normal'
ORDER = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2}
RUNTIME = {'model': 'gpt-6.1-sol', 'reasoningEffort': 'high',
           'skillCondition': 'candidate', 'skillTransport': 'default'}


def digest(record):
    return sha256(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def cohort():
    runs, reviews, snapshots = [], [], {}
    element = {'id': 'progress_denominator', 'dimension': 'factual_grounding',
               'question': 'Is the count denominator clear?', 'majorWhen': 'Hidden or wrong unit',
               'expected': {'semanticReviewRequired': True}, 'evidenceSources': ['output.reply']}
    generic = {**copy.deepcopy(element), 'id': 'source_facts', 'question': 'Were source facts preserved?'}
    spec = {'goal': 'Synthetic complete count review', 'elements': [element, generic]}
    for repeat in range(1, 6):
        snapshot = f'source-{repeat}.json'
        snapshots[snapshot] = sha256(snapshot.encode()).hexdigest()
        record = {'id': CASE, 'sourceSnapshot': snapshot, 'upstreamCalls': [],
                  'automaticPass': True, 'automaticContract': {'pass': True, 'semanticReviewRequirements': ['progress_denominator']},
                  'output': {'reply': '전체 티켓 3건 중 1건 완료입니다.', 'forbiddenToolEvents': []},
                  'isolation': {'worldUnchanged': True, 'providerStoreUnchanged': True}}
        runs.append({'suite': 'codex-mcp-work', 'executionComplete': True, 'qualityGate': copy.deepcopy(GATE),
            'evaluation': {'qualificationEligible': True, 'benchmarkKey': 'benchmark', 'candidateKey': 'candidate',
                'run': {'runKind': 'qualification', 'candidateCommit': 'a' * 40, 'gitStatusAvailable': True,
                        'candidateDirty': False, 'repeatIndex': repeat, 'repetitions': 5},
                'battery': {'fullCaseCount': 1, 'selectedCaseIds': [CASE],
                    'specializedReview': {'suiteElements': [], 'cases': {CASE: spec}}}},
            'summary': {'completed': 1, 'total': 1, 'automaticPass': 1, 'jiraApi': {'observed': True, 'requests': 0}},
            'cases': [record]})
        runs[-1]['evaluation']['run']['candidateOrderIndex'] = ORDER[repeat]
        runs[-1]['evaluation']['run'].update(model=RUNTIME['model'], externalRuntime=copy.deepcopy(RUNTIME))
        reviews.append({'suite': 'codex-mcp-work', 'caseId': CASE, 'repeatIndex': repeat,
            'caseRecordSha256': digest(record), 'sourceSnapshotSha256': snapshots[snapshot],
            'outputExcerpt': '티켓 3건 중 1건', 'materialFalseFacts': 0, 'failureCodes': [],
            'dimensionScores': {dimension['id']: 5 for dimension in load_protocol()['humanRubric']['dimensions']},
            'dimensionRationales': {dimension['id']: 'Synthetic fixture satisfies this dimension.'
                for dimension in load_protocol()['humanRubric']['dimensions']},
            'checklistResults': {dimension['id']: {item['id']: {'status': 'pass', 'evidence': 'Synthetic source/answer fixture.'}
                for item in dimension['checklist']} for dimension in load_protocol()['humanRubric']['dimensions']},
            'specializedReviewSpec': copy.deepcopy(spec),
            'specializedReviewResults': {
                'progress_denominator': {'status': 'pass', 'evidence': 'Three tickets, one complete.'},
                'source_facts': {'status': 'pass', 'evidence': 'Synthetic source facts preserved.'}}})
    return runs, reviews, snapshots


def audit(runs, reviews, snapshots, **kwargs):
    from tools.codex_mcp_eval.qualification import audit_cohort
    return audit_cohort(runs, reviews, evaluator=EVALUATOR, expected_case_ids=[CASE],
                        source_snapshot_hashes=snapshots, expected_order_by_repeat=ORDER,
                        expected_commit='a' * 40, expected_runtime=RUNTIME, **kwargs)


def test_complete_linked_five_repeat_cohort_passes_without_claiming_whole_qualification():
    result = audit(*cohort())
    assert result['designPassed'] and result['qualityPassed'] and result['humanMean'] == 5
    assert result['qualificationClaim'] is False


def test_real_producer_order_strings_and_authored_finding_arrays_are_supported():
    runs, reviews, snapshots = cohort()
    for run, review in zip(runs, reviews):
        run['evaluation']['run']['candidateOrderIndex'] = str(run['evaluation']['run']['candidateOrderIndex'])
        review['materialFalseFacts'] = []
    result = audit(runs, reviews, snapshots)
    assert result['designPassed'] and result['qualityPassed']


def test_enumerated_material_finding_blocks_a_high_mean():
    runs, reviews, snapshots = cohort()
    reviews[0]['materialFalseFacts'] = [{'claim': 'Synthetic unsupported business fact', 'evidence': 'Not in source.'}]
    result = audit(runs, reviews, snapshots)
    assert result['designPassed'] and not result['qualityPassed'] and 'material_false_facts' in result['failures']


@pytest.mark.parametrize('element', ['progress_denominator', 'uncertainty_disclosure'])
@pytest.mark.parametrize('status', ['na', 'NA', ' na '])
def test_applicable_critical_review_cannot_be_skipped_as_na(element, status):
    runs, reviews, snapshots = cohort()
    for run, review in zip(runs, reviews):
        run['cases'][0]['automaticContract']['semanticReviewRequirements'] = [element]
        run['cases'][0]['output']['structuredAnswer'] = {'progress': {'done': 1, 'total': 3, 'percent': 100/3}}
        run['evaluation']['battery']['specializedReview']['cases'][CASE]['elements'][0]['id'] = element
        review['caseRecordSha256'] = digest(run['cases'][0])
        review['specializedReviewSpec']['elements'][0]['id'] = element
        review['specializedReviewResults'] = {'source_facts': review['specializedReviewResults']['source_facts'],
                                             element: {'status': status, 'evidence': 'Synthetic incorrectly skipped review.'}}
    result = audit(runs, reviews, snapshots)
    assert not result['qualityPassed'] and 'required_semantic_review_skipped' in result['failures']


def test_null_progress_without_numeric_progress_can_use_explained_na():
    runs, reviews, snapshots = cohort()
    for run, review in zip(runs, reviews):
        run['cases'][0]['output'].update(reply='수치 진척률은 제시하지 않았습니다.', structuredAnswer={'progress': None})
        review['caseRecordSha256'] = digest(run['cases'][0])
        review['outputExcerpt'] = '수치 진척률은 제시하지 않았습니다.'
        review['specializedReviewResults']['progress_denominator'] = {'status': 'na', 'evidence': 'No numeric progress supplied.'}
    assert audit(runs, reviews, snapshots)['qualityPassed']


@pytest.mark.parametrize('critical,dimension', [('progress_denominator', 'factual_grounding'),
                                              ('uncertainty_disclosure', 'safety_uncertainty')])
def test_major_semantic_finding_fails_despite_mean_above_threshold(critical, dimension):
    runs, reviews, snapshots = cohort()
    for run, review in zip(runs, reviews):
        element = run['evaluation']['battery']['specializedReview']['cases'][CASE]['elements'][0]
        element.update(id=critical, dimension=dimension)
        run['cases'][0]['automaticContract']['semanticReviewRequirements'] = [critical]
        review['caseRecordSha256'] = digest(run['cases'][0])
        review['specializedReviewSpec']['elements'][0].update(id=critical, dimension=dimension)
        review['specializedReviewResults'] = {'source_facts': review['specializedReviewResults']['source_facts'],
                                             critical: {'status': 'pass', 'evidence': 'Synthetic clear scope.'}}
    reviews[0]['specializedReviewResults'][critical] = {'status': 'major', 'evidence': 'Synthetic wrong/hidden unit.'}
    reviews[0]['dimensionScores'][dimension] = 3.5
    result = audit(runs, reviews, snapshots)
    assert result['designPassed'] and result['humanMean'] == 4.94
    assert not result['qualityPassed'] and 'major_semantic:' + critical in result['failures']


@pytest.mark.parametrize('defect', ['missing_run', 'duplicate_repeat', 'missing_review', 'duplicate_review',
                                  'record_hash', 'snapshot_hash', 'changed_spec', 'fake_excerpt',
                                  'missing_element', 'benchmark', 'candidate', 'dirty', 'partial_run', 'jira_unknown',
                                  'order', 'model', 'external_model', 'effort', 'planned_commit'])
def test_incomplete_or_unlinked_evidence_cannot_qualify(defect):
    runs, reviews, snapshots = cohort()
    if defect == 'missing_run':
        runs.pop()
    elif defect == 'duplicate_repeat':
        runs[-1]['evaluation']['run']['repeatIndex'] = 1
    elif defect == 'missing_review':
        reviews.pop()
    elif defect == 'duplicate_review':
        reviews.append(copy.deepcopy(reviews[0]))
    elif defect == 'record_hash':
        reviews[0]['caseRecordSha256'] = 'wrong'
    elif defect == 'snapshot_hash':
        reviews[0]['sourceSnapshotSha256'] = 'wrong'
    elif defect == 'changed_spec':
        reviews[0]['specializedReviewSpec']['goal'] = 'another task'
    elif defect == 'fake_excerpt':
        reviews[0]['outputExcerpt'] = 'not present in this answer'
    elif defect == 'missing_element':
        reviews[0]['specializedReviewResults'] = {}
    elif defect in {'benchmark', 'candidate'}:
        runs[0]['evaluation'][defect + 'Key'] = 'other'
    elif defect == 'dirty':
        runs[0]['evaluation']['run']['candidateDirty'] = True
    elif defect == 'partial_run':
        runs[0]['executionComplete'] = False
    elif defect == 'jira_unknown':
        runs[0]['summary']['jiraApi'] = {'observed': False, 'requests': None}
    elif defect == 'order':
        runs[0]['evaluation']['run']['candidateOrderIndex'] = 2
    elif defect == 'model':
        for run in runs:
            run['evaluation']['run']['model'] = 'wrong-model'
    elif defect == 'effort':
        for run in runs:
            run['evaluation']['run']['externalRuntime']['reasoningEffort'] = 'wrong-effort'
    elif defect == 'external_model':
        for run in runs:
            run['evaluation']['run']['externalRuntime']['model'] = 'contradictory-model'
    elif defect == 'planned_commit':
        for run in runs:
            run['evaluation']['run']['candidateCommit'] = 'b' * 40
    result = audit(runs, reviews, snapshots)
    assert not result['designPassed'] and not result['qualityPassed']


def test_automatic_failure_is_retained_even_when_human_mean_is_five():
    runs, reviews, snapshots = cohort()
    record = runs[0]['cases'][0]
    record['automaticPass'] = record['automaticContract']['pass'] = False
    runs[0]['summary']['automaticPass'] = 0
    reviews[0]['caseRecordSha256'] = digest(record)
    result = audit(runs, reviews, snapshots)
    assert result['designPassed'] and result['humanMean'] == 5 and not result['qualityPassed']
    assert 'automatic_gate_failed' in result['failures']


def test_display_rounding_cannot_raise_a_below_threshold_mean_into_a_pass():
    from tools.codex_mcp_eval.qualification import audit_cohort
    runs, originals, snapshots = cohort()
    ids, reviews, hashes = [f'CASE-{index:02d}' for index in range(22)], [], {}
    for run, original in zip(runs, originals):
        record = run['cases'][0]
        spec = run['evaluation']['battery']['specializedReview']['cases'][CASE]
        run['cases'] = []
        run['evaluation']['battery'].update(fullCaseCount=22, selectedCaseIds=ids)
        run['evaluation']['battery']['specializedReview']['cases'] = {key: copy.deepcopy(spec) for key in ids}
        run['summary'].update(completed=22, total=22, automaticPass=22)
        for key in ids:
            row = {**copy.deepcopy(record), 'id': key, 'sourceSnapshot': f"{run['evaluation']['run']['repeatIndex']}-{key}.json"}
            run['cases'].append(row)
            hashes[row['sourceSnapshot']] = snapshots[record['sourceSnapshot']]
            review = {**copy.deepcopy(original), 'caseId': key, 'caseRecordSha256': digest(row),
                      'dimensionScores': {name: 4.5 for name in original['dimensionScores']}}
            reviews.append(review)
    reviews[0]['dimensionScores']['communication_rendering'] = 4
    result = audit_cohort(runs, reviews, evaluator=EVALUATOR, expected_case_ids=ids,
                         source_snapshot_hashes=hashes, expected_order_by_repeat=ORDER,
                         expected_commit='a' * 40, expected_runtime=RUNTIME)
    assert result['designPassed'] and result['humanMean'] == 4.5
    assert not result['qualityPassed'] and 'human_mean_below_gate' in result['failures']
