"""CREATE Work v2 view; original input still owns acceptance.

Prepend unchanged draft validation/context and bind the existing schema constants.
Entity references are display metadata, never replacement citation identities.
"""
_WORK_SCHEMA_JSON = None
_WORK_SCHEMA_SHA256 = None
_DRAFT_SCHEMA = None
_MAX_WORK_INPUT_UTF8_BYTES = 40000
_HIDDEN_PACK_FIELDS = {"contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256"}
_PACK_STRINGS = {"contract", "request_id", "use_case", "query", "pseudonymous_user", "requested_at",
                 "freshness", "knowledge_manifest_sha256", "payload_schema_json", "payload_schema_sha256"}
_SOURCE_STRINGS = {"source_id", "kind", "canonical_id", "title", "scope", "url", "content",
                   "retrieved_at", "content_sha256"}
_CONTEXT_STRINGS = {"context_id", "name", "content_json", "observed_at", "content_sha256"}


def _fail():
    raise ValueError("invalid_core_work_input")


def _intent(row):
    # Exact predicate in both existing final guards, not source_id or body text.
    return row["kind"] == "ltm" and row["canonical_id"].startswith("ltm:user-intent:")


def _source_view(row):
    if (type(row) is not dict or set(row) != _SOURCE_STRINGS | {"updated_at", "partial", "truncated"}
            or any(type(row[key]) is not str for key in _SOURCE_STRINGS)
            or row["kind"] not in {"jira", "confluence", "bitbucket", "knowledge", "ltm"}
            or not 1 <= len(row["canonical_id"]) <= 240 or not 1 <= len(row["title"]) <= 240
            or row["updated_at"] is not None and type(row["updated_at"]) is not str
            or type(row["partial"]) is not bool or type(row["truncated"]) is not bool):
        _fail()
    intent = _intent(row)
    visible = {key: value for key, value in row.items() if key not in {"content_sha256", "canonical_id"}}
    if not intent:
        visible["entity_reference"] = row["canonical_id"]
    return dict(visible, source_authority="user_intent" if intent else "evidence_source",
                allowed_claim_kinds=["inference", "recommendation"] if intent
                else ["fact", "inference", "recommendation"])


def _request_text(value, sources):
    if type(value) is str:
        return  # Even JSON-looking business text is opaque, never a reference.
    if (type(value) is not dict or set(value) != {"source_content_index"}
            or type(value["source_content_index"]) is not int
            or not 0 <= value["source_content_index"] < len(sources)
            or not _intent(sources[value["source_content_index"]])):
        _fail()


def _check_context(rows, sources):
    if len(rows) > 8:
        _fail()
    ids, names = set(), set()
    for row in rows:
        if (type(row) is not dict or set(row) != _CONTEXT_STRINGS | {"partial"}
                or any(type(row[key]) is not str for key in _CONTEXT_STRINGS)
                or type(row["partial"]) is not bool
                or any(not _ID.fullmatch(row[key]) for key in ("context_id", "name"))
                or row["context_id"] in ids or row["name"] in names
                or _digest(row["content_json"]) != row["content_sha256"]):
            _fail()
        ids.add(row["context_id"])
        names.add(row["name"])
        raw = row["content_json"]
        # Shared decoder accepts objects; a wrapper preserves any JSON root type.
        value = _decode('{"value":' + raw + '}', 16400, _fail)["value"]
        if not raw or len(raw) > 8192 or len(raw.encode("utf-8")) > 16384 or _canonical(value) != raw:
            _fail()
        if row["name"] != "request_plan":
            continue
        if type(value) is not dict:
            _fail()
        if "goal" in value:
            _request_text(value["goal"], sources)
        if "tasks" in value:
            if type(value["tasks"]) is not list or any(type(task) is not dict for task in value["tasks"]):
                _fail()
            for task in value["tasks"]:
                if "instruction" in task:
                    _request_text(task["instruction"], sources)


def main(model_input):
    try:
        incoming, pack, _schema, _sources = _draft_context(model_input, "work_architect",
            _WORK_SCHEMA_JSON, _WORK_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)
        if (any(type(pack[key]) is not str for key in _PACK_STRINGS)
                or any(type(pack[key]) is not list for key in ("sources", "facts", "metrics", "role_context", "flags"))
                or type(pack["scope"]) is not dict or type(pack["partial"]) is not bool
                or type(incoming["knowledge_context"]) is not list):
            _fail()
        context = {key: value for key, value in pack.items() if key not in _HIDDEN_PACK_FIELDS}
        context["sources"] = [_source_view(row) for row in pack["sources"]]
        _check_context(pack["role_context"], pack["sources"])
        view = _canonical({"contract": "work-input.v2", "context": context,
                           "knowledge_context": incoming["knowledge_context"]})
        if len(view.encode("utf-8")) > _MAX_WORK_INPUT_UTF8_BYTES:
            _fail()
        return {"work_input": view}
    except Exception:
        raise ValueError("invalid_core_work_input") from None
