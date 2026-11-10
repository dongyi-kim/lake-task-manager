"""Finite public status-history JQL, installed only on an evaluation app.

DURING follows Jira 8.20.8's standard binary: row.created <= operand2.upper
and row.nextChange >= operand1.lower + 1000ms. Day upper is 23:59:59.000;
minute upper is :59.000. Equal-time rows remain. This is binary-grounded
fixture conformance, not live-server/plugin parity. See the external research
record jira820-during-bytecode-findings-54ac71.md and official artifact below.

Ordinary updated/resolved >= absolute-minute operands use the documented
actor-local minute start and public aware timestamps, independently of DURING.
This finite UI contract does not establish ordinary subsecond/index parity.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from fastapi.responses import JSONResponse
from jira820.jql import _pred
from jira820.serialize import dt as public_timestamp

from app.jira.jql import And, Atom, JqlUnsupported, Not, _Parser, _render_tokens, _split_order, _unquote, tokenize
from app.mock.query_compat import _parent_matches

CORE_URL = "https://maven.artifacts.atlassian.com/com/atlassian/jira/jira-core/8.20.8/jira-core-8.20.8.jar"
CORE_SHA256 = "98bada68e8330daabd5890647e907793789276bccdba28e43bbc481c5a5070b0"
_REGISTRY = "etl_status_history_search"
_MEMBERSHIP = {"project", "labels", "assignee", "reporter", "component", "components", "key",
               "status", "statuscategory", "type", "issuetype", "sprint", "epic link", "epiclink"}
_DATES = {"created", "updated", "resolved", "resolutiondate", "due", "duedate"}
_TEXT = {"text", "summary", "description", "comment"}
_ORDER = {"key", "updated", "created", "duedate", "due", "resolved", "summary"}


def _error(message):
    raise JqlUnsupported("Evaluation history search: " + message)


def _zone(name):
    if not isinstance(name, str) or not name:
        _error("public user timezone is missing")
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        _error("unknown public user timezone")


def _actor(store, search_timezone):
    user = store.users.get(store.config.current_user)
    if not user or user.get("timeZone") != search_timezone:
        _error("current public actor and configured search timezone must agree")
    _zone(user["timeZone"])


def _instant(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("naive")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        _error("public source timestamp is missing or has no timezone")


def _value(token):
    if token.kind not in {"WORD", "STRING"}:
        _error("a literal value is required")
    return _unquote(token.value)


def _list(tokens):
    if len(tokens) < 3 or tokens[0].kind != "LPAREN":
        _error("a nonempty literal list is required")
    values, index = [], 1
    while index < len(tokens):
        values.append(_value(tokens[index]))
        index += 1
        if index < len(tokens) and tokens[index].kind == "RPAREN":
            return values, index + 1
        if index >= len(tokens) or tokens[index].kind != "COMMA":
            _error("invalid literal list")
        index += 1
    _error("literal list is not closed")


def _date_bounds(value, zone):
    if not re.fullmatch(r"\d{4}([/-])\d{2}\1\d{2}(?: \d{2}:\d{2})?", value):
        _error("DURING accepts absolute YYYY-MM-DD or YYYY/MM/DD, optionally HH:mm")
    normalized = value.replace("/", "-")
    try:
        parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M" if " " in normalized else "%Y-%m-%d")
    except ValueError:
        _error("invalid DURING date")
    lower = parsed.replace(tzinfo=zone)
    upper = lower.replace(second=59) if " " in normalized else lower.replace(hour=23, minute=59, second=59)
    return lower.astimezone(timezone.utc), upper.astimezone(timezone.utc)


def _status_intervals(store, issue, observed_until):
    created = _instant((issue.get("_timestamps") or {}).get("created")
                       or public_timestamp(issue.get("created"), issue.get("tcreated")))
    if created > observed_until:
        return []
    current = issue.get("statusName")
    if not isinstance(current, str) or not current:
        _error("current public status is missing")
    try:
        history = store.serializer.changelog(issue)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, JqlUnsupported):
            raise
        _error("public serialized history is incomplete")
    rows = history.get("histories", [])
    if history.get("startAt") != 0 or history.get("total") != len(rows):
        _error("public status history is incomplete")
    changes, future = [], False
    for position, row in enumerate(rows):
        items = [item for item in row.get("items", []) if str(item.get("field", "")).casefold() == "status"]
        if not items:
            continue
        changed = _instant(row.get("created"))
        if changed > observed_until:
            future = True
            continue
        if changed < created:
            _error("public status change predates issue creation")
        for item_index, item in enumerate(items):
            before, after = item.get("fromString"), item.get("toString")
            if not isinstance(before, str) or not before or not isinstance(after, str) or not after:
                _error("public status history has an unknown state")
            changes.append((changed, position, item_index, before.casefold(), after.casefold()))
    changes.sort(key=lambda row: row[:3])
    if not changes and future:
        _error("initial public status at the observation horizon is unknown")
    state, since = (changes[0][3] if changes else current.casefold()), created
    intervals = []
    for changed, _position, _item, before, after in changes:
        if before != state:
            _error("public status history chain is inconsistent")
        intervals.append((state, since, changed))
        state, since = after, changed
    if changes and not future and state != current.casefold():
        _error("current public status disagrees with its history")
    intervals.append((state, since, None))
    return intervals


def _historical(tokens, zone, intervals):
    rest = tokens[2:]
    if rest and rest[0].kind == "WORD" and rest[0].value.upper() == "IN":
        names, used = _list(rest[1:])
        rest = rest[1 + used:]
    else:
        if not rest:
            _error("WAS requires a status name")
        names, rest = [_value(rest[0])], rest[1:]
    if any(not name or name.isdecimal() or name.upper() in {"NOT", "EMPTY", "NULL"} for name in names):
        _error("only positive status names are supported")
    wanted = {name.casefold() for name in names}
    bounds = None
    if rest:
        if rest[0].kind != "WORD" or rest[0].value.upper() != "DURING":
            _error("unsupported historical modifier")
        dates, used = _list(rest[1:])
        if len(dates) != 2 or 1 + used != len(rest):
            _error("DURING requires exactly two dates and no suffix")
        lower, _ = _date_bounds(dates[0], zone)
        _, upper = _date_bounds(dates[1], zone)
        if lower > upper:
            _error("DURING dates are reversed")
        bounds = lower + timedelta(seconds=1), upper
    def matches(issue):
        return any(status in wanted and (bounds is None or (created <= bounds[1]
                   and (next_change is None or next_change >= bounds[0])))
                   for status, created, next_change in intervals(issue))
    return matches


def _ordinary_instant(issue, field):
    key = "resolutiondate" if field in {"resolved", "resolutiondate"} else field
    timestamps = issue.get("_timestamps")
    if not isinstance(timestamps, dict) or key not in timestamps:
        _error("required public ordinary timestamp is missing")
    value = timestamps[key]
    # A projected null resolution is known EMPTY; an absent or malformed field
    # is unknown and must not turn the Workload fallback into a broad match.
    if key == "resolutiondate" and value is None:
        return None
    return _instant(value)


def _ordinary(store, tokens, raw, zone):
    field = _value(tokens[0]).casefold()
    if field == "parent":
        wanted = _parent_matches(store, raw, False)
        return lambda issue: issue["key"] in wanted
    rest = tokens[1:]
    if not rest:
        _error("comparison operator is missing")
    op, rest = rest[0].value.upper(), rest[1:]
    if op == "NOT" and rest and rest[0].value.upper() == "IN":
        op, rest = "NOT IN", rest[1:]
    if op in {"IN", "NOT IN"}:
        values, used = _list(rest)
        if used != len(rest):
            _error("unconsumed comparison suffix")
        if any("," in value for value in values):
            _error("embedded comma list values are outside the supported ordinary grammar")
    elif len(rest) == 1:
        values = [_value(rest[0])]
    else:
        _error("comparison requires one literal or a supported list")
    membership = _MEMBERSHIP | {str(store.config.epic_link_field).casefold()}
    if field in {"epic link", "epiclink", str(store.config.epic_link_field).casefold()}:
        if op not in {"=", "!=", "IN", "NOT IN"}:
            _error("unsupported Epic Link comparison")
        wanted = {value.upper() for value in values}
        negative = op in {"!=", "NOT IN"}
        # The pinned atom evaluator reads only v0; use the fully parsed list.
        # Negative Jira comparisons exclude EMPTY, as the parent adapter does.
        return lambda issue: bool(issue.get("epicKey")) and (
            (str(issue["epicKey"]).upper() in wanted) != negative)
    elif field in membership:
        valid = op in {"=", "!=", "IN", "NOT IN"}
    elif field in _TEXT:
        valid = op == "~"
    elif field in _DATES:
        if (field in {"resolved", "resolutiondate"} and op == "IS"
                and values == ["EMPTY"] and rest[0].kind == "WORD"):
            return lambda issue: _ordinary_instant(issue, field) is None
        if len(values) == 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", values[0]):
            if field not in {"updated", "resolved", "resolutiondate"} or op != ">=":
                _error("unsupported ordinary minute field/operator")
            try:
                cutoff = datetime.strptime(values[0], "%Y-%m-%d %H:%M").replace(tzinfo=zone)
            except ValueError:
                _error("invalid ordinary minute date")
            cutoff = cutoff.astimezone(timezone.utc)
            # Jira 8.20 field/operator references document minute operands in
            # the user's timezone and inclusive >=. No DURING bound expansion
            # or pinned date-only predicate is involved in this comparison.
            return lambda issue: ((value := _ordinary_instant(issue, field)) is not None
                                  and value >= cutoff)
        valid = op in {"=", ">", ">=", "<", "<="} and len(values) == 1
        value = values[0]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                valid = False
        elif not re.fullmatch(r"-\d+[dw]", value, re.I):
            valid = False
    else:
        valid = False
    if not valid:
        _error("unsupported ordinary field/operator/value shape")
    predicate = _pred(store, raw)
    if predicate is None:
        _error("the pinned atomic evaluator does not support this comparison")
    return predicate


def _order(tokens):
    index = 0
    while index < len(tokens):
        if _value(tokens[index]).casefold() not in _ORDER:
            _error("unsupported ORDER BY field")
        index += 1
        if index < len(tokens) and tokens[index].kind == "WORD" and tokens[index].value.upper() in {"ASC", "DESC"}:
            index += 1
        if index < len(tokens):
            if tokens[index].kind != "COMMA" or index == len(tokens) - 1:
                _error("invalid ORDER BY suffix")
            index += 1
    return "ORDER BY " + _render_tokens(tokens) if tokens else ""


def _membership(store, query, observed_until, zone):
    tokens = tokenize(query)
    if any(token.kind == "STRING" and "\\" in token.value for token in tokens):
        _error("escaped string literals are outside the supported fixture grammar")
    body, order = _split_order(tokens)
    if len(body) != len(tokens) and not order:
        _error("ORDER BY requires a field")
    order_only = _order(order)
    interval_cache = {}
    def intervals(issue):
        key = issue["key"]
        if key not in interval_cache:
            interval_cache[key] = _status_intervals(store, issue, observed_until)
        return interval_cache[key]
    def compile_node(node):
        if isinstance(node, Not):
            negate = False
            while isinstance(node, Not):
                negate, node = not negate, node.child
            if isinstance(node, Atom):
                tokens = tokenize(node.value)
                if tokens and _value(tokens[0]).casefold() == "parent":
                    wanted = _parent_matches(store, node.value, negate)
                    return lambda issue: issue["key"] in wanted
            _error("NOT groups are outside the supported fixture grammar")
        if isinstance(node, Atom):
            tokens = tokenize(node.value)
            if not tokens:
                return lambda issue: True
            if len(tokens) > 1 and tokens[1].kind == "WORD" and tokens[1].value.upper() == "WAS":
                if _value(tokens[0]).casefold() != "status":
                    _error("only status history is supported")
                return _historical(tokens, zone, intervals)
            return _ordinary(store, tokens, node.value, zone)
        predicates = [compile_node(child) for child in node.children]
        combine = all if isinstance(node, And) else any
        return lambda issue: combine(predicate(issue) for predicate in predicates)
    predicate = compile_node(_Parser(body).parse())
    return {key: issue for key, issue in store.issues.items()
            if key not in store.denied_issue_keys and predicate(issue)}, order_only


class _View:
    def __init__(self, original, **overrides):
        self._original = original
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(self._original, name)


class StatusHistoryInstallation:
    def __init__(self, app, observed_until, search_timezone):
        self.app, self.observed_until, self.search_timezone = app, observed_until, search_timezone
        self.route = None
        self.original_user = app.state.store.serializer.user_obj
        self.user_wrapper = None

    def uninstall(self):
        if self.route in self.app.router.routes:
            self.app.router.routes.remove(self.route)
        serializer = self.app.state.store.serializer
        if serializer.user_obj is self.user_wrapper:
            serializer.user_obj = self.original_user
        if getattr(self.app.state, _REGISTRY, None) is self:
            delattr(self.app.state, _REGISTRY)

    @property
    def contract(self):
        return {"grammar": "positive-status-WAS-or-WAS-IN/absolute-DURING-v1",
            "searchTimezone": self.search_timezone, "observedUntil": self.observed_until.isoformat(),
            "during": "created<=operand2.upper AND nextChange>=operand1.lower+1000ms",
            "dayUpper": "23:59:59.000", "minuteUpper": ":59.000", "zeroDurationRows": "retained",
            "source": "public-created/current-status/serialized-status-changelog",
            "ordinaryDates": "pinned date-only YYYY-MM-DD/-Nd/-Nw; updated/resolved/resolutiondate >= YYYY-MM-DD HH:mm uses actor-local minute start and public aware timestamps; resolved/resolutiondate IS EMPTY requires explicit public null",
            "unsupportedOrdinaryShapes": ["embedded comma lists", "escaped string literals",
                                          "date functions and subday/month/year relatives"],
            "nativeEvidence": {"coreUrl": CORE_URL, "coreSha256": CORE_SHA256,
                               "liveServerOrPluginParityTested": False}}


def install_status_history_search(app, *, observed_until, search_timezone):
    observed_until = _instant(observed_until)
    zone = _zone(search_timezone)
    _actor(app.state.store, search_timezone)
    existing = getattr(app.state, _REGISTRY, None)
    if existing:
        if existing.observed_until != observed_until or existing.search_timezone != search_timezone:
            raise ValueError("Conflicting per-app history search installation")
        return existing
    original = next(route.endpoint for route in app.router.routes
                    if getattr(route, "path", None) == "/rest/api/2/search"
                    and "GET" in getattr(route, "methods", ()))
    installed = StatusHistoryInstallation(app, observed_until, search_timezone)
    store = app.state.store
    def user_obj(uid):
        original_user = installed.original_user(uid)
        user = store.users.get(uid)
        if not user or not user.get("timeZone") or original_user is None:
            return original_user
        _zone(user.get("timeZone"))
        return {**original_user, "timeZone": user["timeZone"]}
    installed.user_wrapper = user_obj
    store.serializer.user_obj = user_obj

    @app.get("/rest/api/2/search")
    def search(request: Request, jql: str = "", startAt: int = 0,
               maxResults: int = 50, fields: str = ""):
        try:
            _actor(store, search_timezone)
            issues, order_only = _membership(store, jql, observed_until, zone)
            view = _View(store, issues=issues)
            app_view = _View(request.app, state=_View(request.app.state, store=view))
            forwarded = Request({**request.scope, "app": app_view}, request.receive)
            return original(forwarded, jql=order_only, startAt=startAt, maxResults=maxResults, fields=fields)
        except JqlUnsupported as exc:
            return JSONResponse({"errorMessages": [str(exc)], "errors": {}}, status_code=400)

    installed.route = app.router.routes.pop()
    app.router.routes.insert(0, installed.route)
    setattr(app.state, _REGISTRY, installed)
    return installed
