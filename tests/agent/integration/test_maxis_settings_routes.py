from __future__ import annotations

import hashlib
import json
import os

import pytest

os.environ.setdefault("JIRA_ENV", "mock")
pytest.importorskip("langgraph", reason="requirements-agent.txt 미설치")

from fastapi.testclient import TestClient  # noqa: E402

from app.agent.maxis import credentials, profiles  # noqa: E402


DATASET_A = "11111111-1111-4111-8111-111111111111"
SENTINEL_VALUE = "LTM_RELEASE_SENTINEL_V1=integration-test-release"
SENTINEL_SHA = hashlib.sha256(SENTINEL_VALUE.encode("utf-8")).hexdigest()


class _TestCipher:
    def protect(self, value: bytes) -> bytes:
        return b"test-cipher:" + value[::-1]

    def unprotect(self, value: bytes) -> bytes:
        if not value.startswith(b"test-cipher:"):
            raise ValueError("bad test payload")
        return value[len(b"test-cipher:"):][::-1]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    for env in ("LAKE_MAXIS_PROFILE", *profiles.CONFIG_ENV_NAMES.values(),
                *profiles.WORKFLOW_VERSION_ENV_NAMES.values(),
                *credentials.ENV_NAMES.values()):
        monkeypatch.delenv(env, raising=False)
    import app.infra.settings as settings
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    from app.agent.tools import pmo_tools
    monkeypatch.setattr(pmo_tools, "_is_manager", lambda **_kwargs: True)
    credentials.set_backend_for_tests(_TestCipher())
    from app.main import app
    yield TestClient(app)
    credentials.set_backend_for_tests(None)


def _save_candidate(client, *, all_apps=False):
    optional_version = "1.0.0" if all_apps else ""
    return client.put("/api/agent/maxis/profiles/dev", json={
        "baseUrl": "https://dev.maxis.example/v1",
        "dataPolicy": "synthetic_only",
        "workflowBundleVersion": "ltm-agent-1",
        "knowledgeVersion": "ltm-knowledge-2026.09",
        "knowledgeDatasetId": DATASET_A,
        "knowledgeSentinelSha256": SENTINEL_SHA,
        "workflows": {
            "agent_core": {"binding": "agent_core", "version": "1.0.0"},
            "editor_author": {
                "binding": "editor_author", "version": optional_version,
            },
            "pmo_brief": {"binding": "pmo_brief", "version": optional_version},
        },
    })


def _fake_dify_transport(
    monkeypatch, answer_factory, *, retrieval_dataset_id=DATASET_A,
):
    """Run the real Dify client against deterministic in-process response bytes."""
    from app.agent.maxis.client import DifyClient, REQUIRED_START_VARIABLES

    calls = []

    def request_bytes(
        self, method, path, *, api_key, payload, response_limit, breaker=None,
    ):
        calls.append({"method": method, "path": path, "apiKey": api_key,
                      "payload": payload})
        if path == "/parameters":
            body = {"user_input_form": [{
                ("paragraph" if key == "context_pack" else "text-input"): {
                    "variable": key,
                    "required": True,
                    "max_length": 20_000 if key == "context_pack" else 128,
                }
            } for key in sorted(REQUIRED_START_VARIABLES)]}
            return json.dumps(body).encode("utf-8"), 200, 2
        if path == "/chat-messages":
            answer = answer_factory(payload)
            body = {
                "event": "message",
                "message_id": "probe-message",
                "conversation_id": "probe-conversation",
                "answer": answer,
                "metadata": {
                    "usage": {},
                    "retriever_resources": [{
                        "dataset_id": retrieval_dataset_id,
                        "document_id": "private-probe-document",
                        "document_name": "private-probe-document.md",
                        "segment_id": "private-probe-segment",
                        "score": 0.91,
                        "content": "private Knowledge chunk",
                    }],
                },
            }
            return json.dumps(body).encode("utf-8"), 200, 3
        raise AssertionError(f"unexpected fake Dify path: {path}")

    monkeypatch.setattr(DifyClient, "_request_bytes", request_bytes)
    return calls


