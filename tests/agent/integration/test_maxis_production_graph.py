"""Production-shaped MAXIS coverage through the real Session and compiled graph.

The tests in ``tests/agent/maxis`` exercise the boundary objects in isolation.  This
module deliberately starts at :func:`session.ask`, lets the normal graph choose and
execute its nodes, and replaces only the Dify HTTP transport (plus deployment-owned
profile/credential storage).  It catches wiring regressions that a miniature StateGraph
cannot see: runtime pinning, Role routing, remote telemetry reducers, deterministic
``needs_input`` rendering, and the final public Session projection.
"""

from __future__ import annotations

import hashlib
import json
import os

import httpx
import pytest

os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

from app.agent.maxis import backend, credentials, profiles
from app.agent.maxis import client as client_module
from app.agent.maxis.client import REQUIRED_START_VARIABLES
from app.agent.workflow import graph as workflow_graph
from app.agent.workflow import session
from app.agent.workflow.role_manifest import ROLE_SPECS


DATASET_ID = "11111111-1111-4111-8111-111111111111"
AGENT_WORKFLOW_ID = "22222222-2222-4222-8222-222222222222"
PMO_WORKFLOW_ID = "33333333-3333-4333-8333-333333333333"
EDITOR_WORKFLOW_ID = "44444444-4444-4444-8444-444444444444"
MANIFEST_SHA256 = "a" * 64
PROFILE_FINGERPRINT = "profile-prod-graph-v1"
SENTINEL_VALUE = "LTM_RELEASE_SENTINEL_V1=production-graph-release"
SENTINEL_SHA256 = hashlib.sha256(SENTINEL_VALUE.encode("utf-8")).hexdigest()


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
        "knowledge_sentinel_sha256": SENTINEL_SHA256,
        "workflows": {
            "agent_core": {
                "binding": "agent_core", "version": AGENT_WORKFLOW_ID,
            },
            "pmo_brief": {"binding": "pmo_brief", "version": PMO_WORKFLOW_ID},
            "editor_author": {
                "binding": "editor_author",
                "version": EDITOR_WORKFLOW_ID,
            },
        },
        "enabled": True,
    }


def _remote_routes() -> dict[str, str]:
    routes = {role_id: "local" for role_id in ROLE_SPECS}
    routes["request_architect"] = "maxis"
    routes["result_integrator"] = "maxis"
    return routes


def test_production_authority_remains_fail_closed_without_signed_eval_verifier(
    monkeypatch,
):
    """Implemented Role ACL alone must not unlock production authority."""

    assert profiles.SIGNED_EXTERNAL_EVAL_VERIFICATION_IMPLEMENTED is False
    assert profiles.DETERMINISTIC_PRE_LLM_ROLE_ACL_IMPLEMENTED is True
    monkeypatch.setattr(profiles, "resolve", lambda _name: _profile())
    monkeypatch.setattr(profiles, "kill_switch_enabled", lambda: False)

    with pytest.raises(
        backend.MaxisRuntimeError, match="Signed external Knowledge eval",
    ) as caught:
        backend._require_authoritative_routes(
            _profile(), {"request_architect": "maxis"},
        )
    assert "Role ACL enforcement is not implemented" not in str(caught.value)


