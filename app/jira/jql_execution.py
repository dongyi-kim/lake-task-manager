"""Batch proven reusable memberships without evaluating arbitrary JQL locally."""

from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
import json

from app.jira.jql import And, Atom, _Parser, _unquote, tokenize


GROUP_SIZE = 5
_CATEGORIES = {"to do": "new", "new": "new", "in progress": "indeterminate",
               "indeterminate": "indeterminate", "done": "done"}
_REQUIRED_SELECTORS = ContextVar("jql_required_selectors", default=frozenset())


def selector_fields(group):
    return {member.selector for member in group if member.selector} | (
        {"status"} if any(member.category for member in group) else set())


@contextmanager
def projection_scope(group):
    """Carry physical-group requirements through synchronous fetch/cache helpers only."""
    token = _REQUIRED_SELECTORS.set(frozenset(selector_fields(group)))
    try:
        yield
    finally:
        _REQUIRED_SELECTORS.reset(token)


def row_projection_complete(row, fields, required=()):
    requested = {field.strip().lower() for field in str(fields or "").split(",")}
    standard = {"assignee", "reporter", "status"}
    selectors = set(required) | _REQUIRED_SELECTORS.get()
    required = (selectors | standard if requested & {"*all", "*navigable"} else
                {field for field in selectors if field.lower() in requested} | (standard & requested))
    values = row.get("fields") if isinstance(row, dict) else None
    return isinstance(values, dict) and required.issubset(values)


@dataclass(frozen=True)
class Member:
    index: int
    leaf: str
    fields: tuple
    family: str = ""
    selector: str = ""
    value: str = ""
    category: str = ""
    guard: tuple = ()


def _literal(token):
    if token.kind == "STRING" and token.value.startswith('"'):
        return json.loads(token.value)
    return _unquote(token.value)


def _member(index, leaf, fields, epic_field):
    opaque = Member(index, leaf, fields)
    epic_field = epic_field or ""
    node = _Parser(tokenize(leaf)).parse()
    atoms = node.children if isinstance(node, And) else (node,)
    selector, value, category, guards = "", "", "", []
    for atom in atoms:
        if not isinstance(atom, Atom):
            return opaque
        tokens = tokenize(atom.value)
        field = _unquote(tokens[0].value).lower() if tokens else ""
        if field in {"assignee", "reporter", "statuscategory", "epic link", epic_field.lower()}:
            if len(tokens) != 3 or tokens[1].value != "=":
                return opaque
            try:
                literal = _literal(tokens[2])
            except ValueError:
                return opaque  # Jira string escaping need not be JSON string escaping.
            if field == "statuscategory":
                if category or literal.lower() not in _CATEGORIES:
                    return opaque
                category = _CATEGORIES[literal.lower()]
            else:
                if selector or not literal:
                    return opaque
                if field == "epic link" and not epic_field:
                    return opaque
                selector = epic_field if field in {"epic link", epic_field.lower()} else field
                value = literal
        else:
            guards.append(atom.value)
    family = "people" if selector in {"assignee", "reporter"} else "epic" if selector else "status"
    if not selector and not category:
        return opaque
    return Member(index, leaf, fields, family, selector, value, category, tuple(guards))


def plan_groups(compiled, epic_field):
    members = tuple(_member(i, leaf, fields, epic_field) for i, (leaf, fields)
                    in enumerate(zip(compiled.leaves, compiled.leaf_fields)))
    if len(members) < 2 or any(not member.family for member in members):
        return (members,)
    groups = []
    for member in members:
        for group in groups:
            # Distinct status categories prove which guard Jira evaluated. Within a category,
            # identical remaining guards are mandatory even for different people/roles.
            if (group[0].family == member.family
                    and bool(group[0].category) == bool(member.category)
                    and all(row.category != member.category or row.guard == member.guard for row in group)
                    and (member.family == "status" or member.value in {row.value for row in group}
                         or len({row.value for row in group}) < GROUP_SIZE)):
                group.append(member)
                break
        else:
            groups.append([member])
    return tuple(tuple(group) for group in groups)


def group_query(group):
    return group[0].leaf if len(group) == 1 else " OR ".join(f"({row.leaf})" for row in group)


def partition_fields(fields, groups):
    if str(fields).lower() in {"*all", "*navigable"}:
        return fields
    required = set()
    for group in groups:
        if len(group) > 1 and all(row.family for row in group):
            required.update(row.selector for row in group if row.selector)
            if any(row.category for row in group):
                required.add("status")
    supplied = [field.strip() for field in str(fields).split(",") if field.strip()]
    return ",".join(supplied + sorted(required - set(supplied)))


def partition_rows(group, rows, known_users=()):
    if len(group) == 1:
        return [rows]
    if any(not member.family for member in group):
        return None
    # Jira also accepts display-name/email literals. A matching reporter does not prove that
    # an unmatched assignee literal has no rows: it may be an alias for that row's account.
    # Only verified canonical account names can support a per-member negative conclusion.
    verified = set(known_users)
    for row in rows:
        for role in ("assignee", "reporter"):
            user = (row.get("fields") or {}).get(role)
            if isinstance(user, dict) and user.get("name"):
                verified.add(user["name"])
    result = [None if rows and member.family == "people" and member.value not in verified
              else [] for member in group]
    selectors = {member.selector for member in group if member.selector}
    for row in rows:
        fields = row.get("fields") or {}
        if any(selector not in fields for selector in selectors):
            return None
        values = {}
        for selector in selectors:
            value = fields[selector]
            if selector in {"assignee", "reporter"}:
                if value is not None and (not isinstance(value, dict) or not value.get("name")):
                    return None
                value = value.get("name") if value else None
            elif isinstance(value, dict):
                value = value.get("key")
            values[selector] = value
        category = ((fields.get("status") or {}).get("statusCategory") or {}).get("key")
        if any(member.category for member in group) and category not in _CATEGORIES.values():
            return None
        matched = False
        for i, member in enumerate(group):
            if result[i] is None:
                continue
            if ((not member.category or member.category == category)
                    and (not member.selector or values[member.selector] == member.value)):
                result[i].append(row)
                matched = True
        if not matched and all(part is not None for part in result):
            return None  # The upstream response does not prove this query's memberships.
    return result
