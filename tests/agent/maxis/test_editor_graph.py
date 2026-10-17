"""Selection and writing stay bounded behind the original authoritative guards."""
import copy
import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"deploy/maxis-dify/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build():
    spec, validator = _load("editor_graph"), _load("validate_bundle")
    dsl = yaml.safe_load((ROOT / "deploy/maxis-dify/dsl/ltm-editor-author.yml").read_text(encoding="utf-8"))
    return spec, spec.rebuild(ROOT, dsl, validator)


def test_builder_is_idempotent_and_preserves_common_guards():
    spec, dsl = _build()
    assert spec.rebuild(ROOT, dsl, _load("validate_bundle")) == dsl
    graph = dsl["workflow"]["graph"]
    assert len(graph["nodes"]) == 23
    assert len(graph["edges"]) == 26
    nodes = {node["data"]["title"]: node for node in graph["nodes"]}
    assert nodes["Editor Candidate"]["data"] == spec.merge_data(spec.BRANCH_NODE_IDS)
    assert nodes["Contract Guard"]["data"]["variables"][0]["value_selector"] == [nodes["Editor Assembler"]["id"], "candidate"]
    assert nodes["Allowed Answer"]["data"]["answer"] == "{{#1909000000001005.result#}}"


@pytest.mark.parametrize("branch", ["description", "comment", "control", "stop"])
def test_each_path_executes_only_required_models(branch):
    spec, dsl = _build()
    graph = dsl["workflow"]["graph"]
    nodes = {node["id"]: node["data"] for node in graph["nodes"]}
    reached, pending = set(), [spec.STAGE_NODE_ID]
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        handles = {spec.STAGE_NODE_ID: "true" if branch == "control" else "false",
                   spec.ROUTE_NODE_ID: {"description": "true", "comment": "comment"}.get(branch, "false")}
        pending.extend(e["target"] for e in graph["edges"] if e["source"] == current
                       and (current not in handles or e["sourceHandle"] == handles[current]))
    expected = ({spec.CONTROL_NODE_ID} if branch == "control" else {spec.SELECTOR_NODE_ID}
                | ({spec.BRANCH_NODE_IDS[branch]} if branch != "stop" else set()))
    assert {key for key in reached if nodes[key]["type"] == "llm"} == expected
    assert {"Editor Candidate", "Editor Assembler", "Contract Guard", "Allowed Answer"} <= {nodes[key]["title"] for key in reached}
    assert not any(nodes[key]["type"] in {"tool", "agent", "http-request"} for key in reached)


def test_writer_input_cannot_reintroduce_unselected_knowledge_or_raw_evidence():
    spec, dsl = _build()
    nodes = {node["id"]: node["data"] for node in dsl["workflow"]["graph"]["nodes"]}
    for branch in ("description", "comment"):
        data = nodes[spec.BRANCH_NODE_IDS[branch]]
        assert data["context"] == {"enabled": False, "variable_selector": []}
        assert data["prompt_template"][1]["text"].endswith(f"{{{{#{spec.SPEC_GUARD_NODE_ID}.model_input#}}}}")
    assert nodes[spec.ENFORCER_NODE_ID]["variables"] == [
        {"variable": "candidate", "value_selector": [spec.WRITER_MERGE_NODE_ID, "output"]},
        {"variable": "model_input", "value_selector": [_load("validate_bundle").EDITOR_INPUT_NODE_ID, "model_input"]},
        {"variable": "draft_spec", "value_selector": [spec.SELECTOR_NODE_ID, "text"]},
        {"variable": "context_pack", "value_selector": ["start", "context_pack"]},
    ]
    assert nodes[spec.SPEC_GUARD_NODE_ID]["variables"][-1] == {
        "variable": "context_pack", "value_selector": ["start", "context_pack"]}


