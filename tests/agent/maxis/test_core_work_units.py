"""Strict generation transport is lossless and never repairs authored meaning."""
import copy
import json

import pytest
from jsonschema import Draft202012Validator

from test_core_work_contract import contract, canonical, project
from test_core_work_assembler import case, guards, encoded, DIRECTORY


def encode_units(draft, schema):
    """Test-only encoder: preserve every value and explicit absence."""
    units = []
    def encode(value, row):
        if 'anyOf' in row:
            unit = {'id': f'unit-{len(units) + 1}', 'content': copy.deepcopy(value)}
            units.append(unit)
            return unit['id']
        if row.get('type') == 'array':
            return [encode(v, row['items']) for v in value]
        if row.get('type') != 'object': return value
        assert not set(value) - set(row['properties'])
        return {key: encode(value[key], child) if key in row.get('required', [])
                else [encode(value[key], child)] if key in value else []
                for key, child in row['properties'].items()}
    payload = encode(draft['payload'], schema['properties']['payload']['anyOf'][1]) if draft['payload'] else {}
    return {'contract': 'work-units.v1', 'status': draft['status'], 'units': units, 'payload': payload}


@pytest.fixture
def compiler(contract, canonical):
    from app.agent.maxis.contracts import canonical_payload_schema
    from app.agent.workflow.work_architect.contracts import CREATE_SCHEMA
    raw, digest = canonical_payload_schema(CREATE_SCHEMA)
    draft = contract.build_draft_schema(canonical, project)
    generation = contract.build_generation_schema(draft)
    source = '\n\n'.join((DIRECTORY / 'code' / name).read_text(encoding='utf-8') for name in (
        'core_draft_validation.py', 'core_draft_context.py', 'core_fact_text.py',
        'core_work_units.py', 'core_work_assembler.py'))
    ns = {}
    exec(source, ns)
    ns.update(_WORK_SCHEMA_JSON=raw, _WORK_SCHEMA_SHA256=digest, _DRAFT_SCHEMA=draft, _GENERATION_SCHEMA=generation)
    return ns, draft, generation


@pytest.mark.parametrize('labels', [None, [], ['review']])
def test_roundtrip_uses_current_entry_and_preserves_both_guards(compiler, case, labels):
    ns, schema, generation = compiler
    pack, incoming, draft, expected = case
    if labels is not None:
        draft['payload']['items'][0]['labels'] = labels
        expected['items'][0]['labels'] = labels
    wire = encode_units(draft, schema)
    assert Draft202012Validator(generation).is_valid(wire)
    assert json.loads(ns['_expand_work_units'](encoded(wire))) == draft
    result = json.loads(ns['main'](encoded(incoming), encoded(wire))['candidate'])
    remote, local = guards(pack, result)
    assert remote == result and local()[1] == expected


@pytest.mark.parametrize('mutation', ['duplicate', 'unused', 'dangling', 'missing_slot', 'double_slot',
                                   'destination_length', 'duplicate_source', 'old_transport', 'truncated'])
def test_invalid_units_never_repair_or_discard_statements(compiler, case, mutation):
    ns, schema, _ = compiler
    _, incoming, draft, _ = case
    wire = encode_units(draft, schema)
    if mutation == 'duplicate': wire['units'].append(copy.deepcopy(wire['units'][0]))
    elif mutation == 'unused': wire['units'].append(dict(copy.deepcopy(wire['units'][0]), id='unit-99'))
    elif mutation == 'dangling': wire['payload']['items'][0]['summary'] = 'unit-99'
    elif mutation == 'missing_slot': del wire['payload']['items'][0]['labels']
    elif mutation == 'double_slot': wire['payload']['items'][0]['labels'] = [[], []]
    elif mutation == 'destination_length':
        ref = wire['payload']['items'][0]['summary']
        next(u for u in wire['units'] if u['id'] == ref)['content']['value'] = 'x' * 181
    elif mutation == 'duplicate_source': wire['units'][0]['content']['source_ids'] *= 2
    elif mutation == 'old_transport': wire = draft
    candidate = encoded(wire)
    if mutation == 'truncated': candidate = candidate[:-1]
    with pytest.raises(ValueError, match='invalid_core_work_draft'):
        ns['main'](encoded(incoming), candidate)


def test_generation_is_closed_required_and_shallow(contract, canonical):
    draft = contract.build_draft_schema(canonical, project)
    schema = contract.build_generation_schema(draft)
    assert schema['properties']['contract']['enum'] == ['work-units.v1']
    assert list(schema['properties']) == ['contract', 'status', 'payload', 'units']
    def walk(row, depth=0):
        assert depth <= 10
        assert 'uniqueItems' not in row and 'maxProperties' not in row
        if row.get('type') == 'object':
            assert row['additionalProperties'] is False
            assert set(row['required']) == set(row['properties'])
            for child in row['properties'].values(): walk(child, depth + 1)
        if row.get('type') == 'array': walk(row['items'], depth + 1)
        for branch in row.get('anyOf', []): walk(branch, depth)
    walk(schema)
    Draft202012Validator.check_schema(schema)
    assert draft == contract.build_draft_schema(canonical, project)


