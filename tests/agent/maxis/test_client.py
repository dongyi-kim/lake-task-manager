from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from app.agent.maxis import (
    CircuitBreaker,
    DifyClient,
    DifyClientConfig,
    MaxisErrorCode,
    MaxisGatewayError,
    REQUIRED_START_VARIABLES,
    WorkflowBinding,
    canonical_api_base,
)
from app.agent.maxis.contracts import EvidencePackV1, canonical_payload_schema
from app.agent.maxis.context_guard import (
    MAX_CONTEXT_PACK_UTF8_BYTES,
    canonical_evidence_pack_json,
)


_PAYLOAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"summary": {"type": "string", "minLength": 1}},
    "required": ["summary"],
}
WORKFLOW_ID = "abcdef12-3456-4abc-8def-abcdef123456"
OTHER_WORKFLOW_ID = "bcdefa23-4567-4bcd-9efa-bcdefa234567"


def _binding(
    limit=1024 * 1024,
    *,
    key="agent_core",
    roles=("work_architect",),
):
    return WorkflowBinding(
        key=key,
        app_type="chatflow",
        endpoint="/chat-messages",
        response_mode="blocking",
        conversation_mode="stateless",
        api_key_env=f"LAKE_MAXIS_{key.upper()}_API_KEY",
        output_contract="remote-role-result.v1",
        max_response_bytes=limit,
        roles=roles,
    )


def _config(**overrides):
    values = {
        "profile": "test",
        "stage": "test",
        "base_url": "http://dify.test/",
        "require_https": False,
        "verify_tls": False,
    }
    values.update(overrides)
    return DifyClientConfig(**values)


def _success(answer="{}", *, retriever_resources=None):
    metadata = {"usage": {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }}
    if retriever_resources is not None:
        metadata["retriever_resources"] = retriever_resources
    return {
        "event": "message",
        "task_id": "task-1",
        "message_id": "message-1",
        "conversation_id": "conversation-created-by-server",
        "answer": answer,
        "metadata": metadata,
    }


def _context_pack():
    schema_json, schema_sha256 = canonical_payload_schema(_PAYLOAD_SCHEMA)
    return {
        "contract": "evidence-pack.v1",
        "request_id": "req-001",
        "use_case": "work_architect",
        "query": "질문",
        "pseudonymous_user": "ltm_0123456789abcdef",
        "requested_at": "2026-09-04T12:00:00Z",
        "scope": {"projects": [], "spaces": [], "repositories": []},
        "sources": [],
        "facts": [],
        "metrics": [],
        "freshness": "empty",
        "partial": False,
        "flags": [],
        "knowledge_manifest_sha256": None,
        "payload_schema_json": schema_json,
        "payload_schema_sha256": schema_sha256,
    }


def _inputs():
    return {
        "context_pack": _context_pack(),
        "role_id": "work_architect",
        "correlation_id": "req-001",
        "output_contract": "remote-role-result.v1",
        "workflow_version": WORKFLOW_ID,
        "knowledge_version": "kb-1",
    }


def _parameters(keys=REQUIRED_START_VARIABLES):
    return {
        "user_input_form": [
            {("paragraph" if key == "context_pack" else "text-input"): {
                "label": key,
                "variable": key,
                "required": True,
                "max_length": (
                    MAX_CONTEXT_PACK_UTF8_BYTES if key == "context_pack" else 128
                ),
            }}
            for key in sorted(keys)
        ],
        "file_upload": {"enabled": False},
    }


def test_native_request_telemetry_correlates_pin_and_preserves_unknowns():
    raw = _success("private answer must never reach telemetry")
    raw["workflow_run_id"] = OTHER_WORKFLOW_ID
    raw["metadata"]["usage"].update(total_price="0.000125", currency="USD")
    raw["metadata"]["native_model_call_count"] = 99  # Unsupported, not trusted.
    raw["metadata"]["reasoning"] = "private reasoning"
    response = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=raw),
        )),
    ).invoke(
        _binding(), api_key="app-private-secret", query="질문",
        user="ltm_0123456789abcdef", inputs=_inputs(), workflow_id=WORKFLOW_ID,
    )
    observation = response.telemetry
    assert observation.transport_status == "succeeded"
    assert observation.http_status == 200
    assert observation.requested_workflow_id == WORKFLOW_ID
    assert observation.workflow_run_id == OTHER_WORKFLOW_ID
    assert observation.total_tokens == 15
    assert observation.reported_cost == 0.000125
    assert observation.cost_currency == "USD"
    assert observation.native_model_call_count is None
    assert observation.result_status == "not_evaluated"
    assert observation.quality_status == "not_evaluated"
    assert observation.message_id_sha256 == hashlib.sha256(b"message-1").hexdigest()
    encoded = observation.model_dump_json()
    for forbidden in ("private answer", "private reasoning", "app-private-secret", "conversation-created", "message-1"):
        assert forbidden not in encoded


