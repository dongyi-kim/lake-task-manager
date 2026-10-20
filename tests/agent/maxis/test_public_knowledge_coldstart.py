"""A public checkout can prepare PROD without any developer deployment state."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]


def _module(root=ROOT):
    path = root / "deploy/maxis-dify/materialize_environment.py"
    spec = importlib.util.spec_from_file_location("public_materializer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prod(root):
    environment = yaml.safe_load((root / "deploy/maxis-dify/environments/prod.template.yml").read_text(encoding="utf-8"))
    environment.update(status="ready", service_base_url="https://dify.maxis.skax.co.kr/v1")
    knowledge = environment["knowledge"]
    knowledge.update(version="ltm-public-knowledge.v1", name="LTM Governed Knowledge")
    for field in ("dataset_id", "sentinel_dataset_id", "role_metadata_field_id"):
        knowledge[field] = str(uuid4())
    knowledge["sentinel_file"]["document_id"] = str(uuid4())
    for row in knowledge["documents"].values():
        row["document_id"] = str(uuid4())
    environment["model"] = {"plugin_unique_identifier": "test/provider:1.0@" + "a" * 64,
                            "provider": "test/provider/chat", "name": "explicit-prod-model",
                            "completion_params": {}}
    for key, row in environment["workflows"].items():
        row["app_name"] = "LTM " + key
    return environment


def test_public_inventory_has_distinct_descriptions_content_hashes_and_coverage():
    source = _module().public_knowledge_source()
    rows = source["documents"]
    assert len(rows) == 10
    assert len({row["description"] for row in rows}) == 10
    assert len({row["sha256"] for row in rows}) == 10
    assert {row["path"] for row in rows} == {
        "knowledge/" + name for name in (
            "01-ticket-rules.md", "02-progress-formula.md", "03-modules-and-people.md",
            "04-work-breakdown.md", "05-ltm-guide.md", "06-data-assets.md",
            "07-ticket-body-guide.md", "08-editor-and-rendering.md",
            "09-public-project-stack.md", "10-glossary.md")}
    for row in rows:
        assert row["sha256"] == hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest()
        assert row["path"].startswith("knowledge/")
    cases = yaml.safe_load((ROOT / "deploy/maxis-dify/knowledge-evals.yml").read_text(encoding="utf-8"))["cases"]
    assert len(cases) == 31
    for row in rows:
        assert {case["type"] for case in cases if case.get("target_document_id") == row["id"]} == {
            "positive", "prompt_injection", "acl_negative"}


def test_fresh_process_generates_all_prod_dsls_without_dev_or_existing_cache(tmp_path):
    root = tmp_path / "checkout"
    # Include pending public source edits, but never ignored per-PC config or cache.
    inventory = subprocess.run([
        "git", "-c", "safe.directory=" + ROOT.as_posix(), "ls-files", "-z",
        "--cached", "--others", "--exclude-standard", "--",
        "app", "config", "knowledge", "deploy/maxis-dify",
    ], cwd=ROOT, capture_output=True, check=True)
    for relative in inventory.stdout.decode("utf-8").split("\0"):
        if not relative:
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    (root / "deploy/maxis-dify/environments/dev.yml").unlink()
    assert not (root / ".cache").exists()
    environment = _prod(root)
    path = root / "prod.yml"
    path.write_text(yaml.safe_dump(environment), encoding="utf-8")
    program = """
import importlib.util, json, sys
from pathlib import Path
root = Path.cwd()
spec = importlib.util.spec_from_file_location('materializer', root / 'deploy/maxis-dify/materialize_environment.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
source = m.public_knowledge_source()
assert 'app.infra.settings' not in sys.modules
files = m.materialize(root / 'prod.yml', root / 'output')
m._VALIDATOR.validate_bundle(root)
assert not (root / '.cache/maxis').exists()
print(json.dumps({'files': [p.name for p in files], 'documents': len(source['documents'])}))
"""
    result = subprocess.run([sys.executable, "-B", "-c", program], cwd=root,
                            capture_output=True, text=True, encoding="utf-8", timeout=40)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["documents"] == 10
    assert len(json.loads(result.stdout)["files"]) == 3
    for output in (root / "output").glob("*.yml"):
        dsl = yaml.safe_load(output.read_text(encoding="utf-8"))
        assert "__LTM_MODEL_" not in output.read_text(encoding="utf-8")
        for node in dsl["workflow"]["graph"]["nodes"]:
            data = node["data"]
            if data["type"] == "llm":
                assert data["model"]["name"] == "explicit-prod-model"
                assert data["model"]["provider"] == environment["model"]["provider"]
                assert data["model"]["completion_params"] == {}
            if data["title"] == "Knowledge Guard":
                scope = {}
                exec(data["code"], scope)
                assert len(scope["_DOCUMENT_IDS_BY_NAME"]) == 10
                assert scope["_DATASET_ID"] == environment["knowledge"]["dataset_id"]


@pytest.mark.parametrize("params", [{}, {"max_completion_tokens": 2048, "reasoning_effort": "low"},
                                   {"temperature": 0.4, "top_p": 0.8, "enable_thinking": False}])
def test_selected_model_parameters_replace_defaults_in_every_llm_node(tmp_path, params):
    module = _module()
    environment = _prod(ROOT)
    environment["model"]["completion_params"] = params
    original = json.loads(json.dumps(environment))
    outputs = module.materialize(environment, tmp_path / "imports")
    assert environment == original
    for output in outputs:
        dsl = yaml.safe_load(output.read_text(encoding="utf-8"))
        assert dsl["dependencies"][0]["value"]["plugin_unique_identifier"] == environment["model"]["plugin_unique_identifier"]
        for node in dsl["workflow"]["graph"]["nodes"]:
            if node["data"]["type"] == "llm":
                assert node["data"]["model"]["completion_params"] == params
                assert node["data"]["model"]["name"] == "explicit-prod-model"


@pytest.mark.parametrize("params", [
    None, [], {"temperature": float("nan")}, {"max_tokens": True}, {"max_tokens": -1},
    {"reasoning_effort": "private-secret"}, {"api_key": "private-secret"},
    {"reasoning_effort": {"nested": "private-secret"}}, {"unknown": "private-secret"},
])
def test_model_parameters_reject_unbounded_or_private_values_without_echo(params):
    module = _module()
    with pytest.raises(module.EnvironmentBindingError) as error:
        module.validate_completion_params(params)
    assert "private-secret" not in str(error.value)


def test_mapping_input_is_not_mutated_and_optional_manifest_has_same_output(tmp_path):
    module = _module()
    environment = _prod(ROOT)
    original = json.loads(json.dumps(environment))
    first = module.materialize(environment, tmp_path / "explicit")
    assert environment == original
    environment["knowledge"].pop("source_manifest")
    second = module.materialize(environment, tmp_path / "default")
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]


def test_explicit_document_digest_rejects_modified_source(tmp_path):
    from app.agent.maxis.knowledge_manifest import build_manifest, KnowledgeManifestError
    shutil.copytree(ROOT / "config", tmp_path / "config")
    shutil.copytree(ROOT / "knowledge", tmp_path / "knowledge")
    source = tmp_path / "knowledge/01-ticket-rules.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
    with pytest.raises(KnowledgeManifestError, match="SHA-256"):
        build_manifest(knowledge_version="ltm-public-knowledge.v1",
            dataset_id="11111111-1111-4111-8111-111111111111",
            config_path=tmp_path / "config/maxis-knowledge.yml", source_root=tmp_path)
