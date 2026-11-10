"""Content-exact, identity-normalized Jira evidence using the client's cache/auth boundary.

This module deliberately does not calculate reporting periods, ownership or business metrics.
Comments are real REST pages. Jira 8.20 only documents expanded changelog, so a truncated
expansion remains visibly incomplete instead of guessing a Cloud-only pagination endpoint.
"""

from copy import deepcopy
import hashlib
import json

from app.auth.base import SessionExpired
from app.infra.cache_policy import current_read_policy


EVIDENCE_PAGE_MAX_BYTES = 8192
EVIDENCE_RECORD_FORMAT = "jira-evidence-v2"
_ACTOR_FIELDS = ("accountId", "name", "key", "displayName", "self", "timeZone")


def _wire_record(row):
    """Project actor profiles only; shared source-page caches keep their original rows."""
    result = deepcopy(row)
    for role in ("author", "updateAuthor"):
        actor = result.get(role)
        if isinstance(actor, dict):
            result[role] = {field: actor[field] for field in _ACTOR_FIELDS if field in actor}
    return result


def _json_size(value):
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _with_next_call(page):
    next_call = None
    if page["hasMore"] is True and type(page.get("nextStartAt")) is int:
        start, offset = page["nextStartAt"], 0
        for item in page["items"]:
            if "recordJson" in item and type(item.get("nextRecordOffset")) is int:
                start, offset = item["sourceIndex"], item["nextRecordOffset"]
                break
        next_call = {"name": "get_evidence_page", "arguments": {
            "key": page["key"], "kind": page["kind"], "start_at": start,
            "max_results": page["maxResults"], "record_offset": offset}}
        policy = current_read_policy()
        if policy.explicit:
            # Include the option before byte sizing so copied continuation arguments
            # retain the requested source age even across oversized record chunks.
            next_call["arguments"]["cache_mode"] = policy.mode.value
    return {**page, "nextCall": next_call}


def _bounded_evidence_page(page, record_offset):
    """Keep records lossless while bounding each model-visible response by UTF-8 bytes."""
    page = _with_next_call(page)
    rows = page["items"]
    if record_offset and not rows:
        raise ValueError("record_offset에 해당하는 원본 기록이 없습니다.")
    if not record_offset and _json_size(page) <= EVIDENCE_PAGE_MAX_BYTES:
        return page

    def prefix(count):
        more = count < len(rows) or page["hasMore"]
        return _with_next_call({**page, "items": rows[:count], "returned": count,
                "hasMore": more, "nextStartAt": page["startAt"] + count if more else None})

    if not record_offset:
        # Stop only between complete records. The next page starts at the first omitted row.
        count = 0
        for candidate in range(1, len(rows) + 1):
            if _json_size(prefix(candidate)) > EVIDENCE_PAGE_MAX_BYTES:
                break
            count = candidate
        if count:
            return prefix(count)
    if not rows:
        raise ValueError("Jira evidence 페이지 메타데이터가 응답 크기 제한을 초과했습니다.")

    # A single comment or history (including long from/to description values) can itself
    # exceed the budget. Preserve its exact normalized JSON, readable in character ranges.
    record = json.dumps(rows[0], ensure_ascii=False, default=str)
    if record_offset >= len(record):
        raise ValueError("record_offset이 원본 기록의 문자 범위를 벗어났습니다.")
    digest = hashlib.sha256(record.encode("utf-8")).hexdigest()

    def chunk(end):
        next_offset = end if end < len(record) else None
        more = next_offset is not None or len(rows) > 1 or page["hasMore"]
        item = {"id": rows[0]["id"], "sourceIndex": page["startAt"],
                "recordFormat": EVIDENCE_RECORD_FORMAT,
                "recordJson": record[record_offset:end], "recordOffset": record_offset,
                "nextRecordOffset": next_offset, "totalCharacters": len(record),
                "recordSha256": digest}
        return _with_next_call({**page, "items": [item], "returned": 1, "hasMore": more,
                "nextStartAt": (page["startAt"] if next_offset is not None else
                                page["startAt"] + 1) if more else None})

    lower, upper = record_offset, min(len(record), record_offset + EVIDENCE_PAGE_MAX_BYTES)
    while lower < upper:
        middle = (lower + upper + 1) // 2
        if _json_size(chunk(middle)) <= EVIDENCE_PAGE_MAX_BYTES:
            lower = middle
        else:
            upper = middle - 1
    if lower == record_offset:
        raise ValueError("Jira evidence 기록 메타데이터가 응답 크기 제한을 초과했습니다.")
    return chunk(lower)


def validate_page(start_at, max_results):
    if (type(start_at) is not int or start_at < 0 or type(max_results) is not int
            or not 1 <= max_results <= 100):
        raise ValueError("start_at은 0 이상, max_results는 1~100 정수여야 합니다.")


def _identity(row):
    """Retain the original actor object; never derive an ID from a display name."""
    actor = row.get("author") or {}
    return {**deepcopy(row), "authorId": actor.get("accountId") or actor.get("name")
            or actor.get("key"), "authorDisplayName": actor.get("displayName")}


