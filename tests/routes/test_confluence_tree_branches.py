from types import SimpleNamespace

import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy, read_scope
from app.jira.confluence_service import scope_key
from app.services import confluence_tree as tree


def node(cid, parent='', children=False):
    return {'id': str(cid), 'title': f'Page {cid}', 'space': {'key': 'DL'}, 'version': {'number': 1},
            'ancestors': [{'id': parent, 'title': f'Page {parent}'}] if parent else [],
            'children': {'page': {'size': 1 if children else 0}}}


@pytest.fixture
def client():
    cache = Cache(':memory:'); cache.configure(CachePolicy())
    result = SimpleNamespace(env='mock', cache=cache, s=SimpleNamespace(confluence_base='https://docs.test'))
    yield result
    cache.close()


def test_bfs_roots_then_branches_and_entire_warm_snapshot_without_upstream(client):
    calls = []
    def get(url, params):
        calls.append(url)
        assert params['expand'] == 'space,version,ancestors,children.page'
        return {'results': [node(1, children=True), node(2)] if '/space/' in url else [node(3, '1')]}
    client._conf_get_json = get
    assert tree.snapshot(client, 'DL') == {'batches': [], 'jobs': [{'parent': '', 'start': 0, 'depth': 0}]}
    assert len(tree.branch(client, 'DL')['results']) == 2
    assert tree.snapshot(client, 'DL')['jobs'] == [{'parent': '1', 'start': 0, 'depth': 1}]
    tree.branch(client, 'DL', '1')
    warm = tree.snapshot(client, 'DL')
    assert warm['jobs'] == []
    assert {row['id'] for b in warm['batches'] for row in b['results']} == {'1', '2', '3'}
    assert len(calls) == 2  # Heads primed by the root list; known leaves never fetched.
    source = warm['batches'][0]['cacheInfo']['fetchedAt']
    assert tree.snapshot(client, 'DL')['batches'][0]['cacheInfo']['fetchedAt'] == source


def test_stale_snapshot_returns_immediately_and_keeps_only_affected_branch_jobs(client):
    client._conf_get_json = lambda *a, **k: {'results': [node(1, children=True), node(2, children=True)]}
    tree.branch(client, 'DL')
    client._conf_get_json = lambda url, **k: {'results': [node(3, '1')] if '/1/' in url else [node(4, '2')]}
    tree.branch(client, 'DL', '1'); tree.branch(client, 'DL', '2')
    tree.invalidate_changed_branches(client, 'create_page', {'space': {'key': 'DL'}},
                                    {'id': '5', 'parentId': '1'}, ['1', '5'])
    snap = tree.snapshot(client, 'DL')
    assert snap['jobs'] == [{'parent': '1', 'start': 0, 'depth': 1, 'refresh': True}]
    assert client.cache.get(tree.key(client, 'DL', '', 0)) is not None
    assert client.cache.get(tree.key(client, 'DL', '2', 0)) is not None
    with read_scope(strict=True):
        tree.branch(client, 'DL', '1')
    tree.invalidate_changed_branches(client, 'move_page', {'parent_id': '2'},
                                    {'id': '3', 'parentId': '2', 'previousParentId': '1'}, ['3', '2'])
    assert {job['parent'] for job in tree.snapshot(client, 'DL')['jobs']} == {'1', '2'}
    assert client.cache.get(tree.key(client, 'DL', '', 0)) is not None


def test_content_only_changes_preserve_tree(client):
    client._conf_get_json = lambda *a, **k: {'results': [node(1)]}
    tree.branch(client, 'DL')
    for kind in ['create_comment', 'upload_attachment', 'update_page']:
        tree.invalidate_changed_branches(client, kind, {'body': 'new'}, {'id': '1'}, ['1'])
    assert tree.snapshot(client, 'DL')['jobs'] == []