def test_token_triple_is_observed_not_repaired_or_claimed_as_accounting_total():
    from app.agent.maxis.telemetry import request_observation

    observation = request_observation(
        transport_status="succeeded", latency_ms=1, http_status=200,
        raw={"metadata": {"usage": {
            "prompt_tokens": 8, "completion_tokens": 7, "total_tokens": 999,
        }}},
    )
    assert (observation.prompt_tokens, observation.completion_tokens, observation.total_tokens) == (8, 7, 999)
    assert observation.usage_scope == "response_metadata"
    assert observation.native_model_call_count is None


@pytest.mark.parametrize("usage", [{}, {"total_price": "NaN", "currency": "USD"}, {"total_price": 0.1}])
def test_request_metrics_missing_or_invalid_usage_is_unknown_not_zero(usage):
    raw = _success()
    raw["metadata"]["usage"] = usage
    raw["workflow_run_id"] = "app-secret-cannot-be-a-native-run-id"
    response = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=raw),
        )),
    ).invoke(_binding(), api_key="app-key", query="질문",
             user="ltm_0123456789abcdef", inputs=_inputs())
    assert response.telemetry.total_tokens is None
    assert response.telemetry.reported_cost is None
    assert response.telemetry.native_model_call_count is None
    assert response.telemetry.workflow_run_id is None


@pytest.mark.parametrize(("failure", "transport", "status"), [
    ("http", "failed", 503), ("timeout", "failed", None),
    ("network", "failed", None), ("json", "succeeded", 200),
    ("retrieval", "succeeded", 200), ("policy", "not_attempted", None),
    ("circuit", "not_attempted", None),
])
def test_request_failure_preserves_transport_observation(failure, transport, status):
    clock = [0.0]
    def handler(request):
        clock[0] = 0.125
        if failure == "timeout":
            raise httpx.ReadTimeout("private network description", request=request)
        if failure == "network":
            raise httpx.ConnectError("private network description", request=request)
        if failure == "http":
            return httpx.Response(503, text="private upstream body")
        if failure == "json":
            return httpx.Response(200, text="not json private body")
        return httpx.Response(200, json=_success())
    breaker = CircuitBreaker(failure_threshold=1)
    if failure == "circuit":
        breaker.record_failure()
    client = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(handler)),
        breaker=breaker, clock=lambda: clock[0],
    )
    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(), api_key="app-key", query="invalid" if failure == "policy" else "질문",
            user="ltm_0123456789abcdef", inputs=_inputs(), workflow_id=WORKFLOW_ID,
            expected_knowledge_dataset_id=(
                "11111111-1111-4111-8111-111111111111" if failure == "retrieval" else ""
            ),
        )
    observation = caught.value.telemetry
    assert observation.transport_status == transport
    assert observation.http_status == status
    assert observation.latency_ms == (0 if transport == "not_attempted" else 125)
    assert observation.requested_workflow_id == WORKFLOW_ID
    assert observation.native_model_call_count is None
    assert "private" not in observation.model_dump_json()


def test_invoke_is_blocking_stateless_and_uses_canonical_v1_url():
    observed = {}

    def handler(request):
        observed["url"] = str(request.url)
        observed["body"] = json.loads(request.content)
        observed["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=_success())

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    result = DifyClient(_config(), client=http).invoke(
        _binding(),
        api_key="app-test-key",
        query="질문",
        user="ltm_0123456789abcdef",
        inputs=_inputs(),
        workflow_id=WORKFLOW_ID.upper(),
    )
    assert observed["url"] == "http://dify.test/v1/chat-messages"
    assert observed["auth"] == "Bearer app-test-key"
    assert observed["body"]["response_mode"] == "blocking"
    assert observed["body"]["workflow_id"] == WORKFLOW_ID
    assert observed["body"]["conversation_id"] == ""
    assert observed["body"]["auto_generate_name"] is False
    serialized_pack = observed["body"]["inputs"]["context_pack"]
    assert isinstance(serialized_pack, str)
    assert json.loads(serialized_pack)["contract"] == "evidence-pack.v1"
    assert result.message_id == "message-1"
    assert result.usage.total_tokens == 15
    assert result.retrieval.dataset_verified is False
    assert result.retrieval.governed_resource_count == 0
    assert result.retrieval.non_governed_resource_count == 0


def test_invoke_verifies_bounded_retrieval_without_preserving_resource_data():
    resources = [
        {
            "position": 1,
            "dataset_id": "11111111-1111-4111-8111-111111111111",
            "dataset_name": "RAW_DATASET_NAME_MUST_NOT_SURVIVE",
            "document_id": "RAW_DOCUMENT_ID_MUST_NOT_SURVIVE",
            "document_name": "RAW_DOCUMENT_NAME_MUST_NOT_SURVIVE",
            "segment_id": "RAW_SEGMENT_ID_MUST_NOT_SURVIVE",
            "score": 0.81234567,
            "content": "RAW_RETRIEVED_CONTENT_MUST_NOT_SURVIVE",
        },
        {
            "position": 2,
            "dataset_id": "11111111-1111-4111-8111-111111111111",
            "document_id": "another-private-document",
            "score": 0.4,
            "content": "another private chunk",
        },
    ]
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, json=_success(retriever_resources=resources),
        )
    ))

    result = DifyClient(_config(), client=http).invoke(
        _binding(), api_key="app-key", query="질문",
        user="ltm_0123456789abcdef", inputs=_inputs(), workflow_id=WORKFLOW_ID,
        expected_knowledge_dataset_id=(
            "11111111-1111-4111-8111-111111111111"
        ),
    )

    assert result.retrieval.dataset_verified is True
    assert result.retrieval.resource_count == 2
    assert result.retrieval.governed_resource_count == 2
    assert result.retrieval.non_governed_resource_count == 0
    assert result.retrieval.scored_resource_count == 2
    assert result.retrieval.minimum_score == 0.4
    assert result.retrieval.maximum_score == 0.812346
    encoded = result.model_dump_json()
    assert "11111111-1111-4111-8111-111111111111" not in encoded
    assert "RAW_DATASET_NAME_MUST_NOT_SURVIVE" not in encoded
    assert "RAW_DOCUMENT_ID_MUST_NOT_SURVIVE" not in encoded
    assert "RAW_DOCUMENT_NAME_MUST_NOT_SURVIVE" not in encoded
    assert "RAW_SEGMENT_ID_MUST_NOT_SURVIVE" not in encoded
    assert "RAW_RETRIEVED_CONTENT_MUST_NOT_SURVIVE" not in encoded


