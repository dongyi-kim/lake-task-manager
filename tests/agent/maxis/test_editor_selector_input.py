"""Selection hints are a lossless view, never evidence or selection repair."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy/maxis-dify"


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load(Path(__file__).with_name("test_editor_routing.py"))
editor_pack, control_pack = HELPER.editor_pack, HELPER.control_pack
encoded = HELPER.canonical


def nodes():
    graph = yaml.safe_load((DEPLOY / "dsl/ltm-editor-author.yml").read_text(encoding="utf-8"))["workflow"]["graph"]
    return {row["data"]["title"]: row["data"] for row in graph["nodes"]}


def project(pack, knowledge=None):
    namespace = {}
    exec(compile(nodes()["Editor Input"]["code"], "input-node", "exec"), namespace)
    incoming = HELPER.incoming(pack)
    if knowledge is not None:
        incoming["knowledge_context"] = knowledge
    raw = encoded(incoming)
    before = copy.deepcopy(incoming)
    result = namespace["main"](raw)
    original = HELPER.ASSET.prepare(raw)
    assert {key: result[key] for key in original} == original  # Exact old v2 bytes.
    assert incoming == before
    return result, json.loads(result["selector_input"])


def content(source, value, **fields):
    source.update(fields, content=encoded(value))
    source["content_sha256"] = hashlib.sha256(source["content"].encode()).hexdigest()


def intent(pack, target="DL-42", seed=""):
    source = copy.deepcopy(pack["sources"][0])
    content(source, {"kind": "comment", "ticket_key": target, "prompt": "검토 요청", "seed_html": seed},
            source_id="intent", kind="ltm", canonical_id="ltm:user-intent:test", title="Request", url="")
    pack["sources"].append(source)


def test_actual_prepare_groups_mixed_owners_losslessly(editor_pack):
    source = editor_pack["sources"][0]
    content(source, [{"key": "DL-42", "parentKey": "DL-40", "summary": "Target", "statusName": "In Progress"},
                     {"key": "DL-40", "summary": "Parent"}, {"key": "DL-43", "parentKey": "DL-42"},
                     {"summary": "Unknown"}], kind="jira", url="")
    document = copy.deepcopy(source)
    content(document, "Review criteria", source_id="design", kind="confluence", url="https://wiki.example.local/design")
    editor_pack["sources"].append(document)
    intent(editor_pack, seed="<p>기존 30분</p>")
    before = copy.deepcopy(editor_pack)
    result, view = project(editor_pack)
    assert view["contract"] == "editor-selector-input.v1"
    full = json.loads(result["model_input"])
    assert {key: view[key] for key in ("role_id", "request", "entities", "knowledge_context")} == {
        key: full[key] for key in ("role_id", "request", "entities", "knowledge_context")}
    assert view["link_choices"] == ["design"]
    assert [s["source_id"] for s in view["sources"]] == [s["source_id"] for s in full["sources"]]
    for old, new in zip(full["sources"], view["sources"]):
        assert {k: new[k] for k in ("source_id", "kind", "title")} == {k: old[k] for k in ("source_id", "kind", "title")}
        assert set(new) == {"source_id", "kind", "title", "units"}
        assert set(new["units"]) == {"current", "context", "shared", "neither"}
        rows = [u for group in new["units"].values() for u in group]
        assert len(rows) == len({u["unit_id"] for u in rows}) == len(old["units"])
        assert {u["unit_id"]: u for u in rows} == {u["unit_id"]: u for u in old["units"]}
        for group in new["units"].values():
            ids = {u["unit_id"] for u in group}
            assert group == [u for u in old["units"] if u["unit_id"] in ids]
    jira, doc = view["sources"]
    assert {u["entity_key"] for u in jira["units"]["current"]} == {"DL-42"}
    assert {u["entity_key"] for u in jira["units"]["context"]} == {"DL-40"}
    assert {u["entity_key"] for u in jira["units"]["neither"]} == {"DL-43", ""}
    assert len(doc["units"]["shared"]) == len(full["sources"][1]["units"])
    assert view["request"]["seed_visible"] == "기존 30분" and editor_pack == before
    assert project(editor_pack)[0] == result


def test_neither_remains_available_for_history_and_conflicts(editor_pack):
    content(editor_pack["sources"][0], [{"key": "DL-43", "summary": "Past"},
                                       {"key": "DL-44", "summary": "Other"}], kind="jira", url="")
    intent(editor_pack)
    result, view = project(editor_pack)
    ids = [u["unit_id"] for u in view["sources"][0]["units"]["neither"]]
    spec = {"contract": "editor-draft-spec.v2", "status": "blocked", "mode": "comment", "target": "DL-42",
        "current_unit_ids": [], "context_unit_ids": [], "history_unit_ids": ids[:1],
        "conflicts": [{"unit_ids": ids[1:3], "blocking": True}], "required_link_source_ids": [], "missing_inputs": []}
    namespace = {}
    exec(compile(nodes()["DraftSpec Guard"]["code"], "guard-node", "exec"), namespace)
    assert namespace["main"](result["model_input"], encoded(spec), encoded(editor_pack))["route"] == "stop"


@pytest.mark.parametrize("case", ["empty", "missing_owner", "conflict", "cycle"])
def test_unknown_or_conflicting_scope_is_not_promoted(editor_pack, case):
    rows = [{"key": "DL-42", "parentKey": "DL-40"}, {"key": "DL-40", "summary": "Parent"}]
    if case == "empty": rows = [{"summary": "Unowned"}]
    elif case == "missing_owner": rows.append({"summary": "Unowned"})
    elif case == "conflict": rows.append({"key": "DL-42", "parentKey": "DL-41"})
    else: rows[1]["parentKey"] = "DL-42"
    content(editor_pack["sources"][0], rows, kind="jira")
    intent(editor_pack, target="" if case == "empty" else "DL-42")
    _, view = project(editor_pack)
    groups = view["sources"][0]["units"]
    assert all(u["entity_key"] for u in groups["current"])
    if case != "missing_owner": assert groups["context"] == []
    if case == "empty": assert groups["current"] == [] and groups["neither"]


@pytest.mark.parametrize("mutation", ["target_context", "unavailable_link"])
def test_hints_do_not_repair_invalid_selection_or_change_guard(editor_pack, mutation):
    content(editor_pack["sources"][0], {"key": "DL-42", "description": "30분 보존"}, kind="jira", url="")
    intent(editor_pack)
    result, view = project(editor_pack)
    unit = next(u for u in view["sources"][0]["units"]["current"] if u["path"].endswith("/description"))
    spec = {"contract": "editor-draft-spec.v2", "status": "ok", "mode": "comment", "target": "DL-42",
        "current_unit_ids": [unit["unit_id"]], "context_unit_ids": [], "history_unit_ids": [],
        "conflicts": [], "required_link_source_ids": [], "missing_inputs": []}
    if mutation == "target_context": spec.update(current_unit_ids=[], context_unit_ids=[unit["unit_id"]])
    else: spec["required_link_source_ids"] = ["doc"]
    assert view["link_choices"] == []
    before = copy.deepcopy(spec)
    namespace = {}
    for name in ("editor_assembler.py", "editor_draft_spec.py"):
        exec(compile((DEPLOY / "code" / name).read_text(encoding="utf-8"), name, "exec"), namespace)
    with pytest.raises(ValueError, match="^invalid_editor_draft_spec$"):
        namespace["prepare_spec"](result["model_input"], encoded(spec), encoded(editor_pack))
    assert json.loads(namespace["enforce_spec"]("{}", result["model_input"], encoded(spec), encoded(editor_pack))["candidate"])["status"] == "blocked"
    assert spec == before


def test_control_preserves_original_bytes_without_selector_decode(control_pack):
    namespace = {}
    exec(compile(nodes()["Editor Input"]["code"], "input-node", "exec"), namespace)
    namespace["_editor_selector_projection"] = lambda _: pytest.fail("control must not be grouped")
    raw = "  " + encoded(HELPER.incoming(control_pack)) + "\n"
    assert namespace["main"](raw) == {"model_input": raw, "route": "control", "selector_input": "{}"}


@pytest.mark.parametrize("case_id", ["CMP2", "CMP3", "CMP6", "CMP8", "CMP9"])
def test_frozen_cases_fit_max_utf8_knowledge(case_id, monkeypatch):
    from datetime import datetime, timezone
    from tools.maxis_editor_eval_cases import load_case
    monkeypatch.syspath_prepend(str(DEPLOY))
    runner = load(DEPLOY / "run_editor_evals.py")
    pack = runner.build_pack(load_case(case_id), "a" * 64, "req-budget",
                            datetime(2026, 9, 15, tzinfo=timezone.utc)).model_dump(mode="json")
    knowledge = [{"document_name": "명" * 33 + "a", "content": "한" * 256},
                 {"document_name": "명" * 33 + "b", "content": "글" * 85 + "x"}]
    result, view = project(pack, knowledge)
    assert len(result["selector_input"].encode("utf-8")) <= 9000
    assert view["knowledge_context"] == knowledge
    assert sum(len(v) for s in view["sources"] for v in s["units"].values()) == 45


@pytest.mark.parametrize("count", [96, 97])
def test_catalog_limit_is_preserved_without_truncation(editor_pack, count):
    content(editor_pack["sources"][0], ["x"] * (count - 1), title="t", url="")
    if count == 97:
        with pytest.raises(ValueError, match="^invalid_editor_input$"): project(editor_pack, [])
    else:
        result, view = project(editor_pack, [])
        assert sum(len(v) for s in view["sources"] for v in s["units"].values()) == 96
        assert len(result["selector_input"].encode()) <= 9000


def test_ten_sources_are_not_duplicated_and_links_stay_unique(editor_pack):
    original = copy.deepcopy(editor_pack["sources"][0])
    editor_pack["sources"] = []
    for i in range(10):
        source = copy.deepcopy(original)
        content(source, "x", source_id=f"s{i}", title="t", url=f"https://wiki.example.local/{i}")
        editor_pack["sources"].append(source)
    _, view = project(editor_pack, [])
    assert [s["source_id"] for s in view["sources"]] == view["link_choices"] == [f"s{i}" for i in range(10)]


def test_projection_overflow_is_content_free_no_fallback(editor_pack):
    # Old v2 is valid near the cap; grouping must not truncate or return old input.
    content(editor_pack["sources"][0], ["x"] * 80, title="t", url="")
    intent(editor_pack, seed="<p>" + "한" * 130 + "</p>")
    namespace = {}
    exec(compile(nodes()["Editor Input"]["code"], "input-node", "exec"), namespace)
    incoming = HELPER.incoming(editor_pack)
    incoming["knowledge_context"] = []
    # Locate a UTF-8 boundary without assuming platform line endings.
    for length in range(500):
        incoming["context_pack"]["sources"][0]["title"] = "t" + "한" * length
        raw = encoded(incoming)
        try: full = HELPER.ASSET.prepare(raw)["model_input"]
        except ValueError: break
        if len(full.encode()) > 8960:
            with pytest.raises(ValueError, match="^invalid_editor_input$"): namespace["main"](raw)
            return
    pytest.fail("fixture did not reach a valid v2 near 9000 UTF-8 bytes")


@pytest.mark.parametrize("size", [8999, 9000, 9001])
def test_selector_port_exact_byte_limit_with_injected_prepare_output(size):
    """Pure wrapper boundary: injected prepare output, NOT EvidencePack integration."""
    namespace = {}
    exec(compile(nodes()["Editor Input"]["code"], "input-node", "exec"), namespace)
    view = {"contract": "editor-input.v2", "role_id": "editor_author", "entities": [], "sources": [],
            "knowledge_context": [], "request": {"target": "", "prompt": ""}}
    overhead = len(encoded(namespace["_editor_selector_projection"](view)).encode("utf-8"))
    count, remainder = divmod(size - overhead, 3)
    view["request"]["prompt"] = "한" * count + "x" * remainder
    expected = encoded(namespace["_editor_selector_projection"](view))
    assert len(expected.encode("utf-8")) == size
    prepared = {"model_input": encoded(view), "route": "comment"}
    before = copy.deepcopy(prepared)
    namespace["prepare"] = lambda _: prepared
    if size > 9000:
        with pytest.raises(ValueError, match="^invalid_editor_input$"):
            namespace["main"]("injected-prepare-output-only")
    else:
        assert namespace["main"]("injected-prepare-output-only") == {**prepared, "selector_input": expected}
    assert prepared == before
