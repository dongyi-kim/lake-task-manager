"""Self-contained deterministic pre-LLM model-input guard.

``knowledge_guard_asset.py`` replaces the reviewed workflow and environment
markers before this source is embedded in an importable Dify DSL. The Code node is
the only component that receives raw retrieval output: it validates the canonical
LTM EvidencePack, binds the API query, Start controls, and execution UUID,
authorizes Role Knowledge, verifies the environment-specific Release Sentinel,
and emits an allow/deny decision. Only the allow branch can reach the LLM;
retrieved text and caller inputs are always untrusted data.
"""

import json
import re
import unicodedata


_WORKFLOW_KEY = "__LTM_WORKFLOW_KEY__"
_DATASET_ID = "__LTM_DATASET_ID__"
_SENTINEL_DATASET_ID = "__LTM_SENTINEL_DATASET_ID__"
_DOCUMENT_IDS_BY_NAME = __LTM_DOCUMENT_BINDINGS__
_SOURCE_NAMES_BY_REMOTE_NAME = __LTM_SOURCE_NAME_BINDINGS__
_ALLOWED_DOCUMENT_NAMES_BY_ROLE = __LTM_ALLOWED_DOCUMENTS_BY_ROLE__
_SENTINEL_DOCUMENT_NAME = "__LTM_SENTINEL_DOCUMENT_NAME__"
_SENTINEL_DOCUMENT_ID = "__LTM_SENTINEL_DOCUMENT_ID__"

_CONTEXT_CONTRACT = "evidence-pack.v1"
_MODEL_INPUT_CONTRACT = "ltm-model-input.v1"
_OUTPUT_CONTRACT = "remote-role-result.v1"
_MAX_CONTEXT_PACK_UTF8_BYTES = 32768
_MAX_QUERY_UTF8_BYTES = 1024
_MAX_KNOWLEDGE_ITEMS = 2
_MAX_KNOWLEDGE_ITEM_UTF8_BYTES = 768
_MAX_KNOWLEDGE_TOTAL_UTF8_BYTES = 1024
_MAX_SENTINEL_UTF8_BYTES = 512
_MAX_SCALAR_UTF8_BYTES = 128
_MAX_MODEL_INPUT_UTF8_BYTES = 40000
_SAFE_CONTROL_CHARACTERS = {"\n", "\t"}
_CONTEXT_FIELDS = {
    "contract", "request_id", "use_case", "query", "pseudonymous_user",
    "requested_at", "scope", "sources", "facts", "metrics", "role_context",
    "freshness", "partial", "flags", "knowledge_manifest_sha256",
    "payload_schema_json", "payload_schema_sha256",
}
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_WORKFLOW_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_PROOF = re.compile(
    r"(?m)^[ \t]*(LTM_RELEASE_SENTINEL_V1=[^\r\n]{1,216})[ \t]*$"
)


class KnowledgeGuardError(ValueError):
    """A value crossed the deterministic pre-LLM boundary incorrectly."""


def _fail(code):
    # Error codes are constant and never copy caller or retrieved content into logs.
    raise KnowledgeGuardError("knowledge_guard:" + code)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(_value):
    _fail("nonfinite_json_number")


