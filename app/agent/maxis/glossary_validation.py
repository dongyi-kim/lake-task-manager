"""Offline structure/provenance validation; never ingest, resolve, or authorize terms.

Diagnostics contain counts, fixed codes and schema-owned JSON paths only. Source
locators and glossary values are never printed or used for network/file resolution.
The supplied file is the only input read besides the bundled public JSON Schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import unicodedata

from jsonschema import Draft202012Validator, FormatChecker


MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_STRUCTURE_NODES = 50_000
MAX_STRUCTURE_DEPTH = 24
MAX_DIAGNOSTICS = 100
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "deploy/maxis-dify/contracts/glossary.json"
_PATH_FIELDS = frozenset({
    "schemaVersion", "glossaryId", "version", "classification", "asOf", "sources",
    "terms", "id", "repository", "revision", "path", "section", "observedAt",
    "name", "aliases", "scope", "definition", "status", "sourceRefs",
    "ownerModule", "notEquivalentTo", "caveats",
})


@dataclass(frozen=True)
class Issue:
    code: str
    path: str


@dataclass(frozen=True)
class ValidationReport:
    source_count: int = 0
    term_count: int = 0
    errors: tuple[Issue, ...] = ()
    warnings: tuple[Issue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "sourceCount": self.source_count, "termCount": self.term_count,
            "errors": [{"code": row.code, "path": row.path} for row in self.errors],
            "warnings": [{"code": row.code, "path": row.path} for row in self.warnings],
        }


def _failure(code: str) -> ValidationReport:
    return ValidationReport(errors=(Issue(code, "$"),))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(_value):
    raise ValueError("nonfinite_json_number")


def _strict_json(value: str):
    return json.loads(value, object_pairs_hook=_pairs, parse_constant=_nonfinite)


@lru_cache(maxsize=1)
def _schema_validator() -> Draft202012Validator:
    schema = _strict_json(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _bounded_structure(document) -> bool:
    pending = [(document, 0)]
    visited = 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if visited > MAX_STRUCTURE_NODES or depth > MAX_STRUCTURE_DEPTH:
            return False
        if isinstance(value, (dict, list)):
            children = value.values() if isinstance(value, dict) else value
            if len(children) > MAX_STRUCTURE_NODES - visited:
                return False
            pending.extend((child, depth + 1) for child in children)
    return True


def _safe_path(parts) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int) and not isinstance(part, bool):
            path += f"[{part}]"
        else:
            path += "." + (part if isinstance(part, str) and part in _PATH_FIELDS else "unknown")
    return path


def _identity_errors(document) -> list[Issue]:
    errors = []
    for collection, code in (("sources", "duplicate_source_id"), ("terms", "duplicate_term_id")):
        seen = set()
        for index, row in enumerate(document[collection]):
            if row["id"] in seen:
                errors.append(Issue(code, f"$.{collection}[{index}].id"))
            seen.add(row["id"])
    source_ids = {row["id"] for row in document["sources"]}
    term_ids = {row["id"] for row in document["terms"]}
    for index, term in enumerate(document["terms"]):
        for position, ref in enumerate(term["sourceRefs"]):
            if ref not in source_ids:
                errors.append(Issue("source_reference_unknown", f"$.terms[{index}].sourceRefs[{position}]"))
        for position, ref in enumerate(term["notEquivalentTo"]):
            code = "self_non_equivalence" if ref == term["id"] else "term_reference_unknown" if ref not in term_ids else ""
            if code:
                errors.append(Issue(code, f"$.terms[{index}].notEquivalentTo[{position}]"))
    if document["classification"] == "public":
        for index, row in enumerate(document["sources"]):
            if row["classification"] == "internal":
                errors.append(Issue("public_contains_internal_source", f"$.sources[{index}].classification"))
    return errors


def normalize_label(label: str) -> str:
    """Normalize a validated display label, not a code/entity/authorization ID."""
    return " ".join(unicodedata.normalize("NFKC", label).casefold().split())


def _alias_warnings(terms) -> list[Issue]:
    """Compare display labels only; do not select a winner or build a resolver."""
    seen: dict[str, set[str]] = {}
    warnings = []
    for index, term in enumerate(terms):
        labels = [(term["name"], f"$.terms[{index}].name")]
        labels.extend((label, f"$.terms[{index}].aliases[{position}]") for position, label in enumerate(term["aliases"]))
        for label, path in labels:
            normalized = normalize_label(label)
            owners = seen.setdefault(normalized, set())
            if owners - {term["id"]} and len(warnings) < MAX_DIAGNOSTICS:
                warnings.append(Issue("alias_collision", path))
            owners.add(term["id"])
    return warnings


def validate_document(document) -> ValidationReport:
    """Validate detached JSON data without mutation, lookups, or content diagnostics."""
    if not _bounded_structure(document):
        return _failure("structure_limit")
    sources = document.get("sources") if isinstance(document, dict) else None
    terms = document.get("terms") if isinstance(document, dict) else None
    counts = {
        "source_count": len(sources) if isinstance(sources, list) else 0,
        "term_count": len(terms) if isinstance(terms, list) else 0,
    }
    errors = []
    for error in _schema_validator().iter_errors(document):
        errors.append(Issue("schema_" + str(error.validator), _safe_path(error.absolute_path)))
        if len(errors) >= MAX_DIAGNOSTICS:
            break
    if errors:
        return ValidationReport(**counts, errors=tuple(errors))
    errors = _identity_errors(document)
    warnings = _alias_warnings(terms)
    return ValidationReport(**counts, errors=tuple(errors[:MAX_DIAGNOSTICS]), warnings=tuple(warnings))


def validate_file(path: str | Path) -> ValidationReport:
    """Read at most a bounded local UTF-8 file; no source locator is opened."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
    except (OSError, ValueError):
        return _failure("input_unreadable")
    if len(raw) > MAX_INPUT_BYTES:
        return _failure("input_too_large")
    try:
        document = _strict_json(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        return _failure("input_json_invalid")
    return validate_document(document)
