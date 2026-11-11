"""Local ticket read watermarks and activity already known to the shared Jira cache.

This path never fetches Jira or identity data. ``updated`` is Jira's general issue
change timestamp, not proof of a specific changed field. Cached comments additionally
cover edits to older comments; absent comment data is unknown rather than fetched here.
All wire timestamps are epoch milliseconds, independent of cache fetched-at/TTL.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime


RECENT_WINDOW_MS = 7 * 24 * 60 * 60 * 1000


def activity_scope(client, settings):
    context = [str(client.env), str(settings.jira_base or "").rstrip("/"),
               str(settings.project_key or "")]
    return hashlib.sha256(json.dumps(context, ensure_ascii=False).encode()).hexdigest()


def _recent_timestamp(value, now_ms):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Jira emits an offset. A local/date-only value cannot prove an activity instant.
        if parsed.tzinfo is None:
            return None
        timestamp = int(parsed.timestamp() * 1000)
    except (ValueError, OverflowError, OSError):
        return None
    return timestamp if now_ms - RECENT_WINDOW_MS <= timestamp <= now_ms else None


def _comment_timestamps(rows, now_ms):
    if not isinstance(rows, list):
        return []
    return [timestamp for row in rows if isinstance(row, dict)
            for field in ("created", "date", "updated")
            if (timestamp := _recent_timestamp(row.get(field), now_ms)) is not None]


def ticket_activity(client, settings, cache, keys):
    """Inspect only alive local payloads; no revalidation, source timestamp renewal or N+1."""
    now_ms = int(time.time() * 1000)
    scope = activity_scope(client, settings)
    keys = list(dict.fromkeys(key.upper() for key in keys))
    viewed = cache.ticket_read_states(scope, keys)
    known = cache.get_alive_many((f"{kind}:{client.env}:{key}{suffix}" for key in keys
                                 for kind, suffix in (("issue", ""), ("issueL", ""), ("issueview", ""),
                                                      ("comments", ""), ("comments", ":full"))), record=False)
    tickets = {}
    for key in keys:
        updates, comments = [], []
        for kind in ("issue", "issueL", "issueview"):
            hit = known.get(f"{kind}:{client.env}:{key}")
            if not hit or not isinstance(hit[0], dict):
                continue
            fields = hit[0].get("fields") or {}
            if not isinstance(fields, dict):
                continue
            updated = _recent_timestamp(fields.get("updated"), now_ms)
            if updated is not None:
                updates.append(updated)
            embedded = fields.get("comment") or {}
            if isinstance(embedded, dict):
                comments.extend(_comment_timestamps(embedded.get("comments"), now_ms))
        hit = known.get(f"comments:{client.env}:{key}")
        if hit:
            comments.extend(_comment_timestamps(hit[0], now_ms))
        full = known.get(f"comments:{client.env}:{key}:full")
        if full and isinstance(full[0], dict) and full[0].get("complete"):
            comments.extend(_comment_timestamps(full[0].get("comments"), now_ms))
        tickets[key] = {
            "lastViewedAt": viewed.get(key),
            "latestRelevantAt": max(updates + comments, default=None),
            "latestCommentAt": max(comments, default=None),
        }
    return {"scope": scope, "observedAt": now_ms, "tickets": tickets}


def mark_ticket_viewed(client, settings, cache, key, viewed_through):
    if (isinstance(viewed_through, bool) or not isinstance(viewed_through, int)
            or not 0 < viewed_through <= int(time.time() * 1000)):
        raise ValueError("읽은 시각은 현재까지 실제 표시한 내용의 시각이어야 합니다.")
    key = key.upper()
    scope = activity_scope(client, settings)
    timestamp = cache.mark_ticket_viewed(scope, key, viewed_through)
    return {"scope": scope, "key": key, "lastViewedAt": timestamp}
