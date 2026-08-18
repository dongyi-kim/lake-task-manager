"""Canonical user-facing evidence index.

The reply used to expose three independently formatted source lists: model-written
``근거/참조`` Markdown, Research Analyst ``evidence``, and ``related_docs``.  Keeping
those paths independent made numbering, duplication, and badge rules drift.  This
module is the one server-side owner of the persisted reply grammar:

    ### 근거
    [1] {{ticket-detail:DL-123}}
    - [1-a] 본문에서 설정값 확인
    - [1-b] 댓글에서 운영상 예외 확인
    [2] [Confluence 문서](https://...)

One real source receives one integer.  Multiple observations from that source receive
lettered child references.  Legacy headings and numbered-list rows are accepted as
input only and are serialized back to the canonical grammar.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Iterable, TypedDict
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from app.agent.workflow.claim_provenance import (
    CITATION_GROUP_RE, CITATION_OCCURRENCE_RE, CITATION_TOKEN_PATTERN,
    bind_evidence_provenance, build_claim_provenance_graph,
    citation_occurrences, citation_tokens, normalize_citation_wrappers,
)


_HEADING_RE = re.compile(
    r"(?m)^(?:#{1,4}\s*(?:근거|참조)|\*\*(?:근거|참조)\*\*)\s*$"
)
_NEXT_HEADING_RE = re.compile(r"(?m)^#{1,4}\s+")
_ROOT_RE = re.compile(
    r"^\s*(?:-\s*)?(?:\[(\d+)(?:-([a-z]))?\]|(\d+)[.)])\s*(.*?)\s*$",
    re.I,
)
_CHILD_RE = re.compile(r"^\s*-\s*(?:\[(\d+)-([a-z])\]\s*)?(.*?)\s*$", re.I)
_CITATION_RE = CITATION_GROUP_RE
_CITATION_RUN_RE = re.compile(
    rf"\[(?:{CITATION_TOKEN_PATTERN})(?:\s*,\s*{CITATION_TOKEN_PATTERN})*\]"
    rf"(?:\s*,?\s*\[(?:{CITATION_TOKEN_PATTERN})(?:\s*,\s*{CITATION_TOKEN_PATTERN})*\])+",
    re.I,
)
_KEY_RE = re.compile(r"(?<![0-9A-Za-z-])([A-Z][A-Z0-9]*-\d+)(?![0-9A-Za-z-])")
_TOKEN_RE = re.compile(r"\{\{ticket-(?:list|inline|detail):([A-Z][A-Z0-9]*-\d+)\}\}")
_MD_LINK_RE = re.compile(r"\[([^\n]+?)\]\((https?://[^\s)]+)\)")
# Typed source tokens may be followed immediately by ``}}`` or a Markdown
# citation marker. Treating those delimiters as URL characters creates a
# second malformed identity (for example ``.../spec/}}는``).
_URL_RE = re.compile(r"https?://[^\s)>\]}]+", re.I)
_CUT_RE = re.compile(r"^(.*?)\s+(?:—|–|--)\s+(.*)$")
_CONFLUENCE_RE = re.compile(r"confluence|/pages/\d+|/display/|/wiki/", re.I)


class AtomicFact(TypedDict):
    """One provenance-bound fact used only as a deterministic synthesis sidecar.

    ``typed=False`` is intentional: natural-language observations remain visible evidence,
    but they cannot participate in supersession until a producer supplies an exact subject
    and predicate (or the value comes from the canonical materialized Jira snapshot).
    """

    fact_id: str
    subject_id: str
    predicate: str
    value: str
    state: str
    observed_at: str
    source_id: str
    provenance: str
    direct: bool
    typed: bool
    authority: str
    temporal_role: str


_ATOMIC_TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$", re.I)
_ATOMIC_NAMESPACE_RE = re.compile(
    r"^[a-z][a-z0-9_.-]{1,31}:[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$", re.I,
)
_ATOMIC_ACTOR_RE = re.compile(
    r"^[a-z][a-z0-9_-]{1,31}\.[A-Za-z0-9][A-Za-z0-9_.-]{1,79}$", re.I,
)
_ATOMIC_PREDICATE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
_ATOMIC_FIELD_ALIASES = {
    "status": "status", "상태": "status",
    "done": "done", "완료": "done",
    "assignee": "assignee", "담당": "assignee", "담당자": "assignee",
    "duedate": "duedate", "due": "duedate", "마감": "duedate",
    "마감일": "duedate", "기한": "duedate",
    "parent": "parent_key", "parentkey": "parent_key", "parent_key": "parent_key",
    "부모": "parent_key", "부모티켓": "parent_key",
    "epic": "epic_key", "epickey": "epic_key", "epic_key": "epic_key",
    "priority": "priority", "우선순위": "priority",
    "resolution": "resolution", "해결": "resolution",
    "sp": "story_points", "storypoints": "story_points",
    "story_points": "story_points",
    "components": "components", "component": "components",
    "labels": "labels", "label": "labels",
    "summary": "summary", "title": "summary", "type": "issue_type",
    "issuetype": "issue_type", "issue_type": "issue_type",
}
_MATERIALIZED_ATOMIC_FIELDS = (
    ("status", "status"), ("done", "done"), ("assignee", "assignee"),
    ("duedate", "duedate"), ("parentKey", "parent_key"),
    ("epicKey", "epic_key"), ("priority", "priority"),
    ("resolution", "resolution"), ("sp", "story_points"),
    ("components", "components"), ("labels", "labels"),
    ("summary", "summary"), ("title", "summary"),
    ("type", "issue_type"), ("issuetype", "issue_type"),
)
_MATERIALIZED_ATOMIC_PREDICATES = frozenset(
    predicate for _field, predicate in _MATERIALIZED_ATOMIC_FIELDS
)


def _atomic_text(value: Any, limit: int = 500) -> str:
    if isinstance(value, (list, tuple, dict)):
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    elif isinstance(value, bool):
        rendered = "true" if value else "false"
    else:
        rendered = str(value or "")
    return re.sub(r"\s+", " ", rendered).strip()[:limit]


def _atomic_source_identity(item: dict) -> tuple[str, str]:
    key = str(item.get("key") or "").strip().upper()
    if _ATOMIC_TICKET_RE.fullmatch(key):
        return f"ticket:{key}", key
    url = str(item.get("url") or "").strip()
    if _valid_url(url):
        identity = f"url:{_clean_url(url)}"
        return identity[:1000], identity[:160]
    label = _atomic_text(item.get("key") or item.get("title"), 180)
    identity = f"text:{label.casefold()}" if label else "text:unknown"
    return identity, identity


def _atomic_subject(value: Any, default: str) -> str:
    exact = str(value or "").strip()
    if (_ATOMIC_TICKET_RE.fullmatch(exact) or _ATOMIC_NAMESPACE_RE.fullmatch(exact)
            or _ATOMIC_ACTOR_RE.fullmatch(exact)):
        return exact.upper() if _ATOMIC_TICKET_RE.fullmatch(exact) else exact
    return default


def _atomic_evidence_claims_source(observation: dict, default: str) -> bool:
    """A model observation may confirm its source identity, but may never rebind it."""
    claimed = str(observation.get("subject_id") or "").strip()
    return not claimed or claimed.casefold() == str(default or "").casefold()


def _atomic_predicate(observation: dict) -> tuple[str, bool]:
    raw = str(observation.get("predicate") or observation.get("field") or "").strip()
    folded = re.sub(r"[\s_-]+", "", raw).casefold()
    if folded in _ATOMIC_FIELD_ALIASES:
        return _ATOMIC_FIELD_ALIASES[folded], True
    if raw and _ATOMIC_PREDICATE_RE.fullmatch(raw):
        return raw, True
    return "untyped", False


def _atomic_timestamp(value: str) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _materialized_observation_catalog(
        state: dict) -> dict[tuple[str, str, str], list[tuple[str, str]]]:
    """Index exact Jira description/comment cells without interpreting their prose."""
    catalog: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    ledger = state.get("materialized_ticket_sources") or {}
    if not isinstance(ledger, dict):
        return catalog
    for row in ledger.get("ticketDetails") or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        key = str(row.get("key") or "").strip().upper()
        if not _ATOMIC_TICKET_RE.fullmatch(key):
            continue
        observed_at = _atomic_text(row.get("updated") or row.get("created"), 80)
        description = _atomic_text(row.get("description"), 420)
        if description:
            catalog.setdefault((key, "description", description), []).append((
                observed_at,
                f"materialized_ticket_sources.ticketDetails[{key}].description",
            ))
        for comment_index, comment in enumerate(row.get("comments") or []):
            if not isinstance(comment, dict):
                continue
            comment_text = _atomic_text(
                comment.get("body") or comment.get("text") or comment.get("html")
                or comment.get("snippet") or comment.get("comment"), 420,
            )
            if not comment_text:
                continue
            comment_date = _atomic_text(
                comment.get("observed_at") or comment.get("updated")
                or comment.get("modified") or comment.get("created")
                or comment.get("date"), 80,
            )
            catalog.setdefault((key, "comment", comment_text), []).append((
                comment_date,
                (f"materialized_ticket_sources.ticketDetails[{key}]"
                 f".comments[{comment_index}]"),
            ))
    return catalog


def _resolve_atomic_temporality(facts: Iterable[AtomicFact]) -> list[AtomicFact]:
    """Resolve time only inside an exact typed ``subject_id + predicate`` group.

    A later row wins only when both rows are direct and both timestamps parse. Equal-time
    differing values are contemporary conflicts. Missing dates and untyped observations never
    become current merely because they happened to appear later in an array.
    """
    rows: list[AtomicFact] = [dict(row) for row in facts]  # type: ignore[list-item]
    groups: dict[tuple[str, str], list[int]] = {}
    for index, row in enumerate(rows):
        if not row.get("typed"):
            row["temporal_role"] = "untyped"
            continue
        groups.setdefault((row["subject_id"], row["predicate"]), []).append(index)

    for indices in groups.values():
        for index in indices:
            rows[index]["temporal_role"] = (
                "current" if rows[index].get("authority") == "materialized_ticket_sources"
                else "observed"
            )
        # A model-authored row that merely repeats an exact materialized snapshot field is
        # useful corroboration, not a newer snapshot.  Exclude that duplicate from the clock
        # whenever the canonical field itself is present.  Deterministic ``extra_facts`` remain
        # eligible to supersede because their producer, subject, predicate, and timestamp are
        # code-owned rather than model-owned.
        has_materialized = any(
            rows[index].get("authority") == "materialized_ticket_sources"
            for index in indices
        )
        dated = [
            (index, _atomic_timestamp(rows[index].get("observed_at", "")))
            for index in indices
            if rows[index].get("direct")
            and not (has_materialized
                     and rows[index].get("authority") == "materialized_match")
        ]
        dated = [(index, stamp) for index, stamp in dated if stamp is not None]
        if len(dated) < 2:
            continue
        latest_stamp = max(stamp for _index, stamp in dated)
        latest = [index for index, stamp in dated if stamp == latest_stamp]
        latest_values = {rows[index]["value"] for index in latest}
        for index, stamp in dated:
            if stamp < latest_stamp:
                rows[index]["temporal_role"] = "historical"
        if len(latest_values) > 1:
            for index in latest:
                rows[index]["temporal_role"] = "conflict"
        else:
            for index in latest:
                rows[index]["temporal_role"] = "current"
        dated_indices = {index for index, _stamp in dated}
        for index in indices:
            if index in dated_indices:
                continue
            # Undated direct or indirect values remain unresolved when they disagree with
            # the dated current value; they cannot silently become history or current state.
            if rows[index]["value"] not in latest_values:
                rows[index]["temporal_role"] = "unresolved"
    return rows


def _cap_atomic_facts(rows: list[AtomicFact], cap: int) -> list[AtomicFact]:
    """Bound the ledger after resolution so late temporal pairs are not split off."""
    limit = max(0, int(cap or 0))
    if len(rows) <= limit:
        return rows
    temporal_groups = {
        (row["subject_id"], row["predicate"]) for row in rows
        if row.get("typed")
        and row.get("temporal_role") in {"historical", "conflict", "unresolved"}
    }
    roles = {"conflict": 0, "current": 1, "historical": 2,
             "unresolved": 3, "observed": 4, "untyped": 5}
    fields = {"status": 0, "done": 1, "duedate": 2, "assignee": 3,
              "parent_key": 4, "epic_key": 5, "priority": 6}
    ranked = sorted(range(len(rows)), key=lambda index: (
        0 if (rows[index]["subject_id"], rows[index]["predicate"])
        in temporal_groups else 1,
        0 if rows[index].get("typed") else 2,
        roles.get(rows[index].get("temporal_role", ""), 9),
        fields.get(rows[index].get("predicate", ""), 20), index,
    ))
    selected: set[int] = set()
    # A temporal progression is useful only as a pair/set. Select an entire exact
    # subject+predicate group when it fits; never spend the last slot on half a history.
    for key in sorted(temporal_groups, key=lambda group: min(
            ranked.index(index) for index, row in enumerate(rows)
            if (row["subject_id"], row["predicate"]) == group)):
        group_indices = [index for index, row in enumerate(rows)
                         if (row["subject_id"], row["predicate"]) == key]
        if len(selected) + len(group_indices) <= limit:
            selected.update(group_indices)
    for index in ranked:
        if len(selected) >= limit:
            break
        key = (rows[index]["subject_id"], rows[index]["predicate"])
        if key in temporal_groups and index not in selected:
            continue
        selected.add(index)
    return [row for index, row in enumerate(rows) if index in selected]


def build_atomic_fact_ledger(state: dict, *, extra_facts: Iterable[dict] = (),
                             cap: int = 96) -> list[AtomicFact]:
    """Project verified state into a bounded, provenance-preserving atomic ledger.

    The original ``evidence`` and ``materialized_ticket_sources`` containers are never changed.
    Natural-language text is deliberately not parsed into a subject or field. Model-authored
    evidence stays untyped unless its exact field value or complete description/comment text can
    be rebound to the same ticket in the canonical materialized snapshot. Deterministic producers
    opt in through ``extra_facts``; canonical materialized Jira rows contribute typed fields.
    """
    facts: list[AtomicFact] = []
    canonical_fields: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    canonical_observations = _materialized_observation_catalog(state)

    ledger = state.get("materialized_ticket_sources") or {}
    if isinstance(ledger, dict):
        for row_index, row in enumerate(ledger.get("ticketDetails") or []):
            if not isinstance(row, dict) or row.get("error"):
                continue
            key = str(row.get("key") or "").strip().upper()
            if not _ATOMIC_TICKET_RE.fullmatch(key):
                continue
            observed_at = _atomic_text(row.get("updated") or row.get("created"), 80)
            seen_predicates: set[str] = set()
            for field, predicate in _MATERIALIZED_ATOMIC_FIELDS:
                if predicate in seen_predicates or field not in row:
                    continue
                raw_value = row.get(field)
                if raw_value in (None, "", []):
                    continue
                value = _atomic_text(raw_value)
                if not value:
                    continue
                seen_predicates.add(predicate)
                facts.append(AtomicFact(
                    fact_id=f"materialized:{key}:{predicate}:{row_index}",
                    subject_id=key, predicate=predicate, value=value,
                    state=value if predicate in {"status", "done", "resolution"} else "",
                    observed_at=observed_at, source_id=f"ticket:{key}",
                    provenance=(f"materialized_ticket_sources.ticketDetails[{key}].{field}"
                                + (f" @ {observed_at}" if observed_at else "")),
                    direct=True, typed=True, authority="materialized_ticket_sources",
                    temporal_role="current",
                ))
                canonical_fields.setdefault((key, predicate, value), []).append((
                    observed_at,
                    f"materialized_ticket_sources.ticketDetails[{key}].{field}",
                ))

    for item_index, item in enumerate(state.get("evidence") or []):
        if not isinstance(item, dict):
            continue
        source_id, default_subject = _atomic_source_identity(item)
        for observation_index, observation in enumerate(item.get("observations") or []):
            if not isinstance(observation, dict):
                continue
            declared_predicate, declared_typed = _atomic_predicate(observation)
            subject = default_subject
            location = str(observation.get("source") or "observation").strip().casefold()
            text = _atomic_text(observation.get("text"))
            explicit_value = observation.get("value")
            value = _atomic_text(
                explicit_value if explicit_value not in (None, "")
                else observation.get("state") if observation.get("state") not in (None, "")
                else text
            )
            if not value:
                continue
            supplied_observed_at = _atomic_text(observation.get("observed_at"), 80)
            observed_at = supplied_observed_at
            predicate = "untyped"
            typed = False
            direct = False
            authority = "evidence"
            canonical_provenance = ""

            # A field assertion is safe only when its exact subject+field+value already
            # exists in the materialized snapshot. The canonical timestamp, not a supplied
            # model timestamp, owns its recency.
            field_matches = canonical_fields.get(
                (subject, declared_predicate, value), [],
            ) if declared_typed and _atomic_evidence_claims_source(
                observation, default_subject,
            ) and not observation.get("actor_id") else []
            if field_matches:
                observed_at, canonical_provenance = field_matches[-1]
                predicate = declared_predicate
                typed = direct = True
                authority = "materialized_match"
            else:
                # For free-form descriptions/comments, exact text proves provenance only.
                # A model-declared predicate/value cannot create a temporal relation, so each
                # canonical cell receives its own opaque relation identity.
                canonical_location = "comment" if location in {"comment", "comments"} else location
                text_matches = canonical_observations.get(
                    (subject, canonical_location, _atomic_text(text, 420)), [],
                ) if (text and canonical_location in {"comment", "description"}) else []
                if len(text_matches) == 1:
                    observed_at, canonical_provenance = text_matches[0]
                    digest = sha256(canonical_provenance.encode("utf-8")).hexdigest()[:16]
                    predicate = f"canonical_observation:{digest}"
                    value = _atomic_text(text, 420)
                    typed = direct = True
                    authority = "materialized_match"

            supplied_provenance = _atomic_text(observation.get("provenance"), 240)
            provenance = f"{source_id}#{location}:{observation_index + 1}"
            if observed_at:
                provenance += f" @ {observed_at}"
            if canonical_provenance:
                provenance += f" · {canonical_provenance}"
            if supplied_provenance and not typed:
                provenance += f" · {supplied_provenance}"
            fact_state = (_atomic_text(observation.get("state"), 120) if not typed
                          else value if predicate in {"status", "done", "resolution"}
                          else "")
            facts.append(AtomicFact(
                fact_id=f"evidence:{item_index}:{observation_index}:{subject}:{predicate}",
                subject_id=subject, predicate=predicate, value=value,
                state=fact_state,
                observed_at=observed_at, source_id=source_id,
                provenance=provenance[:700], direct=direct, typed=typed,
                authority=authority, temporal_role="observed" if typed else "untyped",
            ))

    for index, raw in enumerate(extra_facts):
        if not isinstance(raw, dict):
            continue
        subject = _atomic_subject(raw.get("subject_id"), "")
        predicate, typed = _atomic_predicate(raw)
        value = _atomic_text(raw.get("value") if raw.get("value") not in (None, "")
                             else raw.get("state"))
        source_id = _atomic_text(raw.get("source_id"), 300)
        if not subject or not typed or not value or not source_id:
            continue
        facts.append(AtomicFact(
            fact_id=_atomic_text(raw.get("fact_id"), 300) or f"extra:{index}:{subject}:{predicate}",
            subject_id=subject, predicate=predicate, value=value,
            state=_atomic_text(raw.get("state"), 120),
            observed_at=_atomic_text(raw.get("observed_at"), 80),
            source_id=source_id,
            provenance=_atomic_text(raw.get("provenance"), 700) or source_id,
            direct=bool(raw.get("direct")), typed=True,
            authority=_atomic_text(raw.get("authority"), 80) or "deterministic",
            temporal_role="observed",
        ))

    return _cap_atomic_facts(_resolve_atomic_temporality(facts), cap)


def canonical_observation_facts(state: dict, evidence: Iterable[dict]) -> list[dict]:
    """Build semantic overlays only for unique exact materialized Jira observations.

    Research evidence remains display data. Neither its ``direct`` flag nor its semantic
    fields are trusted. A description/comment receives authority only when its complete
    normalized text occurs exactly once under the same materialized ticket.
    """
    catalog = _materialized_observation_catalog(state)
    facts: list[dict] = []
    for item_index, item in enumerate(bind_evidence_provenance(evidence or [])):
        key = str(item.get("key") or "").strip().upper()
        if not _ATOMIC_TICKET_RE.fullmatch(key) or item.get("_source_id") != f"ticket:{key}":
            continue
        sidecar = item.get("_provenance") or {}
        observation_ids = {
            int(row.get("ordinal") or 0): str(row.get("observation_id") or "")
            for row in (sidecar.get("observations") or []) if isinstance(row, dict)
        }
        for ordinal, observation in enumerate(item.get("observations") or [], 1):
            if not isinstance(observation, dict):
                continue
            location = str(observation.get("source") or "").strip().casefold()
            location = "comment" if location in {"comment", "comments"} else location
            if location not in {"comment", "description"}:
                continue
            text = _atomic_text(observation.get("text"), 420)
            matches = catalog.get((key, location, text), []) if text else []
            # Duplicate canonical cells are ambiguous even if model payload metadata happens
            # to name one timestamp; supplied metadata cannot select authority.
            if len(matches) != 1 or not observation_ids.get(ordinal):
                continue
            observed_at, provenance = matches[0]
            facts.append({
                "fact_id": f"canonical-observation:{item_index}:{ordinal}",
                "observation_id": observation_ids[ordinal],
                "source_id": f"ticket:{key}", "subject_id": key,
                # Exact text proves where an observation came from, not what it entails.
                # Give every free-text cell its own relation so an unrelated later comment
                # cannot supersede it, and never promote prose to a completion event.
                "predicate": f"canonical_observation:{observation_ids[ordinal]}",
                "value": text, "claim_kind": "observation", "observed_at": observed_at,
                "normalized_text": text, "provenance": provenance,
                "direct": True, "typed": True, "authority": "materialized_match",
                "state": "",
                "temporal_role": "observed",
            })
    return facts


def atomic_fact_sidecar(state: dict, *, extra_facts: Iterable[dict] = (),
                        limit: int = 24) -> list[dict]:
    """Return only typed facts for the LLM; raw/untyped evidence stays in its original block."""
    facts = [row for row in build_atomic_fact_ledger(state, extra_facts=extra_facts)
             if row.get("typed")]
    priority = {"conflict": 0, "current": 1, "historical": 2,
                "unresolved": 3, "observed": 4}
    field_priority = {
        "status": 0, "done": 1, "duedate": 2, "assignee": 3,
        "parent_key": 4, "epic_key": 5, "priority": 6,
    }
    groups: dict[tuple[str, str], list[tuple[int, AtomicFact]]] = {}
    for index, row in enumerate(facts):
        groups.setdefault((row["subject_id"], row["predicate"]), []).append((index, row))
    ordered_groups = sorted(groups.values(), key=lambda group: (
        0 if any(row.get("temporal_role") in {"historical", "conflict", "unresolved"}
                 for _index, row in group) else 1,
        min(priority.get(row.get("temporal_role", ""), 9) for _index, row in group),
        min(field_priority.get(row.get("predicate", ""), 20) for _index, row in group),
        group[0][0],
    ))
    selected: list[tuple[int, AtomicFact]] = []
    bounded = max(0, int(limit or 0))
    for group in ordered_groups:
        if len(selected) + len(group) <= bounded:
            selected.extend(group)
        if len(selected) == bounded:
            break
    compact = []
    for _index, row in selected:
        projected = {key: row[key] for key in (
            "subject_id", "predicate", "value", "state", "observed_at", "source_id",
            "provenance", "direct", "authority", "temporal_role",
        )}
        projected["value"] = _atomic_text(projected["value"], 240)
        projected["state"] = _atomic_text(projected["state"], 80)
        projected["source_id"] = _atomic_text(projected["source_id"], 240)
        projected["provenance"] = _atomic_text(projected["provenance"], 320)
        compact.append(projected)
    return compact


_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_DATED_WEEKDAY_RE = re.compile(
    r"(?<!\d)(\d{4}-\d{2}-\d{2})\s*(?:\(\s*([월화수목금토일])요일?\s*\)|"
    r"([월화수목금토일])요일)", re.I,
)
_RELATIVE_DURATION_RE = re.compile(
    r"(?<![0-9A-Za-z가-힣])((?:한|두|세|네|\d+)\s*주(?:일)?|일주일|\d+\s*일)"
    r"(?![0-9A-Za-z])",
)
_ACTION_FROM_DATE_RE = re.compile(
    r"(?:권고|추천|착수|배포|출시|승인|진행(?:하|해)|실행(?:하|해)|"
    r"recommend|deploy|release|approve|proceed)", re.I,
)
_DATE_MATH_CLAUSE_SPLIT_RE = re.compile(
    r"([.!?;]+(?:[ \t]+|$)|[ \t]*\|[ \t]*)",
)
_DATE_INTERVAL_CONNECTOR_RE = re.compile(
    r"(?:\s*(?:부터|에서)\s*|\s*[~～]\s*|\s+(?:-|–|—)\s+|"
    r"\s+(?:to|through)\s+)", re.I,
)
_DATE_DURATION_BRIDGE_RE = re.compile(
    r"\s*(?:\([월화수목금토일]요일\))?\s*(?:까지(?:의)?\s*)?"
    r"(?:(?:총|정확히|약|for)\s*)?(?:기간(?:은|이|:)?\s*)?",
    re.I,
)


def _duration_days(value: str) -> int | None:
    compact = re.sub(r"\s+", "", str(value or ""))
    if compact == "일주일" or compact.startswith("한주"):
        return 7
    words = {"두": 2, "세": 3, "네": 4}
    week = re.fullmatch(r"(두|세|네|\d+)주(?:일)?", compact)
    if week:
        count = words.get(week.group(1), int(week.group(1)) if week.group(1).isdigit() else 0)
        return count * 7
    day = re.fullmatch(r"(\d+)일", compact)
    return int(day.group(1)) if day else None


def _enforce_exact_date_math(value: str) -> str:
    weekdays = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")

    def correct_weekday(match: re.Match) -> str:
        try:
            parsed = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            return match.group(0)
        return f"{match.group(1)}({weekdays[parsed.weekday()]})"

    def repair_clause(clause: str) -> str:
        """Compare dates and relative duration only inside one structural clause."""
        dates = list(_ISO_DATE_RE.finditer(clause))
        durations = list(_RELATIVE_DURATION_RE.finditer(clause))
        if len(dates) < 2 or not durations:
            return clause
        # A shared sentence is not evidence that its dates and duration describe the same
        # fact. Require an explicit interval connector *and* a bounded bridge from the
        # interval end to the duration. If more than one relation fits, fail closed.
        relations: list[tuple[re.Match, re.Match, re.Match]] = []
        for start_match, end_match in zip(dates, dates[1:]):
            connector = clause[start_match.end():end_match.start()]
            if not _DATE_INTERVAL_CONNECTOR_RE.fullmatch(connector):
                continue
            for duration_match in durations:
                if duration_match.start() < end_match.end():
                    continue
                bridge = clause[end_match.end():duration_match.start()]
                if _DATE_DURATION_BRIDGE_RE.fullmatch(bridge):
                    relations.append((start_match, end_match, duration_match))
                break
        if len(relations) != 1:
            return clause
        start_match, end_match, mismatch = relations[0]
        try:
            start = datetime.strptime(start_match.group(1), "%Y-%m-%d").date()
            end = datetime.strptime(end_match.group(1), "%Y-%m-%d").date()
        except ValueError:
            return clause
        exact_days = abs((end - start).days)
        if _duration_days(mismatch.group(1)) in (None, exact_days):
            return clause
        relative = mismatch.group(1)
        conflict = f"정확히 {exact_days}일(상대 표현 '{relative}'와 불일치)"
        fixed = clause[:mismatch.start()] + conflict + clause[mismatch.end():]
        # Preserve the useful dated statement. Remove only a downstream action whose
        # premise is the false duration, and leave an explicit deterministic boundary.
        action_clause = re.search(
            r"(?:이므로|이어서|이기\s*때문에|때문에|따라서|그러므로|so|therefore)"
            r"[^.!?]*(?:권고|추천|착수|배포|출시|승인|진행|실행|"
            r"recommend|deploy|release|approve|proceed)[^.!?]*[.!?]?",
            fixed, re.I,
        )
        if action_clause and _ACTION_FROM_DATE_RE.search(action_clause.group(0)):
            fixed = (fixed[:action_clause.start()].rstrip(" ,;:")
                     + " 날짜 산술 불일치에 근거한 조치 문구는 제외함")
        return fixed

    corrected = _DATED_WEEKDAY_RE.sub(correct_weekday, str(value or ""))
    rendered = []
    for line in corrected.splitlines():
        # Sentence punctuation, semicolons, and Markdown table cells are minimum safe
        # relation boundaries when no typed interval id is available.  Never borrow a
        # duration from a neighboring claim merely because both appear on one line.
        parts = _DATE_MATH_CLAUSE_SPLIT_RE.split(line)
        rendered.append("".join(
            part if index % 2 else repair_clause(part)
            for index, part in enumerate(parts)
        ))
    return "\n".join(rendered)


def enforce_atomic_fact_boundaries(text: str, facts: Iterable[AtomicFact]) -> str:
    """Repair only exact, mechanically provable subject-field leakage in reply prose.

    This guard never interprets free text. It currently covers Jira due dates because an ISO
    date copied from a parent into a due-null child is both detectable and safely removable.
    Raw observations under ``### 근거`` are immutable history and are therefore excluded.
    """
    value = str(text or "")
    heading = re.search(r"(?m)^###\s*근거\s*$", value)
    body, tail = (value[:heading.start()], value[heading.start():]) if heading else (value, "")
    body = _enforce_exact_date_math(body)
    rows = list(facts)
    due_by_subject: dict[str, set[str]] = {}
    for row in rows:
        if (row.get("typed") and row.get("predicate") == "duedate"
                and row.get("authority") == "materialized_ticket_sources"
                and row.get("temporal_role") == "current"):
            due_by_subject.setdefault(row["subject_id"], set()).add(row["value"])
    all_due = {due for values in due_by_subject.values() for due in values}
    status_by_subject: dict[str, str] = {}
    for row in rows:
        if (row.get("typed") and row.get("predicate") == "status"
                and row.get("temporal_role") == "current" and row.get("value")):
            status_by_subject[row["subject_id"]] = row["value"]
    due_pattern = re.compile(
        r"(?:마감(?:일)?|기한|due\s*date)\s*(?:은|는|이|가|:)?\s*"
        r"(\d{4}-\d{2}-\d{2})", re.I,
    )
    repaired = []
    for line in body.splitlines():
        keys = {match.upper() for match in re.findall(
            r"(?<![A-Za-z0-9-])([A-Z][A-Z0-9]*-\d+)(?![A-Za-z0-9-])", line, re.I,
        )}
        matched_due = due_pattern.search(line)
        if len(keys) == 1 and matched_due:
            subject = next(iter(keys))
            date = matched_due.group(1)
            allowed = due_by_subject.get(subject, set())
            if date in all_due and date not in allowed:
                line = due_pattern.sub("마감 확인되지 않음", line, count=1)
        def repair_status_clause(clause: str) -> str:
            clause_keys = sorted({match.upper() for match in re.findall(
                r"(?<![A-Za-z0-9-])([A-Z][A-Z0-9]*-\d+)(?![A-Za-z0-9-])",
                clause, re.I,
            ) if match.upper() in status_by_subject})
            if len(clause_keys) < 2 or not re.search(
                    r"(?:Jira\s*)?(?:상태|status)\b", clause, re.I):
                return clause
            ledger = "; ".join(
                f"{key}={status_by_subject[key]}" for key in clause_keys
            )
            values = {status_by_subject[key].casefold() for key in clause_keys}
            if len(values) == 1 and next(iter(values)) in clause.casefold():
                return clause
            indent = re.match(r"^\s*(?:[-*+]\s+)?", clause).group(0)
            return f"{indent}티켓별 Jira 상태: {ledger}"

        status_parts = _DATE_MATH_CLAUSE_SPLIT_RE.split(line)
        line = "".join(
            part if index % 2 else repair_status_clause(part)
            for index, part in enumerate(status_parts)
        )
        repaired.append(line)
    body = "\n".join(repaired)

    temporal: dict[tuple[str, str], list[AtomicFact]] = {}
    for row in rows:
        if (row.get("typed") and _ATOMIC_TICKET_RE.fullmatch(row.get("subject_id", ""))
                and row.get("temporal_role") in {"current", "historical", "conflict"}):
            temporal.setdefault((row["subject_id"], row["predicate"]), []).append(row)
    current_additions: list[str] = []
    for (subject, _predicate), group in temporal.items():
        if any(row.get("temporal_role") == "conflict" for row in group):
            continue
        currents = [row for row in group if row.get("temporal_role") == "current"]
        historical = [row for row in group if row.get("temporal_role") == "historical"]
        current_values = {row.get("value", "") for row in currents if row.get("value")}
        if len(current_values) != 1 or not historical:
            continue
        current = max(currents, key=lambda row: _atomic_timestamp(row.get("observed_at", "")) or 0)
        changed = False
        rewritten: list[str] = []
        for line in body.splitlines():
            exact_subject = bool(re.search(
                rf"(?<![A-Za-z0-9-]){re.escape(subject)}(?![A-Za-z0-9-])", line, re.I,
            ))
            if not exact_subject or re.search(r"과거|이전|당시|histor(?:y|ical)|previous", line, re.I):
                rewritten.append(line)
                continue
            matched_history = next((row for row in historical
                                    if len(row.get("value", "")) >= 4
                                    and re.search(r"[A-Za-z가-힣]", row.get("value", ""))
                                    and row["value"].casefold() in line.casefold()
                                    and (not row.get("observed_at")
                                         or row["observed_at"] not in line)), None)
            if not matched_history:
                rewritten.append(line)
                continue
            stamp = matched_history.get("observed_at") or "시점 미상"
            rewritten.append(f"이전 기록({stamp}) [과거·보조 근거]: {line}")
            changed = True
        if changed:
            body = "\n".join(rewritten)
            if current["value"].casefold() not in body.casefold():
                stamp = current.get("observed_at") or "시점 미상"
                current_additions.append(
                    f"현재 기록({stamp}): " + "{{ticket-inline:" + subject + "}}"
                    + f" — {current['value']}"
                )
    if current_additions:
        body = body.rstrip() + "\n\n" + "\n".join(current_additions) + "\n"
    return (body + tail).strip()


def _clean_url(url: str) -> str:
    """Normalize only identity-safe URL parts; preserve the displayed source URL."""
    try:
        p = urlsplit(str(url or "").strip())
        # Confluence emits both an encoded URL (``%5B회의록%5D+...``) and a decoded
        # browser URL (``[회의록]+...``) for the same page.  Identity comparison must
        # decode both percent escapes and the legacy ``+`` space spelling, while the
        # original source URL remains untouched for display and navigation.
        path = re.sub(r"/{2,}", "/", unquote_plus(p.path)).rstrip("/")
        query = unquote_plus(p.query)
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, query, ""))
    except Exception:
        return str(url or "").strip().rstrip("/")


def _valid_url(url: str) -> bool:
    return bool(re.match(r"^https?://", str(url or "").strip(), re.I))


def _observation(text: str, source: str = "") -> str:
    value = re.sub(r"^\s*(?:—|–|--|:)\s*", "", str(text or "")).strip()
    # A legacy/model-written observation can carry its old source marker at the
    # end. The canonical bullet itself receives the new marker, so retaining the
    # old one produces impossible cross-links such as ``[2-a] ... [3-a]`` after
    # renumbering. Remove only trailing citation tokens, not bracketed data in the
    # middle of a finding.
    value = re.sub(r"(?:\s*\[\d+(?:-[a-z])?\])+\s*$", "", value, flags=re.I).rstrip()
    if not value:
        return ""
    # Already source-qualified: never produce "본문에서 본문에서 ...".
    if re.match(r"^(?:본문|댓글|코멘트|변경 이력|문서 본문|웹 문서|조회 결과)에서\b", value):
        return value.replace("코멘트에서", "댓글에서", 1)
    labels = {
        "description": "본문에서", "body": "본문에서",
        "comment": "댓글에서", "comments": "댓글에서",
        "field": "변경 이력에서", "change": "변경 이력에서",
        "history": "변경 이력에서", "document": "문서 본문에서",
        "confluence": "문서 본문에서", "external": "웹 문서에서",
        "web": "웹 문서에서", "query": "조회 결과에서",
    }
    prefix = labels.get(str(source or "").strip().lower(), "")
    return f"{prefix} {value}".strip()


def _source_parts(raw: str) -> tuple[str, str, str]:
    """Return ``(identity, canonical source, observation)`` for a legacy root row."""
    value = str(raw or "").strip()
    cut = _CUT_RE.match(value)
    left = (cut.group(1) if cut else value).strip()
    why = (cut.group(2) if cut else "").strip()

    token = _TOKEN_RE.search(left)
    key_match = token or _KEY_RE.search(left)
    if key_match:
        key = key_match.group(1).upper()
        tail = left[key_match.end():].strip(" \t—–-:,{}")
        comment = re.search(r"(?:코멘트|댓글)\s*(\([^)]*\))?", tail, re.I)
        if comment:
            detail = re.sub(r"^(?:댓글|코멘트)에서\s*", "", why).strip()
            meta = (comment.group(1) or "").strip()
            obs = f"댓글{meta}에서 {detail}".strip()
        else:
            obs = _observation(why, "description")
        return f"ticket:{key}", f"{{{{ticket-detail:{key}}}}}", obs

    legacy_link = re.match(r"^(.+?)\s+\((https?://[^\s)]+)\)$", left)
    if legacy_link:
        title, url = legacy_link.group(1).strip(), legacy_link.group(2).strip()
        return (f"url:{_clean_url(url)}", f"[{title}]({url})",
                _observation(why, "document" if _CONFLUENCE_RE.search(url) else "external"))

    link = _MD_LINK_RE.search(left)
    url_match = link or _URL_RE.search(left)
    if url_match:
        if link:
            title, url = link.group(1).strip(), link.group(2).strip()
            source = f"[{title}]({url})"
        else:
            url = url_match.group(0).rstrip(".,;:!?")
            source = url
        return (f"url:{_clean_url(url)}", source,
                _observation(why, "document" if _CONFLUENCE_RE.search(url) else "external"))

    source = left or value
    return f"text:{source.casefold()}", source, _observation(why)


def _split(text: str) -> tuple[str, list[str], str]:
    value = str(text or "")
    heading = _HEADING_RE.search(value)
    if not heading:
        return value.rstrip(), [], ""
    start = heading.end()
    following = _NEXT_HEADING_RE.search(value, start)
    end = following.start() if following else len(value)
    return value[:heading.start()].rstrip(), value[start:end].splitlines(), value[end:].lstrip()


def _append_observation(group: dict, value: str, *, normalized_value: str = "") -> int | None:
    obs = str(value or "").strip()
    if not obs:
        return None
    def comparison_key(text: str) -> str:
        # The model sometimes writes the same finding once as plain prose and once as
        # ``본문에서 ...``/``댓글에서 ...``.  Provenance belongs on the one surviving
        # observation; the prefix must not manufacture a second finding.
        normalized = re.sub(
            r"^(?:본문|댓글|코멘트|변경\s*이력|문서\s*본문|웹\s*문서|조회\s*결과)에서\s*",
            "", str(text or "").strip(), flags=re.I,
        )
        # Research summaries often restate the same observation as
        # ``X한다는 내용이 기록되어 있음`` while structured evidence carries ``X한다``.
        # These reporting suffixes add no finding or provenance, so compare the
        # underlying proposition instead of manufacturing a second child marker.
        normalized = re.sub(
            r"(?:다는|라는)\s*내용(?:이)?\s*(?:기록|포함)되어\s*(?:있음|있다|있습니다)\.?$",
            "다", normalized, flags=re.I,
        )
        normalized = re.sub(
            r"([가-힣]+)한다고\s*(?:명시|기록|언급)(?:되어\s*)?(?:있음|있다|됨)?\.?$",
            r"\1한다", normalized,
        )
        normalized = re.sub(r"(?:되어\s*)?(?:있음|있다|있습니다)\.?$", "", normalized)
        # Compare common report-style endings by their proposition stem.  This is
        # intentionally last-position only: ``진행 중임`` and ``확인한다`` remain
        # distinct findings, while ``금지됨``/``금지한다`` collapse.
        normalized = re.sub(
            r"(?:되었다|되었습니다|되어\s*있음|됨|하였다|했습니다|한다|하다|함)\.?$",
            "", normalized,
        ).strip()
        normalized = re.sub(r"[\s\"'“”‘’.,;:!?]+", " ", normalized).strip()
        return normalized.casefold()

    keys = group.get("_observation_keys")
    if not isinstance(keys, list) or len(keys) != len(group["observations"]):
        keys = [comparison_key(current) for current in group["observations"]]
        group["_observation_keys"] = keys
    key = comparison_key(normalized_value or obs)
    for index, current_key in enumerate(keys):
        if current_key == key:
            return index
    group["observations"].append(obs)
    keys.append(key)
    return len(group["observations"]) - 1


def _citation_tokens(value: str) -> list[str]:
    return list(citation_tokens(value))


def _compact_adjacent_citations(text: str) -> str:
    """Normalize ``[4] [5]`` and ``[4, 5]`` into compact linked markers ``[4][5]``."""
    def merge(match: re.Match) -> str:
        combined: list[str] = []
        for citation in _CITATION_RE.finditer(match.group(0)):
            for token in _citation_tokens(citation.group(1)):
                if token not in combined:
                    combined.append(token)
        return "".join(f"[{token}]" for token in combined)
    return _CITATION_RUN_RE.sub(merge, str(text or ""))


def normalize_evidence_heading_boundary(value: str) -> str:
    """Separate a glued evidence heading without matching inside an existing hash run."""
    return re.sub(
        r"(?<=[^\n#])(#{1,4}\s*(?:근거|참조)\s*)(?=\r?\n)",
        r"\n\n\1", str(value or ""),
    )


def canonicalize_evidence_index(text: str, evidence: list | None = None,
                                related_docs: list | None = None,
                                claim_facts: list | None = None,
                                observation_facts: list | None = None) -> str:
    """Merge every evidence channel into one stable, hierarchical source index."""
    # Bind model ordinals to structured Research sources before any source dedupe or
    # renumbering.  ``[[1-b]]`` is presentation noise, not a different identifier.
    value = normalize_citation_wrappers(text)
    # Style normalization may glue a canonical heading to the preceding sentence. Restore
    # the structural boundary before parsing so stale bare source rows cannot survive beside
    # the rebuilt index and trigger a false late grounding warning.
    value = normalize_evidence_heading_boundary(value)
    provenance_graph = build_claim_provenance_graph(
        value, evidence or [], claim_facts=claim_facts or [],
        observation_facts=observation_facts or [],
    )
    body, lines, tail = _split(value)
    # The canonical source index is always the last section.  Legacy replies sometimes put
    # another heading after references; preserve that content by moving it before the index.
    if tail:
        body = "\n\n".join(part for part in (body, tail) if part)
        tail = ""
    groups: OrderedDict[str, dict] = OrderedDict()
    parsed_rows: list[dict] = []
    current: dict | None = None

    def ensure(identity: str, source: str) -> dict:
        group = groups.get(identity)
        if group is None:
            group = {"identity": identity, "source": source, "observations": [], "rows": []}
            groups[identity] = group
        elif group["source"].startswith("http") and source.startswith("["):
            # Prefer a human title over a bare URL when either legacy path supplied one.
            group["source"] = source
        return group

    def promote_alias(alias: str, identity: str, source: str) -> dict:
        """Upgrade a model-written bare title to the runtime-verified URL source."""
        if alias == identity or alias not in groups:
            return ensure(identity, source)
        alias_group = groups[alias]
        target = groups.get(identity)
        merged = {
            "identity": identity,
            "source": source,
            "observations": [],
            "rows": [*(alias_group.get("rows") or []),
                     *((target or {}).get("rows") or [])],
        }
        for observation in [*(alias_group.get("observations") or []),
                            *((target or {}).get("observations") or [])]:
            _append_observation(merged, observation)
        for row in merged["rows"]:
            row["identity"] = identity
        rebuilt: OrderedDict[str, dict] = OrderedDict()
        for key, value in groups.items():
            if key == alias:
                rebuilt[identity] = merged
            elif key != identity:
                rebuilt[key] = value
        groups.clear()
        groups.update(rebuilt)
        return merged

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        root = _ROOT_RE.match(line)
        # ``[5-a]`` is a child observation, not another source.
        if root and not root.group(2):
            old = root.group(1) or root.group(3)
            identity, source, obs = _source_parts(root.group(4))
            current = ensure(identity, source)
            obs_index = _append_observation(current, obs)
            row = {"old": old, "identity": identity, "observation": obs_index}
            current["rows"].append(row)
            parsed_rows.append(row)
            continue
        child = _CHILD_RE.match(line)
        if child and current:
            obs_index = _append_observation(current, _observation(child.group(3)))
            if child.group(1):
                row = {"old": f"{child.group(1)}-{child.group(2)}",
                       "identity": current["identity"], "observation": obs_index}
                current["rows"].append(row)
                parsed_rows.append(row)

    # Research Analyst state joins by real source identity.  ``why`` is a fallback only;
    # source-specific observations are preferred and remain lossless.
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        ticket = _KEY_RE.fullmatch(key.upper())
        if ticket:
            identity, source = f"ticket:{ticket.group(1)}", f"{{{{ticket-detail:{ticket.group(1)}}}}}"
        elif _valid_url(url or key):
            actual = url or key
            identity = f"url:{_clean_url(actual)}"
            source = f"[{title or actual}]({actual})" if title else actual
        elif key or title:
            source = title or key
            identity = f"text:{source.casefold()}"
        else:
            continue
        alias = f"text:{(title or key).casefold()}" if (title or key) else ""
        if _valid_url(url or key):
            actual = url or key
            # Confluence models sometimes emit only the stable page id as a root
            # source even though Query Runner supplied the verified page URL. Fold
            # that id into the URL source just like a bare document title.
            page = re.search(r"/pages/(\d+)(?:/|$)", actual, re.I)
            page_alias = f"text:{page.group(1).casefold()}" if page else ""
            if page_alias and page_alias in groups:
                group = promote_alias(page_alias, identity, source)
            else:
                group = ensure(identity, source)
            if alias and alias in groups and alias != identity:
                group = promote_alias(alias, identity, source)
        else:
            group = ensure(identity, source)
        observations = item.get("observations") or []
        for obs in observations:
            if isinstance(obs, dict):
                _append_observation(
                    group, _observation(obs.get("text"), obs.get("source")),
                    normalized_value=_observation(
                        obs.get("normalized_text") or obs.get("canonical_text")
                        or obs.get("text"), obs.get("source"),
                    ),
                )
            elif isinstance(obs, str):
                _append_observation(group, _observation(obs))
        if not observations and not group["observations"]:
            _append_observation(group, _observation(item.get("why"), "query"))

    # Related docs hydrate a title/URL already used by the reply or evidence.  They are not
    # appended merely because retrieval returned them: rejected/guide noise must stay internal.
    for doc in related_docs or []:
        if not isinstance(doc, dict):
            continue
        title = str(doc.get("title") or "").strip()
        url = str(doc.get("url") or "").strip()
        if not title or not _valid_url(url):
            continue
        identity = f"url:{_clean_url(url)}"
        alias = f"text:{title.casefold()}"
        mentioned_in_observation = False
        carried: list[str] = []
        # A model can place a related-document link under a ticket as if the link itself
        # were a ticket finding. Promote that link to its own source root; keep only any
        # actual prose finding after the URL under the document source.
        for existing in list(groups.values()):
            if existing.get("identity") == identity:
                continue
            kept = []
            for observation in existing.get("observations") or []:
                urls = _URL_RE.findall(str(observation or ""))
                same_url = any(_clean_url(found.rstrip(".,;:!?")) == _clean_url(url)
                               for found in urls)
                link_only = same_url or (title in str(observation or "") and bool(urls))
                if not link_only:
                    kept.append(observation)
                    continue
                mentioned_in_observation = True
                remainder = str(observation or "")
                remainder = _MD_LINK_RE.sub("", remainder)
                remainder = _URL_RE.sub("", remainder)
                remainder = remainder.replace(title, "").strip(" []()—–-:;,.\t")
                if len(remainder) >= 8:
                    carried.append(_observation(remainder, "document"))
            existing["observations"] = kept
            existing.pop("_observation_keys", None)
        group = promote_alias(alias, identity, f"[{title}]({url})") \
            if alias in groups else groups.get(identity)
        if group:
            group["source"] = f"[{title}]({url})"
        elif title in body or url in body or mentioned_in_observation:
            group = ensure(identity, f"[{title}]({url})")
        if group:
            for observation in carried:
                _append_observation(group, observation)

    # Drop a model-written source shell that has neither a finding nor a body
    # citation. Structured evidence with a real finding already received an
    # observation above. This removes retrieval noise such as an inspected but
    # unused Confluence page without hiding a source explicitly cited in prose.
    old_identity = {row["old"]: row["identity"] for row in parsed_rows}
    cited_identities = set()
    for citation in _CITATION_RE.finditer(body):
        for token in _citation_tokens(citation.group(1)):
            identity = old_identity.get(token) or old_identity.get(token.split("-", 1)[0])
            if identity:
                cited_identities.add(identity)
    for identity in list(groups):
        if not groups[identity]["observations"] and identity not in cited_identities:
            del groups[identity]

    if not groups:
        # Remove only an empty legacy heading.  Never invent a source index.
        if _HEADING_RE.search(str(text or "")):
            body = _CITATION_RE.sub("(근거 확인 필요)", body)
        return (body + ("\n\n" + tail if tail else "")).strip()

    row_by_old = {row["old"]: row for row in parsed_rows}
    identity_order: list[str] = []
    for match in _CITATION_RE.finditer(body):
        for old in _citation_tokens(match.group(1)):
            row = row_by_old.get(old) or row_by_old.get(old.split("-", 1)[0])
            if row and row["identity"] in groups and row["identity"] not in identity_order:
                identity_order.append(row["identity"])
    identity_order.extend(identity for identity in groups if identity not in identity_order)
    number = {identity: index + 1 for index, identity in enumerate(identity_order)}

    marker_map: dict[str, str] = {}
    for row in parsed_rows:
        if row["identity"] not in groups:
            continue
        group = groups[row["identity"]]
        base = str(number[row["identity"]])
        obs_index = row.get("observation")
        if obs_index is not None and len(group["observations"]) > 1:
            marker_map[row["old"]] = f"{base}-{chr(97 + obs_index)}"
        else:
            marker_map[row["old"]] = base

    # A model may cite the ordinal Research array without also reproducing a legacy source
    # root.  Those bindings are typed before rendering, so add them only as a fallback;
    # an explicitly parsed root above always wins when legacy input used a different order.
    graph_sources = {row["ordinal"]: row for row in provenance_graph["sources"]}
    graph_observations: dict[tuple[str, int], dict] = {
        (row["source_id"], row["ordinal"]): row
        for row in provenance_graph["observations"]
    }
    graph_marker_by_target: dict[tuple[str, int], str] = {}
    for ordinal, source_node in graph_sources.items():
        identity = source_node["source_id"]
        group = groups.get(identity)
        if group is None or identity not in number:
            continue
        base = str(number[identity])
        marker_map.setdefault(str(ordinal), base)
        for observation_ordinal in range(1, 27):
            observation_node = graph_observations.get((identity, observation_ordinal))
            if observation_node is None:
                break
            rendered_observation = _observation(
                observation_node.get("text"), observation_node.get("source"),
            )
            observation_index = _append_observation(
                group, rendered_observation,
                normalized_value=_observation(
                    observation_node.get("normalized_text")
                    or observation_node.get("text"), observation_node.get("source"),
                ),
            )
            marker = base
            if observation_index is not None and len(group["observations"]) > 1:
                marker = f"{base}-{chr(97 + observation_index)}"
            marker_map.setdefault(f"{ordinal}-{chr(96 + observation_ordinal)}", marker)
            graph_marker_by_target[(identity, observation_ordinal)] = marker

    claims_by_occurrence: dict[str, list[dict]] = {}
    for claim in provenance_graph["claims"]:
        occurrence_id = str(claim.get("citation_occurrence_id") or "")
        if occurrence_id:
            claims_by_occurrence.setdefault(occurrence_id, []).append(claim)
    for rows in claims_by_occurrence.values():
        rows.sort(key=lambda row: int(row.get("citation_token_index") or 0))
    unsupported_claim_ids = set(provenance_graph.get("unsupported_claim_ids") or [])
    body_occurrence_ids = {
        (match.start(), match.end()): occurrence_id
        for occurrence_id, match, _tokens in citation_occurrences(body)
    }

    def replace_citation(match: re.Match) -> str:
        mapped: list[str] = []
        unresolved = False
        unsupported = False
        occurrence_id = body_occurrence_ids.get((match.start(), match.end()), "")
        bindings = claims_by_occurrence.get(occurrence_id) or []
        by_index = {int(row.get("citation_token_index") or 0): row for row in bindings}
        for token_index, old in enumerate(_citation_tokens(match.group(0)), 1):
            claim = by_index.get(token_index)
            current = ""
            if claim and claim.get("entailment") in {"direct", "rebound"}:
                current = graph_marker_by_target.get((
                    str(claim.get("source_id") or ""),
                    int(claim.get("observation_ordinal") or 0),
                ), "")
            if not current:
                current = marker_map.get(old) or marker_map.get(old.split("-", 1)[0]) or ""
            if current and current not in mapped:
                mapped.append(current)
            elif not current:
                unresolved = True
            if claim and claim.get("claim_id") in unsupported_claim_ids:
                unsupported = True
        citation = "".join(f"[{current}]" for current in mapped)
        if unsupported:
            citation += (" " if citation else "") + "(직접 완료 근거 확인 필요)"
        if unresolved:
            citation += (" " if citation else "") + "(근거 확인 필요)"
        return citation

    body = _compact_adjacent_citations(CITATION_OCCURRENCE_RE.sub(replace_citation, body))
    rendered: list[str] = []
    for identity in identity_order:
        group = groups[identity]
        base = number[identity]
        rendered.append(f"[{base}] {group['source']}")
        observations = group["observations"]
        for index, obs in enumerate(observations):
            marker = f" [{base}-{chr(97 + index)}]" if len(observations) > 1 else ""
            rendered.append(f"-{marker} {obs}".replace("-  ", "- "))

    result = body.rstrip() + "\n\n### 근거\n\n" + "\n".join(rendered)
    if tail:
        result += "\n\n" + tail
    return result.strip()


__all__ = [
    "AtomicFact", "atomic_fact_sidecar", "build_atomic_fact_ledger",
    "build_claim_provenance_graph", "canonicalize_evidence_index",
    "canonical_observation_facts", "enforce_atomic_fact_boundaries",
    "normalize_evidence_heading_boundary",
]
