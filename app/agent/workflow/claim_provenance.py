"""Typed claim → source → observation provenance for final evidence rendering.

The language model writes numeric citation markers, while Research state stores real source
objects.  Treating those two structures as unrelated strings made valid citations orphan
during source deduplication and renumbering.  This module binds the model's ordinal markers
to stable server-derived identities before rendering.  Numeric labels remain presentation;
source and observation ids are the authority.
"""

from __future__ import annotations

from hashlib import sha256
from typing import TypedDict
from urllib.parse import unquote_plus, urlsplit, urlunsplit
import re


class ProvenanceSource(TypedDict):
    source_id: str
    ordinal: int
    source_class: str
    internal_readiness_authority: bool


class ProvenanceObservation(TypedDict, total=False):
    observation_id: str
    source_id: str
    ordinal: int
    source: str
    text: str
    observed_at: str
    normalized_text: str
    subject_id: str
    predicate: str
    value: str
    claim_kind: str
    temporal_role: str
    direct: bool
    authority: str


class ClaimBinding(TypedDict, total=False):
    claim_id: str
    source_id: str
    observation_id: str
    source_ordinal: int
    observation_ordinal: int
    entailment: str
    citation_occurrence_id: str
    citation_token: str
    citation_token_index: int


class ClaimProvenanceGraph(TypedDict):
    sources: list[ProvenanceSource]
    observations: list[ProvenanceObservation]
    claims: list[ClaimBinding]
    unbound_claim_ids: list[str]
    unsupported_claim_ids: list[str]


_TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$", re.I)
CITATION_TOKEN_PATTERN = r"\d+(?:-[a-z])?"
_CITATION_CONTENT_PATTERN = (
    rf"(?:{CITATION_TOKEN_PATTERN})(?:\s*,\s*{CITATION_TOKEN_PATTERN})*"
)
_CITATION_GROUP_PATTERN = rf"\[{_CITATION_CONTENT_PATTERN}\](?!\()"
CITATION_GROUP_RE = re.compile(
    rf"\[({_CITATION_CONTENT_PATTERN})\](?!\()", re.I,
)
CITATION_OCCURRENCE_RE = re.compile(
    rf"{_CITATION_GROUP_PATTERN}(?:[ \t]*,?[ \t]*{_CITATION_GROUP_PATTERN})*", re.I,
)
_WRAPPED_CITATION_GROUP_RE = re.compile(
    rf"\[\[({_CITATION_CONTENT_PATTERN})\]\]", re.I,
)
_TYPED_CITATION_ALIAS_RE = re.compile(
    r"\{\{ticket-(?:list|inline|detail):([A-Z][A-Z0-9]*-\d+)\}\}", re.I,
)
_CITATION_ALIAS_GROUP_RE = re.compile(r"\[([^\[\]\n]{1,180})\](?!\()")
_CLAIM_SENTENCE_BOUNDARY_RE = re.compile(
    r"\r?\n|[;|!?]|[.](?!\d)(?=\s|$|\[)",
)
EVIDENCE_SECTION_LABEL_PATTERN = r"(?:근거|참조|evidence|sources?|references?)"
EVIDENCE_HEADING_RE = re.compile(
    rf"(?im)^[ \t]*(?:#{{1,6}}[ \t]*{EVIDENCE_SECTION_LABEL_PATTERN}|"
    rf"\*\*{EVIDENCE_SECTION_LABEL_PATTERN}\*\*)[ \t]*$",
)
_COMPLETION_ASSERTION_RE = re.compile(
    r"(?:완료(?:되었|됐|했|함|됨|된|하였|되었습니다|됐습니다|했습니다|하였습니다)?|"
    r"성공(?:했|함|했습니다|하였다|하였습니다)?|"
    r"결과(?:를|가)?\s*(?:확보|생성)(?:했|함|됨|했습니다)?|"
    r"\b(?:completed|complete|succeeded|successful)\b)", re.I,
)
_NEGATED_COMPLETION_RE = re.compile(
    r"(?:미완료|완료(?:되지\s*않|하지\s*않|하지\s*못)|아직.{0,24}완료|"
    r"완료\s*(?:조건|기준|계획|예정|목표|여부)|"
    r"\b(?:not|never)\s+(?:yet\s+)?(?:completed|complete|performed|run)\b|"
    r"\b(?:planned|planning|pending|in\s+progress)\b)", re.I,
)
_CLAIM_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9._:-]{2,}|[가-힣]{2,}")
_CLAIM_STOP = {
    "완료", "성공", "결과", "확보", "생성", "현재", "과거", "기록", "확인", "필요",
    "작업", "진행", "검증", "계획", "예정", "completed", "complete", "successful",
    "succeeded", "result", "current", "historical", "planned", "pending", "validation",
}
_COMPLETION_VALUES = {
    "complete", "completed", "done", "success", "succeeded", "successful", "resolved",
    "완료", "성공", "해결", "result_obtained", "generated",
}


