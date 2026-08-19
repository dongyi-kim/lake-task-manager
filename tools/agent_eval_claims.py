"""Pure deterministic measurement gates for Agent evaluation batteries.

The runtime owns generation and grounding.  This evaluator-only module consumes explicit
check records and typed source identities; it does not infer product-specific truth from a
case name.  Natural-language parsing is deliberately limited to exact date arithmetic,
count/percentage arithmetic, and literal date/quantity claims explicitly bound to typed
source-index identities.

Runtime evidence renderers also recognize some date and quantity syntax in order to repair
prose before display.  Importing those repair functions here would let the system under test
self-validate and would couple historical raw replay to the current runtime.  The bounded
syntax below is therefore an independent stdlib-only measurement implementation: it reports
defects, never mutates output, and its source is fingerprinted into the battery manifest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit


_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_CLAUSE_RE = re.compile(r"(?:[^\n.!?;|]|(?<=\d)\.(?=\d))+")
_DATE_CONNECTOR_RE = re.compile(
    r"^\s*(?:에서|부터|~|～|→|->|to|through|until|–|—|-)\s*$", re.I,
)
_DURATION_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<approx>약\s*|대략\s*|around\s+|about\s+|approximately\s+)?"
    r"(?P<count>한|두|세|네|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>주(?:일)?|일|weeks?|days?)(?![A-Za-z가-힣])",
    re.I,
)
_APPROX_DURATION_SUFFIX_RE = re.compile(
    r"^\s*(?:정도|가량|내외|반)"
    r"(?=\s|[.,;:!?]|$|이|입|였|었|로|의|가|는|은)", re.I,
)
_DATE_RELATION_RE = re.compile(
    r"연기|연장|미뤄|늦춰|앞당|변경|차이|기간|늘어|줄어|"
    r"delay|extend|extension|shift|move|span|duration|difference|lasted|took",
    re.I,
)
_DATE_DURATION_PREDICATE_RE = re.compile(
    r"^\s*(?:이|가|은|는|만큼|정도)?\s*(?:연기|연장|미뤄|늦춰|앞당|"
    r"차이|기간|늘어|줄어|delay|extend|extension|shift|span|duration|difference)",
    re.I,
)
_DATE_DURATION_BRIDGE_PREDICATE_RE = re.compile(
    r"^\s*(?:lasted|took|spanned|was)\s*$", re.I,
)
_DATE_DURATION_BY_RE = re.compile(r"^\s*by\s*$", re.I)
_FRACTION_RE = re.compile(
    r"(?<![\d/])(?P<part>\d+(?:\.\d+)?)\s*/\s*"
    r"(?P<total>\d+(?:\.\d+)?)",
)
_PERCENT_RE = re.compile(
    r"(?P<approx>약\s*|대략\s*|around\s+|about\s+|approximately\s+)?"
    r"(?P<percent>\d+(?:\.\d+)?)\s*%",
    re.I,
)
_CLAIM_BOUNDARY_RE = re.compile(r"[\n.!?;|]")
_PERCENT_RELATION_BRIDGE_RE = re.compile(
    r"완료|진척|진행률|비율|complete|progress|rate|ratio", re.I,
)
_QUANTITY_UNIT_PATTERN = (
    r"표본|후보|테이블|티켓|작업|문서|사람|인원|명|건|"
    r"samples?|candidates?|tables?|tickets?|tasks?|documents?|people|persons?|items?"
)
_QUANTITY_BOUNDARY_PATTERN = (
    r"(?=$|[\s,.;:)\]}]|은|는|이|가|을|를|의|로|으|중|에서|와|과|입|인|임|였)"
)
_QUANTITY_RE = re.compile(
    rf"(?<![\w.])(?P<value>\d+(?:\.\d+)?)\s*"
    rf"(?:(?:개|건|명)\s*(?:의\s*)?)?(?P<unit>{_QUANTITY_UNIT_PATTERN})"
    + _QUANTITY_BOUNDARY_PATTERN,
    re.I,
)
_QUANTITY_UNIT_FIRST_RE = re.compile(
    rf"(?<![\w.])(?P<unit>{_QUANTITY_UNIT_PATTERN})\s*"
    rf"(?:은|는|이|가|을|를|의|에서|중)?\s*"
    rf"(?P<value>\d+(?:\.\d+)?)\s*(?:개|건|명)"
    + _QUANTITY_BOUNDARY_PATTERN,
    re.I,
)
_GENERIC_COUNT_RE = re.compile(
    r"(?<![\w.])(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>개|건|명)"
    + _QUANTITY_BOUNDARY_PATTERN,
    re.I,
)
_COUNT_RELATION_CONNECTOR_RE = re.compile(
    r"^\s*(?:(?P<total_first>중|가운데)|"
    r"(?P<part_first>(?:out\s+)?of))\s*$", re.I,
)
_TICKET_TOKEN_RE = re.compile(
    r"\{\{ticket-detail:\s*([A-Z][A-Z0-9]{1,9}-\d+)\s*\}\}", re.I,
)
_MARKDOWN_URL_RE = re.compile(r"\[[^\]\n]+\]\((https?://[^)\s]+)\)", re.I)
_SOURCE_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*(?:근거|참조|evidence|sources?|references?)|"
    r"\*\*\s*(?:근거|참조|evidence|sources?|references?)\s*\*\*)\s*$", re.I,
)
_ATX_HEADING_RE = re.compile(r"^\s*#{1,6}(?:\s+|$)")
_STANDALONE_BOLD_HEADING_RE = re.compile(
    r"^\s*\*\*\s*\S(?:.*?\S)?\s*\*\*\s*$",
)
_SOURCE_ROOT_RE = re.compile(r"^\s*\[(\d+)\]\s+(.+)$")
_SOURCE_CHILD_RE = re.compile(r"^\s*-\s*\[(\d+)-([a-z])\]\s*(.+)$", re.I)
_SOURCE_BULLET_RE = re.compile(r"^\s*-\s+(.+)$")
_DIRECT_NUMERIC_CITATION_RE = re.compile(
    r"(?<!!)\[(\d+)(?:-[a-z])?\](?![-\w]|\s*\()", re.I,
)


@dataclass(frozen=True, slots=True)
class RequiredBoolean:
    check_id: str
    actual: bool | None


@dataclass(frozen=True, slots=True)
class UnresolvedViolation:
    check_id: str
    count: int | None


@dataclass(frozen=True, slots=True)
class MeasurementAvailability:
    """Typed evaluator health, kept separate from product-output violations."""

    check_id: str
    available: bool
    error_type: str = ""

    def as_dict(self) -> dict[str, str | bool]:
        return {"available": self.available, "errorType": self.error_type}


@dataclass(frozen=True, slots=True)
class TextRecord:
    record_id: str
    text: str


@dataclass(frozen=True, slots=True)
class SourceAuthority:
    source_id: str
    text: str


@dataclass(frozen=True, slots=True)
class DirectSourceClaim:
    claim_id: str
    source_id: str
    text: str


@dataclass(frozen=True, order=True, slots=True)
class _LiteralValue:
    kind: str
    value: str
    unit: str = ""


@dataclass(frozen=True, order=True, slots=True)
class _CountAtom:
    start: int
    end: int
    value: str
    unit: str


@dataclass(frozen=True, order=True, slots=True)
class _CountRelation:
    start: int
    end: int
    total: str
    part: str


def measurement_gate_flaws(
    required_booleans: Sequence[RequiredBoolean],
    unresolved_violations: Sequence[UnresolvedViolation],
    availability: Sequence[MeasurementAvailability] = (),
) -> list[str]:
    """Turn explicit evaluator measurements into hard automatic defects.

    ``None`` remains optional for historical callers. A caller that supplies typed availability
    explicitly makes evaluator failure a hard defect, without mislabelling it as a product
    grounding or postcheck violation.
    """
    flaws: list[str] = []
    for status in availability:
        if status.available:
            continue
        detail = f" errorType={status.error_type}" if status.error_type else ""
        flaws.append(
            f"evaluator infrastructure failure: measurement unavailable: "
            f"{status.check_id}{detail}"
        )
    for requirement in required_booleans:
        if requirement.actual is False:
            flaws.append(
                f"required structural boolean=false: {requirement.check_id}"
            )
    for violation in unresolved_violations:
        count = violation.count
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            continue
        flaws.append(
            f"unresolved violation count>0: {violation.check_id}={count}"
        )
    return list(dict.fromkeys(flaws))


def _duration_days(match: re.Match[str]) -> Decimal | None:
    words = {
        "한": Decimal(1), "두": Decimal(2),
        "세": Decimal(3), "네": Decimal(4),
    }
    raw = match.group("count").casefold()
    count = words.get(raw)
    if count is None:
        try:
            count = Decimal(raw)
        except InvalidOperation:
            return None
    unit = match.group("unit").casefold()
    return count * Decimal(7) if unit.startswith("주") or unit.startswith("week") else count


def _decimal_text(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _date_span_flaws(record: TextRecord) -> list[str]:
    flaws: list[str] = []
    for clause_match in _CLAUSE_RE.finditer(record.text or ""):
        clause = clause_match.group(0)
        dates = list(_ISO_DATE_RE.finditer(clause))
        if len(dates) < 2 or not _DATE_RELATION_RE.search(clause):
            continue
        relations: list[tuple[re.Match[str], re.Match[str], re.Match[str]]] = []
        for start_match, end_match in zip(dates, dates[1:]):
            if not _DATE_CONNECTOR_RE.fullmatch(
                clause[start_match.end():end_match.start()]
            ):
                continue
            for duration in _DURATION_RE.finditer(clause, end_match.end()):
                if duration.start() - end_match.end() > 48:
                    break
                relation_tail = clause[duration.end():duration.end() + 24]
                relation_bridge = clause[end_match.end():duration.start()]
                predicate_before_dates = clause[:start_match.start()]
                if (_DATE_DURATION_PREDICATE_RE.search(relation_tail)
                        or _DATE_DURATION_BRIDGE_PREDICATE_RE.fullmatch(relation_bridge)
                        or (_DATE_DURATION_BY_RE.fullmatch(relation_bridge)
                            and _DATE_RELATION_RE.search(predicate_before_dates))):
                    relations.append((start_match, end_match, duration))
                break
        if len(relations) != 1:
            continue
        start_match, end_match, duration = relations[0]
        if (duration.group("approx")
                or _APPROX_DURATION_SUFFIX_RE.search(
                    clause[duration.end():duration.end() + 24]
                )):
            continue
        try:
            start = date.fromisoformat(start_match.group(1))
            end = date.fromisoformat(end_match.group(1))
        except ValueError:
            continue
        exact_days = abs((end - start).days)
        stated_days = _duration_days(duration)
        allowed_days = {exact_days}
        interval_text = clause[start_match.end():duration.start()]
        if re.search(r"까지|through|until|inclusive", interval_text, re.I):
            allowed_days.add(exact_days + 1)
        if stated_days is not None and stated_days not in allowed_days:
            flaws.append(
                f"{record.record_id} date span mismatch: exact={exact_days} days, "
                f"stated={_decimal_text(stated_days)} days"
            )
    return flaws


def _count_atoms(text: str) -> list[_CountAtom]:
    """Return non-overlapping typed count atoms, preferring the widest morphology."""
    candidates = [
        _CountAtom(
            match.start(), match.end(), match.group("value"),
            str(match.group("unit") or "").casefold(),
        )
        for pattern in (_QUANTITY_RE, _QUANTITY_UNIT_FIRST_RE, _GENERIC_COUNT_RE)
        for match in pattern.finditer(text or "")
    ]
    selected: list[_CountAtom] = []
    for candidate in sorted(candidates, key=lambda item: (item.start, -item.end)):
        if any(candidate.start < item.end and item.start < candidate.end
               for item in selected):
            continue
        selected.append(candidate)
    return sorted(selected)


def _typed_count_relations(text: str) -> list[_CountRelation]:
    """Bind adjacent typed counts only when an explicit part-of connector joins them."""
    atoms = _count_atoms(text)
    relations: list[_CountRelation] = []
    for first, second in zip(atoms, atoms[1:]):
        connector = _COUNT_RELATION_CONNECTOR_RE.fullmatch(
            text[first.end:second.start],
        )
        if not connector:
            continue
        total, part = ((first, second) if connector.group("total_first")
                       else (second, first))
        relations.append(_CountRelation(
            first.start, second.end, total.value, part.value,
        ))
    return relations


def _percentage_flaws(record: TextRecord) -> list[str]:
    flaws: list[str] = []
    seen: set[tuple[int, int]] = set()
    text = record.text or ""
    relations = _typed_count_relations(text)
    relations.extend(
        _CountRelation(
            match.start(), match.end(), match.group("total"), match.group("part"),
        )
        for match in _FRACTION_RE.finditer(text)
    )
    for relation in relations:
        tail = text[relation.end:relation.end + 48]
        percent = _PERCENT_RE.search(tail)
        if not percent:
            continue
        bridge = tail[:percent.start()]
        if _CLAIM_BOUNDARY_RE.search(bridge):
            continue
        if (not _PERCENT_RELATION_BRIDGE_RE.search(bridge)
                and not re.fullmatch(r"[\s():=~-]*", bridge)):
            continue
        span = (relation.start, relation.end + percent.end())
        if span in seen:
            continue
        seen.add(span)
        if re.search(r"잔여|남은|미완료|remaining|incomplete", bridge, re.I):
            continue
        try:
            part = Decimal(relation.part)
            total = Decimal(relation.total)
            stated = Decimal(percent.group("percent"))
        except (InvalidOperation, ValueError):
            continue
        if total == 0:
            continue
        exact = part * Decimal(100) / total
        rounded_display = stated == stated.to_integral_value()
        tolerance = (Decimal(1) if percent.group("approx") or rounded_display
                     else Decimal("0.05"))
        if abs(exact - stated) > tolerance:
            flaws.append(
                f"{record.record_id} quantity percentage mismatch: "
                f"exact={_decimal_text(exact)}%, stated={_decimal_text(stated)}%"
            )
    return flaws


def date_quantity_consistency_flaws(records: Sequence[TextRecord]) -> list[str]:
    """Validate exact arithmetic only within one bounded text record and clause."""
    flaws: list[str] = []
    for record in records:
        flaws.extend(_date_span_flaws(record))
        flaws.extend(_percentage_flaws(record))
    return list(dict.fromkeys(flaws))


def _normalized_number(value: str) -> str:
    try:
        return _decimal_text(Decimal(value))
    except InvalidOperation:
        return str(value)


def _normalized_unit(value: str) -> str:
    unit = str(value or "").casefold()
    aliases = {
        "표본": "sample", "sample": "sample", "samples": "sample",
        "후보": "candidate", "candidate": "candidate", "candidates": "candidate",
        "테이블": "table", "table": "table", "tables": "table",
        "티켓": "ticket", "ticket": "ticket", "tickets": "ticket",
        "작업": "task", "task": "task", "tasks": "task",
        "문서": "document", "document": "document", "documents": "document",
        "사람": "person", "인원": "person", "명": "person",
        "people": "person", "person": "person", "persons": "person",
        "건": "item", "item": "item", "items": "item",
    }
    return aliases.get(unit, unit)


def _quantity_literal_values(text: str) -> set[_LiteralValue]:
    """Parse Korean counter/particle and unit-first forms into one semantic quantity."""
    return {
        _LiteralValue(
            "quantity", _normalized_number(match.group("value")),
            _normalized_unit(match.group("unit")),
        )
        for pattern in (_QUANTITY_RE, _QUANTITY_UNIT_FIRST_RE)
        for match in pattern.finditer(text or "")
    }


def _literal_values(text: str) -> set[_LiteralValue]:
    values = {
        _LiteralValue("date", match.group(1))
        for match in _ISO_DATE_RE.finditer(text or "")
    }
    values.update(_quantity_literal_values(text))
    return values


def source_claim_consistency_flaws(
    authorities: Sequence[SourceAuthority],
    claims: Sequence[DirectSourceClaim],
) -> list[str]:
    """Reject literal direct-source values absent from that exact typed authority.

    This is intentionally narrower than semantic entailment: it checks ISO dates and measured
    quantities only.  A missing authority is left to source-index completeness checks rather
    than being treated as evidence that the claim is false.
    """
    texts: dict[str, list[str]] = {}
    for authority in authorities:
        source_id = str(authority.source_id or "").strip()
        if source_id:
            texts.setdefault(source_id, []).append(str(authority.text or ""))
    values_by_source = {
        source_id: _literal_values("\n".join(source_texts))
        for source_id, source_texts in texts.items()
    }
    flaws: list[str] = []
    for claim in claims:
        authority_values = values_by_source.get(claim.source_id)
        if authority_values is None:
            continue
        unsupported = sorted(_literal_values(claim.text) - authority_values)
        for value in unsupported:
            rendered = value.value + (f" {value.unit}" if value.unit else "")
            flaws.append(
                f"direct source claim {claim.claim_id} value absent from "
                f"{claim.source_id}: {value.kind}={rendered}"
            )
    return list(dict.fromkeys(flaws))


def _canonical_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return ""
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path,
                       parsed.query, ""))


def _flatten_text(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        rows: list[str] = []
        for key, item in value.items():
            if str(key).startswith("_"):
                continue
            rows.extend(_flatten_text(item))
        return rows
    if isinstance(value, (list, tuple)):
        rows: list[str] = []
        for item in value:
            rows.extend(_flatten_text(item))
        return rows
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        return [text] if text else []
    return []


def _authority_rows(evaluation_evidence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    roots: list[Mapping[str, Any]] = []
    for row in evaluation_evidence.get("queryResults") or []:
        if isinstance(row, Mapping) and isinstance(row.get("result"), Mapping):
            roots.append(row["result"])
    artifacts = evaluation_evidence.get("queryArtifacts")
    if isinstance(artifacts, Mapping):
        roots.extend(value for value in artifacts.values() if isinstance(value, Mapping))
    roots.append(evaluation_evidence)
    return roots


def evaluation_source_authorities(
    evaluation_evidence: Mapping[str, Any] | None,
) -> list[SourceAuthority]:
    """Project common query-result shapes into stable ticket/document source identities."""
    evidence = dict(evaluation_evidence) if isinstance(evaluation_evidence, Mapping) else {}
    texts: dict[str, list[str]] = {}

    def add(source_id: str, value: Any) -> None:
        source_id = str(source_id or "").strip()
        if not source_id:
            return
        body = "\n".join(_flatten_text(value))
        if body:
            texts.setdefault(source_id, []).append(body)

    for root in _authority_rows(evidence):
        for field in ("ticketDetails", "projectedTicketDetails", "tickets"):
            rows = root.get(field)
            for row in rows if isinstance(rows, (list, tuple)) else ():
                if isinstance(row, Mapping) and str(row.get("key") or "").strip():
                    add(f"ticket:{str(row['key']).strip().upper()}", row)
        for field in ("comments",):
            rows = root.get(field)
            for row in rows if isinstance(rows, (list, tuple)) else ():
                if isinstance(row, Mapping) and str(row.get("ticketKey") or "").strip():
                    add(f"ticket:{str(row['ticketKey']).strip().upper()}", row)
        for field in ("documents", "documentBodies", "projectedDocumentBodies", "results"):
            rows = root.get(field)
            for row in rows if isinstance(rows, (list, tuple)) else ():
                if not isinstance(row, Mapping):
                    continue
                url = _canonical_url(str(row.get("url") or ""))
                if url:
                    add(f"url:{url}", row)
                document_id = str(row.get("id") or "").strip()
                if document_id:
                    add(f"document:{document_id}", row)
    return [
        SourceAuthority(source_id, "\n".join(source_texts))
        for source_id, source_texts in sorted(texts.items())
    ]


def indexed_source_claims(reply: str) -> list[DirectSourceClaim]:
    """Bind source-index observations and unambiguous body citations to typed roots."""
    in_sources = False
    root_candidates: dict[str, set[str]] = {}
    current_number = ""
    plain_ordinals: dict[str, int] = {}
    pending: list[tuple[str, str, str]] = []
    body_lines: list[tuple[int, str]] = []
    for line_number, line in enumerate(str(reply or "").splitlines(), 1):
        if _SOURCE_HEADING_RE.fullmatch(line):
            in_sources = True
            current_number = ""
            continue
        if in_sources and (
            _ATX_HEADING_RE.match(line) or _STANDALONE_BOLD_HEADING_RE.fullmatch(line)
        ):
            in_sources = False
            current_number = ""
            continue
        if not in_sources:
            body_lines.append((line_number, line))
            continue
        root = _SOURCE_ROOT_RE.fullmatch(line)
        if root:
            current_number = root.group(1)
            material = root.group(2)
            ticket = _TICKET_TOKEN_RE.search(material)
            markdown = _MARKDOWN_URL_RE.search(material)
            source_id = ""
            if ticket:
                source_id = f"ticket:{ticket.group(1).upper()}"
            elif markdown:
                url = _canonical_url(markdown.group(1))
                if url:
                    source_id = f"url:{url}"
            root_candidates.setdefault(current_number, set()).add(source_id)
            continue
        child = _SOURCE_CHILD_RE.fullmatch(line)
        if child:
            number = child.group(1)
            pending.append((
                f"{number}-{child.group(2).casefold()}", number, child.group(3),
            ))
            continue
        bullet = _SOURCE_BULLET_RE.fullmatch(line)
        if bullet and current_number:
            plain_ordinals[current_number] = plain_ordinals.get(current_number, 0) + 1
            pending.append((
                f"{current_number}:{plain_ordinals[current_number]}",
                current_number, bullet.group(1),
            ))

    roots = {
        number: next(iter(candidates))
        for number, candidates in root_candidates.items()
        if len(candidates) == 1 and "" not in candidates
    }
    claims = [
        DirectSourceClaim(claim_id, roots[number], text)
        for claim_id, number, text in pending
        if number in roots
    ]
    for line_number, line in body_lines:
        for clause_number, clause_match in enumerate(_CLAUSE_RE.finditer(line), 1):
            clause = clause_match.group(0)
            citations = list(_DIRECT_NUMERIC_CITATION_RE.finditer(clause))
            if len(citations) != 1:
                continue
            number = citations[0].group(1)
            source_id = roots.get(number, "")
            if not source_id or not _literal_values(clause):
                continue
            claims.append(DirectSourceClaim(
                f"body:{line_number}:{clause_number}:{number}", source_id, clause,
            ))
    return claims


def _claim_text_records(reply: str, evidence: Mapping[str, Any]) -> list[TextRecord]:
    records = [TextRecord("reply", str(reply or ""))]
    for index, row in enumerate(evidence.get("evidence") or []):
        if not isinstance(row, Mapping):
            continue
        for field in ("title", "why"):
            value = str(row.get(field) or "").strip()
            if value:
                records.append(TextRecord(f"evidence[{index}].{field}", value))
        for observation_index, observation in enumerate(row.get("observations") or []):
            if not isinstance(observation, Mapping):
                continue
            value = str(observation.get("text") or "").strip()
            if value:
                records.append(TextRecord(
                    f"evidence[{index}].observations[{observation_index}]", value,
                ))
    return records


def evaluation_claim_consistency_flaws(
    reply: str,
    evaluation_evidence: Mapping[str, Any] | None,
) -> list[str]:
    """Apply bounded arithmetic and typed direct-source checks to one evaluated reply."""
    evidence = dict(evaluation_evidence) if isinstance(evaluation_evidence, Mapping) else {}
    flaws = date_quantity_consistency_flaws(_claim_text_records(reply, evidence))
    flaws.extend(source_claim_consistency_flaws(
        evaluation_source_authorities(evidence), indexed_source_claims(reply),
    ))
    return list(dict.fromkeys(flaws))


EVALUATION_CLAIM_CONTRACT_DEPENDENCIES = (
    _ISO_DATE_RE, _CLAUSE_RE, _DATE_CONNECTOR_RE, _DURATION_RE, _DATE_RELATION_RE,
    _DATE_DURATION_PREDICATE_RE,
    _FRACTION_RE, _PERCENT_RE,
    _CLAIM_BOUNDARY_RE, _PERCENT_RELATION_BRIDGE_RE, _QUANTITY_UNIT_PATTERN,
    _QUANTITY_BOUNDARY_PATTERN,
    _QUANTITY_RE, _QUANTITY_UNIT_FIRST_RE, _GENERIC_COUNT_RE,
    _COUNT_RELATION_CONNECTOR_RE, _TICKET_TOKEN_RE,
    _MARKDOWN_URL_RE, _SOURCE_HEADING_RE, _ATX_HEADING_RE,
    _STANDALONE_BOLD_HEADING_RE,
    _SOURCE_ROOT_RE, _SOURCE_CHILD_RE,
    _SOURCE_BULLET_RE, _DIRECT_NUMERIC_CITATION_RE,
    RequiredBoolean, UnresolvedViolation, MeasurementAvailability, TextRecord,
    SourceAuthority, DirectSourceClaim, _LiteralValue, _CountAtom, _CountRelation,
    measurement_gate_flaws,
    _duration_days, _decimal_text, _date_span_flaws, _count_atoms,
    _typed_count_relations, _percentage_flaws,
    date_quantity_consistency_flaws, _normalized_number, _normalized_unit,
    _quantity_literal_values, _literal_values, source_claim_consistency_flaws,
    _canonical_url, _flatten_text,
    _authority_rows, evaluation_source_authorities, indexed_source_claims,
    _claim_text_records, evaluation_claim_consistency_flaws,
)


__all__ = [
    "DirectSourceClaim", "EVALUATION_CLAIM_CONTRACT_DEPENDENCIES", "MeasurementAvailability",
    "RequiredBoolean", "SourceAuthority", "TextRecord", "UnresolvedViolation",
    "date_quantity_consistency_flaws", "evaluation_claim_consistency_flaws",
    "evaluation_source_authorities", "indexed_source_claims",
    "measurement_gate_flaws", "source_claim_consistency_flaws",
]
