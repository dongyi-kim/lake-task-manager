"""Favorite CQL metadata reuses cache but cannot certify a complete branch."""
from types import SimpleNamespace

import pytest

from app.auth.base import UpstreamUnavailable
from app.infra.cache import Cache
from app.infra.cache_policy import CachePolicy
from app.jira import confluence_service as reads
from app.services.confluence_tree_focus import favorite_pages


@pytest.fixture
def client():
    cache = Cache(':memory:'); cache.configure(CachePolicy())
    result = SimpleNamespace(env='mock', cache=cache, s=SimpleNamespace(confluence_base='https://docs.test'))
    yield result
    cache.close()


def page(cid, ancestors=(), space='DL'):
    return {'id':str(cid),'title':f'Page {cid}','space':{'key':space},'version':{'number':1},
            'ancestors':[{'id':str(a),'title':f'Page {a}'} for a in ancestors]}


def test_deep_paths_and_descendants_share_one_batch_then_cache_and_mutation_refresh(client):
    calls=[]
    def get(url,params):
        calls.append((url,params))
        return {'results':[page(4,(1,2,3)),page(8,(1,2,3,4,5,6,7))], '_links':{'next':'?start=37'} if params['start']==0 else {}}
    client._conf_get_json=get
    result=favorite_pages(client,'DL','4,8')
    assert [a['id'] for a in result['results'][0]['ancestors']]==['1','2','3']
    assert result['results'][1]['id']=='8' and result['nextStart']==37
    assert len(calls)==1 and 'ancestor IN (4,8)' in calls[0][1]['cql']
    assert 'body' not in calls[0][1]['expand']
    assert favorite_pages(client,'DL','8,4,4')==result and len(calls)==1
    favorite_pages(client,'DL','4,8',37)
    assert len(calls)==2 and client.cache.entries_by_prefix('confbranch:')=={}
    reads.invalidate(client,['4'])
    favorite_pages(client,'DL','4,8')
    assert len(calls)==3


def test_wrong_space_and_incomplete_favorite_query_never_become_empty_success(client):
    client._conf_get_json=lambda url,params: {'results':[page(4,space='OTHER')]}
    with pytest.raises(UpstreamUnavailable): favorite_pages(client,'DL','4')
    reads.invalidate(client,['4'])
    calls=[]
    def get(url,params):
        calls.append(url)
        return {'results':[page(8,(9,))]}
    client._conf_get_json=get
    for _ in range(2):
        with pytest.raises(UpstreamUnavailable): favorite_pages(client,'DL','4')
    assert len(calls)==2
    assert not client.cache.entries_by_prefix('conftree:')


def test_favorite_route_limits_scope_and_batch_without_upstream(client):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes.confluence import build_confluence_router
    client.s.search_confluence_spaces=['DL']
    client._conf_get_json=lambda *a,**k: pytest.fail('Invalid scope must not query upstream')
    app=FastAPI(); app.include_router(build_confluence_router(get_client=lambda:client))
    with TestClient(app) as http:
        assert http.get('/api/confluence/tree/favorite',params={'space':'OTHER','page_ids':'4'}).status_code==403
        assert http.get('/api/confluence/tree/favorite',params={'space':'DL','page_ids':','.join(map(str,range(21)))}).status_code==422
        assert http.get('/api/confluence/tree/favorite',params={'space':'DL','page_ids':'4 OR 1=1'}).status_code==422