def _clean_url(url: str) -> str:
    try:
        parsed = urlsplit(str(url or "").strip())
        path = re.sub(r"/{2,}", "/", unquote_plus(parsed.path)).rstrip("/")
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path,
                           unquote_plus(parsed.query), ""))
    except Exception:
        return str(url or "").strip().rstrip("/")


def evidence_source_id(item: dict) -> str:
    """Return the same stable identity used by the canonical evidence index."""
    key = str((item or {}).get("key") or "").strip()
    if _TICKET_RE.fullmatch(key):
        return f"ticket:{key.upper()}"
    explicit_url = str((item or {}).get("url") or "").strip()
    url = explicit_url or key
    if url.casefold().startswith(("http://", "https://")):
        return f"url:{_clean_url(url)}"
    # The canonical evidence index intentionally prefers an explicit key over a title.
    label = str(key or (item or {}).get("title") or "").strip()
    return f"text:{label.casefold()}" if label else "text:unknown"


def _source_class(item: dict) -> tuple[str, bool]:
    source_kinds = {
        str(observation.get("source") or "").strip().casefold()
        for observation in ((item or {}).get("observations") or [])
        if isinstance(observation, dict)
    }
    key = str((item or {}).get("key") or "").strip()
    if _TICKET_RE.fullmatch(key):
        return "jira", True
    if source_kinds.intersection({"document", "confluence"}):
        return "internal_document", True
    if source_kinds.intersection({"external", "web"}):
        return "external_specification", False
    url = str((item or {}).get("url") or key).strip()
    if url.casefold().startswith(("http://", "https://")):
        return "external_unclassified", False
    return "untyped", False


def _observation_id(source_id: str, observation: dict) -> str:
    normalized = str(
        observation.get("normalized_text") or observation.get("canonical_text")
        or observation.get("text") or ""
    ).strip()
    material = "\x1f".join((
        str(observation.get("source") or "").strip().casefold(),
        " ".join(normalized.split()).casefold(),
        str(observation.get("observed_at") or "").strip(),
        str(observation.get("subject_id") or "").strip(),
        str(observation.get("predicate") or "").strip(),
    ))
    digest = sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{source_id}#observation:{digest}"


def bind_evidence_provenance(rows) -> list[dict]:
    """Attach server-owned ids in a sidecar without changing observation payloads."""
    bound: list[dict] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        source_id = evidence_source_id(item)
        source_class, readiness = _source_class(item)
        item["_source_id"] = source_id
        item["_source_class"] = source_class
        item["_internal_readiness_authority"] = readiness
        observations = []
        observation_bindings = []
        for ordinal, raw_observation in enumerate(item.get("observations") or [], 1):
            if not isinstance(raw_observation, dict):
                observations.append(raw_observation)
                continue
            observation = dict(raw_observation)
            observations.append(observation)
            observation_bindings.append({
                "ordinal": ordinal,
                "observation_id": _observation_id(source_id, observation),
            })
        item["observations"] = observations
        item["_provenance"] = {
            "source_id": source_id,
            "source_class": source_class,
            "internal_readiness_authority": readiness,
            "observations": observation_bindings,
        }
        bound.append(item)
    return bound


