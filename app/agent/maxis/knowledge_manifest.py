"""Deterministic, governed MAXIS Knowledge release manifest.

The human-friendly Knowledge version is not an integrity boundary by itself.  This
module binds it to the exact tracked documents, their classification/Role ACLs, the
review and immutable blue-green release policy, a deterministic segment ledger, and
the selected Dify dataset deployment.  Public metadata contains headings and hashes,
never segment body text or dataset credentials.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml


_SOURCE_CONTRACT = "ltm-knowledge-source.v2"
_MANIFEST_CONTRACT = "ltm-knowledge-manifest.v2"
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_SAFE_ID = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_UTC_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_DATASET_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_CLASSIFICATIONS = frozenset({"internal"})
_AUDIENCES = frozenset({"all_ltm_users"})
_CHUNKING = {
    "strategy": "markdown-headings-then-length.v1",
    "max_chars": 1200,
    "overlap_chars": 120,
}
_DOCUMENT_FIELDS = frozenset({
    "id", "path", "revision", "reviewed_for_release", "reviewed_for_version",
    "reviewed_at", "reviewer_digest", "owner",
    "classification", "audience", "allowed_roles",
})
_DOCUMENT_OPTIONAL_FIELDS = frozenset({"description", "sha256"})
_GOVERNANCE_FIELDS = frozenset({
    "review_cycle_days", "immutable_release", "deployment_strategy",
    "eval_suite_sha256",
})


class KnowledgeManifestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeManifest:
    knowledge_version: str
    dataset_id: str
    sha256: str
    document_count: int
    segment_count: int
    governance: dict[str, Any]
    chunking: dict[str, Any]
    role_registry_path: str
    registered_roles: tuple[str, ...]
    documents: tuple[dict[str, Any], ...]

    def public_metadata(self) -> dict[str, Any]:
        """Return release metadata and segment digests without document body text."""

        return {
            "contract": _MANIFEST_CONTRACT,
            "knowledgeVersion": self.knowledge_version,
            "datasetId": self.dataset_id,
            "sha256": self.sha256,
            "documentCount": self.document_count,
            "segmentCount": self.segment_count,
            "governance": dict(self.governance),
            "chunking": dict(self.chunking),
            "roleRegistry": {
                "path": self.role_registry_path,
                "roles": list(self.registered_roles),
            },
            "documents": [
                {**row, "segments": [dict(segment) for segment in row["segments"]]}
                for row in self.documents
            ],
        }


def _defaults() -> tuple[Path, Path]:
    from app.infra.settings import CONFIG_DIR, SRC_DIR

    return Path(CONFIG_DIR) / "maxis-knowledge.yml", Path(SRC_DIR)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_dataset_id(value: str) -> str:
    """Return one lowercase Dify dataset UUID or fail without coercing aliases."""

    dataset = str(value or "").strip().lower()
    if not _DATASET_ID.fullmatch(dataset):
        raise KnowledgeManifestError("A canonical Dify Knowledge dataset UUID is required.")
    return dataset


def _tracked_file(root: Path, relative: Any, label: str, *, suffix: str = "") -> tuple[str, Path]:
    if not isinstance(relative, str) or not relative.strip():
        raise KnowledgeManifestError(f"{label} must be a repository-relative path.")
    normalized = relative.strip().replace("\\", "/")
    if normalized.startswith("/") or "//" in normalized:
        raise KnowledgeManifestError(f"{label} must be a canonical repository-relative path.")
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise KnowledgeManifestError(f"{label} escapes the source root.") from exc
    if not candidate.is_file() or (suffix and candidate.suffix.lower() != suffix):
        raise KnowledgeManifestError(f"{label} is missing: {normalized}")
    return normalized, candidate


def _registered_roles(root: Path, registry_path: Any) -> tuple[str, tuple[str, ...]]:
    normalized, candidate = _tracked_file(
        root, registry_path, "Knowledge Role registry", suffix=".yml",
    )
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise KnowledgeManifestError("Knowledge Role registry cannot be read.") from exc
    workflows = raw.get("workflows") if isinstance(raw, dict) else None
    if not isinstance(workflows, dict) or not workflows:
        raise KnowledgeManifestError("Knowledge Role registry has no workflows.")
    roles: list[str] = []
    for workflow in workflows.values():
        rows = workflow.get("roles") if isinstance(workflow, dict) else None
        if not isinstance(rows, list) or not rows:
            raise KnowledgeManifestError("Knowledge Role registry has an invalid Role list.")
        for role in rows:
            if not isinstance(role, str) or not _SAFE_ID.fullmatch(role):
                raise KnowledgeManifestError("Knowledge Role registry contains an invalid Role.")
            roles.append(role)
    if len(roles) != len(set(roles)):
        raise KnowledgeManifestError("Knowledge Role registry contains duplicate Roles.")
    return normalized, tuple(sorted(roles))


def _governance(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _GOVERNANCE_FIELDS:
        raise KnowledgeManifestError("Knowledge governance policy is incomplete.")
    review_days = value.get("review_cycle_days")
    if isinstance(review_days, bool) or not isinstance(review_days, int) \
            or not 1 <= review_days <= 365:
        raise KnowledgeManifestError("Knowledge review_cycle_days must be between 1 and 365.")
    if value.get("immutable_release") is not True:
        raise KnowledgeManifestError("Knowledge releases must be immutable.")
    if value.get("deployment_strategy") != "blue_green":
        raise KnowledgeManifestError("Knowledge releases must use blue_green deployment.")
    eval_suite_sha256 = str(value.get("eval_suite_sha256") or "").strip()
    if not _DIGEST.fullmatch(eval_suite_sha256):
        raise KnowledgeManifestError(
            "Knowledge eval_suite_sha256 must be one lowercase SHA-256 digest."
        )
    return {
        "review_cycle_days": review_days,
        "immutable_release": True,
        "deployment_strategy": "blue_green",
        "eval_suite_sha256": eval_suite_sha256,
    }


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _review_timestamp(value: Any, *, review_cycle_days: int) -> str:
    raw = str(value or "").strip()
    if not _UTC_INSTANT.fullmatch(raw):
        raise KnowledgeManifestError(
            "Knowledge reviewed_at must be a canonical UTC ISO timestamp ending in Z."
        )
    try:
        instant = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise KnowledgeManifestError("Knowledge reviewed_at is invalid.") from exc
    now = _now_utc()
    if instant > now:
        raise KnowledgeManifestError("Knowledge reviewed_at cannot be in the future.")
    if now - instant > timedelta(days=review_cycle_days):
        raise KnowledgeManifestError(
            "Knowledge document review is older than review_cycle_days."
        )
    return raw


def _normalized_markdown(content: str) -> str:
    value = unicodedata.normalize("NFC", content.lstrip("\ufeff"))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in value.split("\n")).strip()


def _heading_sections(text: str) -> list[tuple[int, int, str]]:
    """Return half-open normalized character ranges grouped by Markdown headings."""

    headings: list[tuple[int, int, str]] = []
    stack: list[str] = []
    offset = 0
    fenced_by = ""
    for line in text.splitlines(keepends=True):
        visible = line.rstrip("\n")
        fence = _FENCE.match(visible)
        if fence:
            marker = fence.group(1)[0]
            if not fenced_by:
                fenced_by = marker
            elif marker == fenced_by:
                fenced_by = ""
        if not fenced_by and not fence:
            match = _HEADING.match(visible)
            if match:
                level = len(match.group(1))
                title = " ".join(match.group(2).split())
                if not title or len(title) > 500:
                    raise KnowledgeManifestError("Knowledge heading is empty or too long.")
                stack = stack[:level - 1]
                stack.extend([""] * max(0, level - 1 - len(stack)))
                stack.append(title)
                heading = " / ".join(part for part in stack if part)
                headings.append((offset, level, heading))
        offset += len(line)

    sections: list[tuple[int, int, str]] = []
    if not headings:
        return [(0, len(text), "(preamble)")] if text else []
    if headings[0][0] > 0:
        sections.append((0, headings[0][0], "(preamble)"))
    for index, (start, _level, heading) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        sections.append((start, end, heading))
    return sections


def _segment_ledger(document_id: str, content: str, chunking: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    normalized = _normalized_markdown(content)
    if not normalized:
        raise KnowledgeManifestError("Knowledge documents cannot be empty.")
    maximum = int(chunking["max_chars"])
    overlap = int(chunking["overlap_chars"])
    segments: list[dict[str, Any]] = []
    ordinal = 1
    for section_start, section_end, heading in _heading_sections(normalized):
        cursor = section_start
        while cursor < section_end:
            end = min(cursor + maximum, section_end)
            segment = normalized[cursor:end]
            if segment.strip():
                segments.append({
                    "document_id": document_id,
                    "heading": heading,
                    "ordinal": ordinal,
                    "normalized_text_sha256": hashlib.sha256(
                        segment.encode("utf-8")
                    ).hexdigest(),
                    "char_start": cursor,
                    "char_end": end,
                })
                ordinal += 1
            if end >= section_end:
                break
            cursor = end - overlap
    if not segments:
        raise KnowledgeManifestError("Knowledge document produced no segments.")
    return normalized, segments


def build_manifest(
    *,
    knowledge_version: str,
    dataset_id: str,
    config_path: Path | str | None = None,
    source_root: Path | str | None = None,
) -> KnowledgeManifest:
    default_config, default_root = (
        _defaults() if config_path is None or source_root is None else (None, None)
    )
    config_file = Path(config_path) if config_path is not None else default_config
    root = (Path(source_root) if source_root is not None else default_root).resolve()
    version = str(knowledge_version or "").strip()
    dataset = canonical_dataset_id(dataset_id)
    if not _SAFE_LABEL.fullmatch(version):
        raise KnowledgeManifestError("A wire-safe knowledge version is required.")
    try:
        raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise KnowledgeManifestError("MAXIS knowledge source manifest cannot be read.") from exc
    if not isinstance(raw, dict) or raw.get("contract") != _SOURCE_CONTRACT:
        raise KnowledgeManifestError("Unsupported MAXIS knowledge source manifest.")
    if set(raw) != {"contract", "role_registry", "governance", "chunking", "documents"}:
        raise KnowledgeManifestError("MAXIS knowledge source manifest fields are incomplete.")

    governance = _governance(raw.get("governance"))
    chunking = raw.get("chunking")
    if not isinstance(chunking, dict) or chunking != _CHUNKING:
        raise KnowledgeManifestError("Knowledge chunking policy does not match LTM.")
    registry_path, registered_roles = _registered_roles(root, raw.get("role_registry"))
    registered = set(registered_roles)

    documents = raw.get("documents")
    if not isinstance(documents, list) or not documents:
        raise KnowledgeManifestError("At least one knowledge document is required.")
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    paths: set[str] = set()
    for index, value in enumerate(documents):
        if (not isinstance(value, dict) or not _DOCUMENT_FIELDS.issubset(value)
                or set(value) - _DOCUMENT_FIELDS - _DOCUMENT_OPTIONAL_FIELDS):
            raise KnowledgeManifestError(
                f"Knowledge document {index + 1} governance fields are incomplete."
            )
        document_id = str(value.get("id") or "").strip()
        owner = str(value.get("owner") or "").strip()
        classification = str(value.get("classification") or "").strip()
        audience = str(value.get("audience") or "").strip()
        if not _SAFE_ID.fullmatch(document_id) or document_id in ids:
            raise KnowledgeManifestError("Knowledge document ids must be stable and unique.")
        revision = value.get("revision")
        revision_ok = (
            isinstance(revision, int) and not isinstance(revision, bool) and revision > 0
        ) or (
            isinstance(revision, str) and bool(_SAFE_LABEL.fullmatch(revision))
        )
        if not revision_ok:
            raise KnowledgeManifestError(
                "Knowledge document revision must be a positive integer or safe release label."
            )
        if value.get("reviewed_for_release") is not True:
            raise KnowledgeManifestError(
                "Every Knowledge document must be reviewed for the release."
            )
        reviewed_for_version = str(value.get("reviewed_for_version") or "").strip()
        if not _SAFE_LABEL.fullmatch(reviewed_for_version):
            raise KnowledgeManifestError(
                "Knowledge reviewed_for_version must be a wire-safe release label."
            )
        if reviewed_for_version != version:
            raise KnowledgeManifestError(
                "Every Knowledge document must be reviewed for the exact knowledge version."
            )
        reviewed_at = _review_timestamp(
            value.get("reviewed_at"),
            review_cycle_days=int(governance["review_cycle_days"]),
        )
        reviewer_digest = str(value.get("reviewer_digest") or "").strip()
        if not _DIGEST.fullmatch(reviewer_digest):
            raise KnowledgeManifestError(
                "Knowledge reviewer_digest must be one non-identifying lowercase SHA-256 digest."
            )
        if not _SAFE_ID.fullmatch(owner):
            raise KnowledgeManifestError("Knowledge document owner is invalid.")
        if classification not in _CLASSIFICATIONS:
            raise KnowledgeManifestError("Knowledge document classification is unregistered.")
        if audience not in _AUDIENCES:
            raise KnowledgeManifestError("Knowledge document audience is unregistered.")
        allowed_roles = value.get("allowed_roles")
        if not isinstance(allowed_roles, list) or not allowed_roles \
                or any(not isinstance(role, str) for role in allowed_roles):
            raise KnowledgeManifestError("Knowledge document allowed_roles must be non-empty.")
        if len(allowed_roles) != len(set(allowed_roles)):
            raise KnowledgeManifestError("Knowledge document allowed_roles must be unique.")
        unknown = set(allowed_roles) - registered
        if unknown:
            raise KnowledgeManifestError(
                "Knowledge document allowed_roles contains an unregistered Role."
            )

        normalized_path, candidate = _tracked_file(
            root, value.get("path"), "Knowledge document", suffix=".md",
        )
        if normalized_path in paths:
            raise KnowledgeManifestError("Knowledge document paths must be unique.")
        try:
            content_bytes = candidate.read_bytes()
            content_text = content_bytes.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise KnowledgeManifestError(
                f"Knowledge document cannot be read: {normalized_path}"
            ) from exc
        normalized_text, segments = _segment_ledger(
            document_id, content_text, _CHUNKING,
        )
        content_sha256 = hashlib.sha256(content_bytes).hexdigest()
        if "sha256" in value and value["sha256"] != content_sha256:
            raise KnowledgeManifestError("Knowledge document content SHA-256 does not match its source.")
        description = value.get("description")
        if description is not None and (
            not isinstance(description, str) or not description.strip()
            or len(description) > 500 or "\n" in description
        ):
            raise KnowledgeManifestError("Knowledge document description must be a concise single line.")
        rows.append({
            "id": document_id,
            "path": normalized_path,
            "revision": revision,
            "reviewed_for_release": True,
            "reviewed_for_version": reviewed_for_version,
            "reviewed_at": reviewed_at,
            "reviewer_digest": reviewer_digest,
            "owner": owner,
            "classification": classification,
            "audience": audience,
            "allowed_roles": list(allowed_roles),
            "bytes": len(content_bytes),
            "sha256": content_sha256,
            **({"description": description} if description is not None else {}),
            "normalized_chars": len(normalized_text),
            "segment_count": len(segments),
            "segments": segments,
        })
        ids.add(document_id)
        paths.add(normalized_path)

    role_registry = {"path": registry_path, "roles": list(registered_roles)}
    material = {
        "contract": _MANIFEST_CONTRACT,
        "knowledgeVersion": version,
        "datasetId": dataset,
        "governance": governance,
        "chunking": dict(_CHUNKING),
        "roleRegistry": role_registry,
        "documents": rows,
    }
    digest = hashlib.sha256(_canonical(material)).hexdigest()
    return KnowledgeManifest(
        knowledge_version=version,
        dataset_id=dataset,
        sha256=digest,
        document_count=len(rows),
        segment_count=sum(row["segment_count"] for row in rows),
        governance=governance,
        chunking=dict(_CHUNKING),
        role_registry_path=registry_path,
        registered_roles=registered_roles,
        documents=tuple(rows),
    )


__all__ = [
    "KnowledgeManifest",
    "KnowledgeManifestError",
    "build_manifest",
    "canonical_dataset_id",
]
