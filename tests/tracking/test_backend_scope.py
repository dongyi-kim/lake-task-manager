import pytest

from app.domain.mytasks import _matches_scope
from app.tracking.scope import module_candidates


PEOPLE = {'A': ['member'], 'B': ['other']}


def row(key, components, epic=None, parent=None, subtask=False):
    return {'key': key, 'fields': {'components': [{'name': c} for c in components], 'epic': epic,
        'assignee': None, 'reporter': None, 'parent': {'key': parent} if parent else None,
        'issuetype': {'name': 'Sub-Task' if subtask else 'Task', 'subtask': subtask}}}


def test_module_scope_inherits_parent_and_shared_epic_and_retains_unknown_parent():
    rows = [row('P-1', ['A']), row('P-2', ['Other'], parent='P-1', subtask=True),
            row('P-3', ['Other'], epic='P-100'), row('P-4', ['Other']),
            row('P-5', [], parent='P-unknown', subtask=True)]
    kept, missing, uncertain = module_candidates(rows, ['A'], ['P-100'], 'epic', module_people=PEOPLE)
    assert {r['key'] for r in kept} == {'P-1', 'P-2', 'P-3', 'P-5'}
    assert missing == {'P-unknown'}
    assert uncertain == {'P-5'}
    kept, missing, uncertain = module_candidates(rows, ['A'], ['P-100'], 'epic',
        {'P-unknown': row('P-unknown', ['Other'])}, module_people=PEOPLE)
    assert {r['key'] for r in kept} == {'P-1', 'P-2', 'P-3'}
    assert not missing and not uncertain


@pytest.mark.parametrize('components,assignee,reporter,epic', [
    pytest.param(['A'], 'other', 'bot', None, id='component-union'),
    pytest.param(['사용자 voc'], 'member', 'bot', None, id='voc-assignee-with-bot-reporter'),
    pytest.param(['사용자 voc'], 'other', 'member', None, id='voc-reporter'),
    pytest.param(['사용자 voc', 'B'], 'member', 'bot', None, id='other-module-component'),
    pytest.param(['사용자 voc'], 'member', 'bot', 'P-200', id='other-module-epic'),
    pytest.param(['B'], 'member', 'other', None, id='ordinary-assignee'),
    pytest.param([], 'other', 'member', None, id='ordinary-reporter'),
    pytest.param(['A'], None, None, 'P-200', id='component-despite-other-epic'),
    pytest.param(['사용자 voc'], 'other', 'bot', None, id='unrelated-voc'),
    pytest.param(['B'], 'other', None, 'P-200', id='unrelated-ordinary'),
    pytest.param([], None, None, None, id='unassigned'),
])
def test_current_module_membership_matches_task_window_union(components, assignee, reporter, epic):
    raw = row('P-1', components, epic=epic)
    raw['fields'].update(assignee={'name': assignee} if assignee else None,
                         reporter={'name': reporter} if reporter else None)
    task_match = _matches_scope({'assigneeId': assignee, 'reporterId': reporter,
                                'components': components},
                               {'scopeKind': 'module', 'moduleIds': PEOPLE['A'], 'moduleComponents': ['A']})
    kept, missing, uncertain = module_candidates([raw], ['A'], ['P-100'], 'epic', module_people=PEOPLE)
    assert [r['key'] for r in kept] == (['P-1'] if task_match else [])
    assert not missing and not uncertain


@pytest.mark.parametrize('role', ['assignee', 'reporter'])
@pytest.mark.parametrize('identity', [{'name': ' MEMBER '}, {'key': 'MEMBER'}, {'accountId': 'member'}])
def test_person_membership_uses_immutable_identity(role, identity):
    raw = row('P-1', ['B'])
    raw['fields'][role] = identity
    kept, missing, uncertain = module_candidates([raw], ['A'], [], 'epic', module_people=PEOPLE)
    assert [r['key'] for r in kept] == ['P-1']
    assert not missing and not uncertain


@pytest.mark.parametrize('role', ['assignee', 'reporter'])
@pytest.mark.parametrize('identity', [
    pytest.param('absent', id='absent'), pytest.param('malformed', id='malformed'),
    pytest.param({}, id='empty'), pytest.param({'displayName': 'member'}, id='display-only'),
    pytest.param({'key': 'JIRAUSER123'}, id='unresolved-key'),
    pytest.param({'accountId': 'account-123'}, id='unresolved-account'),
])
def test_incomplete_person_identity_retains_unknown_scope(role, identity):
    raw = row('P-1', ['사용자 voc'])
    if identity == 'absent':
        raw['fields'].pop(role)
    else:
        raw['fields'][role] = identity
    kept, missing, uncertain = module_candidates([raw], ['A'], [], 'epic', module_people=PEOPLE)
    assert [r['key'] for r in kept] == ['P-1']
    assert not missing and uncertain == {'P-1'}


@pytest.mark.parametrize('role', ['assignee', 'reporter'])
def test_confirmed_outside_username_is_excluded_despite_matching_display_name(role):
    raw = row('P-1', ['사용자 voc'])
    raw['fields'][role] = {'name': 'outsider', 'key': 'JIRAUSER123', 'displayName': 'member'}
    kept, missing, uncertain = module_candidates([raw], ['A'], [], 'epic', module_people=PEOPLE)
    assert not kept and not missing and not uncertain


@pytest.mark.parametrize('role', ['assignee', 'reporter'])
@pytest.mark.parametrize('parent_kind', ['missing', 'other-module', 'cycle'])
def test_own_person_match_is_certain_before_parent_resolution(role, parent_kind):
    raw = row('P-1', ['사용자 voc'], parent='P-10', subtask=True)
    raw['fields'][role] = {'name': 'member'}
    parent = row('P-10', ['B'], parent='P-1' if parent_kind == 'cycle' else None)
    parents = {} if parent_kind == 'missing' else {'P-10': parent}
    kept, missing, uncertain = module_candidates([raw], ['A'], [], 'epic', parents, module_people=PEOPLE)
    assert [r['key'] for r in kept] == ['P-1']
    assert not missing and not uncertain


@pytest.mark.parametrize('role', ['assignee', 'reporter'])
def test_subtask_inherits_parent_person_membership(role):
    parent = row('P-10', ['사용자 voc'])
    parent['fields'][role] = {'name': 'member'}
    child = row('P-1', ['B'], parent='P-10', subtask=True)
    child['fields'].update(assignee={'name': 'other'}, reporter={'name': 'bot'})
    kept, missing, uncertain = module_candidates([child], ['A'], [], 'epic', {'P-10': parent}, module_people=PEOPLE)
    assert [r['key'] for r in kept] == ['P-1']
    assert not missing and not uncertain
