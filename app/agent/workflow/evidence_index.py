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
import json
import re
from typing import Any, Iterable, TypedDict
from urllib.parse import unquote_plus, urlsplit, urlunsplit


_HEADING_RE = re.compile(
    r"(?m)^(?:#{1,4}\s*(?:근거|참조)|\*\*(?:근거|참조)\*\*)\s*$"
)
_NEXT_HEADING_RE = re.compile(r"(?m)^#{1,4}\s+")
_ROOT_RE = re.compile(
    r"^\s*(?:-\s*)?(?:\[(\d+)(?:-([a-z]))?\]|(\d+)[.)])\s*(.*?)\s*$",
    re.I,
)
_CHILD_RE = re.compile(r"^\s*-\s*(?:\[(\d+)-([a-z])\]\s*)?(.*?)\s*$", re.I)
_CITATION_TOKEN = r"\d+(?:-[a-z])?"
_CITATION_RE = re.compile(
    rf"\[((?:{_CITATION_TOKEN})(?:\s*,\s*{_CITATION_TOKEN})*)\](?!\()", re.I,
)
_CITATION_RUN_RE = re.compile(
    rf"\[(?:{_CITATION_TOKEN})(?:\s*,\s*{_CITATION_TOKEN})*\]"
    rf"(?:\s*,?\s*\[(?:{_CITATION_TOKEN})(?:\s*,\s*{_CITATION_TOKEN})*\])+",
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
    ))[:limit]
    selected = set(ranked)
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
    canonical_observations: dict[
        tuple[str, str, str], list[tuple[str, str]]
    ] = {}

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

            description = _atomic_text(row.get("description"), 420)
            if description:
                canonical_observations.setdefault(
                    (key, "description", description), [],
                ).append((
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
                canonical_observations.setdefault(
                    (key, "comment", comment_text), [],
                ).append((
                    comment_date,
                    (f"materialized_ticket_sources.ticketDetails[{key}]"
                     f".comments[{comment_index}]"),
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
                # For free-form descriptions/comments, the complete normalized observation
                # must equal a canonical source cell. A separately supplied semantic value is
                # not trusted: only the full canonical text may become the fact value.
                canonical_location = "comment" if location in {"comment", "comments"} else location
                text_matches = canonical_observations.get(
                    (subject, canonical_location, _atomic_text(text, 420)), [],
                ) if (declared_typed and text
                      and declared_predicate not in _MATERIALIZED_ATOMIC_PREDICATES
                      and _atomic_evidence_claims_source(observation, default_subject)
                      and not observation.get("actor_id")
                      and explicit_value in (None, "")) else []
                if supplied_observed_at and len(text_matches) > 1:
                    text_matches = [match for match in text_matches
                                    if match[0] == supplied_observed_at]
                if len(text_matches) == 1:
                    observed_at, canonical_provenance = text_matches[0]
                    predicate = declared_predicate
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


def enforce_atomic_fact_boundaries(text: str, facts: Iterable[AtomicFact]) -> str:
    """Repair only exact, mechanically provable subject-field leakage in reply prose.

    This guard never interprets free text. It currently covers Jira due dates because an ISO
    date copied from a parent into a due-null child is both detectable and safely removable.
    Raw observations under ``### 근거`` are immutable history and are therefore excluded.
    """
    value = str(text or "")
    heading = re.search(r"(?m)^###\s*근거\s*$", value)
    body, tail = (value[:heading.start()], value[heading.start():]) if heading else (value, "")
    rows = list(facts)
    due_by_subject: dict[str, set[str]] = {}
    for row in rows:
        if (row.get("typed") and row.get("predicate") == "duedate"
                and row.get("authority") == "materialized_ticket_sources"
                and row.get("temporal_role") == "current"):
            due_by_subject.setdefault(row["subject_id"], set()).add(row["value"])
    all_due = {due for values in due_by_subject.values() for due in values}
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
            rewritten.append(f"이전 기록({stamp}): {line}")
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


def _append_observation(group: dict, value: str) -> int | None:
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

    key = comparison_key(obs)
    for index, current in enumerate(group["observations"]):
        if comparison_key(current) == key:
            return index
    group["observations"].append(obs)
    return len(group["observations"]) - 1


def _citation_tokens(value: str) -> list[str]:
    return [token.strip().lower() for token in str(value or "").split(",") if token.strip()]


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


def canonicalize_evidence_index(text: str, evidence: list | None = None,
                                related_docs: list | None = None) -> str:
    """Merge every evidence channel into one stable, hierarchical source index."""
    body, lines, tail = _split(text)
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
                _append_observation(group, _observation(obs.get("text"), obs.get("source")))
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

    def replace_citation(match: re.Match) -> str:
        mapped: list[str] = []
        unresolved = False
        for old in _citation_tokens(match.group(1)):
            current = marker_map.get(old) or marker_map.get(old.split("-", 1)[0])
            if current and current not in mapped:
                mapped.append(current)
            elif not current:
                unresolved = True
        citation = "".join(f"[{current}]" for current in mapped)
        if unresolved:
            citation += (" " if citation else "") + "(근거 확인 필요)"
        return citation

    body = _compact_adjacent_citations(_CITATION_RE.sub(replace_citation, body))
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
    "canonicalize_evidence_index", "enforce_atomic_fact_boundaries",
]
