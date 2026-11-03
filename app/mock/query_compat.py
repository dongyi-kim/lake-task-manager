"""App-scoped Jira820 compatibility for parent JQL and issue visibility.

Jira820 0.12 ignores unsupported fields, including ``parent``. Rewrite only that
field using the shared JQL parser and keep its existing evaluator/serializer for
the rest. This is installed on each mock app, never on a production transport or
the installed dependency's global functions.
"""
from __future__ import annotations

import json

from fastapi import Query, Request
from fastapi.responses import JSONResponse
from jira820.jql import filter_keys

from app.jira.jql import (And, Atom, JqlUnsupported, Not, _Parser,
                          _render_tokens, _split_order, _unquote, tokenize)


def parent_key(issue):
    parent = issue.get("parentKey") or issue.get("parent") or ""
    return str(parent.get("key", "") if isinstance(parent, dict) else parent).upper()


def _key_list(keys):
    # An empty IN is an internal evaluator expression, never sent to real Jira.
    return "key IN (" + ", ".join(json.dumps(key) for key in sorted(keys)) + ")"


def _parent_matches(store, atom, negate):
    tokens = tokenize(atom)
    if not tokens or _unquote(tokens[0].value).casefold() != "parent":
        return None
    rest = tokens[1:]
    if not rest:
        raise JqlUnsupported("parent requires =, !=, IN or NOT IN")
    op = rest.pop(0).value.upper()
    if op == "NOT" and rest and rest[0].value.upper() == "IN":
        op += " " + rest.pop(0).value.upper()
    if op not in {"=", "!=", "IN", "NOT IN"}:
        raise JqlUnsupported("Mock parent supports =, !=, IN and NOT IN")
    if op in {"IN", "NOT IN"}:
        if len(rest) < 3 or rest[0].kind != "LPAREN" or rest[-1].kind != "RPAREN":
            raise JqlUnsupported("parent IN requires a nonempty list")
        values = rest[1:-1]
        if any(token.kind not in ({"WORD", "STRING"} if i % 2 == 0 else {"COMMA"})
               for i, token in enumerate(values)) or len(values) % 2 == 0:
            raise JqlUnsupported("Invalid parent list")
        wanted = {_unquote(token.value).upper() for token in values[::2]}
    else:
        if len(rest) != 1 or rest[0].kind not in {"WORD", "STRING"}:
            raise JqlUnsupported("parent comparison requires one issue key")
        wanted = {_unquote(rest[0].value).upper()}
    negative = (op in {"!=", "NOT IN"}) != negate
    # Jira negative comparisons exclude EMPTY, rather than complementing all rows.
    return {key for key, issue in store.issues.items()
            if (parent := parent_key(issue)) and ((parent in wanted) != negative)}


def rewrite_parent_jql(store, raw):
    tokens = tokenize(raw or "")
    if not any(_unquote(token.value).casefold() == "parent" for token in tokens):
        return raw or ""
    body, order = _split_order(tokens)

    def render(node, negate=False):
        if isinstance(node, Not):
            return render(node.child, not negate)
        if isinstance(node, Atom):
            keys = _parent_matches(store, node.value, negate)
            if keys is not None:
                return _key_list(keys)
            if negate:
                return _key_list(set(store.issues) - set(filter_keys(store, node.value)))
            return node.value
        join = " AND " if isinstance(node, And) != negate else " OR "
        return "(" + join.join(render(child, negate) for child in node.children) + ")"

    query = render(_Parser(body).parse())
    return query + (" ORDER BY " + _render_tokens(order) if order else "")


def install_query_compat(app):
    """Replace this app's search route, retaining Jira820's response serializer."""
    @app.get("/rest/api/2/search")
    def search(request: Request, jql: str = Query(""), startAt: int = 0,
               maxResults: int = 50, fields: str = ""):
        store = request.app.state.store
        try:
            query = rewrite_parent_jql(store, jql)
        except JqlUnsupported as exc:
            return JSONResponse({"errorMessages": [str(exc)], "errors": {}}, status_code=400)
        keys = [key for key in filter_keys(store, query) if key not in store.denied_issue_keys]
        page = keys[startAt:startAt + maxResults]
        base = str(request.base_url).rstrip("/")
        return {"expand": "schema,names", "startAt": startAt, "maxResults": maxResults,
                "total": len(keys), "issues": [store.serializer.issue_res(store.issues[key], base, fields)
                                               for key in page]}

    # New FastAPI versions keep include_router() as lazy router nodes. Prepend
    # this app's override instead of mutating an installed shared router object.
    app.router.routes.insert(0, app.router.routes.pop())

    @app.middleware("http")
    async def issue_visibility(request, call_next):
        prefix = "/rest/api/2/issue/"
        path = request.url.path
        if path.startswith(prefix) and path[len(prefix):].split("/", 1)[0] in app.state.store.denied_issue_keys:
            return JSONResponse({"errorMessages": ["You do not have permission to view this issue."],
                                 "errors": {}}, status_code=403)
        return await call_next(request)
