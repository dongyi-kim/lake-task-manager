"""Pinned Workflow API transport, using real contracts and an HTTP fake only."""
from __future__ import annotations

from datetime import datetime, timezone
import json

import httpx
import pytest

from app.agent.maxis.client import CircuitBreaker, DifyClient, DifyClientConfig
from app.agent.maxis.context_guard import ContextGuard, ContextPolicy
from app.agent.maxis.errors import MaxisErrorCode, MaxisGatewayError
from app.agent.maxis.taskflow_contracts import (
    TaskFlowPinV1, TaskFlowRequestV1, TaskFlowResultV1, evidence_digest,
)
from app.agent.maxis.taskflow_client import TaskFlowClient

WORKFLOW = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def request_pack():
    now = datetime.now(timezone.utc)
    evidence = ContextGuard(ContextPolicy(allowed_scopes={}), b"fixture-pseudonym-key").build(
        request_id="taskflow-test", use_case="task_draft", query="Synthetic task draft",
        user_identity="synthetic-user", requested_at=now,
        payload_schema={"type": "object", "properties": {}, "additionalProperties": False},
        knowledge_manifest_sha256="a" * 64,
        sources=[dict(source_id="synthetic-source", kind="ltm", canonical_id="fixture",
                      title="Synthetic task", scope="", url="", content="Synthetic task draft",
                      retrieved_at=now)],
    )
    pin = TaskFlowPinV1(profile_fingerprint="fixture-profile", workflow_release=WORKFLOW,
        workflow_sha256="b" * 64, research_release=OTHER, research_sha256="c" * 64,
        knowledge_manifest_sha256="a" * 64)
    return TaskFlowRequestV1(request_id=evidence.request_id, revision=0, pin=pin,
        evidence=evidence, evidence_sha256=evidence_digest(evidence))


def native_response(request, **changes):
    result = TaskFlowResultV1(request_id=request.request_id, revision=request.revision,
        pin=request.pin, evidence_sha256=request.evidence_sha256,
        status="blocked", reason="insufficient_evidence")
    raw = {"workflow_run_id": RUN, "task_id": "native-task", "data": {
        "id": RUN, "workflow_id": WORKFLOW, "status": "succeeded",
        "outputs": {"result_json": result.model_dump_json()},
        "total_tokens": 123, "elapsed_time": 1.25,
    }}
    raw.update(changes)
    return raw


def config(**changes):
    values = dict(profile="test", stage="test", base_url="http://dify.test",
        profile_fingerprint="fixture-profile", require_https=False, verify_tls=False)
    return DifyClientConfig(**{**values, **changes})


def transport(handler, **config_changes):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = DifyClient(config(**config_changes), client=http, breaker=CircuitBreaker())
    return http, gateway, TaskFlowClient(gateway)


def test_pinned_endpoint_stateless_body_and_native_observations(request_pack):
    observed = []
    def handler(request):
        observed.append(request)
        return httpx.Response(200, json=native_response(request_pack))
    http, _, client = transport(handler)
    with http:
        response = client.invoke(request_pack, api_key="test-key", timeout_seconds=2.5)
    request = observed[0]
    assert request.method == "POST"
    assert request.url.path == f"/v1/workflows/{WORKFLOW}/run"
    body = json.loads(request.content)
    assert set(body) == {"inputs", "response_mode", "user"}
    assert body["response_mode"] == "blocking"
    assert body["user"] == request_pack.evidence.pseudonymous_user
    assert set(body["inputs"]) == {"taskflow_request"}
    assert json.loads(body["inputs"]["taskflow_request"]) == request_pack.model_dump(mode="json")
    assert all(value <= 2.5 for value in request.extensions["timeout"].values())
    assert response.result.status == "blocked"
    assert response.telemetry.workflow_run_id == RUN
    assert response.telemetry.total_tokens == 123
    assert response.elapsed_seconds == 1.25
    assert response.telemetry.native_model_call_count is None
    assert response.telemetry.reported_cost is None
    assert response.telemetry.quality_status == "not_evaluated"