def test_complete_bulk_pass_seeds_parent_caches_without_refreshing_age(client):
    from app.jira import confluence_service as reads
    calls = []
    def get(url, params):
        calls.append(url)
        return {'results': [node(1), node(2, '1'), node(3, '1')]}
    client._conf_get_json = get
    reads.tree_pages(client, 'DL')
    tree.seed_complete_index(client, 'DL')
    snap = tree.snapshot(client, 'DL')
    assert snap['jobs'] == [] and len(calls) == 1
    assert [row['id'] for row in snap['batches'][0]['results']] == ['1']
    assert [row['id'] for row in snap['batches'][1]['results']] == ['2', '3']
    assert all(not row['hasChildren'] for row in snap['batches'][1]['results'])
    assert len({b['cacheInfo']['fetchedAt'] for b in snap['batches']}) == 1


def test_move_reconciliation_without_head_only_invalidates_old_and_new_parent(client):
    from app.jira import confluence_service as reads
    client._conf_get_json = lambda *a, **k: {'results': [node(1), node(2), node(3, '1')]}
    reads.tree_pages(client, 'DL'); tree.seed_complete_index(client, 'DL')
    tree.invalidate_changed_branches(client, 'move_page', {'parent_id': '2'}, {'id': '3', 'parentId': '2'}, ['3', '2'])
    # Root stayed fresh, and the old branch is revalidated even without a cached confhead.
    assert client.cache.get(tree.key(client, 'DL', '', 0)) is not None
    assert client.cache.get(tree.key(client, 'DL', '1', 0)) is None
    assert client.cache.get(tree.key(client, 'DL', '2', 0)) is None


def test_incomplete_bulk_index_cannot_replace_existing_branch_cache(client):
    from app.jira import confluence_service as reads
    client._conf_get_json = lambda *a, **k: {'results': [node(1)]}
    tree.branch(client, 'DL')
    client._conf_get_json = lambda *a, **k: {'results': [node(2)], '_links': {'next': '?start=100'}}
    reads.tree_pages(client, 'DL'); tree.seed_complete_index(client, 'DL')
    assert tree.snapshot(client, 'DL')['batches'][0]['results'][0]['id'] == '1'


def test_first_child_creation_reopens_a_previously_confirmed_leaf(client):
    client._conf_get_json = lambda *a, **k: {'results': [node(1)]}
    tree.branch(client, 'DL')
    tree.invalidate_changed_branches(client, 'create_page', {'space': {'key': 'DL'}},
                                    {'id': '2', 'parentId': '1'}, ['1', '2'])
    assert [job['parent'] for job in tree.snapshot(client, 'DL')['jobs']] == ['1']
    client._conf_get_json = lambda *a, **k: {'results': [node(2, '1')]}
    tree.branch(client, 'DL', '1')
    # Root metadata may still say no children. A newly collected branch must survive
    # subsequent snapshots without another upstream request.
    assert any(row['id'] == '2' for b in tree.snapshot(client, 'DL')['batches'] for row in b['results'])


def test_large_metadata_batch_uses_server_cursor_and_remembers_limit_rejection(client):
    from app.auth.base import UpstreamError
    from app.jira import confluence_service as reads
    calls = []
    def get(url, params):
        calls.append((params['start'], params['limit']))
        if params['limit'] > 100:
            raise UpstreamError(400, url)
        return {'results': [node(params['start'] + 1)], '_links': {'next': '?start=37'} if params['start'] == 0 else {}}
    client._conf_get_json = get
    assert reads.tree_pages(client, 'DL', limit=500)['nextStart'] == 37
    reads.tree_pages(client, 'DL', start=37, limit=500)
    tree.seed_complete_index(client, 'DL', 500)
    assert calls == [(0, 500), (0, 100), (37, 100)]
    assert tree.snapshot(client, 'DL')['jobs'] == []
    assert {r['id'] for b in tree.snapshot(client, 'DL')['batches'] for r in b['results']} == {'1', '38'}


def test_bulk_retains_ancestor_stubs_when_only_descendant_is_returned(client):
    from app.jira import confluence_service as reads
    client._conf_get_json = lambda *a, **k: {'results': [node(2, '1')]}
    reads.tree_pages(client, 'DL'); tree.seed_complete_index(client, 'DL')
    assert {r['id'] for b in tree.snapshot(client, 'DL')['batches'] for r in b['results']} == {'1', '2'}
