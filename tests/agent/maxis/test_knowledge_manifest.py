from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from app.agent.maxis.knowledge_manifest import KnowledgeManifestError, build_manifest


DATASET_A = "11111111-1111-4111-8111-111111111111"
DATASET_B = "22222222-2222-4222-8222-222222222222"


def _write_registry(root: Path) -> None:
    config = root / "config"
    config.mkdir(exist_ok=True)
    (config / "maxis-workflows.yml").write_text(
        yaml.safe_dump({
            "version": 1,
            "workflows": {
                "agent_core": {
                    "roles": ["knowledge_curator", "auditor", "editor_author"],
                },
            },
        }, sort_keys=False),
        encoding="utf-8",
    )


def _write_manifest(
    root: Path,
    *,
    max_chars: int = 1200,
    document_patch: dict | None = None,
    governance_patch: dict | None = None,
    reviewed_for_version: str = "kb-1",
) -> Path:
    _write_registry(root)
    document = {
        "id": "rules",
        "path": "knowledge/rules.md",
        "revision": 1,
        "reviewed_for_release": True,
        "reviewed_for_version": reviewed_for_version,
        "reviewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reviewer_digest": "d" * 64,
        "owner": "pmo-governance",
        "classification": "internal",
        "audience": "all_ltm_users",
        "allowed_roles": ["knowledge_curator", "auditor"],
    }
    document.update(document_patch or {})
    governance = {
        "review_cycle_days": 90,
        "immutable_release": True,
        "deployment_strategy": "blue_green",
        "eval_suite_sha256": "e" * 64,
    }
    governance.update(governance_patch or {})
    config = root / "config.yml"
    config.write_text(
        yaml.safe_dump({
            "contract": "ltm-knowledge-source.v2",
            "role_registry": "config/maxis-workflows.yml",
            "governance": governance,
            "chunking": {
                "strategy": "markdown-headings-then-length.v1",
                "max_chars": max_chars,
                "overlap_chars": 120,
            },
            "documents": [document],
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return config


def _build(root: Path, config: Path, *, version: str = "kb-1", dataset: str = DATASET_A):
    return build_manifest(
        knowledge_version=version,
        dataset_id=dataset,
        config_path=config,
        source_root=root,
    )


def test_manifest_binds_governance_content_segments_version_and_dataset(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    document = knowledge / "rules.md"
    document.write_text("# Rules\n\noriginal", encoding="utf-8")
    config = _write_manifest(tmp_path)

    first = _build(tmp_path, config)
    document.write_text("# Rules\n\nchanged", encoding="utf-8")
    content_changed = _build(tmp_path, config)
    dataset_changed = _build(tmp_path, config, dataset=DATASET_B)

    assert len(first.sha256) == 64
    assert first.sha256 != content_changed.sha256
    assert content_changed.sha256 != dataset_changed.sha256
    public = first.public_metadata()
    assert public["contract"] == "ltm-knowledge-manifest.v2"
    assert public["governance"] == {
        "review_cycle_days": 90,
        "immutable_release": True,
        "deployment_strategy": "blue_green",
        "eval_suite_sha256": "e" * 64,
    }
    row = public["documents"][0]
    assert row["id"] == "rules"
    assert row["path"] == "knowledge/rules.md"
    assert row["revision"] == 1
    assert row["reviewed_for_release"] is True
    assert row["reviewed_for_version"] == "kb-1"
    assert row["reviewer_digest"] == "d" * 64
    assert row["allowed_roles"] == ["knowledge_curator", "auditor"]
    assert public["segmentCount"] == len(row["segments"])
    assert set(row["segments"][0]) == {
        "document_id", "heading", "ordinal", "normalized_text_sha256",
        "char_start", "char_end",
    }
    assert "original" not in json.dumps(public, ensure_ascii=False)


def test_segment_ledger_is_deterministic_bounded_and_contains_no_body(tmp_path):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    normalized = "# Long section\n\n" + ("가" * 1500)
    (knowledge / "rules.md").write_text(normalized, encoding="utf-8")
    manifest = _build(tmp_path, _write_manifest(tmp_path))

    segments = manifest.documents[0]["segments"]
    assert [(row["char_start"], row["char_end"]) for row in segments] == [
        (0, 1200), (1080, len(normalized)),
    ]
    assert [row["ordinal"] for row in segments] == [1, 2]
    assert all(row["heading"] == "Long section" for row in segments)
    for row in segments:
        piece = normalized[row["char_start"]:row["char_end"]]
        assert row["normalized_text_sha256"] == hashlib.sha256(
            piece.encode("utf-8")
        ).hexdigest()
        assert "text" not in row


def test_manifest_digest_changes_with_revision_acl_or_release_review_binding(tmp_path):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "rules.md").write_text("# Rules\n\nbody", encoding="utf-8")
    first = _build(tmp_path, _write_manifest(tmp_path))
    revision = _build(tmp_path, _write_manifest(tmp_path, document_patch={"revision": 2}))
    acl = _build(tmp_path, _write_manifest(
        tmp_path, document_patch={"allowed_roles": ["knowledge_curator"]},
    ))
    release = _build(
        tmp_path, _write_manifest(tmp_path, reviewed_for_version="kb-2"),
        version="kb-2",
    )

    assert len({first.sha256, revision.sha256, acl.sha256, release.sha256}) == 4
    assert release.documents[0]["reviewed_for_version"] == "kb-2"


def test_manifest_rejects_chunk_governance_drift_and_path_escape(tmp_path):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "rules.md").write_text("rules", encoding="utf-8")
    with pytest.raises(KnowledgeManifestError, match="chunking"):
        _build(tmp_path, _write_manifest(tmp_path, max_chars=999))
    with pytest.raises(KnowledgeManifestError, match="immutable"):
        _build(tmp_path, _write_manifest(
            tmp_path, governance_patch={"immutable_release": False},
        ))

    outside = tmp_path.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    config = _write_manifest(tmp_path, document_patch={"path": "../outside.md"})
    with pytest.raises(KnowledgeManifestError, match="escapes"):
        _build(tmp_path, config)


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"classification": ""}, "classification"),
        ({"classification": "restricted"}, "classification"),
        ({"audience": "pmo_users"}, "audience"),
        ({"allowed_roles": ["not_registered"]}, "unregistered Role"),
        ({"allowed_roles": ["auditor", "auditor"]}, "unique"),
        ({"revision": 0}, "revision"),
        ({"reviewed_for_release": False}, "reviewed"),
        ({"reviewed_for_version": "kb-other"}, "exact knowledge version"),
        ({"reviewer_digest": "named-reviewer"}, "reviewer_digest"),
    ],
)
def test_manifest_rejects_unclassified_unregistered_or_unreviewed_documents(
    tmp_path, patch, message,
):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "rules.md").write_text("rules", encoding="utf-8")
    with pytest.raises(KnowledgeManifestError, match=message):
        _build(tmp_path, _write_manifest(tmp_path, document_patch=patch))


