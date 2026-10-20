"""Generated artifact and validation-call lifetime integration checks."""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def load(path):
    spec = importlib.util.spec_from_file_location("grounding_integration_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = load(Path(__file__).with_name("test_source_grounding.py"))


def test_source_tokens_are_private_to_one_validation_call(context_guard, now, monkeypatch):
    pack = api.pack_for(context_guard, now, '{"body":"first\\nsecond"}')
    result = api.candidate(pack, "second")
    result["claims"].append({**result["claims"][0], "claim_id": "claim-two"})
    original = api.LOCAL.source_lexical_parts
    calls = []

    def counted(content):
        calls.append(content)
        return original(content)

    monkeypatch.setattr(api.LOCAL, "source_lexical_parts", counted)
    api.local_result(api.LOCAL, pack, result)
    assert calls == [pack.sources[0].content]
    changed = api.pack_for(context_guard, now, '{"body":"alpha\\nreplacement"}')
    assert changed.sources[0].source_id == pack.sources[0].source_id
    with pytest.raises(api.LOCAL.ResponseGuardError, match="citation_assertion_mismatch"):
        api.local_result(api.LOCAL, changed, result)
    assert calls == [pack.sources[0].content, changed.sources[0].content]
    api.local_result(api.LOCAL, changed, api.candidate(changed, "replacement"))
    assert calls[-1] == changed.sources[0].content and len(calls) == 3


@pytest.mark.parametrize("template", ["", api.RENDERER.HELPER_IMPORT + "\n" + api.RENDERER.HELPER_IMPORT])
def test_renderer_rejects_missing_or_duplicate_helper_import(template):
    with pytest.raises(ValueError, match="canonical helper import drifted"):
        api.RENDERER.render_contract_guard(template, "def source_lexical_parts(value): pass")


def test_rendered_source_is_same_for_validator_materializer_and_all_dsl_guards():
    validator = load(ROOT / "deploy/maxis-dify/validate_bundle.py")
    materializer = load(ROOT / "deploy/maxis-dify/materialize_environment.py")
    assert api.EMITTED_SOURCE == materializer.CONTRACT_GUARD_SOURCE
    assert hashlib.sha256(api.EMITTED_SOURCE.encode()).hexdigest() == validator.DIFY_CONTRACT_GUARD_SHA256
    for name, counts in (("agent-core", (32, 38)), ("pmo-brief", (12, 12)), ("editor-author", (23, 26))):
        dsl = yaml.safe_load((ROOT / f"deploy/maxis-dify/dsl/ltm-{name}.yml").read_text(encoding="utf-8"))
        graph = dsl["workflow"]["graph"]
        assert (len(graph["nodes"]), len(graph["edges"])) == counts
        guards = [n for n in graph["nodes"] if n["data"].get("title") == "Contract Guard"]
        assert len(guards) == 1 and guards[0]["data"]["code"] == api.EMITTED_SOURCE


def test_emitted_template_behavior_parity_for_all_existing_smoke_cases(monkeypatch):
    probes = load(Path(__file__).with_name("test_dify_contract_guard_code.py"))
    for case in probes._cases():
        original = probes._invoke(deepcopy(case))
        monkeypatch.setattr(probes, "GUARD", api.EMITTED)
        assert probes._invoke(deepcopy(case)) == original
        monkeypatch.setattr(probes, "GUARD", api.REMOTE)


@pytest.mark.parametrize("key", ["sourceGrounding", "contractGuardRenderer"])
def test_eval_identity_changes_when_a_guard_dependency_changes(tmp_path, monkeypatch, key):
    from app.agent.maxis import knowledge_eval

    relative = knowledge_eval._SOURCE_FILE_PATHS[key]
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_bytes((ROOT / relative).read_bytes())
    monkeypatch.setattr(knowledge_eval, "_root", lambda: tmp_path)
    monkeypatch.setattr(knowledge_eval, "_SOURCE_FILE_PATHS", {key: relative})
    before = knowledge_eval.evaluation_source_file_sha256()
    target.write_bytes(target.read_bytes() + b"\n# dependency mutation\n")
    assert knowledge_eval.evaluation_source_file_sha256()[key] != before[key]


def test_empty_json_parts_and_source_rows_fail_without_union_errors(context_guard, now):
    for raw in ("{}", "[]"):
        assert api.LOCAL.source_lexical_parts(raw) == ()
        pack = api.pack_for(context_guard, now, raw)
        result = api.candidate(pack, "unsupported")
        assert api.remote_result(api.REMOTE, pack, result)["status"] == "blocked"
        with pytest.raises(api.LOCAL.ResponseGuardError, match="citation_assertion_mismatch"):
            api.local_result(api.LOCAL, pack, result)
    empty = pack.model_copy(update={"sources": ()})
    assert api.remote_result(api.REMOTE, empty, result)["status"] == "blocked"
    with pytest.raises(api.LOCAL.ResponseGuardError):
        api.local_result(api.LOCAL, empty, result)
