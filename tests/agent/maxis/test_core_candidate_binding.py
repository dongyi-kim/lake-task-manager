"""Transport metadata is code-owned; semantic failures are never repaired."""
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "candidate_binding",
    Path(__file__).resolve().parents[3] / "deploy/maxis-dify/code/core_candidate_binding.py",
)
BINDING = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BINDING)


def request():
    return {
        "contract": "ltm-model-input.v1", "role_id": "knowledge_curator",
        "correlation_id": "probe", "output_contract": "remote-role-result.v1",
        "workflow_version": "758e1de2-201f-4620-9306-9b9aa76dd3de",
        "knowledge_version": "kb", "knowledge_proof": "sentinel",
        "context_pack": {"request_id": "probe", "use_case": "knowledge_curator",
                         "knowledge_manifest_sha256": "a" * 64},
    }


@pytest.mark.parametrize("status", ["ok", "needs_input", "blocked"])
def test_binding_changes_only_request_owned_transport(status):
    semantic = {"status": status, "payload": {"state": "ready"}, "claims": [],
                "citations": [], "payload_claims": [], "missing_inputs": ["required input"],
                "warnings": ["preserve this failure"]}
    candidate = {**dict.fromkeys(BINDING._FIELDS, "misspelled"), **semantic}
    original = json.dumps(candidate)
    result = json.loads(BINDING.main(json.dumps(request()), original)["candidate"])
    assert {key: result[key] for key in semantic} == semantic
    assert result["workflow_version"] == request()["workflow_version"]
    assert result["knowledge_manifest_sha256"] == "a" * 64
    assert json.dumps(candidate) == original


@pytest.mark.parametrize("candidate", ["{}", "```json\n{}\n```", '{"status":1,"status":2}',
                                       '{"value":NaN}', "[]", "not json"])
def test_binding_does_not_repair_invalid_json_or_missing_fields(candidate):
    with pytest.raises(ValueError, match="invalid_core_candidate_binding"):
        BINDING.main(json.dumps(request()), candidate)
