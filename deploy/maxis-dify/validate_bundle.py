"""Deterministically validate the tracked LTM MAXIS/Dify build specification."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
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
KNOWLEDGE_DOCUMENT_FIELDS = frozenset({
    "id", "path", "revision", "reviewed_for_release", "reviewed_for_version",
    "reviewed_at", "reviewer_digest", "owner",
    "classification", "audience", "allowed_roles",
})
KNOWLEDGE_EVAL_TYPES = frozenset({
    "positive", "no_hit_abstention", "prompt_injection", "acl_negative",
})
EXECUTION_POLICY = {
    "critical_node_failure": "terminate",
    "automatic_retry": "disabled",
    "default_value_on_error": "forbidden",
    "failure_branch_answer": "forbidden",
    "model_fallback": "forbidden",
    "conversation_mode": "stateless",
    "client_conversation_id": "empty",
    "auto_generate_name": False,
}
SAFE_ASSET_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
SAFE_RELEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
UTC_INSTANT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")


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


def _validate_knowledge_source(
    root: Path,
    bundle: dict[str, Any],
    registry_roles: set[str],
) -> tuple[dict[str, dict[str, Any]], str]:
    source_path = _tracked_path(
        root, bundle.get("knowledge_source_manifest"), "knowledge_source_manifest",
    )
    source = _load_yaml(source_path)
    if source.get("contract") != "ltm-knowledge-source.v2":
        raise BundleValidationError("Knowledge source contract drifted")
    if set(source) != {
        "contract", "role_registry", "governance", "chunking", "documents",
    }:
        raise BundleValidationError("Knowledge source fields are incomplete")
    if source.get("role_registry") != bundle.get("registry"):
        raise BundleValidationError("Knowledge source Role registry drifted from bundle")
    governance = _mapping(source.get("governance"), "knowledge governance")
    if set(governance) != {
        "review_cycle_days", "immutable_release", "deployment_strategy",
        "eval_suite_sha256",
    }:
        raise BundleValidationError("Knowledge governance policy is incomplete")
    review_days = governance.get("review_cycle_days")
    if isinstance(review_days, bool) or not isinstance(review_days, int) \
            or not 1 <= review_days <= 365:
        raise BundleValidationError("Knowledge review_cycle_days must be between 1 and 365")
    if governance.get("immutable_release") is not True \
            or governance.get("deployment_strategy") != "blue_green":
        raise BundleValidationError("Knowledge releases must be immutable blue_green releases")
    eval_suite_sha256 = governance.get("eval_suite_sha256")
    if not isinstance(eval_suite_sha256, str) \
            or not DIGEST_RE.fullmatch(eval_suite_sha256):
        raise BundleValidationError("Knowledge eval_suite_sha256 must be lowercase SHA-256")
    if source.get("chunking") != {
        "strategy": "markdown-headings-then-length.v1",
        "max_chars": 1200,
        "overlap_chars": 120,
    }:
        raise BundleValidationError("Knowledge chunking policy drifted")

    raw_documents = source.get("documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise BundleValidationError("Knowledge documents must be a non-empty list")
    documents: dict[str, dict[str, Any]] = {}
    paths: set[str] = set()
    reviewed_versions: set[str] = set()
    now = datetime.now(timezone.utc)
    for index, raw in enumerate(raw_documents):
        document = _mapping(raw, f"knowledge documents[{index}]")
        if set(document) != KNOWLEDGE_DOCUMENT_FIELDS:
            raise BundleValidationError("every Knowledge document needs complete governance fields")
        document_id = document.get("id")
        if not isinstance(document_id, str) or not SAFE_ASSET_ID_RE.fullmatch(document_id) \
                or document_id in documents:
            raise BundleValidationError("Knowledge document ids must be stable and unique")
        path = document.get("path")
        if not isinstance(path, str) or path in paths:
            raise BundleValidationError("Knowledge document paths must be unique")
        tracked = _tracked_path(root, path, f"Knowledge document {document_id}")
        if tracked.suffix.lower() != ".md":
            raise BundleValidationError("Knowledge documents must be Markdown files")
        revision = document.get("revision")
        revision_ok = (
            isinstance(revision, int) and not isinstance(revision, bool) and revision > 0
        ) or (
            isinstance(revision, str) and bool(SAFE_RELEASE_RE.fullmatch(revision))
        )
        if not revision_ok:
            raise BundleValidationError("Knowledge document revision is invalid")
        if document.get("reviewed_for_release") is not True:
            raise BundleValidationError("every Knowledge document must be reviewed for release")
        reviewed_for_version = document.get("reviewed_for_version")
        if not isinstance(reviewed_for_version, str) \
                or not SAFE_RELEASE_RE.fullmatch(reviewed_for_version):
            raise BundleValidationError("Knowledge reviewed_for_version is invalid")
        reviewed_versions.add(reviewed_for_version)
        reviewed_at = document.get("reviewed_at")
        if not isinstance(reviewed_at, str) or not UTC_INSTANT_RE.fullmatch(reviewed_at):
            raise BundleValidationError("Knowledge reviewed_at must be canonical UTC ISO")
        try:
            reviewed_instant = datetime.fromisoformat(reviewed_at[:-1] + "+00:00")
        except ValueError as exc:
            raise BundleValidationError("Knowledge reviewed_at is invalid") from exc
        if reviewed_instant > now:
            raise BundleValidationError("Knowledge reviewed_at cannot be in the future")
        if now - reviewed_instant > timedelta(days=review_days):
            raise BundleValidationError("Knowledge review is older than review_cycle_days")
        if not isinstance(document.get("reviewer_digest"), str) \
                or not DIGEST_RE.fullmatch(document["reviewer_digest"]):
            raise BundleValidationError("Knowledge reviewer_digest must be lowercase SHA-256")
        owner = document.get("owner")
        if not isinstance(owner, str) or not SAFE_ASSET_ID_RE.fullmatch(owner):
            raise BundleValidationError("Knowledge document owner is invalid")
        # The current release uses one dataset without an enforced audience metadata
        # filter.  Restrict the source inventory to the single audience it can safely
        # represent; narrower classifications require a separate dataset/filter first.
        if document.get("classification") != "internal" \
                or document.get("audience") != "all_ltm_users":
            raise BundleValidationError(
                "current Knowledge documents must be internal/all_ltm_users"
            )
        allowed_roles = document.get("allowed_roles")
        if not isinstance(allowed_roles, list) or not allowed_roles \
                or any(not isinstance(role, str) for role in allowed_roles) \
                or len(allowed_roles) != len(set(allowed_roles)):
            raise BundleValidationError("Knowledge allowed_roles must be non-empty and unique")
        if not set(allowed_roles).issubset(registry_roles):
            raise BundleValidationError("Knowledge allowed_roles contains an unregistered Role")
        documents[document_id] = document
        paths.add(path)
    if len(reviewed_versions) != 1:
        raise BundleValidationError(
            "every Knowledge document must be reviewed for one identical knowledge version"
        )
    return documents, eval_suite_sha256


def _validate_knowledge_evals(
    root: Path,
    bundle: dict[str, Any],
    registry_roles: set[str],
    documents: dict[str, dict[str, Any]],
    expected_suite_sha256: str,
) -> None:
    eval_path = _tracked_path(root, bundle.get("knowledge_evals"), "knowledge_evals")
    spec = _load_yaml(eval_path)
    if set(spec) != {"contract", "release_gate", "cases"} \
            or spec.get("contract") != "ltm-maxis-dify-knowledge-evals.v1":
        raise BundleValidationError("Knowledge eval contract drifted")
    release_gate = _mapping(spec.get("release_gate"), "knowledge eval release_gate")
    if release_gate != {
        "required_before_prod": True,
        "execution": "remote_maxis_dify",
        "repository_status": "specification_only_not_executed",
        "evidence_artifact": "required",
    }:
        raise BundleValidationError(
            "Knowledge evals must remain an unexecuted spec with a required prod evidence artifact"
        )
    cases = spec.get("cases")
    if not isinstance(cases, list) or not cases:
        raise BundleValidationError("Knowledge eval cases must be a non-empty list")
    ids: set[str] = set()
    types: set[str] = set()
    coverage = {name: set() for name in (
        "positive", "prompt_injection", "acl_negative",
    )}
    for index, raw in enumerate(cases):
        case = _mapping(raw, f"knowledge eval cases[{index}]")
        case_type = case.get("type")
        required_fields = {"id", "type", "role_id", "query", "expected"}
        allowed_fields = required_fields | {"target_document_id"}
        if not required_fields.issubset(case) or set(case) - allowed_fields:
            raise BundleValidationError("Knowledge eval case fields are incomplete")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not SAFE_ASSET_ID_RE.fullmatch(case_id) \
                or case_id in ids:
            raise BundleValidationError("Knowledge eval ids must be safe and unique")
        if case_type not in KNOWLEDGE_EVAL_TYPES:
            raise BundleValidationError("Knowledge eval type is unsupported")
        role_id = case.get("role_id")
        if role_id not in registry_roles:
            raise BundleValidationError("Knowledge eval references an unregistered Role")
        query = case.get("query")
        if not isinstance(query, str) or not 3 <= len(query.strip()) <= 1000:
            raise BundleValidationError("Knowledge eval query is invalid")
        expected = _mapping(case.get("expected"), f"knowledge eval {case_id}.expected")
        if set(expected) != {
            "outcome", "include_document_ids", "exclude_document_ids",
            "ignore_query_instructions",
        }:
            raise BundleValidationError("Knowledge eval expected contract is incomplete")
        included = expected.get("include_document_ids")
        excluded = expected.get("exclude_document_ids")
        if not isinstance(included, list) or not isinstance(excluded, list) \
                or any(value not in documents for value in included + excluded) \
                or len(included) != len(set(included)) \
                or len(excluded) != len(set(excluded)) \
                or set(included) & set(excluded):
            raise BundleValidationError("Knowledge eval document expectations are invalid")
        target = case.get("target_document_id")
        if case_type == "no_hit_abstention":
            if "target_document_id" in case or expected != {
                "outcome": "abstain",
                "include_document_ids": [],
                "exclude_document_ids": [],
                "ignore_query_instructions": False,
            }:
                raise BundleValidationError("no-hit eval must require a clean abstention")
        else:
            if target not in documents:
                raise BundleValidationError("Knowledge eval target document is unknown")
            allowed = set(documents[target]["allowed_roles"])
            if case_type in {"positive", "prompt_injection"}:
                if role_id not in allowed:
                    raise BundleValidationError("positive Knowledge eval Role is not allowed")
                if expected != {
                    "outcome": "retrieve",
                    "include_document_ids": [target],
                    "exclude_document_ids": [],
                    "ignore_query_instructions": case_type == "prompt_injection",
                }:
                    raise BundleValidationError("positive Knowledge eval expectation drifted")
            elif case_type == "acl_negative":
                if role_id in allowed:
                    raise BundleValidationError("ACL-negative eval must use a denied Role")
                if expected != {
                    "outcome": "deny",
                    "include_document_ids": [],
                    "exclude_document_ids": [target],
                    "ignore_query_instructions": False,
                }:
                    raise BundleValidationError("ACL-negative eval expectation drifted")
            coverage[case_type].add(target)
        ids.add(case_id)
        types.add(case_type)
    if types != KNOWLEDGE_EVAL_TYPES:
        raise BundleValidationError("Knowledge evals must include all four required types")
    required_documents = set(documents)
    for case_type, covered in coverage.items():
        if covered != required_documents:
            raise BundleValidationError(
                f"{case_type} Knowledge evals must cover every document"
            )
    actual_suite_sha256 = hashlib.sha256(eval_path.read_bytes()).hexdigest()
    if actual_suite_sha256 != expected_suite_sha256:
        raise BundleValidationError(
            "Knowledge eval_suite_sha256 does not match knowledge-evals.yml"
        )


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
    if _mapping(bundle.get("execution_policy"), "execution_policy") != EXECUTION_POLICY:
        raise BundleValidationError("Dify fail-closed execution policy drifted")
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

    knowledge_documents, knowledge_eval_suite_sha256 = _validate_knowledge_source(
        repository, bundle, set(all_roles),
    )
    _validate_knowledge_evals(
        repository, bundle, set(all_roles), knowledge_documents,
        knowledge_eval_suite_sha256,
    )
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
    print(
        "MAXIS/Dify bundle valid: 3 workflows, 6 Start variables, "
        "governed Knowledge + static eval coverage"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