@pytest.mark.parametrize("mutation", [
    lambda raw: raw["data"].update(workflow_id=OTHER),
    lambda raw: raw["data"].update(id=OTHER),
    lambda raw: raw.update(workflow_run_id="hostile run id"),
    lambda raw: raw["data"].update(status="running"),
    lambda raw: raw["data"].update(status="failed", error="SECRET remote payload"),
    lambda raw: raw["data"].update(outputs={"result_json": {}}),
    lambda raw: raw["data"]["outputs"].update(result_json="```json\n{}\n```"),
    lambda raw: raw["data"]["outputs"].update(result_json='{"status":"blocked","status":"completed"}'),
    lambda raw: raw["data"]["outputs"].update(result_json='{"value":NaN}'),
])
def test_rejects_native_or_exact_json_contract_failure(request_pack, mutation):
    raw = native_response(request_pack)
    mutation(raw)
    http, _, client = transport(lambda _: httpx.Response(200, json=raw))
    with http, pytest.raises(MaxisGatewayError) as caught:
        client.invoke(request_pack, api_key="test-key")
    assert "SECRET" not in str(caught.value)
    assert caught.value.telemetry.transport_status == "succeeded"
    assert caught.value.telemetry.result_status == "failed"


@pytest.mark.parametrize("field,value", [
    ("request_id", "different-request"), ("revision", 1),
    ("evidence_sha256", "d" * 64),
    ("pin", {"workflow_sha256": "d" * 64}),
])
def test_rejects_result_identity_rebinding(request_pack, field, value):
    raw = native_response(request_pack)
    result = json.loads(raw["data"]["outputs"]["result_json"])
    if field == "pin":
        result[field].update(value)
    else:
        result[field] = value
    raw["data"]["outputs"]["result_json"] = json.dumps(result)
    http, _, client = transport(lambda _: httpx.Response(200, json=raw))
    with http, pytest.raises(MaxisGatewayError) as caught:
        client.invoke(request_pack, api_key="test-key")
    assert caught.value.code == MaxisErrorCode.INVALID_RESPONSE


@pytest.mark.parametrize("value", [None, True, -1, "12", float("inf"), 10 ** 400],
                         ids=["absent", "bool", "negative", "string", "nonfinite", "oversize-int"])
def test_unknown_native_usage_not_coerced_or_inferred(request_pack, value):
    raw = native_response(request_pack)
    raw["data"].update(total_tokens=value, elapsed_time=value)
    # An attacker-controlled output cannot claim native usage or a model call count.
    raw["data"]["outputs"].update(total_tokens=999, native_model_call_count=8)
    http, _, client = transport(lambda _: httpx.Response(200, content=json.dumps(raw)))
    with http:
        if value == float("inf"):
            with pytest.raises(MaxisGatewayError):
                client.invoke(request_pack, api_key="test-key")
            return
        response = client.invoke(request_pack, api_key="test-key")
    assert response.telemetry.total_tokens is None
    assert response.telemetry.native_model_call_count is None
    assert response.elapsed_seconds is None


@pytest.mark.parametrize("malformed", [b'{"data":{},"data":{}}', b'\xff', b'{}' * 40000,
                                     '{}'.encode("utf-16")],
                         ids=["duplicate-key", "invalid-utf8", "oversize", "utf16"])
def test_bounded_strict_utf8_response(request_pack, malformed):
    http, _, client = transport(lambda _: httpx.Response(200, content=malformed))
    with http, pytest.raises(MaxisGatewayError):
        client.invoke(request_pack, api_key="test-key")


@pytest.mark.parametrize("mutation", [
    lambda request: request.model_copy(update={"evidence_sha256": "d" * 64}),
    lambda request: request.model_copy(update={"pin": request.pin.model_copy(update={"workflow_release": "../unsafe"})}),
    lambda request: request.model_copy(update={"pin": request.pin.model_copy(update={"profile_fingerprint": "other-profile"})}),
])
def test_revalidates_before_any_http(request_pack, mutation):
    observed = []
    http, _, client = transport(lambda _: observed.append(True))
    with http, pytest.raises(MaxisGatewayError):
        client.invoke(mutation(request_pack), api_key="test-key")
    assert observed == []


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
def test_invalid_remaining_time_never_sends_request(request_pack, timeout):
    observed = []
    http, _, client = transport(lambda _: observed.append(True))
    with http, pytest.raises(MaxisGatewayError):
        client.invoke(request_pack, api_key="test-key", timeout_seconds=timeout)
    assert observed == []


