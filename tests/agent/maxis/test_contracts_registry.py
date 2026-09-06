from __future__ import annotations

from dataclasses import FrozenInstanceError
import json

import pytest
from pydantic import ValidationError

from app.agent.maxis import (
    CompactQueryPlanV1,
    QueryIntentV1,
    RemoteRoleResultV1,
    WorkflowRegistryError,
    load_registry,
    reset_registry_cache,
)
from app.agent.maxis.contracts import (
    MAX_PAYLOAD_SCHEMA_UTF8_BYTES,
    canonical_payload_schema,
)


def test_strict_contract_rejects_coercion_and_extra_fields():
    with pytest.raises(ValidationError):
        QueryIntentV1(source="jira", exhaustive="false")
    with pytest.raises(ValidationError):
        QueryIntentV1(source="jira", unexpected=True)


def test_contract_is_frozen_and_uses_versioned_name():
    plan = CompactQueryPlanV1(reads=(QueryIntentV1(source="jira"),))
    assert plan.contract == "compact-query-plan.v1"
    with pytest.raises(ValidationError):
        plan.contract = "other"


def test_model_payload_schema_drops_annotations_but_preserves_property_names():
    encoded, digest = canonical_payload_schema({
        "type": "object",
        "title": "human-only root title",
        "description": "human-only description",
        "additionalProperties": False,
        "properties": {
            "title": {
                "type": "string",
                "title": "human-only field title",
                "description": "human-only field description",
            },
            "default": {"type": "integer", "default": 7},
        },
        "required": ["title", "default"],
    })
    projected = json.loads(encoded)

    assert set(projected["properties"]) == {"title", "default"}
    assert projected["properties"]["title"] == {"type": "string"}
    assert projected["properties"]["default"] == {"type": "integer"}
    assert "title" not in projected
    assert "description" not in projected
    assert len(encoded.encode("utf-8")) <= MAX_PAYLOAD_SCHEMA_UTF8_BYTES
    assert len(digest) == 64


def test_remote_result_rejects_uncited_evidence_claim_and_unknown_citation():
    base = {
        "role_id": "auditor",
        "status": "ok",
        "payload": {},
        "workflow_version": "wf-1",
        "knowledge_version": "kb-1",
        "knowledge_manifest_sha256": "a" * 64,
        "knowledge_proof": "LTM_RELEASE_SENTINEL_V1=test",
    }
    with pytest.raises(ValidationError):
        RemoteRoleResultV1(**base, claims=({
            "claim_id": "claim-1", "kind": "fact", "text": "확인됨",
        },))
    with pytest.raises(ValidationError):
        RemoteRoleResultV1(**base, claims=({
            "claim_id": "claim-1", "kind": "inference", "text": "추론됨",
        },))
    with pytest.raises(ValidationError):
        RemoteRoleResultV1(**base, claims=({
            "claim_id": "claim-1",
            "kind": "fact",
            "text": "확인됨",
            "citation_ids": ("citation-2",),
        },))


def test_remote_result_rejects_input_only_correlation_id_echo():
    with pytest.raises(ValidationError):
        RemoteRoleResultV1(
            role_id="auditor",
            status="ok",
            payload={},
            workflow_version="wf-1",
            knowledge_version="kb-1",
            knowledge_manifest_sha256="a" * 64,
            knowledge_proof="LTM_RELEASE_SENTINEL_V1=test",
            correlation_id="request-input-only",
        )


def test_remote_result_requires_knowledge_proof_field():
    values = {
        "role_id": "auditor",
        "status": "ok",
        "payload": {},
        "workflow_version": "wf-1",
        "knowledge_version": "kb-1",
        "knowledge_manifest_sha256": "a" * 64,
    }

    with pytest.raises(ValidationError):
        RemoteRoleResultV1(**values)


@pytest.mark.parametrize("provenance", ("claims", "payload_claims", "citations"))
def test_non_ok_remote_result_rejects_provenance_sidecars(provenance):
    values = {
        "role_id": "auditor",
        "status": "needs_input",
        "payload": {},
        "claims": (),
        "payload_claims": (),
        "citations": (),
        "missing_inputs": ("role_knowledge",),
        "workflow_version": "wf-1",
        "knowledge_version": "kb-1",
        "knowledge_manifest_sha256": "a" * 64,
        "knowledge_proof": "LTM_RELEASE_SENTINEL_V1=test",
    }
    if provenance == "claims":
        values[provenance] = ({
            "claim_id": "claim-1",
            "kind": "open_fact",
            "text": "unverified model text",
            "citation_ids": (),
        },)
    elif provenance == "payload_claims":
        values[provenance] = ({
            "path": "/status",
            "claim_ids": ("claim-1",),
        },)
        values["claims"] = ({
            "claim_id": "claim-1",
            "kind": "open_fact",
            "text": "unverified model text",
            "citation_ids": (),
        },)
    else:
        values[provenance] = ({
            "citation_id": "citation-1",
            "source_id": "source-1",
            "locator": "",
        },)

    with pytest.raises(ValidationError, match="provenance empty"):
        RemoteRoleResultV1(**values)


def test_default_registry_is_immutable_and_resolves_roles():
    reset_registry_cache()
    registry = load_registry()
    binding = registry.resolve("agent_core")
    assert len(registry.digest) == 64
    assert registry.resolve_role("work_architect") is binding
    assert binding.endpoint == "/chat-messages"
    assert binding.response_mode == "blocking"
    assert binding.conversation_mode == "stateless"
    with pytest.raises(TypeError):
        registry.workflows["new"] = binding
    with pytest.raises(FrozenInstanceError):
        binding.key = "other"


def test_default_registry_excludes_local_only_semantic_roles():
    reset_registry_cache()
    registry = load_registry()

    with pytest.raises(WorkflowRegistryError, match="not bound for role"):
        registry.resolve_role("research_analyst")
    with pytest.raises(WorkflowRegistryError, match="not bound for role"):
        registry.resolve_role("people_advisor")


def test_registry_rejects_duplicate_role_binding(tmp_path):
    path = tmp_path / "maxis-workflows.yml"
    path.write_text(
        """version: 1
workflows:
  first:
    app_type: chatflow
    endpoint: /chat-messages
    response_mode: blocking
    conversation_mode: stateless
    api_key_env: LAKE_MAXIS_FIRST_API_KEY
    output_contract: remote-role-result.v1
    max_response_bytes: 1024
    roles: [auditor]
  second:
    app_type: chatflow
    endpoint: /chat-messages
    response_mode: blocking
    conversation_mode: stateless
    api_key_env: LAKE_MAXIS_SECOND_API_KEY
    output_contract: remote-role-result.v1
    max_response_bytes: 1024
    roles: [auditor]
""",
        encoding="utf-8",
    )
    with pytest.raises(WorkflowRegistryError, match="multiple"):
        load_registry(path)


def test_registry_rejects_unknown_keys_without_echoing_values(tmp_path):
    path = tmp_path / "maxis-workflows.yml"
    path.write_text(
        """version: 1
workflows:
  core:
    app_type: chatflow
    endpoint: /chat-messages
    response_mode: blocking
    conversation_mode: stateless
    api_key_env: LAKE_MAXIS_CORE_API_KEY
    output_contract: remote-role-result.v1
    max_response_bytes: 1024
    roles: [auditor]
    secret: must-not-appear
""",
        encoding="utf-8",
    )
    with pytest.raises(WorkflowRegistryError) as caught:
        load_registry(path)
    assert "must-not-appear" not in str(caught.value)
