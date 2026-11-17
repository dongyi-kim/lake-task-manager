"""Small, independently cached Jira pages for complete raw body projections.

Never build a whole-query snapshot for descriptions: ten large tickets already
make a meaningful physical page. Requested max_results remains advisory. Bodies
are never truncated; callers can lower max_results when a page exceeds 16 MiB.
"""
import hashlib
import json
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from app.auth.base import QueryStateChanged
from app.infra.json_payload import encoded_size
from app.infra.resource_pressure import resource_pressure
from app.jira.jql import JqlUnsupported, _split_order, tokenize
from app.jira.jql_budget import current_budget, validate_max_parts
from app.jira.ticket_body_cache import TicketBodyCache


BODY_PAGE_SIZE = 10
DISCOVERY_PAGE_SIZE = 100
_known_keys = ContextVar("body_search_known_keys", default=None)
MAX_BODY_RESULT_BYTES = 16 * 1024 * 1024
BODY_PAGE_LIMIT_MESSAGE = (
    "Description search page exceeds 16 MiB. Use search_issues(max_results=1, "
    "include_description=true) pagination. For one oversized body use get_ticket(detail='read').")
BODY_BATCH_LIMIT_MESSAGE = (
    "Ticket batch exceeds 16 MiB. Use fewer keys or search_issues(max_results=1, "
    "include_description=true) pagination. For one oversized body use get_ticket(detail='read').")


def is_body_projection(fields):
    return bool({field.strip().lower() for field in str(fields or "").split(",")}
                & {"description", "*all", "*navigable"})


@contextmanager
def known_key_search(keys):
    """Internal get_tickets hint; never exposed in the MCP input schema."""
    token = _known_keys.set({"keys": tuple(keys) if len(keys) <= BODY_PAGE_SIZE else None,
                            "cold": None, "state": None})
    try:
        yield
    finally:
        _known_keys.reset(token)


def _without_body(issue):
    result = {**issue, "fields": {key: value for key, value in issue["fields"].items() if key != "description"}}
    if isinstance(result.get("renderedFields"), dict):
        result["renderedFields"] = {key: value for key, value in result["renderedFields"].items() if key != "description"}
    return result


def _session_revision(client):
    # Silent Jira renewal can keep the same provider while replacing its cookies.
    # File stat revisions include the shared identity state and need no /myself.
    return client.sso_store().revision("jira") if client.env == "prod" else None


def _provider_session(client):
    provider = client.provider  # Initial construction advances its generation.
    with client._provider_lock:
        if provider is not client._provider:
            raise QueryStateChanged("인증 상태가 갱신되었습니다. 검색을 다시 시도해 주세요.")
        nonce = getattr(client, "_body_search_nonce", None)
        if nonce is None:
            nonce = client._body_search_nonce = uuid4().hex
        generation = client._provider_generation
    return provider, (nonce, generation, _session_revision(client))


def _assert_session(client, provider, session):
    with client._provider_lock:
        changed = provider is not client._provider or session[1] != client._provider_generation
    if changed or session[2] != _session_revision(client):
        raise QueryStateChanged("인증 상태가 갱신되었습니다. 검색을 다시 시도해 주세요.")


