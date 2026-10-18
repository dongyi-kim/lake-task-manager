"""Fail-closed guard template for a Dify 1.11.1 Python Code node.

The shared renderer replaces the canonical lexical helper import with its source,
producing a standard-library-only asset for each deployed DSL. The Code node binds the
LLM ``text`` output, all six Start variables, Dify's ``sys.workflow_id``, and the
Knowledge Guard's verified Release Sentinel proof to :func:`main`; its single declared
output is the string field ``result``.
"""

import hashlib
import html
import json
import math
import re

from app.agent.maxis.source_grounding import source_lexical_parts


_CONTRACT = "remote-role-result.v1"
_VALID_ROLES = {
    "request_architect",
    "query_specialist",
    "knowledge_curator",
    "portfolio_analyst",
    "work_architect",
    "auditor",
    "result_integrator",
    "editor_author",
}
_TOP_FIELDS = {
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
    "knowledge_proof",
}
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PUBLISHED_WORKFLOW_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_POINTER = re.compile(r"^/(?:[^~/]|~[01])+(?:/(?:[^~/]|~[01])+)*$")
_PROOF = re.compile(
    r"(?m)^[ \t]*(LTM_RELEASE_SENTINEL_V1=[^\r\n]{1,216})[ \t]*$"
)
_GROUND_TOKEN = re.compile(r"[0-9A-Za-z\uac00-\ud7a3]{2,}")
_USER_INTENT_CANONICAL_PREFIX = "ltm:user-intent:"
_CONTROL_FIELDS = {
    "contract", "role_id", "mode", "tier", "issue_type", "type", "kind",
    "finding_kind", "intent", "module", "playbook", "answer_depth", "field",
    "relation", "state", "status", "structure", "priority", "duedate",
    "source_id", "source_ids", "metric_id", "metric_ids", "id", "task_id",
    "depends_on", "anchor_key", "target_selector_id", "parent_ref", "parent",
    "epic", "epic_name", "key", "keys", "assignee", "components", "labels",
    "ref", "url", "target_id", "record_id", "meeting_assignment_ref",
    "outcome_refs", "outcome_contract_id", "user",
}
_AUTHORITY_KEYS = {
    "approved", "approval", "approvaltoken", "execute", "executiontoken",
    "authorized", "authorization", "credential", "credentials", "apikey",
    "accesstoken", "refreshtoken", "password", "secret", "commenttoken",
    "executionreceipt", "mutationreceipt", "questionreceiptprojection",
}
_AUTHORITY_KEY_PARTS = {
    "authorization", "bearer", "cookie", "credential", "credentials",
    "password", "passwd", "secret", "session", "sessionid", "token",
}
_AUTHORITY_KEY_MARKERS = {
    "accesstoken", "refreshtoken", "clientsecret", "apikey", "privatekey",
}
_SCHEMA_KEYS = {
    "$schema", "$id", "$ref", "$defs", "definitions", "$comment",
    "title", "description", "default", "examples", "deprecated", "readOnly",
    "writeOnly", "discriminator", "type", "const", "enum", "allOf", "anyOf",
    "oneOf", "not", "if", "then", "else", "properties", "required",
    "additionalProperties", "minProperties", "maxProperties", "items",
    "prefixItems", "minItems", "maxItems", "uniqueItems", "contains",
    "minContains", "maxContains", "minLength", "maxLength", "pattern", "format",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
}


class GuardError(Exception):
    """An internal, non-sensitive validation code."""


def _fail(code):
    raise GuardError(code)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(_value):
    _fail("nonfinite_json_number")


def _json_value(value, object_only=False):
    if isinstance(value, str):
        try:
            value = json.loads(
                value, object_pairs_hook=_pairs, parse_constant=_nonfinite
            )
        except GuardError:
            raise
        except Exception:
            _fail("invalid_json")
    if object_only and not isinstance(value, dict):
        _fail("json_object_required")
    return value


def _json_equal(left, right):
    return type(left) is type(right) and left == right


def _canonical(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except Exception:
        _fail("non_json_value")


def _strings(value, depth=0):
    if depth > 24:
        _fail("value_too_deep")
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, depth + 1)


def _proof_from(retrieval):
    proofs = set()
    for text in _strings(retrieval):
        proofs.update(match.group(1).strip() for match in _PROOF.finditer(text))
    if len(proofs) != 1:
        _fail("knowledge_proof_unavailable")
    return next(iter(proofs))


