from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from app.agent.maxis import context_guard as local_context_guard
from app.agent.maxis import EvidencePackV1, ResponseGuard
from app.agent.maxis.context_guard import forbidden_authority_key


ROOT = Path(__file__).resolve().parents[3]
GUARD_PATH = ROOT / "deploy" / "maxis-dify" / "code" / "contract_guard.py"
SMOKE_PATH = ROOT / "deploy" / "maxis-dify" / "smoke-cases.yml"


def _load_guard():
    spec = importlib.util.spec_from_file_location("ltm_dify_contract_guard", GUARD_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = _load_guard()


def _cases() -> list[dict]:
    return yaml.safe_load(SMOKE_PATH.read_text(encoding="utf-8"))["cases"]


def _invoke(
    case: dict,
    candidate: object | None = None,
    sentinel: object | None = None,
    executed_workflow_id: object | None = None,
):
    inputs = case["inputs"]
    result = GUARD.main(
        candidate=json.dumps(
            case["expected_response"] if candidate is None else candidate,
            ensure_ascii=False,
            separators=(",", ":"),
        ) if not isinstance(candidate, str) else candidate,
        context_pack=json.dumps(
            inputs["context_pack"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        role_id=inputs["role_id"],
        correlation_id=inputs["correlation_id"],
        output_contract=inputs["output_contract"],
        workflow_version=inputs["workflow_version"],
        executed_workflow_id=(
            inputs["workflow_version"]
            if executed_workflow_id is None
            else executed_workflow_id
        ),
        knowledge_version=inputs["knowledge_version"],
        sentinel_result=(
            [{"content": "release metadata\nLTM_RELEASE_SENTINEL_V1=smoke-bundle-v1\n"}]
            if sentinel is None else sentinel
        ),
    )
    assert set(result) == {"result"}
    return json.loads(result["result"])


def _source_free_case(schema: dict | None = None, payload: dict | None = None) -> dict:
    case = copy.deepcopy(_cases()[0])
    schema = schema or {
        "type": "object",
        "additionalProperties": False,
        "properties": {"status": {"const": "ready"}},
        "required": ["status"],
    }
    encoded = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    pack = case["inputs"]["context_pack"]
    pack.update(
        sources=[],
        facts=[],
        metrics=[],
        payload_schema_json=encoded,
        payload_schema_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )
    case["expected_response"].update(
        payload=payload or {"status": "ready"},
        claims=[],
        payload_claims=[],
        citations=[],
    )
    return case


@pytest.mark.parametrize("case", _cases(), ids=lambda row: row["id"])
def test_guard_accepts_bundle_smoke_responses(case):
    guarded = _invoke(case)

    assert guarded == case["expected_response"]


@pytest.mark.parametrize("fixed", ({"const": "ready"}, {"enum": ["ready"]}))
def test_guard_accepts_source_free_control_only_payload(fixed):
    case = _source_free_case({
        "type": "object",
        "additionalProperties": False,
        "properties": {"status": fixed},
        "required": ["status"],
    })

    assert _invoke(case) == case["expected_response"]


@pytest.mark.parametrize(
    ("schema", "payload"),
    (
        ({"properties": {"status": {"const": "ready"}}, "required": []},
         {"status": "ready"}),
        ({"properties": {"status": {"type": "string"}}, "required": ["status"]},
         {"status": "ready"}),
        ({"properties": {"status": {"default": "ready", "type": "string"}},
          "required": ["status"]}, {"status": "ready"}),
        ({"properties": {"status": {"enum": ["ready", "pending"]}},
          "required": ["status"]}, {"status": "ready"}),
        ({"properties": {"summary": {"const": "ready"}}, "required": ["summary"]},
         {"summary": "ready"}),
        ({"properties": {"status": {"const": "ready"}}, "required": [{}]},
         {"status": "ready"}),
    ),
    ids=(
        "optional", "free-text", "default", "multi-enum", "narrative",
        "malformed-required",
    ),
)
def test_guard_blocks_open_source_free_payloads(schema, payload):
    schema = {
        "type": "object", "additionalProperties": False, **schema,
    }
    guarded = _invoke(_source_free_case(schema, payload))

    assert guarded["status"] == "blocked"
    assert guarded["payload"] == {}
    assert guarded["warnings"] == ["contract_guard:source_free_payload_forbidden"]


@pytest.mark.parametrize(
    "provenance",
    ("open_fact", "citation", "payload_claim"),
)
def test_guard_blocks_all_source_free_provenance(provenance):
    case = _source_free_case()
    candidate = case["expected_response"]
    if provenance in {"open_fact", "payload_claim"}:
        candidate["claims"] = [{
            "claim_id": "claim-open",
            "kind": "open_fact",
            "text": "model-authored unverified fact",
            "citation_ids": [],
        }]
    if provenance == "citation":
        candidate["citations"] = [{
            "citation_id": "cite-ghost",
            "source_id": "source-ghost",
            "locator": "",
        }]
    if provenance == "payload_claim":
        candidate["payload_claims"] = [{
            "path": "/status",
            "claim_ids": ["claim-open"],
        }]

    guarded = _invoke(case, candidate)

    assert guarded["status"] == "blocked"
    assert guarded["payload"] == {}
    assert guarded["claims"] == []
    assert guarded["payload_claims"] == []
    assert guarded["citations"] == []
    assert guarded["warnings"] == [
        "contract_guard:source_free_provenance_forbidden"
    ]


def test_guard_blocks_intent_source_as_fact():
    case = copy.deepcopy(_cases()[0])
    source = case["inputs"]["context_pack"]["sources"][0]
    source.update(
        kind="ltm",
        canonical_id="ltm:user-intent:0123456789abcdef",
        title="LTM guarded user intent",
    )

    guarded = _invoke(case)

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == [
        "contract_guard:intent_source_fact_forbidden"
    ]


def test_guard_blocks_uncited_open_fact_payload_binding():
    case = copy.deepcopy(_cases()[0])
    candidate = case["expected_response"]
    candidate["claims"][0].update(kind="open_fact", citation_ids=[])
    candidate["citations"] = []

    guarded = _invoke(case, candidate)

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == [
        "contract_guard:payload_claim_citation_required"
    ]


def test_guard_keeps_unbound_open_fact_sidecar():
    case = copy.deepcopy(_cases()[0])
    candidate = case["expected_response"]
    candidate["claims"].append({
        "claim_id": "open_gap",
        "kind": "open_fact",
        "text": "An owner still needs confirmation.",
        "citation_ids": [],
    })

    assert _invoke(case, candidate) == candidate


def test_guard_blocks_model_authored_citation_locator():
    case = copy.deepcopy(_cases()[0])
    case["expected_response"]["citations"][0]["locator"] = "content"

    guarded = _invoke(case)

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == [
        "contract_guard:citation_locator_forbidden"
    ]


def test_guard_blocks_warnings_beside_ok_result():
    case = copy.deepcopy(_cases()[0])
    case["expected_response"]["warnings"] = ["model-authored caveat"]

    guarded = _invoke(case)

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == ["contract_guard:ok_has_warnings"]


@pytest.mark.parametrize("provenance", ("claim", "citation", "payload_claim"))
def test_guard_blocks_provenance_beside_non_ok_result(provenance):
    case = copy.deepcopy(_cases()[0])
    candidate = case["expected_response"]
    candidate.update(status="needs_input", payload={}, missing_inputs=["role_knowledge"])
    if provenance == "citation":
        candidate["claims"] = []
        candidate["payload_claims"] = []
    elif provenance == "claim":
        candidate["payload_claims"] = []
        candidate["citations"] = []
        candidate["claims"][0].update(kind="open_fact", citation_ids=[])
    else:
        candidate["citations"] = []
        candidate["claims"][0].update(kind="open_fact", citation_ids=[])

    guarded = _invoke(case, candidate)

    assert guarded["status"] == "blocked"
    assert guarded["payload"] == {}
    assert guarded["claims"] == []
    assert guarded["payload_claims"] == []
    assert guarded["citations"] == []
    assert guarded["warnings"] == [
        "contract_guard:non_ok_provenance_not_empty"
    ]


def test_guard_accepts_empty_provenance_beside_non_ok_result():
    case = copy.deepcopy(_cases()[0])
    candidate = case["expected_response"]
    candidate.update(
        status="needs_input",
        payload={},
        claims=[],
        payload_claims=[],
        citations=[],
        missing_inputs=["role_knowledge"],
        warnings=["knowledge_guard:no_role_knowledge"],
    )

    assert _invoke(case, candidate) == candidate


def test_guard_blocks_empty_knowledge_proof_without_copying_candidate_text():
    case = _cases()[0]
    candidate = copy.deepcopy(case["expected_response"])
    candidate["knowledge_proof"] = ""
    candidate["payload"]["our_context"] = "UNTRUSTED_PRIVATE_MODEL_TEXT"

    guarded = _invoke(case, candidate)

    assert guarded["status"] == "blocked"
    assert guarded["payload"] == {}
    assert guarded["claims"] == []
    assert guarded["citations"] == []
    assert guarded["payload_claims"] == []
    assert guarded["warnings"] == ["contract_guard:response_echo_mismatch"]
    assert "UNTRUSTED_PRIVATE_MODEL_TEXT" not in json.dumps(guarded)


def test_guard_blocks_unknown_context_source_citation():
    case = _cases()[0]
    candidate = copy.deepcopy(case["expected_response"])
    candidate["citations"][0]["source_id"] = "maxis_release_sentinel"

    guarded = _invoke(case, candidate)

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == ["contract_guard:citation_source_unknown"]


def test_guard_grounds_a_fact_from_its_linked_source_ids_only():
    case = copy.deepcopy(_cases()[0])
    pack = case["inputs"]["context_pack"]
    pack["sources"][0]["content"] = "Base record without the asserted state."
    fact_text = "Release readiness is confirmed."
    pack["facts"] = [{
        "fact_id": "fact_release_ready",
        "text": fact_text,
        "source_ids": ["smoke_source_core"],
        "observed_at": "2026-09-04T00:00:00Z",
    }]
    candidate = case["expected_response"]
    candidate["payload"] = {
        "concepts": [],
        "our_context": fact_text,
        "references": [],
        "gaps": [],
    }
    candidate["claims"][0]["text"] = fact_text
    candidate["payload_claims"] = [{
        "path": "/our_context",
        "claim_ids": ["claim_core_ready"],
    }]
    assert _invoke(case) == candidate


def test_guard_grounds_a_metric_label_value_and_unit_from_its_source_ids_only():
    case = copy.deepcopy(_cases()[1])
    pack = case["inputs"]["context_pack"]
    source_content = "Base record without any progress figure."
    pack["sources"][0]["content"] = source_content
    pack["sources"][0]["content_sha256"] = hashlib.sha256(
        source_content.encode("utf-8")
    ).hexdigest()
    pack["metrics"] = [{
        "metric_id": "metric_progress",
        "label": "Story Point progress",
        "value": 50,
        "unit": "percent",
        "formula_version": "epic-sp.v1",
        "source_ids": ["smoke_source_pmo"],
    }]
    metric_text = "Story Point progress 50 percent"
    candidate = case["expected_response"]
    candidate["payload"] = {
        "headline": metric_text,
        "findings": [],
        "caution": "",
    }
    candidate["claims"][0]["text"] = metric_text
    candidate["payload_claims"] = [{
        "path": "/headline",
        "claim_ids": ["claim_pmo_progress"],
    }]
    guarded = _invoke(case)
    assert guarded == candidate

    evidence = EvidencePackV1.model_validate_json(
        json.dumps(pack, ensure_ascii=False), strict=True,
    )
    local, payload = ResponseGuard().validate_remote_role(
        json.dumps(candidate, ensure_ascii=False),
        expected_role_id=case["inputs"]["role_id"],
        payload_model=evidence.payload_schema(),
        evidence=evidence,
        expected_knowledge_manifest_sha256=evidence.knowledge_manifest_sha256,
    )
    assert local.model_dump(mode="json") == guarded
    assert payload == candidate["payload"]


@pytest.mark.parametrize("evidence_field", ["facts", "metrics"])
def test_guard_blocks_fact_or_metric_linked_to_an_unknown_source(evidence_field):
    case = copy.deepcopy(_cases()[0])
    if evidence_field == "facts":
        case["inputs"]["context_pack"]["facts"] = [{
            "fact_id": "fact_unknown_source",
            "text": "Unknown source fact",
            "source_ids": ["missing_source"],
            "observed_at": "2026-09-04T00:00:00Z",
        }]
    else:
        case["inputs"]["context_pack"]["metrics"] = [{
            "metric_id": "metric_unknown_source",
            "label": "Unknown source metric",
            "value": 1,
            "unit": "item",
            "formula_version": "test.v1",
            "source_ids": ["missing_source"],
        }]

    guarded = _invoke(case)

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == [
        f"contract_guard:{evidence_field[:-1]}_source_unknown"
    ]


def test_guard_blocks_empty_payload_claim_ids():
    case = _cases()[0]
    candidate = copy.deepcopy(case["expected_response"])
    candidate["payload_claims"][-1]["claim_ids"] = []

    guarded = _invoke(case, candidate)

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == ["contract_guard:payload_claim_ids_invalid"]


def test_guard_blocks_missing_narrative_binding():
    case = _cases()[0]
    candidate = copy.deepcopy(case["expected_response"])
    candidate["payload_claims"] = [
        row for row in candidate["payload_claims"] if row["path"] != "/our_context"
    ]

    guarded = _invoke(case, candidate)

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == ["contract_guard:payload_claim_coverage_missing"]


def test_guard_blocks_sentinel_document_used_as_payload():
    case = _cases()[0]
    candidate = copy.deepcopy(case["expected_response"])
    candidate["payload"]["our_context"] = (
        "LTM_RELEASE_SENTINEL_V1=smoke-bundle-v1"
    )

    guarded = _invoke(case, candidate)

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == ["contract_guard:knowledge_proof_leaked"]


def test_guard_blocks_payload_schema_violation():
    case = _cases()[1]
    candidate = copy.deepcopy(case["expected_response"])
    del candidate["payload"]["headline"]
    candidate["payload_claims"] = []

    guarded = _invoke(case, candidate)

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == ["contract_guard:payload_required_missing"]


def test_guard_blocks_extra_top_level_fields_including_correlation_id():
    case = _cases()[0]
    candidate = copy.deepcopy(case["expected_response"])
    candidate["correlation_id"] = case["inputs"]["correlation_id"]

    guarded = _invoke(case, candidate)

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == ["contract_guard:response_fields_mismatch"]


@pytest.mark.parametrize(
    "key",
    (
        "approvalToken",
        "approval-token",
        "authToken",
        "AUTH_TOKEN",
        "cookie",
        "session",
        "sessionId",
        "private_key",
        "privateKey",
        "client_secret",
        "clientSecret",
        "accessToken",
        "refresh-token",
        "apiKey",
        "db_password",
        "commentToken",
        "executionReceipt",
        "mutation-receipt",
        "questionReceiptProjection",
    ),
)
def test_guard_authority_key_detection_matches_local_guard(key):
    assert forbidden_authority_key(key) is True
    assert GUARD._forbidden_authority_key(key) is True


@pytest.mark.parametrize("key", sorted(local_context_guard._FORBIDDEN_CONTEXT_KEYS))
def test_guard_covers_every_canonical_local_authority_key(key):
    assert GUARD._forbidden_authority_key(key) is True


@pytest.mark.parametrize(
    "key",
    ("approved", "approval", "execute", "authorized", "executionToken"),
)
def test_guard_preserves_stricter_remote_capability_detection(key):
    assert GUARD._forbidden_authority_key(key) is True


@pytest.mark.parametrize(
    "key",
    ("tokenization", "meeting_summary", "private_notes", "client_name"),
)
def test_guard_authority_key_detection_matches_local_safe_lookalikes(key):
    assert forbidden_authority_key(key) is False
    assert GUARD._forbidden_authority_key(key) is False


@pytest.mark.parametrize(
    "key",
    ("approvalToken", "authToken", "cookie", "sessionId", "privateKey", "clientSecret"),
)
def test_guard_blocks_normalized_authority_fields_before_payload_schema(key):
    case = _cases()[0]
    candidate = copy.deepcopy(case["expected_response"])
    candidate["payload"][key] = "must-not-cross-the-guard"

    guarded = _invoke(case, candidate)

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == ["contract_guard:forbidden_authority_field"]
    assert "must-not-cross-the-guard" not in json.dumps(guarded)


@pytest.mark.parametrize("candidate", ["not json", "[]", '{"x":NaN}', '{"x":1,"x":2}'])
def test_guard_converts_malformed_model_output_to_blocked(candidate):
    guarded = _invoke(_cases()[0], candidate)

    assert guarded["status"] == "blocked"
    assert guarded["payload"] == {}


def test_guard_blocks_when_release_sentinel_retrieval_is_absent():
    case = _cases()[0]
    guarded = _invoke(case, sentinel=[])

    assert guarded["status"] == "blocked"
    assert guarded["knowledge_proof"] == ""
    assert guarded["warnings"] == ["contract_guard:knowledge_proof_unavailable"]

    pack = EvidencePackV1.model_validate_json(
        json.dumps(case["inputs"]["context_pack"], ensure_ascii=False),
        strict=True,
    )
    remote, payload = ResponseGuard().validate_remote_role(
        json.dumps(guarded, ensure_ascii=False),
        expected_role_id=case["inputs"]["role_id"],
        payload_model=pack.payload_schema(),
        evidence=pack,
        expected_knowledge_manifest_sha256=pack.knowledge_manifest_sha256,
    )
    assert remote.status == "blocked"
    assert payload is None


def test_guard_blocks_when_executed_workflow_differs_from_requested_version():
    case = _cases()[0]

    guarded = _invoke(
        case,
        executed_workflow_id="99999999-9999-4999-8999-999999999999",
    )

    assert guarded["status"] == "blocked"
    assert guarded["payload"] == {}
    assert guarded["knowledge_proof"] == ""
    assert guarded["warnings"] == [
        "contract_guard:workflow_execution_mismatch"
    ]


@pytest.mark.parametrize(
    "executed_workflow_id",
    ("", "not-a-uuid", "11111111-1111-4111-8111-11111111111A"),
)
def test_guard_blocks_invalid_executed_workflow_id(executed_workflow_id):
    guarded = _invoke(
        _cases()[0], executed_workflow_id=executed_workflow_id,
    )

    assert guarded["status"] == "blocked"
    assert guarded["warnings"] == ["contract_guard:workflow_id_invalid"]


def test_guard_source_uses_only_dify_standard_library_modules():
    source = GUARD_PATH.read_text(encoding="utf-8")

    assert "import hashlib" in source
    assert "import html" in source
    assert "import json" in source
    assert "import math" in source
    assert "import re" in source
    assert "requests" not in source
    assert "open(" not in source
