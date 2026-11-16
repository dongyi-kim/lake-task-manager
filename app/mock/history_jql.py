"""The Jira 8.20 history predicates missing from the development Jira dependency.

This adapter uses canonical world records and rejects unsupported history syntax
instead of letting jira820 silently ignore it. Dates use the mock server's UTC
timestamps; date-only DURING/ON and updatedBy endpoints include the whole day.
"""
from datetime import date, datetime, time, timedelta, timezone
import math
import re

from app.jira.jql import JqlUnsupported, _unquote, tokenize


def instant(value, hm=None):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.fromisoformat(hm or "09:00"), timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def _day(value, store):
    raw = _unquote(value)
    relative = re.fullmatch(r"([+-]?\d+)([wdhm]?)", raw, re.I)
    if relative:
        n, unit = int(relative[1]), (relative[2] or "d").lower()
        days = n * {"w": 7, "d": 1, "h": 1 / 24, "m": 1 / 1440}[unit]
        days = math.copysign(math.ceil(abs(days)), days)
        value = store.now + timedelta(days=days)
        return datetime.combine(value, time.min, timezone.utc)
    try:
        return instant(raw.replace("/", "-"))
    except ValueError as exc:
        raise JqlUnsupported(f"Unsupported history date: {raw}") from exc


def _events(issue):
    yield issue.get("creator") or issue.get("reporter"), instant(issue["created"], issue.get("tcreated"))
    for row in issue.get("changelog", []):
        yield row.get("author"), instant(row["date"], row.get("time"))
    for row in issue.get("comments", []):
        yield row.get("author"), instant(row["created"], row.get("tcreated"))
        if row.get("updated"):
            yield row.get("updateAuthor") or row.get("author"), instant(row["updated"], row.get("tupdated"))
    for row in issue.get("worklog", []):
        yield row.get("author"), instant(row["date"])


def history_matches(store, raw):
    """Return matching keys for a history atom, or None for other JQL atoms."""
    updated = re.fullmatch(r"(?:issue|issuekey|key)\s+(NOT\s+IN|IN)\s+updatedBy\s*\((.*)\)", raw, re.I)
    if updated:
        tokens = tokenize(updated[2])
        if not tokens or any(t.kind != ("COMMA" if i % 2 else "STRING") and
                             not (i % 2 == 0 and t.kind == "WORD") for i, t in enumerate(tokens)):
            raise JqlUnsupported("updatedBy requires user and optional dateFrom/dateTo")
        args = [_unquote(t.value) for t in tokens[::2]]
        if len(args) not in {1, 2, 3} or len(tokens) % 2 == 0:
            raise JqlUnsupported("updatedBy requires one to three arguments")
        start = _day(args[1], store) if len(args) > 1 else datetime.min.replace(tzinfo=timezone.utc)
        end = _day(args[2], store) + timedelta(days=1) if len(args) > 2 else datetime.max.replace(tzinfo=timezone.utc)
        keys = {key for key, issue in store.issues.items()
                if any(actor == args[0] and start <= when < end for actor, when in _events(issue))}
        return set(store.issues) - keys if updated[1].upper().startswith("NOT") else keys
    if "updatedby" in raw.casefold():
        raise JqlUnsupported("Unsupported updatedBy syntax")
    match = re.match(r"^\s*(assignee|status)\s+(CHANGED|WAS)\b(.*)$", raw, re.I)
    if not match:
        return None
    field, operator, suffix = match[1].lower(), match[2].upper(), match[3]
    tokens = tokenize(suffix)
    values, negative = [], False
    if operator == "WAS":
        if tokens and tokens[0].value.upper() == "NOT":
            negative = True
            tokens.pop(0)
        if tokens and tokens[0].value.upper() == "IN":
            tokens.pop(0)
            if not tokens or tokens.pop(0).kind != "LPAREN":
                raise JqlUnsupported("WAS IN requires a list")
            while tokens and tokens[0].kind != "RPAREN":
                values.append(_unquote(tokens.pop(0).value).casefold())
                if tokens and tokens[0].kind == "COMMA":
                    tokens.pop(0)
            if not tokens or not values:
                raise JqlUnsupported("WAS IN requires a nonempty list")
            tokens.pop(0)
        elif tokens:
            values = [_unquote(tokens.pop(0).value).casefold()]
        else:
            raise JqlUnsupported("WAS requires a value")
    predicates = {}
    low = datetime.min.replace(tzinfo=timezone.utc)
    high = datetime.max.replace(tzinfo=timezone.utc)
    while tokens:
        name = tokens.pop(0).value.upper()
        if name in {"FROM", "TO", "BY"} and tokens:
            predicates[name] = _unquote(tokens.pop(0).value).casefold()
        elif name in {"AFTER", "BEFORE", "ON"} and tokens:
            value = _day(tokens.pop(0).value, store)
            if name == "AFTER":
                low = max(low, value + timedelta(microseconds=1))
            elif name == "BEFORE":
                high = min(high, value)
            else:
                low, high = max(low, value), min(high, value + timedelta(days=1))
        elif name == "DURING" and len(tokens) >= 5 and [t.kind for t in tokens[:5]] == ["LPAREN", "STRING", "COMMA", "STRING", "RPAREN"]:
            low, high = max(low, _day(tokens[1].value, store)), min(high, _day(tokens[3].value, store) + timedelta(days=1))
            tokens = tokens[5:]
        else:
            raise JqlUnsupported(f"Unsupported history predicate: {name}")

    def matches(issue):
        changes = sorted([(instant(row["date"], row.get("time")), row, item)
                          for row in issue.get("changelog", []) for item in row["items"]
                          if item.get("field", "").casefold() == field], key=lambda row: row[0])
        def same(item, side, wanted):
            return wanted in {str(item.get(side) or "").casefold(), str(item.get(side + "String") or "").casefold()}
        if operator == "CHANGED":
            return any(low <= when < high and
                       all(same(item, side.lower(), wanted) if side in {"FROM", "TO"}
                           else str(row.get("author", "")).casefold() == wanted
                           for side, wanted in predicates.items()) for when, row, item in changes)
        if predicates:
            raise JqlUnsupported("WAS supports date predicates only in this mock")
        current = issue.get("assignee") if field == "assignee" else issue.get("statusName")
        previous = {str(current or "").casefold()}
        if field == "status":
            previous.update(str(sid).casefold() for name, _category, sid in store.config.statuses
                            if name.casefold() == str(current or "").casefold())
        if changes:
            first = changes[0][2]
            previous = {str(first.get("from") or "").casefold(), str(first.get("fromString") or "").casefold()}
        begin = instant(issue["created"], issue.get("tcreated"))
        intervals = []
        for when, _row, item in changes:
            intervals.append((begin, when, previous))
            begin, previous = when, {str(item.get("to") or "").casefold(), str(item.get("toString") or "").casefold()}
        intervals.append((begin, datetime.max.replace(tzinfo=timezone.utc), previous))
        # WAS NOT negates the historical match, rather than matching a different
        # value at some other time during the issue's life.
        found = any(begin < high and end > low and bool(set(values) & value)
                    for begin, end, value in intervals)
        return not found if negative else found
    return {key for key, issue in store.issues.items() if matches(issue)}