def test_selector_prompt_and_both_boundaries_use_selection_only_input_v2():
    spec, dsl = _build()
    nodes = {node["id"]: node["data"] for node in dsl["workflow"]["graph"]["nodes"]}
    selector = nodes[spec.SELECTOR_NODE_ID]
    prompt = selector["prompt_template"][0]["text"]
    assert "editor-selector-input.v1" in prompt and "editor-input.v2" not in prompt
    assert "editor-draft-spec.v2" in prompt and "editor-plan.v2" not in prompt
    input_id = _load("validate_bundle").EDITOR_INPUT_NODE_ID
    for node_id in (input_id, spec.SPEC_GUARD_NODE_ID, spec.ENFORCER_NODE_ID):
        code = nodes[node_id]["code"]
        assert "editor-input.v2" in code and "editor-input.v1" not in code
        assert "def _draft_contract(" not in code
    assert set(nodes[input_id]["outputs"]) == {"model_input", "selector_input", "route"}
    assert selector["prompt_template"][1]["text"].endswith(f"{{{{#{input_id}.selector_input#}}}}")
    for title in ("DraftSpec Guard", "DraftSpec Enforcer", "Editor Assembler", "Contract Guard"):
        data = next(row for row in nodes.values() if row["title"] == title)
        assert "editor-selector-input.v1" not in data["code"]


@pytest.mark.parametrize("mutation", ["raw_writer_input", "raw_writer_context", "selector_bypass",
                                     "guard_source", "guard_output", "stop_source", "enforcer_input",
                                     "enforcer_source", "guard_pack", "enforcer_pack", "writer_merge", "control_stage", "recovery"])
def test_selection_boundary_rejects_graph_shortcuts(mutation):
    spec, dsl = _build()
    validator = _load("validate_bundle")
    graph = dsl["workflow"]["graph"]
    nodes = {node["id"]: node["data"] for node in graph["nodes"]}
    if mutation == "raw_writer_input":
        nodes[spec.COMMENT_NODE_ID]["prompt_template"][1]["text"] = "{{#start.context_pack#}}"
    elif mutation == "raw_writer_context":
        nodes[spec.COMMENT_NODE_ID]["context"] = {"enabled": True, "variable_selector": ["start", "context_pack"]}
    elif mutation == "selector_bypass":
        edge = next(row for row in graph["edges"] if row["source"] == spec.STAGE_NODE_ID and row["sourceHandle"] == "false")
        edge["target"] = spec.COMMENT_NODE_ID
    elif mutation in {"guard_source", "stop_source", "enforcer_source"}:
        key = {"guard_source": spec.SPEC_GUARD_NODE_ID, "stop_source": spec.STOP_NODE_ID,
               "enforcer_source": spec.ENFORCER_NODE_ID}[mutation]
        nodes[key]["code"] += "\n# unreviewed source"
    elif mutation == "guard_output":
        nodes[spec.SPEC_GUARD_NODE_ID]["outputs"].pop("route")
    elif mutation == "enforcer_input":
        nodes[spec.ENFORCER_NODE_ID]["variables"][1]["value_selector"] = [spec.SPEC_GUARD_NODE_ID, "model_input"]
    elif mutation in {"guard_pack", "enforcer_pack"}:
        node_id = spec.SPEC_GUARD_NODE_ID if mutation == "guard_pack" else spec.ENFORCER_NODE_ID
        nodes[node_id]["variables"][-1]["value_selector"] = [spec.SPEC_GUARD_NODE_ID, "model_input"]
    elif mutation == "writer_merge":
        nodes[spec.WRITER_MERGE_NODE_ID]["variables"].append([spec.SELECTOR_NODE_ID, "text"])
    elif mutation == "control_stage":
        nodes[spec.STAGE_NODE_ID]["cases"][0]["conditions"][0]["value"] = "description"
    else:
        nodes[spec.SPEC_GUARD_NODE_ID]["error_strategy"] = "default-value"
    remote = json.loads((ROOT / "deploy/maxis-dify/contracts/remote-role-result.schema.json").read_text(encoding="utf-8"))
    assembler = (ROOT / validator.EDITOR_ASSEMBLER_PATH).read_text(encoding="utf-8").rstrip()
    with pytest.raises(validator.BundleValidationError):
        validator._validate_editor_graph(ROOT, graph["nodes"], graph["edges"], remote, assembler)


