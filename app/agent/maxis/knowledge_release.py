"""Offline, immutable Knowledge candidates and current-authority read admission.

No upload, dataset mutation, profile activation, or source-locator resolution occurs.
An AccessContext must come from LTM's current authorization service, never an LLM
or remote retrieval response. Candidate integrity does not prove authorization.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.maxis.glossary_lookup import GlossarySnapshot, lookup, prepare_snapshot
from app.agent.maxis.glossary_validation import MAX_INPUT_BYTES, _strict_json
from app.agent.maxis.knowledge_manifest import canonical_dataset_id, _normalized_markdown
from app.agent.maxis.public_metadata import require_safe_public_text
from app.agent.workflow.role_manifest import ROLE_SPECS

Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Label = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")]


class KnowledgeReleaseError(ValueError):
    """Fixed error codes, without potentially private source values."""


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: Id
    corpus: Literal["policy", "glossary", "design"]
    path: Annotated[str, Field(min_length=1, max_length=512)]
    revision: Label
    sha256: Digest
    owner: Id
    classification: Literal["public", "internal"]
    audiences: Annotated[tuple[Id, ...], Field(min_length=1, max_length=64)]
    allowed_roles: Annotated[tuple[Id, ...], Field(min_length=1, max_length=64)]
    reviewed_for_version: Label
    reviewed_at: datetime
    reviewer_digest: Digest
    valid_until: datetime

    @model_validator(mode="after")
    def validate_metadata(self):
        for value in (self.id, self.revision, self.owner, self.reviewed_for_version,
                      *self.audiences, *self.allowed_roles):
            require_safe_public_text(value, "knowledge label")
        if len(set(self.audiences)) != len(self.audiences) or \
                len(set(self.allowed_roles)) != len(self.allowed_roles):
            raise ValueError("duplicate_access_label")
        if set(self.allowed_roles) - ROLE_SPECS.keys():
            raise ValueError("unknown_role")
        if self.reviewed_at.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("timezone_required")
        if self.valid_until <= self.reviewed_at:
            raise ValueError("invalid_validity")
        return self


class ReleaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["ltm-knowledge-candidate.v1"]
    release_id: Label
    dataset_id: str
    sources: Annotated[tuple[SourceSpec, ...], Field(min_length=1, max_length=256)]
    max_chunk_bytes: Annotated[int, Field(ge=256, le=65536)] = 8192

    @model_validator(mode="after")
    def validate_release(self):
        require_safe_public_text(self.release_id, "knowledge release")
        canonical_dataset_id(self.dataset_id)
        if self.dataset_id != canonical_dataset_id(self.dataset_id):
            raise ValueError("noncanonical_dataset_id")
        if len({source.id for source in self.sources}) != len(self.sources) or \
                len({source.path for source in self.sources}) != len(self.sources):
            raise ValueError("duplicate_source")
        if any(source.reviewed_for_version != self.release_id for source in self.sources):
            raise ValueError("release_review_mismatch")
        return self


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now(value: datetime | None) -> datetime:
    instant = value or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise KnowledgeReleaseError("timezone_required")
    return instant


def _check_fresh(source: SourceSpec, now: datetime, *, historical: bool = False) -> None:
    if source.reviewed_at > now or (not historical and (
            now >= source.valid_until or now - source.reviewed_at > timedelta(days=90))):
        raise KnowledgeReleaseError("source_review_not_current")


def _read_bounded(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise KnowledgeReleaseError("source_too_large")
        return raw
    except OSError:
        raise KnowledgeReleaseError("source_unreadable") from None


def _source_path(root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if "\\" in value or ":" in value or relative.is_absolute() or \
            any(part in ("", ".", "..") for part in value.split("/")):
        raise KnowledgeReleaseError("source_path_invalid")
    candidate = (root / value).resolve()
    if not candidate.is_relative_to(root):
        raise KnowledgeReleaseError("source_path_escape")
    return candidate


def whole_heading_sections(content: str) -> tuple[tuple[str, str], ...]:
    """Keep a whole heading definition; oversized sections fail, never truncate.

    Hierarchical heading context accompanies every section. Fenced code headings
    are data, including fences longer than their embedded examples.
    """
    text = _normalized_markdown(content)
    sections = []
    stack: list[tuple[int, str]] = []
    lines: list[str] = []
    heading = "(preamble)"
    fence_char, fence_size = "", 0
    for line in text.splitlines():
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            marker, rest = fence.groups()
            if not fence_char:
                fence_char, fence_size = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size and not rest.strip():
                fence_char, fence_size = "", 0
        match = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$", line) if not fence_char and not fence else None
        if match:
            if any(part.strip() for part in lines):
                sections.append((heading, "\n".join(lines).strip()))
            level, title = len(match[1]), match[2].strip()
            stack = [(depth, name) for depth, name in stack if depth < level]
            stack.append((level, title))
            heading = " / ".join(name for _, name in stack)
            lines = [line]
        else:
            lines.append(line)
    if any(part.strip() for part in lines):
        sections.append((heading, "\n".join(lines).strip()))
    if not sections:
        raise KnowledgeReleaseError("source_empty")
    return tuple(sections)


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    id: str
    source_id: str
    source_revision: str
    source_sha256: str
    corpus: str
    sha256: str
    heading: str = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class KnowledgeCandidate:
    spec: ReleaseSpec = field(repr=False)
    sha256: str
    chunks: tuple[KnowledgeChunk, ...] = field(repr=False)
    glossaries: tuple[tuple[str, GlossarySnapshot], ...] = field(repr=False)

    def metadata(self) -> dict:
        """Operator metadata excludes source paths, headings, terms and body text."""
        return _metadata(self.spec, self.sha256, len(self.chunks))


@dataclass(frozen=True, slots=True)
class KnowledgeHistoricalSnapshot:
    """Metadata-only comparison baseline; cannot be used for runtime admission."""

    spec: ReleaseSpec = field(repr=False)
    sha256: str
    chunk_count: int

    def metadata(self) -> dict:
        return _metadata(self.spec, self.sha256, self.chunk_count)


def _metadata(spec: ReleaseSpec, digest: str, chunk_count: int) -> dict:
    return {
        "contract": "ltm-knowledge-release-summary.v1",
        "releaseId": require_safe_public_text(spec.release_id, "knowledge release"),
        "sha256": digest, "datasetId": spec.dataset_id,
        "sourceCount": len(spec.sources), "chunkCount": chunk_count,
        "corpora": {kind: sum(row.corpus == kind for row in spec.sources)
                    for kind in ("policy", "glossary", "design")},
    }


def build_candidate(config_path: str | Path, source_root: str | Path, *,
                    now: datetime | None = None) -> KnowledgeCandidate:
    """Verify explicitly pinned local sources; never resolve upstream locators."""
    return _build_candidate(config_path, source_root, now=now, historical=False)


def historical_snapshot(config_path: str | Path, source_root: str | Path, *,
                        now: datetime | None = None) -> KnowledgeHistoricalSnapshot:
    """Verify old source hashes for review even when their reviews have expired."""
    old = _build_candidate(config_path, source_root, now=now, historical=True)
    return KnowledgeHistoricalSnapshot(old.spec, old.sha256, len(old.chunks))


def _build_candidate(config_path: str | Path, source_root: str | Path, *,
                     now: datetime | None, historical: bool) -> KnowledgeCandidate:
    instant = _now(now)
    raw = _read_bounded(Path(config_path))
    try:
        document = _strict_json(raw.decode("utf-8"))
        spec = ReleaseSpec.model_validate_json(_canonical(document))
    except Exception:
        raise KnowledgeReleaseError("release_spec_invalid") from None
    root = Path(source_root).resolve()
    chunks: list[KnowledgeChunk] = []
    glossaries = []
    for source in sorted(spec.sources, key=lambda row: row.id):
        _check_fresh(source, instant, historical=historical)
        content = _read_bounded(_source_path(root, source.path))
        if _sha(content) != source.sha256:
            raise KnowledgeReleaseError("source_digest_mismatch")
        try:
            if source.corpus == "glossary":
                snapshot = prepare_snapshot(content)
                glossary = _strict_json(content.decode("utf-8"))
                if source.classification == "public" and glossary["classification"] != "public":
                    raise KnowledgeReleaseError("source_classification_mismatch")
                glossaries.append((source.id, snapshot))
                sections = tuple((term["id"], _canonical({
                    "term": term,
                    "sources": [row for row in glossary["sources"] if row["id"] in term["sourceRefs"]],
                }).decode("utf-8")) for term in sorted(glossary["terms"], key=lambda row: row["id"]))
            else:
                sections = whole_heading_sections(content.decode("utf-8"))
        except KnowledgeReleaseError:
            raise
        except Exception:
            raise KnowledgeReleaseError("source_content_invalid") from None
        for ordinal, (heading, body) in enumerate(sections):
            text = body if source.corpus == "glossary" else f"{heading}\n\n{body}"
            if len(text.encode("utf-8")) > spec.max_chunk_bytes:
                raise KnowledgeReleaseError("whole_section_exceeds_budget")
            chunk_sha = _sha(text.encode("utf-8"))
            chunk_id = _sha(_canonical([source.id, source.revision, source.sha256, ordinal, chunk_sha]))
            chunks.append(KnowledgeChunk(chunk_id, source.id, source.revision, source.sha256,
                                         source.corpus, chunk_sha, heading, text))
    digest = _sha(_canonical({"spec": spec.model_dump(mode="json"),
                              "chunks": [(row.id, row.sha256) for row in chunks]}))
    return KnowledgeCandidate(spec, digest, tuple(chunks), tuple(glossaries))


@dataclass(frozen=True, slots=True)
class KnowledgeAccessContext:
    """Fresh trusted-local grants; not an authorization credential by itself."""

    release_sha256: str
    scope_digest: str
    authorization_revision: str
    audience: str
    role_id: str
    allowed_source_ids: frozenset[str]
    revoked_source_ids: frozenset[str]
    source_revisions: tuple[tuple[str, str, str], ...]
    checked_at: datetime
    expires_at: datetime


def _admit_source(candidate: KnowledgeCandidate, source_id: str,
                  access: KnowledgeAccessContext, now: datetime) -> SourceSpec:
    if type(candidate) is not KnowledgeCandidate:
        raise KnowledgeReleaseError("runtime_candidate_required")
    if access.release_sha256 != candidate.sha256:
        raise KnowledgeReleaseError("release_not_current")
    if not re.fullmatch(r"[0-9a-f]{64}", access.scope_digest) or not access.authorization_revision:
        raise KnowledgeReleaseError("access_context_invalid")
    if access.checked_at.tzinfo is None or access.expires_at.tzinfo is None or \
            not access.checked_at <= now < access.expires_at or \
            access.expires_at - access.checked_at > timedelta(minutes=5):
        raise KnowledgeReleaseError("access_context_expired")
    sources = {row.id: row for row in candidate.spec.sources}
    source = sources.get(source_id)
    if source is None or source_id in access.revoked_source_ids or \
            source_id not in access.allowed_source_ids:
        raise KnowledgeReleaseError("source_not_authorized")
    if access.audience not in source.audiences or access.role_id not in source.allowed_roles:
        raise KnowledgeReleaseError("source_not_authorized")
    revisions = {row[0]: row[1:] for row in access.source_revisions}
    if len(revisions) != len(access.source_revisions) or \
            revisions.get(source_id) != (source.revision, source.sha256):
        raise KnowledgeReleaseError("source_revision_not_current")
    _check_fresh(source, now)
    return source


def admit_chunk(candidate: KnowledgeCandidate, *, chunk_id: str, text: str,
                access: KnowledgeAccessContext, now: datetime | None = None) -> KnowledgeChunk:
    """Recheck at retrieval, model handoff and display; remote metadata grants nothing."""
    if type(candidate) is not KnowledgeCandidate:
        raise KnowledgeReleaseError("runtime_candidate_required")
    chunk = next((row for row in candidate.chunks if row.id == chunk_id), None)
    if chunk is None:
        raise KnowledgeReleaseError("chunk_unknown")
    _admit_source(candidate, chunk.source_id, access, _now(now))
    if type(text) is not str or text != chunk.text:
        raise KnowledgeReleaseError("chunk_content_mismatch")
    return chunk


def lookup_glossary(candidate: KnowledgeCandidate, source_id: str, label: str, *,
                    access: KnowledgeAccessContext, scope: str | None = None,
                    now: datetime | None = None) -> dict:
    """Exact/ambiguous lookup only after current release/source/access admission."""
    source = _admit_source(candidate, source_id, access, _now(now))
    if source.corpus != "glossary":
        raise KnowledgeReleaseError("source_not_glossary")
    return lookup(dict(candidate.glossaries)[source_id], label, scope=scope)


def cache_identity(candidate: KnowledgeCandidate, access: KnowledgeAccessContext) -> str:
    """A cache namespace, not permission to skip admit_chunk on a cache hit."""
    return _sha(_canonical([
        candidate.sha256, access.scope_digest, access.authorization_revision,
        access.audience, access.role_id, sorted(access.allowed_source_ids),
        sorted(access.revoked_source_ids), sorted(access.source_revisions),
    ]))


def plan_blue_green(current: KnowledgeCandidate | KnowledgeHistoricalSnapshot,
                    candidate: KnowledgeCandidate, *, now: datetime | None = None) -> dict:
    """Content-free review diff. Always dry-run; never supplies an activation token."""
    if type(candidate) is not KnowledgeCandidate:
        raise KnowledgeReleaseError("runtime_candidate_required")
    for source in candidate.spec.sources:
        _check_fresh(source, _now(now))
    old = {row.id: row.model_dump(mode="json") for row in current.spec.sources}
    new = {row.id: row.model_dump(mode="json") for row in candidate.spec.sources}
    for source_id in old.keys() | new.keys():
        require_safe_public_text(source_id, "knowledge source")
    blockers = []
    if current.spec.dataset_id == candidate.spec.dataset_id:
        blockers.append("new_dataset_required")
    if current.spec.release_id == candidate.spec.release_id:
        blockers.append("new_release_id_required")
    return {
        "contract": "ltm-knowledge-release-plan.v1", "dryRun": True,
        "current": current.metadata(), "candidate": candidate.metadata(),
        "diff": {
            "added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()),
            "changed": sorted(key for key in new.keys() & old.keys() if new[key] != old[key]),
        },
        "blockers": blockers,
        "activationAllowed": False,
        "requiredEvidence": ["current_access_check", "retrieval_quality", "acl_and_injection",
                             "server_chunk_parity", "data_retention", "human_release_review"],
    }