def normalize_citation_wrappers(text: str) -> str:
    """Collapse double wrappers without changing the citation token grammar."""
    return _WRAPPED_CITATION_GROUP_RE.sub(r"[\1]", str(text or ""))


def _citation_alias_key(value: str) -> str:
    return " ".join(str(value or "").split()).strip().casefold()


def _citation_alias_indexes(evidence) -> tuple[dict[str, int], dict[str, set[int]]]:
    bound = bind_evidence_provenance(evidence or [])
    alias_identities: dict[str, set[str]] = {}
    identity_ordinals: dict[str, set[int]] = {}
    first_ordinal_by_identity: dict[str, int] = {}

    def add(alias: str, identity: str) -> None:
        normalized = _citation_alias_key(alias)
        if normalized:
            alias_identities.setdefault(normalized, set()).add(identity)

    for ordinal, item in enumerate(bound, 1):
        identity = str(item.get("_source_id") or "")
        first_ordinal = first_ordinal_by_identity.setdefault(identity, ordinal)
        identity_ordinals[identity] = {first_ordinal}
        key = str(item.get("key") or "").strip()
        title = str(item.get("title") or "").strip()
        add(key, identity)
        add(title, identity)
        # Source labels commonly use ``Short title - publisher``.  The short display alias
        # is safe only when it resolves uniquely after all sources have been inspected.
        for label in (key, title):
            if " - " in label:
                add(label.split(" - ", 1)[0], identity)

    return ({
        alias: first_ordinal_by_identity[next(iter(identities))]
        for alias, identities in alias_identities.items() if len(identities) == 1
    }, identity_ordinals)


def _resolved_citation_alias(content: str, unique_aliases: dict[str, int],
                             identity_ordinals: dict[str, set[int]]) -> str | None:
    value = str(content or "").strip()
    if re.fullmatch(_CITATION_CONTENT_PATTERN, value, re.I):
        return ",".join(citation_tokens(value))
    typed = _TYPED_CITATION_ALIAS_RE.fullmatch(value)
    if typed:
        ordinals = identity_ordinals.get(f"ticket:{typed.group(1).upper()}", set())
        return str(next(iter(ordinals))) if len(ordinals) == 1 else ""
    ordinal = unique_aliases.get(_citation_alias_key(value))
    return str(ordinal) if ordinal else None


def normalize_citation_aliases(text: str, evidence) -> str:
    """Compile verified typed/named aliases into the ordinal citation grammar.

    Arbitrary bracketed prose is not a citation merely because it is adjacent to one.
    Priority tags, checklist markers, and domain notation must remain literal unless the
    content is a typed source marker or resolves to one unique runtime-verified source.
    """
    unique_aliases, identity_ordinals = _citation_alias_indexes(evidence)
    value = normalize_citation_wrappers(text)

    def replace(match: re.Match) -> str:
        content = match.group(1).strip()
        resolved = _resolved_citation_alias(content, unique_aliases, identity_ordinals)
        if resolved and re.fullmatch(_CITATION_CONTENT_PATTERN, content, re.I):
            return match.group(0)
        if resolved:
            return f"[{resolved}]"
        if resolved == "":
            return "(근거 확인 필요)"
        return match.group(0)

    return _CITATION_ALIAS_GROUP_RE.sub(replace, value)


