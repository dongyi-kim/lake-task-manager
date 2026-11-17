import pytest

from app.tracking.scope import module_candidates


def row(key, components, epic=None, parent=None, subtask=False):
    return {'key': key, 'fields': {'components': [{'name': c} for c in components], 'epic': epic,
        'parent': {'key': parent} if parent else None, 'issuetype': {'name': 'Sub-Task' if subtask else 'Task', 'subtask': subtask}}}


def test_module_scope_inherits_parent_and_shared_epic_and_retains_unknown_parent():
    rows = [row('P-1', ['A']), row('P-2', ['Other'], parent='P-1', subtask=True),
            row('P-3', ['Other'], epic='P-100'), row('P-4', ['Other']),
            row('P-5', [], parent='P-unknown', subtask=True)]
    kept, missing, uncertain = module_candidates(rows, ['A'], ['P-100'], 'epic')
    assert {r['key'] for r in kept} == {'P-1', 'P-2', 'P-3', 'P-5'}
    assert missing == {'P-unknown'}
    assert uncertain == {'P-5'}
    kept, missing, uncertain = module_candidates(rows, ['A'], ['P-100'], 'epic', {'P-unknown': row('P-unknown', ['Other'])})
    assert {r['key'] for r in kept} == {'P-1', 'P-2', 'P-3'}
    assert not missing and not uncertain


@pytest.mark.parametrize('assignee', [{'name': 'MEMBER'}, {'key': 'member'}, {'accountId': 'member'}])
def test_voc_uses_current_assignee_immutable_identity_for_configured_module(assignee):
    raw = row('P-1', ['Configured VOC'])
    raw['fields']['assignee'] = assignee
    kept, missing, uncertain = module_candidates([raw], ['A'], [], 'epic',
        voc_component='Configured VOC', module_people={'A': ['member'], 'B': ['other']})
    assert [r['key'] for r in kept] == ['P-1']
    assert not missing and not uncertain


def test_voc_module_supplement_does_not_expand_other_voc_or_ordinary_ticket_scope():
    other_voc = row('P-1', ['Configured VOC'])
    other_voc['fields']['assignee'] = {'name': 'other'}
    ordinary = row('P-2', ['Other'])
    ordinary['fields']['assignee'] = {'name': 'member'}
    wrong_component = row('P-3', ['Different VOC'])
    wrong_component['fields']['assignee'] = {'name': 'member'}
    kept, missing, uncertain = module_candidates([other_voc, ordinary, wrong_component], ['A'], [], 'epic',
        voc_component='Configured VOC', module_people={'A': ['member'], 'B': ['other']})
    assert not kept and not missing and not uncertain


@pytest.mark.parametrize('assignee_present,assignee', [(False, None), (True, None), (True, {}), (True, {'displayName': 'member'})])
def test_voc_without_confirmed_current_assignee_stays_unknown(assignee_present, assignee):
    raw = row('P-1', ['Configured VOC'])
    if assignee_present:
        raw['fields']['assignee'] = assignee
    kept, missing, uncertain = module_candidates([raw], ['A'], [], 'epic',
        voc_component='Configured VOC', module_people={'A': ['member']})
    assert [r['key'] for r in kept] == ['P-1']
    assert not missing and uncertain == {'P-1'}


@pytest.mark.parametrize('assignee', [
    {'key': 'JIRAUSER123'}, {'accountId': 'account-123'},
    {'key': 'JIRAUSER123', 'displayName': 'member'},
])
def test_voc_unresolved_assignee_namespace_is_retained_as_uncertain(assignee):
    raw = row('P-1', ['Configured VOC'])
    raw['fields']['assignee'] = assignee
    kept, missing, uncertain = module_candidates([raw], ['A'], [], 'epic',
        voc_component='Configured VOC', module_people={'A': ['member']})
    assert [r['key'] for r in kept] == ['P-1']
    assert not missing and uncertain == {'P-1'}


def test_voc_explicit_outside_username_is_excluded_despite_matching_display_name():
    raw = row('P-1', ['Configured VOC'])
    raw['fields']['assignee'] = {'name': 'outsider', 'key': 'JIRAUSER123', 'displayName': 'member'}
    kept, missing, uncertain = module_candidates([raw], ['A'], [], 'epic',
        voc_component='Configured VOC', module_people={'A': ['member']})
    assert not kept and not missing and not uncertain


def test_subtask_inherits_voc_parent_configured_module_membership():
    parent = row('P-10', ['Configured VOC'])
    parent['fields']['assignee'] = {'name': 'member'}
    child = row('P-1', [], parent='P-10', subtask=True)
    child['fields']['assignee'] = {'name': 'outsider'}
    kept, missing, uncertain = module_candidates([child], ['A'], [], 'epic', {'P-10': parent},
        voc_component='Configured VOC', module_people={'A': ['member']})
    assert [r['key'] for r in kept] == ['P-1']
    assert not missing and not uncertain


@pytest.mark.parametrize('components,epic', [(['Configured VOC', 'B'], None), (['Configured VOC'], 'P-200')])
def test_voc_assignee_supplement_cannot_override_explicit_other_module_or_wbs_epic(components, epic):
    raw = row('P-1', components, epic=epic)
    raw['fields']['assignee'] = {'name': 'member'}
    kept, missing, uncertain = module_candidates([raw], ['A'], ['P-100'], 'epic',
        voc_component='Configured VOC', module_people={'A': ['member'], 'B': ['other']},
        configured_epics={'P-100', 'P-200'})
    assert not kept and not missing and not uncertain


def test_voc_supplement_allows_unmapped_epic_but_preserves_explicit_parent_module():
    raw = row('P-1', ['Configured VOC'], epic='P-unmapped')
    raw['fields']['assignee'] = {'name': 'member'}
    options = dict(voc_component='Configured VOC', module_people={'A': ['member'], 'B': ['other']},
                   configured_epics={'P-100', 'P-200'})
    kept, missing, uncertain = module_candidates([raw], ['A'], ['P-100'], 'epic', **options)
    assert [r['key'] for r in kept] == ['P-1']
    assert not missing and not uncertain
    raw['fields']['parent'] = {'key': 'P-10'}
    kept, missing, uncertain = module_candidates([raw], ['A'], ['P-100'], 'epic',
        {'P-10': row('P-10', ['B'])}, **options)
    assert not kept and not missing and not uncertain
