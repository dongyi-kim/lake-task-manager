"""Typed fact-relation contracts for deterministic evaluator checks.

The evaluator should not infer semantics from one ever-growing case regex.  This module
turns only payload description DOM text into bounded fact units, then evaluates declarative
actor/relation/state contracts.  It deliberately does not inspect reply or retrieval prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Sequence


@dataclass(frozen=True)
class FactTerm:
    """A named, versionable lexical boundary used by a relation contract."""

    name: str
    pattern: str

    def finditer(self, text: str):
        return re.finditer(self.pattern, text, re.I)

    def present(self, text: str) -> bool:
        return bool(re.search(self.pattern, text, re.I))


@dataclass(frozen=True)
class FactUnit:
    description: int
    leaf: int
    ordinal: int
    text: str


@dataclass(frozen=True)
class RelationRef:
    """Actors and material anchors that identify one semantic relation."""

    actors: tuple[FactTerm, ...]
    anchors: tuple[FactTerm, ...] = ()
    qualifiers: tuple[FactTerm, ...] = ()
    scope_boundaries: tuple[FactTerm, ...] = ()


@dataclass(frozen=True)
class FactRelationContract:
    """Declarative expected relation plus deterministic contradiction policy."""

    name: str
    mode: str
    relation: RelationRef
    expected_states: tuple[str, ...]
    contradiction_states: tuple[str, ...]
    missing_message: str
    reversal_message: str
    action: RelationRef | None = None


_CLAUSE_BREAK_RE = re.compile(r"(?:[.!?]+|[;；]+|\s+/\s+|\r?\n+)\s*")
_COMPLETION_RE = re.compile(
    r"(?<!미)(?:완료(?:하|해|했|했습|하여|함|되|돼|됐|되어|되었|됨|된|입니다|이다)?|"
    r"(?:결과|산출물).{0,24}확보(?:하|해|했|했습|하여|함|되|돼|됐|되어|되었|됨)?|"
    r"\b(?:done|completed)\b)",
    re.I,
)
_COMPLETION_DISQUALIFIER_RE = re.compile(
    r"^(?:\s*(?:전|까지|후|예정|계획|목표|필요|아님|아니|되지|하지|못|않))|"
    r"(?:예정|계획|목표).{0,20}$",
    re.I,
)
_INCOMPLETE_RE = re.compile(
    r"미완료|미수행|(?:완료|수행).{0,16}(?:하지|되지|못하|않)|"
    r"(?:완료|수행)\s*(?:전|까지)|"
    r"\b(?:incomplete|not\s+(?:done|completed|performed))\b",
    re.I,
)
_IN_PROGRESS_RE = re.compile(
    r"(?:진행|확인|검증|검토)\s*(?:을|를|이|가)?\s*"
    r"(?:하고|하는|되고|되는|되어|중)|"
    r"\b(?:in\s+progress|under\s+(?:validation|review))\b",
    re.I,
)
_UNCONFIRMED_RE = re.compile(
    r"미확정|확정\s*(?:되지|하지|된\s*것이\s*아니|아니).{0,12}(?:않|못|아님)?|"
    r"아직.{0,36}확정.{0,16}(?:않|못|아니)|"
    r"지원\s*여부.{0,36}(?:미확정|확인\s*중|확정.{0,12}(?:않|못|아니))|"
    r"\b(?:unconfirmed|not\s+confirmed)\b",
    re.I,
)
_CONFIRMED_RE = re.compile(
    r"(?:검증\s*완료|지원(?:\s*여부)?(?:이|은|을|는)?\s*확정|"
    r"소비(?:가|는|를)?\s*확정|\bconfirmed\b)",
    re.I,
)
_HOLD_RE = re.compile(
    r"보류|금지|승인\s*(?:하지|되지|못하|않)|"
    r"(?:반영|배포)\s*(?:하지|되지|못하|않)|\bhold\b|\bblocked?\b",
    re.I,
)
_POSITIVE_ACTION_RE = re.compile(
    r"(?:승인|진행|허용|실행)\s*"
    r"(?:해|하여|함|했(?:다|음)?|한다|됨|됐|되었|하기로|하도록)|"
    r"\b(?:approved?|proceed(?:ed|ing)?|allowed?|executed?)\b",
    re.I,
)
_NEGATED_ACTION_RE = re.compile(
    r"(?:승인|진행|허용|실행).{0,12}(?:하지|되지|못하|않|금지|보류)|"
    r"\bnot\s+(?:approved?|proceed(?:ed|ing)?|allowed?|executed?)\b",
    re.I,
)
_BOUNDARY_RE = re.compile(r"(?:완료|검증|확인|증거|근거|evidence)?.{0,14}(?:전|까지|before|until)", re.I)
_CAUSAL_RE = re.compile(
    r"^\s*(?:따라서|그러므로|이에\s*따라|그에\s*따라|그때까지|"
    r"therefore|accordingly)",
    re.I,
)


class _DescriptionLeafParser(HTMLParser):
    """Keep DOM text nodes separate while retaining their document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.leaves: list[str] = []

    def handle_data(self, data: str) -> None:
        text = re.sub(r"\s+", " ", unescape(str(data or ""))).strip()
        if text:
            self.leaves.append(text)


