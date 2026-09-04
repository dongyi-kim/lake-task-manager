from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from app.agent.maxis.contracts import (
    EditorDraftV1,
    EvidencePackV1,
    canonical_payload_schema,
)
from app.agent.maxis.response_guard import ResponseGuard
from app.agent.workflow.agents.portfolio_analyst import SCHEMA as PORTFOLIO_SCHEMA
from app.agent.workflow.contracts import role_output_schema


ROOT = Path(__file__).resolve().parents[3]
VALIDATOR_PATH = ROOT / "deploy" / "maxis-dify" / "validate_bundle.py"


def _validator_module():
    spec = importlib.util.spec_from_file_location("ltm_maxis_dify_bundle_validator", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tracked_dify_bundle_matches_registry_and_guard_contracts():
    validator = _validator_module()
    validator.validate_bundle(ROOT)

    smoke = yaml.safe_load(
        (ROOT / "deploy" / "maxis-dify" / "smoke-cases.yml").read_text(
            encoding="utf-8"
        )
    )
    for case in smoke["cases"]:
        pack = EvidencePackV1.model_validate_json(
            json.dumps(case["inputs"]["context_pack"], ensure_ascii=False),
            strict=True,
        )
        remote, _ = ResponseGuard().validate_remote_role(
            json.dumps(case["expected_response"], ensure_ascii=False),
            expected_role_id=case["inputs"]["role_id"],
            payload_model=pack.payload_schema(),
            evidence=pack,
            expected_knowledge_manifest_sha256=pack.knowledge_manifest_sha256,
        )
        assert remote.workflow_version == case["inputs"]["workflow_version"]
        assert remote.knowledge_version == case["inputs"]["knowledge_version"]

        if case["inputs"]["role_id"] == "knowledge_curator":
            expected_schema = role_output_schema("knowledge_curator")
        elif case["inputs"]["role_id"] == "portfolio_analyst":
            expected_schema = PORTFOLIO_SCHEMA
        else:
            expected_schema = EditorDraftV1.model_json_schema()
            expected_schema["properties"]["mode"] = {
                "type": "string", "const": "description",
            }
        expected_json, expected_digest = canonical_payload_schema(expected_schema)
        assert pack.payload_schema_json == expected_json
        assert pack.payload_schema_sha256 == expected_digest


def test_validator_rejects_an_extra_start_variable(tmp_path):
    validator = _validator_module()
    target = tmp_path / "repo"
    (target / "deploy").mkdir(parents=True)
    (target / "config").mkdir()
    source = ROOT / "deploy" / "maxis-dify"
    destination = target / "deploy" / "maxis-dify"
    import shutil
    shutil.copytree(source, destination)
    shutil.copy2(ROOT / "config" / "maxis-workflows.yml", target / "config")
    bundle_path = destination / "bundle.yml"
    bundle = yaml.safe_load(bundle_path.read_text(encoding="utf-8"))
    bundle["required_start_variables"].append("approval_token")
    bundle_path.write_text(yaml.safe_dump(bundle, allow_unicode=True), encoding="utf-8")
    with pytest.raises(validator.BundleValidationError, match="exactly six"):
        validator.validate_bundle(target)


def test_validator_rejects_a_missing_manifest_echo(tmp_path):
    validator = _validator_module()
    target = tmp_path / "repo"
    (target / "deploy").mkdir(parents=True)
    (target / "config").mkdir()
    source = ROOT / "deploy" / "maxis-dify"
    destination = target / "deploy" / "maxis-dify"
    import shutil
    shutil.copytree(source, destination)
    shutil.copy2(ROOT / "config" / "maxis-workflows.yml", target / "config")
    smoke_path = destination / "smoke-cases.yml"
    smoke = yaml.safe_load(smoke_path.read_text(encoding="utf-8"))
    smoke["cases"][0]["expected_response"].pop("knowledge_manifest_sha256")
    smoke_path.write_text(yaml.safe_dump(smoke, allow_unicode=True), encoding="utf-8")
    with pytest.raises(validator.BundleValidationError, match="violates schema"):
        validator.validate_bundle(target)


def test_validator_rejects_a_missing_knowledge_retrieval_proof(tmp_path):
    validator = _validator_module()
    target = tmp_path / "repo"
    (target / "deploy").mkdir(parents=True)
    (target / "config").mkdir()
    source = ROOT / "deploy" / "maxis-dify"
    destination = target / "deploy" / "maxis-dify"
    import shutil
    shutil.copytree(source, destination)
    shutil.copy2(ROOT / "config" / "maxis-workflows.yml", target / "config")
    smoke_path = destination / "smoke-cases.yml"
    smoke = yaml.safe_load(smoke_path.read_text(encoding="utf-8"))
    smoke["cases"][0]["expected_response"].pop("knowledge_proof")
    smoke_path.write_text(yaml.safe_dump(smoke, allow_unicode=True), encoding="utf-8")
    with pytest.raises(validator.BundleValidationError, match="retrieval proof"):
        validator.validate_bundle(target)


def test_validator_rejects_payload_schema_digest_or_payload_drift(tmp_path):
    validator = _validator_module()
    target = tmp_path / "repo"
    (target / "deploy").mkdir(parents=True)
    (target / "config").mkdir()
    source = ROOT / "deploy" / "maxis-dify"
    destination = target / "deploy" / "maxis-dify"
    import shutil
    shutil.copytree(source, destination)
    shutil.copy2(ROOT / "config" / "maxis-workflows.yml", target / "config")
    smoke_path = destination / "smoke-cases.yml"
    smoke = yaml.safe_load(smoke_path.read_text(encoding="utf-8"))
    smoke["cases"][0]["inputs"]["context_pack"]["payload_schema_sha256"] = "f" * 64
    smoke_path.write_text(yaml.safe_dump(smoke, allow_unicode=True), encoding="utf-8")
    with pytest.raises(validator.BundleValidationError, match="schema digest mismatch"):
        validator.validate_bundle(target)

    smoke = yaml.safe_load(
        (ROOT / "deploy" / "maxis-dify" / "smoke-cases.yml").read_text(
            encoding="utf-8"
        )
    )
    smoke["cases"][0]["expected_response"]["payload"] = {"summary": "wrong shape"}
    smoke_path.write_text(yaml.safe_dump(smoke, allow_unicode=True), encoding="utf-8")
    with pytest.raises(validator.BundleValidationError, match="per-call schema"):
        validator.validate_bundle(target)
