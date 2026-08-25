"""JQL context preprocessing, parsing, normalization and local result composition.

This module intentionally implements the subset exercised by Lake Task Manager.  Jira and
plugin functions outside that subset are rejected so the caller can preserve compatibility by
falling back to Jira's monolithic search implementation.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cmp_to_key
from typing import Iterable
from zoneinfo import ZoneInfo


MAX_INPUT = 16 * 1024
MAX_LEAVES = 64
MAX_ATOMS_PER_LEAF = 64


class JqlUnsupported(ValueError):
    """The query must use the legacy Jira execution path."""


@dataclass(frozen=True)
class Token:
    kind: str
    value: str


@dataclass(frozen=True)
class Atom:
    value: str


@dataclass(frozen=True)
class And:
    children: tuple


@dataclass(frozen=True)
class Or:
    children: tuple


@dataclass(frozen=True)
class Not:
    child: object


@dataclass(frozen=True)
class OrderSpec:
    field: str
    direction: str = "ASC"


@dataclass(frozen=True)
class CompiledJql:
    canonical: str
    leaves: tuple[str, ...]
    leaf_fields: tuple[tuple[str, ...], ...]
    order: tuple[OrderSpec, ...]


_WORD_END = set(" \t\r\n(),<>!=~\"'")
_RELATIVE_RE = re.compile(r"^[+-]\d+[mhdw]$", re.I)
_OFFSET_RE = re.compile(r"^([+-])(\d+)([mhdwMy])$")
_START_END_RE = re.compile(r"^(start|end)of(day|week|month|year)$", re.I)
_SUPPORTED_OPAQUE_FUNCTIONS = {"now", "opensprints", "closedsprints", "futuresprints"}
_ORDER_ALIASES = {"due": "duedate", "resolved": "resolutiondate"}
_LOCAL_ORDER_FIELDS = {
    "key", "created", "updated", "resolutiondate", "duedate", "summary",
    "assignee", "reporter", "status", "priority", "issuetype", "project",
}


def tokenize(raw: str) -> list[Token]:
    if len(raw or "") > MAX_INPUT:
        raise JqlUnsupported("JQL이 16KiB 안전 한도를 초과했습니다.")
    out: list[Token] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            start = i
            i += 1
            escaped = False
            while i < len(raw):
                cur = raw[i]
                i += 1
                if escaped:
                    escaped = False
                elif cur == "\\":
                    escaped = True
                elif cur == quote:
                    break
            else:
                raise JqlUnsupported("JQL 문자열 따옴표가 닫히지 않았습니다.")
            out.append(Token("STRING", raw[start:i]))
            continue
        if ch == "(":
            out.append(Token("LPAREN", ch)); i += 1; continue
        if ch == ")":
            out.append(Token("RPAREN", ch)); i += 1; continue
        if ch == ",":
            out.append(Token("COMMA", ch)); i += 1; continue
        if ch in "<>!=~":
            op = ch
            if i + 1 < len(raw) and raw[i + 1] in "=~":
                op += raw[i + 1]
                i += 1
            out.append(Token("OP", op)); i += 1; continue
        start = i
        while i < len(raw) and raw[i] not in _WORD_END:
            i += 1
        if start == i:
            raise JqlUnsupported(f"지원하지 않는 JQL 문자: {raw[i:i + 1]}")
        out.append(Token("WORD", raw[start:i]))
    return out


def _keyword(token: Token | None, value: str) -> bool:
    return bool(token and token.kind == "WORD" and token.value.upper() == value)


def _split_order(tokens: list[Token]) -> tuple[list[Token], list[Token]]:
    depth = 0
    for i, token in enumerate(tokens):
        if token.kind == "LPAREN":
            depth += 1
        elif token.kind == "RPAREN":
            depth -= 1
            if depth < 0:
                raise JqlUnsupported("JQL 괄호가 올바르지 않습니다.")
        elif depth == 0 and _keyword(token, "ORDER"):
            if i + 1 < len(tokens) and _keyword(tokens[i + 1], "BY"):
                return tokens[:i], tokens[i + 2:]
    if depth:
        raise JqlUnsupported("JQL 괄호가 닫히지 않았습니다.")
    return tokens, []


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> Token:
        token = self.peek()
        if token is None:
            raise JqlUnsupported("JQL 조건이 비어 있습니다.")
        self.pos += 1
        return token

    def match(self, value: str) -> bool:
        if _keyword(self.peek(), value):
            self.pos += 1
            return True
        return False

    def parse(self):
        if not self.tokens:
            return Atom("")
        node = self.parse_or()
        if self.peek() is not None:
            raise JqlUnsupported(f"JQL을 해석할 수 없습니다: {self.peek().value}")
        return node

    def parse_or(self):
        children = [self.parse_and()]
        while self.match("OR"):
            children.append(self.parse_and())
        return children[0] if len(children) == 1 else Or(tuple(children))

    def parse_and(self):
        children = [self.parse_not()]
        while self.match("AND"):
            children.append(self.parse_not())
        return children[0] if len(children) == 1 else And(tuple(children))

    def parse_not(self):
        if self.match("NOT"):
            return Not(self.parse_not())
        return self.parse_primary()

    def parse_primary(self):
        if self.peek() and self.peek().kind == "LPAREN":
            self.take()
            node = self.parse_or()
            if not self.peek() or self.take().kind != "RPAREN":
                raise JqlUnsupported("JQL 그룹 괄호가 닫히지 않았습니다.")
            return node
        atom: list[Token] = []
        depth = 0
        while self.peek() is not None:
            token = self.peek()
            if depth == 0 and (token.kind == "RPAREN" or _keyword(token, "AND") or _keyword(token, "OR")):
                break
            token = self.take()
            if token.kind == "LPAREN":
                depth += 1
            elif token.kind == "RPAREN":
                depth -= 1
                if depth < 0:
                    self.pos -= 1
                    break
            atom.append(token)
        if not atom or depth:
            raise JqlUnsupported("JQL 비교식을 해석할 수 없습니다.")
        return Atom(_render_tokens(atom))


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_tokens(tokens: Iterable[Token]) -> str:
    out = ""
    previous: Token | None = None
    for token in tokens:
        value = token.value
        if token.kind == "COMMA":
            out = out.rstrip() + ", "
        elif token.kind == "LPAREN":
            if previous and previous.kind == "WORD" and previous.value.upper() not in {"IN"}:
                out = out.rstrip() + "("
            else:
                out = out.rstrip() + " ("
        elif token.kind == "RPAREN":
            out = out.rstrip() + ")"
        elif not out or out.endswith((" ", "(")):
            out += value
        else:
            out += " " + value
        previous = token
    return out.strip()


def _add_months(value: datetime, months: int) -> datetime:
    total = value.year * 12 + value.month - 1 + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _apply_offset(value: datetime, raw: str) -> datetime:
    if not raw:
        return value
    hit = _OFFSET_RE.fullmatch(raw)
    if not hit:
        raise JqlUnsupported(f"지원하지 않는 상대 날짜 offset입니다: {raw}")
    sign = -1 if hit.group(1) == "-" else 1
    amount = sign * int(hit.group(2))
    unit = hit.group(3)
    if unit == "m":
        return value + timedelta(minutes=amount)
    if unit.lower() == "h":
        return value + timedelta(hours=amount)
    if unit.lower() == "d":
        return value + timedelta(days=amount)
    if unit.lower() == "w":
        return value + timedelta(weeks=amount)
    if unit == "M":
        return _add_months(value, amount)
    if unit.lower() == "y":
        return _add_months(value, amount * 12)
    raise JqlUnsupported(f"지원하지 않는 상대 날짜 단위입니다: {unit}")


def _period_boundary(name: str, now: datetime, offset: str = "") -> datetime:
    hit = _START_END_RE.fullmatch(name)
    if not hit:
        raise JqlUnsupported(f"지원하지 않는 날짜 함수입니다: {name}")
    edge, period = hit.group(1).lower(), hit.group(2).lower()
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1) - timedelta(minutes=1)
    elif period == "week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7) - timedelta(minutes=1)
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = _add_months(start, 1) - timedelta(minutes=1)
    else:
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1) - timedelta(minutes=1)
    return _apply_offset(start if edge == "start" else end, offset)


def _absolute_relative(raw: str, now: datetime) -> str:
    return _apply_offset(now, raw).strftime("%Y-%m-%d %H:%M")


def _resolve_context(tokens: list[Token], user_id: str, now: datetime) -> list[Token]:
    out: list[Token] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.kind == "WORD" and _RELATIVE_RE.fullmatch(token.value):
            out.append(Token("STRING", _quote(_absolute_relative(token.value, now))))
            i += 1
            continue
        if token.kind == "WORD" and i + 1 < len(tokens) and tokens[i + 1].kind == "LPAREN":
            name = token.value
            lower = name.lower()
            # Whole-query preprocessing also sees boolean/grouping syntax.  These keywords may
            # be followed by ``(`` but are not function calls.
            if lower in {"in", "and", "or", "not", "order", "by", "is"}:
                out.append(token); i += 1; continue
            depth = 0
            end = None
            for j in range(i + 1, len(tokens)):
                if tokens[j].kind == "LPAREN":
                    depth += 1
                elif tokens[j].kind == "RPAREN":
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
            if end is None:
                raise JqlUnsupported(f"JQL 함수 괄호가 닫히지 않았습니다: {name}")
            args = tokens[i + 2:end]
            if lower == "currentuser":
                if args or not user_id:
                    raise JqlUnsupported("currentUser() context를 확인할 수 없습니다.")
                out.append(Token("STRING", _quote(user_id)))
            elif _START_END_RE.fullmatch(name):
                if len(args) > 1 or (args and args[0].kind not in {"STRING", "WORD"}):
                    raise JqlUnsupported(f"지원하지 않는 날짜 함수 인자입니다: {name}")
                offset = _unquote(args[0].value) if args else ""
                out.append(Token("STRING", _quote(_period_boundary(name, now, offset).strftime("%Y-%m-%d %H:%M"))))
            elif lower in _SUPPORTED_OPAQUE_FUNCTIONS:
                out.extend(tokens[i:end + 1])
            else:
                # Custom/plugin and context-heavy Jira functions are intentionally excluded.
                # Examples: membersOf, linkedIssuesOf, issueHistory, watchedIssues, currentLogin.
                raise JqlUnsupported(f"지원하지 않는 JQL 함수입니다: {name}")
            i = end + 1
            continue
        out.append(token)
        i += 1
    return out


def _sort_in_lists(tokens: list[Token]) -> list[Token]:
    out = list(tokens)
    i = 0
    while i + 1 < len(out):
        if not _keyword(out[i], "IN") or out[i + 1].kind != "LPAREN":
            i += 1
            continue
        depth = 0
        end = None
        for j in range(i + 1, len(out)):
            if out[j].kind == "LPAREN":
                depth += 1
            elif out[j].kind == "RPAREN":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end is None:
            raise JqlUnsupported("IN 목록 괄호가 닫히지 않았습니다.")
        values: list[list[Token]] = []
        current: list[Token] = []
        nested = 0
        for item in out[i + 2:end]:
            if item.kind == "LPAREN":
                nested += 1
            elif item.kind == "RPAREN":
                nested -= 1
            if item.kind == "COMMA" and nested == 0:
                values.append(current); current = []
            else:
                current.append(item)
        values.append(current)
        if any(not value for value in values):
            raise JqlUnsupported("IN 목록에 빈 값이 있습니다.")
        unique = {_render_tokens(value): value for value in values}
        replacement: list[Token] = []
        for index, key in enumerate(sorted(unique, key=lambda value: value.casefold())):
            if index:
                replacement.append(Token("COMMA", ","))
            replacement.extend(unique[key])
        out = out[:i + 2] + replacement + out[end:]
        i += len(replacement) + 2
    return out


def preprocess_context_jql(raw: str, *, user_id: str = "", timezone_name: str = "UTC",
                           now: datetime | None = None, ttl_seconds: int = 900) -> str:
    """Resolve the supported request context before any syntax parser sees the JQL.

    Context policy is deliberately independent from the hand-written parser and the Lark POC.
    The small tokenizer is quote-aware, so strings such as ``summary ~ "currentUser()"`` are
    never rewritten.  Unsupported Jira/plugin functions still force the caller onto Jira's
    monolithic compatibility path.
    """
    cleaned = (raw or "").strip().rstrip(";").strip()
    tokens = tokenize(cleaned)
    try:
        tz = ZoneInfo(timezone_name or "UTC")
    except Exception:
        tz = timezone.utc
    if now is None:
        stamp = datetime.now(tz).timestamp()
    else:
        current = now if now.tzinfo else now.replace(tzinfo=tz)
        stamp = current.astimezone(tz).timestamp()
    bucket = max(1, int(ttl_seconds or 900))
    context_now = datetime.fromtimestamp((int(stamp) // bucket) * bucket, tz)
    return _render_tokens(_resolve_context(tokens, user_id, context_now))


def _normalize_atom(value: str) -> str:
    tokens = tokenize(value)
    tokens = _sort_in_lists(tokens)
    return _render_tokens(tokens)


def _atom_field(value: str) -> str:
    """Return the field referenced by one normalized DNF atom.

    Leaf membership only depends on predicate fields, not on the result projection or ORDER BY.
    Keeping this alongside the canonical leaf lets the cache layer invalidate just the leaves a
    successful mutation can move an issue into or out of.
    """
    raw = str(value or "").strip()
    if raw.upper().startswith("NOT (") and raw.endswith(")"):
        raw = raw[5:-1].strip()
    tokens = tokenize(raw)
    if not tokens or tokens[0].kind not in {"WORD", "STRING"}:
        raise JqlUnsupported("JQL 비교식의 필드를 확인할 수 없습니다.")
    return _unquote(tokens[0].value).strip().lower()


def _normalize_tree(node):
    if isinstance(node, Atom):
        return Atom(_normalize_atom(node.value))
    if isinstance(node, Not):
        return Not(_normalize_tree(node.child))
    children = []
    cls = And if isinstance(node, And) else Or
    for child in node.children:
        normalized = _normalize_tree(child)
        if isinstance(normalized, cls):
            children.extend(normalized.children)
        else:
            children.append(normalized)
    unique = {_node_key(child): child for child in children}
    ordered = tuple(unique[key] for key in sorted(unique, key=str.casefold))
    return ordered[0] if len(ordered) == 1 else cls(ordered)


def _node_key(node) -> str:
    if isinstance(node, Atom):
        return node.value
    if isinstance(node, Not):
        return f"NOT ({_node_key(node.child)})"
    op = " AND " if isinstance(node, And) else " OR "
    return op.join(f"({_node_key(child)})" for child in node.children)


def _nnf(node, negate: bool = False):
    if isinstance(node, Atom):
        return Not(node) if negate else node
    if isinstance(node, Not):
        return _nnf(node.child, not negate)
    if isinstance(node, And):
        cls = Or if negate else And
    else:
        cls = And if negate else Or
    return cls(tuple(_nnf(child, negate) for child in node.children))


def _dnf(node) -> list[tuple[str, ...]]:
    if isinstance(node, Atom):
        return [(node.value,)] if node.value else [tuple()]
    if isinstance(node, Not):
        if not isinstance(node.child, Atom):
            raise JqlUnsupported("NOT 정규화에 실패했습니다.")
        return [(f"NOT ({node.child.value})",)]
    if isinstance(node, Or):
        rows: list[tuple[str, ...]] = []
        for child in node.children:
            rows.extend(_dnf(child))
            if len(rows) > MAX_LEAVES:
                raise JqlUnsupported("OR 분해 결과가 64개 leaf를 초과했습니다.")
        return rows
    rows = [tuple()]
    for child in node.children:
        child_rows = _dnf(child)
        rows = [left + right for left in rows for right in child_rows]
        if len(rows) > MAX_LEAVES:
            raise JqlUnsupported("JQL DNF 전개 결과가 64개 leaf를 초과했습니다.")
    return rows


def _parse_order(tokens: list[Token]) -> tuple[OrderSpec, ...]:
    if not tokens:
        return (OrderSpec("key", "ASC"),)
    chunks: list[list[Token]] = []
    current: list[Token] = []
    for token in tokens:
        if token.kind == "COMMA":
            if not current:
                raise JqlUnsupported("ORDER BY 항목이 비어 있습니다.")
            chunks.append(current); current = []
        else:
            current.append(token)
    if current:
        chunks.append(current)
    specs: list[OrderSpec] = []
    for chunk in chunks:
        if len(chunk) not in {1, 2} or chunk[0].kind not in {"WORD", "STRING"}:
            raise JqlUnsupported("로컬 정렬로 지원하지 않는 ORDER BY입니다.")
        field = _unquote(chunk[0].value).lower()
        field = _ORDER_ALIASES.get(field, field)
        direction = "ASC"
        if len(chunk) == 2:
            direction = chunk[1].value.upper()
            if direction not in {"ASC", "DESC"}:
                raise JqlUnsupported("ORDER BY 방향은 ASC 또는 DESC여야 합니다.")
        if field not in _LOCAL_ORDER_FIELDS:
            raise JqlUnsupported(f"로컬 정렬을 보장할 수 없는 필드입니다: {field}")
        if not any(existing.field == field for existing in specs):
            specs.append(OrderSpec(field, direction))
    if not any(spec.field == "key" for spec in specs):
        specs.append(OrderSpec("key", "ASC"))
    return tuple(specs)


def _compile_ast(parsed, order: tuple[OrderSpec, ...]) -> CompiledJql:
    """Apply parser-independent normalization, DNF and safety policies to an AST."""
    normalized = _normalize_tree(parsed)
    normalized = _nnf(normalized)
    rows = _dnf(normalized)
    clean: set[tuple[str, ...]] = set()
    for row in rows:
        atoms = tuple(sorted(set(row), key=str.casefold))
        if len(atoms) > MAX_ATOMS_PER_LEAF:
            raise JqlUnsupported("한 leaf의 조건이 64개를 초과했습니다.")
        clean.add(atoms)
    ordered_rows = tuple(sorted(clean, key=lambda x: tuple(y.casefold() for y in x)))
    leaves = tuple(" AND ".join(atoms) for atoms in ordered_rows)
    leaf_fields = tuple(
        tuple(sorted({_atom_field(atom) for atom in atoms}))
        for atoms in ordered_rows
    )
    if not leaves:
        leaves = ("",)
        leaf_fields = (tuple(),)
    order_text = ", ".join(f"{spec.field} {spec.direction}" for spec in order)
    base = " OR ".join(f"({leaf})" for leaf in leaves) if len(leaves) > 1 else leaves[0]
    canonical = (base + " ORDER BY " + order_text).strip()
    return CompiledJql(canonical=canonical, leaves=leaves, leaf_fields=leaf_fields, order=order)


def compile_jql(raw: str, *, user_id: str = "", timezone_name: str = "UTC",
                now: datetime | None = None, ttl_seconds: int = 900) -> CompiledJql:
    prepared = preprocess_context_jql(
        raw, user_id=user_id, timezone_name=timezone_name, now=now,
        ttl_seconds=ttl_seconds)
    tokens = tokenize(prepared)
    condition_tokens, order_tokens = _split_order(tokens)
    parsed = _Parser(condition_tokens).parse()
    return _compile_ast(parsed, _parse_order(order_tokens))


def fields_with_order(fields: str, order: Iterable[OrderSpec]) -> str:
    raw = str(fields or "").strip()
    if raw in {"*all", "*navigable"}:
        return raw
    values = [part.strip() for part in raw.split(",") if part.strip()]
    seen = {value.lower() for value in values}
    for spec in order:
        if spec.field != "key" and spec.field not in seen:
            values.append(spec.field)
            seen.add(spec.field)
    return ",".join(values)


def _natural_key(value: str):
    match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)-(\d+)", str(value or ""))
    return (match.group(1).casefold(), int(match.group(2))) if match else (str(value or "").casefold(), -1)


def _priority_key(value):
    name = str(value or "")
    hit = re.search(r"\bP([0-9]+)\b", name, re.I)
    if hit:
        return int(hit.group(1)), name.casefold()
    words = name.casefold()
    rank = {"blocker": 0, "critical": 1, "major": 2, "minor": 3, "trivial": 4}
    for word, number in rank.items():
        if word in words:
            return number, words
    return 999, words


def _sort_value(issue: dict, field: str):
    if field == "key":
        return _natural_key(issue.get("key") or "")
    fields = (issue or {}).get("fields") or {}
    value = fields.get(field)
    if field in {"assignee", "reporter"}:
        value = (value or {}).get("displayName") or (value or {}).get("name")
    elif field in {"status", "issuetype", "project"}:
        value = (value or {}).get("name") or (value or {}).get("key")
    elif field == "priority":
        return _priority_key((value or {}).get("name"))
    if isinstance(value, dict):
        value = value.get("name") or value.get("value") or value.get("key")
    if isinstance(value, list):
        value = tuple(str((item or {}).get("name") if isinstance(item, dict) else item).casefold()
                      for item in value)
    if isinstance(value, str):
        return value.casefold()
    return value


def sort_issues(issues: Iterable[dict], order: Iterable[OrderSpec]) -> list[dict]:
    specs = tuple(order)

    def compare(left: dict, right: dict) -> int:
        for spec in specs:
            lv, rv = _sort_value(left, spec.field), _sort_value(right, spec.field)
            if lv is None and rv is None:
                continue
            if lv is None:
                return 1
            if rv is None:
                return -1
            try:
                result = (lv > rv) - (lv < rv)
            except TypeError:
                result = (str(lv) > str(rv)) - (str(lv) < str(rv))
            if result:
                return -result if spec.direction == "DESC" else result
        return 0

    return sorted(list(issues), key=cmp_to_key(compare))