def description_fact_units(descriptions: Sequence[str]) -> list[list[FactUnit]]:
    """Parse descriptions into ordered, source-bounded DOM/sentence fact units."""
    result: list[list[FactUnit]] = []
    for description_index, description in enumerate(descriptions):
        parser = _DescriptionLeafParser()
        try:
            parser.feed(str(description or ""))
            parser.close()
            leaves = parser.leaves
        except Exception:
            leaves = [re.sub(r"<[^>]+>", " ", str(description or ""))]
        if not leaves and str(description or "").strip():
            leaves = [re.sub(r"<[^>]+>", " ", str(description or ""))]
        units: list[FactUnit] = []
        ordinal = 0
        for leaf_index, leaf in enumerate(leaves):
            for clause in _CLAUSE_BREAK_RE.split(leaf):
                text = re.sub(r"\s+", " ", clause).strip().casefold()
                if not text:
                    continue
                units.append(FactUnit(description_index, leaf_index, ordinal, text))
                ordinal += 1
        if units:
            result.append(units)
    return result


def _all_present(text: str, terms: Sequence[FactTerm]) -> bool:
    return all(term.present(text) for term in terms)


def _any_present(text: str, terms: Sequence[FactTerm]) -> bool:
    return any(term.present(text) for term in terms)


def _direct_completion(text: str) -> bool:
    for match in _COMPLETION_RE.finditer(text):
        before = text[max(0, match.start() - 20):match.start()]
        after = text[match.end():match.end() + 24]
        if before.rstrip().endswith("미"):
            continue
        if _COMPLETION_DISQUALIFIER_RE.search(after) \
                or re.search(r"(?:예정|계획|목표).{0,20}$", before, re.I):
            continue
        return True
    return False


def _direct_confirmation(text: str) -> bool:
    for match in _CONFIRMED_RE.finditer(text):
        before = text[max(0, match.start() - 18):match.start()]
        after = text[match.end():match.end() + 24]
        context = before + match.group() + after
        if _UNCONFIRMED_RE.search(context) or _BOUNDARY_RE.search(after) \
                or re.search(r"(?:예정|계획|목표)", after, re.I):
            continue
        return True
    return False


def semantic_states(text: str) -> frozenset[str]:
    """Return conservative states; positive completion/confirmation must be direct."""
    states: set[str] = set()
    if _direct_completion(text):
        states.add("completed")
    if _INCOMPLETE_RE.search(text):
        states.add("incomplete")
    if _IN_PROGRESS_RE.search(text):
        states.add("in_progress")
    if _UNCONFIRMED_RE.search(text):
        states.add("unconfirmed")
    if _direct_confirmation(text):
        states.add("confirmed")
    if _HOLD_RE.search(text):
        states.add("held")
    if _POSITIVE_ACTION_RE.search(text) and not _NEGATED_ACTION_RE.search(text):
        states.add("positive_action")
    if _BOUNDARY_RE.search(text):
        states.add("before_boundary")
    return frozenset(states)


def _unit_has_any_actor(unit: FactUnit, relation: RelationRef) -> bool:
    return _any_present(unit.text, (*relation.actors, *relation.scope_boundaries))


