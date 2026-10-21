"""Explicit offline name lookup, not semantic resolution or access authorization.

Preparation reads the bundled schema, never source locators. Lookup uses only an
immutable prepared snapshot and returns detached, potentially private data. There
is deliberately no CLI, logging, ingestion, provider call, or runtime registration.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from app.agent.maxis.glossary_validation import MAX_INPUT_BYTES, _strict_json, normalize_label, validate_document


@dataclass(frozen=True, slots=True, repr=False)
class _Term:
    labels: frozenset[str]
    scope: str
    record: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False, init=False)
class GlossarySnapshot:
    """Construct with prepare_snapshot. Not a security or data-release capability."""

    _identity: str
    _terms: tuple[_Term, ...]
    _sources: tuple[tuple[str, str], ...]

    def __init__(self, *_args, **_kwargs):
        raise TypeError("use_prepare_snapshot")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def prepare_snapshot(raw: bytes) -> GlossarySnapshot:
    """Validate bounded UTF-8 JSON and detach it; only the bundled schema is read.

    Errors have fixed messages, never query/source values. A valid snapshot says
    nothing about source authenticity, current truth, consent, or dataset ACLs.
    """
    if type(raw) is not bytes or len(raw) > MAX_INPUT_BYTES:
        raise ValueError("invalid_glossary")
    try:
        document = _strict_json(raw.decode("utf-8"))
        canonical = _json(document).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ValueError("invalid_glossary") from None
    if len(canonical) > MAX_INPUT_BYTES:
        raise ValueError("invalid_glossary")
    try:
        report = validate_document(document)
    except Exception:
        raise ValueError("glossary_validation_unavailable") from None
    if not report.ok:
        raise ValueError("invalid_glossary")
    identity = {key: document[key] for key in ("schemaVersion", "glossaryId", "version", "classification", "asOf")}
    identity["contentSha256"] = hashlib.sha256(canonical).hexdigest()
    terms = tuple(
        _Term(
            frozenset(normalize_label(label) for label in [term["name"], *term["aliases"]]),
            normalize_label(term["scope"]), _json(term), tuple(term["sourceRefs"]),
        )
        for term in sorted(document["terms"], key=lambda row: row["id"])
    )
    sources = tuple((row["id"], _json(row)) for row in document["sources"])
    prepared = object.__new__(GlossarySnapshot)
    object.__setattr__(prepared, "_identity", _json(identity))
    object.__setattr__(prepared, "_terms", terms)
    object.__setattr__(prepared, "_sources", sources)
    return prepared


def _error(status: str, code: str) -> dict:
    return {"status": status, "code": code, "candidates": []}


def _valid_label(value, limit: int) -> bool:
    if type(value) is not str or not 1 <= len(value) <= limit or not normalize_label(value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def lookup(
    snapshot: GlossarySnapshot, label: str, *, scope: str | None = None,
    max_candidates: int = 16, max_bytes: int = 65536,
) -> dict:
    """Match full normalized names/aliases, optionally within an explicit scope.

    No fuzzy matching, priority winner, text replacement, ownership decision, or
    fallback after a scope miss. Counts cover all matches before display limits.
    The byte budget uses canonical UTF-8 JSON; whole candidate records either fit
    together or no data is returned. Returned content is data, never instructions.
    """
    if type(snapshot) is not GlossarySnapshot:
        return _error("invalid_request", "invalid_snapshot")
    if not _valid_label(label, 120):
        return _error("invalid_request", "invalid_label")
    if scope is not None and not _valid_label(scope, 240):
        return _error("invalid_request", "invalid_scope")
    if type(max_candidates) is not int or not 1 <= max_candidates <= 64:
        return _error("invalid_request", "invalid_candidate_limit")
    if type(max_bytes) is not int or not 256 <= max_bytes <= 65536:
        return _error("invalid_request", "invalid_byte_limit")
    normalized = normalize_label(label)
    scoped = None if scope is None else normalize_label(scope)
    matches = [term for term in snapshot._terms if normalized in term.labels and (scoped is None or scoped == term.scope)]
    sources = dict(snapshot._sources)
    result = {
        "status": "not_found" if not matches else "matched" if len(matches) == 1 else "ambiguous",
        "totalCandidates": len(matches), "truncated": len(matches) > max_candidates,
        "snapshot": json.loads(snapshot._identity),
        "candidates": [
            {"term": json.loads(term.record), "sources": [json.loads(sources[ref]) for ref in term.source_refs]}
            for term in matches[:max_candidates]
        ],
    }
    if len(_json(result).encode("utf-8")) > max_bytes:
        return _error("budget_exceeded", "whole_result_does_not_fit")
    return result
