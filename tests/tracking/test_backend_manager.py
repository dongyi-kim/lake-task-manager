from types import SimpleNamespace
from copy import deepcopy
import json

import pytest

from app.auth.base import UpstreamError, UpstreamUnavailable
from app.jira.jql_scheduling import BoundedQueryScheduler
from app.jira.jql import And, Atom, Or, _Parser, _split_order, tokenize
from app.tracking.manager import TrackingManager


class Provider:
    supports_parallel = False

    def __init__(self):
        self.calls = []
        self.fail = False

    def get_json(self, path, params=None):
        params = params or {}
        self.calls.append((path, dict(params)))
        if path.endswith('/status'):
            return [{"id": "1", "name": "Ready", "statusCategory": {"key": "new"}},
                    {"id": "2", "name": "Finished", "statusCategory": {"key": "done"}}]
        if self.fail:
            raise UpstreamUnavailable('offline')
        if path.endswith('/comment'):
            return {"total": 1, "startAt": 0, "comments": [{"id": "1", "created": "2026-11-11T10:00:00+0900", "author": {"name": "a"}, "body": "hello"}]}
        raw = {"key": "P-1", "fields": {"summary": "Work", "created": "2026-11-11T00:00:00+0900",
            "updated": "2026-11-11T10:00:00+0900", "creator": {"name": "a"}, "assignee": {"name": "a"}, "reporter": None,
            "issuetype": {"name": "Task"}, "status": {"statusCategory": {"key": "new"}}},
            "changelog": {"startAt": 0, "total": 0, "histories": []}}
        if path.endswith('/search'):
            return {"startAt": params.get('startAt', 0), "total": 1, "issues": [raw]}
        return raw


def client(provider):
    return SimpleNamespace(provider=provider, _query_scheduler=BoundedQueryScheduler(), env='test',
        s=SimpleNamespace(project_key='P',search_jira_projects=['P'],epic_link_field_id='epic',jira_base='test'),
        current_user_for_authorization=lambda: {"id": 'owner', "name": 'Owner'}, _jql_generation=lambda: 1)


def manager(tmp_path, provider):
    return TrackingManager(client(provider), tmp_path/'tracking.sqlite3', plan_loader=lambda: {"wbs": []},
                           people_loader=lambda: {"M": ['a']})


def test_missing_search_comments_falls_back_and_durable_warm_source(tmp_path):
    provider = Provider()
    m = manager(tmp_path, provider)
    run = m.create({"startDate": '2026-11-10', 'endDate': '2026-11-12'}, background=False)
    assert run['status'] == 'complete'
    assert run['summary']['created'] == 1
    assert run['summary']['comments'] == 1
    assert run['coverage']['complete'] is True
    assert run['metrics']['restRequests'] == len(provider.calls)
    count = len(provider.calls)
    again = manager(tmp_path, provider).create(run['request'], background=False)
    assert len(provider.calls) == count + 1
    assert again['summary']['comments'] == 1
    assert again['metrics']['cacheHits'] > 0
    assert m.get(run['id'])['asOf'] == run['asOf']


def test_disconnect_checkpoint_resume_does_not_skip_query(tmp_path):
    provider = Provider()
    provider.fail = True
    m = manager(tmp_path, provider)
    run = m.create({"startDate": '2026-11-10', 'endDate': '2026-11-12'}, background=False)
    assert run['status'] == 'paused'
    assert run['coverage']['complete'] is False
    provider.fail = False
    resumed = m.resume(run['id'], background=False)
    assert resumed['status'] == 'complete'
    assert resumed['summary']['created'] == 1
    assert resumed['asOf'] == run['asOf']


def test_stored_run_cannot_be_read_by_different_account(tmp_path):
    m = manager(tmp_path, Provider())
    run = m.create({"startDate": '2026-11-10', 'endDate': '2026-11-12'}, background=False)
    m.client.current_user_for_authorization = lambda: {'id': 'other'}
    with pytest.raises(PermissionError):
        m.get(run['id'])


