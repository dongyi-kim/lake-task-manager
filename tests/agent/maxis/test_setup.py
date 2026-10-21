"""PROD cold-start preparation uses repository sources and local metadata only."""
from __future__ import annotations

import io
import json
import uuid
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.maxis import setup
from app.agent.maxis import setup_routes


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    from app.infra import settings
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    monkeypatch.delenv("LAKE_MAXIS_PROD_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(setup_routes, "_can_manage", lambda: True)
    app = FastAPI()
    app.include_router(setup_routes.router)
    return TestClient(app)


def test_public_guide_is_repository_source_without_setup_or_credentials(isolated, monkeypatch):
    from app.agent.maxis import credentials, profiles
    monkeypatch.setattr(credentials, "get_for", lambda *_: pytest.fail("secret read"))
    monkeypatch.setattr(profiles, "get", lambda *_: pytest.fail("profile dependency"))
    monkeypatch.setattr(setup_routes, "_can_manage", lambda: False)
    response = isolated.get("/api/agent/maxis/setup/guide")
    assert response.status_code == 200
    assert response.text == (setup._root() / "docs" / "agent-maxis-operations.md").read_text(encoding="utf-8")
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" not in response.headers


def test_missing_public_guide_returns_safe_not_found(isolated, monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "_root", lambda: tmp_path)
    response = isolated.get("/api/agent/maxis/setup/guide")
    assert response.status_code == 404
    assert str(tmp_path) not in response.text


def test_repo_source_download_is_complete_without_dev_or_credentials(isolated, monkeypatch):
    from app.agent.maxis import credentials, profiles
    monkeypatch.setattr(credentials, "get_for", lambda *_: pytest.fail("secret read"))
    monkeypatch.setattr(profiles, "get", lambda *_: pytest.fail("profile dependency"))
    result = isolated.post("/api/agent/maxis/setup/knowledge", json={}).json()
    assert result["ok"] and result["runId"]
    archive = isolated.get(result["downloadUrl"])
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        assert len([name for name in zipped.namelist() if name.startswith("knowledge/")]) >= 8
        assert "sentinel/ltm-release-sentinel-prod.txt" in zipped.namelist()
        metadata = json.loads(zipped.read("deployment-source.json"))
        assert metadata["environment"] == "prod"
        assert "LTM_RELEASE_SENTINEL_V1=" not in json.dumps(metadata)
        assert "dev_environment_binding" not in json.dumps(metadata)
    assert "LTM_RELEASE_SENTINEL_V1=" not in json.dumps(result)


def test_same_source_preparation_reuses_run_and_unique_document_descriptions(isolated):
    first = isolated.post("/api/agent/maxis/setup/knowledge", json={}).json()
    second = isolated.post("/api/agent/maxis/setup/knowledge", json={}).json()
    assert first["runId"] == second["runId"]
    archive = isolated.get(first["downloadUrl"])
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        import csv
        rows = list(csv.DictReader(io.StringIO(zipped.read("document-metadata.csv").decode("utf-8-sig"))))
        assert len(rows) == len({row["description"] for row in rows}) == 10
        assert all(len(row["content_sha256"]) == 64 for row in rows)


@pytest.mark.parametrize("url", [
    "http://prod.example/v1", "https://user:password@prod.example/v1",
    "https://prod.example:8443/v1", "https://127.0.0.1/v1",
    "https://prod.example/v1?token=secret", "https://prod.example/v1/chat-messages",
])
def test_host_registration_rejects_unsafe_destinations_without_persisting(isolated, url):
    response = isolated.put("/api/agent/maxis/setup/host", json={"baseUrl": url, "confirmed": True})
    assert response.status_code == 400
    assert setup.registered_prod_hosts() == frozenset()
    assert "password" not in response.text and "token=secret" not in response.text


def test_host_registration_requires_explicit_confirmation_and_respects_env(isolated, monkeypatch):
    path = "/api/agent/maxis/setup/host"
    assert isolated.put(path, json={"baseUrl": "https://prod.example/v1", "confirmed": False}).status_code == 400
    assert isolated.put(path, json={"baseUrl": "https://prod.example", "confirmed": True}).status_code == 200
    assert setup.registered_prod_hosts() == frozenset({"prod.example"})
    monkeypatch.setenv("LAKE_MAXIS_PROD_ALLOWED_HOSTS", "locked.example")
    assert isolated.put(path, json={"baseUrl": "https://other.example", "confirmed": True}).status_code == 400
    assert setup.registered_prod_hosts() == frozenset({"prod.example"})


def test_every_mutation_and_download_requires_fresh_manager_identity(isolated, monkeypatch):
    prepared = isolated.post("/api/agent/maxis/setup/knowledge", json={}).json()
    monkeypatch.setattr(setup_routes, "_can_manage", lambda: False)
    assert isolated.get(prepared["downloadUrl"]).status_code == 403
    assert isolated.put("/api/agent/maxis/setup/host", json={"baseUrl": "https://prod.example", "confirmed": True}).status_code == 403
    assert isolated.post("/api/agent/maxis/setup/knowledge", json={}).status_code == 403


def test_candidate_requires_generated_bundle_and_exact_published_ids(isolated):
    prepared = isolated.post("/api/agent/maxis/setup/knowledge", json={}).json()
    response = isolated.post("/api/agent/maxis/setup/candidate", json={
        "runId": prepared["runId"], "workflowVersions": {key: str(uuid.uuid4()) for key in setup.WORKFLOWS},
        "remoteLogPolicyId": "approved-policy", "remoteLogMode": "redacted", "remoteLogRetentionDays": 0,
    })
    assert response.status_code == 400


def test_download_never_accepts_path_traversal_or_unknown_kind(isolated):
    assert isolated.get("/api/agent/maxis/setup/download/invalid/knowledge").status_code == 400
    prepared = isolated.post("/api/agent/maxis/setup/knowledge", json={}).json()
    assert isolated.get(f"/api/agent/maxis/setup/download/{prepared['runId']}/credentials").status_code == 400


def test_unsupported_credentials_are_never_echoed_in_validation_error(isolated):
    response = isolated.put("/api/agent/maxis/setup/host", json={
        "baseUrl": "https://prod.example", "confirmed": True, "password": "private-do-not-echo",
    })
    assert response.status_code == 422
    assert "private-do-not-echo" not in response.text


def test_preparation_is_bound_to_current_repository_source(isolated, monkeypatch):
    prepared = isolated.post("/api/agent/maxis/setup/knowledge", json={}).json()
    inventory = setup.source_inventory()
    monkeypatch.setattr(setup, "source_inventory", lambda: {**inventory, "sourceDigest": "0" * 64})
    with pytest.raises(ValueError, match="소스가 변경"):
        setup._run(prepared["runId"])


def test_model_import_only_returns_identifiers_and_never_persists_source(isolated):
    exported = """
dependencies:
- value:
    plugin_unique_identifier: example/model:1.0@aaaaaaaa
workflow:
  environment_variables:
  - name: hidden
    value: private-do-not-return
  graph:
    nodes:
    - data:
        type: llm
        model:
          provider: example/model/model
          name: approved-chat
"""
    response = isolated.post("/api/agent/maxis/setup/model", json={"yamlText": exported})
    assert response.status_code == 200
    assert response.json()["model"]["name"] == "approved-chat"
    assert response.json()["model"]["completion_params"] == {}
    assert "private-do-not-return" not in response.text
    assert not setup._directory().exists()


@pytest.mark.parametrize("params", [{}, {"reasoning_effort": "low", "max_completion_tokens": 2048}])
def test_model_export_preserves_approved_parameters(isolated, params):
    import yaml
    model = {"provider": "example/model/chat", "name": "project-model", "completion_params": params}
    exported = {"dependencies": [{"value": {"plugin_unique_identifier": "example/model:1.0@aaaaaaaa"}}],
                "workflow": {"graph": {"nodes": [{"data": {"type": "llm", "model": model}}]}}}
    response = isolated.post("/api/agent/maxis/setup/model", json={"yamlText": yaml.safe_dump(exported)})
    assert response.status_code == 200, response.text
    assert response.json()["model"]["completion_params"] == params


def test_model_export_rejects_mixed_parameter_settings(isolated):
    import yaml
    nodes = [{"data": {"type": "llm", "model": {
        "provider": "example/model/chat", "name": "project-model", "completion_params": params}}}
        for params in ({}, {"temperature": 0.1})]
    exported = {"dependencies": [{"value": {"plugin_unique_identifier": "example/model:1.0@aaaaaaaa"}}],
                "workflow": {"graph": {"nodes": nodes}}}
    response = isolated.post("/api/agent/maxis/setup/model", json={"yamlText": yaml.safe_dump(exported)})
    assert response.status_code == 400


@pytest.mark.parametrize("params", [None, {"unknown": "private-do-not-echo"},
                                   {"reasoning_effort": "private-do-not-echo"}])
def test_model_parameters_are_rejected_before_preparation_without_value_echo(isolated, params):
    body = {"runId": "a" * 32, "baseUrl": "https://prod.example/v1",
            "datasetId": str(uuid.uuid4()), "sentinelDatasetId": str(uuid.uuid4()),
            "sentinelDocumentId": str(uuid.uuid4()), "roleMetadataFieldId": str(uuid.uuid4()),
            "documentIds": {}, "model": {"plugin_unique_identifier": "example/model:1.0@aaaaaaaa",
                "provider": "example/model/model", "name": "approved-chat", "completion_params": params}}
    response = isolated.post("/api/agent/maxis/setup/bundle", json=body)
    assert response.status_code == 422
    assert "private-do-not-echo" not in response.text
    assert not setup._directory().exists()

@pytest.mark.parametrize("output_mode", [None, "guarded_json"])
@pytest.mark.parametrize("params", [None, {}, {"reasoning_effort": "low", "max_completion_tokens": 2048}])
def test_clean_prod_prepares_real_dsls_and_saves_only_an_inactive_candidate(isolated, monkeypatch, params, output_mode):
    from app.agent.maxis import credentials, profiles
    from app.infra import prefs
    monkeypatch.setenv("LAKE_MAXIS_PROD_ALLOWED_HOSTS", "prod.example")
    monkeypatch.setattr(credentials, "get_for", lambda *_: pytest.fail("preparation reads a secret"))
    monkeypatch.setattr(profiles, "activate", lambda *_: pytest.fail("preparation activates runtime"))
    prepared = isolated.post("/api/agent/maxis/setup/knowledge", json={}).json()
    body = {"runId": prepared["runId"], "baseUrl": "https://prod.example/v1",
            "datasetId": str(uuid.uuid4()), "sentinelDatasetId": str(uuid.uuid4()),
            "sentinelDocumentId": str(uuid.uuid4()), "roleMetadataFieldId": str(uuid.uuid4()),
            "documentIds": {row["id"]: str(uuid.uuid4()) for row in prepared["documents"]},
            "model": {"plugin_unique_identifier": "example/model:1.0@aaaaaaaa", "provider": "example/model/model", "name": "approved-chat"}}
    if params is not None:
        body["model"]["completion_params"] = params
    if output_mode is not None:
        body["model"]["output_mode"] = output_mode
    bundled = isolated.post("/api/agent/maxis/setup/bundle", json=body)
    assert bundled.status_code == 200, bundled.text
    assert bundled.json()["knowledgeSentinelSha256"] == prepared["knowledgeSentinelSha256"]
    with zipfile.ZipFile(io.BytesIO(isolated.get(bundled.json()["downloadUrl"]).content)) as archive:
        workflows = [name for name in archive.namelist() if name.startswith("workflows/")]
        assert len(workflows) == 3
        import yaml
        environment = yaml.safe_load(archive.read("environment.yml"))
        assert environment["environment"] == "prod"
        assert "dev_environment_binding" not in environment["knowledge"]
        assert ("completion_params" in environment["model"]) == (params is not None)
        assert environment["model"].get("output_mode") == output_mode
        for filename in workflows:
            dsl = archive.read(filename).decode("utf-8")
            assert body["datasetId"] in dsl and "approved-chat" in dsl
            assert "dify.maxis.skax.co.kr" not in dsl
            if params is not None:
                for node in yaml.safe_load(dsl)["workflow"]["graph"]["nodes"]:
                    if node["data"]["type"] == "llm":
                        assert node["data"]["model"]["completion_params"] == (
                            {**params, "response_format": "text"} if output_mode == "guarded_json" else params)
                        assert node["data"]["structured_output_enabled"] is (output_mode != "guarded_json")
    published = {key: str(uuid.uuid4()) for key in ("agent_core", "editor_author")}
    candidate = isolated.post("/api/agent/maxis/setup/candidate", json={
        "runId": prepared["runId"], "workflowVersions": published,
        "remoteLogPolicyId": "prod-approved", "remoteLogMode": "redacted", "remoteLogRetentionDays": 0,
    })
    assert candidate.status_code == 200, candidate.text
    stored = prefs.load()
    assert stored["maxisActiveProfile"] == ""
    assert all(mode == "local" for mode in stored["maxisRoutes"].values())
    profile = next(row for row in stored["maxisProfiles"] if row["name"] == "prod")
    assert profile["knowledge_environment_binding"].startswith("cache:maxis/setup/")
    assert {key: value["version"] for key, value in profile["workflows"].items() if value["version"]} == published
    assert profile["workflows"]["pmo_brief"]["version"] == ""


def test_apply_route_uses_one_atomic_profile_operation(isolated, monkeypatch):
    from app.agent.maxis import profiles
    calls = []
    monkeypatch.setattr(profiles, "apply_profile", lambda profile, workflows: (calls.append((profile, workflows)), {"ok": True})[1])
    response = isolated.post("/api/agent/maxis/setup/apply", json={"workflows": ["agent_core", "editor_author"]})
    assert response.status_code == 200 and response.json()["ok"]
    assert calls == [("prod", ["agent_core", "editor_author"])]
    monkeypatch.setattr(setup_routes, "_can_manage", lambda: False)
    assert isolated.post("/api/agent/maxis/setup/apply", json={"workflows": ["agent_core", "editor_author"]}).status_code == 403
    assert len(calls) == 1


def test_preparation_and_download_are_isolated_by_profile(isolated):
    names = ["dev", "maxis-" + uuid.uuid4().hex]
    prepared = [isolated.post("/api/agent/maxis/setup/knowledge", params={"profile": name}).json()
                for name in names]
    assert prepared[0]["runId"] != prepared[1]["runId"]
    for name, result in zip(names, prepared):
        assert result["profile"] == name
        assert isolated.get(result["downloadUrl"]).status_code == 200
        current = isolated.get("/api/agent/maxis/setup", params={"profile": name}).json()["currentRun"]
        assert current["runId"] == result["runId"]
        wrong = isolated.get(f"/api/agent/maxis/setup/download/{result['runId']}/knowledge",
                             params={"profile": "prod"})
        assert wrong.status_code == 400
    assert isolated.get("/api/agent/maxis/setup").json()["currentRun"] is None
    assert isolated.post("/api/agent/maxis/setup/knowledge", params={"profile": "../escape"}).status_code == 400


def test_bundle_candidate_and_apply_target_the_selected_profile(isolated, monkeypatch):
    from app.agent.maxis import profiles
    name = "maxis-" + uuid.uuid4().hex
    monkeypatch.setattr(profiles, "get", lambda selected: {"name": selected, "stage": "prod"})
    saved, applied = [], []
    monkeypatch.setattr(profiles, "save", lambda selected, body: saved.append((selected, body)))
    monkeypatch.setattr(profiles, "apply_profile", lambda selected, workflows:
                        (applied.append((selected, workflows)), {"ok": True})[1])
    query = {"profile": name}
    host = isolated.put("/api/agent/maxis/setup/host", params=query,
                        json={"baseUrl": "https://other.example/v1", "confirmed": True})
    assert host.status_code == 200, host.text
    assert setup.registered_prod_hosts() == frozenset({"other.example"})
    prepared = isolated.post("/api/agent/maxis/setup/knowledge", params=query).json()
    body = {"runId": prepared["runId"], "baseUrl": "https://other.example/v1",
            "datasetId": str(uuid.uuid4()), "sentinelDatasetId": str(uuid.uuid4()),
            "sentinelDocumentId": str(uuid.uuid4()), "roleMetadataFieldId": str(uuid.uuid4()),
            "documentIds": {row["id"]: str(uuid.uuid4()) for row in prepared["documents"]},
            "model": {"plugin_unique_identifier": "example/model:1.0@aaaaaaaa",
                      "provider": "example/model/model", "name": "approved-chat"}}
    bundled = isolated.post("/api/agent/maxis/setup/bundle", params=query, json=body)
    assert bundled.status_code == 200, bundled.text
    assert isolated.post("/api/agent/maxis/setup/bundle", json=body).status_code == 400
    candidate = isolated.post("/api/agent/maxis/setup/candidate", params=query, json={
        "runId": prepared["runId"], "workflowVersions": {
            key: str(uuid.uuid4()) for key in ("agent_core", "editor_author")},
        "remoteLogPolicyId": "approved-policy", "remoteLogMode": "redacted", "remoteLogRetentionDays": 0,
    })
    assert candidate.status_code == 200, candidate.text
    assert saved[0][0] == name
    assert f"/profiles/{name}/" in saved[0][1]["knowledge_environment_binding"]
    assert isolated.post("/api/agent/maxis/setup/apply", params=query,
                         json={"workflows": ["agent_core", "editor_author"]}).status_code == 200
    assert applied == [(name, ["agent_core", "editor_author"])]
