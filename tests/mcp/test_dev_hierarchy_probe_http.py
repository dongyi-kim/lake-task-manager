"""DevTool HTTP authorization and SDK-backed start/poll integration."""

import time

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.routes.dev_hierarchy_probe import build_hierarchy_probe_router
from tests.mcp.test_dev_hierarchy_probe import creates, setup_probe


def test_http_start_poll_scope_and_local_manager_guards(build_env, monkeypatch, tmp_path):
    env, wizard, _ = setup_probe(build_env, monkeypatch, tmp_path)
    manager = [True]
    def guard():
        if not manager[0]:
            raise HTTPException(403, "manager only")
    router = build_hierarchy_probe_router(runtime=env.runtime, require_manager=guard, state_dir=tmp_path / "routes")
    app = FastAPI()
    app.include_router(router)
    with TestClient(app, base_url="http://127.0.0.1:4457", client=("127.0.0.1", 1234)) as http:
        options = http.get("/api/dev/hierarchy-test/options")
        assert options.status_code == 200
        body = {"runId": "probe:http:0001", "taskType": "Task", "subtaskType": "Sub-task", "expectedScope": options.json()["scope"]}
        assert http.post("/api/dev/hierarchy-test", json=body, headers={"Origin": "https://evil.test"}).status_code == 403
        assert http.post("/api/dev/hierarchy-test", json=body, headers={"Host": "evil.test"}).status_code == 403
        manager[0] = False
        assert http.post("/api/dev/hierarchy-test", json=body).status_code == 403
        assert http.get("/api/dev/hierarchy-test/options").status_code == 403
        manager[0] = True
        assert http.post("/api/dev/hierarchy-test", json=body).status_code == 202
        deadline = time.monotonic() + 15
        while router.probe._active and time.monotonic() < deadline:
            time.sleep(.01)
        assert router.probe._active is None
        assert http.get("/api/dev/hierarchy-test/" + body["runId"]).json()["status"] == "passed"
        assert http.post("/api/dev/hierarchy-test", json={**body, "taskType": "Bug"}).status_code == 409
        env.client.s.project_key = "OTHER"
        assert http.get("/api/dev/hierarchy-test/" + body["runId"]).status_code == 409
    assert len(creates(env)) == 3 and wizard.commits == 3