def test_invoke_accepts_bounded_native_role_and_sentinel_aggregation():
    governed_id = "11111111-1111-4111-8111-111111111111"
    sentinel_id = "22222222-2222-4222-8222-222222222222"
    resources = [
        {"dataset_id": governed_id, "score": 0.8, "content": "private-role-1"},
        {"dataset_id": governed_id, "score": 0.7, "content": "private-role-2"},
        {"dataset_id": sentinel_id, "score": 1.0, "content": "private-sentinel"},
    ]
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, json=_success(retriever_resources=resources),
        )
    ))

    result = DifyClient(_config(), client=http).invoke(
        _binding(), api_key="app-key", query="질문",
        user="ltm_0123456789abcdef", inputs=_inputs(), workflow_id=WORKFLOW_ID,
        expected_knowledge_dataset_id=governed_id,
    )

    assert result.retrieval.dataset_verified is True
    assert result.retrieval.resource_count == 3
    assert result.retrieval.governed_resource_count == 2
    assert result.retrieval.non_governed_resource_count == 1
    encoded = result.model_dump_json()
    assert governed_id not in encoded
    assert sentinel_id not in encoded
    assert "private-role" not in encoded
    assert "private-sentinel" not in encoded


@pytest.mark.parametrize(
    "resources",
    [
        None,
        [],
        [{"dataset_id": "22222222-2222-4222-8222-222222222222"}],
        [{"score": 0.9}],
    ],
)
def test_invoke_fails_closed_when_expected_knowledge_dataset_is_not_proven(resources):
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, json=_success(retriever_resources=resources),
        )
    ))

    with pytest.raises(MaxisGatewayError) as caught:
        DifyClient(_config(), client=http).invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(), workflow_id=WORKFLOW_ID,
            expected_knowledge_dataset_id=(
                "11111111-1111-4111-8111-111111111111"
            ),
        )

    assert caught.value.code is MaxisErrorCode.POLICY


@pytest.mark.parametrize(
    "resources",
    [
        [
            {"dataset_id": "11111111-1111-4111-8111-111111111111"},
            {"dataset_id": "11111111-1111-4111-8111-111111111111"},
            {"dataset_id": "11111111-1111-4111-8111-111111111111"},
        ],
        [
            {"dataset_id": "11111111-1111-4111-8111-111111111111"},
            {"dataset_id": "22222222-2222-4222-8222-222222222222"},
            {"dataset_id": "33333333-3333-4333-8333-333333333333"},
        ],
        [
            {"dataset_id": "11111111-1111-4111-8111-111111111111"},
            {"dataset_id": "not-a-dataset-id"},
        ],
    ],
)
def test_invoke_fails_closed_on_native_retrieval_id_or_count_drift(resources):
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, json=_success(retriever_resources=resources),
        )
    ))

    with pytest.raises(MaxisGatewayError) as caught:
        DifyClient(_config(), client=http).invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(), workflow_id=WORKFLOW_ID,
            expected_knowledge_dataset_id=(
                "11111111-1111-4111-8111-111111111111"
            ),
        )

    assert caught.value.code is MaxisErrorCode.POLICY


def test_invoke_rejects_unbounded_or_invalid_retrieval_metadata():
    resources = [
        {
            "dataset_id": "11111111-1111-4111-8111-111111111111",
            "score": 0.5,
        }
    ] * 21
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, json=_success(retriever_resources=resources),
        )
    ))

    with pytest.raises(MaxisGatewayError) as caught:
        DifyClient(_config(), client=http).invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(), workflow_id=WORKFLOW_ID,
            expected_knowledge_dataset_id=(
                "11111111-1111-4111-8111-111111111111"
            ),
        )

    assert caught.value.code is MaxisErrorCode.INVALID_RESPONSE


