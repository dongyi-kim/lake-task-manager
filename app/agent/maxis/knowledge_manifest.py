"""Deterministic MAXIS Knowledge release manifest.

The human-friendly knowledge version is not an integrity boundary by itself.  This
module binds it to the exact tracked documents, LTM chunking policy, and the selected
Dify dataset deployment.  The digest is safe to expose and is carried in every
EvidencePack; document contents and dataset credentials are not.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_DATASET_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)


class KnowledgeManifestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeManifest:
    knowledge_version: str
    dataset_id: str
    sha256: str
    document_count: int
    chunking: dict[str, Any]
    documents: tuple[dict[str, Any], ...]

    def public_metadata(self) -> dict[str, Any]:
        return {
            "contract": "ltm-knowledge-manifest.v1",
            "knowledgeVersion": self.knowledge_version,
            "datasetId": self.dataset_id,
            "sha256": self.sha256,
            "documentCount": self.document_count,
            "chunking": dict(self.chunking),
            "documents": [dict(row) for row in self.documents],
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


def build_manifest(
    *,
    knowledge_version: str,
    dataset_id: str,
    config_path: Path | str | None = None,
    source_root: Path | str | None = None,
) -> KnowledgeManifest:
    default_config, default_root = _defaults()
    config_file = Path(config_path) if config_path is not None else default_config
    root = (Path(source_root) if source_root is not None else default_root).resolve()
    version = str(knowledge_version or "").strip()
    dataset = canonical_dataset_id(dataset_id)
    if not _SAFE_LABEL.fullmatch(version):
        raise KnowledgeManifestError("A wire-safe knowledge version is required.")
    try:
        raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise KnowledgeManifestError("MAXIS knowledge source manifest cannot be read.") from exc
    if not isinstance(raw, dict) or raw.get("contract") != "ltm-knowledge-source.v1":
        raise KnowledgeManifestError("Unsupported MAXIS knowledge source manifest.")
    chunking = raw.get("chunking")
    if not isinstance(chunking, dict) or set(chunking) != {
        "strategy", "max_chars", "overlap_chars",
    }:
        raise KnowledgeManifestError("Knowledge chunking policy is incomplete.")
    expected_chunking = {
        "strategy": "markdown-headings-then-length.v1",
        "max_chars": 1200,
        "overlap_chars": 120,
    }
    if chunking != expected_chunking:
        raise KnowledgeManifestError("Knowledge chunking policy does not match LTM.")
    documents = raw.get("documents")
    if not isinstance(documents, list) or not documents:
        raise KnowledgeManifestError("At least one knowledge document is required.")
    if any(not isinstance(value, str) or not value.strip() for value in documents):
        raise KnowledgeManifestError("Knowledge document paths must be strings.")
    if len(set(documents)) != len(documents):
        raise KnowledgeManifestError("Knowledge document paths must be unique.")

    rows: list[dict[str, Any]] = []
    for relative in documents:
        normalized = relative.replace("\\", "/")
        candidate = (root / normalized).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise KnowledgeManifestError("Knowledge document escapes the source root.") from exc
        if not candidate.is_file() or candidate.suffix.lower() != ".md":
            raise KnowledgeManifestError(f"Knowledge document is missing: {normalized}")
        content = candidate.read_bytes()
        rows.append({
            "path": normalized,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        })

    material = {
        "contract": "ltm-knowledge-manifest.v1",
        "knowledgeVersion": version,
        "datasetId": dataset,
        "chunking": expected_chunking,
        "documents": rows,
    }
    digest = hashlib.sha256(_canonical(material)).hexdigest()
    return KnowledgeManifest(
        knowledge_version=version,
        dataset_id=dataset,
        sha256=digest,
        document_count=len(rows),
        chunking=expected_chunking,
        documents=tuple(rows),
    )


__all__ = [
    "KnowledgeManifest",
    "KnowledgeManifestError",
    "build_manifest",
    "canonical_dataset_id",
]
