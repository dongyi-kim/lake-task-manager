from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.maxis.knowledge_manifest import KnowledgeManifestError, build_manifest


DATASET_A = "11111111-1111-4111-8111-111111111111"
DATASET_B = "22222222-2222-4222-8222-222222222222"


def _write_manifest(root: Path, *, max_chars: int = 1200) -> Path:
    config = root / "config.yml"
    config.write_text(
        "contract: ltm-knowledge-source.v1\n"
        "chunking:\n"
        "  strategy: markdown-headings-then-length.v1\n"
        f"  max_chars: {max_chars}\n"
        "  overlap_chars: 120\n"
        "documents:\n"
        "  - knowledge/rules.md\n",
        encoding="utf-8",
    )
    return config


def test_manifest_binds_content_chunking_version_and_dataset(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    document = knowledge / "rules.md"
    document.write_text("# Rules\n\noriginal", encoding="utf-8")
    config = _write_manifest(tmp_path)

    first = build_manifest(
        knowledge_version="kb-1", dataset_id=DATASET_A,
        config_path=config, source_root=tmp_path,
    )
    document.write_text("# Rules\n\nchanged", encoding="utf-8")
    content_changed = build_manifest(
        knowledge_version="kb-1", dataset_id=DATASET_A,
        config_path=config, source_root=tmp_path,
    )
    dataset_changed = build_manifest(
        knowledge_version="kb-1", dataset_id=DATASET_B,
        config_path=config, source_root=tmp_path,
    )

    assert len(first.sha256) == 64
    assert first.sha256 != content_changed.sha256
    assert content_changed.sha256 != dataset_changed.sha256
    assert first.public_metadata()["documents"][0]["path"] == "knowledge/rules.md"


def test_manifest_rejects_chunk_policy_drift_and_path_escape(tmp_path):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "rules.md").write_text("rules", encoding="utf-8")
    with pytest.raises(KnowledgeManifestError, match="chunking"):
        build_manifest(
            knowledge_version="kb-1", dataset_id=DATASET_A,
            config_path=_write_manifest(tmp_path, max_chars=999), source_root=tmp_path,
        )

    outside = tmp_path.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    config = tmp_path / "escape.yml"
    config.write_text(
        "contract: ltm-knowledge-source.v1\n"
        "chunking:\n"
        "  strategy: markdown-headings-then-length.v1\n"
        "  max_chars: 1200\n"
        "  overlap_chars: 120\n"
        "documents:\n"
        "  - ../outside.md\n",
        encoding="utf-8",
    )
    with pytest.raises(KnowledgeManifestError, match="escapes"):
        build_manifest(
            knowledge_version="kb-1", dataset_id=DATASET_A,
            config_path=config, source_root=tmp_path,
        )


@pytest.mark.parametrize("dataset_id", ["", "not-a-uuid", "11111111-1111-1111-1111-111111111111"])
def test_manifest_requires_canonical_dataset_uuid(tmp_path, dataset_id):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "rules.md").write_text("rules", encoding="utf-8")
    with pytest.raises(KnowledgeManifestError, match="dataset UUID"):
        build_manifest(
            knowledge_version="kb-1", dataset_id=dataset_id,
            config_path=_write_manifest(tmp_path), source_root=tmp_path,
        )
