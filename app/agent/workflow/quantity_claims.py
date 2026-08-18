"""Pure typed quantity-relation parsing and source-bound claim reconciliation.

The parser recognizes only explicit subset/selection grammar.  It does not infer a
relationship from neighboring numbers, product names, or document position.  Callers own
source authority and pass canonical source ids/spans; this module keeps the resulting value
objects immutable and repairs only a contradictory value+unit pair for that same source.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import re
from typing import Iterable


@dataclass(frozen=True, slots=True)
class QuantityTerm:
    """One exact quantity literal from a canonical, code-owned source span."""

    value: str
    unit: str
    literal: str


@dataclass(frozen=True, slots=True)
class QuantityRelation:
    """Immutable subset/selection relation; prose summaries carry no authority."""

    relation_id: str
    source_id: str
    subject_id: str
    relation_kind: str
    container: QuantityTerm
    selection: QuantityTerm
    source_span: str
    source_text: str
    observed_at: str
    provenance: str

    def as_dict(self) -> dict:
        row = asdict(self)
        # Full source text is a deterministic runtime boundary, not another prompt payload.
        row.pop("source_text", None)
        return row


@dataclass(frozen=True, slots=True)
class _MatchedQuantityTerm:
    """One typed term plus the exact source span needed for deterministic replacement."""

    term: QuantityTerm
    start: int
    end: int
    suffix: str
    original: str


_NUMBER = r"(?:0|[1-9]\d*)(?:\.\d+)?"
_UNIT = r"(?:[A-Za-z][A-Za-z0-9_-]{0,31}|[가-힣]{1,24}?)"
_COUNTER = r"(?:개|건|명)"
_UNIT_PARTICLE = r"(?:은|는|이|가|을|를|의)?"
_SUFFIX = r"(?:입니다|이다|이었다|였다|으로|로|을|를|이|가|은|는)?"
_CONTAINER_CONNECTOR = r"(?:중(?:에서|의)?|[-=]+>|to|into|yielding)"
_SELECTION_CONNECTOR = r"(?:out\s+of|from)"
_TERM_BOUNDARY = r"(?=[\s.,;:!?)]|$)"


def _value_first_term(role: str) -> str:
    return (
        rf"(?P<{role}_term>"
        rf"(?P<{role}_value>{_NUMBER})\s*"
        rf"(?:(?:{_COUNTER})\s*(?:의\s*)?)?"
        rf"(?P<{role}_unit>{_UNIT}))"
    )


def _unit_first_term(role: str) -> str:
    return (
        rf"(?P<{role}_term>"
        rf"(?P<{role}_unit>{_UNIT})\s*{_UNIT_PARTICLE}\s*"
        rf"(?P<{role}_value>{_NUMBER})\s*{_COUNTER})"
    )


_TERM_BUILDERS = (_value_first_term, _unit_first_term)
_CONTAINER_FIRST_PATTERNS = tuple(
    re.compile(
        container("container") + rf"\s*{_CONTAINER_CONNECTOR}\s*"
        + selection("selection")
        + rf"(?P<selection_suffix>{_SUFFIX}){_TERM_BOUNDARY}",
        re.I,
    )
    for container in _TERM_BUILDERS
    for selection in _TERM_BUILDERS
)
_SELECTION_FIRST_PATTERNS = tuple(
    re.compile(
        selection("selection") + rf"\s*{_SELECTION_CONNECTOR}\s*"
        + container("container")
        + rf"(?P<container_suffix>{_SUFFIX}){_TERM_BOUNDARY}",
        re.I,
    )
    for selection in _TERM_BUILDERS
    for container in _TERM_BUILDERS
)
_CLAIM_TERM_PATTERNS = tuple(
    re.compile(
        builder("quantity")
        + rf"(?P<quantity_suffix>{_SUFFIX}){_TERM_BOUNDARY}",
        re.I,
    )
    for builder in _TERM_BUILDERS
)


def _unit(value: str) -> str:
    return " ".join(str(value or "").split()).strip().casefold()


def _from_match(match: re.Match, *, source_id: str, subject_id: str,
                 observed_at: str, provenance: str) -> QuantityRelation:
    container_literal = match.group("container_term")
    selection_literal = match.group("selection_term")
    span_start = min(match.start("container_term"), match.start("selection_term"))
    span_end = max(match.end("container_term"), match.end("selection_term"))
    source_span = match.string[span_start:span_end]
    material = "\x1f".join((source_id, subject_id, "subset_selection", source_span))
    return QuantityRelation(
        relation_id="quantity:" + sha256(material.encode("utf-8")).hexdigest()[:20],
        source_id=source_id,
        subject_id=subject_id,
        relation_kind="subset_selection",
        container=QuantityTerm(
            value=match.group("container_value"),
            unit=_unit(match.group("container_unit")),
            literal=container_literal,
        ),
        selection=QuantityTerm(
            value=match.group("selection_value"),
            unit=_unit(match.group("selection_unit")),
            literal=selection_literal,
        ),
        source_span=source_span,
        source_text=match.string,
        observed_at=str(observed_at or "").strip(),
        provenance=str(provenance or source_id).strip(),
    )


def _quantity_term_matches(
        text: str, *, preferred_units: Iterable[str] = ()) \
        -> tuple[_MatchedQuantityTerm, ...]:
    value = str(text or "")
    preferred = {_unit(unit) for unit in preferred_units if _unit(unit)}
    candidates = [match for pattern in _CLAIM_TERM_PATTERNS
                  for match in pattern.finditer(value)]
    # Without this grammatical guard, the value-first alternative reads the Korean
    # copula in ``표본은 5개입니다`` as the semantic unit ``입니다``.
    candidates = [
        match for match in candidates
        if _unit(match.group("quantity_unit"))
        not in {"은", "는", "이", "가", "을", "를", "의", "로", "으로",
                "이다", "였다", "이었다", "입니다"}
    ]
    # ``표본은 20개`` also contains a shorter value-first ``20개`` parse. The widest
    # parse.  When a caller owns relation units, prefer that typed vocabulary over a
    # neighboring topic noun (``대상은 20개 표본``); then discard only overlaps.
    candidates.sort(key=lambda match: (
        0 if _unit(match.group("quantity_unit")) in preferred else 1,
        match.start(), -(match.end() - match.start()),
    ))
    selected: list[re.Match] = []
    for match in candidates:
        if any(match.start() < row.end() and row.start() < match.end() for row in selected):
            continue
        selected.append(match)
    selected.sort(key=lambda match: match.start())
    return tuple(
        _MatchedQuantityTerm(
            term=QuantityTerm(
                value=match.group("quantity_value"),
                unit=_unit(match.group("quantity_unit")),
                literal=match.group("quantity_term"),
            ),
            start=match.start(), end=match.end(),
            suffix=match.group("quantity_suffix") or "",
            original=match.group(0),
        )
        for match in selected
    )


def parse_quantity_terms(text: str) -> tuple[QuantityTerm, ...]:
    """Project value-first and unit-first literals into one semantic term shape."""
    return tuple(match.term for match in _quantity_term_matches(text))


def parse_quantity_relations(text: str, *, source_id: str, subject_id: str,
                             observed_at: str = "", provenance: str = "") \
        -> tuple[QuantityRelation, ...]:
    """Parse only explicitly connected container/selection quantities."""
    value = str(text or "")
    matches = [
        *(match for pattern in _CONTAINER_FIRST_PATTERNS for match in pattern.finditer(value)),
        *(match for pattern in _SELECTION_FIRST_PATTERNS for match in pattern.finditer(value)),
    ]
    # A unit-first relation also contains a shorter counter-only value-first substring
    # (``후보 20개 중 ...`` contains ``20개 중 ...``). Prefer the earliest/widest
    # explicit relation and suppress only overlapping alternative parses.
    matches.sort(key=lambda match: (match.start(), -(match.end() - match.start())))
    selected: list[re.Match] = []
    for match in matches:
        if any(match.start() < row.end() and row.start() < match.end() for row in selected):
            continue
        selected.append(match)
    return tuple(
        _from_match(
            match, source_id=source_id, subject_id=subject_id,
            observed_at=observed_at, provenance=provenance,
        )
        for match in selected
    )


def reconcile_quantity_observation(
        value: str, relations: Iterable[QuantityRelation], *,
        relation_id: str = "", subject_id: str = "",
        authoritative_texts: Iterable[str] = ()) -> str:
    """Reconcile only an explicitly bound relation; otherwise fail closed.

    A source citation binds a document, not one relation inside that document.  Exact
    quantities present elsewhere in the authoritative source remain valid, while an
    unsupported value/unit pair is never rewritten to an unrelated relation.
    """
    text = str(value or "")
    rows = tuple(relation for relation in relations
                 if isinstance(relation, QuantityRelation))
    if not text or not rows:
        return text
    by_unit: dict[str, list[tuple[str, QuantityRelation]]] = {}
    for relation in rows:
        by_unit.setdefault(relation.container.unit, []).append(
            (relation.container.value, relation))
        by_unit.setdefault(relation.selection.unit, []).append(
            (relation.selection.value, relation))
    supported_terms = {
        (allowed, unit)
        for unit, candidates in by_unit.items()
        for allowed, _relation in candidates
    }
    canonical_texts = [relation.source_text for relation in rows]
    canonical_texts.extend(str(text or "") for text in authoritative_texts or ())
    for authoritative in canonical_texts:
        supported_terms.update(
            (match.term.value, match.term.unit)
            for match in _quantity_term_matches(
                authoritative, preferred_units=by_unit,
            )
        )

    def replace(match: _MatchedQuantityTerm) -> str:
        unit = match.term.unit
        term = (match.term.value, unit)
        candidates = by_unit.get(unit, [])
        if not candidates:
            return match.original
        bound = {
            relation.relation_id: relation
            for _allowed, relation in candidates
            if ((relation_id and relation.relation_id == relation_id)
                or (not relation_id and subject_id
                    and relation.subject_id == subject_id))
        }
        if bound:
            bound_terms = {
                (allowed, unit)
                for allowed, relation in candidates
                if relation.relation_id in bound
            }
            if term in bound_terms:
                return match.original
        elif not relation_id and not subject_id and term in supported_terms:
            return match.original
        if len(bound) == 1:
            relation = next(iter(bound.values()))
            if relation.source_span not in text:
                return relation.source_span + match.suffix
        return "수량 근거 확인 필요"

    chunks: list[str] = []
    cursor = 0
    for match in _quantity_term_matches(text, preferred_units=by_unit):
        chunks.extend((text[cursor:match.start], replace(match)))
        cursor = match.end
    chunks.append(text[cursor:])
    return "".join(chunks)


__all__ = [
    "QuantityRelation", "QuantityTerm", "parse_quantity_relations", "parse_quantity_terms",
    "reconcile_quantity_observation",
]
