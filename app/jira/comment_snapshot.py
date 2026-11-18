"""Coverage proof for unmodified Jira embedded comment collections."""

from datetime import datetime


def _source_comment(row):
    if not isinstance(row.get("body"), str):
        return False
    try:
        return datetime.fromisoformat(str(row.get("created") or "").replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def embedded_comment_rows(value):
    rows = value.get("comments") if isinstance(value, dict) else value
    return rows if isinstance(rows, list) and all(isinstance(row, dict) for row in rows) else None


def embedded_comment_coverage(value, key):
    rows = embedded_comment_rows(value)
    if rows is None:
        value = None  # An unreadable collection proves no source positions/totals.
    rows = rows if rows is not None else []
    total = value.get("total") if isinstance(value, dict) else None
    start = value.get("startAt") if isinstance(value, dict) else None
    total = total if type(total) is int and total >= 0 else None
    start = start if type(start) is int and start >= 0 else None
    ids = [str(row.get("id") or "") for row in rows]
    valid_ids = all(ids) and len(ids) == len(set(ids))
    errors = isinstance(value, dict) and (any(value.get(name) for name in ("error", "errors", "errorMessages", "partial"))
        or value.get("complete") is False or value.get("hasMore") is True or value.get("isLast") is False)
    complete = (isinstance(value, dict) and embedded_comment_rows(value) is not None
                and start == 0 and total == len(rows) and valid_ids
                and all(_source_comment(row) for row in rows) and not errors)
    more = (start + len(rows) < total) if start is not None and total is not None else None
    result = {"total": total, "returned": len(rows), "startAt": start,
              "complete": bool(complete), "hasMore": False if complete else more,
              "nextStartAt": None}
    if not complete:
        result["nextCall"] = {"name": "get_evidence_page", "arguments": {
            "key": key, "kind": "comments", "start_at": 0, "max_results": 100}}
        from app.infra.cache_policy import current_read_policy
        policy = current_read_policy()
        if policy.explicit:
            result["nextCall"]["arguments"]["cache_mode"] = policy.mode.value
    return result


def cached_complete_comments(client, key, *, fresh_after=None, for_ui=False):
    """Read a complete permission-scoped snapshot, retaining cache source notes."""
    from app.infra.cache_policy import read_scope
    from app.jira.body_search import MAX_BODY_RESULT_BYTES, ticket_field_context

    # A separate collector prevents a rejected partial/old snapshot from adding
    # irrelevant evidence to the caller's successful fallback request.
    with read_scope() as reads:
        context = ticket_field_context(client)
        values = context.caches["comment"].read_latest([key], MAX_BODY_RESULT_BYTES)
        value = values.get(key)
        if not embedded_comment_coverage(value, key)["complete"]:
            return None
        if for_ui and client._comment_fmt() == "wiki":
            import re
            if any(not row.get("renderedBody") and re.search(r"!([^!\n/:]+)(?:\|[^!\n]*)?!", row["body"])
                   for row in embedded_comment_rows(value)):
                return None  # The comment endpoint can render attachment filenames safely.
        sources = list(reads)
        if fresh_after is not None and (not sources or any(
                source.get("fetchedAt", 0) * 1000 < fresh_after for source in sources)):
            return None
        context.check()
    from app.infra.cache_policy import note_read
    for source in sources:
        note_read(source)
    return embedded_comment_rows(value), sources


def ordered_comments(rows, *, newest_first=False):
    """Complete collections can be reordered locally; embedded offsets cannot."""
    def order(row):
        try:
            stamp = datetime.fromisoformat(str(row.get("created") or "").replace("Z", "+00:00")).timestamp()
        except (ValueError, OverflowError, OSError):
            stamp = float("-inf")
        identity = str(row.get("id") or "")
        return stamp, (0, int(identity)) if identity.isdigit() else (1, identity)
    return sorted(rows, key=order, reverse=newest_first)