def _relation_windows(units: Sequence[FactUnit], relation: RelationRef) -> list[list[FactUnit]]:
    """Compose only bounded adjacent units that retain the same relation authority."""
    windows: list[list[FactUnit]] = []
    for index, unit in enumerate(units):
        if not _all_present(unit.text, relation.actors) or not _all_present(
                unit.text, relation.anchors):
            continue
        selected = [unit]
        for neighbor_index in (index - 1, index + 1):
            if neighbor_index < 0 or neighbor_index >= len(units):
                continue
            neighbor = units[neighbor_index]
            same_leaf = neighbor.leaf == unit.leaf
            has_scope_boundary = _any_present(neighbor.text, relation.scope_boundaries)
            repeats_relation = (
                _all_present(neighbor.text, relation.actors)
                and _all_present(neighbor.text, relation.anchors)
            )
            actorless_material = (
                not _unit_has_any_actor(neighbor, relation)
                and (_any_present(neighbor.text, relation.qualifiers)
                     or bool(semantic_states(neighbor.text)))
            )
            if (same_leaf and not has_scope_boundary) or repeats_relation or actorless_material:
                selected.append(neighbor)
        windows.append(sorted(selected, key=lambda row: row.ordinal))
    return windows


def _actor_scopes(text: str, actor: FactTerm, boundaries: Sequence[FactTerm]) -> list[str]:
    typed: list[tuple[int, int, str]] = []
    typed.extend((m.start(), m.end(), "target") for m in actor.finditer(text))
    for boundary in boundaries:
        typed.extend((m.start(), m.end(), "boundary") for m in boundary.finditer(text))
    typed.sort()
    result: list[str] = []
    for position, (start, _end, kind) in enumerate(typed):
        if kind != "target":
            continue
        left = typed[position - 1][1] if position and typed[position - 1][2] == "boundary" else 0
        right = next(
            (other_start for other_start, _other_end, other_kind in typed[position + 1:]
             if other_kind == "boundary"),
            len(text),
        )
        result.append(text[left:right])
    return result


def _single_relation_status(groups: Sequence[Sequence[FactUnit]],
                            contract: FactRelationContract) -> str:
    relation = contract.relation
    for units in groups:
        for window in _relation_windows(units, relation):
            text = " ".join(unit.text for unit in window)
            if not _all_present(text, relation.qualifiers):
                continue
            scopes = _actor_scopes(text, relation.actors[0], relation.scope_boundaries) or [text]
            scoped_states = [semantic_states(scope) for scope in scopes]
            if any(set(contract.contradiction_states) & set(states) for states in scoped_states):
                return "reversed"
            if any(set(contract.expected_states) <= set(states) for states in scoped_states):
                return "satisfied"
    return "missing"


def _actors_coordinated(text: str, actors: Sequence[FactTerm]) -> bool:
    if "함께" in text or re.search(r"\b(?:together|jointly)\b", text, re.I):
        return True
    positions = []
    for actor in actors:
        first = next(iter(actor.finditer(text)), None)
        if first is None:
            return False
        positions.append((first.start(), first.end()))
    positions.sort()
    return all(re.fullmatch(r"\s*(?:와|과|및|/|,|and)\s*", text[left[1]:right[0]], re.I)
               for left, right in zip(positions, positions[1:]))


def _shared_relation_status(groups: Sequence[Sequence[FactUnit]],
                            contract: FactRelationContract) -> str:
    relation = contract.relation
    for units in groups:
        # A direct positive reversal belongs to its explicitly named actor even when
        # another actor's uncertainty is in the next sentence.
        if "confirmed" in contract.contradiction_states and any(
            actor.present(unit.text) and "confirmed" in semantic_states(unit.text)
            for unit in units for actor in relation.actors
        ):
            return "reversed"

        windows = _relation_windows(units, relation)
        # Shared predicates may put one actor in each adjacent DOM/sentence unit.  Form a
        # bounded collective window, then require either coordination or full state for
        # every actor below; merely placing different states by different actors will fail.
        for index, unit in enumerate(units):
            if not _any_present(unit.text, relation.actors):
                continue
            collective = list(units[max(0, index - 1):min(len(units), index + 2)])
            text = " ".join(row.text for row in collective)
            ordinals = tuple(row.ordinal for row in collective)
            if _all_present(text, relation.actors) and not any(
                    ordinals == tuple(item.ordinal for item in prior)
                    for prior in windows):
                windows.append(collective)

        for window in windows:
            text = " ".join(unit.text for unit in window)
            if not _all_present(text, relation.qualifiers):
                continue
            aggregate = semantic_states(text)
            boundaries = relation.scope_boundaries
            actor_states = []
            for actor in relation.actors:
                other_actors = tuple(other for other in relation.actors if other is not actor)
                scopes = _actor_scopes(text, actor, (*other_actors, *boundaries))
                actor_states.append(set().union(*(semantic_states(scope) for scope in scopes)))
            coordinated = _actors_coordinated(text, relation.actors)
            if "confirmed" in contract.contradiction_states and (
                (coordinated and "confirmed" in aggregate)
                or any("confirmed" in states for states in actor_states)
            ):
                return "reversed"
            expected = set(contract.expected_states)
            if coordinated and expected <= set(aggregate):
                return "satisfied"
            if actor_states and all(expected <= states for states in actor_states):
                return "satisfied"
    return "missing"