def _safe_text_list(value, limit):
    if not isinstance(value, list) or len(value) > limit:
        _fail("invalid_text_list")
    if any(not isinstance(row, str) or not row or len(row) > 500 for row in value):
        _fail("invalid_text_list")


def _safe_version(value, fallback):
    return value if isinstance(value, str) and 0 < len(value) <= 120 else fallback


def _verify_executed_workflow(workflow_version, executed_workflow_id):
    """Bind the caller's published UUID to Dify's execution-owned system value."""

    if (
        not isinstance(workflow_version, str)
        or not _PUBLISHED_WORKFLOW_UUID.fullmatch(workflow_version)
        or not isinstance(executed_workflow_id, str)
        or not _PUBLISHED_WORKFLOW_UUID.fullmatch(executed_workflow_id)
    ):
        _fail("workflow_id_invalid")
    if workflow_version != executed_workflow_id:
        _fail("workflow_execution_mismatch")


def _blocked(code, role_id, workflow_version, knowledge_version, pack, proof):
    digest = pack.get("knowledge_manifest_sha256") if isinstance(pack, dict) else None
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        digest = "0" * 64
    safe_role = role_id if role_id in _VALID_ROLES else "auditor"
    response = {
        "contract": _CONTRACT,
        "role_id": safe_role,
        "status": "blocked",
        "payload": {},
        "claims": [],
        "payload_claims": [],
        "citations": [],
        "missing_inputs": [],
        "warnings": ["contract_guard:" + str(code)[:460]],
        "workflow_version": _safe_version(workflow_version, "invalid-workflow-version"),
        "knowledge_version": _safe_version(knowledge_version, "invalid-knowledge-version"),
        "knowledge_manifest_sha256": digest,
        "knowledge_proof": proof if isinstance(proof, str) else "",
    }
    return {"result": _canonical(response)}


def _source_context(pack, role_id, correlation_id):
    if pack.get("contract") != "evidence-pack.v1":
        _fail("evidence_contract_mismatch")
    if pack.get("request_id") != correlation_id:
        _fail("correlation_mismatch")
    if pack.get("use_case") != role_id:
        _fail("use_case_mismatch")
    digest = pack.get("knowledge_manifest_sha256")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        _fail("manifest_digest_invalid")
    raw_schema = pack.get("payload_schema_json")
    schema_digest = pack.get("payload_schema_sha256")
    if not isinstance(raw_schema, str) or not isinstance(schema_digest, str):
        _fail("payload_schema_missing")
    if hashlib.sha256(raw_schema.encode("utf-8")).hexdigest() != schema_digest:
        _fail("payload_schema_digest_mismatch")
    schema = _json_value(raw_schema, object_only=True)
    if _canonical(schema) != raw_schema or schema.get("type") != "object":
        _fail("payload_schema_noncanonical")
    rows = pack.get("sources")
    if not isinstance(rows, list) or len(rows) > 10:
        _fail("sources_invalid")
    sources = {}
    intent_sources = set()
    for row in rows:
        if not isinstance(row, dict):
            _fail("source_invalid")
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not _IDENTIFIER.fullmatch(source_id):
            _fail("source_id_invalid")
        if source_id in sources:
            _fail("source_id_duplicate")
        sources[source_id] = [
            str(row.get(key) or "") for key in ("canonical_id", "title", "url")
        ]
        sources[source_id].extend(source_lexical_parts(str(row.get("content") or "")))
        if row.get("kind") == "ltm" and str(row.get("canonical_id") or "").startswith(
            _USER_INTENT_CANONICAL_PREFIX
        ):
            intent_sources.add(source_id)

    def source_ids(value, label):
        if not isinstance(value, list) or not 0 < len(value) <= 10 \
                or any(not isinstance(source_id, str) for source_id in value) \
                or len(set(value)) != len(value):
            _fail(label + "_source_ids_invalid")
        for source_id in value:
            if not _IDENTIFIER.fullmatch(source_id):
                _fail(label + "_source_ids_invalid")
            if source_id not in sources:
                _fail(label + "_source_unknown")
        return value

    facts = pack.get("facts")
    if not isinstance(facts, list) or len(facts) > 40:
        _fail("facts_invalid")
    fact_ids = set()
    for row in facts:
        if not isinstance(row, dict) or set(row) != {
            "fact_id", "text", "source_ids", "observed_at",
        }:
            _fail("fact_shape_invalid")
        fact_id = row["fact_id"]
        text = row["text"]
        if not isinstance(fact_id, str) or not _IDENTIFIER.fullmatch(fact_id):
            _fail("fact_id_invalid")
        if fact_id in fact_ids:
            _fail("fact_id_duplicate")
        if not isinstance(text, str) or not 0 < len(text) <= 1000:
            _fail("fact_text_invalid")
        observed_at = row["observed_at"]
        if not isinstance(observed_at, str) or not 0 < len(observed_at) <= 64:
            _fail("fact_observed_at_invalid")
        linked_sources = source_ids(row["source_ids"], "fact")
        for source_id in linked_sources:
            sources[source_id].append(text)
        fact_ids.add(fact_id)

    metrics = pack.get("metrics")
    if not isinstance(metrics, list) or len(metrics) > 40:
        _fail("metrics_invalid")
    metric_ids = set()
    for row in metrics:
        if not isinstance(row, dict) or set(row) != {
            "metric_id", "label", "value", "unit", "formula_version",
            "source_ids",
        }:
            _fail("metric_shape_invalid")
        metric_id = row["metric_id"]
        label = row["label"]
        value = row["value"]
        unit = row["unit"]
        formula_version = row["formula_version"]
        if not isinstance(metric_id, str) or not _IDENTIFIER.fullmatch(metric_id):
            _fail("metric_id_invalid")
        if metric_id in metric_ids:
            _fail("metric_id_duplicate")
        if not isinstance(label, str) or not 0 < len(label) <= 160:
            _fail("metric_label_invalid")
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(float(value)):
            _fail("metric_value_invalid")
        if not isinstance(unit, str) or len(unit) > 32:
            _fail("metric_unit_invalid")
        if not isinstance(formula_version, str) \
                or not 0 < len(formula_version) <= 80:
            _fail("metric_formula_version_invalid")
        linked_sources = source_ids(row["source_ids"], "metric")
        support = " ".join((label, str(value), unit))
        for source_id in linked_sources:
            sources[source_id].append(support)
        metric_ids.add(metric_id)

    return schema, {
        source_id: set().union(*(_ground_tokens(part) for part in parts))
        for source_id, parts in sources.items()
    }, intent_sources


