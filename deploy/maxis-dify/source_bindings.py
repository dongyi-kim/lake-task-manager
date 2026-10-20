"""Environment-neutral canonical DSL markers; never live deployment identities.

UUID-shaped markers keep the reviewed guard compiler strict. Materialization must
replace these markers with explicitly supplied environment-owned assets.
"""
from __future__ import annotations

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


MODEL_PLUGIN = "__LTM_MODEL_PLUGIN__"
MODEL_PROVIDER = "__LTM_MODEL_PROVIDER__"
MODEL_NAME = "__LTM_MODEL_NAME__"
APP_NAMES = {"agent_core": "LTM Agent Core", "pmo_brief": "LTM PMO Brief",
             "editor_author": "LTM Editor Author"}


def marker_id(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, "https://lake-task-manager.invalid/source/" + name))


def canonical_knowledge(documents) -> dict:
    rows = documents.values() if isinstance(documents, dict) else documents
    return {
        "version": "source-only", "name": "LTM Governed Knowledge",
        "dataset_id": marker_id("knowledge"),
        "sentinel_dataset_id": marker_id("sentinel"),
        "role_metadata_field_id": marker_id("role-metadata"),
        "sentinel_file": {"source_name": "ltm-release-sentinel.md",
                          "remote_name": "ltm-release-sentinel.txt",
                          "document_id": marker_id("sentinel-document")},
        "documents": {row["id"]: {
            "source_name": Path(row["path"]).name,
            "remote_name": Path(row["path"]).stem + ".txt",
            "document_id": marker_id("document/" + row["id"]),
        } for row in rows},
    }
