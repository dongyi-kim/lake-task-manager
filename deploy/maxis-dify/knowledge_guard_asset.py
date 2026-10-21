"""Render the environment-bound, self-contained Dify Knowledge guard source."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping, Sequence


_MARKERS = {
    "workflow_key": '"__LTM_WORKFLOW_KEY__"',
    "dataset_id": '"__LTM_DATASET_ID__"',
    "sentinel_dataset_id": '"__LTM_SENTINEL_DATASET_ID__"',
    "documents": "__LTM_DOCUMENT_BINDINGS__",
    "source_names": "__LTM_SOURCE_NAME_BINDINGS__",
    "allowlist": "__LTM_ALLOWED_DOCUMENTS_BY_ROLE__",
    "sentinel_name": '"__LTM_SENTINEL_DOCUMENT_NAME__"',
    "sentinel_id": '"__LTM_SENTINEL_DOCUMENT_ID__"',
}
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SAFE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.txt$")
_SAFE_SOURCE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.md$")


class KnowledgeGuardAssetError(ValueError):
    """The static guard template or one of its deployment bindings is unsafe."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def render_knowledge_guard(
    template_source: str,
    *,
    workflow_key: str,
    dataset_id: str,
    sentinel_dataset_id: str,
    document_ids_by_name: Mapping[str, str],
    source_names_by_remote_name: Mapping[str, str],
    allowed_document_names_by_role: Mapping[str, Sequence[str]],
    sentinel_document_name: str,
    sentinel_document_id: str,
) -> str:
    """Return one compile-checked Code-node program with immutable bindings.

    No binding is accepted from a Dify Start variable.  The environment
    materializer supplies the dataset/document UUIDs, while the reviewed bundle
    supplies the workflow Role registry and per-Role document allow-list.
    """

    if not isinstance(template_source, str) or not template_source.strip():
        raise KnowledgeGuardAssetError("Knowledge guard template is empty.")
    if not _SAFE_KEY.fullmatch(workflow_key):
        raise KnowledgeGuardAssetError("Knowledge guard workflow key is invalid.")
    if not _UUID.fullmatch(dataset_id):
        raise KnowledgeGuardAssetError("Knowledge guard dataset UUID is invalid.")
    if (
        not _UUID.fullmatch(sentinel_dataset_id)
        or sentinel_dataset_id == dataset_id
    ):
        raise KnowledgeGuardAssetError(
            "Knowledge guard sentinel dataset UUID must be distinct."
        )
    documents = dict(document_ids_by_name)
    if not documents or any(
        not isinstance(name, str)
        or not _SAFE_FILE.fullmatch(name)
        or not isinstance(file_id, str)
        or not _UUID.fullmatch(file_id)
        for name, file_id in documents.items()
    ):
        raise KnowledgeGuardAssetError("Knowledge guard document bindings are invalid.")
    if len(documents) != len(set(documents.values())):
        raise KnowledgeGuardAssetError("Knowledge guard document UUIDs must be unique.")
    source_names = dict(source_names_by_remote_name)
    if (
        set(source_names) != set(documents)
        or len(source_names.values()) != len(set(source_names.values()))
        or any(
            not isinstance(source_name, str)
            or not _SAFE_SOURCE_FILE.fullmatch(source_name)
            or remote_name.removesuffix(".txt")
            != source_name.removesuffix(".md")
            for remote_name, source_name in source_names.items()
        )
    ):
        raise KnowledgeGuardAssetError(
            "Knowledge guard source-name bindings are invalid."
        )
    if (
        not isinstance(sentinel_document_name, str)
        or not _SAFE_FILE.fullmatch(sentinel_document_name)
        or not isinstance(sentinel_document_id, str)
        or not _UUID.fullmatch(sentinel_document_id)
        or sentinel_document_name in documents
        or sentinel_document_id in set(documents.values())
    ):
        raise KnowledgeGuardAssetError("Knowledge guard sentinel binding is invalid.")

    allowlist: dict[str, list[str]] = {}
    for role_id, raw_names in allowed_document_names_by_role.items():
        if not isinstance(role_id, str) or not _SAFE_KEY.fullmatch(role_id):
            raise KnowledgeGuardAssetError("Knowledge guard Role id is invalid.")
        if isinstance(raw_names, (str, bytes)) or not isinstance(raw_names, Sequence):
            raise KnowledgeGuardAssetError("Knowledge guard Role allow-list is invalid.")
        names = list(raw_names)
        if (
            not names
            or any(not isinstance(name, str) or name not in documents for name in names)
            or len(names) != len(set(names))
        ):
            raise KnowledgeGuardAssetError("Knowledge guard Role allow-list is invalid.")
        allowlist[role_id] = sorted(names)
    if not allowlist:
        raise KnowledgeGuardAssetError("Knowledge guard workflow has no Roles.")

    rendered = template_source
    replacements = {
        _MARKERS["workflow_key"]: _canonical_json(workflow_key),
        _MARKERS["dataset_id"]: _canonical_json(dataset_id),
        _MARKERS["sentinel_dataset_id"]: _canonical_json(sentinel_dataset_id),
        _MARKERS["documents"]: _canonical_json(documents),
        _MARKERS["source_names"]: _canonical_json(source_names),
        _MARKERS["allowlist"]: _canonical_json(allowlist),
        _MARKERS["sentinel_name"]: _canonical_json(sentinel_document_name),
        _MARKERS["sentinel_id"]: _canonical_json(sentinel_document_id),
    }
    for marker, replacement in replacements.items():
        if rendered.count(marker) != 1:
            raise KnowledgeGuardAssetError(
                "Knowledge guard template marker count is invalid."
            )
        rendered = rendered.replace(marker, replacement)
    if "__LTM_" in rendered:
        raise KnowledgeGuardAssetError("Knowledge guard template has an unknown marker.")
    try:
        tree = ast.parse(rendered, "<ltm-dify-knowledge-guard>", "exec")
        compile(tree, "<ltm-dify-knowledge-guard>", "exec")
    except SyntaxError as exc:
        raise KnowledgeGuardAssetError(
            "Rendered Knowledge guard does not compile."
        ) from exc
    main_nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    ]
    expected_main_arguments = [
        "retrieval_items", "sentinel_items", "context_pack", "query", "role_id",
        "correlation_id", "output_contract", "workflow_version",
        "executed_workflow_id", "knowledge_version",
    ]
    string_literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    if (
        len(main_nodes) != 1
        or [argument.arg for argument in main_nodes[0].args.args]
        != expected_main_arguments
        or not {"policy_decision", "denied_result"}.issubset(string_literals)
    ):
        raise KnowledgeGuardAssetError(
            "Knowledge guard deterministic branch interface is invalid."
        )
    return rendered.rstrip()
