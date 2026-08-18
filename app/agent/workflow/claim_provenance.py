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


class ProvenanceObservation(TypedDict):
    observation_id: str
    source_id: str
    ordinal: int
    source: str
    text: str
    observed_at: str


class ClaimBinding(TypedDict):
    claim_id: str
    source_id: str
    observation_id: str
    source_ordinal: int
    observation_ordinal: int


class ClaimProvenanceGraph(TypedDict):
    sources: list[ProvenanceSource]
    observations: list[ProvenanceObservation]
    claims: list[ClaimBinding]
    unbound_claim_ids: list[str]


_TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$", re.I)
_CITATION_RE = re.compile(r"\[\[?(\d+)(?:-([a-z]))?\]?\](?!\()", re.I)


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
    material = "\x1f".join(str(observation.get(field) or "").strip() for field in (
        "source", "text", "observed_at",
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
    """Collapse the common model spelling ``[[1-b]]`` to canonical ``[1-b]``."""
    return re.sub(r"\[\[(\d+(?:-[a-z])?)\]\]", r"[\1]", str(text or ""), flags=re.I)


def _claim_text_at(value: str, start: int, end: int) -> str:
    left = max(value.rfind("\n", 0, start), value.rfind(".", 0, start),
               value.rfind("!", 0, start), value.rfind("?", 0, start))
    right_candidates = [position for position in (
        value.find("\n", end), value.find(".", end), value.find("!", end),
        value.find("?", end),
    ) if position >= 0]
    right = min(right_candidates) + 1 if right_candidates else len(value)
    return " ".join(value[left + 1:right].split()).strip()


def build_claim_provenance_graph(text: str, evidence) -> ClaimProvenanceGraph:
    """Bind every structured ordinal citation before display numbering can change."""
    bound = bind_evidence_provenance(evidence)
    sources: list[ProvenanceSource] = []
    observations: list[ProvenanceObservation] = []
    by_ordinal: dict[int, tuple[ProvenanceSource, list[ProvenanceObservation]]] = {}
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
            row = ProvenanceObservation(
                observation_id=(observation_ids.get(observation_ordinal)
                                or _observation_id(source["source_id"], observation)),
                source_id=source["source_id"], ordinal=observation_ordinal,
                source=str(observation.get("source") or "").strip(),
                text=str(observation.get("text") or "").strip(),
                observed_at=str(observation.get("observed_at") or "").strip(),
            )
            observations.append(row)
            source_observations.append(row)
        sources.append(source)
        by_ordinal[source_ordinal] = (source, source_observations)

    value = normalize_citation_wrappers(text)
    claims: list[ClaimBinding] = []
    unbound: list[str] = []
    occurrences: dict[str, int] = {}
    source_scope_ids: set[str] = set()
    for match in _CITATION_RE.finditer(value):
        source_ordinal = int(match.group(1))
        observation_ordinal = (ord(match.group(2).casefold()) - 96) if match.group(2) else 0
        claim_text = _claim_text_at(value, match.start(), match.end())
        # Display ordinals are mutable aliases.  They must not participate in claim identity,
        # otherwise a source dedupe from [2-b] to [1-b] creates a different logical claim.
        claim_material = " ".join(_CITATION_RE.sub("", claim_text).split()).strip()
        base = sha256(claim_material.encode("utf-8")).hexdigest()[:16]
        occurrences[base] = occurrences.get(base, 0) + 1
        claim_id = f"claim:{base}:{occurrences[base]}"
        target = by_ordinal.get(source_ordinal)
        if not target:
            unbound.append(claim_id)
            continue
        source, source_observations = target
        observation_id = ""
        if observation_ordinal:
            if observation_ordinal > len(source_observations):
                unbound.append(claim_id)
                continue
            observation_id = source_observations[observation_ordinal - 1]["observation_id"]
        elif not observation_ordinal and len(source_observations) == 1:
            observation_id = source_observations[0]["observation_id"]
        else:
            # ``[n]`` cites the source as a whole rather than pretending that one arbitrary
            # child observation was selected.  Model that scope explicitly so the typed
            # claim chain never terminates in an empty observation id.
            observation_id = f"{source['source_id']}#observation:source-scope"
            if observation_id not in source_scope_ids:
                source_scope_ids.add(observation_id)
                observations.append(ProvenanceObservation(
                    observation_id=observation_id, source_id=source["source_id"],
                    ordinal=0, source="source_scope", text="", observed_at="",
                ))
        claims.append(ClaimBinding(
            claim_id=claim_id, source_id=source["source_id"],
            observation_id=observation_id, source_ordinal=source_ordinal,
            observation_ordinal=observation_ordinal,
        ))
    return ClaimProvenanceGraph(
        sources=sources, observations=observations, claims=claims,
        unbound_claim_ids=unbound,
    )


__all__ = [
    "ClaimBinding", "ClaimProvenanceGraph", "ProvenanceObservation",
    "ProvenanceSource", "bind_evidence_provenance",
    "build_claim_provenance_graph", "evidence_source_id",
    "normalize_citation_wrappers",
]
