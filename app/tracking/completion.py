"""Conservative, period-independent proof of a current done interval."""
from datetime import datetime, timezone


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def current_completion(raw, categories, history_complete):
    """Return context only when complete history agrees with the raw snapshot.

    Jira's updated revision bounds history even when fetched later. A missing
    revision can use the actual raw fetch time; without either bound old stored
    records cannot prove contemporaneous status. Query end/asOf is never a bound.
    Unknown or ambiguous chains deliberately leave the optional field absent.
    """
    fields = raw.get("fields") or {}
    status = fields.get("status") or {}
    if not history_complete or (status.get("statusCategory") or {}).get("key") != "done":
        return None
    bounds = []
    if fields.get("updated") is not None:
        updated = _instant(fields["updated"])
        if updated is None:
            return None
        bounds.append(updated)
    if raw.get("_trackingFetchedAt") is not None:
        try:
            fetched = datetime.fromtimestamp(raw["_trackingFetchedAt"], timezone.utc)
        except (ValueError, TypeError, OverflowError, OSError):
            return None
        if bounds and bounds[0] > fetched:
            return None
        bounds.append(fetched)
    if not bounds:
        return None

    def state(ident, name):
        ident, name = str(ident or "").casefold(), str(name or "").casefold()
        category = categories.get(ident) if ident else categories.get(name)
        if not category or (name in categories and categories[name] != category):
            return None
        return {"id": ident, "name": name, "category": category}

    def same(left, right):
        field = "id" if left["id"] and right["id"] else "name"
        return bool(left[field] and left[field] == right[field] and left["category"] == right["category"])

    transitions, seen = [], set()
    for history in (raw.get("changelog") or {}).get("histories", []):
        # Do not inherit analyze_issue's deduplication: conflicting source records
        # and history fetched after the raw revision cannot establish provenance.
        ident = history.get("id")
        if ident is not None:
            if str(ident) in seen:
                return None
            seen.add(str(ident))
        when = _instant(history.get("created"))
        if when is None or any(when > bound for bound in bounds):
            return None
        for item in history.get("items") or []:
            if str(item.get("field")).casefold() != "status":
                continue
            before = state(item.get("from"), item.get("fromString"))
            after = state(item.get("to"), item.get("toString"))
            if before is None or after is None:
                return None
            transitions.append((when, before, after))
    transitions.sort(key=lambda row: row[0])
    previous, previous_at, completed_at = None, None, None
    for when, before, after in transitions:
        if when == previous_at or (previous is not None and not same(previous, before)):
            return None
        if before["category"] != "done" and after["category"] == "done":
            completed_at = when
        elif after["category"] != "done":
            completed_at = None
        previous, previous_at = after, when
    current = state(status.get("id"), status.get("name"))
    if completed_at is None or current is None or not same(previous, current):
        return None
    return {"at": completed_at.isoformat(), "source": "completeStatusHistory"}