@pytest.mark.parametrize("event", [None, "agent_message"])
def test_blocking_invoke_requires_exact_message_event(event):
    response = _success()
    if event is None:
        response.pop("event")
    else:
        response["event"] = event
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=response)
    ))

    with pytest.raises(MaxisGatewayError) as caught:
        DifyClient(_config(), client=http).invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(),
            workflow_id=WORKFLOW_ID,
        )

    assert caught.value.code is MaxisErrorCode.INVALID_RESPONSE


def test_http_failure_is_classified_and_never_retried():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="secret upstream response")

    client = DifyClient(_config(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(),
        )
    assert calls == 1
    assert caught.value.code is MaxisErrorCode.UPSTREAM
    assert "secret upstream response" not in str(caught.value)


def test_circuit_opens_after_three_failures_without_fourth_request():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    client = DifyClient(
        _config(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        breaker=CircuitBreaker(),
    )
    for _ in range(3):
        with pytest.raises(MaxisGatewayError) as caught:
            client.invoke(
                _binding(), api_key="app-key", query="질문",
                user="ltm_0123456789abcdef", inputs=_inputs(),
            )
        assert caught.value.code is MaxisErrorCode.UPSTREAM
    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(),
        )
    assert caught.value.code is MaxisErrorCode.CIRCUIT_OPEN
    assert calls == 3


def test_shared_circuit_spans_clients_and_rotations_get_new_scope():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    scoped = _config(profile_fingerprint="profile-shared-breaker-a")
    for _ in range(3):
        client = DifyClient(scoped, client=httpx.Client(transport=transport))
        with pytest.raises(MaxisGatewayError) as caught:
            client.invoke(
                _binding(), api_key="shared-app-key", query="질문",
                user="ltm_0123456789abcdef", inputs=_inputs(),
            )
        assert caught.value.code is MaxisErrorCode.UPSTREAM

    fourth = DifyClient(scoped, client=httpx.Client(transport=transport))
    with pytest.raises(MaxisGatewayError) as caught:
        fourth.invoke(
            _binding(), api_key="shared-app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(),
        )
    assert caught.value.code is MaxisErrorCode.CIRCUIT_OPEN
    assert calls == 3

    # Neither credential nor profile rotations inherit the old open circuit.
    rotated_key = DifyClient(scoped, client=httpx.Client(transport=transport))
    with pytest.raises(MaxisGatewayError) as caught:
        rotated_key.invoke(
            _binding(), api_key="rotated-app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(),
        )
    assert caught.value.code is MaxisErrorCode.UPSTREAM
    rotated_profile = DifyClient(
        _config(profile_fingerprint="profile-shared-breaker-b"),
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(MaxisGatewayError) as caught:
        rotated_profile.invoke(
            _binding(), api_key="shared-app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(),
        )
    assert caught.value.code is MaxisErrorCode.UPSTREAM
    other_workflow = WorkflowBinding(
        key="editor_author",
        app_type="chatflow",
        endpoint="/chat-messages",
        response_mode="blocking",
        conversation_mode="stateless",
        api_key_env="LAKE_MAXIS_EDITOR_AUTHOR_API_KEY",
        output_contract="remote-role-result.v1",
        max_response_bytes=1024 * 1024,
        roles=("work_architect",),
    )
    rotated_workflow = DifyClient(scoped, client=httpx.Client(transport=transport))
    with pytest.raises(MaxisGatewayError) as caught:
        rotated_workflow.invoke(
            other_workflow, api_key="shared-app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(),
        )
    assert caught.value.code is MaxisErrorCode.UPSTREAM
    assert calls == 6


def test_invalid_success_payloads_also_open_circuit():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"answer": "missing ids"})

    client = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    for _ in range(3):
        with pytest.raises(MaxisGatewayError) as caught:
            client.invoke(
                _binding(), api_key="app-key", query="질문",
                user="ltm_0123456789abcdef", inputs=_inputs(),
            )
        assert caught.value.code is MaxisErrorCode.INVALID_RESPONSE
    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(),
        )
    assert caught.value.code is MaxisErrorCode.CIRCUIT_OPEN
    assert calls == 3


def test_response_size_cap_is_enforced_before_json_parse():
    response = _success(answer="x" * 1500)
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=response)
    ))
    client = DifyClient(_config(max_response_bytes=1024), client=http)
    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(limit=1024), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(),
        )
    assert caught.value.code is MaxisErrorCode.RESPONSE_TOO_LARGE


def test_probe_returns_metadata_only():
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=_parameters())
    ))
    probe = DifyClient(_config(), client=http).probe_parameters(api_key="app-key")
    assert probe.ok is True
    assert probe.status_code == 200
    assert probe.parameter_keys == tuple(sorted(REQUIRED_START_VARIABLES))
    assert "file_upload" not in probe.parameter_keys