def _canonical(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except Exception:
        _fail("non_json_value")


def _utf8_bytes(value):
    try:
        return len(value.encode("utf-8"))
    except Exception:
        _fail("text_encoding_invalid")


def _bounded_scalar(value, code, pattern=None):
    if (
        not isinstance(value, str)
        or not value
        or _utf8_bytes(value) > _MAX_SCALAR_UTF8_BYTES
        or (pattern is not None and not pattern.fullmatch(value))
    ):
        _fail(code)
    for character in value:
        if unicodedata.category(character).startswith("C"):
            _fail(code)
    return value


def _policy_text(value, maximum, size_code):
    if not isinstance(value, str) or not value:
        _fail("content_type_invalid")
    # Bound the raw value before normalization as well as the exact value emitted.
    if _utf8_bytes(value) > maximum:
        _fail(size_code)
    normalized = unicodedata.normalize("NFC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized or _utf8_bytes(normalized) > maximum:
        _fail(size_code)
    for character in normalized:
        if (
            unicodedata.category(character).startswith("C")
            and character not in _SAFE_CONTROL_CHARACTERS
        ):
            _fail("content_control_character")
    if not normalized.strip():
        _fail("content_empty")
    return normalized


def _metadata_identity(
    value, *, expected_dataset_id, expected_name=None, expected_id=None,
):
    if not isinstance(value, dict):
        _fail("item_shape_invalid")
    if value.get("files") not in (None, []):
        _fail("attachments_forbidden")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        _fail("metadata_shape_invalid")
    dataset_id = metadata.get("dataset_id")
    document_id = metadata.get("document_id")
    document_name = metadata.get("document_name")
    if not all(isinstance(item, str) for item in (
        dataset_id, document_id, document_name,
    )):
        _fail("metadata_identity_missing")
    if dataset_id != expected_dataset_id:
        _fail("dataset_mismatch")
    if expected_name is not None and (
        document_name != expected_name or document_id != expected_id
    ):
        _fail("document_identity_mismatch")
    return document_name, document_id


def _context(value, query, role_id, correlation_id):
    if not isinstance(value, str) or _utf8_bytes(value) > _MAX_CONTEXT_PACK_UTF8_BYTES:
        _fail("context_pack_size_invalid")
    try:
        pack = json.loads(value, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except KnowledgeGuardError:
        raise
    except Exception:
        _fail("context_pack_json_invalid")
    if not isinstance(pack, dict) or set(pack) != _CONTEXT_FIELDS:
        _fail("context_pack_shape_invalid")
    if _canonical(pack) != value:
        _fail("context_pack_noncanonical")
    if pack.get("contract") != _CONTEXT_CONTRACT:
        _fail("context_contract_mismatch")
    if pack.get("use_case") != role_id:
        _fail("use_case_mismatch")
    if pack.get("request_id") != correlation_id:
        _fail("correlation_mismatch")
    pack_query = pack.get("query")
    if (
        not isinstance(pack_query, str)
        or not pack_query
        or _utf8_bytes(pack_query) > _MAX_QUERY_UTF8_BYTES
        or query != pack_query
    ):
        _fail("query_mismatch")
    manifest_digest = pack.get("knowledge_manifest_sha256")
    if (
        not isinstance(manifest_digest, str)
        or not _DIGEST.fullmatch(manifest_digest)
    ):
        _fail("manifest_digest_invalid")
    return pack


def _knowledge(retrieval_items, role_id):
    if (
        not isinstance(retrieval_items, list)
        or len(retrieval_items) > _MAX_KNOWLEDGE_ITEMS
    ):
        _fail("retrieval_count_invalid")
    allowed_names = set(_ALLOWED_DOCUMENT_NAMES_BY_ROLE[role_id])
    sanitized = []
    forbidden = False
    total = 0
    for item in retrieval_items:
        document_name, document_id = _metadata_identity(
            item, expected_dataset_id=_DATASET_ID,
        )
        bound_document_id = _DOCUMENT_IDS_BY_NAME.get(document_name)
        if bound_document_id is None or document_id != bound_document_id:
            _fail("document_identity_mismatch")
        content = _policy_text(
            item.get("content"), _MAX_KNOWLEDGE_ITEM_UTF8_BYTES,
            "knowledge_content_size_invalid",
        )
        total += _utf8_bytes(content)
        if total > _MAX_KNOWLEDGE_TOTAL_UTF8_BYTES:
            _fail("aggregate_content_size_invalid")
        source_name = _SOURCE_NAMES_BY_REMOTE_NAME.get(document_name)
        if not isinstance(source_name, str):
            _fail("document_source_mapping_missing")
        if document_name not in allowed_names:
            forbidden = True
        else:
            sanitized.append({"content": content, "document_name": source_name})
    if forbidden:
        # Every item has already passed identity, shape, size, and content checks.
        # Drop the entire retrieval projection so a mixed allowed/forbidden result
        # cannot disclose either document to the LLM.
        return [], "blocked"
    if not sanitized:
        return [], "needs_input"
    return sanitized, "allow"


def _sentinel(sentinel_items):
    if not isinstance(sentinel_items, list) or len(sentinel_items) != 1:
        _fail("sentinel_count_invalid")
    item = sentinel_items[0]
    _metadata_identity(
        item,
        expected_dataset_id=_SENTINEL_DATASET_ID,
        expected_name=_SENTINEL_DOCUMENT_NAME,
        expected_id=_SENTINEL_DOCUMENT_ID,
    )
    content = _policy_text(
        item.get("content"), _MAX_SENTINEL_UTF8_BYTES,
        "sentinel_content_size_invalid",
    )
    proofs = set(match.group(1).strip() for match in _PROOF.finditer(content))
    if len(proofs) != 1:
        _fail("knowledge_proof_unavailable")
    proof = next(iter(proofs))
    if len(proof) > 240:
        _fail("knowledge_proof_size_invalid")
    return proof


def _verify_executed_workflow(workflow_version, executed_workflow_id):
    if (
        not isinstance(executed_workflow_id, str)
        or not _WORKFLOW_UUID.fullmatch(executed_workflow_id)
    ):
        _fail("executed_workflow_id_invalid")
    if workflow_version != executed_workflow_id:
        _fail("workflow_execution_mismatch")


def _denied_result(status, role_id, workflow_version, knowledge_version, pack, proof):
    if status == "blocked":
        missing_inputs = []
        warnings = ["knowledge_guard:role_document_forbidden"]
    elif status == "needs_input":
        missing_inputs = ["role_knowledge"]
        warnings = ["knowledge_guard:no_role_knowledge_hit"]
    else:
        _fail("policy_status_invalid")
    return _canonical({
        "citations": [],
        "claims": [],
        "contract": _OUTPUT_CONTRACT,
        "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
        "knowledge_proof": proof,
        "knowledge_version": knowledge_version,
        "missing_inputs": missing_inputs,
        "payload": {},
        "payload_claims": [],
        "role_id": role_id,
        "status": status,
        "warnings": warnings,
        "workflow_version": workflow_version,
    })


def main(retrieval_items, sentinel_items, context_pack, query, role_id,
         correlation_id, output_contract, workflow_version,
         executed_workflow_id, knowledge_version):
    """Return one verified allow/deny branch decision and bounded output."""

    if not isinstance(role_id, str) or role_id not in _ALLOWED_DOCUMENT_NAMES_BY_ROLE:
        _fail("role_not_allowed_for_" + _WORKFLOW_KEY)
    _bounded_scalar(role_id, "role_invalid", _IDENTIFIER)
    _bounded_scalar(correlation_id, "correlation_invalid", _IDENTIFIER)
    if _bounded_scalar(output_contract, "output_contract_invalid") != _OUTPUT_CONTRACT:
        _fail("output_contract_mismatch")
    _bounded_scalar(workflow_version, "workflow_version_invalid", _WORKFLOW_UUID)
    _verify_executed_workflow(workflow_version, executed_workflow_id)
    _bounded_scalar(knowledge_version, "knowledge_version_invalid", _IDENTIFIER)
    if len(knowledge_version) > 120:
        _fail("knowledge_version_invalid")
    if not isinstance(query, str) or _utf8_bytes(query) > _MAX_QUERY_UTF8_BYTES:
        _fail("query_size_invalid")

    pack = _context(context_pack, query, role_id, correlation_id)
    proof = _sentinel(sentinel_items)
    knowledge, policy_status = _knowledge(retrieval_items, role_id)
    if policy_status != "allow":
        return {
            "denied_result": _denied_result(
                policy_status, role_id, workflow_version, knowledge_version,
                pack, proof,
            ),
            "knowledge_proof": proof,
            "model_input": "",
            "policy_decision": "deny",
        }
    model_input = _canonical({
        "context_pack": pack,
        "contract": _MODEL_INPUT_CONTRACT,
        "correlation_id": correlation_id,
        "knowledge_context": knowledge,
        "knowledge_proof": proof,
        "knowledge_version": knowledge_version,
        "output_contract": output_contract,
        "role_id": role_id,
        "workflow_version": workflow_version,
    })
    if _utf8_bytes(model_input) > _MAX_MODEL_INPUT_UTF8_BYTES:
        _fail("model_input_size_invalid")
    return {
        "denied_result": "",
        "knowledge_proof": proof,
        "model_input": model_input,
        "policy_decision": "allow",
    }