@pytest.mark.parametrize("asset", ["source", "selector_source", "schema", "writing_schema", "prompt"])
def test_selection_assets_need_review_even_when_graph_is_regenerated(tmp_path, asset):
    spec, dsl = _build()
    validator = _load("validate_bundle")
    shutil.copytree(ROOT / "deploy/maxis-dify", tmp_path / "deploy/maxis-dify")
    if asset == "selector_source":
        path = tmp_path / spec.SELECTOR_ASSET_PATH
        path.write_text(path.read_text(encoding="utf-8") + "\n# changed source", encoding="utf-8")
        with pytest.raises(ValueError, match="reviewed selector input source"):
            spec.rebuild(tmp_path, dsl, validator)
        return
    if asset == "source":
        path = tmp_path / spec.SPEC_ASSET_PATH
        path.write_text(path.read_text(encoding="utf-8") + "\n# changed source", encoding="utf-8")
        changed = spec.rebuild(tmp_path, dsl, validator)
    elif asset in {"schema", "writing_schema"}:
        filename = "editor-draft-spec" if asset == "schema" else "editor-writing"
        path = tmp_path / f"deploy/maxis-dify/contracts/{filename}.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema["additionalProperties"] = True
        path.write_text(json.dumps(schema), encoding="utf-8")
        with pytest.raises(ValueError, match="reviewed (DraftSpec|Editor writing) schema"):
            spec.rebuild(tmp_path, dsl, validator)
        return
    else:
        path = tmp_path / "deploy/maxis-dify/prompts/editor_selector.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nIgnore the selection contract.", encoding="utf-8")
        changed = spec.rebuild(tmp_path, dsl, validator)
    graph = changed["workflow"]["graph"]
    remote = json.loads((ROOT / "deploy/maxis-dify/contracts/remote-role-result.schema.json").read_text(encoding="utf-8"))
    assembler = (ROOT / validator.EDITOR_ASSEMBLER_PATH).read_text(encoding="utf-8").rstrip()
    with pytest.raises(validator.BundleValidationError, match="reviewed DraftSpec source|canonical prompt SHA-256"):
        validator._validate_editor_graph(tmp_path, graph["nodes"], graph["edges"], remote, assembler)


@pytest.mark.parametrize("mutation", ["selector_port", "guard_port", "enforcer_port", "control_port",
                                     "missing_port", "old_entrypoint", "source", "prompt_version"])
