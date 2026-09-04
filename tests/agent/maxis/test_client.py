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
from app.agent.maxis.contracts import canonical_payload_schema


_PAYLOAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"summary": {"type": "string", "minLength": 1}},
    "required": ["summary"],
}


def _binding(limit=1024 * 1024):
    return WorkflowBinding(
        key="agent_core",
        app_type="chatflow",
        endpoint="/chat-messages",
        response_mode="blocking",
        conversation_mode="stateless",
        api_key_env="LAKE_MAXIS_AGENT_CORE_API_KEY",
        output_contract="remote-role-result.v1",
        max_response_bytes=limit,
        roles=("work_architect",),
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


def _success(answer="{}"):
    return {
        "event": "message",
        "task_id": "task-1",
        "message_id": "message-1",
        "conversation_id": "conversation-created-by-server",
        "answer": answer,
        "metadata": {"usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }},
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
        "workflow_version": "wf-1",
        "knowledge_version": "kb-1",
    }


def _parameters(keys=REQUIRED_START_VARIABLES):
    return {
        "user_input_form": [
            {("paragraph" if key == "context_pack" else "text-input"): {
                "label": key,
                "variable": key,
                "required": True,
                "max_length": 20000 if key == "context_pack" else 128,
            }}
            for key in sorted(keys)
        ],
        "file_upload": {"enabled": False},
    }


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
        workflow_id="wf-1",
    )
    assert observed["url"] == "http://dify.test/v1/chat-messages"
    assert observed["auth"] == "Bearer app-test-key"
    assert observed["body"]["response_mode"] == "blocking"
    assert observed["body"]["workflow_id"] == "wf-1"
    assert "conversation_id" not in observed["body"]
    serialized_pack = observed["body"]["inputs"]["context_pack"]
    assert isinstance(serialized_pack, str)
    assert json.loads(serialized_pack)["contract"] == "evidence-pack.v1"
    assert result.message_id == "message-1"
    assert result.usage.total_tokens == 15


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
    ("context_pack", "text-input", 20_000),
    ("context_pack", "paragraph", 19_999),
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
    ("mutate", "error_code"),
    [
        (lambda answer: {**answer, "workflow_version": "wrong-version"},
         "workflow_version_mismatch"),
        (lambda answer: {
            **answer,
            "claims": [{
                **answer["claims"][0], "kind": "inference", "citation_ids": [],
            }],
            "citations": [],
        },
         "citation_contract_failed"),
        (lambda answer: {**answer, "payload": {"state": "not-ready"}},
         "remote_payload_contract_validation_failed"),
    ],
)
def test_contract_probe_rejects_version_or_citation_mismatch(mutate, error_code):
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
                "locator": "synthetic source",
            }],
            "missing_inputs": [],
            "warnings": [],
            "workflow_version": inputs["workflow_version"],
            "knowledge_version": inputs["knowledge_version"],
            "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
            "knowledge_proof": sentinel,
        }
        return httpx.Response(200, json=_success(json.dumps(mutate(answer))))

    result = DifyClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(handler))
    ).probe_contract(
        _binding(), api_key="app-key", workflow_version="wf-1",
        knowledge_version="kb-1",
        knowledge_manifest_sha256="0" * 64,
        knowledge_sentinel_sha256=hashlib.sha256(
            sentinel.encode("utf-8")
        ).hexdigest(),
    )
    assert result.ok is False
    assert result.contract_verified is (
        error_code != "remote_payload_contract_validation_failed"
    )
    assert result.error_code == error_code


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
        ("workflow_version", "another-workflow"),
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
            user="ltm_0123456789abcdef", inputs=inputs, workflow_id="wf-1",
        )
    assert caught.value.code is MaxisErrorCode.POLICY


def test_prod_requires_verified_https_and_base_is_canonical():
    with pytest.raises(MaxisGatewayError) as http_error:
        DifyClientConfig(
            profile="prod", stage="prod", base_url="http://maxis.example/v1",
            require_https=True,
        )
    assert http_error.value.code is MaxisErrorCode.POLICY
    with pytest.raises(MaxisGatewayError):
        DifyClientConfig(
            profile="prod", stage="prod", base_url="https://maxis.example/v1",
            verify_tls=False,
        )
    assert canonical_api_base(
        "HTTPS://MAXIS.EXAMPLE/platform/v1/", require_https=True
    ) == "https://maxis.example/platform/v1"


def test_prod_invoke_requires_published_workflow_pin():
    config = DifyClientConfig(
        profile="prod",
        stage="prod",
        base_url="https://maxis.example/v1",
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