def _gate_relation_status(groups: Sequence[Sequence[FactUnit]],
                          contract: FactRelationContract) -> str:
    relation = contract.relation
    action = contract.action
    if action is None:
        return "missing"
    for units in groups:
        for index, unit in enumerate(units):
            if not _all_present(unit.text, action.anchors):
                continue
            start, end = max(0, index - 1), min(len(units), index + 2)
            window = list(units[start:end])
            # All clauses originating from the same DOM text leaf share their explicit
            # subject; include them even when sentence splitting put them outside ±1.
            window.extend(row for row in units if row.leaf == unit.leaf and row not in window)
            window.sort(key=lambda row: row.ordinal)
            text = " ".join(row.text for row in window)
            has_condition = (
                _all_present(text, relation.actors)
                and _all_present(text, relation.anchors)
                and "before_boundary" in semantic_states(text)
            )
            if not has_condition:
                continue
            states = semantic_states(text)
            if "positive_action" in states:
                return "reversed"
            if "held" in states:
                return "satisfied"
            # A causal second leaf is safe to compose with the explicit antecedent.
            if index and _CAUSAL_RE.search(unit.text):
                antecedent = units[index - 1].text
                if ("before_boundary" in semantic_states(antecedent)
                        and _all_present(antecedent, relation.actors)
                        and _all_present(antecedent, relation.anchors)):
                    if "positive_action" in semantic_states(unit.text):
                        return "reversed"
                    if "held" in semantic_states(unit.text):
                        return "satisfied"
    return "missing"


def fact_relation_flaws(descriptions: Sequence[str],
                        contracts: Sequence[FactRelationContract]) -> list[str]:
    """Evaluate typed relations and return stable missing/reversal diagnostics."""
    groups = description_fact_units(descriptions)
    flaws: list[str] = []
    for contract in contracts:
        if contract.mode == "single":
            status = _single_relation_status(groups, contract)
        elif contract.mode == "shared":
            status = _shared_relation_status(groups, contract)
        elif contract.mode == "gate":
            status = _gate_relation_status(groups, contract)
        else:
            raise ValueError(f"unknown fact relation mode: {contract.mode}")
        if status == "reversed":
            flaws.append(contract.reversal_message)
        elif status == "missing":
            flaws.append(contract.missing_message)
    return flaws


FACT_RELATION_DEPENDENCIES = (
    FactTerm, FactUnit, RelationRef, FactRelationContract,
    _CLAUSE_BREAK_RE, _COMPLETION_RE, _COMPLETION_DISQUALIFIER_RE,
    _INCOMPLETE_RE, _IN_PROGRESS_RE, _UNCONFIRMED_RE, _CONFIRMED_RE,
    _HOLD_RE, _POSITIVE_ACTION_RE, _NEGATED_ACTION_RE, _BOUNDARY_RE, _CAUSAL_RE,
    _DescriptionLeafParser, description_fact_units, _all_present, _any_present,
    _direct_completion, _direct_confirmation, semantic_states, _unit_has_any_actor,
    _relation_windows, _actor_scopes, _single_relation_status, _actors_coordinated,
    _shared_relation_status, _gate_relation_status, fact_relation_flaws,
)


__all__ = [
    "FACT_RELATION_DEPENDENCIES", "FactRelationContract", "FactTerm", "FactUnit",
    "RelationRef", "description_fact_units", "fact_relation_flaws", "semantic_states",
]
