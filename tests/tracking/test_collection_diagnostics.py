from copy import deepcopy
import re

from app.auth.base import UpstreamError, UpstreamUnavailable
from app.tracking.diagnostics import issue_counts
from test_backend_manager import Provider, manager


class DiagnosticProvider(Provider):
    fail_hydration = False

    def get_json(self, path, params=None):
        params = params or {}
        if self.fail_hydration and path.endswith('/search') and params['jql'].startswith('key in'):
            self.calls.append((path, dict(params)))
            raise UpstreamUnavailable('rich source offline')
        result = super().get_json(path, params)
        if not path.endswith('/search'):
            return result
        template = result['issues'][0]
        keys = set(re.findall(r'"(P-\d+)"', params['jql'])) if params['jql'].startswith('key in') else None
        rows = []
        for key, component, assignee in [('P-1', '사용자 VoC', 'a'), ('P-2', '사용자 VoC', 'outsider'),
                                         ('P-3', 'M', 'outsider'), ('P-4', '사용자 VoC', 'a')]:
            if keys is not None and key not in keys:
                continue
            raw = deepcopy(template)
            raw['key'] = key
            raw['fields'].update(components=[{'name': component}], epic=None, parent=None,
                                 assignee={'name': assignee}, reporter={'name': 'report-bot'})
            requested = set(params['fields'].split(','))
            raw['fields'] = {name: value for name, value in raw['fields'].items() if name in requested}
            if not params.get('expand'):
                raw.pop('changelog', None)
            rows.append(raw)
        return {'startAt': 0, 'total': len(rows), 'issues': rows}


def stages(run):
    return {row['id']: (row['total'], row['voc'], row['complete']) for row in run['coverage']['funnel']['stages']}


def test_collection_counts_distinguish_module_pruning_and_persist_without_extra_reads(tmp_path):
    provider = DiagnosticProvider()
    m = manager(tmp_path, provider)
    request = {'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M']}
    run = m.create(request, background=False)
    assert run['status'] == 'complete'
    assert run['coverage']['funnel']['vocComponent'] == '사용자 VoC'
    assert stages(run) == {'discovery': (4, 3, True), 'module': (3, 2, True), 'hydrated': (3, 2, True),
                           'analyzed': (3, 2, True), 'published': (3, 2, True)}
    assert len(provider.calls) == run['metrics']['restRequests'] == 6
    assert stages(m.get(run['id'])) == stages(run)
    assert stages(m.list()[0]) == stages(run)
    assert len(provider.calls) == 6
    warm = m.create(request, background=False)
    assert stages(warm) == stages(run)
    assert warm['metrics']['restRequests'] == 1


def test_interrupted_stage_is_unknown_until_resume_not_an_apparent_zero(tmp_path):
    provider = DiagnosticProvider()
    provider.fail_hydration = True
    m = manager(tmp_path, provider)
    run = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M']}, background=False)
    assert run['status'] == 'paused'
    assert stages(run) == {'discovery': (4, 3, True), 'module': (3, 2, True), 'hydrated': (None, None, False),
                           'analyzed': (None, None, False), 'published': (None, None, False)}
    provider.fail_hydration = False
    resumed = m.resume(run['id'], background=False)
    assert resumed['status'] == 'complete'
    assert stages(resumed)['published'] == (3, 2, True)


def test_empty_module_results_record_real_zeroes(tmp_path):
    m = manager(tmp_path, DiagnosticProvider())
    m.people_loader = lambda: {'Empty': ['nobody']}
    run = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['Empty']}, background=False)
    assert run['status'] == 'complete'
    assert stages(run) == {'discovery': (4, 3, True), 'module': (0, 0, True), 'hydrated': (0, 0, True),
                           'analyzed': (0, 0, True), 'published': (0, 0, True)}