def _rows(data, field):
    rows = data.get(field) if isinstance(data, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Jira {field} 응답 형식이 올바르지 않습니다.")
    ids = [str(row.get("id") or "") for row in rows]
    if any(not identity for identity in ids) or len(ids) != len(set(ids)):
        raise ValueError(f"Jira {field} 응답에 식별자가 없거나 중복되어 있습니다.")
    return rows


def _check_auth(client, data):
    if client._looks_anonymous(data):
        raise SessionExpired("Jira evidence 응답이 익명 세션을 나타냅니다.")


def _comments_page(client, key, start_at, max_results):
    # Transport pages stay aligned even when the model-visible byte budget returns only
    # a handful of records. Continuations and single-record chunks reuse that source page.
    def source_page(source_start):
        cache_key = f"comments:{client.env}:{key}:evidence:v1:{source_start}:100"

        def fetch():
            data = client.provider.get_json(f"/rest/api/2/issue/{key}/comment", params={
                "startAt": source_start, "maxResults": 100, "orderBy": "created",
                "expand": "renderedBody",
            })
            _check_auth(client, data)
            rows = _rows(data, "comments")
            total, actual_start = data.get("total"), data.get("startAt")
            complete = (type(total) is int and total >= 0 and type(actual_start) is int
                        and actual_start == source_start and len(rows) <= 100
                        and (not rows or source_start + len(rows) <= total)
                        and (bool(rows) or source_start >= total))
            return {"key": key, "kind": "comments", "items": [_identity(row) for row in rows],
                    "total": total if type(total) is int and total >= 0 else None,
                    "sourceStartAt": actual_start, "sourceComplete": complete,
                    "complete": complete, "partial": not complete,
                    "incompleteReason": None if complete else "unproven_comment_page",
                    "consistency": "live_offset; deduplicate IDs and recheck totals across pages"}

        # Prefixes match UI mutation invalidation and retain the write-generation fence.
        return client.cache.get_or_set_strict(cache_key, client.s.cache_ttl_seconds, fetch)[0]

    source_start = start_at // 100 * 100
    source = source_page(source_start)
    while (source["sourceComplete"] and source_start + len(source["items"]) <= start_at < source["total"]):
        # Align to the observed server page size, not each small MCP slice. Reuse the
        # clamped source page across slices; a changed clamp is handled by the loop.
        size = len(source["items"])
        source_start += (start_at - source_start) // size * size
        source = source_page(source_start)
    rows = source["items"][start_at - source_start:start_at - source_start + max_results]
    more = start_at + len(rows) < source["total"] if source["sourceComplete"] else None
    return {**source, "items": rows, "startAt": start_at, "maxResults": max_results,
            "returned": len(rows), "hasMore": more,
            "nextStartAt": start_at + len(rows) if more else None}


def _history_page(client, key, start_at, max_results):
    cache_key = f"changelog:{client.env}:{key}:evidence:v1"

    def fetch():
        data = client.provider.get_json(f"/rest/api/2/issue/{key}", params={
            "expand": "changelog", "fields": "created,reporter",
        })
        _check_auth(client, data)
        source = data.get("changelog") if isinstance(data, dict) else None
        rows = _rows(source, "histories")
        total, source_start = source.get("total"), source.get("startAt")
        complete = (type(total) is int and total >= 0 and type(source_start) is int
                    and source_start == 0 and len(rows) == total)
        return {"items": [_identity(row) for row in rows], "sourceStartAt": source_start,
                "total": total if type(total) is int and total >= 0 else None,
                "sourceComplete": complete, "partial": not complete,
                "incompleteReason": None if complete else "changelog_expansion_incomplete"}

    source = client.cache.get_or_set_strict(cache_key, client.s.cache_ttl_seconds, fetch)[0]
    available = len(source["items"])
    rows = source["items"][start_at:start_at + max_results]
    more = start_at + len(rows) < available
    return {**source, "key": key, "kind": "history", "items": rows,
            "startAt": start_at, "maxResults": max_results, "returned": len(rows),
            "availableTotal": available, "hasMore": more,
            "nextStartAt": start_at + len(rows) if more else None,
            "complete": source["sourceComplete"],
            "consistency": "cached_expansion; pagination covers available histories only"}


def get_evidence_page(client, key, kind="comments", start_at=0, max_results=100, record_offset=0):
    validate_page(start_at, max_results)
    if type(record_offset) is not int or record_offset < 0:
        raise ValueError("record_offset은 첫 원본 기록의 0 이상 문자 위치여야 합니다.")
    key = key.strip().upper()
    if kind == "comments":
        page = _comments_page(client, key, start_at, max_results)
    elif kind == "history":
        page = _history_page(client, key, start_at, max_results)
    else:
        raise ValueError("kind는 comments 또는 history여야 합니다.")
    wire = {**page, "recordFormat": EVIDENCE_RECORD_FORMAT,
            "items": [_wire_record(row) for row in page["items"]]}
    return _bounded_evidence_page(wire, record_offset)