def test_resume_refetches_only_previously_unavailable_ancestor(tmp_path):
    class HierarchyProvider(Provider):
        visible = False

        def get_json(self, path, params=None):
            if path.endswith('/search') and 'P-10' in (params or {}).get('jql', ''):
                self.calls.append((path, dict(params)))
                rows = [{"key": "P-10", "fields": {"summary": "Parent", "issuetype": {"name": "Epic"}}}] if self.visible else []
                return {"startAt": 0, "total": len(rows), "issues": rows}
            result = super().get_json(path, params)
            if path.endswith('/search'):
                result['issues'][0]['fields']['epic'] = 'P-10'
            return result

    provider = HierarchyProvider()
    m = manager(tmp_path, provider)
    first = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12'}, background=False)
    assert first['status'] == 'partial'
    assert any(item['source'] == 'hierarchy' for item in first['coverage']['missing'])
    provider.visible = True
    calls = len(provider.calls)
    resumed = m.resume(first['id'], background=False)
    assert resumed['status'] == 'complete'
    assert len(provider.calls) == calls + 1
    assert resumed['summary']['comments'] == first['summary']['comments']
    assert resumed['coverage']['missing'] == []


def test_explicit_person_selector_falls_back_conservatively_when_server_rejects_history_jql(tmp_path):
    class OldProvider(Provider):
        def get_json(self, path, params=None):
            if 'updatedBy' in (params or {}).get('jql', ''):
                self.calls.append((path, dict(params)))
                raise UpstreamError(400, path, 'Unsupported function updatedBy')
            return super().get_json(path, params)

    provider = OldProvider()
    m = manager(tmp_path, provider)
    request = {'startDate': '2026-11-10', 'endDate': '2026-11-12', 'people': ['a']}
    first = m.create(request, background=False)
    assert first['status'] == 'complete'
    assert first['summary']['comments'] == 1
    assert first['coverage']['candidateStrategy'] == 'broadFallback'
    assert any('assignee WAS IN' in params.get('jql', '') for _, params in provider.calls)
    calls = len(provider.calls)
    again = m.create(request, background=False)
    assert again['status'] == 'complete'
    assert len(provider.calls) == calls + 1
    assert all('updatedBy' not in params.get('jql', '') for _, params in provider.calls[calls:])


def test_module_metadata_prunes_unrelated_ticket_before_heavy_sources_and_keeps_outsider(tmp_path):
    class ModuleProvider(Provider):
        def get_json(self, path, params=None):
            if path.endswith('/search') and 'project in' in (params or {}).get('jql', ''):
                result = super().get_json(path, params)
                result['issues'][0]['fields'].update(components=[{'name': 'M'}], epic=None,
                                                     assignee={'name': 'outsider'})
                other = {'key': 'P-2', 'fields': {'updated': '2026-11-11T10:00:00+0900',
                    'components': [{'name': 'Other'}], 'epic': None, 'issuetype': {'name': 'Task'},
                    'assignee': {'name': 'outsider'}, 'reporter': None}}
                result['issues'].append(other)
                result['total'] = 2
                return result
            result = super().get_json(path, params)
            if path.endswith('/search'):
                result['issues'][0]['fields'].update(components=[{'name': 'M'}], epic=None,
                                                     assignee={'name': 'outsider'})
            return result

    provider = ModuleProvider()
    m = manager(tmp_path, provider)
    run = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M']}, background=False)
    assert run['status'] == 'complete'
    assert run['coverage']['candidatesDiscovered'] == 2
    assert run['coverage']['issuesTotal'] == 1
    assert run['issues'][0]['assigneeId'] == 'outsider'
    assert all('P-2' not in path and 'P-2' not in params.get('jql', '') for path, params in provider.calls)