@pytest.fixture
def production_graph_transport(monkeypatch, request):
    """Install a real DifyClient backed by MockTransport and return its controller."""

    monkeypatch.setenv("LAKE_MAXIS_PROD_ALLOWED_HOSTS", "maxis.example")
    routes = _remote_routes()
    profile = _profile()
    audit_events = []
    observed_requests: list[dict] = []
    open_clients: list[httpx.Client] = []
    controller = {
        "request_architect_status": "ok",
        "request_architect_intent": "chitchat",
        "query_specialist_status": "ok",
        "routes": routes,
        "retrieval_dataset_id": DATASET_ID,
        "knowledge_proof": SENTINEL_VALUE,
        "runtime_valid": True,
        "invalidate_after_response": False,
    }

    # This suite covers production-shaped graph/transport wiring, not promotion proof.
    # Simulate only the remaining signed-artifact capability and replace the
    # deployment-owned verifier with a narrow assertion over this fixed test identity.
    # The separate test above exercises the real default and proves it remains blocked.
    monkeypatch.setattr(
        profiles, "SIGNED_EXTERNAL_EVAL_VERIFICATION_IMPLEMENTED", True,
    )

    def simulated_authority_verifier(profile_name, workflow_keys):
        assert profiles.SIGNED_EXTERNAL_EVAL_VERIFICATION_IMPLEMENTED is True
        assert profiles.DETERMINISTIC_PRE_LLM_ROLE_ACL_IMPLEMENTED is True
        assert profile_name == "prod"
        assert set(workflow_keys) == {"agent_core"}

    monkeypatch.setattr(
        profiles, "require_authoritative", simulated_authority_verifier,
    )

    monkeypatch.setattr(
        backend,
        "_canonical_role_routes",
        lambda *, explicit_engine="": dict(routes),
    )
    def profile_snapshot(_name="", *, required=False):
        if required and not controller["runtime_valid"]:
            raise backend.MaxisRuntimeError("MAXIS kill switch가 활성화되어 있습니다.")
        return dict(profile)

    monkeypatch.setattr(backend, "_profile_snapshot", profile_snapshot)
    monkeypatch.setattr(credentials, "get_for", lambda *_args: "test-api-key")

    from app.agent.maxis import audit

    monkeypatch.setattr(audit, "record_audit_event", audit_events.append)

    def answer_for(role_id: str, context_pack: dict) -> dict:
        sources = context_pack.get("sources") or []
        assert len(sources) == 1
        intent_source_id = sources[0]["source_id"]
        if role_id == "request_architect":
            status = controller["request_architect_status"]
        elif role_id == "query_specialist":
            status = controller["query_specialist_status"]
        else:
            status = "ok"
        if status == "needs_input":
            payload = {}
            claims = []
            payload_claims = []
            missing_inputs = [
                "처리할 업무 대상을 알려 주세요."
                if role_id == "request_architect"
                else "조회할 시스템이나 티켓을 알려 주세요."
            ]
        elif status == "blocked":
            payload = {}
            claims = []
            payload_claims = []
            missing_inputs = []
        elif role_id == "request_architect":
            # The classification is a recommendation derived from the guarded user-intent
            # source. RequestArchitect's normal local apply() still owns the resulting
            # RequestPlan and graph route.
            payload = {
                "intent": controller["request_architect_intent"],
                "keywords": [],
                "sufficient": True,
                "request_questions": [],
                "requested_effects": [],
                "target_selectors": [],
            }
            claims = [{
                "claim_id": "intent_claim",
                "kind": "recommendation",
                "text": controller["request_architect_intent"],
                "citation_ids": ["intent_citation"],
            }]
            payload_claims = [{
                "path": "/intent",
                "claim_ids": ["intent_claim"],
            }]
            missing_inputs = []
        elif role_id == "query_specialist":
            payload = {
                "reads": [],
                "joins": [],
                "uncertainty": [],
            }
            claims = [{
                "claim_id": "query_plan_claim",
                "kind": "recommendation",
                "text": "조회 계획 없음",
                "citation_ids": ["intent_citation"],
            }]
            payload_claims = []
            missing_inputs = []
        elif role_id == "result_integrator":
            text = "반갑습니다. 요청하실 업무를 말씀해 주세요."
            payload = {"text": text}
            claims = [{
                "claim_id": "reply_claim",
                "kind": "recommendation",
                "text": text,
                "citation_ids": ["intent_citation"],
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
            "citations": ([{
                "citation_id": "intent_citation",
                "source_id": intent_source_id,
                "locator": "",
            }] if claims else []),
            "missing_inputs": missing_inputs,
            "warnings": [],
            "workflow_version": AGENT_WORKFLOW_ID,
            "knowledge_version": "knowledge-graph-1",
            "knowledge_manifest_sha256": MANIFEST_SHA256,
            "knowledge_proof": controller["knowledge_proof"],
        }

    def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.method == "POST"
        assert http_request.url == "https://maxis.example/v1/chat-messages"
        assert http_request.headers["Authorization"] == "Bearer test-api-key"
        assert b"test-api-key" not in http_request.content
        body = json.loads(http_request.content)
        assert set(body["inputs"]) == REQUIRED_START_VARIABLES
        assert body["response_mode"] == "blocking"
        assert body["workflow_id"] == AGENT_WORKFLOW_ID
        assert body["conversation_id"] == ""
        assert body["auto_generate_name"] is False
        context_pack = json.loads(body["inputs"]["context_pack"])
        assert context_pack["contract"] == "evidence-pack.v1"
        assert context_pack["request_id"] == body["inputs"]["correlation_id"]
        assert context_pack["use_case"] == body["inputs"]["role_id"]
        assert context_pack["payload_schema_sha256"]
        assert "messages" not in context_pack
        assert "approval_token" not in json.dumps(context_pack, ensure_ascii=False)
        observed_requests.append({"body": body, "context_pack": context_pack})
        role_id = body["inputs"]["role_id"]
        response = httpx.Response(200, json={
            "event": "message",
            "answer": json.dumps(
                answer_for(role_id, context_pack), ensure_ascii=False,
            ),
            "message_id": f"message-{len(observed_requests)}",
            "conversation_id": f"conversation-{len(observed_requests)}",
            "metadata": {
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
                "retriever_resources": [{
                    "dataset_id": controller["retrieval_dataset_id"],
                    "dataset_name": "PRIVATE_DATASET_NAME",
                    "document_id": "PRIVATE_DOCUMENT_ID",
                    "document_name": "PRIVATE_DOCUMENT_NAME",
                    "segment_id": "PRIVATE_SEGMENT_ID",
                    "score": 0.84,
                    "content": "PRIVATE_RETRIEVED_CONTENT",
                }],
            },
        })
        if controller["invalidate_after_response"]:
            controller["runtime_valid"] = False
        return response

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
        "kind": "recommendation",
        "text": "반갑습니다. 요청하실 업무를 말씀해 주세요.",
        "sourceIds": [requests[-1]["context_pack"]["sources"][0]["source_id"]],
    }]
    # Public Session state includes only the allow-listed projection, never wire payloads.
    rendered = json.dumps(result, ensure_ascii=False)
    assert "context_pack" not in rendered
    assert "payload_schema_json" not in rendered
    assert "test-api-key" not in rendered
    assert "PRIVATE_DATASET_NAME" not in rendered
    assert "PRIVATE_DOCUMENT_ID" not in rendered
    assert "PRIVATE_DOCUMENT_NAME" not in rendered
    assert "PRIVATE_SEGMENT_ID" not in rendered
    assert "PRIVATE_RETRIEVED_CONTENT" not in rendered
    rendered_audit = json.dumps(
        [event.model_dump(mode="json") for event in audit_events],
        ensure_ascii=False,
    )
    assert "PRIVATE_DATASET_NAME" not in rendered_audit
    assert "PRIVATE_DOCUMENT_ID" not in rendered_audit
    assert "PRIVATE_DOCUMENT_NAME" not in rendered_audit
    assert "PRIVATE_SEGMENT_ID" not in rendered_audit
    assert "PRIVATE_RETRIEVED_CONTENT" not in rendered_audit
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