def search_body_page(client, jql, *, start_at=0, max_results=100, fields,
                     light=True, snapshot_id=None, strategy="auto", max_parts=64):
    """Return one physical Jira page with no normalized membership hydration."""
    validate_max_parts(max_parts)
    if strategy not in ("auto", "off", "people", "full"):
        raise ValueError("strategy must be auto, off, people or full")
    if snapshot_id:
        raise ValueError("Description searches do not use a frozen snapshot. Restart from the first page without snapshot_id.")
    start = max(0, int(start_at or 0))
    size = max(1, min(int(max_results or 100), BODY_PAGE_SIZE))
    query = str(jql or "").strip().rstrip(";").strip()
    try:
        _where, order = _split_order(tokenize(query))
    except JqlUnsupported:
        pass  # Jira still owns unsupported grammar and its ordering semantics.
    else:
        if not order:
            query += " ORDER BY key ASC"
    provider, session = _provider_session(client)
    try:
        # Resolve actor/relative-time context without DNF, memberships or snapshots.
        # Ordinary JQL does not require an identity lookup in _compile_jql.
        canonical = client._compile_jql(query, strategy="off").canonical
    except JqlUnsupported:
        canonical = query
    _assert_session(client, provider, session)
    fence, generation = client.cache.fence(), client._jql_generation()
    actor = (client.current_user() or {}).get("id") if re.search(r"\bcurrentUser\s*\(", query, re.I) else None
    context = [client._jql_user_context(), session, generation, actor]
    scope = hashlib.sha256(json.dumps(context, separators=(",", ":")).encode()).hexdigest()
    bodies = TicketBodyCache(client.cache, scope, client.s.cache_ttl_seconds, fence)
    requested = [field.strip() for field in fields.split(",") if field.strip()]
    wildcard = bool(set(field.lower() for field in requested) & {"*all", "*navigable"})
    body_fields = fields if "updated" in requested or wildcard else fields + ",updated"
    metadata_fields = ",".join(field for field in requested if field.lower() != "description")
    if "updated" not in metadata_fields.split(","):
        metadata_fields += ("," if metadata_fields else "") + "updated"
    hint = _known_keys.get()
    known = hint["keys"] if hint is not None else None
    if hint is not None:
        state = (scope, fence)
        if hint["state"] is None:
            hint["state"] = state
        elif hint["state"] != state:
            raise QueryStateChanged("다건 조회 중 인증 상태 또는 티켓이 변경되었습니다. 다시 조회하세요.")
    if known is not None and hint["cold"] is None:
        hint["cold"] = not bodies.covered(known)
    direct = (wildcard or max_parts == 1 or (hint is not None and hint["cold"])
              or bool(set(field.lower() for field in requested) & {"attachment", "issuelinks"}))
    # Explicitly smaller output pages also narrow discovery, so max_results=1
    # can recover when otherwise-light custom metadata turns out to be enormous.
    physical_size = size if direct or size < BODY_PAGE_SIZE else DISCOVERY_PAGE_SIZE
    fresh_rows = {}

    def check():
        _assert_session(client, provider, session)
        if client.cache.fence() != fence or client._jql_generation() != generation:
            raise QueryStateChanged("티켓이 변경되어 검색 결과를 다시 확인해야 합니다.")

    def fetch(physical_query, physical_fields, offset, limit):
        check()
        resource_pressure.check()
        started = time.time()
        page = client._legacy_search_issues_page(physical_query, start_at=offset, max_results=limit,
                                                fields=physical_fields, light=light, write_through=False)
        if encoded_size(page) > MAX_BODY_RESULT_BYTES:
            raise ValueError(BODY_PAGE_LIMIT_MESSAGE)
        check()
        return page, started

    def discovery_cache_key(offset, limit, combined):
        identity = [scope, canonical, metadata_fields, bool(light), offset, limit, combined]
        digest = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        return f"search:body-discovery:v1:{client.env}:{digest}"

    def discovery(offset, limit_hint=None):
        if known is not None and not direct:
            # The initial all-cold shortcut already observed this query's metadata.
            # Reuse it when switching to missing-only hydration on subsequent reads.
            prior_key = discovery_cache_key(offset, size, True)
            prior = client.cache.get(prior_key)
            if prior is not None:
                check()
                return prior, prior_key
        key = discovery_cache_key(offset, physical_size, direct)
        def produce():
            limit = min(physical_size, limit_hint or physical_size)
            page, stamp = fetch(query, body_fields if direct else metadata_fields, offset, limit)
            if direct:
                bodies.store(page["issues"], stamp)
                fresh_rows.update((row["key"], row) for row in page["issues"])
            return {**page, "issues": [_without_body(row) for row in page["issues"]]}
        page = client.cache.get_or_set_strict(key, client.s.cache_ttl_seconds, produce)[0]
        check()
        return page, key

    anchor = start if direct else start // DISCOVERY_PAGE_SIZE * DISCOVERY_PAGE_SIZE
    discovered, discovery_key = discovery(anchor)
    if (not direct and start >= anchor + len(discovered["issues"])
            and (discovered["total"] is None or start < discovered["total"])):
        cap = discovered["maxResults"]
        anchor = anchor + (start - anchor) // cap * cap if cap else start
        if anchor == discovered["startAt"]:
            anchor = start
        discovered, discovery_key = discovery(anchor, cap)
        if start >= anchor + len(discovered["issues"]) and discovered["hasMore"]:
            anchor = start
            discovered, discovery_key = discovery(anchor)
    selected = discovered["issues"][max(0, start - anchor):max(0, start - anchor) + size]
    metadata_bytes = encoded_size(selected)
    if metadata_bytes >= MAX_BODY_RESULT_BYTES:
        raise ValueError(BODY_PAGE_LIMIT_MESSAGE)
    available = bodies.read([row for row in selected if row["key"] not in fresh_rows],
                            MAX_BODY_RESULT_BYTES - metadata_bytes)
    hydrated = {key: {**row, "fields": dict(row["fields"])} for key, row in fresh_rows.items()}
    missing = [row for row in selected if row["key"] not in available and row["key"] not in hydrated]
    expected = {row["key"]: row for row in selected}
    retained_sizes = {}
    retained_bytes = 2
    for row in selected:
        initial = {**row, "fields": dict(row["fields"])}
        if row["key"] in available:
            initial["fields"]["description"] = available[row["key"]]
        retained_sizes[row["key"]] = encoded_size(initial) + 1
        retained_bytes += retained_sizes[row["key"]]

    def reserve_rows(rows):
        """Bound the complete output before retaining/storing another physical page."""
        nonlocal retained_bytes
        for row in rows:
            original = expected.get(row["key"])
            if original is None:
                raise QueryStateChanged("요청하지 않은 티켓이 본문 조회에 포함되었습니다.")
            version = original["fields"].get("updated")
            if version and version != row["fields"].get("updated"):
                raise QueryStateChanged("검색 중 티켓이 변경되었습니다. 다시 조회하세요.")
            if version:
                candidate = {**original, "fields": dict(original["fields"])}
                if "description" in row["fields"]:
                    candidate["fields"]["description"] = row["fields"]["description"]
            else:
                candidate = row
            size = encoded_size(candidate) + 1
            retained_bytes += size - retained_sizes[row["key"]]
            if retained_bytes > MAX_BODY_RESULT_BYTES:
                raise ValueError(BODY_PAGE_LIMIT_MESSAGE)
            retained_sizes[row["key"]] = size

    if retained_bytes > MAX_BODY_RESULT_BYTES:
        raise ValueError(BODY_PAGE_LIMIT_MESSAGE)

    try:
        if missing and direct:
            # A one-JQL budget or wildcard must retain the original physical query.
            page, stamp = fetch(query, body_fields, start, size)
            if [row["key"] for row in page["issues"]] != [row["key"] for row in selected]:
                raise QueryStateChanged("검색 결과가 변경되었습니다. 첫 페이지부터 다시 조회하세요.")
            reserve_rows(page["issues"])
            bodies.store(page["issues"], stamp)
            hydrated.update((row["key"], row) for row in page["issues"])
        if missing and not direct:
            keys = [row["key"] for row in missing]
            key_query = "key in (" + ",".join(json.dumps(key) for key in keys) + ") ORDER BY key ASC"
            offset = 0
            while True:
                page, stamp = fetch(key_query, body_fields, offset, len(keys))
                # Persist every validated physical page before another can fail.
                for row in page["issues"]:
                    if row["key"] not in keys or row["key"] in hydrated:
                        raise QueryStateChanged("요청하지 않은 티켓이 본문 조회에 포함되었습니다.")
                reserve_rows(page["issues"])
                bodies.store(page["issues"], stamp)
                hydrated.update((row["key"], row) for row in page["issues"])
                if not page["hasMore"]:
                    break
                offset = page["nextStartAt"]
            if not set(keys) <= hydrated.keys():
                raise QueryStateChanged("본문 조회 중 티켓이 사라졌거나 조회 권한이 변경되었습니다.")
    except QueryStateChanged:
        client.cache.invalidate(discovery_key)
        raise

    rows = []
    for row in selected:
        fields_copy = dict(row["fields"])
        if row["key"] in available:
            fields_copy["description"] = available[row["key"]]
            rows.append({**row, "fields": fields_copy})
        else:
            current = hydrated.get(row["key"], row)
            # Versionless discovery cannot authorize mixing an older metadata row
            # with a new body. Return the complete fresh physical row instead.
            if not row["fields"].get("updated"):
                rows.append(current)
            else:
                if "description" in current["fields"]:
                    fields_copy["description"] = current["fields"]["description"]
                rows.append({**row, "fields": fields_copy})
    check()
    total, next_start = discovered["total"], start + len(rows)
    has_more = bool(rows) and (total is None or next_start < total)
    budget = current_budget()
    result = {"startAt": start, "maxResults": size, "total": total, "issues": rows, "returned": len(rows),
            "hasMore": has_more, "nextStartAt": next_start if has_more else None,
            "snapshotId": None, "snapshotFrozen": False,
            "execution": {"requestedStrategy": strategy, "effectiveStrategy": "off", "maxParts": max_parts,
                          "logicalLeafCount": None, "physicalGroupCount": 1,
                          "fallbackReason": "body_projection",
                          **(budget.diagnostics() if budget is not None else {"physicalQueryCount": 0})}}
    if encoded_size(result) > MAX_BODY_RESULT_BYTES:
        raise ValueError(BODY_PAGE_LIMIT_MESSAGE)
    return result