def test_module_scope_keeps_subtask_when_only_its_parent_component_matches(tmp_path):
    class ParentComponentProvider(Provider):
        def get_json(self, path, params=None):
            if path.endswith('/search') and 'P-10' in (params or {}).get('jql', ''):
                self.calls.append((path, dict(params)))
                return {'startAt': 0, 'total': 1, 'issues': [{'key': 'P-10', 'fields': {
                    'summary': 'Old module parent', 'updated': '2026-01-01T00:00:00+0900',
                    'components': [{'name': 'M'}], 'epic': None, 'issuetype': {'name': 'Task'}}}]}
            result = super().get_json(path, params)
            if path.endswith('/search'):
                result['issues'][0]['fields'].update(components=[], epic=None,
                    assignee={'name': 'outsider'},
                    parent={'key': 'P-10'}, issuetype={'name': 'Sub-task', 'subtask': True})
            return result

    m = manager(tmp_path, ParentComponentProvider())
    run = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M']}, background=False)
    assert run['status'] == 'complete'
    assert run['coverage']['issuesTotal'] == 1
    assert run['summary']['created'] == 1
    assert run['summary']['comments'] == 1
    assert {issue['key'] for issue in run['issues']} == {'P-1', 'P-10'}


def test_resume_can_finish_when_resolved_parent_proves_ticket_outside_module(tmp_path):
    class RestoredParentProvider(Provider):
        visible = False

        def get_json(self, path, params=None):
            if path.endswith('/search') and 'P-10' in (params or {}).get('jql', ''):
                self.calls.append((path, dict(params)))
                rows = [{'key': 'P-10', 'fields': {'summary': 'Other parent',
                    'updated': '2026-01-01T00:00:00+0900', 'components': [{'name': 'Other'}],
                    'assignee': {'name': 'outsider'}, 'reporter': None,
                    'epic': None, 'issuetype': {'name': 'Task'}}}] if self.visible else []
                return {'startAt': 0, 'total': len(rows), 'issues': rows}
            result = super().get_json(path, params)
            if path.endswith('/search'):
                result['issues'][0]['fields'].update(components=[], epic=None,
                    assignee={'name': 'outsider'},
                    parent={'key': 'P-10'}, issuetype={'name': 'Sub-task', 'subtask': True})
            return result

    provider = RestoredParentProvider()
    m = manager(tmp_path, provider)
    first = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M']}, background=False)
    assert first['status'] == 'partial'
    assert first['coverage']['issuesTotal'] == 1
    provider.visible = True
    calls = len(provider.calls)
    run = m.resume(first['id'], background=False)
    assert run['status'] == 'complete'
    assert run['coverage']['issuesTotal'] == run['coverage']['issuesComplete'] == 0
    assert run['coverage']['missing'] == []
    assert run['summary']['totalEvents'] == 0
    assert len(provider.calls) == calls + 1


@pytest.mark.parametrize('assignee,retained', [({'name': 'a'}, True), (None, False)])
def test_module_voc_current_assignee_or_known_unassigned_without_extra_reads(tmp_path, assignee, retained):
    class VocProvider(Provider):
        def get_json(self, path, params=None):
            result = super().get_json(path, params)
            if path.endswith('/search'):
                result['issues'][0]['fields'].update(components=[{'name': 'Configured VOC'}],
                                                     epic=None, assignee=assignee)
            return result

    provider = VocProvider()
    m = manager(tmp_path, provider)
    m.client.s.voc_component = 'Configured VOC'
    run = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M']}, background=False)
    assert run['status'] == 'complete'
    assert {issue['key'] for issue in run['issues']} == ({'P-1'} if retained else set())
    assert run['summary']['created'] == run['summary']['comments'] == int(retained)
    assert run['coverage']['issuesTotal'] == int(retained)
    discovery = [params for path, params in provider.calls if path.endswith('/search') and 'project in' in params.get('jql', '')]
    assert 'assignee' in discovery[0]['fields'].split(',')
    assert len(provider.calls) == (4 if retained else 2)
    assert not [gap for gap in run['coverage']['missing'] if gap['source'] == 'moduleScope']


def test_voc_module_supplement_preserves_explicit_historical_person_filter(tmp_path):
    class VocProvider(Provider):
        def get_json(self, path, params=None):
            result = super().get_json(path, params)
            if path.endswith('/search'):
                result['issues'][0]['fields'].update(components=[{'name': 'Configured VOC'}], epic=None)
            if path.endswith('/comment'):
                result['comments'].append({'id': '2', 'created': '2026-11-11T11:00:00+0900',
                    'author': {'name': 'b'}, 'body': 'Selected outsider activity'})
                result['total'] = 2
            return result

    m = manager(tmp_path, VocProvider())
    m.client.s.voc_component = 'Configured VOC'
    m.people_loader = lambda: {'M': ['a'], 'Other': ['b']}
    run = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M'], 'people': ['b']}, background=False)
    assert run['status'] == 'complete'
    assert run['summary']['created'] == 0
    assert run['summary']['comments'] == 1
    assert {event['actorId'] for event in run['events']} == {'b'}
    assert run['issues'][0]['assigneeId'] == 'a'


