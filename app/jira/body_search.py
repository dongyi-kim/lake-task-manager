"""Small, independently cached Jira pages for complete raw body projections.

Never build a whole-query snapshot for descriptions: ten large tickets already
make a meaningful physical page. Requested max_results remains advisory. Bodies
are never truncated; callers can lower max_results when a page exceeds 16 MiB.
"""
import hashlib
import json
from uuid import uuid4

from app.auth.base import QueryStateChanged
from app.infra.json_payload import encoded_size
from app.infra.resource_pressure import resource_pressure
from app.jira.jql import JqlUnsupported, _split_order, tokenize
from app.jira.jql_budget import current_budget, validate_max_parts


BODY_PAGE_SIZE = 10
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
    identity = [client._jql_user_context(), session, generation, canonical, fields, bool(light), start, size]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    key = f"search:body:v2:{client.env}:{generation}:{digest}"

    def produce():
        _assert_session(client, provider, session)
        resource_pressure.check()
        # This path owns only a physical page, not canonical full/light issue rows
        # or the whole query's membership. Cache.refresh records its source age.
        page = client._legacy_search_issues_page(query, start_at=start, max_results=size,
                                                fields=fields, light=light, write_through=False)
        if encoded_size(page) > MAX_BODY_RESULT_BYTES:
            raise ValueError(BODY_PAGE_LIMIT_MESSAGE)
        _assert_session(client, provider, session)
        return page

    page, _hit = client.cache.get_or_set_strict(key, client.s.cache_ttl_seconds, produce)
    _assert_session(client, provider, session)
    if client.cache.fence() != fence or client._jql_generation() != generation:
        raise QueryStateChanged("티켓이 변경되어 검색 결과를 다시 확인해야 합니다.")
    budget = current_budget()
    return {**page, "snapshotId": None, "snapshotFrozen": False,
            "execution": {"requestedStrategy": strategy, "effectiveStrategy": "off", "maxParts": max_parts,
                          "logicalLeafCount": None, "physicalGroupCount": 1,
                          "fallbackReason": "body_projection",
                          **(budget.diagnostics() if budget is not None else {"physicalQueryCount": 0})}}
