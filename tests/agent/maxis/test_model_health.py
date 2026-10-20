from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from app.agent.maxis.client import DifyClient, DifyClientConfig, DifyContractProbeResult
from app.agent.maxis.errors import MaxisErrorCode, MaxisGatewayError
from app.agent.maxis.registry import WorkflowBinding


WORKFLOW_ID = "abcdef12-3456-4abc-8def-abcdef123456"
SENTINEL = "LTM_RELEASE_SENTINEL_V1=synthetic-model-health"


def _client(handler, **kwargs):
    return DifyClient(
        DifyClientConfig(profile="test", stage="test", base_url="http://dify.test",
                         require_https=False, verify_tls=False),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _request_error(response, **kwargs):
    client = _client(lambda _: response, **kwargs)
    with pytest.raises(MaxisGatewayError) as caught:
        client._request_bytes("GET", "/parameters", api_key="app-synthetic",
                              payload=None, response_limit=1024 * 1024)
    return caught.value


@pytest.mark.parametrize(("payload", "expected"), [
    ({"code": "model_not_found"}, "model_unavailable"),
    ({"code": "model_not_supported"}, "model_unavailable"),
    ({"code": "provider_not_initialize"}, "model_credentials"),
    ({"error": {"code": "invalid_api_key"}}, "model_credentials"),
    ({"error": {"code": "unsupported_parameter"}}, "model_parameters"),
    ({"code": "invalid_param", "message": "Run failed: ModelNotFoundError: private model"},
     "model_unavailable"),
    ({"code": "invalid_param", "message": "Run failed: Incorrect API key provided: secret"},
     "model_credentials"),
    ({"code": "invalid_param", "message": "Unsupported parameter: private_parameter"},
     "model_parameters"),
    ({"code": "invalid_param", "message": "ValueError: invalid_core_work_draft"},
     "workflow_contract"),
    ({"code": "invalid_param", "message": "ValueError: invalid_editor_plan"},
     "workflow_contract"),
])
@pytest.mark.parametrize("status", [400, 422])
def test_dify_errors_are_safe_fixed_classifications(payload, expected, status):
    error = _request_error(httpx.Response(status, json=payload))
    assert error.code.value == expected
    assert error.status_code == status
    assert not error.retryable
    assert error.telemetry.http_status == status
    assert error.__cause__ is None
    projection = str(error) + error.telemetry.model_dump_json()
    for secret in ("private model", "private_parameter", "app-synthetic", "secret"):
        assert secret not in projection


@pytest.mark.parametrize(("status", "expected", "retryable"), [
    (401, "authentication", False), (403, "forbidden", False),
    (404, "not_found", False), (409, "conflict", False),
    (429, "rate_limited", True), (500, "upstream", True), (503, "upstream", True),
])
def test_other_status_semantics_do_not_depend_on_error_body(status, expected, retryable):
    class NeverRead(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("unnecessary error body read")
            yield b""

    error = _request_error(httpx.Response(status, stream=NeverRead()))
    assert (error.code.value, error.retryable) == (expected, retryable)


@pytest.mark.parametrize("body", [
    b"<html>model_not_found secret</html>",
    b'{"code":"model_not_found","code":"unsupported_parameter"}',
    b'{"code":"model_not_found","padding":NaN}',
    b'{"code":"model_not_found","padding":' + b"[" * 2000 + b"]" * 2000 + b"}",
    b'["model_not_found"]',
    b'{"message":{"code":"model_not_found"}}',
    b'{"code":"unrecognized","message":"secret"}',
    b'{"message":"not_invalid_core_work_draft_suffix"}',
    b'{"code":"model_not_found","padding":"' + b"x" * 8192 + b'"}',
])
def test_malformed_unknown_and_oversize_errors_fall_back_without_echo(body):
    error = _request_error(httpx.Response(400, content=body))
    assert error.code == MaxisErrorCode.BAD_REQUEST
    assert "secret" not in str(error)
    assert error.__cause__ is None


def test_streamed_error_body_stops_at_size_bound_and_closes():
    state = {"chunks": 0, "closed": False}

    class TooLarge(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                state["chunks"] += 1
                yield b"x" * 1024

        def close(self):
            state["closed"] = True

    error = _request_error(httpx.Response(400, stream=TooLarge()))
    assert error.code == MaxisErrorCode.BAD_REQUEST
    assert state == {"chunks": 9, "closed": True}


@pytest.mark.parametrize("failure", ["deadline", "timeout", "decode"])
def test_error_body_failure_keeps_received_http_status(failure):
    current_time = [0.0]

    class FailedRead(httpx.SyncByteStream):
        def __iter__(self):
            if failure == "deadline":
                current_time[0] = 40.0
                yield b'{"code":"model_not_found"}'
            elif failure == "timeout":
                raise httpx.ReadTimeout("secret error text")
            else:
                raise httpx.DecodingError("secret error text")

    error = _request_error(httpx.Response(400, stream=FailedRead()),
                           clock=lambda: current_time[0])
    assert error.code == MaxisErrorCode.BAD_REQUEST
    assert error.status_code == 400
    assert not error.retryable
    assert "secret" not in str(error)
    assert error.__cause__ is None


def _probe(mutate=lambda answer: answer):
    def handler(request):
        body = json.loads(request.content)
        assert "model" not in body
        assert "model" not in body["inputs"]
        inputs = body["inputs"]
        pack = json.loads(inputs["context_pack"])
        source = pack["sources"][0]
        answer = mutate({
            "contract": "remote-role-result.v1", "role_id": inputs["role_id"],
            "status": "ok", "payload": {"state": "ready"},
            "claims": [{"claim_id": "probe_claim", "kind": "fact",
                        "text": source["content"], "citation_ids": ["probe_citation"]}],
            "payload_claims": [{"path": "/state", "claim_ids": ["probe_claim"]}],
            "citations": [{"citation_id": "probe_citation",
                           "source_id": source["source_id"], "locator": ""}],
            "missing_inputs": [], "warnings": [],
            "workflow_version": inputs["workflow_version"],
            "knowledge_version": inputs["knowledge_version"],
            "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
            "knowledge_proof": SENTINEL,
        })
        return httpx.Response(200, json={
            "event": "message", "message_id": "synthetic-message",
            "conversation_id": "synthetic-conversation", "answer": json.dumps(answer),
            "metadata": {},
        })

    return _client(handler).probe_contract(
        WorkflowBinding(
            key="agent_core", app_type="chatflow", endpoint="/chat-messages",
            response_mode="blocking", conversation_mode="stateless",
            api_key_env="LAKE_MAXIS_AGENT_CORE_API_KEY",
            output_contract="remote-role-result.v1", max_response_bytes=1024 * 1024,
            roles=("knowledge_curator",),
        ),
        api_key="app-synthetic", workflow_version=WORKFLOW_ID,
        knowledge_version="synthetic-kb", knowledge_manifest_sha256="0" * 64,
        knowledge_sentinel_sha256=hashlib.sha256(SENTINEL.encode()).hexdigest(),
    )


def test_generation_verification_defaults_to_unknown_without_canary():
    result = DifyContractProbeResult(ok=True, latency_ms=1)
    assert result.generation_verified is False


def test_canary_generation_is_verified_without_assuming_model_identity():
    result = _probe()
    assert result.ok
    assert result.generation_verified
    assert result.knowledge_retrieval_verified
    assert "model" not in DifyContractProbeResult.model_fields


@pytest.mark.parametrize("mutate", [
    lambda value: {**value, "payload": {"unrecognized": "wrong"}},
    lambda value: {**value, "workflow_version": "wrong-version"},
    lambda value: {**value, "status": "needs_input", "missing_inputs": ["target"]},
])
def test_http_success_or_invalid_contract_is_not_generation_health(mutate):
    result = _probe(mutate)
    assert not result.ok
    assert not result.generation_verified


def test_knowledge_failure_is_distinct_from_verified_generation():
    result = _probe(lambda value: {**value, "knowledge_proof": "different-proof"})
    assert not result.ok
    assert result.error_code == "knowledge_retrieval_failed"
    assert result.generation_verified
    assert not result.knowledge_retrieval_verified


def project(**fields):
    from app.agent.maxis import profiles
    return profiles._safe_public_probe_workflows({"agent_core": fields})["agent_core"]


def generation(**fields):
    return dict(statusCode=200, generationAttempted=True, generationVerified=True,
                appModeVerified=True, executionContractVerified=True,
                versionBindingVerified=True, **fields)


def test_metadata_success_and_legacy_success_do_not_prove_model_health():
    row = project(ok=True, statusCode=200, appModeVerified=True)
    assert row["modelHealth"]["status"] == "not_checked"
    assert row["generationVerified"] is False


def test_success_is_explicitly_limited_to_synthetic_path_not_model_identity():
    row = project(**generation())
    assert row["modelHealth"]["status"] == "passed"
    assert row["modelHealth"]["scope"] == "synthetic_control_path"
    assert row["modelHealth"]["identityStatus"] == "not_exposed"
    assert "별도" in row["modelHealth"]["nextAction"]
    assert project(**row) == row


@pytest.mark.parametrize("field", [
    "generationAttempted", "generationVerified", "appModeVerified",
    "executionContractVerified", "versionBindingVerified", "statusCode",
])
def test_incomplete_or_stale_proof_never_passes(field):
    value = generation()
    value[field] = False
    assert project(**value)["modelHealth"]["status"] != "passed"


@pytest.mark.parametrize("code,expected", [
    ("model_credentials", "인증정보"),
    ("model_unavailable", "등록된 모델"),
    ("model_parameters", "파라미터"),
    ("workflow_contract", "코드 노드"),
    ("bad_request", "단정할 수 없습니다"),
    ("workflow_version_mismatch", "게시 Workflow ID"),
])
def test_failures_have_fixed_actionable_model_diagnostics(code, expected):
    row = project(generationAttempted=True, errorCode=code, statusCode=400)
    assert row["modelHealth"]["status"] == "failed"
    assert row["modelHealth"]["reasonCode"] == code
    assert expected in row["modelHealth"]["nextAction"]


def test_generation_and_knowledge_checks_are_separate():
    row = project(**generation(ok=False, knowledgeRetrievalVerified=False,
                               errorCode="knowledge_retrieval_failed"))
    assert row["ok"] is False
    assert row["modelHealth"]["status"] == "passed"


def test_upstream_or_stored_metadata_cannot_inject_model_name_or_error_content():
    sentinel = "private-secret-value"
    row = project(ok=True, generationVerified="true", errorCode=sentinel,
                  model=sentinel, modelHealth={"status": "passed", "nextAction": sentinel})
    assert sentinel not in json.dumps(row)
    assert row["modelHealth"]["status"] == "not_checked"
    assert row["modelHealth"]["reasonCode"] == "probe_failed"


def test_route_merge_retains_generation_proof_only_as_a_distinct_observation():
    from app.agent.routes import _maxis_contract_probe_result
    result = _maxis_contract_probe_result(
        {"ok": True, "appModeVerified": True, "statusCode": 200, "ms": 1},
        {"ok": True, "status_code": 200, "generation_verified": True,
         "contract_verified": True, "version_verified": True,
         "citation_verified": True, "knowledge_retrieval_verified": True},
        knowledge_manifest_sha256="a" * 64)
    assert result["ok"] is True
    assert project(**result)["modelHealth"]["status"] == "passed"


def test_hostile_content_length_keeps_http_classification():
    error = _request_error(httpx.Response(400, content=b'{"code":"model_not_found"}',
                                         headers={"content-length": "9" * 5000}))
    assert error.code == MaxisErrorCode.BAD_REQUEST
