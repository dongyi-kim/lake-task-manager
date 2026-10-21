"""Stdlib-only Core unit classifier; never an authorization decision.

The caller must supply Knowledge Guard's bounded model input after policy allow.
This unit neither verifies policy/proof nor validates ordinary business evidence.
It emits only a route, never a model response, citation, or successful canary.
"""
import hashlib
import json
import re
from datetime import datetime


_INPUT_FIELDS = {"contract", "context_pack", "role_id", "correlation_id", "output_contract",
                 "workflow_version", "knowledge_version", "knowledge_proof", "knowledge_context"}
_NONCE = re.compile(r"probe_agent_core_([0-9a-f]{12})")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_UTC_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z")
# The pure graph builder appends the canonical CompactQueryPlan JSON and digest.
# An unbound source cannot silently send query requests to the generic model.
_QUERY_SCHEMA_JSON = None
_QUERY_SCHEMA_SHA256 = None
_REQUEST_SCHEMA_JSON = None
_REQUEST_SCHEMA_SHA256 = None
_WORK_SCHEMA_JSON = None
_WORK_SCHEMA_SHA256 = None
_KNOWLEDGE_SCHEMA_JSON = None
_KNOWLEDGE_SCHEMA_SHA256 = None
_TEXT_SCHEMA_JSON = None
_TEXT_SCHEMA_SHA256 = None
_AUDIT_SCHEMA_JSON = None
_AUDIT_SCHEMA_SHA256 = None


def _fail():
    raise ValueError("invalid_core_control_input")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pairs(rows):
    value = {}
    for key, item in rows:
        if key in value:
            _fail()
        value[key] = item
    return value


def _text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > maximum:
        _fail()
    return value


def _reserved(pack):
    """Reserved identity metadata only; never inspect query or Knowledge text."""
    for field in ("sources", "role_context"):
        rows = pack.get(field)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            _fail()
    request = pack.get("request_id")
    if isinstance(request, str) and request.startswith("probe_"):
        return True
    for source in pack.get("sources", []):
        if isinstance(source, dict):
            identity = source.get("canonical_id")
            if isinstance(identity, str) and identity.startswith("ltm:maxis-probe:"):
                return True
    return any(isinstance(row, dict) and row.get("name") == "activation_probe"
               for row in pack.get("role_context", []))


def _activation(incoming, pack):
    match = _NONCE.fullmatch(_text(pack.get("request_id"), 128))
    if (not match or incoming["role_id"] != "knowledge_curator"
            or incoming["correlation_id"] != pack["request_id"]
            or incoming["output_contract"] != "remote-role-result.v1"
            or not _UUID.fullmatch(_text(incoming["workflow_version"], 120))
            or not _ID.fullmatch(_text(incoming["knowledge_version"], 120))):
        _fail()
    _text(incoming["knowledge_proof"], 240)  # Shape only; not Sentinel authentication.
    nonce = match.group(1)
    when = _text(pack.get("requested_at"), 80)
    if not _UTC_TIME.fullmatch(when):
        _fail()
    # Calendar validity only; avoid Python-version-specific ISO 'Z' parsing.
    datetime(int(when[:4]), int(when[5:7]), int(when[8:10]),
             int(when[11:13]), int(when[14:16]), int(when[17:19]))
    manifest = _text(pack.get("knowledge_manifest_sha256"), 64)
    if not _DIGEST.fullmatch(manifest):
        _fail()
    source_id = "probe_source_" + nonce
    content = "The supplied synthetic activation canary has state ready."
    context = _canonical({"expected_state": "ready", "probe_kind": "synthetic_contract", "workflow": "agent_core"})
    schema = _canonical({"type": "object", "additionalProperties": False,
                         "properties": {"state": {"type": "string", "const": "ready"}}, "required": ["state"]})
    expected = {
        "contract": "evidence-pack.v1", "request_id": "probe_agent_core_" + nonce,
        "use_case": "knowledge_curator", "query": _text(pack.get("query"), 1024),
        "pseudonymous_user": "ltm_" + nonce + "0000", "requested_at": when,
        "scope": {"projects": [], "spaces": [], "repositories": []},
        "sources": [{"source_id": source_id, "kind": "ltm", "canonical_id": "ltm:maxis-probe:agent_core",
            "title": "Synthetic MAXIS activation canary", "scope": "", "url": "", "content": content,
            "retrieved_at": when, "updated_at": None, "content_sha256": _digest(content),
            "partial": False, "truncated": False}],
        "facts": [{"fact_id": "probe_fact_" + nonce, "text": content,
                   "source_ids": [source_id], "observed_at": when}],
        "metrics": [], "role_context": [{"context_id": "probe_context_" + nonce,
            "name": "activation_probe", "content_json": context, "content_sha256": _digest(context),
            "observed_at": when, "partial": False}],
        "freshness": "fresh", "partial": False, "flags": [],
        "knowledge_manifest_sha256": manifest,
        "payload_schema_json": schema, "payload_schema_sha256": _digest(schema),
    }
    # JSON comparison also rejects Python's bool/int aliases and extra fields.
    if _canonical(pack) != _canonical(expected):
        _fail()