def _valid_probe_answer(payload):
    inputs = payload["inputs"]
    pack = json.loads(inputs["context_pack"])
    assert pack["contract"] == "evidence-pack.v1"
    assert pack["role_context"][0]["name"] == "activation_probe"
    assert len(pack["knowledge_manifest_sha256"]) == 64
    source = pack["sources"][0]
    return json.dumps({
        "contract": "remote-role-result.v1",
        "role_id": inputs["role_id"],
        "status": "ok",
        "payload": {"state": "ready"},
        "claims": [{
            "claim_id": "probe_claim",
            "kind": "fact",
            "text": "ready",
            "citation_ids": ["probe_citation"],
        }],
        "payload_claims": [{
            "path": "/state", "claim_ids": ["probe_claim"],
        }],
        "citations": [{
            "citation_id": "probe_citation",
            "source_id": source["source_id"],
            "locator": "synthetic source",
        }],
        "missing_inputs": [],
        "warnings": [],
        "workflow_version": inputs["workflow_version"],
        "knowledge_version": inputs["knowledge_version"],
        "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
        "knowledge_proof": SENTINEL_VALUE,
    })


def _verified_probe_result(*workflow_keys):
    manifest = profiles.resolve_knowledge_manifest("dev", required=True)
    assert manifest is not None
    return {
        "ok": True,
        "workflows": {
            key: {
                "ok": True,
                "statusCode": 200,
                "ms": 7,
                "errorCode": "",
                "executionContractVerified": True,
                "versionBindingVerified": True,
                "citationContractVerified": True,
                "knowledgeRetrievalVerified": True,
                "knowledgeManifestSha256": manifest.sha256,
            }
            for key in workflow_keys
        },
        "error": "",
    }


def test_maxis_status_is_separate_from_llm_provider_status(client):
    body = client.get("/api/agent/maxis/status").json()
    assert [p["name"] for p in body["profiles"]] == ["dev", "prod"]
    assert body["routes"] == {"agent_core": "local", "editor_author": "local", "pmo_brief": "local"}
    assert "provider" not in body and "chatModel" not in body
    assert body["canManage"] is True


def test_maxis_runtime_mutations_are_manager_only(client, monkeypatch):
    from app.agent.tools import pmo_tools

    monkeypatch.setattr(pmo_tools, "_is_manager", lambda **_kwargs: False)
    assert client.get("/api/agent/maxis/status").json()["canManage"] is False
    attempts = (
        client.put("/api/agent/maxis/profiles/dev", json={"baseUrl": "https://x.test/v1"}),
        client.put("/api/agent/maxis/credentials/dev", json={"agentCoreApiKey": "not-saved"}),
        client.post("/api/agent/maxis/probe/dev", json={}),
        client.post("/api/agent/maxis/activate/dev", json={}),
        client.put("/api/agent/maxis/routes", json={"routes": {"agent_core": "shadow"}}),
        client.put("/api/agent/maxis/kill-switch", json={"enabled": True}),
    )
    assert all(response.status_code == 403 for response in attempts)
    status = client.get("/api/agent/maxis/status").json()
    assert status["killSwitch"] is False
    assert status["profiles"][0]["baseUrl"] == ""


def test_maxis_manager_gate_always_requests_fresh_jira_identity(client, monkeypatch):
    from app.agent.tools import pmo_tools

    calls = []
    monkeypatch.setattr(
        pmo_tools, "_is_manager",
        lambda *, fresh=False: calls.append(fresh) or True,
    )
    assert client.get("/api/agent/maxis/status").status_code == 200
    assert client.put(
        "/api/agent/maxis/kill-switch", json={"enabled": True},
    ).status_code == 200
    assert calls == [True, True]


def test_profile_route_validates_and_exposes_knowledge_manifest_binding(client):
    rejected = client.put("/api/agent/maxis/profiles/dev", json={
        "knowledgeDatasetId": "not-a-dataset-uuid",
    })
    assert rejected.status_code == 400
    assert "dataset UUID" in rejected.json()["error"]

    assert _save_candidate(client).status_code == 200
    row = client.get("/api/agent/maxis/status").json()["profiles"][0]
    assert row["knowledgeDatasetId"] == DATASET_A
    assert row["effectiveKnowledgeDatasetId"] == DATASET_A
    assert row["knowledgeSentinelSha256"] == SENTINEL_SHA
    assert row["effectiveKnowledgeSentinelSha256"] == SENTINEL_SHA
    assert len(row["knowledgeManifestSha256"]) == 64
    assert row["knowledgeDocumentCount"] == 8