@pytest.mark.parametrize("query_status", ["blocked", "needs_input"])
def test_query_specialist_control_outcome_stops_before_reads_or_writes(
    production_graph_transport, monkeypatch, query_status,
):
    controller, requests, _audit_events = production_graph_transport
    controller["request_architect_intent"] = "ask"
    controller["query_specialist_status"] = query_status
    controller["routes"]["query_specialist"] = "maxis"

    visited: list[str] = []

    def forbidden_node(name):
        def node(_state):
            visited.append(name)
            raise AssertionError(f"{name} must not run after QuerySpecialist {query_status}")

        return node

    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.agents.work_architect import WorkArchitect

    monkeypatch.setattr(QueryRunner, "node", lambda _self: forbidden_node("query_runner"))
    monkeypatch.setattr(
        ResearchAnalyst, "node", lambda _self: forbidden_node("research_analyst")
    )
    monkeypatch.setattr(
        WorkArchitect, "node", lambda _self: forbidden_node("work_architect")
    )
    monkeypatch.setattr(
        ActionExecutor, "node", lambda _self: forbidden_node("action_executor")
    )
    workflow_graph.reset()

    thread_id = f"maxis-query-specialist-terminal-{query_status}"
    result = session.ask(
        "관련 Jira 이력을 확인해 줘.",
        thread_id=thread_id,
        user_role="member",
        user_id="integration.user",
    )

    assert visited == []
    remote_roles = [row["body"]["inputs"]["role_id"] for row in requests]
    assert remote_roles[:2] == ["request_architect", "query_specialist"]
    # A generic error is rendered by the configured ResultIntegrator; needs_input has a
    # deterministic renderer and needs no third semantic call. Neither path may visit an
    # acquisition, research, drafting, approval, or execution Role.
    assert remote_roles[2:] == (
        ["result_integrator"] if query_status == "blocked" else []
    )
    assert "pending" not in result
    snapshot = workflow_graph.get_graph().get_state({
        "configurable": {"thread_id": thread_id},
    })
    assert (snapshot.values or {}).get("approval_token") == ""
    if query_status == "needs_input":
        assert result["ok"] is True
        assert result["questions"][0]["field"] == "maxis:query_specialist:1"
    else:
        assert result["ok"] is False
        assert result["error"]


@pytest.mark.parametrize(
    ("failure", "audit_code"),
    [
        ("dataset", "gateway_policy"),
        ("sentinel", "knowledge_sentinel_mismatch"),
        ("midflight", "runtime_changed_in_flight"),
    ],
)
def test_session_discards_unverified_or_midflight_stale_maxis_results(
    production_graph_transport, failure, audit_code,
):
    controller, requests, audit_events = production_graph_transport
    if failure == "dataset":
        controller["retrieval_dataset_id"] = (
            "22222222-2222-4222-8222-222222222222"
        )
    elif failure == "sentinel":
        controller["knowledge_proof"] = "LTM_RELEASE_SENTINEL_V1=drifted"
    else:
        controller["invalidate_after_response"] = True

    result = session.ask(
        "안녕, 오늘도 잘 부탁해.",
        thread_id=f"maxis-production-graph-{failure}",
        user_role="member",
        user_id="integration.user",
    )

    # A failed RequestArchitect still reaches the real ResultIntegrator node so the
    # graph can render its deterministic error projection.  That is a second Role,
    # not a transport retry: no individual Role may be sent twice.
    request_roles = [row["body"]["inputs"]["role_id"] for row in requests]
    assert request_roles[0] == "request_architect"
    assert len(request_roles) == len(set(request_roles))
    assert result["ok"] is False
    assert not result.get("lastRole")
    assert not result.get("lastRoleEngine")
    assert "반갑습니다" not in result.get("reply", "")
    statuses = [event.status for event in audit_events]
    assert statuses[::2] == ["started"] * len(statuses[::2])
    assert statuses[1::2] == ["blocked"] * len(statuses[1::2])
    assert audit_events[1].error_code == audit_code
