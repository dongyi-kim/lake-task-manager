"""Typed exact-field request checks shared by multi-turn evaluator suites."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_TARGET_RE = re.compile(r"(?<![A-Z0-9-])([A-Z][A-Z0-9]{1,9}-\d+)(?!\d)", re.I)
_FIELD_PATTERNS = {
    "priority": re.compile(
        r"(?:priority|우선순위)\s*(?:를|을|은|는|이|가|:|=)?\s*"
        r"(P[0-5](?:-[A-Za-z][A-Za-z-]*)?)",
        re.I,
    ),
    "duedate": re.compile(
        r"(?:due(?:\s*date)?|duedate|기한|마감(?:일)?)\s*"
        r"(?:를|을|은|는|이|가|:|=)?\s*(\d{4}-\d{2}-\d{2})",
        re.I,
    ),
    "summary": re.compile(
        r"(?:summary|제목)\s*(?:만)?\s*(?:을|를|은|는|이|가|:|=)?\s*"
        r"['\"`](.+?)['\"`]",
        re.I,
    ),
    "assignee": re.compile(
        r"(?:assignee|담당자?)\s*(?:를|을|은|는|이|가|:|=)?\s*"
        r"(미할당|unassigned|[a-z][a-z0-9.-]{2,})",
        re.I,
    ),
}
_FIELD_ALIASES = {
    "due": "duedate",
    "due_date": "duedate",
    "title": "summary",
    "owner": "assignee",
}


@dataclass(frozen=True, order=True)
class RequestedField:
    target: str
    field: str
    value: str


def extract_requested_fields(text: str) -> tuple[RequestedField, ...]:
    """Extract only unambiguous literal ``(target, field, value)`` requests."""
    targets = list(dict.fromkeys(value.upper() for value in _TARGET_RE.findall(str(text or ""))))
    if len(targets) != 1:
        return ()
    target = targets[0]
    positioned: list[tuple[int, str, str]] = []
    for field, pattern in _FIELD_PATTERNS.items():
        for match in pattern.finditer(str(text or "")):
            positioned.append((match.start(), field, str(match.group(1)).strip()))
    latest: dict[str, tuple[int, str]] = {}
    for position, field, value in positioned:
        latest[field] = (position, value)
    return tuple(
        RequestedField(target, field, value)
        for field, (_position, value) in sorted(latest.items(), key=lambda row: row[1][0])
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _canonical_changes(pending: Mapping[str, Any]) -> dict[str, str]:
    changes = _mapping(pending.get("changes"))
    result: dict[str, str] = {}
    for raw_field, value in changes.items():
        field = _FIELD_ALIASES.get(str(raw_field).strip().lower(),
                                   str(raw_field).strip().lower())
        result[field] = str(value or "")
    return result


def intermediate_request_field_flaws(
    inputs: Sequence[str], outputs: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Verify every non-final turn's explicit update fields in payload and reply.

    A later replacement is evaluated by its own case contract.  It cannot retroactively
    make an incomplete earlier approval card correct.
    """
    flaws: list[str] = []
    for index, request in enumerate(inputs[:-1]):
        expected = extract_requested_fields(request)
        if not expected:
            continue
        output = _mapping(outputs[index]) if index < len(outputs) else {}
        pending = _mapping(output.get("pending"))
        target = expected[0].target
        keys = [str(value or "").upper() for value in (pending.get("keys") or [])]
        if not keys and pending.get("key"):
            keys = [str(pending.get("key") or "").upper()]
        action = str(pending.get("action") or "")
        if action not in {"update_ticket", "update_tickets"} or keys != [target]:
            flaws.append(
                f"turn[{index}] pending exact target/action 불일치: "
                f"target={target}, action={action or '없음'}, keys={keys or ['없음']}"
            )
        actual = _canonical_changes(pending)
        exact = {fact.field: fact.value for fact in expected}
        if actual != exact:
            for fact in expected:
                if actual.get(fact.field) != fact.value:
                    flaws.append(
                        f"turn[{index}] pending exact field 누락/불일치: "
                        f"({fact.target}, {fact.field}, {fact.value})"
                    )
            extras = sorted(set(actual) - set(exact))
            if extras:
                flaws.append(
                    f"turn[{index}] pending 요청하지 않은 field 포함: {', '.join(extras)}"
                )
        reply = str(output.get("reply") or "")
        for fact in expected:
            if fact.target not in reply.upper() or fact.value not in reply:
                flaws.append(
                    f"turn[{index}] reply exact field 누락/불일치: "
                    f"({fact.target}, {fact.field}, {fact.value})"
                )
    return flaws


REQUEST_FIELD_DEPENDENCIES = (
    _TARGET_RE, _FIELD_PATTERNS, _FIELD_ALIASES, RequestedField,
    extract_requested_fields, _mapping, _canonical_changes,
    intermediate_request_field_flaws,
)


__all__ = [
    "REQUEST_FIELD_DEPENDENCIES", "RequestedField", "extract_requested_fields",
    "intermediate_request_field_flaws",
]