def test_probe_adapter_uses_foundation_client_contract_without_network(client, monkeypatch):
    _save_candidate(client)
    from app.agent.maxis.client import (
        DifyClient,
        DifyContractProbeResult,
        DifyProbeResult,
        REQUIRED_START_VARIABLES,
    )
    import app.agent.routes as routes

    assert routes._MAXIS_REQUIRED_START_VARIABLES == REQUIRED_START_VARIABLES
    seen = {}
    def fake_probe(self, *, api_key):
        seen["profile"] = self.config.profile
        seen["base"] = self.config.base_url
        seen["key"] = api_key
        return DifyProbeResult(ok=True, status_code=200, latency_ms=9,
                               parameter_keys=tuple(sorted(REQUIRED_START_VARIABLES)),
                               error_code="")
    monkeypatch.setattr(DifyClient, "probe_parameters", fake_probe)
    monkeypatch.setattr(
        DifyClient,
        "probe_contract",
        lambda self, binding, **kwargs: (
            seen.update(dataset=kwargs["expected_knowledge_dataset_id"])
            or DifyContractProbeResult(
                ok=True,
                status_code=200,
                latency_ms=5,
                role_id="knowledge_curator",
                contract_verified=True,
                version_verified=True,
                citation_verified=True,
                knowledge_retrieval_verified=True,
                retrieval_resource_count=1,
            )
        ),
    )
    profile = __import__("app.agent.maxis.profiles", fromlist=["resolve"]).resolve("dev")
    manifest_sha = profile["knowledge_manifest_sha256"]
    result = routes._run_maxis_probe(profile, {
        "agent_core": "fake-key", "editor_author": "", "pmo_brief": "",
    })
    assert result["ok"] is True
    assert result["workflows"]["agent_core"] == {
        "ok": True, "statusCode": 200, "ms": 14, "errorCode": "",
        "missingParameters": [],
        "executionContractVerified": True,
        "versionBindingVerified": True,
        "citationContractVerified": True,
        "knowledgeRetrievalVerified": True,
        "knowledgeManifestSha256": manifest_sha,
        "roleId": "knowledge_curator",
    }
    assert seen == {"profile": "dev", "base": "https://dev.maxis.example/v1",
                    "key": "fake-key", "dataset": DATASET_A}

    failed = routes._maxis_probe_result(DifyProbeResult(
        ok=False,
        status_code=200,
        latency_ms=3,
        parameter_keys=("context_pack",),
        error_code="missing_start_variables",
    ))
    assert failed["ok"] is False
    assert failed["errorCode"] == "missing_start_variables"
    assert "role_id" in failed["missingParameters"]


def test_http_200_probe_with_missing_start_variables_cannot_activate(client, monkeypatch):
    _save_candidate(client)
    client.put("/api/agent/maxis/credentials/dev", json={
        "agentCoreApiKey": "fake-key",
    })
    from app.agent.maxis.client import DifyClient, DifyProbeResult

    monkeypatch.setattr(DifyClient, "probe_parameters", lambda self, *, api_key:
                        DifyProbeResult(
                            ok=True,
                            status_code=200,
                            latency_ms=4,
                            parameter_keys=("context_pack", "role_id"),
                        ))
    probed = client.post("/api/agent/maxis/probe/dev", json={}).json()
    assert probed["ok"] is False
    core = probed["workflows"]["agent_core"]
    assert core["statusCode"] == 200
    assert core["errorCode"] == "missing_start_variables"
    assert core["missingParameters"] == [
        "correlation_id", "knowledge_version", "output_contract",
        "workflow_version",
    ]
    status = client.get("/api/agent/maxis/status").json()
    assert status["profiles"][0]["verified"] is False
    assert client.post("/api/agent/maxis/activate/dev", json={}).status_code == 409


def test_parameters_success_with_invalid_chat_contract_cannot_activate(client, monkeypatch):
    _save_candidate(client)
    client.put("/api/agent/maxis/credentials/dev", json={
        "agentCoreApiKey": "fake-contract-key",
    })
    calls = _fake_dify_transport(monkeypatch, lambda payload: "{}")

    probed = client.post("/api/agent/maxis/probe/dev", json={}).json()

    assert [call["path"] for call in calls] == ["/parameters", "/chat-messages"]
    assert probed["ok"] is False
    core = probed["workflows"]["agent_core"]
    assert core["statusCode"] == 200
    assert core["errorCode"] == "remote_contract_validation_failed"
    assert core["executionContractVerified"] is False
    assert core["versionBindingVerified"] is False
    assert core["citationContractVerified"] is False
    status = client.get("/api/agent/maxis/status").json()
    assert status["profiles"][0]["verified"] is False
    assert client.post("/api/agent/maxis/activate/dev", json={}).status_code == 409


