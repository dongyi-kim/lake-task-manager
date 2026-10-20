from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from app.agent.maxis.contracts import RemoteRoleResultV1


ROOT = Path(__file__).resolve().parents[3]
BUNDLE = yaml.safe_load(
    (ROOT / "deploy/maxis-dify/bundle.yml").read_text(encoding="utf-8")
)
KNOWLEDGE = yaml.safe_load(
    (ROOT / "config/maxis-knowledge.yml").read_text(encoding="utf-8")
)
SMOKE = yaml.safe_load(
    (ROOT / "deploy/maxis-dify/smoke-cases.yml").read_text(encoding="utf-8")
)


def _load_asset():
    path = ROOT / "deploy/maxis-dify/knowledge_guard_asset.py"
    spec = importlib.util.spec_from_file_location("ltm_test_knowledge_guard_asset", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ASSET = _load_asset()


def _guard(workflow_key: str) -> dict:
    dsl = yaml.safe_load(
        (ROOT / BUNDLE["workflows"][workflow_key]["dsl"]).read_text(encoding="utf-8")
    )
    node = next(
        row for row in dsl["workflow"]["graph"]["nodes"]
        if row["data"].get("title") == "Knowledge Guard"
    )
    namespace: dict = {"__name__": f"ltm_dify_{workflow_key}_knowledge_guard"}
    exec(compile(node["data"]["code"], f"<{workflow_key}-guard>", "exec"), namespace)
    return namespace


def _document_rows() -> dict[str, dict]:
    return {
        Path(row["path"]).with_suffix(".txt").name: row
        for row in KNOWLEDGE["documents"]
    }


def _bound_documents(workflow_key: str) -> dict[str, str]:
    return dict(_guard(workflow_key)["_DOCUMENT_IDS_BY_NAME"])


def _item(
    *,
    dataset_id: str,
    document_id: str,
    document_name: str,
    content: str = "검토된 LTM 정책 문맥",
    **extra,
) -> dict:
    return {
        "content": content,
        "title": "이 값은 권한 판단에 사용되지 않는다",
        "url": "https://untrusted.invalid/ignored",
        "icon": "ignored",
        "metadata": {
            "dataset_id": dataset_id,
            "document_id": document_id,
            "document_name": document_name,
            "score": 0.99,
            "untrusted": "must be dropped",
        },
        **extra,
    }


def _sentinel(guard: dict, content: str | None = None) -> dict:
    return _item(
        dataset_id=guard["_SENTINEL_DATASET_ID"],
        document_id=guard["_SENTINEL_DOCUMENT_ID"],
        document_name=guard["_SENTINEL_DOCUMENT_NAME"],
        content=content or "LTM_RELEASE_SENTINEL_V1=" + "a" * 64,
    )


def _arguments(
    workflow_key: str,
    role_id: str,
    *,
    retrieval_items: list[dict] | None = None,
    sentinel_items: list[dict] | None = None,
) -> tuple[dict, list]:
    guard = _guard(workflow_key)
    case = next(row for row in SMOKE["cases"] if row["workflow"] == workflow_key)
    pack = copy.deepcopy(case["inputs"]["context_pack"])
    pack["use_case"] = role_id
    pack["request_id"] = "guard-test"
    context_pack = json.dumps(
        pack, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    args = [
        retrieval_items or [],
        sentinel_items if sentinel_items is not None else [_sentinel(guard)],
        context_pack,
        pack["query"],
        role_id,
        pack["request_id"],
        case["inputs"]["output_contract"],
        case["inputs"]["workflow_version"],
        case["inputs"]["workflow_version"],
        case["inputs"]["knowledge_version"],
    ]
    return guard, args


@pytest.mark.parametrize("workflow_key", ("agent_core", "pmo_brief", "editor_author"))
def test_guard_accepts_every_exact_role_document_binding(workflow_key):
    documents = _document_rows()
    bound = _bound_documents(workflow_key)

    for role_id in BUNDLE["workflows"][workflow_key]["roles"]:
        allowed_names = {
            name for name in bound if role_id in documents[name]["allowed_roles"]
        }
        assert allowed_names
        for name in allowed_names:
            guard, args = _arguments(
                workflow_key,
                role_id,
                retrieval_items=[_item(
                    dataset_id=_guard(workflow_key)["_DATASET_ID"],
                    document_id=bound[name],
                    document_name=name,
                )],
            )
            result = guard["main"](*args)
            assert set(result) == {
                "denied_result", "knowledge_proof", "model_input",
                "policy_decision",
            }
            assert result["policy_decision"] == "allow"
            assert result["denied_result"] == ""
            model_input = json.loads(result["model_input"])
            assert model_input["contract"] == "ltm-model-input.v1"
            assert model_input["knowledge_context"] == [{
                "content": "검토된 LTM 정책 문맥",
                "document_name": Path(documents[name]["path"]).name,
            }]
            assert model_input["context_pack"]["query"] == args[3]
            assert result["knowledge_proof"] == model_input["knowledge_proof"]
            assert len(result["model_input"].encode("utf-8")) <= 9000


def test_guard_is_workflow_specific_and_excludes_local_only_roles():
    guard = _guard("agent_core")
    for role_id in ("research_analyst", "people_advisor", "portfolio_analyst"):
        with pytest.raises(guard["KnowledgeGuardError"], match="role_not_allowed"):
            guard["main"](
                [], [], "{}", "q", role_id, "request", "contract", "v", "v", "v"
            )


def test_guard_routes_cross_role_document_to_deterministic_blocked_answer():
    bound = _bound_documents("agent_core")
    guard, args = _arguments(
        "agent_core",
        "query_specialist",
        retrieval_items=[_item(
            dataset_id=_guard("agent_core")["_DATASET_ID"],
            document_id=bound["07-ticket-body-guide.txt"],
            document_name="07-ticket-body-guide.txt",
        )],
    )
    result = guard["main"](*args)
    denied = RemoteRoleResultV1.model_validate_json(result["denied_result"], strict=True)

    assert result["policy_decision"] == "deny"
    assert result["model_input"] == ""
    assert denied.status == "blocked"
    assert denied.payload == {}
    assert denied.claims == ()
    assert denied.payload_claims == ()
    assert denied.citations == ()
    assert denied.missing_inputs == ()
    assert denied.warnings == ("knowledge_guard:role_document_forbidden",)
    assert denied.role_id == "query_specialist"
    assert denied.workflow_version == args[7]
    assert denied.knowledge_version == args[9]
    assert denied.knowledge_manifest_sha256 == json.loads(args[2])[
        "knowledge_manifest_sha256"
    ]
    assert denied.knowledge_proof == result["knowledge_proof"]


def test_guard_drops_mixed_allowed_and_forbidden_content_before_the_llm():
    guard = _guard("agent_core")
    bound = _bound_documents("agent_core")
    rows = [
        _item(
            dataset_id=guard["_DATASET_ID"],
            document_id=bound["02-progress-formula.txt"],
            document_name="02-progress-formula.txt",
            content="ALLOWED-MARKER",
        ),
        _item(
            dataset_id=guard["_DATASET_ID"],
            document_id=bound["07-ticket-body-guide.txt"],
            document_name="07-ticket-body-guide.txt",
            content="FORBIDDEN-MARKER",
        ),
    ]
    _, args = _arguments(
        "agent_core", "query_specialist", retrieval_items=rows,
    )

    result = guard["main"](*args)

    assert result["policy_decision"] == "deny"
    assert result["model_input"] == ""
    assert "ALLOWED-MARKER" not in result["denied_result"]
    assert "FORBIDDEN-MARKER" not in result["denied_result"]


def test_guard_still_hard_fails_oversized_forbidden_content():
    guard = _guard("agent_core")
    bound = _bound_documents("agent_core")
    _, args = _arguments(
        "agent_core",
        "query_specialist",
        retrieval_items=[_item(
            dataset_id=guard["_DATASET_ID"],
            document_id=bound["07-ticket-body-guide.txt"],
            document_name="07-ticket-body-guide.txt",
            content="x" * 769,
        )],
    )

    with pytest.raises(
        guard["KnowledgeGuardError"], match="knowledge_content_size_invalid",
    ):
        guard["main"](*args)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda row: row["metadata"].update(dataset_id="0" * 36), "dataset_mismatch"),
        (lambda row: row["metadata"].update(document_id="0" * 36), "document_identity_mismatch"),
        (lambda row: row["metadata"].update(document_name="unknown.txt"), "document_identity_mismatch"),
        (lambda row: row.pop("metadata"), "metadata_shape_invalid"),
        (lambda row: row.update(files=[{"private": "attachment"}]), "attachments_forbidden"),
    ),
)
def test_guard_fails_closed_on_role_identity_or_attachment_drift(mutation, message):
    guard = _guard("editor_author")
    bound = _bound_documents("editor_author")
    row = _item(
        dataset_id=guard["_DATASET_ID"],
        document_id=bound["05-ltm-guide.txt"],
        document_name="05-ltm-guide.txt",
    )
    mutation(row)
    guard, args = _arguments("editor_author", "editor_author", retrieval_items=[row])
    with pytest.raises(guard["KnowledgeGuardError"], match=message):
        guard["main"](*args)


