from types import SimpleNamespace

import pytest

from app.auth.base import UpstreamError, UpstreamUnavailable
from app.jira.jql_scheduling import BoundedQueryScheduler
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
            "updated": "2026-11-11T10:00:00+0900", "creator": {"name": "a"}, "assignee": {"name": "a"},
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
                    'components': [{'name': 'Other'}], 'epic': None, 'issuetype': {'name': 'Task'}}}
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
                    'epic': None, 'issuetype': {'name': 'Task'}}}] if self.visible else []
                return {'startAt': 0, 'total': len(rows), 'issues': rows}
            result = super().get_json(path, params)
            if path.endswith('/search'):
                result['issues'][0]['fields'].update(components=[], epic=None,
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


@pytest.mark.parametrize('assignee,expected_status', [({'name': 'a'}, 'complete'), (None, 'partial')])
def test_module_voc_current_assignee_survives_both_prune_and_publish_without_extra_reads(tmp_path, assignee, expected_status):
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
    assert run['status'] == expected_status
    assert {issue['key'] for issue in run['issues']} == {'P-1'}
    assert run['summary']['created'] == run['summary']['comments'] == 1
    assert run['coverage']['issuesTotal'] == 1
    discovery = [params for path, params in provider.calls if path.endswith('/search') and 'project in' in params.get('jql', '')]
    assert 'assignee' in discovery[0]['fields'].split(',')
    assert len(provider.calls) == 4  # Existing status, metadata, batch source, and comment source only.
    assert bool([gap for gap in run['coverage']['missing'] if gap['source'] == 'moduleScope']) == (assignee is None)


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
def test_voc_other_module_mapping_is_pruned_before_sources_even_when_assignee_matches(tmp_path, components, epic):
    class MappedVocProvider(Provider):
        def get_json(self, path, params=None):
            if path.endswith('/search'):
                assert 'project in' in params['jql'], 'Explicitly unrelated VoC must not hydrate heavy sources.'
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
    assert run['coverage']['issuesTotal'] == 0
    assert run['summary']['totalEvents'] == 0
    assert run['issues'] == []
    assert len(provider.calls) == 2  # Status metadata plus candidate metadata only.
