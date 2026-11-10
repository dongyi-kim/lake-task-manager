"""Batch proven reusable memberships without evaluating arbitrary JQL locally."""

from dataclasses import dataclass, replace
from contextlib import contextmanager
from contextvars import ContextVar
import json

from app.jira.jql import And, Atom, CompiledJql, _Parser, _unquote, tokenize
from app.jira.jql_budget import (
    PhysicalQueryBudgetExceeded, claim_physical_query, physical_query_scope, query_identity,
    validate_max_parts,
)


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
    whole: bool = False


@dataclass(frozen=True)
class ExecutionPlan:
    compiled: CompiledJql
    groups: tuple[tuple[Member, ...], ...]
    requested_strategy: str
    effective_strategy: str
    max_parts: int
    logical_leaf_count: int | None
    physical_group_count: int
    fallback_reason: str | None = None

    def diagnostics(self):
        return {"requestedStrategy": self.requested_strategy,
                "effectiveStrategy": self.effective_strategy,
                "maxParts": self.max_parts, "logicalLeafCount": self.logical_leaf_count,
                "physicalGroupCount": self.physical_group_count,
                "fallbackReason": self.fallback_reason}


def build_execution_plan(compiled, epic_field, *, strategy="auto", max_parts=64,
                         covered_leaves=(), pending_queries=None, groups=None):
    """Select requests using proven cache coverage, without upstream cost probes.

    ``covered_leaves`` proves membership, projection and freshness. A callable receives
    the selected groups once, allowing projection-aware coverage without replanning.
    An opaque union never claims to partition an arbitrary JQL.
    """
    if not isinstance(strategy, str) or strategy not in {"auto", "off", "people", "full"}:
        raise ValueError("strategy must be auto, off, people or full")
    validate_max_parts(max_parts)
    if groups is None:
        groups = plan_groups(compiled, epic_field, strategy=strategy)
    covered = set(covered_leaves(groups) if callable(covered_leaves) else covered_leaves)
    queries = ({query_identity(query) for query in pending_queries(groups, covered)}
               if pending_queries is not None else
               {query_identity(group_query(missing)) for group in groups
                if (missing := tuple(member for member in group if member.leaf not in covered))})
    effective, reason = strategy, None
    if not compiled.dnf_expanded and strategy != "off":
        effective, reason = "off", "dnf_not_expanded"
    if len(queries) > max_parts:
        # A true monolithic member avoids dispatching separate hydration for each cached
        # logical membership after discovering that their physical cost is unaffordable.
        groups = _whole_group(compiled, tuple(member for group in groups for member in group))
        queries = {query_identity(groups[0][0].leaf)}
        effective, reason = "off", "physical_query_budget"
    return ExecutionPlan(compiled, groups, strategy, effective, max_parts,
                         len(compiled.leaves) if compiled.dnf_expanded else None,
                         len(queries), reason)


def _whole_group(compiled, members):
    fields = tuple(sorted({field for member in members for field in member.fields}))
    return ((Member(0, compiled.whole_query or group_query(members), fields, whole=True),),)


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


def plan_groups(compiled, epic_field, *, strategy="auto"):
    """Choose structural groups and projection requirements without reading the cache."""
    if not isinstance(strategy, str) or strategy not in {"auto", "off", "people", "full"}:
        raise ValueError("strategy must be auto, off, people or full")
    members = tuple(_member(i, leaf, fields, epic_field) for i, (leaf, fields)
                    in enumerate(zip(compiled.leaves, compiled.leaf_fields)))
    if strategy == "off" or not compiled.dnf_expanded:
        return _whole_group(compiled, members)
    if strategy == "full":
        return tuple((member,) for member in members)
    if strategy == "people":
        people = tuple(member for member in members if member.family == "people")
        opaque = tuple(replace(member, family="", selector="", value="", category="", guard=())
                       for member in members if member.family != "people")
        return _group_members(people) + ((opaque,) if opaque else ())
    # Omitted auto retains the deployed proof/group selection until qualification.
    return _group_members(members)


def _group_members(members):
    if not members:
        return ()
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
