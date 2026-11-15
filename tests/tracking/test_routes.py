"""The asynchronous HTTP contract retains saved results and account isolation."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.tracking import build_tracking_router
from tracking.test_backend_manager import manager, Provider


def test_http_create_poll_reopen_validation_and_account_boundary(tmp_path):
    provider = Provider()
    engine = manager(tmp_path, provider)
    app = FastAPI()
    app.include_router(build_tracking_router(get_client=lambda: engine.client, manager=engine))
    with TestClient(app) as http:
        response = http.post('/api/tracking/runs', json={'startDate':'2026-11-10','endDate':'2026-11-12'})
        assert response.status_code == 202
        run_id = response.json()['id']
        engine.wait(run_id, timeout=5)
        response = http.get('/api/tracking/runs/' + run_id)
        assert response.status_code == 200 and response.json()['status'] == 'complete'
        count = len(provider.calls)
        assert http.get('/api/tracking/runs').json()['runs'][0]['id'] == run_id
        assert http.get('/api/tracking/runs/' + run_id).json()['summary']['comments'] == 1
        assert len(provider.calls) == count
        invalid = http.post('/api/tracking/runs', json={'startDate':'2026-11-13','endDate':'2026-11-12'})
        assert invalid.status_code == 400
        engine.client.current_user_for_authorization = lambda: {'id':'someone-else'}
        assert http.get('/api/tracking/runs/' + run_id).status_code == 403
        assert http.get('/api/tracking/runs').json()['runs'] == []


def test_summary_endpoint_persists_citations_and_usage_without_jira_calls(tmp_path, monkeypatch):
    from app.tracking import summary
    provider = Provider()
    engine = manager(tmp_path, provider)
    run = engine.create({'startDate':'2026-11-10','endDate':'2026-11-12'}, background=False)
    event_id = run['events'][0]['id']
    monkeypatch.setattr(summary, '_ready', lambda: (True,''))
    monkeypatch.setattr(summary, '_invoke', lambda _: ({'items':[{'text':'P-1 활동 확인','evidenceIds':[event_id]}]}, {'totalTokens':23}))
    app = FastAPI()
    app.include_router(build_tracking_router(get_client=lambda: engine.client, manager=engine))
    with TestClient(app) as http:
        count = len(provider.calls)
        response = http.post('/api/tracking/runs/' + run['id'] + '/summary')
        assert response.status_code == 200
        assert response.json()['items'][0]['evidenceIds'] == [event_id]
        restored = http.get('/api/tracking/runs/' + run['id']).json()
        assert restored['aiSummary']['usage']['totalTokens'] == 23
        assert restored['metrics']['llmTokens'] == 23
        assert len(provider.calls) == count
