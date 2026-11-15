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