@pytest.mark.parametrize('resolved_username,expected_status,retained', [
    (None, 'partial', True), ('a', 'complete', True), ('outsider', 'complete', False),
])
def test_voc_key_only_discovery_preserves_unknown_scope_until_rich_identity_resolves(
        tmp_path, resolved_username, expected_status, retained):
    class KeyOnlyVocProvider(Provider):
        def get_json(self, path, params=None):
            result = super().get_json(path, params)
            if path.endswith('/search'):
                assignee = {'key': 'JIRAUSER123', 'displayName': 'a'}
                if 'project in' not in params['jql'] and resolved_username:
                    assignee['name'] = resolved_username
                result['issues'][0]['fields'].update(
                    components=[{'name': 'Configured VOC'}], epic=None, assignee=assignee)
            return result

    provider = KeyOnlyVocProvider()
    m = manager(tmp_path, provider)
    m.client.s.voc_component = 'Configured VOC'
    run = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M']}, background=False)
    assert run['status'] == expected_status
    assert {issue['key'] for issue in run['issues']} == ({'P-1'} if retained else set())
    assert run['coverage']['issuesTotal'] == run['coverage']['issuesComplete'] == 1
    gaps = [gap for gap in run['coverage']['missing'] if gap['source'] == 'moduleScope']
    if resolved_username is None:
        assert gaps == [{'issueKey': 'P-1', 'source': 'moduleScope', 'reason': 'unresolvedCurrentHierarchy'}]
        assert run['coverage']['scopeComplete'] is False
        assert run['summary']['provisional'] is True
        # An uncertain identity must not be replaced by a coincidentally matching display name.
        assert run['issues'][0]['assigneeId'] == 'JIRAUSER123'
    else:
        assert not gaps
        assert run['coverage']['scopeComplete'] is True
    assert len(provider.calls) == 4  # Existing metadata, rich source and comment reads only.


@pytest.mark.parametrize('components,epic', [(['Configured VOC', 'Other'], None), (['Configured VOC'], 'P-200')])
def test_other_module_mapping_does_not_veto_selected_member_assignee(tmp_path, components, epic):
    class MappedVocProvider(Provider):
        def get_json(self, path, params=None):
            if path.endswith('/search') and 'P-200' in params['jql']:
                self.calls.append((path, dict(params)))
                return {'startAt': 0, 'total': 1, 'issues': [{'key': 'P-200', 'fields': {
                    'summary': 'Other Epic', 'issuetype': {'name': 'Epic'}, 'components': [],
                    'assignee': None, 'reporter': None}}]}
            result = super().get_json(path, params)
            if path.endswith('/search'):
                result['issues'][0]['fields'].update(components=[{'name': name} for name in components], epic=epic)
            return result

    provider = MappedVocProvider()
    m = manager(tmp_path, provider)
    m.client.s.voc_component = 'Configured VOC'
    m.people_loader = lambda: {'M': ['a'], 'Other': ['b']}
    m.plan_loader = lambda: {'wbs': [{'module': 'Other', 'name': 'Other delivery', 'epics': [{'key': 'P-200'}]}]}
    run = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M']}, background=False)
    assert run['status'] == 'complete'
    assert run['coverage']['candidatesDiscovered'] == 1
    assert run['coverage']['issuesTotal'] == run['coverage']['issuesComplete'] == 1
    assert run['summary']['created'] == run['summary']['comments'] == 1
    assert 'P-1' in {issue['key'] for issue in run['issues']}
    assert len(provider.calls) == (5 if epic else 4)