def citation_tokens(value: str) -> tuple[str, ...]:
    """Parse one content group or an adjacent citation run in display order."""
    raw = str(value or "")
    groups = [match.group(1) for match in CITATION_GROUP_RE.finditer(raw)]
    contents = groups or [raw]
    return tuple(
        token.strip().casefold()
        for content in contents
        for token in content.split(",")
        if re.fullmatch(CITATION_TOKEN_PATTERN, token.strip(), re.I)
    )


def citation_occurrence_id(claim_material: str, tokens: tuple[str, ...], index: int) -> str:
    """Return a join key stable across source-tail movement and invalid neighbor tokens."""
    material = "\x1f".join((
        " ".join(str(claim_material or "").split()).casefold(),
        ",".join(tokens), str(int(index)),
    ))
    return f"citation:{sha256(material.encode('utf-8')).hexdigest()[:20]}"


def citation_occurrences(value: str):
    """Yield ``(occurrence_id, match, tokens)`` from the single canonical parser."""
    counts: dict[tuple[str, tuple[str, ...]], int] = {}
    for match in CITATION_OCCURRENCE_RE.finditer(str(value or "")):
        claim_text = _claim_text_at(str(value or ""), match.start(), match.end())
        claim_material = " ".join(CITATION_GROUP_RE.sub("", claim_text).split()).strip()
        tokens = citation_tokens(match.group(0))
        key = (claim_material.casefold(), tokens)
        counts[key] = counts.get(key, 0) + 1
        yield (
            citation_occurrence_id(claim_material, tokens, counts[key]),
            match,
            tokens,
        )


def citation_claim_span(value: str, start: int, end: int) -> tuple[int, int]:
    """Return the bounded sentence/line span owned by one citation occurrence."""
    text = str(value or "")
    prior = list(_CLAIM_SENTENCE_BOUNDARY_RE.finditer(text, 0, max(0, start)))
    following = _CLAIM_SENTENCE_BOUNDARY_RE.search(text, max(0, end))
    left = prior[-1].end() if prior else 0
    right = following.end() if following else len(text)
    return left, right


def _claim_text_at(value: str, start: int, end: int) -> str:
    span_start, span_end = citation_claim_span(value, start, end)
    return " ".join(value[span_start:span_end].split()).strip()


def _without_negated_completion(value: str) -> str:
    return _NEGATED_COMPLETION_RE.sub(" ", str(value or ""))


def _is_completion_claim(value: str) -> bool:
    return bool(_COMPLETION_ASSERTION_RE.search(_without_negated_completion(value)))


def _observation_supports_completion(observation: dict) -> bool:
    """Accept completion only from the canonical typed semantic contract."""
    if not observation.get("direct", False):
        return False
    if str(observation.get("temporal_role") or "observed").casefold() in {
            "historical", "conflict", "unresolved"}:
        return False
    kind = str(observation.get("claim_kind") or "").strip().casefold()
    value = str(observation.get("value") or "").strip().casefold()
    return bool(
        observation.get("subject_id") and observation.get("predicate")
        and kind == "completion" and value in _COMPLETION_VALUES
    )


def _claim_words(value: str) -> set[str]:
    return {
        word.casefold().strip("._:-") for word in _CLAIM_WORD_RE.findall(str(value or ""))
        if word.casefold().strip("._:-") not in _CLAIM_STOP
    }


def _typed_observation_fact(raw: dict) -> dict:
    """Validate a server-owned observation overlay; model payload fields are ignored."""
    if not isinstance(raw, dict) or raw.get("authority") != "materialized_match":
        return {}
    required = {
        key: str(raw.get(key) or "").strip()
        for key in (
            "observation_id", "source_id", "subject_id", "predicate", "value",
            "claim_kind", "observed_at", "temporal_role",
        )
    }
    if (not raw.get("direct") or not all(required[key] for key in (
            "observation_id", "source_id", "subject_id", "predicate", "value",
            "claim_kind", "temporal_role"))):
        return {}
    return {**required, "direct": True, "authority": "materialized_match",
            "normalized_text": str(raw.get("normalized_text") or "").strip()}