def _resolve_ref(root, ref):
    if not isinstance(ref, str) or not ref.startswith("#/"):
        _fail("payload_schema_ref_unsupported")
    current = root
    for raw in ref[2:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            _fail("payload_schema_ref_missing")
        current = current[part]
    return current


def _schema_matches(value, schema, root, depth):
    try:
        _validate_schema_value(value, schema, root, depth)
        return True
    except GuardError:
        return False


def _validate_schema_value(value, schema, root, depth=0):
    if depth > 32:
        _fail("payload_schema_depth")
    if schema is True:
        return
    if schema is False or not isinstance(schema, dict):
        _fail("payload_schema_rejected")
    unknown = set(schema) - _SCHEMA_KEYS
    if unknown:
        _fail("payload_schema_keyword_unsupported")
    if "$ref" in schema:
        _validate_schema_value(value, _resolve_ref(root, schema["$ref"]), root, depth + 1)
    for branch in schema.get("allOf", []):
        _validate_schema_value(value, branch, root, depth + 1)
    if "anyOf" in schema and not any(
        _schema_matches(value, branch, root, depth + 1) for branch in schema["anyOf"]
    ):
        _fail("payload_anyof_mismatch")
    if "oneOf" in schema and sum(
        _schema_matches(value, branch, root, depth + 1) for branch in schema["oneOf"]
    ) != 1:
        _fail("payload_oneof_mismatch")
    if "not" in schema and _schema_matches(value, schema["not"], root, depth + 1):
        _fail("payload_not_mismatch")
    if "if" in schema:
        branch = "then" if _schema_matches(value, schema["if"], root, depth + 1) else "else"
        if branch in schema:
            _validate_schema_value(value, schema[branch], root, depth + 1)
    if "const" in schema and not _json_equal(value, schema["const"]):
        _fail("payload_const_mismatch")
    if "enum" in schema and not any(_json_equal(value, row) for row in schema["enum"]):
        _fail("payload_enum_mismatch")
    expected = schema.get("type")
    if expected is not None:
        expected = [expected] if isinstance(expected, str) else expected
        checks = {
            "null": value is None,
            "boolean": isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool)
                      and not (isinstance(value, float) and not math.isfinite(value)),
            "string": isinstance(value, str),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
        }
        if not isinstance(expected, list) or not any(checks.get(row, False) for row in expected):
            _fail("payload_type_mismatch")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            _fail("payload_schema_properties_invalid")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(key not in value for key in required):
            _fail("payload_required_missing")
        if len(value) < schema.get("minProperties", 0):
            _fail("payload_properties_too_few")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            _fail("payload_properties_too_many")
        extra = set(value) - set(properties)
        additional = schema.get("additionalProperties", True)
        if additional is False and extra:
            _fail("payload_additional_property")
        for key, item in value.items():
            child = properties.get(key, additional if isinstance(additional, dict) else None)
            if child is not None:
                _validate_schema_value(item, child, root, depth + 1)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            _fail("payload_items_too_few")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            _fail("payload_items_too_many")
        if schema.get("uniqueItems") and len({_canonical(row) for row in value}) != len(value):
            _fail("payload_items_not_unique")
        prefix = schema.get("prefixItems", [])
        if prefix and not isinstance(prefix, list):
            _fail("payload_prefix_items_invalid")
        for index, child in enumerate(prefix):
            if index < len(value):
                _validate_schema_value(value[index], child, root, depth + 1)
        items = schema.get("items")
        start = len(prefix)
        if items is False and len(value) > start:
            _fail("payload_extra_items")
        if isinstance(items, dict):
            for item in value[start:]:
                _validate_schema_value(item, items, root, depth + 1)
        if "contains" in schema:
            count = sum(_schema_matches(item, schema["contains"], root, depth + 1) for item in value)
            if count < schema.get("minContains", 1) or (
                "maxContains" in schema and count > schema["maxContains"]
            ):
                _fail("payload_contains_mismatch")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            _fail("payload_string_too_short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            _fail("payload_string_too_long")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except Exception:
                _fail("payload_schema_pattern_unsupported")
            if not matched:
                _fail("payload_pattern_mismatch")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            _fail("payload_number_too_small")
        if "maximum" in schema and value > schema["maximum"]:
            _fail("payload_number_too_large")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            _fail("payload_number_too_small")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            _fail("payload_number_too_large")
        if "multipleOf" in schema:
            quotient = value / schema["multipleOf"]
            if not math.isclose(quotient, round(quotient), rel_tol=1e-12, abs_tol=1e-12):
                _fail("payload_multiple_mismatch")


def _pointer_segment(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def _control_field(field):
    return (
        field in _CONTROL_FIELDS or field.endswith("_id") or field.endswith("_ids")
        or field.endswith("_ref") or field.endswith("_refs")
    )


def _source_free_control_payload(payload, schema):
    """Allow only a closed, schema-owned control object without source evidence."""

    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list) \
            or schema.get("type") != "object" \
            or schema.get("additionalProperties") is not False \
            or any(not isinstance(field, str) for field in required) \
            or len(required) != len(set(required)) \
            or set(required) != set(properties) \
            or not isinstance(payload, dict) or set(payload) != set(properties):
        return False
    for field, field_schema in properties.items():
        if not isinstance(field, str) or not _control_field(field) \
                or not isinstance(field_schema, dict) or "default" in field_schema:
            return False
        has_const = "const" in field_schema
        enum = field_schema.get("enum")
        has_single_enum = isinstance(enum, list) and len(enum) == 1
        if has_const == has_single_enum:
            return False
        expected = field_schema["const"] if has_const else enum[0]
        if isinstance(expected, (dict, list)) \
                or not _json_equal(payload[field], expected):
            return False
    return True


def _narratives(value, path="", field=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _narratives(item, path + "/" + _pointer_segment(key), str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _narratives(item, path + "/" + str(index), field)
    elif isinstance(value, str) and value.strip() and not _control_field(field):
        yield path, value


def _resolve_pointer(value, pointer):
    current = value
    for raw in pointer[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and re.fullmatch(r"0|[1-9][0-9]*", part):
            index = int(part)
            if index >= len(current):
                _fail("payload_claim_path_invalid")
            current = current[index]
        else:
            _fail("payload_claim_path_invalid")
    return current


def _visible(value):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(value))).split())


def _ground_tokens(value):
    return set(_GROUND_TOKEN.findall(_visible(value).casefold()))


def _forbidden_authority_key(value):
    """Mirror LTM's separator/case-agnostic capability and secret-key guard."""
    raw = str(value or "")
    camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw)
    parts = tuple(
        part for part in re.split(r"[^a-z0-9]+", camel_split.casefold()) if part
    )
    normalized = "".join(parts)
    if normalized in _AUTHORITY_KEYS:
        return True
    if any(part in _AUTHORITY_KEY_PARTS for part in parts):
        return True
    return any(marker in normalized for marker in _AUTHORITY_KEY_MARKERS)