@pytest.mark.parametrize('member_role', ['assignee', 'reporter'])
def test_current_module_member_voc_survives_projected_discovery_publish_and_warm_cache(tmp_path, member_role):
    class ProjectedProvider(Provider):
        def get_json(self, path, params=None):
            params = params or {}
            assert 'P-2' not in path and 'P-2' not in params.get('jql', ''), 'Outside ticket must not hydrate.'
            result = super().get_json(path, params)
            if not path.endswith('/search'):
                return result
            fields = result['issues'][0]['fields']
            fields.update(components=[{'name': '사용자 VoC'}], epic=None,
                          assignee={'name': 'outsider'}, reporter={'name': 'reporter-bot'})
            fields[member_role] = {'name': 'a'}
            if 'project in' in params['jql']:
                result['issues'].append({'key': 'P-2', 'fields': {
                    **fields, 'assignee': {'name': 'outsider'}, 'reporter': {'name': 'reporter-bot'}}})
                result['total'] = 2
            # Jira returns only requested fields; an unrequested reporter cannot
            # accidentally make a deficient discovery projection pass this test.
            requested = set(params['fields'].split(','))
            for row in result['issues']:
                row['fields'] = {name: value for name, value in row['fields'].items() if name in requested}
                if 'expand' not in params:
                    row.pop('changelog', None)
            return result

    provider = ProjectedProvider()
    m = manager(tmp_path, provider)
    m.people_loader = lambda: {'ETL': ['a'], 'Other': ['b']}
    # Current membership must not depend on the configured generic VoC label.
    m.client.s.voc_component = 'Configured VOC'
    request = {'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['ETL']}
    run = m.create(request, background=False)
    assert run['status'] == 'complete'
    assert run['coverage']['scopeComplete'] is True
    assert run['coverage']['candidatesDiscovered'] == 2
    assert run['coverage']['issuesTotal'] == run['coverage']['issuesComplete'] == 1
    assert run['summary']['created'] == run['summary']['comments'] == 1
    assert {issue['key'] for issue in run['issues']} == {'P-1'}
    assert {event['issueKey'] for event in run['events']} == {'P-1'}
    assert m.get(run['id'])['events'] == run['events']
    assert len(provider.calls) == 4  # Status, discovery, rich batch, and comments; no identity REST reads.
    discovery = provider.calls[1][1]
    assert {'assignee', 'reporter'} <= set(discovery['fields'].split(','))
    assert run['metrics']['restRequests'] == 4

    count = len(provider.calls)
    warm = m.create(request, background=False)
    assert warm['status'] == 'complete'
    assert warm['summary']['created'] == warm['summary']['comments'] == 1
    assert {issue['key'] for issue in warm['issues']} == {'P-1'}
    assert warm['metrics']['restRequests'] == 1
    assert len(provider.calls) == count + 1
    assert provider.calls[-1][0].endswith('/search')
    assert 'project in' in provider.calls[-1][1]['jql']


def project_scope_provider(module, member, *, configured_member=False):
    class QueryProvider(Provider):
        def get_json(self, path, params=None):
            result = super().get_json(path, params)
            if not path.endswith('/search'):
                return result
            template = result['issues'][0]
            rows = []
            for key, assignee, reporter, component, updated in [
                ('P-1', member if configured_member else 'outsider', 'reporter-bot', 'Other', '2026-11-11T10:00:00+0900'),
                ('VOC-1', member, 'reporter-bot', '사용자 VoC', '2026-11-11T10:00:00+0900'),
                ('VOC-2', 'outsider', member, '사용자 VoC', '2026-11-11T10:00:00+0900'),
                ('P-2', 'outsider', 'reporter-bot', module, '2026-11-11T10:00:00+0900'),
                ('VOC-4', 'outsider', 'reporter-bot', '사용자 VoC', '2026-11-11T10:00:00+0900'),
                ('VOC-5', member, 'reporter-bot', '사용자 VoC', '2026-01-01T10:00:00+0900'),
            ]:
                row = deepcopy(template)
                row['key'] = key
                row['fields'].update(assignee={'name': assignee}, reporter={'name': reporter},
                                     components=[{'name': component}], epic=None, updated=updated)
                rows.append(row)

            # Use the existing parser for parentheses/escaping, then evaluate only
            # the supported boundary predicates. Unknown clauses fail this fake.
            body, _ = _split_order(tokenize(params['jql']))
            query = _Parser(body).parse()

            def matches(node, row):
                if isinstance(node, (And, Or)):
                    results = [matches(child, row) for child in node.children]
                    return all(results) if isinstance(node, And) else any(results)
                assert isinstance(node, Atom)
                tokens = tokenize(node.value)
                field, op = tokens[0].value.casefold(), tokens[1].value.casefold()
                # A roster module need not name a Jira component. Jira validates
                # these predicates before searching, even inside an OR union.
                if field in {'component', 'components'}:
                    raise UpstreamError(400, path, 'The component does not exist.')
                values = [json.loads(token.value) if token.kind == 'STRING' else token.value
                          for token in tokens[2:] if token.kind in {'STRING', 'WORD'}]
                fields = row['fields']
                if field == 'updated' and op == '>=':
                    return fields['updated'][:10] >= values[0][:10]
                assert op == 'in', node.value
                actual = {'project': row['key'].split('-')[0], 'key': row['key'],
                          'assignee': fields['assignee']['name'], 'reporter': fields['reporter']['name'],
                          'component': fields['components'][0]['name'],
                          'components': fields['components'][0]['name']}
                assert field in actual, node.value
                return actual[field] in values

            matched = [row for row in rows if matches(query, row)]
            requested = set(params['fields'].split(','))
            for row in matched:
                row['fields'] = {name: value for name, value in row['fields'].items() if name in requested}
                if 'expand' not in params:
                    row.pop('changelog', None)
            return {'startAt': 0, 'total': len(matched), 'issues': matched}

    return QueryProvider()


@pytest.mark.parametrize('selected,module,member', [
    (False, 'ETL', 'a'),
    (True, 'ETL', 'a'),
    (True, 'ETL "delivery"\\module', 'quoted"person\\name'),
])
def test_module_discovery_includes_cross_project_current_union_and_preserves_date_bound(
        tmp_path, selected, module, member):
    provider = project_scope_provider(module, member)
    m = manager(tmp_path, provider)
    m.people_loader = lambda: {module: [member]}
    request = {'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': [module] if selected else []}
    run = m.create(request, background=False)
    assert run['status'] == 'complete'
    expected = {'VOC-1', 'VOC-2', 'P-2'} if selected else {'P-1', 'P-2'}
    assert {issue['key'] for issue in run['issues']} == expected
    assert {event['issueKey'] for event in run['events']} == expected
    assert run['coverage']['candidatesDiscovered'] == (4 if selected else 2)
    assert run['summary']['created'] == run['summary']['comments'] == len(expected)
    assert all('VOC-4' not in path and 'VOC-5' not in path for path, _ in provider.calls)


def test_legacy_paused_module_run_rediscovers_cross_project_candidates_and_reuses_rich_sources(tmp_path):
    provider = project_scope_provider('ETL', 'a', configured_member=True)
    m = manager(tmp_path, provider)
    m.people_loader = lambda: {'ETL': ['a']}
    # Seed the durable, complete project-only candidate/source checkpoints that
    # an older process could leave behind before its final publish was paused.
    first = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12'}, background=False)
    assert {issue['key'] for issue in first['issues']} == {'P-1', 'P-2'}
    _, _, snapshot, cp = m.store.read(first['id'])
    snapshot['request']['modules'] = ['ETL']
    snapshot['status'] = 'paused'
    cp.pop('moduleScopeVersion', None)
    m.store.save(first['id'], snapshot, cp)
    count = len(provider.calls)

    resumed = m.resume(first['id'], background=False)
    assert resumed['status'] == 'complete'
    assert resumed['asOf'] == first['asOf']
    assert {issue['key'] for issue in resumed['issues']} == {'P-1', 'P-2', 'VOC-1', 'VOC-2'}
    assert resumed['summary']['created'] == resumed['summary']['comments'] == 4
    new_calls = provider.calls[count:]
    assert len(new_calls) == 4  # Rediscovery, new rich batch, and two new comment reads.
    assert all(key not in path and key not in params.get('jql', '')
               for path, params in new_calls for key in ('P-1', 'P-2'))
    assert resumed['metrics']['cacheHits'] > first['metrics']['cacheHits']