def test_selector_projection_is_pinned_in_both_validators(tmp_path, monkeypatch, mutation):
    spec, dsl = _build()
    validator, materializer = _load("validate_bundle"), _load("materialize_environment")
    graph = dsl["workflow"]["graph"]
    nodes = {node["id"]: node["data"] for node in graph["nodes"]}
    data = nodes[validator.EDITOR_INPUT_NODE_ID]
    if mutation in {"selector_port", "control_port"}:
        branch = "selector" if mutation == "selector_port" else "control"
        message = nodes[spec.BRANCH_NODE_IDS[branch]]["prompt_template"][1]
        old, new = ("selector_input", "model_input") if branch == "selector" else ("model_input", "selector_input")
        message["text"] = message["text"].replace("." + old + "#", "." + new + "#")
    elif mutation in {"guard_port", "enforcer_port"}:
        node_id = spec.SPEC_GUARD_NODE_ID if mutation == "guard_port" else spec.ENFORCER_NODE_ID
        next(v for v in nodes[node_id]["variables"] if v["variable"] == "model_input")["value_selector"][-1] = "selector_input"
    elif mutation == "missing_port": del data["outputs"]["selector_input"]
    elif mutation == "old_entrypoint": data["code"] = data["code"].replace("return prepare_selector_input(model_input)", "return prepare(model_input)")
    elif mutation == "source": data["code"] += "\n# unreviewed projection"
    else:
        nodes[spec.SELECTOR_NODE_ID]["prompt_template"][0]["text"] = nodes[spec.SELECTOR_NODE_ID]["prompt_template"][0]["text"].replace("editor-selector-input.v1", "editor-input.v2")
    remote = json.loads((ROOT / "deploy/maxis-dify/contracts/remote-role-result.schema.json").read_text(encoding="utf-8"))
    assembler = (ROOT / validator.EDITOR_ASSEMBLER_PATH).read_text(encoding="utf-8").rstrip()
    with pytest.raises(validator.BundleValidationError):
        validator._validate_editor_graph(ROOT, graph["nodes"], graph["edges"], remote, assembler)
    shutil.copytree(ROOT / "deploy/maxis-dify", tmp_path / "deploy/maxis-dify")
    (tmp_path / "deploy/maxis-dify/dsl/ltm-editor-author.yml").write_text(yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(materializer, "ROOT", tmp_path)
    environment = materializer.load_environment(ROOT / "deploy/maxis-dify/environments/dev.yml")
    with pytest.raises(materializer.EnvironmentBindingError):
        materializer._materialize_dsl(environment, "editor_author")


@pytest.mark.parametrize("branch,sections", [("description", ["background", "scope", "work", "dod"]), ("comment", ["comment"])])
def test_writer_contract_contains_no_control_envelope(branch, sections):
    spec, dsl = _build()
    node = next(n["data"] for n in dsl["workflow"]["graph"]["nodes"] if n["id"] == spec.BRANCH_NODE_IDS[branch])
    schema = node["structured_output"]["schema"]
    assert "oneOf" not in schema
    assert schema["properties"]["contract"]["enum"] == ["editor-writing.v1"]
    assert set(schema["properties"]) == {"contract", "sections"}
    assert schema["required"] == ["contract", "sections"]
    assert schema["properties"]["sections"]["minItems"] == len(sections)
    assert schema["properties"]["sections"]["maxItems"] == len(sections)
    assert schema["properties"]["sections"]["items"]["properties"]["section"]["enum"] == sections
    prompt = node["prompt_template"][0]["text"]
    assert "remote-role-result" not in prompt
    assert "payload_schema_sha256" not in prompt
    assert len(prompt.encode("utf-8")) < 8192


@pytest.mark.parametrize("branch", ["description", "comment"])
@pytest.mark.parametrize("mutation", ["status", "source_ids", "link"])
def test_writer_schema_cannot_restore_compiler_owned_fields(branch, mutation):
    spec, dsl = _build()
    validator = _load("validate_bundle")
    graph = dsl["workflow"]["graph"]
    data = next(node["data"] for node in graph["nodes"] if node["id"] == spec.BRANCH_NODE_IDS[branch])
    schema = data["structured_output"]["schema"]
    variants = schema["properties"]["sections"]["items"]["properties"]["blocks"]["items"]["oneOf"]
    if mutation == "status":
        schema["properties"]["status"] = {"type": "string", "enum": ["ok", "blocked"]}
    elif mutation == "source_ids":
        proposal = next(row for row in variants if row["properties"]["type"]["enum"] == ["proposal"])
        proposal["properties"]["source_ids"] = {"type": "array", "items": {"type": "string"}}
    else:
        variants.append({"type": "object", "properties": {"type": {"enum": ["link"]},
                                                               "source_id": {"type": "string"}}})
    remote = json.loads((ROOT / "deploy/maxis-dify/contracts/remote-role-result.schema.json").read_text(encoding="utf-8"))
    assembler = (ROOT / validator.EDITOR_ASSEMBLER_PATH).read_text(encoding="utf-8").rstrip()
    with pytest.raises(validator.BundleValidationError, match=rf"editor_author\.{branch} Dify LLM model or structured output"):
        validator._validate_editor_graph(ROOT, graph["nodes"], graph["edges"], remote, assembler)


def test_builder_does_not_change_its_input():
    spec, dsl = _build()
    original = copy.deepcopy(dsl)
    spec.rebuild(ROOT, dsl, _load("validate_bundle"))
    assert dsl == original


@pytest.mark.parametrize("mutation", ["prompt", "schema", "selector", "retry", "extra_edge", "model"])
def test_publish_comparison_retains_all_execution_semantics(mutation):
    spec, dsl = _build()
    receipt = _load("graph_receipt")
    graph = dsl["workflow"]["graph"]
    changed = copy.deepcopy(graph)
    data = next(n["data"] for n in changed["nodes"] if n["id"] == spec.COMMENT_NODE_ID)
    if mutation == "prompt":
        data["prompt_template"][0]["text"] += " weakened policy"
    elif mutation == "schema":
        data["structured_output"]["schema"]["additionalProperties"] = True
    elif mutation == "selector":
        data["context"]["variable_selector"] = ["start", "context_pack"]
    elif mutation == "retry":
        data["retry_config"] = {"retry_enabled": True}
    elif mutation == "extra_edge":
        changed["edges"].append({"source": spec.CONTROL_NODE_ID, "sourceHandle": "source",
                                  "target": "answer", "targetHandle": "target", "data": {}})
    else:
        data["model"]["name"] = "unreviewed-model"
    assert receipt.graph_sha256(changed) != receipt.graph_sha256(graph)


def test_publish_comparison_ignores_only_presentation_and_local_uuid():
    _, dsl = _build()
    receipt = _load("graph_receipt")
    graph = dsl["workflow"]["graph"]
    changed = copy.deepcopy(graph)
    for node in changed["nodes"]:
        node["position"] = {"x": 0, "y": 0}
        node["data"]["selected"] = True
        node["data"]["desc"] = "UI annotation"
        if node["data"]["type"] == "llm":
            node["data"]["prompt_template"][0]["id"] = "new-editor-id"
        if node["data"]["title"] == "Role Knowledge":
            node["data"]["metadata_filtering_conditions"]["conditions"][0]["id"] = "d8bf897d-8b8c-490f-95f4-88eb9aeea400"
    assert receipt.graph_sha256(changed) == receipt.graph_sha256(graph)


def test_publication_reservation_blocks_concurrent_and_repeated_attempts(tmp_path):
    receipt = _load("graph_receipt")
    path = tmp_path / "attempt.json"
    with receipt.publication_receipt(path) as record:
        assert path.with_suffix(".json.claim").exists()
        with pytest.raises(FileExistsError):
            with receipt.publication_receipt(path):
                pytest.fail("duplicate must not enter an external operation")
        record["status"] = "published"
    assert '"published"' in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        with receipt.publication_receipt(path):
            pytest.fail("completed attempt must never run again")


@pytest.mark.parametrize("error", [RuntimeError, SystemExit, KeyboardInterrupt])
def test_publication_failure_records_phase_without_exception_text(tmp_path, error):
    receipt = _load("graph_receipt")
    path = tmp_path / "attempt.json"
    with pytest.raises(error):
        with receipt.publication_receipt(path) as record:
            record["phase"] = "login"
            raise error("private failure detail")
    result = path.read_text(encoding="utf-8")
    assert '"failed"' in result and '"login"' in result
    assert "private failure detail" not in result
    assert path.with_suffix(".json.claim").exists()


def test_description_prompt_rebuild_changes_only_its_system_message(monkeypatch):
    spec, before = _build()
    validator = _load("validate_bundle")
    path = ROOT / "deploy/maxis-dify/prompts/editor_description.md"
    original = Path.read_text
    marker = "Focused description-only test instruction."
    changed = original(path, encoding="utf-8").rstrip() + "\n\n" + marker
    monkeypatch.setattr(Path, "read_text", lambda self, *args, **kwargs:
        changed if self == path else original(self, *args, **kwargs))
    after = spec.rebuild(ROOT, before, validator)
    expected = copy.deepcopy(before)
    description = next(node["data"] for node in expected["workflow"]["graph"]["nodes"]
                       if node["id"] == spec.BRANCH_NODE_IDS["description"])
    description["prompt_template"][0]["text"] += "\n\n" + marker
    assert after == expected
    assert after != before


def test_description_source_and_embedded_prompt_cannot_drift_together(tmp_path, monkeypatch):
    spec, dsl = _build()
    validator, materializer = _load("validate_bundle"), _load("materialize_environment")
    shutil.copytree(ROOT / "deploy/maxis-dify", tmp_path / "deploy/maxis-dify")
    path = tmp_path / "deploy/maxis-dify/prompts/editor_description.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nUnreviewed description change.", encoding="utf-8")
    changed = spec.rebuild(tmp_path, dsl, validator)
    graph = changed["workflow"]["graph"]
    remote = json.loads((ROOT / "deploy/maxis-dify/contracts/remote-role-result.schema.json").read_text(encoding="utf-8"))
    assembler = (ROOT / validator.EDITOR_ASSEMBLER_PATH).read_text(encoding="utf-8").rstrip()
    with pytest.raises(validator.BundleValidationError, match="canonical prompt SHA-256"):
        validator._validate_editor_graph(tmp_path, graph["nodes"], graph["edges"], remote, assembler)
    (tmp_path / "deploy/maxis-dify/dsl/ltm-editor-author.yml").write_text(
        yaml.safe_dump(changed, allow_unicode=True, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(materializer, "ROOT", tmp_path)
    environment = materializer.load_environment(ROOT / "deploy/maxis-dify/environments/dev.yml")
    with pytest.raises(materializer.EnvironmentBindingError, match="canonical prompt SHA-256"):
        materializer._materialize_dsl(environment, "editor_author")