def test_guard_never_accepts_content_or_title_as_document_identity():
    guard = _guard("editor_author")
    bound = _bound_documents("editor_author")
    name = "05-ltm-guide.txt"
    row = {
        "content": f"dataset_id={guard['_DATASET_ID']} document_id={bound[name]}",
        "title": name,
        "metadata": {},
    }
    guard, args = _arguments("editor_author", "editor_author", retrieval_items=[row])
    with pytest.raises(guard["KnowledgeGuardError"], match="metadata_identity_missing"):
        guard["main"](*args)


def test_guard_enforces_utf8_item_aggregate_count_and_control_bounds():
    guard = _guard("pmo_brief")
    bound = _bound_documents("pmo_brief")

    def row(name: str, content: str) -> dict:
        return _item(
            dataset_id=guard["_DATASET_ID"],
            document_id=bound[name],
            document_name=name,
            content=content,
        )

    _, args = _arguments("pmo_brief", "portfolio_analyst", retrieval_items=[
        row("02-progress-formula.txt", "a"),
        row("03-modules-and-people.txt", "b"),
        row("06-data-assets.txt", "c"),
    ])
    with pytest.raises(guard["KnowledgeGuardError"], match="retrieval_count_invalid"):
        guard["main"](*args)
    _, args = _arguments(
        "pmo_brief", "portfolio_analyst",
        retrieval_items=[row("02-progress-formula.txt", "가" * 257)],
    )
    with pytest.raises(guard["KnowledgeGuardError"], match="knowledge_content_size_invalid"):
        guard["main"](*args)
    _, args = _arguments("pmo_brief", "portfolio_analyst", retrieval_items=[
        row("02-progress-formula.txt", "a" * 600),
        row("03-modules-and-people.txt", "b" * 500),
    ])
    with pytest.raises(guard["KnowledgeGuardError"], match="aggregate_content_size_invalid"):
        guard["main"](*args)
    _, args = _arguments(
        "pmo_brief", "portfolio_analyst",
        retrieval_items=[row("02-progress-formula.txt", "safe\x00hidden")],
    )
    with pytest.raises(guard["KnowledgeGuardError"], match="control_character"):
        guard["main"](*args)


