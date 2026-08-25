"""Lark-backed parser POC for Lake Task Manager's supported JQL subset.

This module is intentionally not wired into ``JiraClient`` yet.  It proves that a maintained
grammar library can replace the recursive syntax parser while reusing LTM's context, canonical
ordering, DNF, safety-limit and fallback policies from :mod:`app.jira.jql`.
"""

from __future__ import annotations

from functools import lru_cache

from lark import Lark, Tree
from lark.exceptions import LarkError

from app.jira.jql import (
    And,
    Atom,
    JqlUnsupported,
    Not,
    Or,
    _compile_ast,
    _parse_order,
    preprocess_context_jql,
    tokenize,
)


_GRAMMAR = r"""
    start: expression order_clause?
         | order_clause

    ?expression: disjunction
    ?disjunction: conjunction (_OR conjunction)*  -> disjunction
    ?conjunction: negation (_AND negation)*        -> conjunction
    ?negation: _NOT negation                       -> negation
             | "(" expression ")"                 -> grouped
             | atom

    atom: field predicate
    field: scalar
    ?predicate: COMPARISON_OP operand
              | _IN operand
              | _NOT _IN operand
              | _IS _NOT? empty_value
    ?operand: value
            | value_list
    value_list: "(" [value ("," value)*] ")"
    ?value: scalar
          | function
    function: BARE "(" [value ("," value)*] ")"
    ?scalar: STRING
           | BARE
    empty_value: _EMPTY | _NULL

    order_clause: _ORDER _BY order_item ("," order_item)*
    order_item: field DIRECTION?

    _AND.10: /(?i:AND)\b/
    _OR.10: /(?i:OR)\b/
    _NOT.10: /(?i:NOT)\b/
    _IN.10: /(?i:IN)\b/
    _IS.10: /(?i:IS)\b/
    _EMPTY.10: /(?i:EMPTY)\b/
    _NULL.10: /(?i:NULL)\b/
    _ORDER.10: /(?i:ORDER)\b/
    _BY.10: /(?i:BY)\b/
    DIRECTION.10: /(?i:ASC|DESC)\b/
    COMPARISON_OP: "!=" | ">=" | "<=" | "!~" | "=" | ">" | "<" | "~"
    STRING: /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/
    BARE: /[^\s(),<>!=~"']+/

    %import common.WS
    %ignore WS
"""


@lru_cache(maxsize=1)
def _parser() -> Lark:
    # Do not enable Lark's on-disk grammar cache: LTM workspace policy forbids incidental temp
    # artifacts, and this POC's grammar is small enough to initialize in memory.
    return Lark(
        _GRAMMAR,
        parser="lalr",
        lexer="contextual",
        propagate_positions=True,
        maybe_placeholders=False,
    )


def _children(node: Tree) -> list[Tree]:
    return [child for child in node.children if isinstance(child, Tree)]


def _to_ast(node: Tree, source: str):
    kind = str(node.data)
    children = _children(node)
    if kind == "atom":
        return Atom(source[node.meta.start_pos:node.meta.end_pos].strip())
    if kind == "grouped":
        if len(children) != 1:
            raise JqlUnsupported("Lark JQL 그룹을 AST로 변환할 수 없습니다.")
        return _to_ast(children[0], source)
    if kind == "negation":
        if len(children) != 1:
            raise JqlUnsupported("Lark JQL NOT 절을 AST로 변환할 수 없습니다.")
        return Not(_to_ast(children[0], source))
    if kind in {"conjunction", "disjunction"}:
        values = tuple(_to_ast(child, source) for child in children)
        if not values:
            raise JqlUnsupported("Lark JQL 논리식을 AST로 변환할 수 없습니다.")
        if len(values) == 1:
            return values[0]
        return (And if kind == "conjunction" else Or)(values)
    raise JqlUnsupported(f"Lark JQL AST가 지원하지 않는 노드입니다: {kind}")


def _parsed_parts(tree: Tree, source: str):
    nodes = _children(tree)
    order_node = next((node for node in nodes if str(node.data) == "order_clause"), None)
    expression = next((node for node in nodes if str(node.data) != "order_clause"), None)
    parsed = _to_ast(expression, source) if expression is not None else Atom("")
    if order_node is None:
        order = _parse_order([])
    else:
        order_source = source[order_node.meta.start_pos:order_node.meta.end_pos]
        order_tokens = tokenize(order_source)
        if len(order_tokens) < 2:
            raise JqlUnsupported("Lark ORDER BY 절을 변환할 수 없습니다.")
        order = _parse_order(order_tokens[2:])
    return parsed, order


def compile_jql_lark(raw: str, *, user_id: str = "", timezone_name: str = "UTC",
                     now=None, ttl_seconds: int = 900):
    """Compile JQL with context preprocessing followed by the Lark syntax parser.

    Invalid or out-of-scope syntax is translated to :class:`JqlUnsupported`, preserving the
    production contract that callers may safely fall back to Jira's whole-query execution.
    """
    prepared = preprocess_context_jql(
        raw, user_id=user_id, timezone_name=timezone_name, now=now,
        ttl_seconds=ttl_seconds)
    if not prepared:
        return _compile_ast(Atom(""), _parse_order([]))
    try:
        tree = _parser().parse(prepared)
        parsed, order = _parsed_parts(tree, prepared)
        return _compile_ast(parsed, order)
    except JqlUnsupported:
        raise
    except LarkError as exc:
        raise JqlUnsupported(f"Lark가 지원하지 않는 JQL입니다: {exc}") from exc