def test_info_probe_accepts_only_advanced_chat_without_exposing_app_metadata():
    observed = {}

    def handler(request):
        observed["path"] = request.url.path
        return httpx.Response(200, json={
            "mode": "advanced-chat",
            "name": "private-app-name",
            "description": "private-app-description",
        })

    http = httpx.Client(transport=httpx.MockTransport(handler))
    probe = DifyClient(_config(), client=http).probe_info(api_key="app-key")

    assert observed["path"] == "/v1/info"
    assert probe.ok is True
    assert probe.status_code == 200
    assert probe.app_mode_verified is True
    assert probe.error_code == ""
    assert "private-app-name" not in repr(probe)
    assert "private-app-description" not in repr(probe)


@pytest.mark.parametrize("mode", [None, "chat", "ADVANCED-CHAT", "advanced_chat"])
def test_info_probe_rejects_missing_or_nonexact_app_mode(mode):
    body = {} if mode is None else {"mode": mode, "name": "private-name"}
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=body)
    ))

    probe = DifyClient(_config(), client=http).probe_info(api_key="app-key")

    assert probe.ok is False
    assert probe.status_code == 200
    assert probe.app_mode_verified is False
    assert probe.error_code == "unexpected_app_mode"
    assert "private-name" not in repr(probe)


def test_info_probe_rejects_non_object_json():
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=[{"mode": "advanced-chat"}])
    ))

    probe = DifyClient(_config(), client=http).probe_info(api_key="app-key")

    assert probe.ok is False
    assert probe.status_code == 200
    assert probe.app_mode_verified is False
    assert probe.error_code == MaxisErrorCode.INVALID_RESPONSE.value


def test_probe_fails_when_required_start_variable_is_missing():
    present = REQUIRED_START_VARIABLES - {"knowledge_version"}
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=_parameters(present))
    ))
    probe = DifyClient(_config(), client=http).probe_parameters(api_key="app-key")
    assert probe.ok is False
    assert probe.status_code == 200
    assert probe.error_code == "missing_start_variables"
    assert probe.parameter_keys == tuple(sorted(present))


def test_probe_fails_when_an_extra_start_variable_is_exposed():
    present = set(REQUIRED_START_VARIABLES) | {"approval_token"}
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=_parameters(present))
    ))
    probe = DifyClient(_config(), client=http).probe_parameters(api_key="app-key")
    assert probe.ok is False
    assert probe.status_code == 200
    assert probe.error_code == "unexpected_start_variables"
    assert probe.parameter_keys == tuple(sorted(present))


@pytest.mark.parametrize("form", [
    None,
    "not-a-list",
    [{"paragraph": {"variable": "context_pack"}},
     {"text-input": {"variable": "context_pack"}}],
    [{"paragraph": {"variable": "bad-name"}}],
    [{"paragraph": {"variable": "context_pack"},
      "text-input": {"variable": "role_id"}}],
    [{"paragraph": {"label": "missing variable"}}],
])
def test_probe_rejects_malformed_or_duplicate_input_forms(form):
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"user_input_form": form})
    ))
    probe = DifyClient(_config(), client=http).probe_parameters(api_key="app-key")
    assert probe.ok is False
    assert probe.status_code == 200
    assert probe.error_code == MaxisErrorCode.INVALID_RESPONSE.value
    assert probe.parameter_keys == ()


@pytest.mark.parametrize("variable,control,max_length", [
    ("context_pack", "text-input", MAX_CONTEXT_PACK_UTF8_BYTES),
    ("context_pack", "paragraph", MAX_CONTEXT_PACK_UTF8_BYTES - 1),
    ("role_id", "paragraph", 128),
    ("correlation_id", "text-input", 127),
])
def test_probe_rejects_wrong_or_undersized_start_controls(
    variable, control, max_length,
):
    body = _parameters()
    for index, row in enumerate(body["user_input_form"]):
        existing_control, definition = next(iter(row.items()))
        if definition["variable"] == variable:
            body["user_input_form"][index] = {
                control: {**definition, "max_length": max_length}
            }
            break
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=body)
    ))
    probe = DifyClient(_config(), client=http).probe_parameters(api_key="app-key")
    assert probe.ok is False
    assert probe.status_code == 200
    assert probe.error_code == MaxisErrorCode.INVALID_RESPONSE.value


def test_probe_response_size_cap_is_enforced():
    oversized = json.dumps({
        "user_input_form": [],
        "padding": "x" * (257 * 1024),
    }).encode()
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=oversized)
    ))
    probe = DifyClient(_config(), client=http).probe_parameters(api_key="app-key")
    assert probe.ok is False
    assert probe.error_code == MaxisErrorCode.RESPONSE_TOO_LARGE.value