def test_probe_rejects_wrong_knowledge_manifest_echo(client, monkeypatch):
    _save_candidate(client)
    client.put("/api/agent/maxis/credentials/dev", json={
        "agentCoreApiKey": "fake-contract-key",
    })

    def wrong_manifest(payload):
        answer = json.loads(_valid_probe_answer(payload))
        answer["knowledge_manifest_sha256"] = "f" * 64
        return json.dumps(answer)

    calls = _fake_dify_transport(monkeypatch, wrong_manifest)
    probed = client.post("/api/agent/maxis/probe/dev", json={}).json()

    assert [call["path"] for call in calls] == ["/parameters", "/chat-messages"]
    assert probed["ok"] is False
    assert probed["workflows"]["agent_core"]["errorCode"] == (
        "remote_knowledge_manifest_mismatch"
    )
    assert client.post("/api/agent/maxis/activate/dev", json={}).status_code == 409


@pytest.mark.parametrize("proof", ["", "LTM_RELEASE_SENTINEL_V1=wrong-release"])
def test_probe_rejects_missing_or_wrong_knowledge_retrieval_proof(
    client, monkeypatch, proof,
):
    _save_candidate(client)
    client.put("/api/agent/maxis/credentials/dev", json={
        "agentCoreApiKey": "fake-contract-key",
    })

    def wrong_proof(payload):
        answer = json.loads(_valid_probe_answer(payload))
        answer["knowledge_proof"] = proof
        return json.dumps(answer)

    calls = _fake_dify_transport(monkeypatch, wrong_proof)
    probed = client.post("/api/agent/maxis/probe/dev", json={}).json()

    assert [call["path"] for call in calls] == ["/parameters", "/chat-messages"]
    core = probed["workflows"]["agent_core"]
    assert core["ok"] is False
    assert core["knowledgeRetrievalVerified"] is False
    assert core["errorCode"] == "knowledge_retrieval_failed"
    assert client.post("/api/agent/maxis/activate/dev", json={}).status_code == 409


def test_probe_rejects_retrieval_from_an_unpinned_dataset(client, monkeypatch):
    _save_candidate(client)
    client.put("/api/agent/maxis/credentials/dev", json={
        "agentCoreApiKey": "fake-contract-key",
    })
    calls = _fake_dify_transport(
        monkeypatch,
        _valid_probe_answer,
        retrieval_dataset_id="22222222-2222-4222-8222-222222222222",
    )

    probed = client.post("/api/agent/maxis/probe/dev", json={}).json()

    assert [call["path"] for call in calls] == ["/parameters", "/chat-messages"]
    core = probed["workflows"]["agent_core"]
    assert core["ok"] is False
    assert core["knowledgeRetrievalVerified"] is False
    assert core["errorCode"] == "policy"
    assert client.post("/api/agent/maxis/activate/dev", json={}).status_code == 409


def test_three_apps_pass_synthetic_chat_contract_and_can_activate(client, monkeypatch):
    assert _save_candidate(client, all_apps=True).status_code == 200
    saved = client.put("/api/agent/maxis/credentials/dev", json={
        "agentCoreApiKey": "fake-core-key",
        "editorAuthorApiKey": "fake-editor-key",
        "pmoBriefApiKey": "fake-pmo-key",
    })
    assert saved.status_code == 200
    calls = _fake_dify_transport(monkeypatch, _valid_probe_answer)

    probed = client.post("/api/agent/maxis/probe/dev", json={}).json()

    assert probed["ok"] is True
    assert set(probed["workflows"]) == {"agent_core", "editor_author", "pmo_brief"}
    for row in probed["workflows"].values():
        assert row["ok"] is True
        assert row["executionContractVerified"] is True
        assert row["versionBindingVerified"] is True
        assert row["citationContractVerified"] is True
        assert row["knowledgeRetrievalVerified"] is True
    chat_calls = [call for call in calls if call["path"] == "/chat-messages"]
    assert len(chat_calls) == 3
    assert {call["apiKey"] for call in chat_calls} == {
        "fake-core-key", "fake-editor-key", "fake-pmo-key",
    }
    assert all(call["payload"]["workflow_id"] == "1.0.0" for call in chat_calls)
    assert client.get("/api/agent/maxis/status").json()["profiles"][0]["verified"] is True
    activated = client.post("/api/agent/maxis/activate/dev", json={})
    assert activated.status_code == 200
    assert activated.json()["activeProfile"] == "dev"