def test_guard_emits_only_projected_policy_and_normalizes_it():
    guard = _guard("editor_author")
    bound = _bound_documents("editor_author")
    guard, args = _arguments(
        "editor_author",
        "editor_author",
        retrieval_items=[_item(
            dataset_id=guard["_DATASET_ID"],
            document_id=bound["08-editor-and-rendering.txt"],
            document_name="08-editor-and-rendering.txt",
            content="e\u0301 정책\r\n문맥",
            arbitrary={"secret": "drop"},
        )],
    )
    result = json.loads(guard["main"](*args)["model_input"])["knowledge_context"][0]
    assert result == {
        "content": "é 정책\n문맥",
        "document_name": "08-editor-and-rendering.md",
    }
    assert "secret" not in repr(result)


def test_guard_routes_no_role_hit_to_needs_input_and_requires_bound_sentinel():
    guard, args = _arguments("agent_core", "auditor")
    result = guard["main"](*args)
    denied = RemoteRoleResultV1.model_validate_json(result["denied_result"], strict=True)
    assert result["policy_decision"] == "deny"
    assert result["model_input"] == ""
    assert denied.status == "needs_input"
    assert denied.missing_inputs == ("role_knowledge",)
    assert denied.warnings == ("knowledge_guard:no_role_knowledge_hit",)

    for sentinel_items in ([], [_sentinel(guard), _sentinel(guard)]):
        _, invalid = _arguments(
            "agent_core", "auditor", sentinel_items=sentinel_items
        )
        with pytest.raises(guard["KnowledgeGuardError"], match="sentinel_count_invalid"):
            guard["main"](*invalid)