@pytest.mark.parametrize(
    ("mutate", "error_code", "contract_verified"),
    [
        (lambda answer: {**answer, "workflow_version": "wrong-version"},
         "workflow_version_mismatch", True),
        (lambda answer: {
            **answer,
            "claims": [{
                **answer["claims"][0], "kind": "open_fact", "citation_ids": [],
            }],
            "citations": [],
        },
         "remote_payload_claim_citation_required", False),
        (lambda answer: {**answer, "payload": {"state": "not-ready"}},
         "remote_payload_claim_text_mismatch", False),
    ],
)
def test_contract_probe_rejects_version_or_citation_mismatch(
    mutate, error_code, contract_verified,
):
    sentinel = "LTM_RELEASE_SENTINEL_V1=client-test-release"
    def handler(request):
        request_body = json.loads(request.content)
        inputs = request_body["inputs"]
        pack = json.loads(inputs["context_pack"])
        source = pack["sources"][0]
        answer = {
            "contract": "remote-role-result.v1",
            "role_id": inputs["role_id"],
            "status": "ok",
            "payload": {"state": "ready"},
            "claims": [{
                "claim_id": "probe_claim",
                "kind": "fact",
                "text": source["content"],
                "citation_ids": ["probe_citation"],
            }],
            "payload_claims": [{
                "path": "/state",
                "claim_ids": ["probe_claim"],
            }],
            "citations": [{
                "citation_id": "probe_citation",
                "source_id": source["source_id"],
                "locator": "",
            }],
            "missing_inputs": [],
            "warnings": [],
            "workflow_version": inputs["workflow_version"],
            "knowledge_version": inputs["knowledge_version"],
            "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
            "knowledge_proof": sentinel,
        }
        return httpx.Response(200, json=_success(
            json.dumps(mutate(answer)),
            retriever_resources=[{
                "dataset_id": "11111111-1111-4111-8111-111111111111",
                "document_id": "private-probe-document",
                "content": "private probe chunk",
                "score": 0.75,
            }],
        ))

    result = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(handler))
    ).probe_contract(
        _binding(), api_key="app-key", workflow_version=WORKFLOW_ID,
        knowledge_version="kb-1",
        knowledge_manifest_sha256="0" * 64,
        knowledge_sentinel_sha256=hashlib.sha256(
            sentinel.encode("utf-8")
        ).hexdigest(),
        expected_knowledge_dataset_id=(
            "11111111-1111-4111-8111-111111111111"
        ),
    )
    assert result.ok is False
    assert result.contract_verified is contract_verified
    assert result.error_code == error_code


@pytest.mark.parametrize(("workflow_key", "role_id", "knowledge_query"), [
    ("agent_core", "knowledge_curator", "Story Point Epic Link"),
    (
        "editor_author",
        "editor_author",
        "TipTap Editor HTML Jira Description rendering",
    ),
    (
        "pmo_brief",
        "portfolio_analyst",
        "Confluence People Directory Snowflake BI Mart module owner",
    ),
])
def test_contract_probe_targets_governed_terms_and_counts_only_governed_hits(
    workflow_key, role_id, knowledge_query,
):
    sentinel = "LTM_RELEASE_SENTINEL_V1=client-test-release"
    governed_id = "11111111-1111-4111-8111-111111111111"
    sentinel_id = "22222222-2222-4222-8222-222222222222"
    observed = {}

    def handler(request):
        request_body = json.loads(request.content)
        observed["query"] = request_body["query"]
        inputs = request_body["inputs"]
        pack = json.loads(inputs["context_pack"])
        observed["pack_query"] = pack["query"]
        source = pack["sources"][0]
        answer = {
            "contract": "remote-role-result.v1",
            "role_id": inputs["role_id"],
            "status": "ok",
            "payload": {"state": "ready"},
            "claims": [{
                "claim_id": "probe_claim",
                "kind": "fact",
                "text": source["content"],
                "citation_ids": ["probe_citation"],
            }],
            "payload_claims": [{
                "path": "/state", "claim_ids": ["probe_claim"],
            }],
            "citations": [{
                "citation_id": "probe_citation",
                "source_id": source["source_id"],
                "locator": "",
            }],
            "missing_inputs": [],
            "warnings": [],
            "workflow_version": inputs["workflow_version"],
            "knowledge_version": inputs["knowledge_version"],
            "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
            "knowledge_proof": sentinel,
        }
        return httpx.Response(200, json=_success(
            json.dumps(answer),
            retriever_resources=[
                {"dataset_id": governed_id, "score": 0.8},
                {"dataset_id": governed_id, "score": 0.7},
                {"dataset_id": sentinel_id, "score": 1.0},
            ],
        ))

    result = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(handler))
    ).probe_contract(
        _binding(key=workflow_key, roles=(role_id,)),
        api_key="app-key", workflow_version=WORKFLOW_ID,
        knowledge_version="kb-1", knowledge_manifest_sha256="0" * 64,
        knowledge_sentinel_sha256=hashlib.sha256(
            sentinel.encode("utf-8")
        ).hexdigest(),
        expected_knowledge_dataset_id=governed_id,
    )

    assert knowledge_query in observed["query"]
    assert observed["pack_query"] == observed["query"]
    assert result.ok is True
    assert result.knowledge_retrieval_verified is True
    assert result.retrieval_resource_count == 2