def test_unknown_components_are_not_reported_as_zero_and_counts_deduplicate_keys():
    raw = {'key': 'P-1', 'fields': {'components': [{'name': '  사용자 ＶＯＣ  '}]}}
    assert issue_counts([raw, deepcopy(raw)], '사용자 VoC') == {'total': 1, 'voc': 1}
    assert issue_counts([raw, {'key': 'P-2', 'fields': {}}], '사용자 VoC') == {'total': 2, 'voc': None}


def test_saved_counts_keep_the_collection_component_and_legacy_runs_stay_unrecorded(tmp_path):
    m = manager(tmp_path, DiagnosticProvider())
    run = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M']}, background=False)
    m.client.s.voc_component = 'Changed component'
    assert m.get(run['id'])['coverage']['funnel']['vocComponent'] == '사용자 VoC'
    assert stages(m.get(run['id']))['published'] == (3, 2, True)
    _, _, snapshot, cp = m.store.read(run['id'])
    snapshot['coverage'].pop('funnel')
    m.store.save(run['id'], snapshot, cp)
    assert 'funnel' not in m.get(run['id'])['coverage']


def test_person_filter_drop_is_visible_between_analysis_and_published_results(tmp_path):
    class NoHistoricalSelector(DiagnosticProvider):
        def get_json(self, path, params=None):
            if 'updatedBy' in (params or {}).get('jql', ''):
                self.calls.append((path, dict(params)))
                raise UpstreamError(400, path, 'Historical selector unsupported')
            return super().get_json(path, params)

    m = manager(tmp_path, NoHistoricalSelector())
    m.people_loader = lambda: {'M': ['a'], 'Other': ['b']}
    run = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M'], 'people': ['b']}, background=False)
    assert run['status'] == 'complete'
    assert run['coverage']['candidateStrategy'] == 'broadFallback'
    assert stages(run)['analyzed'] == (3, 2, True)
    assert stages(run)['published'] == (0, 0, True)
    assert run['issues'] == []


def test_interrupted_analysis_preserves_partial_counts_and_resume_finishes(tmp_path):
    class InterruptedComments(DiagnosticProvider):
        offline = True

        def get_json(self, path, params=None):
            if self.offline and path.endswith('/comment'):
                self.calls.append((path, dict(params or {})))
                raise UpstreamUnavailable('comments offline')
            return super().get_json(path, params)

    provider = InterruptedComments()
    m = manager(tmp_path, provider)
    run = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M']}, background=False)
    assert run['status'] == 'paused'
    assert stages(run)['hydrated'] == (3, 2, True)
    assert stages(run)['analyzed'] == stages(run)['published'] == (1, 1, False)
    provider.offline = False
    resumed = m.resume(run['id'], background=False)
    assert resumed['status'] == 'complete'
    assert stages(resumed)['analyzed'] == stages(resumed)['published'] == (3, 2, True)


def test_context_ancestors_are_not_counted_as_collected_targets(tmp_path):
    class ParentProvider(DiagnosticProvider):
        def get_json(self, path, params=None):
            if path.endswith('/search') and '"P-10"' in params['jql']:
                self.calls.append((path, dict(params)))
                return {'startAt': 0, 'total': 1, 'issues': [{'key': 'P-10', 'fields': {
                    'summary': 'Parent context', 'created': '2025-01-01T00:00:00+0900',
                    'updated': '2025-01-01T00:00:00+0900', 'issuetype': {'name': 'Task'},
                    'components': [{'name': '사용자 VoC'}], 'epic': None, 'parent': None}}]}
            result = super().get_json(path, params)
            for row in result.get('issues', []) if isinstance(result, dict) else []:
                if row['key'] == 'P-1':
                    row['fields']['parent'] = {'key': 'P-10'}
            return result

    m = manager(tmp_path, ParentProvider())
    run = m.create({'startDate': '2026-11-10', 'endDate': '2026-11-12', 'modules': ['M']}, background=False)
    assert run['status'] == 'complete'
    assert len(run['issues']) == 4
    assert next(issue for issue in run['issues'] if issue['key'] == 'P-10')['contextOnly'] is True
    assert stages(run)['published'] == (3, 2, True)