def _authority_walk(value, depth=0):
    if depth > 24:
        _fail("candidate_too_deep")
    if isinstance(value, dict):
        for key, item in value.items():
            if _forbidden_authority_key(key):
                _fail("forbidden_authority_field")
            _authority_walk(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _authority_walk(item, depth + 1)


def _validate_candidate(candidate, pack, schema, sources, intent_sources,
                        role_id, output_contract,
                        workflow_version, knowledge_version, proof):
    if set(candidate) != _TOP_FIELDS:
        _fail("response_fields_mismatch")
    if candidate.get("contract") != _CONTRACT or output_contract != _CONTRACT:
        _fail("response_contract_mismatch")
    expected = {
        "role_id": role_id,
        "workflow_version": workflow_version,
        "knowledge_version": knowledge_version,
        "knowledge_manifest_sha256": pack["knowledge_manifest_sha256"],
        "knowledge_proof": proof,
    }
    if any(candidate.get(key) != value for key, value in expected.items()):
        _fail("response_echo_mismatch")
    if role_id not in _VALID_ROLES:
        _fail("role_invalid")
    status = candidate.get("status")
    payload = candidate.get("payload")
    if status not in {"ok", "needs_input", "blocked"} or not isinstance(payload, dict):
        _fail("status_or_payload_invalid")
    claims = candidate.get("claims")
    bindings = candidate.get("payload_claims")
    citations = candidate.get("citations")
    if not isinstance(claims, list) or len(claims) > 40:
        _fail("claims_invalid")
    if not isinstance(bindings, list) or len(bindings) > 256:
        _fail("payload_claims_invalid")
    if not isinstance(citations, list) or len(citations) > 40:
        _fail("citations_invalid")
    if not sources and (claims or bindings or citations):
        # A schema-defined control-only payload is valid without evidence, but no
        # claim kind (including open_fact), citation, or payload binding may cross
        # the trust boundary when the caller supplied no sources.
        _fail("source_free_provenance_forbidden")
    if not sources and status == "ok" \
            and not _source_free_control_payload(payload, schema):
        _fail("source_free_payload_forbidden")
    _safe_text_list(candidate.get("missing_inputs"), 8)
    _safe_text_list(candidate.get("warnings"), 16)
    if status == "ok" and candidate["warnings"]:
        _fail("ok_has_warnings")
    if status == "needs_input" and not candidate["missing_inputs"]:
        _fail("missing_inputs_required")
    if status == "ok" and candidate["missing_inputs"]:
        _fail("ok_has_missing_inputs")
    if status != "ok" and payload:
        _fail("non_ok_payload_not_empty")
    if status != "ok" and (claims or bindings or citations):
        _fail("non_ok_provenance_not_empty")
    _authority_walk(candidate)
    for text in _strings({"payload": payload, "claims": claims, "citations": citations}):
        if "LTM_RELEASE_SENTINEL_V1=" in text:
            _fail("knowledge_proof_leaked")

    citation_map = {}
    for row in citations:
        if not isinstance(row, dict) or set(row) != {"citation_id", "source_id", "locator"}:
            _fail("citation_shape_invalid")
        citation_id = row["citation_id"]
        source_id = row["source_id"]
        locator = row["locator"]
        if not isinstance(citation_id, str) or not _IDENTIFIER.fullmatch(citation_id):
            _fail("citation_id_invalid")
        if citation_id in citation_map:
            _fail("citation_id_duplicate")
        if source_id not in sources:
            _fail("citation_source_unknown")
        if not isinstance(locator, str) or len(locator) > 240:
            _fail("citation_locator_invalid")
        if locator:
            _fail("citation_locator_forbidden")
        citation_map[citation_id] = source_id

    claim_map = {}
    for row in claims:
        if not isinstance(row, dict) or set(row) != {"claim_id", "kind", "text", "citation_ids"}:
            _fail("claim_shape_invalid")
        claim_id = row["claim_id"]
        kind = row["kind"]
        text = row["text"]
        citation_ids = row["citation_ids"]
        if not isinstance(claim_id, str) or not _IDENTIFIER.fullmatch(claim_id):
            _fail("claim_id_invalid")
        if claim_id in claim_map:
            _fail("claim_id_duplicate")
        if kind not in {"fact", "inference", "recommendation", "open_fact"}:
            _fail("claim_kind_invalid")
        if not isinstance(text, str) or not 0 < len(text) <= 1200:
            _fail("claim_text_invalid")
        if not isinstance(citation_ids, list) or len(citation_ids) > 8 \
                or len(set(citation_ids)) != len(citation_ids):
            _fail("claim_citations_invalid")
        if any(citation_id not in citation_map for citation_id in citation_ids):
            _fail("claim_citation_unknown")
        if kind in {"fact", "inference", "recommendation"} and not citation_ids:
            _fail("claim_citation_required")
        if kind == "fact" and any(
            citation_map[citation_id] in intent_sources
            for citation_id in citation_ids
        ):
            _fail("intent_source_fact_forbidden")
        if kind == "fact":
            support = set().union(*(sources[citation_map[row_id]] for row_id in citation_ids))
            tokens = _ground_tokens(text)
            overlap = tokens & support
            required = max(1, (len(tokens) * 7 + 9) // 10)
            if not tokens or len(overlap) < required:
                _fail("claim_not_grounded")
        claim_map[claim_id] = row

    binding_map = {}
    for row in bindings:
        if not isinstance(row, dict) or set(row) != {"path", "claim_ids"}:
            _fail("payload_claim_shape_invalid")
        pointer = row["path"]
        claim_ids = row["claim_ids"]
        if not isinstance(pointer, str) or not _POINTER.fullmatch(pointer):
            _fail("payload_claim_path_invalid")
        if pointer in binding_map:
            _fail("payload_claim_path_duplicate")
        if not isinstance(claim_ids, list) or not 0 < len(claim_ids) <= 40 \
                or len(set(claim_ids)) != len(claim_ids):
            _fail("payload_claim_ids_invalid")
        if any(claim_id not in claim_map for claim_id in claim_ids):
            _fail("payload_claim_unknown")
        binding_map[pointer] = claim_ids

    if status == "ok":
        _validate_schema_value(payload, schema, schema)
        if payload and sources and not claims:
            _fail("payload_claims_required")
        narratives = dict(_narratives(payload))
        missing = sorted(set(narratives) - set(binding_map))
        if missing:
            _fail("payload_claim_coverage_missing")
        for pointer, claim_ids in binding_map.items():
            pointed = _resolve_pointer(payload, pointer)
            if not isinstance(pointed, str) or not pointed.strip():
                _fail("payload_claim_path_not_text")
            if any(not claim_map[row_id]["citation_ids"] for row_id in claim_ids):
                _fail("payload_claim_citation_required")
            support = _visible(" ".join(claim_map[row_id]["text"] for row_id in claim_ids))
            assertion = _visible(pointed)
            if assertion != support and assertion not in support:
                _fail("payload_claim_text_mismatch")


def main(candidate, context_pack, role_id, correlation_id, output_contract,
         workflow_version, executed_workflow_id, knowledge_version, sentinel_result):
    """Return ``{'result': '<remote-role-result.v1 JSON>'}`` without raising.

    Any validation or runtime failure is converted to a deterministic blocked response.
    The response never copies untrusted candidate content or retrieved knowledge text.
    """

    pack = {}
    proof = ""
    try:
        pack = _json_value(context_pack, object_only=True)
        _verify_executed_workflow(workflow_version, executed_workflow_id)
        proof = _proof_from(sentinel_result)
        schema, sources, intent_sources = _source_context(
            pack, role_id, correlation_id,
        )
        parsed = _json_value(candidate, object_only=True)
        _validate_candidate(
            parsed, pack, schema, sources, intent_sources,
            role_id, output_contract,
            workflow_version, knowledge_version, proof,
        )
        return {"result": _canonical(parsed)}
    except GuardError as exc:
        return _blocked(str(exc), role_id, workflow_version, knowledge_version, pack, proof)
    except Exception:
        return _blocked("internal_validation_error", role_id, workflow_version,
                        knowledge_version, pack, proof)