def main(model_input):
    """Return semantic/control/query/request/work/knowledge/result; malformed inputs fail.

    No I/O, clock reads, mutation, evidence rewriting, or fallback repair. A semantic
    result says only 'not this activation'; all existing validation remains required.
    """
    try:
        if not isinstance(model_input, str) or len(model_input.encode("utf-8")) > 40000:
            _fail()
        incoming = json.loads(model_input, object_pairs_hook=_pairs, parse_constant=lambda _: _fail())
        if (not isinstance(incoming, dict) or set(incoming) != _INPUT_FIELDS
                or incoming["contract"] != "ltm-model-input.v1" or _canonical(incoming) != model_input):
            _fail()
        pack = incoming["context_pack"]
        if not isinstance(pack, dict) or len(_canonical(pack).encode("utf-8")) > 32768:
            _fail()
        if not _reserved(pack):
            if incoming["role_id"] == "auditor" or pack.get("use_case") == "auditor":
                if (incoming["role_id"] != "auditor" or pack.get("use_case") != "auditor"
                        or not isinstance(_AUDIT_SCHEMA_JSON, str)
                        or _digest(_AUDIT_SCHEMA_JSON) != _AUDIT_SCHEMA_SHA256
                        or pack.get("payload_schema_json") != _AUDIT_SCHEMA_JSON
                        or pack.get("payload_schema_sha256") != _AUDIT_SCHEMA_SHA256):
                    _fail()
                return {"route": "audit"}
            if incoming["role_id"] == "result_integrator" or pack.get("use_case") == "result_integrator":
                if (incoming["role_id"] != "result_integrator" or pack.get("use_case") != "result_integrator"
                        or not isinstance(_TEXT_SCHEMA_JSON, str)
                        or _digest(_TEXT_SCHEMA_JSON) != _TEXT_SCHEMA_SHA256
                        or pack.get("payload_schema_json") != _TEXT_SCHEMA_JSON
                        or pack.get("payload_schema_sha256") != _TEXT_SCHEMA_SHA256):
                    _fail()
                return {"route": "result"}
            if incoming["role_id"] == "knowledge_curator" or pack.get("use_case") == "knowledge_curator":
                if (incoming["role_id"] != "knowledge_curator" or pack.get("use_case") != "knowledge_curator"
                        or not isinstance(_KNOWLEDGE_SCHEMA_JSON, str)
                        or _digest(_KNOWLEDGE_SCHEMA_JSON) != _KNOWLEDGE_SCHEMA_SHA256
                        or pack.get("payload_schema_json") != _KNOWLEDGE_SCHEMA_JSON
                        or pack.get("payload_schema_sha256") != _KNOWLEDGE_SCHEMA_SHA256):
                    _fail()
                return {"route": "knowledge"}
            if incoming["role_id"] == "query_specialist":
                if (pack.get("use_case") != "query_specialist"
                        or not isinstance(_QUERY_SCHEMA_JSON, str)
                        or _digest(_QUERY_SCHEMA_JSON) != _QUERY_SCHEMA_SHA256
                        or pack.get("payload_schema_json") != _QUERY_SCHEMA_JSON
                        or pack.get("payload_schema_sha256") != _QUERY_SCHEMA_SHA256):
                    _fail()
                return {"route": "query"}
            if incoming["role_id"] == "request_architect" or pack.get("use_case") == "request_architect":
                if (incoming["role_id"] != "request_architect" or pack.get("use_case") != "request_architect"
                        or not isinstance(_REQUEST_SCHEMA_JSON, str)
                        or _digest(_REQUEST_SCHEMA_JSON) != _REQUEST_SCHEMA_SHA256
                        or pack.get("payload_schema_json") != _REQUEST_SCHEMA_JSON
                        or pack.get("payload_schema_sha256") != _REQUEST_SCHEMA_SHA256):
                    _fail()
                return {"route": "request"}
            if incoming["role_id"] == "work_architect" or pack.get("use_case") == "work_architect":
                if (incoming["role_id"] != "work_architect" or pack.get("use_case") != "work_architect"
                        or not isinstance(_WORK_SCHEMA_JSON, str)
                        or _digest(_WORK_SCHEMA_JSON) != _WORK_SCHEMA_SHA256):
                    _fail()
                raw = pack.get("payload_schema_json")
                digest = pack.get("payload_schema_sha256")
                if not isinstance(raw, str) or _digest(raw) != digest:
                    _fail()
                schema = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _fail())
                if not isinstance(schema, dict) or _canonical(schema) != raw:
                    _fail()
                if raw == _WORK_SCHEMA_JSON and digest == _WORK_SCHEMA_SHA256:
                    return {"route": "work"}
                if raw == _WORK_SCHEMA_JSON or digest == _WORK_SCHEMA_SHA256:
                    _fail()
                # Other valid Work modes retain their existing semantic path.
            return {"route": "semantic"}
        _activation(incoming, pack)
        return {"route": "control"}
    except Exception:
        raise ValueError("invalid_core_control_input") from None