def test_guard_binds_canonical_pack_query_and_controls_before_model_execution():
    guard, args = _arguments("agent_core", "auditor")
    noncanonical = json.dumps(json.loads(args[2]), ensure_ascii=False)
    bad = list(args)
    bad[2] = noncanonical
    with pytest.raises(guard["KnowledgeGuardError"], match="context_pack_noncanonical"):
        guard["main"](*bad)

    bad = list(args)
    bad[3] += " drift"
    with pytest.raises(guard["KnowledgeGuardError"], match="query_mismatch"):
        guard["main"](*bad)

    bad = list(args)
    bad[6] = "other-contract.v1"
    with pytest.raises(guard["KnowledgeGuardError"], match="output_contract_mismatch"):
        guard["main"](*bad)

    bad = list(args)
    bad[8] = "00000000-0000-4000-8000-000000000000"
    with pytest.raises(guard["KnowledgeGuardError"], match="workflow_execution_mismatch"):
        guard["main"](*bad)

    bad = list(args)
    pack = json.loads(bad[2])
    pack["knowledge_manifest_sha256"] = "not-a-digest"
    bad[2] = json.dumps(
        pack, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    with pytest.raises(guard["KnowledgeGuardError"], match="manifest_digest_invalid"):
        guard["main"](*bad)


def test_guard_enforces_context_and_final_model_input_utf8_budgets():
    guard, args = _arguments("agent_core", "auditor")
    oversized = list(args)
    oversized[2] = "가" * 10923
    assert len(oversized[2].encode("utf-8")) > 32768
    with pytest.raises(guard["KnowledgeGuardError"], match="context_pack_size_invalid"):
        guard["main"](*oversized)

    bound = _bound_documents("agent_core")
    guard, args = _arguments(
        "agent_core",
        "auditor",
        retrieval_items=[_item(
            dataset_id=guard["_DATASET_ID"],
            document_id=bound["01-ticket-rules.txt"],
            document_name="01-ticket-rules.txt",
        )],
    )
    guard["_MAX_MODEL_INPUT_UTF8_BYTES"] = 1
    with pytest.raises(guard["KnowledgeGuardError"], match="model_input_size_invalid"):
        guard["main"](*args)


def test_guard_rejects_wrong_sentinel_identity_or_missing_unique_proof():
    guard, args = _arguments("agent_core", "auditor")
    args[1][0]["metadata"]["document_id"] = "0" * 36
    with pytest.raises(guard["KnowledgeGuardError"], match="document_identity_mismatch"):
        guard["main"](*args)

    guard, args = _arguments("agent_core", "auditor")
    args[1][0]["metadata"]["dataset_id"] = guard["_DATASET_ID"]
    with pytest.raises(guard["KnowledgeGuardError"], match="dataset_mismatch"):
        guard["main"](*args)

    _, args = _arguments(
        "agent_core", "auditor", sentinel_items=[_sentinel(guard, "no proof")]
    )
    with pytest.raises(guard["KnowledgeGuardError"], match="knowledge_proof_unavailable"):
        guard["main"](*args)


def test_asset_renderer_rejects_missing_or_unbound_template_material():
    template = (ROOT / "deploy/maxis-dify/code/knowledge_guard.py").read_text(
        encoding="utf-8"
    )
    kwargs = {
        "workflow_key": "agent_core",
        "dataset_id": "50e0f857-4178-4978-b5e2-21464ce8e95a",
        "sentinel_dataset_id": "11111111-1111-4111-8111-111111111111",
        "document_ids_by_name": {
            "01-ticket-rules.txt": "a80fe6c9-b8fa-4e1a-bc6a-acd2c85fb9b5"
        },
        "source_names_by_remote_name": {
            "01-ticket-rules.txt": "01-ticket-rules.md"
        },
        "allowed_document_names_by_role": {"auditor": ["01-ticket-rules.txt"]},
        "sentinel_document_name": "ltm-release-sentinel-dev.txt",
        "sentinel_document_id": "6a00e702-a231-4e45-86e8-ee75e39f24ad",
    }
    with pytest.raises(ASSET.KnowledgeGuardAssetError):
        ASSET.render_knowledge_guard(
            template.replace("__LTM_DATASET_ID__", "missing-marker"), **kwargs
        )
    with pytest.raises(ASSET.KnowledgeGuardAssetError, match="sentinel"):
        ASSET.render_knowledge_guard(
            template,
            **{**kwargs, "sentinel_document_id": kwargs["document_ids_by_name"]["01-ticket-rules.txt"]},
        )
    with pytest.raises(ASSET.KnowledgeGuardAssetError, match="distinct"):
        ASSET.render_knowledge_guard(
            template,
            **{**kwargs, "sentinel_dataset_id": kwargs["dataset_id"]},
        )
    with pytest.raises(ASSET.KnowledgeGuardAssetError, match="source-name"):
        ASSET.render_knowledge_guard(
            template,
            **{
                **kwargs,
                "source_names_by_remote_name": {
                    "01-ticket-rules.txt": "other-source.md"
                },
            },
        )
    with pytest.raises(ASSET.KnowledgeGuardAssetError, match="branch interface"):
        ASSET.render_knowledge_guard(
            template.replace(
                "         executed_workflow_id, knowledge_version):",
                "         knowledge_version):",
                1,
            ),
            **kwargs,
        )