@pytest.mark.parametrize("status,code", [(401, MaxisErrorCode.AUTHENTICATION), (302, MaxisErrorCode.HTTP), (503, MaxisErrorCode.UPSTREAM)])
def test_http_failure_does_not_expose_remote_body_or_follow_redirect(request_pack, status, code):
    observed = []
    def handler(request):
        observed.append(request)
        return httpx.Response(status, text="SECRET remote body", headers={"location": "https://evil.example"})
    http, _, client = transport(handler)
    with http, pytest.raises(MaxisGatewayError) as caught:
        client.invoke(request_pack, api_key="test-key")
    assert caught.value.code == code
    assert "SECRET" not in str(caught.value)
    assert len(observed) == 1
    assert caught.value.telemetry.requested_workflow_id == WORKFLOW


def test_remaining_deadline_applies_after_upstream_return(request_pack):
    current = [0.0]
    observed = []
    def handler(request):
        observed.append(request)
        current[0] += 2
        return httpx.Response(200, json=native_response(request_pack))
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        gateway = DifyClient(config(), client=http, clock=lambda: current[0])
        with pytest.raises(MaxisGatewayError) as caught:
            TaskFlowClient(gateway).invoke(request_pack, api_key="test-key", timeout_seconds=1)
    assert caught.value.code == MaxisErrorCode.TIMEOUT
    assert len(observed) == 1


def test_direct_http_seam_cannot_bypass_input_validation(request_pack):
    observed = []
    http, gateway, _ = transport(lambda _: observed.append(True))
    with http, pytest.raises(MaxisGatewayError):
        gateway.request_taskflow_bytes(request_pack.model_copy(update={"revision": 20}), api_key="test-key")
    assert observed == []


@pytest.mark.parametrize("manifest,accepted", [("a" * 64, True), ("d" * 64, False)])
def test_completed_draft_has_independent_knowledge_binding(request_pack, manifest, accepted):
    raw = native_response(request_pack)
    result = json.loads(raw["data"]["outputs"]["result_json"])
    result.update(status="completed", reason="", draft_result={
        "role_id": "work_architect", "status": "ok",
        "payload": {"contract": "work-draft.v1", "mode": "task", "structure": "single_task",
            "items": [{"summary": "Synthetic task draft", "tier": "task", "issue_type": "Task",
                       "scope_in": ["Synthetic task"], "dod": ["Synthetic draft"],
                       "source_ids": ["synthetic-source"]}]},
        "claims": [{"claim_id": "claim-1", "kind": "inference",
                    "text": "Synthetic task draft", "citation_ids": ["cite-1"]}],
        "citations": [{"citation_id": "cite-1", "source_id": "synthetic-source"}],
        "payload_claims": [{"path": path, "claim_ids": ["claim-1"]} for path in
            ["/items/0/summary", "/items/0/scope_in/0", "/items/0/dod/0"]],
        "workflow_version": WORKFLOW, "knowledge_version": "synthetic-kb",
        "knowledge_manifest_sha256": manifest,
        "knowledge_proof": "LTM_RELEASE_SENTINEL_V1=synthetic",
    })
    raw["data"]["outputs"]["result_json"] = json.dumps(result)
    http, _, client = transport(lambda _: httpx.Response(200, json=raw))
    with http:
        if accepted:
            response = client.invoke(request_pack, api_key="test-key")
            assert response.result.status == "completed"
            # Transport acceptance is not a grounding or quality verdict.
            assert response.telemetry.result_status == "not_evaluated"
        else:
            with pytest.raises(MaxisGatewayError) as caught:
                client.invoke(request_pack, api_key="test-key")
            assert caught.value.code == MaxisErrorCode.INVALID_RESPONSE