@pytest.mark.parametrize("duplicate_field", ["id", "path"])
def test_manifest_rejects_duplicate_document_identity(tmp_path, duplicate_field):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "rules.md").write_text("rules", encoding="utf-8")
    config = _write_manifest(tmp_path)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    second = dict(raw["documents"][0])
    second["id"] = "rules-two"
    second["path"] = "knowledge/rules-two.md"
    (tmp_path / "knowledge" / "rules-two.md").write_text("other", encoding="utf-8")
    second[duplicate_field] = raw["documents"][0][duplicate_field]
    raw["documents"].append(second)
    config.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(KnowledgeManifestError, match="unique"):
        _build(tmp_path, config)


@pytest.mark.parametrize(
    "dataset_id", ["", "not-a-uuid", "11111111-1111-1111-1111-111111111111"],
)
def test_manifest_requires_canonical_dataset_uuid(tmp_path, dataset_id):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "rules.md").write_text("rules", encoding="utf-8")
    with pytest.raises(KnowledgeManifestError, match="dataset UUID"):
        _build(tmp_path, _write_manifest(tmp_path), dataset=dataset_id)


@pytest.mark.parametrize("offset", [timedelta(days=-91), timedelta(minutes=5)])
def test_manifest_rejects_expired_or_future_review_checkpoint(tmp_path, offset):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "rules.md").write_text("rules", encoding="utf-8")
    reviewed_at = (datetime.now(timezone.utc) + offset).strftime("%Y-%m-%dT%H:%M:%SZ")

    with pytest.raises(KnowledgeManifestError, match="older|future"):
        _build(tmp_path, _write_manifest(
            tmp_path, document_patch={"reviewed_at": reviewed_at},
        ))


def test_manifest_rejects_arbitrary_profile_version_without_matching_review(tmp_path):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "rules.md").write_text("rules", encoding="utf-8")
    config = _write_manifest(tmp_path, reviewed_for_version="kb-reviewed")

    with pytest.raises(KnowledgeManifestError, match="exact knowledge version"):
        _build(tmp_path, config, version="kb-injected")
