from __future__ import annotations

import importlib.util
import json
import shutil
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


def _copy_bundle_repository(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    (target / "deploy").mkdir(parents=True)
    (target / "config").mkdir()
    shutil.copytree(
        ROOT / "deploy" / "maxis-dify", target / "deploy" / "maxis-dify",
    )
    for name in ("maxis-workflows.yml", "maxis-knowledge.yml"):
        shutil.copy2(ROOT / "config" / name, target / "config" / name)
    shutil.copytree(ROOT / "knowledge", target / "knowledge")
    return target


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
    target = _copy_bundle_repository(tmp_path)
    destination = target / "deploy" / "maxis-dify"
    bundle_path = destination / "bundle.yml"
    bundle = yaml.safe_load(bundle_path.read_text(encoding="utf-8"))
    bundle["required_start_variables"].append("approval_token")
    bundle_path.write_text(yaml.safe_dump(bundle, allow_unicode=True), encoding="utf-8")
    with pytest.raises(validator.BundleValidationError, match="exactly six"):
        validator.validate_bundle(target)


def test_validator_rejects_a_missing_manifest_echo(tmp_path):
    validator = _validator_module()
    target = _copy_bundle_repository(tmp_path)
    destination = target / "deploy" / "maxis-dify"
    smoke_path = destination / "smoke-cases.yml"
    smoke = yaml.safe_load(smoke_path.read_text(encoding="utf-8"))
    smoke["cases"][0]["expected_response"].pop("knowledge_manifest_sha256")
    smoke_path.write_text(yaml.safe_dump(smoke, allow_unicode=True), encoding="utf-8")
    with pytest.raises(validator.BundleValidationError, match="violates schema"):
        validator.validate_bundle(target)


def test_validator_rejects_a_missing_knowledge_retrieval_proof(tmp_path):
    validator = _validator_module()
    target = _copy_bundle_repository(tmp_path)
    destination = target / "deploy" / "maxis-dify"
    smoke_path = destination / "smoke-cases.yml"
    smoke = yaml.safe_load(smoke_path.read_text(encoding="utf-8"))
    smoke["cases"][0]["expected_response"].pop("knowledge_proof")
    smoke_path.write_text(yaml.safe_dump(smoke, allow_unicode=True), encoding="utf-8")
    with pytest.raises(validator.BundleValidationError, match="retrieval proof"):
        validator.validate_bundle(target)


def test_validator_rejects_payload_schema_digest_or_payload_drift(tmp_path):
    validator = _validator_module()
    target = _copy_bundle_repository(tmp_path)
    destination = target / "deploy" / "maxis-dify"
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


def test_validator_rejects_fail_open_dify_execution_policy(tmp_path):
    validator = _validator_module()
    target = _copy_bundle_repository(tmp_path)
    bundle_path = target / "deploy" / "maxis-dify" / "bundle.yml"
    bundle = yaml.safe_load(bundle_path.read_text(encoding="utf-8"))
    bundle["execution_policy"]["automatic_retry"] = "enabled"
    bundle_path.write_text(
        yaml.safe_dump(bundle, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )

    with pytest.raises(validator.BundleValidationError, match="fail-closed"):
        validator.validate_bundle(target)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda row: row.pop("classification"), "complete governance"),
        (lambda row: row.update(classification="restricted"), "internal/all_ltm_users"),
        (lambda row: row.update(allowed_roles=["not_registered"]), "unregistered Role"),
        (lambda row: row.update(reviewed_for_release=False), "reviewed for release"),
        (lambda row: row.update(reviewed_for_version="other-version"), "one identical"),
        (lambda row: row.update(reviewed_at="2020-01-01T00:00:00Z"), "older"),
        (lambda row: row.update(reviewer_digest="named-reviewer"), "reviewer_digest"),
        (lambda row: row.update(revision=0), "revision"),
    ],
)
def test_validator_rejects_ungoverned_knowledge_documents(
    tmp_path, mutate, message,
):
    validator = _validator_module()
    target = _copy_bundle_repository(tmp_path)
    source_path = target / "config" / "maxis-knowledge.yml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    mutate(source["documents"][0])
    source_path.write_text(
        yaml.safe_dump(source, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )

    with pytest.raises(validator.BundleValidationError, match=message):
        validator.validate_bundle(target)


def test_validator_rejects_duplicate_knowledge_identity_and_policy_drift(tmp_path):
    validator = _validator_module()
    target = _copy_bundle_repository(tmp_path)
    source_path = target / "config" / "maxis-knowledge.yml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["documents"].append(dict(source["documents"][0]))
    source_path.write_text(
        yaml.safe_dump(source, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )
    with pytest.raises(validator.BundleValidationError, match="stable and unique"):
        validator.validate_bundle(target)

    source = yaml.safe_load(
        (ROOT / "config" / "maxis-knowledge.yml").read_text(encoding="utf-8")
    )
    source["governance"]["deployment_strategy"] = "in_place"
    source_path.write_text(
        yaml.safe_dump(source, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )
    with pytest.raises(validator.BundleValidationError, match="immutable blue_green"):
        validator.validate_bundle(target)


def test_validator_binds_declared_eval_suite_digest_to_tracked_spec(tmp_path):
    validator = _validator_module()
    target = _copy_bundle_repository(tmp_path)
    source_path = target / "config" / "maxis-knowledge.yml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["governance"]["eval_suite_sha256"] = "0" * 64
    source_path.write_text(
        yaml.safe_dump(source, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )

    with pytest.raises(validator.BundleValidationError, match="does not match"):
        validator.validate_bundle(target)


@pytest.mark.parametrize(
    ("case_id", "message"),
    [
        ("ticket-rules-positive", "positive.*cover every document"),
        ("ticket-rules-injection", "prompt_injection.*cover every document"),
        ("ticket-rules-acl-negative", "acl_negative.*cover every document"),
        ("unrelated-domain-no-hit", "all four required types"),
    ],
)
def test_validator_requires_every_golden_eval_type_and_document_coverage(
    tmp_path, case_id, message,
):
    validator = _validator_module()
    target = _copy_bundle_repository(tmp_path)
    eval_path = target / "deploy" / "maxis-dify" / "knowledge-evals.yml"
    spec = yaml.safe_load(eval_path.read_text(encoding="utf-8"))
    spec["cases"] = [row for row in spec["cases"] if row["id"] != case_id]
    eval_path.write_text(
        yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )

    with pytest.raises(validator.BundleValidationError, match=message):
        validator.validate_bundle(target)


def test_validator_rejects_acl_negative_case_that_uses_an_allowed_role(tmp_path):
    validator = _validator_module()
    target = _copy_bundle_repository(tmp_path)
    eval_path = target / "deploy" / "maxis-dify" / "knowledge-evals.yml"
    spec = yaml.safe_load(eval_path.read_text(encoding="utf-8"))
    case = next(row for row in spec["cases"] if row["id"] == "ticket-rules-acl-negative")
    case["role_id"] = "work_architect"
    eval_path.write_text(
        yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )

    with pytest.raises(validator.BundleValidationError, match="denied Role"):
        validator.validate_bundle(target)


def test_validator_does_not_allow_repository_to_claim_remote_eval_success(tmp_path):
    validator = _validator_module()
    target = _copy_bundle_repository(tmp_path)
    eval_path = target / "deploy" / "maxis-dify" / "knowledge-evals.yml"
    spec = yaml.safe_load(eval_path.read_text(encoding="utf-8"))
    spec["release_gate"]["repository_status"] = "passed"
    eval_path.write_text(
        yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )

    with pytest.raises(validator.BundleValidationError, match="unexecuted spec"):
        validator.validate_bundle(target)
