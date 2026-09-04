"""Production-shaped MAXIS coverage through the real Session and compiled graph.

The tests in ``tests/agent/maxis`` exercise the boundary objects in isolation.  This
module deliberately starts at :func:`session.ask`, lets the normal graph choose and
execute its nodes, and replaces only the Dify HTTP transport (plus deployment-owned
profile/credential storage).  It catches wiring regressions that a miniature StateGraph
cannot see: runtime pinning, Role routing, remote telemetry reducers, deterministic
``needs_input`` rendering, and the final public Session projection.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

from app.agent.maxis import backend, credentials
from app.agent.maxis import client as client_module
from app.agent.maxis.client import REQUIRED_START_VARIABLES
from app.agent.workflow import graph as workflow_graph
from app.agent.workflow import session
from app.agent.workflow.role_manifest import ROLE_SPECS


DATASET_ID = "11111111-1111-4111-8111-111111111111"
MANIFEST_SHA256 = "a" * 64
PROFILE_FINGERPRINT = "profile-prod-graph-v1"


def _profile() -> dict:
    return {
        "name": "prod",
        "stage": "prod",
        "fingerprint": PROFILE_FINGERPRINT,
        "base_url": "https://maxis.example/v1",
        "data_policy": "internal_read_only",
        "workflow_bundle_version": "bundle-graph-1",
        "knowledge_version": "knowledge-graph-1",
        "knowledge_dataset_id": DATASET_ID,
        "knowledge_manifest_sha256": MANIFEST_SHA256,
        "knowledge_document_count": 3,
        "knowledge_sentinel_sha256": "b" * 64,
        "workflows": {
            "agent_core": {"binding": "agent_core", "version": "wf-core-graph-1"},
            "pmo_brief": {"binding": "pmo_brief", "version": "wf-pmo-graph-1"},
            "editor_author": {
                "binding": "editor_author",
                "version": "wf-editor-graph-1",
            },
        },
        "enabled": True,
    }


def _remote_routes() -> dict[str, str]:
    routes = {role_id: "local" for role_id in ROLE_SPECS}
    routes["request_architect"] = "maxis"
    routes["result_integrator"] = "maxis"
    return routes


@pytest.fixture
def production_graph_transport(monkeypatch, request):
    """Install a real DifyClient backed by MockTransport and return its controller."""

    routes = _remote_routes()
    profile = _profile()
    audit_events = []
    observed_requests: list[dict] = []
    open_clients: list[httpx.Client] = []
    controller = {"request_architect_status": "ok"}

    monkeypatch.setattr(
        backend,
        "_canonical_role_routes",
        lambda *, explicit_engine="": dict(routes),
    )
    monkeypatch.setattr(
        backend,
        "_profile_snapshot",
        lambda _name="", *, required=False: dict(profile),
    )
    monkeypatch.setattr(credentials, "get_for", lambda *_args: "test-api-key")

    from app.agent.maxis import audit

    monkeypatch.setattr(audit, "record_audit_event", audit_events.append)

    def answer_for(role_id: str) -> dict:
        status = controller["request_architect_status"] if role_id == "request_architect" else "ok"
        if status == "needs_input":
            payload = {}
            claims = []
            payload_claims = []
            missing_inputs = ["처리할 업무 대상을 알려 주세요."]
        elif role_id == "request_architect":
            # Every non-empty string is a structural control field, so this classification
            # needs no evidence claim.  RequestArchitect's normal local apply() still owns
            # the resulting RequestPlan and graph route.
            payload = {
                "intent": "chitchat",
                "keywords": [],
                "sufficient": True,
                "request_questions": [],
                "requested_effects": [],
                "target_selectors": [],
            }
            claims = []
            payload_claims = []
            missing_inputs = []
        elif role_id == "result_integrator":
            text = "반갑습니다. 요청하실 업무를 말씀해 주세요."
            payload = {"text": text}
            claims = [{
                "claim_id": "reply_claim",
                "kind": "inference",
                "text": text,
                "citation_ids": [],
            }]
            payload_claims = [{
                "path": "/text",
                "claim_ids": ["reply_claim"],
            }]
            missing_inputs = []
        else:  # The graph must not unexpectedly route another Role remotely.
            raise AssertionError(f"unexpected remote Role: {role_id}")
        return {
            "contract": "remote-role-result.v1",
            "role_id": role_id,
            "status": status,
            "payload": payload,
            "claims": claims,
            "payload_claims": payload_claims,
            "citations": [],
            "missing_inputs": missing_inputs,
            "warnings": [],
            "workflow_version": "wf-core-graph-1",
            "knowledge_version": "knowledge-graph-1",
            "knowledge_manifest_sha256": MANIFEST_SHA256,
        }

    def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.method == "POST"
        assert http_request.url == "https://maxis.example/v1/chat-messages"
        assert http_request.headers["Authorization"] == "Bearer test-api-key"
        assert b"test-api-key" not in http_request.content
        body = json.loads(http_request.content)
        assert set(body["inputs"]) == REQUIRED_START_VARIABLES
        assert body["response_mode"] == "blocking"
        assert body["workflow_id"] == "wf-core-graph-1"
        context_pack = json.loads(body["inputs"]["context_pack"])
        assert context_pack["contract"] == "evidence-pack.v1"
        assert context_pack["request_id"] == body["inputs"]["correlation_id"]
        assert context_pack["use_case"] == body["inputs"]["role_id"]
        assert context_pack["payload_schema_sha256"]
        assert "messages" not in context_pack
        assert "approval_token" not in json.dumps(context_pack, ensure_ascii=False)
        observed_requests.append({"body": body, "context_pack": context_pack})
        role_id = body["inputs"]["role_id"]
        return httpx.Response(200, json={
            "event": "message",
            "answer": json.dumps(answer_for(role_id), ensure_ascii=False),
            "message_id": f"message-{len(observed_requests)}",
            "conversation_id": f"conversation-{len(observed_requests)}",
            "metadata": {
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
            },
        })

    real_dify_client = client_module.DifyClient

    def client_factory(config):
        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        open_clients.append(http_client)
        return real_dify_client(config, client=http_client)

    monkeypatch.setattr(client_module, "DifyClient", client_factory)
    workflow_graph.reset()

    def cleanup():
        workflow_graph.reset()
        for http_client in open_clients:
            http_client.close()

    request.addfinalizer(cleanup)
    return controller, observed_requests, audit_events


def test_session_runs_authoritative_maxis_roles_through_real_compiled_graph(
    production_graph_transport,
):
    _controller, requests, audit_events = production_graph_transport

    result = session.ask(
        "안녕, 오늘도 잘 부탁해.",
        thread_id="maxis-production-graph-ok",
        user_role="member",
        user_id="integration.user",
    )

    assert result["ok"] is True
    assert "반갑습니다" in result["reply"]
    assert [row["body"]["inputs"]["role_id"] for row in requests] == [
        "request_architect",
        "result_integrator",
    ]
    assert result["engine"] == "maxis"
    assert result["profile"] == "prod"
    assert result["roleEngines"] == {
        "request_architect": "maxis",
        "result_integrator": "maxis",
    }
    assert result["lastRole"] == "result_integrator"
    assert result["lastRoleEngine"] == "maxis"
    assert result["usage"]["remoteCalls"] == 2
    assert result["usage"]["totalTokens"] == 12
    assert result["qualityGate"]["ok"] is True
    assert result["qualityGate"]["schemaValid"] is True
    assert result["evidenceWorkspace"]["claims"] == [{
        "kind": "inference",
        "text": "반갑습니다. 요청하실 업무를 말씀해 주세요.",
        "sourceIds": [],
    }]
    # Public Session state includes only the allow-listed projection, never wire payloads.
    rendered = json.dumps(result, ensure_ascii=False)
    assert "context_pack" not in rendered
    assert "payload_schema_json" not in rendered
    assert "test-api-key" not in rendered
    assert [event.status for event in audit_events] == [
        "started", "succeeded", "started", "succeeded",
    ]


def test_session_projects_remote_needs_input_without_calling_another_model(
    production_graph_transport,
):
    controller, requests, audit_events = production_graph_transport
    controller["request_architect_status"] = "needs_input"

    result = session.ask(
        "처리해 줘.",
        thread_id="maxis-production-graph-needs-input",
        user_role="member",
        user_id="integration.user",
    )

    # ResultIntegrator's question renderer is deterministic, so no second remote or local
    # semantic call is needed after the validated control outcome crosses the boundary.
    assert [row["body"]["inputs"]["role_id"] for row in requests] == [
        "request_architect",
    ]
    assert result["ok"] is True
    assert len(result["questions"]) == 1
    question = result["questions"][0]
    assert question["contract"] == "question.v1"
    assert question["field"] == "maxis:request_architect:1"
    assert question["required_input"] is True
    assert question["question"] == "처리할 업무 대상을 알려 주세요."
    assert result["roleEngines"] == {"request_architect": "maxis"}
    assert result["lastRole"] == "request_architect"
    assert result["lastRoleEngine"] == "maxis"
    assert result["usage"]["remoteCalls"] == 1
    assert result["qualityGate"]["ok"] is False
    assert result["qualityGate"]["schemaValid"] is True
    assert result["evidenceWorkspace"]["missingInputs"] == [
        "처리할 업무 대상을 알려 주세요.",
    ]
    assert [event.status for event in audit_events] == ["started", "succeeded"]