def _typed_observation(source_id: str, ordinal: int, observation: dict,
                       observation_id: str, fact: dict) -> ProvenanceObservation:
    source = str(observation.get("source") or "").strip()
    text = str(observation.get("text") or "").strip()
    trusted = fact if fact.get("source_id") == source_id else {}
    # No payload location or model-supplied flag implies authority. A canonical overlay is
    # the sole semantic source; raw observation fields remain display-only evidence.
    return ProvenanceObservation(
        observation_id=observation_id,
        source_id=source_id,
        ordinal=ordinal,
        source=source,
        text=text,
        observed_at=str(trusted.get("observed_at") or "").strip(),
        normalized_text=str(
            trusted.get("normalized_text") or observation.get("normalized_text")
            or observation.get("canonical_text") or text
        ).strip(),
        subject_id=str(trusted.get("subject_id") or "").strip(),
        predicate=str(trusted.get("predicate") or "").strip(),
        value=str(trusted.get("value") or "").strip(),
        claim_kind=str(trusted.get("claim_kind") or "").strip().casefold(),
        temporal_role=str(trusted.get("temporal_role") or "untyped").strip().casefold(),
        direct=trusted.get("direct") is True,
        authority=str(trusted.get("authority") or "").strip(),
    )


def _typed_claim_fact(raw: dict) -> dict:
    """Validate the product-owned claim interface; incomplete hints carry no authority."""
    if (not isinstance(raw, dict) or raw.get("direct") is not True
            or raw.get("authority") != "result_claim_sidecar"):
        return {}
    subject = str(raw.get("subject_id") or "").strip()
    predicate = str(raw.get("predicate") or "").strip()
    value = str(raw.get("value") or "").strip().casefold()
    kind = str(raw.get("claim_kind") or "").strip().casefold()
    try:
        citation_index = int(raw.get("citation_index") or 0)
    except (TypeError, ValueError):
        citation_index = 0
    if not (citation_index > 0 and subject and predicate and value and kind):
        return {}
    return {"citation_index": citation_index, "subject_id": subject,
            "predicate": predicate, "value": value, "claim_kind": kind}