def test_invoke_rejects_raw_state_or_serialized_context_without_network():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success())

    client = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    unsafe_inputs = (
        {"state": {"approval_token": "secret"}, "context_pack": _context_pack()},
        {"context_pack": json.dumps(_context_pack())},
        {"context_pack": {**_context_pack(), "messages": ["raw history"]}},
        {"context_pack": {**_context_pack(), "query": "owner@example.com 업무"}},
    )
    for inputs in unsafe_inputs:
        with pytest.raises(MaxisGatewayError) as caught:
            client.invoke(
                _binding(), api_key="app-key", query="질문",
                user="ltm_0123456789abcdef", inputs=inputs,
            )
        assert caught.value.code is MaxisErrorCode.POLICY
    assert calls == 0


def test_invoke_rejects_exact_serialized_context_over_dify_cap_without_network():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success())

    pack = _context_pack()
    pack["query"] = "q" * 1024
    pack["sources"] = []
    for index in range(10):
        content = "\\\"" * 750
        pack["sources"].append({
            "source_id": f"src-{index}",
            "kind": "ltm",
            "canonical_id": f"ltm:test:{index}",
            "title": f"Synthetic source {index}",
            "scope": "",
            "url": "",
            "content": content,
            "retrieved_at": pack["requested_at"],
            "updated_at": None,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "partial": False,
            "truncated": False,
        })
    pack["freshness"] = "fresh"
    inputs = _inputs()
    inputs["context_pack"] = pack
    client = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    parsed = EvidencePackV1.model_validate_json(json.dumps(pack), strict=True)
    assert (
        len(canonical_evidence_pack_json(parsed).encode("utf-8"))
        > MAX_CONTEXT_PACK_UTF8_BYTES
    )
    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(), api_key="app-key", query=pack["query"],
            user="ltm_0123456789abcdef", inputs=inputs,
        )
    assert caught.value.code is MaxisErrorCode.POLICY
    assert calls == 0


def test_invoke_rejects_query_byte_overflow_without_network():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success())

    pack = _context_pack()
    pack["query"] = "가" * 342
    inputs = _inputs()
    inputs["context_pack"] = pack
    client = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(), api_key="app-key", query=pack["query"],
            user="ltm_0123456789abcdef", inputs=inputs,
        )
    assert caught.value.code is MaxisErrorCode.POLICY
    assert calls == 0


@pytest.mark.parametrize("tamper", ["digest", "canonical", "external_ref"])
def test_invoke_rejects_unbound_or_unsafe_payload_schema_without_network(tamper):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success())

    pack = _context_pack()
    if tamper == "digest":
        pack["payload_schema_sha256"] = "f" * 64
    elif tamper == "canonical":
        pack["payload_schema_json"] = json.dumps(_PAYLOAD_SCHEMA, ensure_ascii=False)
    else:
        schema = {
            "type": "object",
            "properties": {"value": {"$ref": "https://example.test/schema.json"}},
        }
        encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
        pack["payload_schema_json"] = encoded
        import hashlib
        pack["payload_schema_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    inputs = _inputs()
    inputs["context_pack"] = pack
    client = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=inputs,
        )
    assert caught.value.code is MaxisErrorCode.POLICY
    assert calls == 0


def test_invoke_requires_complete_start_contract_and_rejects_raw_group_identity():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success())

    client = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    incomplete = _inputs()
    incomplete.pop("knowledge_version")
    with pytest.raises(MaxisGatewayError) as missing:
        client.invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=incomplete,
        )
    assert missing.value.code is MaxisErrorCode.POLICY
    with pytest.raises(MaxisGatewayError) as identity:
        client.invoke(
            _binding(), api_key="app-key", query="skhy.x1234 업무",
            user="ltm_0123456789abcdef", inputs=_inputs(),
        )
    assert identity.value.code is MaxisErrorCode.POLICY
    assert calls == 0


def test_invoke_requires_dify_user_to_match_pack_pseudonym():
    client = DifyClient(
        _config(),
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=_success())
        )),
    )
    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_ffffffffffffffff", inputs=_inputs(),
        )
    assert caught.value.code is MaxisErrorCode.POLICY


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("query", "independent query"),
        ("role_id", "request_architect"),
        ("correlation_id", "different-request"),
        ("output_contract", "another-contract.v1"),
        ("workflow_version", OTHER_WORKFLOW_ID),
    ],
)
def test_invoke_binds_duplicate_dify_controls_to_the_evidence_contract(field, value):
    client = DifyClient(
        _config(),
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail("network must not be called")
        )),
    )
    inputs = _inputs()
    query = _context_pack()["query"]
    if field == "query":
        query = value
    else:
        inputs[field] = value
    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(), api_key="app-key", query=query,
            user="ltm_0123456789abcdef", inputs=inputs, workflow_id=WORKFLOW_ID,
        )
    assert caught.value.code is MaxisErrorCode.POLICY


def test_prod_requires_verified_https_and_base_is_canonical():
    with pytest.raises(MaxisGatewayError) as http_error:
        DifyClientConfig(
            profile="prod", stage="prod",
            base_url="http://dify.maxis.skax.co.kr/v1",
            require_https=True,
        )
    assert http_error.value.code is MaxisErrorCode.POLICY
    with pytest.raises(MaxisGatewayError):
        DifyClientConfig(
            profile="prod", stage="prod",
            base_url="https://dify.maxis.skax.co.kr/v1",
            verify_tls=False,
        )
    assert canonical_api_base(
        "HTTPS://DIFY.MAXIS.SKAX.CO.KR/platform/v1/",
        stage="prod", require_https=True,
    ) == "https://dify.maxis.skax.co.kr/platform/v1"


