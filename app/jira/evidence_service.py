"""Lossless, paged Jira evidence using the running client's cache/auth boundary.

This module deliberately does not calculate reporting periods, ownership or business metrics.
Comments are real REST pages. Jira 8.20 only documents expanded changelog, so a truncated
expansion remains visibly incomplete instead of guessing a Cloud-only pagination endpoint.
"""

from copy import deepcopy

from app.auth.base import SessionExpired


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
    if (source["sourceComplete"] and source_start + len(source["items"]) <= start_at < source["total"]):
        # Jira may clamp maxResults below 100. Resume exactly where the caller needs data,
        # never jump by the requested source page size or mistake a short page for the end.
        source_start = start_at
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


def get_evidence_page(client, key, kind="comments", start_at=0, max_results=100):
    validate_page(start_at, max_results)
    key = key.strip().upper()
    if kind == "comments":
        page = _comments_page(client, key, start_at, max_results)
    elif kind == "history":
        page = _history_page(client, key, start_at, max_results)
    else:
        raise ValueError("kind는 comments 또는 history여야 합니다.")
    return page
