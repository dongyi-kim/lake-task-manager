"""Deterministically validate the tracked LTM MAXIS/Dify build specification."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema.validators import validator_for


START_VARIABLES = frozenset({
    "context_pack",
    "role_id",
    "correlation_id",
    "output_contract",
    "workflow_version",
    "knowledge_version",
})
WORKFLOW_FIELDS = frozenset({
    "app_type",
    "endpoint",
    "response_mode",
    "conversation_mode",
    "output_contract",
})
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_PAYLOAD_SCHEMA_CHARS = 16_000
RESPONSE_FIELDS = frozenset({
    "contract",
    "role_id",
    "status",
    "payload",
    "claims",
    "payload_claims",
    "citations",
    "missing_inputs",
    "warnings",
    "workflow_version",
    "knowledge_version",
    "knowledge_manifest_sha256",
})
OPTIONAL_RESPONSE_FIELDS = frozenset({"knowledge_proof"})


class BundleValidationError(ValueError):
    """One or more checked-in Dify bundle invariants drifted."""


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise BundleValidationError(f"{label} must be a mapping")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise BundleValidationError(f"cannot load {path.name}") from exc


def _strict_json_object(value: str, label: str) -> dict[str, Any]:
    def object_pairs(pairs):
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = item
        return result

    def invalid_constant(_value):
        raise ValueError("non-finite JSON number")

    try:
        decoded = json.loads(
            value,
            object_pairs_hook=object_pairs,
            parse_constant=invalid_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BundleValidationError(f"{label} must be strict JSON") from exc
    if not isinstance(decoded, dict):
        raise BundleValidationError(f"{label} must be a JSON object")
    return decoded


def _validate_local_schema_refs(value: Any, label: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                not isinstance(item, str) or not item.startswith("#")
            ):
                raise BundleValidationError(f"{label} contains a non-local schema reference")
            _validate_local_schema_refs(item, label)
    elif isinstance(value, list):
        for item in value:
            _validate_local_schema_refs(item, label)


def _payload_schema_validator(pack: dict[str, Any], case_id: str) -> Any:
    encoded = pack.get("payload_schema_json")
    digest = pack.get("payload_schema_sha256")
    if not isinstance(encoded, str) or not 2 <= len(encoded) <= MAX_PAYLOAD_SCHEMA_CHARS:
        raise BundleValidationError(f"{case_id} payload schema JSON is invalid")
    schema = _strict_json_object(encoded, f"{case_id} payload schema")
    canonical = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if canonical != encoded:
        raise BundleValidationError(f"{case_id} payload schema is not canonical JSON")
    actual_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if not isinstance(digest, str) or digest != actual_digest:
        raise BundleValidationError(f"{case_id} payload schema digest mismatch")
    if schema.get("type") != "object":
        raise BundleValidationError(f"{case_id} payload schema root must be an object")
    _validate_local_schema_refs(schema, f"{case_id} payload schema")
    try:
        validator_type = validator_for(schema)
        validator_type.check_schema(schema)
    except Exception as exc:
        raise BundleValidationError(f"{case_id} payload schema is invalid") from exc
    return validator_type(schema)


def _tracked_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise BundleValidationError(f"{label} must be a repository-relative path")
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise BundleValidationError(f"{label} escapes repository root") from exc
    if not candidate.is_file():
        raise BundleValidationError(f"{label} does not exist")
    return candidate


def _validate_prompts(
    root: Path,
    bundle: dict[str, Any],
    workflows: dict[str, Any],
) -> None:
    common_path = _tracked_path(root, bundle.get("common_prompt"), "common_prompt")
    common = common_path.read_text(encoding="utf-8")
    common_requirements = (
        "untrusted data",
        "additionalProperties: false",
        "knowledge_manifest_sha256",
        "payload_schema_json",
        "payload_schema_sha256",
        "payload_claims",
        "approval tokens",
        "Return one JSON object",
    )
    missing_common = [value for value in common_requirements if value not in common]
    if missing_common:
        raise BundleValidationError("common prompt is missing trust-boundary rules")
    for variable in START_VARIABLES:
        if f"`{variable}`" not in common:
            raise BundleValidationError(f"common prompt omits Start variable {variable}")

    for workflow_key, raw in workflows.items():
        workflow = _mapping(raw, f"workflows.{workflow_key}")
        prompt = _tracked_path(
            root, workflow.get("prompt"), f"workflows.{workflow_key}.prompt"
        ).read_text(encoding="utf-8")
        for variable in START_VARIABLES:
            if f"`{variable}`" not in prompt:
                raise BundleValidationError(
                    f"{workflow_key} prompt omits Start variable {variable}"
                )
        if "`context_pack.payload_schema_json`" not in prompt or (
            "`context_pack.payload_schema_sha256`" not in prompt
        ):
            raise BundleValidationError(
                f"{workflow_key} prompt omits the per-call payload schema binding"
            )
        for role_id in workflow.get("roles") or []:
            if f"`{role_id}`" not in prompt:
                raise BundleValidationError(
                    f"{workflow_key} prompt omits canonical Role {role_id}"
                )


def _validate_schema(
    schema: dict[str, Any], registry_roles: set[str]
) -> Any:
    validator_type = validator_for(schema)
    try:
        validator_type.check_schema(schema)
    except Exception as exc:
        raise BundleValidationError("response schema is invalid") from exc
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise BundleValidationError("response schema must be a closed object")
    properties = _mapping(schema.get("properties"), "response schema properties")
    if set(properties) != RESPONSE_FIELDS | OPTIONAL_RESPONSE_FIELDS or (
        set(schema.get("required") or []) != RESPONSE_FIELDS
    ):
        raise BundleValidationError("response schema fields must exactly match the wire contract")
    if properties.get("contract", {}).get("const") != "remote-role-result.v1":
        raise BundleValidationError("response contract literal drifted")
    if set(properties.get("role_id", {}).get("enum") or []) != registry_roles:
        raise BundleValidationError("response Role enum does not cover the workflow registry")
    digest = schema.get("$defs", {}).get("digest", {})
    if digest.get("pattern") != DIGEST_RE.pattern:
        raise BundleValidationError("knowledge manifest echo must be one lowercase SHA-256")
    for definition in (schema.get("$defs") or {}).values():
        if isinstance(definition, dict) and definition.get("type") == "object":
            if definition.get("additionalProperties") is not False:
                raise BundleValidationError("nested response objects must be closed")
    return validator_type(schema)


def _validate_smoke_cases(
    smoke: dict[str, Any], workflows: dict[str, Any], response_validator: Any
) -> None:
    if smoke.get("contract") != "ltm-maxis-dify-smoke.v1":
        raise BundleValidationError("smoke contract drifted")
    cases = smoke.get("cases")
    if not isinstance(cases, list) or not cases:
        raise BundleValidationError("smoke cases must be a non-empty list")
    ids: set[str] = set()
    covered: set[str] = set()
    for index, raw in enumerate(cases):
        case = _mapping(raw, f"cases[{index}]")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise BundleValidationError("smoke case ids must be non-empty and unique")
        ids.add(case_id)
        workflow_key = case.get("workflow")
        if workflow_key not in workflows:
            raise BundleValidationError(f"{case_id} references an unknown workflow")
        covered.add(workflow_key)
        inputs = _mapping(case.get("inputs"), f"{case_id}.inputs")
        if set(inputs) != START_VARIABLES:
            raise BundleValidationError(f"{case_id} must supply exactly six Start variables")
        pack = _mapping(inputs.get("context_pack"), f"{case_id}.context_pack")
        role_id = inputs.get("role_id")
        if role_id not in workflows[workflow_key].get("roles", []):
            raise BundleValidationError(f"{case_id} Role is not bound to its workflow")
        if pack.get("use_case") != role_id:
            raise BundleValidationError(f"{case_id} use_case/Role mismatch")
        if pack.get("request_id") != inputs.get("correlation_id"):
            raise BundleValidationError(f"{case_id} correlation mismatch")
        if inputs.get("output_contract") != "remote-role-result.v1":
            raise BundleValidationError(f"{case_id} output contract mismatch")
        digest = pack.get("knowledge_manifest_sha256")
        if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            raise BundleValidationError(f"{case_id} manifest digest is invalid")
        payload_validator = _payload_schema_validator(pack, case_id)
        source_ids: set[str] = set()
        for source in pack.get("sources") or []:
            row = _mapping(source, f"{case_id}.source")
            source_id = row.get("source_id")
            content = row.get("content")
            if not isinstance(source_id, str) or source_id in source_ids:
                raise BundleValidationError(f"{case_id} source ids must be unique")
            if not isinstance(content, str):
                raise BundleValidationError(f"{case_id} source content must be text")
            actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if row.get("content_sha256") != actual:
                raise BundleValidationError(f"{case_id} source digest mismatch")
            source_ids.add(source_id)

        response = _mapping(case.get("expected_response"), f"{case_id}.expected_response")
        try:
            response_validator.validate(response)
        except Exception as exc:
            raise BundleValidationError(f"{case_id} response violates schema") from exc
        try:
            payload_validator.validate(response.get("payload"))
        except Exception as exc:
            raise BundleValidationError(
                f"{case_id} payload violates its per-call schema"
            ) from exc
        for field in ("role_id", "workflow_version", "knowledge_version"):
            if response.get(field) != inputs.get(field):
                raise BundleValidationError(f"{case_id} does not echo {field}")
        if response.get("knowledge_manifest_sha256") != digest:
            raise BundleValidationError(f"{case_id} does not echo the Knowledge manifest")
        proof = response.get("knowledge_proof")
        if not isinstance(proof, str) or not proof.startswith("LTM_RELEASE_SENTINEL_V1="):
            raise BundleValidationError(f"{case_id} omits the Knowledge retrieval proof")
        citations = {
            row.get("citation_id"): row.get("source_id")
            for row in response.get("citations") or []
            if isinstance(row, dict)
        }
        if not set(citations.values()).issubset(source_ids):
            raise BundleValidationError(f"{case_id} cites an unknown source")
        claims = {
            row.get("claim_id"): row
            for row in response.get("claims") or []
            if isinstance(row, dict)
        }
        for claim in claims.values():
            if not set(claim.get("citation_ids") or []).issubset(citations):
                raise BundleValidationError(f"{case_id} claim cites an unknown citation")
        expected = _mapping(case.get("expected"), f"{case_id}.expected")
        bindings = {
            row.get("path"): row.get("claim_ids")
            for row in response.get("payload_claims") or []
            if isinstance(row, dict)
        }
        path = expected.get("narrative_path")
        bound_claim_ids = bindings.get(path)
        if not isinstance(bound_claim_ids, list) or not bound_claim_ids:
            raise BundleValidationError(f"{case_id} expected narrative is not claim-bound")
        narrative = str(expected.get("narrative_text") or "")
        support = " ".join(str(claims.get(claim_id, {}).get("text") or "") for claim_id in bound_claim_ids)
        if not narrative or narrative not in support:
            raise BundleValidationError(f"{case_id} expected narrative is not claim-supported")
        if expected.get("source_id") not in citations.values():
            raise BundleValidationError(f"{case_id} expected source is not cited")
    if covered != set(workflows):
        raise BundleValidationError("smoke cases must cover every workflow")


def validate_bundle(root: Path | None = None) -> None:
    repository = (root or Path(__file__).resolve().parents[2]).resolve()
    bundle_path = repository / "deploy" / "maxis-dify" / "bundle.yml"
    bundle = _load_yaml(bundle_path)
    if bundle.get("contract") != "ltm-maxis-dify-bundle.v1":
        raise BundleValidationError("bundle contract drifted")
    retrieval = _mapping(
        bundle.get("knowledge_retrieval_contract"),
        "knowledge_retrieval_contract",
    )
    if retrieval != {
        "required": True,
        "nodes": "release_sentinel_and_role_knowledge",
        "query": "LTM_RELEASE_SENTINEL_V1",
        "role_query_source": "sys.query",
        "sentinel_prefix": "LTM_RELEASE_SENTINEL_V1=",
        "response_field": "knowledge_proof",
        "expected_digest_source": "active_profile_only",
    }:
        raise BundleValidationError("Knowledge retrieval sentinel contract drifted")
    configured = list(bundle.get("required_start_variables") or [])
    if len(configured) != len(set(configured)) or set(configured) != START_VARIABLES:
        raise BundleValidationError("bundle must declare exactly six Start variables")
    controls = _mapping(bundle.get("start_variable_control"), "start_variable_control")
    if set(controls) != START_VARIABLES or not set(controls.values()).issubset({"paragraph", "text-input"}):
        raise BundleValidationError("Start controls must exactly cover the six inputs")
    capacities = _mapping(
        bundle.get("start_variable_min_capacity"), "start_variable_min_capacity"
    )
    expected_capacities = {
        key: 20_000 if key == "context_pack" else 128
        for key in START_VARIABLES
    }
    if capacities != expected_capacities:
        raise BundleValidationError("Start input capacity contract drifted")

    registry_path = _tracked_path(root=repository, value=bundle.get("registry"), label="registry")
    registry = _load_yaml(registry_path)
    registry_workflows = _mapping(registry.get("workflows"), "registry.workflows")
    workflows = _mapping(bundle.get("workflows"), "bundle.workflows")
    if set(workflows) != set(registry_workflows):
        raise BundleValidationError("bundle workflow keys drifted from registry")
    all_roles: list[str] = []
    for key, raw in workflows.items():
        workflow = _mapping(raw, f"bundle.workflows.{key}")
        registered = _mapping(registry_workflows[key], f"registry.workflows.{key}")
        for field in WORKFLOW_FIELDS:
            if workflow.get(field) != registered.get(field):
                raise BundleValidationError(f"{key}.{field} drifted from registry")
        roles = workflow.get("roles")
        if not isinstance(roles, list) or roles != registered.get("roles"):
            raise BundleValidationError(f"{key}.roles drifted from registry")
        if len(roles) != len(set(roles)):
            raise BundleValidationError(f"{key}.roles contains duplicates")
        all_roles.extend(roles)
    if len(all_roles) != len(set(all_roles)):
        raise BundleValidationError("one Role cannot be bound to multiple workflows")

    _validate_prompts(repository, bundle, workflows)
    schema_path = _tracked_path(
        repository, bundle.get("response_schema"), "response_schema"
    )
    try:
        schema = _mapping(json.loads(schema_path.read_text(encoding="utf-8")), "schema")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleValidationError("cannot load response schema") from exc
    response_validator = _validate_schema(schema, set(all_roles))
    smoke_path = _tracked_path(repository, bundle.get("smoke_cases"), "smoke_cases")
    _validate_smoke_cases(_load_yaml(smoke_path), workflows, response_validator)


def main() -> int:
    try:
        validate_bundle()
    except BundleValidationError as exc:
        print(f"MAXIS/Dify bundle invalid: {exc}", file=sys.stderr)
        return 1
    print("MAXIS/Dify bundle valid: 3 workflows, 6 Start variables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
