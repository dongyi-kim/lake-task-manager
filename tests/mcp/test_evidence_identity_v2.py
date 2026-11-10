"""Normalized evidence identity keeps content exact without exposing actor profiles."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import read_scope
from app.jira.evidence_service import get_evidence_page
from app.local_mcp.schemas.tickets import EvidencePage


@pytest.fixture
def source_client():
    cache = Cache(":memory:")
    rows, calls = [], []
    def fetch(path, params):
        calls.append((path, deepcopy(params)))
        if path.endswith('/comment'):
            start = params['startAt']
            return {'startAt': start, 'total': len(rows), 'comments': deepcopy(rows[start:start + 100])}
        return {'changelog': {'startAt': 0, 'total': len(rows), 'histories': deepcopy(rows)}}
    client = SimpleNamespace(env='mock', s=SimpleNamespace(cache_ttl_seconds=600), cache=cache,
                             provider=SimpleNamespace(get_json=fetch), _looks_anonymous=lambda _: False)
    yield client, rows, calls
    cache.close()


def actor(identity):
    return {'accountId': 'account-' + identity, 'name': identity, 'key': 'key-' + identity,
            'displayName': '이름🙂 (TEAM)', 'self': '/user?username=' + identity, 'timeZone': 'Asia/Seoul',
            'avatarUrls': {'48x48': 'private-avatar' * 2000}, 'emailAddress': 'private@example.test',
            'active': True, 'customPrivateProfile': {'secret': 'not evidence'}}


@pytest.mark.parametrize('kind', ['comments', 'history'])
def test_v2_preserves_exact_content_distinct_actor_roles_and_raw_cache(source_client, kind):
    client, rows, calls = source_client
    original = {'id': 17, 'author': actor('alice'), 'updateAuthor': actor('bob'),
                'created': '2026-11-01T00:00:00.123+0900', 'updated': '2026-11-02T00:00:00.456Z',
                'self': '/issue/comment/17', 'visibility': {'type': 'role', 'value': 'restricted'},
                'jsdPublic': False, 'unknownMetadata': {'preserve': [1, '🙂']}}
    if kind == 'comments':
        original.update(body='[~alice] e\u0301🙂\\\n<exact>', renderedBody='<p data-x="1">🙂&amp;</p>')
    else:
        original['items'] = [{'field': 'description', 'fieldId': 'custom_17', 'fieldtype': 'custom',
                              'from': '01', 'to': '02', 'fromString': '<old>🙂', 'toString': '[~alice]',
                              'unknownItem': {'keep': True}}]
    rows.append(deepcopy(original))
    page = get_evidence_page(client, 'DL-1', kind=kind)
    EvidencePage.model_validate(page)
    assert page['recordFormat'] == 'jira-evidence-v2'
    record, = page['items']
    expected = deepcopy(original)
    for role in ('author', 'updateAuthor'):
        expected[role] = {field: original[role][field] for field in
                          ('accountId', 'name', 'key', 'displayName', 'self', 'timeZone')}
    expected.update(authorId='account-alice', authorDisplayName='이름🙂 (TEAM)')
    assert record == expected
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 8192
    assert rows == [original] and len(calls) == 1
    cache_key = ('comments:mock:DL-1:evidence:v1:0:100' if kind == 'comments'
                 else 'changelog:mock:DL-1:evidence:v1')
    assert client.cache.get(cache_key)['items'][0]['author'] == original['author']


@pytest.mark.parametrize('profile', [None, {}, {'displayName': 'Same Name'},
                                    {'self': '/user?username=not-an-id', 'emailAddress': 'not-an-id'}])
def test_missing_identity_never_borrows_update_author_or_profile_fields(source_client, profile):
    client, rows, _ = source_client
    rows.append({'id': '1', 'body': 'exact', 'author': profile, 'updateAuthor': actor('bob')})
    record, = get_evidence_page(client, 'DL-1')['items']
    assert record['authorId'] is None
    assert record['author'] == (None if profile is None else {k: v for k, v in profile.items()
                               if k in {'accountId', 'name', 'key', 'displayName', 'self', 'timeZone'}})
    assert record['updateAuthor']['name'] == 'bob'


def test_chunks_hash_normalized_unicode_record_and_ignore_only_profile_edits(source_client):
    client, rows, _ = source_client
    rows.append({'id': '1', 'author': actor('alice'), 'body': '🙂 e\u0301 "quote" \\\n' * 3000})
    fragments, digests, args = [], set(), {'key': 'DL-1'}
    while True:
        with read_scope(cache_mode='live'):
            page = get_evidence_page(client, **args)
        assert page['recordFormat'] == 'jira-evidence-v2'
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= 8192
        item, = page['items']
        assert item['recordFormat'] == 'jira-evidence-v2'
        fragments.append(item['recordJson']); digests.add(item['recordSha256'])
        if page['nextCall'] is None:
            break
        args = {k: v for k, v in page['nextCall']['arguments'].items() if k != 'cache_mode'}
        assert page['nextCall']['arguments']['cache_mode'] == 'live'
    normalized = ''.join(fragments)
    assert len(digests) == 1 and digests == {hashlib.sha256(normalized.encode()).hexdigest()}
    decoded = json.loads(normalized)
    assert decoded['body'] == rows[0]['body'] and 'avatarUrls' not in decoded['author']
    rows[0]['author']['avatarUrls'] = {'new': 'profile-only'}
    with read_scope(cache_mode='live'):
        same = get_evidence_page(client, 'DL-1')['items'][0]
    assert same['recordSha256'] in digests
    rows[0]['body'] += 'actual content edit'
    with read_scope(cache_mode='live'):
        changed = get_evidence_page(client, 'DL-1')['items'][0]
    assert changed['recordSha256'] not in digests


def test_wire_projection_does_not_restamp_cache_or_relax_live(source_client):
    client, rows, calls = source_client
    rows.extend({'id': str(i), 'author': actor('alice'), 'body': 'body ' * 2000} for i in range(2))
    first = get_evidence_page(client, 'DL-1')
    assert first['recordFormat'] == 'jira-evidence-v2'
    key = 'comments:mock:DL-1:evidence:v1:0:100'
    stamp = client.cache.metadata(key)['fetchedAt']
    get_evidence_page(client, 'DL-1')
    assert len(calls) == 1 and client.cache.metadata(key)['fetchedAt'] == stamp
    for _ in range(2):
        with read_scope(cache_mode='live'):
            get_evidence_page(client, 'DL-1')
    assert len(calls) == 3
