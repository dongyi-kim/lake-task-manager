"""One durable Confluence history, bounded independently from ticket activity."""
import json

from app.infra.cache import Cache


def test_hundred_documents_deduplicate_aliases_and_survive_ticket_activity_and_restart(tmp_path, monkeypatch):
    clock=[1000.0]
    monkeypatch.setattr('app.infra.cache.time.time', lambda:clock[0])
    path=tmp_path/'cache.sqlite3'
    cache=Cache(path)
    for cid in range(1,106):
        clock[0]+=1
        cache.touch_recent(f'https://docs.test/wiki/spaces/DL/pages/{cid}/title', 'confluence', f'Doc {cid}', data={'space':'DL'})
    for cid in range(250):
        clock[0]+=1
        cache.touch_recent(f'/browse/DL-{cid}', 'jira', 'Task')
    rows=cache.recent_items(100,'confluence')
    assert len(rows)==100 and rows[0]['title']=='Doc 105' and rows[-1]['title']=='Doc 6'
    cache.touch_recent('https://docs.test/wiki/display/DL/Renamed', 'confluence', 'Renamed',
                       data={'pageId':'6','space':'DL'})
    assert cache.recent_items(100,'confluence')[0]['url']=='https://docs.test/wiki/pages/viewpage.action?pageId=6'
    cache.touch_recent('https://docs.test/wiki/pages/viewpage.action?pageId=6#old', 'confluence', 'Old import',
                       opened_at=500)
    assert cache.recent_items(100,'confluence')[0]['title']=='Renamed'
    cache.close(); cache=Cache(path)
    assert len(cache.recent_items(1000,'confluence'))==100
    assert cache.recent_items(100,'confluence')[0]['title']=='Renamed'
    assert len(cache.recent_items(1000,'jira'))==200
    cache.forget_recent('https://docs.test/wiki/spaces/DL/pages/6/other#heading')
    assert len(cache.recent_items(100,'confluence'))==99
    cache.close()


def test_legacy_sqlite_alias_migration_preserves_newest_title_and_visit_time(tmp_path):
    path=tmp_path/'legacy.sqlite3'
    cache=Cache(path)
    for url,title,stamp,data in [
        ('https://docs.test/wiki/spaces/DL/pages/42/title','Older',100,{}),
        ('https://docs.test/wiki/display/DL/New','Newest',200,{'pageId':'42'}),
        ('https://other.test/wiki/pages/viewpage.action?pageId=42','Other server',300,{}),
    ]:
        cache._conn.execute('INSERT INTO recent(url,kind,title,opened_at,data) VALUES (?,?,?,?,?)',
                            (url,'confluence',title,stamp,json.dumps(data)))
    cache._conn.commit(); cache.close()
    cache=Cache(path)
    rows=cache.recent_items(100,'confluence')
    assert [(r['title'],r['openedAt']) for r in rows]==[('Other server',300),('Newest',200)]
    cache.close()


def test_recent_http_history_needs_no_identity_or_upstream_and_validates_import_time():
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes.resources import build_resource_router
    cache=Cache(':memory:')
    def no_client():
        raise AssertionError('Recent history must not authenticate or query upstream')
    app=FastAPI(); app.include_router(build_resource_router(get_client=no_client,cache=cache,settings=SimpleNamespace(app_port=4457)))
    with TestClient(app) as http:
        payload={'url':'https://docs.test/wiki/pages/viewpage.action?pageId=1','kind':'confluence','title':'Imported','openedAt':1000}
        assert http.post('/api/recent',json=payload).status_code==200
        assert http.get('/api/recent?kind=confluence&limit=100').json()[0]['openedAt']==1000
        assert http.post('/api/recent',json={**payload,'openedAt':-1}).status_code==422
        assert http.delete('/api/recent',params={'url':payload['url']}).status_code==200
        assert http.get('/api/recent').json()==[]
    cache.close()