def build_claim_provenance_graph(text: str, evidence, *, claim_facts=(),
                                 observation_facts=()) -> ClaimProvenanceGraph:
    """Bind every structured ordinal citation before display numbering can change."""
    bound = bind_evidence_provenance(evidence)
    sources: list[ProvenanceSource] = []
    observations: list[ProvenanceObservation] = []
    by_ordinal: dict[int, tuple[ProvenanceSource, list[ProvenanceObservation]]] = {}
    observation_ids_seen: set[str] = set()
    typed_observation_by_id = {
        row["observation_id"]: row for row in (
            _typed_observation_fact(raw) for raw in (observation_facts or [])
        ) if row
    }
    for source_ordinal, item in enumerate(bound, 1):
        sidecar = item.get("_provenance") or {}
        observation_ids = {
            int(row.get("ordinal") or 0): str(row.get("observation_id") or "")
            for row in (sidecar.get("observations") or []) if isinstance(row, dict)
        }
        source = ProvenanceSource(
            source_id=item["_source_id"], ordinal=source_ordinal,
            source_class=item["_source_class"],
            internal_readiness_authority=bool(item["_internal_readiness_authority"]),
        )
        source_observations: list[ProvenanceObservation] = []
        for observation_ordinal, observation in enumerate(item.get("observations") or [], 1):
            if not isinstance(observation, dict):
                continue
            row = _typed_observation(
                source["source_id"], observation_ordinal, observation,
                (observation_ids.get(observation_ordinal)
                 or _observation_id(source["source_id"], observation)),
                typed_observation_by_id.get(
                    observation_ids.get(observation_ordinal)
                    or _observation_id(source["source_id"], observation), {},
                ),
            )
            if row["observation_id"] not in observation_ids_seen:
                observation_ids_seen.add(row["observation_id"])
                observations.append(row)
            source_observations.append(row)
        sources.append(source)
        by_ordinal[source_ordinal] = (source, source_observations)

    value = normalize_citation_wrappers(text)
    claims: list[ClaimBinding] = []
    unbound: list[str] = []
    unsupported: list[str] = []
    claim_text_by_id: dict[str, str] = {}
    typed_claim_by_index = {
        row["citation_index"]: row for row in (
            _typed_claim_fact(raw) for raw in (claim_facts or [])
        ) if row
    }
    typed_claim_by_id: dict[str, dict] = {}
    occurrences: dict[str, int] = {}
    source_scope_ids: set[str] = set()
    for citation_index, (occurrence_id, match, tokens) in enumerate(
            citation_occurrences(value), 1):
        claim_text = _claim_text_at(value, match.start(), match.end())
        # Display ordinals are mutable aliases.  They must not participate in claim identity,
        # otherwise a source dedupe from [2-b] to [1-b] creates a different logical claim.
        claim_material = " ".join(CITATION_GROUP_RE.sub("", claim_text).split()).strip()
        base = sha256(claim_material.encode("utf-8")).hexdigest()[:16]
        for token_index, token in enumerate(tokens, 1):
            source_part, _, observation_part = token.partition("-")
            source_ordinal = int(source_part)
            observation_ordinal = (
                ord(observation_part.casefold()) - 96 if observation_part else 0
            )
            occurrences[base] = occurrences.get(base, 0) + 1
            claim_id = f"claim:{base}:{occurrences[base]}"
            claim_text_by_id[claim_id] = claim_text
            if citation_index in typed_claim_by_index:
                typed_claim_by_id[claim_id] = typed_claim_by_index[citation_index]
            common = {
                "claim_id": claim_id,
                "source_ordinal": source_ordinal,
                "observation_ordinal": observation_ordinal,
                "citation_occurrence_id": occurrence_id,
                "citation_token": token,
                "citation_token_index": token_index,
            }
            target = by_ordinal.get(source_ordinal)
            if not target:
                unbound.append(claim_id)
                claims.append(ClaimBinding(
                    **common, source_id="", observation_id="", entailment="unbound",
                ))
                continue
            source, source_observations = target
            observation_id = ""
            if observation_ordinal:
                if observation_ordinal > len(source_observations):
                    unbound.append(claim_id)
                    claims.append(ClaimBinding(
                        **common, source_id=source["source_id"], observation_id="",
                        entailment="unbound",
                    ))
                    continue
                observation_id = source_observations[observation_ordinal - 1]["observation_id"]
            elif len(source_observations) == 1:
                observation_id = source_observations[0]["observation_id"]
            else:
                # ``[n]`` cites the source as a whole rather than pretending that one
                # arbitrary child observation was selected.
                observation_id = f"{source['source_id']}#observation:source-scope"
                if observation_id not in source_scope_ids:
                    source_scope_ids.add(observation_id)
                    observations.append(ProvenanceObservation(
                        observation_id=observation_id, source_id=source["source_id"],
                        ordinal=0, source="source_scope", text="", observed_at="",
                    ))
            claims.append(ClaimBinding(
                **common, source_id=source["source_id"], observation_id=observation_id,
                entailment="unvalidated",
            ))

    source_by_id = {row["source_id"]: row for row in sources}
    observation_by_id = {row["observation_id"]: row for row in observations}
    candidate_rows: list[ProvenanceObservation] = []
    candidate_ids: set[str] = set()
    for _source, rows in by_ordinal.values():
        for row in rows:
            if row["observation_id"] in candidate_ids:
                continue
            candidate_ids.add(row["observation_id"])
            candidate_rows.append(row)

    for claim in claims:
        claim_text = claim_text_by_id.get(claim["claim_id"], "")
        typed_claim = typed_claim_by_id.get(claim["claim_id"], {})
        is_completion = (
            typed_claim.get("claim_kind") == "completion"
            and typed_claim.get("value") in _COMPLETION_VALUES
        ) if typed_claim else _is_completion_claim(claim_text)
        if not is_completion:
            claim["entailment"] = "not_applicable"
            continue
        current = observation_by_id.get(claim["observation_id"])
        current_source = source_by_id.get(claim["source_id"])
        words = _claim_words(claim_text)
        current_overlap = len(words & _claim_words((current or {}).get("text", "")))
        if (current and current_source
                and current_source.get("internal_readiness_authority")
                and _observation_supports_completion(current)
                and (typed_claim or current_overlap >= 2)):
            claim["entailment"] = "direct"
            continue

        expected_subject = str(
            typed_claim.get("subject_id") or (current or {}).get("subject_id") or "")
        expected_predicate = str(
            typed_claim.get("predicate") or (current or {}).get("predicate") or "")
        ranked = []
        for candidate in candidate_rows:
            source_node = source_by_id.get(candidate["source_id"])
            if not source_node or not source_node.get("internal_readiness_authority"):
                continue
            if not _observation_supports_completion(candidate):
                continue
            if (expected_subject and candidate.get("subject_id") != expected_subject) \
                    or (expected_predicate and candidate.get("predicate") != expected_predicate):
                continue
            candidate_words = _claim_words(candidate.get("text", ""))
            overlap = len(words & candidate_words)
            same_source = candidate["source_id"] == claim["source_id"]
            exact_relation = bool(
                expected_subject and expected_predicate
                and candidate.get("subject_id") == expected_subject
                and candidate.get("predicate") == expected_predicate
            )
            # Without a typed claim identity, exact relation copied from a bad citation is
            # not enough: actor/name swaps often retain the same generic predicate. Require
            # two distinctive shared terms. A product-owned typed claim may match by exact
            # subject+predicate without reparsing its display prose.
            if typed_claim:
                if not exact_relation:
                    continue
            elif overlap < 2:
                continue
            ranked.append((
                1 if exact_relation else 0,
                1 if candidate["source_id"].startswith("ticket:") else 0,
                overlap,
                1 if same_source else 0,
                str(candidate.get("observed_at") or ""),
                candidate,
            ))
        if not ranked:
            claim["entailment"] = "unsupported"
            unsupported.append(claim["claim_id"])
            continue
        ranked.sort(key=lambda row: row[:-1], reverse=True)
        # Equal authority/identity/overlap/recency means there is no unique best source.
        if len(ranked) > 1 and ranked[0][:-1] == ranked[1][:-1]:
            claim["entailment"] = "unsupported"
            unsupported.append(claim["claim_id"])
            continue
        replacement = ranked[0][-1]
        target_source = source_by_id[replacement["source_id"]]
        claim.update(
            source_id=replacement["source_id"],
            observation_id=replacement["observation_id"],
            source_ordinal=target_source["ordinal"],
            observation_ordinal=replacement["ordinal"],
            entailment="rebound",
        )
    return ClaimProvenanceGraph(
        sources=sources, observations=observations, claims=claims,
        unbound_claim_ids=unbound, unsupported_claim_ids=unsupported,
    )


__all__ = [
    "CITATION_GROUP_RE", "CITATION_OCCURRENCE_RE", "CITATION_TOKEN_PATTERN",
    "EVIDENCE_HEADING_RE", "EVIDENCE_SECTION_LABEL_PATTERN", "ClaimBinding",
    "ClaimProvenanceGraph", "ProvenanceObservation",
    "ProvenanceSource", "bind_evidence_provenance",
    "build_claim_provenance_graph", "citation_occurrence_id",
    "citation_claim_span", "citation_occurrences", "citation_tokens", "evidence_source_id",
    "normalize_citation_aliases", "normalize_citation_wrappers",
]
