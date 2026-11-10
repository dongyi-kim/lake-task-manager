"""A deployment must not stitch old raw chunks into normalized record chunks."""
from copy import deepcopy
from hashlib import sha256
import json

from tools.codex_mcp_eval.work_evidence import evidence_collection, source_observed


def page(start, items, more, record_format=None):
    data = {'key': 'DL-1', 'kind': 'comments', 'startAt': start, 'total': 2,
            'sourceComplete': True, 'partial': False, 'hasMore': more, 'items': items}
    if record_format is not None:
        data['recordFormat'] = record_format
    return {'name': 'get_evidence_page', 'result': {'ok': True, 'data': data}}


def chunks(record_format):
    text = json.dumps({'id': '2', 'body': 'exact🙂' * 100}, ensure_ascii=False)
    digest, middle = sha256(text.encode()).hexdigest(), len(text) // 2
    calls = []
    for start, end in ((0, middle), (middle, len(text))):
        item = {'id': '2', 'sourceIndex': 1, 'recordOffset': start, 'recordJson': text[start:end],
                'totalCharacters': len(text), 'recordSha256': digest,
                'nextRecordOffset': end if end < len(text) else None}
        if record_format is not None:
            item['recordFormat'] = record_format
        calls.append(page(1, [item], end < len(text), record_format))
    return calls


def test_mixed_format_same_digest_requires_restart_of_only_affected_record():
    known = page(0, [{'id': '1', 'body': 'old saved full record'}], True)
    old, new = chunks(None), chunks('jira-evidence-v2')
    mixed = [known, old[0], new[1]]
    result = evidence_collection(mixed, 'DL-1')
    assert not result['complete'] and result['records'] == [{'id': '1', 'body': 'old saved full record'}]
    repaired = evidence_collection([*mixed, *new], 'DL-1')
    assert repaired['complete'] and repaired['records'][0]['body'] == 'old saved full record'


def test_unknown_format_and_mismatched_chunk_marker_never_prove_coverage():
    known = page(0, [{'id': '1'}], True)
    for bad in ('future-format', 'jira-evidence-v1'):
        calls = chunks('jira-evidence-v2')
        calls[1]['result']['data']['items'][0]['recordFormat'] = bad
        assert not evidence_collection([known, *calls], 'DL-1')['complete']
    for unknown in ('unknown', None, [], {'version': 2}, 2):
        full = page(0, [{'id': '1'}, {'id': '2'}], False)
        full['result']['data']['recordFormat'] = unknown
        assert not evidence_collection([full], 'DL-1')['complete']


def test_known_legacy_full_records_remain_readable():
    for record_format in (None, 'jira-evidence-v1'):
        saved = page(0, [{'id': '1'}, {'id': '2'}], False, record_format)
        assert evidence_collection([saved], 'DL-1')['complete']


def test_v2_page_cannot_relabel_an_unmarked_legacy_chunk():
    known = page(0, [{'id': '1'}], True)
    calls = chunks('jira-evidence-v2')
    del calls[0]['result']['data']['items'][0]['recordFormat']
    assert not evidence_collection([known, *calls], 'DL-1')['complete']


def test_unknown_format_is_not_literal_comment_proof_through_ticket_url_fallback():
    ticket = {'name': 'get_ticket', 'result': {'ok': True, 'data': {
        'key': 'DL-1', 'url': 'https://jira.test/browse/DL-1'}}}
    evidence = page(0, [{'id': '7', 'self': '/rest/api/2/issue/DL-1/comment/7'}], False, 'future')
    source = {'kind': 'comment', 'id': '7', 'url': 'https://jira.test/rest/api/2/issue/DL-1/comment/7'}
    recognized = deepcopy(evidence)
    recognized['result']['data']['recordFormat'] = 'jira-evidence-v2'
    assert source_observed([ticket, recognized], source)
    assert not source_observed([ticket, evidence], source)


def test_record_format_gate_mutation_changes_both_battery_manifests(monkeypatch):
    from tools import agent_eval_protocol as protocol
    from tools.codex_mcp_eval import work_cases, work_evidence
    from tools.codex_mcp_eval.etl import cases as etl_cases
    suites = (work_cases, etl_cases)
    before = [protocol.battery_manifest_sha256(suite.CASES,
        checker_dependencies=suite.CHECKER_DEPENDENCIES) for suite in suites]
    source = protocol.inspect.getsource
    def changed_format_gate(value):
        if value is work_evidence._evidence_record_format:
            return 'def _evidence_record_format(page):\n    return None\n'
        return source(value)
    monkeypatch.setattr(protocol.inspect, 'getsource', changed_format_gate)
    after = [protocol.battery_manifest_sha256(suite.CASES,
        checker_dependencies=suite.CHECKER_DEPENDENCIES) for suite in suites]
    assert all(first != second for first, second in zip(before, after))