def test_generation_uses_portable_identifier_patterns_but_keeps_exact_internal_bounds(compiler, case):
    ns, draft, generation = compiler
    content = generation['properties']['units']['items']['properties']['content']['anyOf']
    for identifier in (content[0]['properties']['source_ids']['items'], content[1]['properties']['source_id']):
        assert identifier['pattern'] == r'^[A-Za-z0-9][A-Za-z0-9._:-]*$'
    original = draft['properties']['payload']['anyOf'][1]['properties']['items']['items']['properties']['summary']
    assert '(?!' in original['anyOf'][0]['properties']['source_ids']['items']['pattern']
    _, incoming, candidate, _ = case
    wire = encode_units(candidate, draft)
    wire['units'][0]['content']['source_ids'][0] += '\n'
    with pytest.raises(ValueError, match='invalid_core_work_draft'):
        ns['main'](encoded(incoming), encoded(wire))


def test_optional_slots_distinguish_omission_from_explicit_empty(contract, canonical):
    schema = contract.build_generation_schema(contract.build_draft_schema(canonical, project))
    item = schema['properties']['payload']['anyOf'][1]['properties']['items']['items']
    slot = Draft202012Validator(item['properties']['labels'])
    assert slot.is_valid([]) and slot.is_valid([[]]) and slot.is_valid([['review']])
    assert not slot.is_valid(None) and not slot.is_valid([[], []])
    assert item['properties']['summary'] == {'type': 'string', 'pattern': '^unit-[1-9][0-9]*$'}


def test_required_remote_interview_is_not_reclassified_by_korean_wording():
    from langchain_core.messages import HumanMessage
    from app.agent.runtime_status import CORE_ROLES
    from app.agent.workflow.agents.work_architect import WorkArchitect
    state = {'messages': [HumanMessage(content='NDV 배치 Job Task 만들어줘. 나머지는 알아서')],
             'intent': 'plan_work', 'situation': '검증된 관련 티켓 조회 완료', 'runtime_profile': 'named-profile',
             'runtime_role_routes': {role: 'maxis' for role in CORE_ROLES}}
    question = {'question': '기존 검증 티켓을 확장할까요, 새 작업으로 분리할까요?',
                'kind': 'choice', 'field': 'target', 'options': ['기존 확장', '새 작업'],
                'required_input': True, 'why_required': '동일 계열 작업이 확인되어 사용자 결정이 필요함'}
    output = {'mode': 'task', 'items': [], 'questions': [question]}
    before = copy.deepcopy(output)
    assert WorkArchitect().post_projection_correction(state, output) == ''
    assert output == before  # No question, approval or payload rewriting.
    applied = WorkArchitect().apply(state, copy.deepcopy(output))
    assert any(q.get('required_input') is True and q.get('field') == 'target'
               for q in applied['questions'])
    assert not (applied.get('draft') or {}).get('items')
    question['required_input'] = False
    question['why_required'] = ''
    assert WorkArchitect().post_projection_correction(state, output)


@pytest.mark.parametrize('reason', ['source_id', 'fact_selection'])
def test_generation_entry_preserves_bounded_retry_reason(compiler, case, reason):
    ns, schema, _ = compiler
    _, incoming, draft, _ = case
    draft['payload']['items'][0]['summary'] = (
        {'value': '요약', 'kind': 'inference', 'source_ids': ['absent-source']} if reason == 'source_id'
        else {'kind': 'fact', 'source_id': 'record-b', 'source_path': '/missing'})
    with pytest.raises(ValueError, match=f'^invalid_core_work_draft native_draft_{reason}$'):
        ns['main'](encoded(incoming), encoded(encode_units(draft, schema)))


@pytest.mark.parametrize('variant', ['question', 'child', 'fact', 'reuse'])
def test_full_entry_roundtrips_distinct_semantic_units(compiler, case, variant):
    ns, schema, generation = compiler
    pack, incoming, draft, expected = case
    from test_core_work_assembler import leaf
    if variant == 'question':
        draft['payload']['items'] = []
        draft['payload']['questions'] = [{'question': leaf('기존 작업 확장 또는 신규 분리?'),
            'kind': 'choice', 'field': 'target', 'input_need': 'required',
            'why_required': leaf('사용자가 결정할 작업 범위'),
            'options': [leaf('기존 작업 확장'), leaf('신규 작업 분리')]}]
    elif variant == 'child':
        draft['payload']['items'][0]['children'] = [{'summary': leaf('하위 점검'),
            'scope_in': [leaf('경로 검토')], 'dod': [leaf('검토 결과 기록')]}]
    elif variant == 'fact':
        draft['payload']['items'][0]['references'] = [{'kind': 'fact', 'source_id': 'record-b', 'source_path': ''}]
    wire = encode_units(draft, schema)
    if variant == 'reuse':
        item = wire['payload']['items'][0]
        old_ref = item['dod'][0]
        item['dod'][0] = item['scope_in'][0]
        wire['units'] = [u for u in wire['units'] if u['id'] != old_ref]
        draft['payload']['items'][0]['dod'] = copy.deepcopy(draft['payload']['items'][0]['scope_in'])
    assert Draft202012Validator(generation).is_valid(wire)
    assert json.loads(ns['_expand_work_units'](encoded(wire))) == draft
    result = json.loads(ns['main'](encoded(incoming), encoded(wire))['candidate'])
    remote, local = guards(pack, result)
    assert remote == result and local()[1] == result['payload']


def test_shared_unit_obeys_each_destination_bound(compiler, case):
    ns, schema, _ = compiler
    _, incoming, draft, _ = case
    wire = encode_units(draft, schema)
    item = wire['payload']['items'][0]
    original = item['summary']
    item['summary'] = item['background']
    wire['units'] = [u for u in wire['units'] if u['id'] != original]
    next(u for u in wire['units'] if u['id'] == item['background'])['content']['value'] = 'x' * 181
    with pytest.raises(ValueError, match='native_draft_schema'):
        ns['main'](encoded(incoming), encoded(wire))