def test_candidate_must_be_saved_probed_and_explicitly_activated(client, monkeypatch):
    assert _save_candidate(client).status_code == 200
    secret = "dify-app-secret-1234"
    saved = client.put("/api/agent/maxis/credentials/dev", json={"agentCoreApiKey": secret})
    assert saved.status_code == 200
    assert secret not in saved.text
    assert saved.json()["credentials"]["agent_core"]["source"] == "dpapi"

    not_yet = client.post("/api/agent/maxis/activate/dev", json={})
    assert not_yet.status_code == 409

    import app.agent.routes as routes
    monkeypatch.setattr(
        routes, "_run_maxis_probe",
        lambda profile, keys: _verified_probe_result("agent_core"),
    )
    probed = client.post("/api/agent/maxis/probe/dev", json={})
    assert probed.status_code == 200 and probed.json()["ok"] is True
    activated = client.post("/api/agent/maxis/activate/dev", json={})
    assert activated.status_code == 200 and activated.json()["activeProfile"] == "dev"
    assert activated.json()["profiles"][0]["enabled"] is True

    authoritative = client.put(
        "/api/agent/maxis/routes", json={"routes": {"agent_core": "maxis"}},
    )
    assert authoritative.status_code == 409
    routed = client.put(
        "/api/agent/maxis/routes", json={"routes": {"agent_core": "shadow"}},
    )
    assert routed.status_code == 200 and routed.json()["routes"]["agent_core"] == "shadow"
    killed = client.put("/api/agent/maxis/kill-switch", json={"enabled": True}).json()
    assert killed["killSwitch"] is True and killed["profiles"][0]["enabled"] is False
    assert killed["routes"]["agent_core"] == "shadow"


def test_profile_edit_and_credential_rotation_invalidate_probe(client, monkeypatch):
    _save_candidate(client)
    client.put("/api/agent/maxis/credentials/dev", json={"agentCoreApiKey": "old-secret"})
    import app.agent.routes as routes
    monkeypatch.setattr(
        routes, "_run_maxis_probe",
        lambda profile, keys: _verified_probe_result("agent_core"),
    )
    client.post("/api/agent/maxis/probe/dev", json={})
    client.post("/api/agent/maxis/activate/dev", json={})
    assert client.get("/api/agent/maxis/status").json()["profiles"][0]["enabled"] is True

    changed = client.put("/api/agent/maxis/profiles/dev", json={"knowledgeDatasetId": "33333333-3333-4333-8333-333333333333"}).json()
    assert changed["profiles"][0]["verified"] is False
    assert changed["profiles"][0]["enabled"] is False

    client.post("/api/agent/maxis/probe/dev", json={})
    client.post("/api/agent/maxis/activate/dev", json={})
    rotated = client.put("/api/agent/maxis/credentials/dev", json={"agentCoreApiKey": "new-secret"})
    assert "new-secret" not in rotated.text
    assert client.get("/api/agent/maxis/status").json()["profiles"][0]["verified"] is False


def test_maxis_impact_is_manager_only_and_aggregate(client, monkeypatch):
    from app.agent.tools import pmo_tools
    monkeypatch.setattr(pmo_tools, "_is_manager", lambda: False)
    assert client.get("/api/agent/maxis/impact?days=30").status_code == 403

    def unavailable_manager_check():
        raise RuntimeError("identity backend unavailable")

    monkeypatch.setattr(pmo_tools, "_is_manager", unavailable_manager_check)
    assert client.get("/api/agent/maxis/impact?days=30").status_code == 403

    monkeypatch.setattr(pmo_tools, "_is_manager", lambda: True)
    body = client.get("/api/agent/maxis/impact?days=30").json()
    assert body["contract"] == "agent-impact.v1"
    assert body["privacy"] == "aggregate_metadata_only"
    assert "rows" not in body and "users" not in body