@pytest.mark.parametrize("url", [
    "https://evil.example/v1",
    "https://@dify.maxis.skax.co.kr/v1",
    "https://169.254.169.254/v1",
    "https://127.1/v1",
    "https://127.0.0.1/v1",
    "https://[::1]/v1",
    "https://dify.maxis.skax.co.kr:8443/v1",
])
def test_prod_rejects_non_allowlisted_and_ip_literal_destinations(url):
    with pytest.raises(MaxisGatewayError) as caught:
        DifyClientConfig(profile="prod", stage="prod", base_url=url)
    assert caught.value.code in {MaxisErrorCode.CONFIGURATION, MaxisErrorCode.POLICY}


def test_prod_environment_allowlist_replaces_default(monkeypatch):
    monkeypatch.setenv(
        "LAKE_MAXIS_PROD_ALLOWED_HOSTS",
        "prod.maxis.example, backup.maxis.example",
    )
    assert DifyClientConfig(
        profile="prod", stage="prod", base_url="https://prod.maxis.example/v1",
    ).base_url == "https://prod.maxis.example/v1"
    with pytest.raises(MaxisGatewayError) as caught:
        DifyClientConfig(
            profile="prod", stage="prod",
            base_url="https://dify.maxis.skax.co.kr/v1",
        )
    assert caught.value.code is MaxisErrorCode.POLICY


def test_remote_explicit_443_is_canonical_and_dev_stub_retains_its_port():
    assert DifyClientConfig(
        profile="prod", stage="prod",
        base_url="https://dify.maxis.skax.co.kr:443/v1",
    ).base_url == "https://dify.maxis.skax.co.kr/v1"
    assert DifyClientConfig(
        profile="dev", stage="dev",
        base_url="http://127.0.0.1:19001/v1", require_https=False,
    ).base_url == "http://127.0.0.1:19001/v1"


@pytest.mark.parametrize("value", [
    "*.maxis.example",
    "maxis.example,",
    "localhost",
    "127.0.0.1",
    "맥시스.example",
])
def test_invalid_prod_allowlist_environment_fails_closed(monkeypatch, value):
    monkeypatch.setenv("LAKE_MAXIS_PROD_ALLOWED_HOSTS", value)
    with pytest.raises(MaxisGatewayError):
        DifyClientConfig(
            profile="prod", stage="prod",
            base_url="https://dify.maxis.skax.co.kr/v1",
        )


def test_invalid_dev_allowlist_fails_closed_even_for_loopback(monkeypatch):
    monkeypatch.setenv("LAKE_MAXIS_DEV_ALLOWED_HOSTS", "*.maxis.example")

    with pytest.raises(MaxisGatewayError) as caught:
        DifyClientConfig(
            profile="dev", stage="dev",
            base_url="http://127.0.0.1:19001/v1", require_https=False,
        )

    assert caught.value.code is MaxisErrorCode.CONFIGURATION


def test_http_boundary_rechecks_environment_allowlist(monkeypatch):
    monkeypatch.setenv("LAKE_MAXIS_PROD_ALLOWED_HOSTS", "prod.maxis.example")
    config = DifyClientConfig(
        profile="prod", stage="prod", base_url="https://prod.maxis.example/v1",
    )
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_parameters())

    client = DifyClient(
        config, client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setenv("LAKE_MAXIS_PROD_ALLOWED_HOSTS", "other.maxis.example")

    probe = client.probe_parameters(api_key="app-key")

    assert probe.ok is False
    assert probe.error_code == MaxisErrorCode.POLICY.value
    assert calls == 0


def test_prod_invoke_requires_published_workflow_pin():
    config = DifyClientConfig(
        profile="prod",
        stage="prod",
        base_url="https://dify.maxis.skax.co.kr/v1",
    )
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success())

    client = DifyClient(
        config, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(),
        )
    assert caught.value.code is MaxisErrorCode.POLICY
    assert calls == 0


def test_workflow_pin_rejects_control_characters_before_network():
    client = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail("network must not be called")
        ))
    )
    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=_inputs(),
            workflow_id="wf-1\nforged",
        )
    assert caught.value.code is MaxisErrorCode.CONFIGURATION


def test_non_uuid_workflow_version_is_rejected_before_network():
    client = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail("network must not be called")
        ))
    )
    inputs = _inputs()
    inputs["workflow_version"] = "1.0.0"

    with pytest.raises(MaxisGatewayError) as caught:
        client.invoke(
            _binding(), api_key="app-key", query="질문",
            user="ltm_0123456789abcdef", inputs=inputs,
            workflow_id=WORKFLOW_ID,
        )

    assert caught.value.code is MaxisErrorCode.CONFIGURATION
