from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("JIRA_ENV", "mock")
pytest.importorskip("langgraph", reason="requirements-agent.txt 미설치")

from fastapi.testclient import TestClient  # noqa: E402

from app.agent.maxis import profiles  # noqa: E402
from app.infra import prefs  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app.infra.settings as settings
    from app.agent.tools import pmo_tools
    from app.main import app

    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    for name in ("LAKE_MAXIS_PROFILE", *profiles.CONFIG_ENV_NAMES.values()):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(pmo_tools, "_is_manager", lambda **_kwargs: True)
    yield TestClient(app)


def test_maxis_control_plane_routes_return_503_when_strict_write_fails(client, monkeypatch):
    def unavailable(_patch):
        raise prefs.PrefsPersistenceError("Runtime preferences could not be persisted safely.")

    monkeypatch.setattr(prefs, "save_strict", unavailable)
    responses = (
        client.put(
            "/api/agent/maxis/profiles/dev",
            json={"baseUrl": "https://dev.maxis.example/v1"},
        ),
        client.put(
            "/api/agent/maxis/routes",
            json={"routes": {"agent_core": "local"}},
        ),
        client.put(
            "/api/agent/maxis/kill-switch",
            json={"enabled": True},
        ),
    )

    assert [response.status_code for response in responses] == [503, 503, 503]
    assert all(response.json()["ok"] is False for response in responses)


def test_failed_probe_returns_503_when_failure_evidence_cannot_be_persisted(
        client, monkeypatch):
    from app.agent.maxis import profiles as maxis_profiles

    # An invalid profile enters the route's failure-recording branch before any
    # outbound request.  Persistence failure must be visible to the operator; it
    # cannot be swallowed while older success evidence remains on disk.
    monkeypatch.setattr(
        maxis_profiles, "mark_probe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            prefs.PrefsPersistenceError(
                "Runtime preferences could not be persisted safely."
            )
        ),
    )

    response = client.post("/api/agent/maxis/probe/prod")

    assert response.status_code == 503
    assert response.json() == {
        "ok": False,
        "workflows": {},
        "error": "Runtime preferences could not be persisted safely.",
    }


def test_prod_remote_log_attestation_round_trips_through_profile_route(client):
    response = client.put("/api/agent/maxis/profiles/prod", json={
        "remoteLogPolicyId": "SEC-2026-091",
        "remoteLogMode": "approved_bounded_content",
        "remoteLogRetentionDays": 14,
    })

    assert response.status_code == 200
    row = response.json()["profiles"][1]
    assert row["remoteLogPolicyId"] == "SEC-2026-091"
    assert row["remoteLogMode"] == "approved_bounded_content"
    assert row["remoteLogRetentionDays"] == 14


def test_knowledge_eval_attestation_round_trips_and_omitted_ui_field_is_preserved(client):
    configured = client.put("/api/agent/maxis/profiles/prod", json={
        "baseUrl": "https://prod.maxis.example/v1",
        "workflowBundleVersion": "bundle-1",
        "knowledgeVersion": "ltm-knowledge-2026.09",
        "knowledgeDatasetId": "11111111-1111-4111-8111-111111111111",
        "knowledgeSentinelSha256": "a" * 64,
        "remoteLogPolicyId": "SEC-2026-091",
        "remoteLogMode": "redacted",
        "remoteLogRetentionDays": 14,
        "workflows": {
            "agent_core": {"binding": "agent_core", "version": "core-1"},
            "editor_author": {"binding": "editor_author", "version": "editor-1"},
            "pmo_brief": {"binding": "pmo_brief", "version": "pmo-1"},
        },
    })
    assert configured.status_code == 200
    attestation = {
        "contract": profiles.KNOWLEDGE_EVAL_ATTESTATION_CONTRACT,
        "evidence_id": "EVAL-2026-09-05",
        "outcome": "passed",
        "case_count": 25,
        "artifact_sha256": "b" * 64,
        "passed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **profiles.knowledge_eval_attestation_binding("prod"),
    }

    response = client.put("/api/agent/maxis/profiles/prod", json={
        "knowledgeEvalAttestation": attestation,
    })
    assert response.status_code == 200
    public = response.json()["profiles"][1]["knowledgeEvalAttestation"]
    assert public["valid"] is True
    assert public["attestation"]["outcome"] == "passed"
    assert public["attestation"]["caseCount"] == 25
    assert public["verification"] == "metadata_binding_only_external_artifact_not_read"

    # Older clients/UI do not know this field; an unrelated partial patch must not
    # silently clear the already-attested release metadata.
    preserved = client.put("/api/agent/maxis/profiles/prod", json={
        "remoteLogRetentionDays": 7,
    })
    assert preserved.status_code == 200
    assert preserved.json()["profiles"][1]["knowledgeEvalAttestation"]["configured"] is True
