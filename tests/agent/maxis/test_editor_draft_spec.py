"""Strict internal selection closure, independent of paid semantic evaluation."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASSET = {}
for filename in ("editor_assembler.py", "editor_draft_spec.py"):
    exec(compile((ROOT / "deploy/maxis-dify/code" / filename).read_text(encoding="utf-8"), filename, "exec"), ASSET)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.fixture
def sample(monkeypatch):
    # Already-validated Editor Input; regression focuses on the next boundary.
    sources = [
        {"source_id": "ticket", "kind": "jira", "title": "대상", "available_link": True, "units": [
            {"unit_id": "u_target", "entity_key": "DL-42", "path": "/summary", "text": "다운스트림 연동"},
            {"unit_id": "u_parent", "entity_key": "DL-40", "path": "/description", "text": "전체 탐색 기능"},
            {"unit_id": "u_sibling", "entity_key": "DL-41", "path": "/summary", "text": "업스트림 완료"}]},
        {"source_id": "design", "kind": "confluence", "title": "설계 문서", "available_link": True, "units": [
            {"unit_id": "u_constraint", "entity_key": "", "path": "/scope", "text": "조회 전용"},
            {"unit_id": "u_history", "entity_key": "", "path": "/history", "text": "이전 완료 기록"},
            {"unit_id": "u_current", "entity_key": "", "path": "/status", "text": "현재 진행 중"}]},
        {"source_id": "other", "kind": "confluence", "title": "관련 없음", "available_link": False, "units": [
            {"unit_id": "u_other", "entity_key": "", "path": "/body", "text": "선택하지 않은 자료"}]},
    ]
    view = {"contract": "editor-input.v1", "role_id": "editor_author",
            "request": {"kind": "description", "target": "DL-42", "prompt": "본문 작성", "seed_visible": "",
                        "source_ids": ["intent"]}, "draft_contract": ASSET["_draft_contract"]("description"),
            "entities": [{"key": "DL-40", "relation": "parent", "parent_conflict": False}],
            "knowledge_context": [{"document_name": "policy", "content": "정책은 사실이 아니다."}], "sources": sources}
    spec = {"contract": "editor-draft-spec.v1", "status": "ok", "mode": "description", "target": "DL-42",
            "target_unit_ids": ["u_target"], "context_unit_ids": ["u_parent"],
            "constraint_unit_ids": ["u_constraint"], "history_unit_ids": ["u_history"],
            "conflicts": [], "required_link_source_ids": ["design"], "missing_inputs": []}
    plan = {"contract": "editor-plan.v2", "status": "ok", "missing_inputs": [], "sections": [
        {"section": "background", "blocks": [{"type": "fact", "unit_id": "u_parent", "tag": "p"}]},
        {"section": "scope", "blocks": [{"type": "fact", "unit_id": "u_target", "tag": "p"}]},
        {"section": "work", "blocks": [{"type": "proposal", "text": "현재 연동을 점검한다.", "source_ids": ["ticket"], "tag": "li"}]},
        {"section": "dod", "blocks": [{"type": "fact", "unit_id": "u_constraint", "tag": "p"},
                                       {"type": "link", "source_id": "design"}]},
    ]}
    # Isolate contract tests from the original-pack renderer. Integration tests
    # below exercise the real dependency helper without this fixture/mock.
    monkeypatch.setitem(ASSET, "_spec_link_dependencies", lambda view, pack: {
        unit["unit_id"]: [] for source in view["sources"] for unit in source["units"]})
    return view, spec, plan


def enforce(sample):
    view, spec, plan = sample
    return json.loads(ASSET["enforce_spec"](encoded(plan), encoded(view), encoded(spec), "{}")["candidate"])


def test_narrow_writer_view_excludes_history_conflicts_unselected_and_knowledge(sample):
    view, spec, plan = sample
    original = copy.deepcopy(sample)
    first = ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")
    assert first == ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")
    assert first["route"] == "description"
    writer = json.loads(first["model_input"])
    assert writer["contract"] == "editor-writer-input.v1"
    assert "knowledge_context" not in writer and "entities" not in writer
    assert {u["unit_id"] for s in writer["sources"] for u in s["units"]} == {"u_target", "u_parent", "u_constraint"}
    assert {s["source_id"] for s in writer["sources"]} == {"ticket", "design"}
    assert enforce(sample) == plan
    assert sample == original


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "cross_bucket", "mode", "target", "parent_target",
                                     "sibling_target", "sibling_context", "cycle_context", "extra", "unavailable_link",
                                     "intent_link", "blocking_conflict", "selected_conflict", "nonbool", "missing_with_ok"])
def test_invalid_selection_is_content_free_and_never_reaches_writer(sample, mutation):
    view, spec, _ = sample
    if mutation == "unknown": spec["target_unit_ids"] = ["private-unknown"]
    elif mutation == "duplicate": spec["target_unit_ids"] *= 2
    elif mutation == "cross_bucket": spec["constraint_unit_ids"] += spec["target_unit_ids"]
    elif mutation == "mode": spec["mode"] = "comment"
    elif mutation == "target": spec["target"] = "DL-40"
    elif mutation == "parent_target": spec["target_unit_ids"] = ["u_parent"]; spec["context_unit_ids"] = []
    elif mutation == "sibling_target": spec["target_unit_ids"] = ["u_sibling"]
    elif mutation == "sibling_context": spec["context_unit_ids"] = ["u_sibling"]
    elif mutation == "cycle_context": view["entities"][0]["parent_conflict"] = True
    elif mutation == "extra": spec["private-extra"] = "secret"
    elif mutation == "unavailable_link": spec["required_link_source_ids"] = ["other"]
    elif mutation == "intent_link": spec["required_link_source_ids"] = ["intent"]
    elif mutation == "blocking_conflict": spec["conflicts"] = [{"unit_ids": ["u_history", "u_current"], "blocking": True}]
    elif mutation == "selected_conflict": spec["conflicts"] = [{"unit_ids": ["u_target", "u_current"], "blocking": False}]
    elif mutation == "nonbool": spec["conflicts"] = [{"unit_ids": ["u_history", "u_current"], "blocking": 0}]
    else: spec["missing_inputs"] = ["이미 알고 있는 값을 다시 입력해 주세요."]
    with pytest.raises(ValueError, match="^invalid_editor_draft_spec$"):
        ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")
    assert enforce(sample) == {"contract": "editor-plan.v2", "status": "blocked", "sections": [], "missing_inputs": []}


@pytest.mark.parametrize("unit_id", ["u_parent", "u_history", "u_sibling", "u_other", "u_current"])
def test_writer_cannot_promote_context_or_unselected_units_into_work(sample, unit_id):
    sample[2]["sections"][2]["blocks"] = [{"type": "fact", "unit_id": unit_id, "tag": "p"}]
    assert enforce(sample)["status"] == "blocked"


@pytest.mark.parametrize("mutation", ["missing_link", "wrong_valid_link", "unselected_proposal_source", "invented_plan_key", "reopen_interview"])
def test_writer_closure_is_checked_even_when_original_source_exists(sample, mutation):
    plan = sample[2]
    if mutation == "missing_link": plan["sections"][-1]["blocks"].pop()
    elif mutation == "wrong_valid_link": plan["sections"][-1]["blocks"][-1]["source_id"] = "ticket"
    elif mutation == "unselected_proposal_source": plan["sections"][2]["blocks"][0]["source_ids"] = ["other"]
    elif mutation == "invented_plan_key": plan["raw_html"] = "private"
    else: plan.update(status="needs_input", sections=[], missing_inputs=["다시 확인해 주세요."])
    assert enforce(sample)["status"] == "blocked"


@pytest.mark.parametrize("section_index", [0, 3])
def test_each_required_link_can_only_be_used_once_across_all_sections(sample, section_index):
    sample[2]["sections"][section_index]["blocks"].append({"type": "link", "source_id": "design"})
    assert enforce(sample)["status"] == "blocked"


@pytest.mark.parametrize("status", ["needs_input", "blocked"])
def test_non_ok_spec_skips_writer_and_cannot_be_changed_by_later_output(sample, status):
    view, spec, _ = sample
    spec.update(status=status, target_unit_ids=[], context_unit_ids=[], constraint_unit_ids=[], required_link_source_ids=[],
                missing_inputs=["실제 p95 값과 비교 기준을 알려주세요."] if status == "needs_input" else [])
    spec["conflicts"] = [{"unit_ids": ["u_history", "u_current"], "blocking": True}]
    prepared = ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")
    assert prepared["route"] == "stop" and prepared["model_input"] == "{}"
    terminal = json.loads(prepared["candidate"])
    assert terminal["status"] == status and terminal["sections"] == []
    assert ASSET["enforce_spec"](prepared["candidate"], encoded(view), encoded(spec), "{}")["candidate"] == prepared["candidate"]
    assert enforce(sample)["status"] == "blocked"


def test_conflicted_and_historical_units_are_diagnostic_not_writer_facts(sample):
    view, spec, _ = sample
    spec["conflicts"] = [{"unit_ids": ["u_history", "u_current"], "blocking": False}]
    prepared = json.loads(ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")["model_input"])
    assert "이전 완료 기록" not in encoded(prepared) and "현재 진행 중" not in encoded(prepared)
    assert enforce(sample)["status"] == "ok"


@pytest.mark.parametrize("raw", ['{"status":"ok","status":"ok"}', '{"status":NaN}', '```json\n{}\n```', '[]'])
def test_noncanonical_model_json_is_rejected(sample, raw):
    with pytest.raises(ValueError, match="^invalid_editor_draft_spec$"):
        ASSET["prepare_spec"](encoded(sample[0]), raw, "{}")


def test_canonical_schema_accepts_the_same_contract(sample):
    import jsonschema
    schema = json.loads((ROOT / "deploy/maxis-dify/contracts/editor-draft-spec.v1.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(sample[1])


@pytest.fixture
def original_pack_case(context_guard, now):
    from app.agent.maxis import EditorDraftV1
    import hashlib

    helper_spec = importlib.util.spec_from_file_location("draftspec_guard_helpers", Path(__file__).with_name("test_editor_routing.py"))
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    schema = EditorDraftV1.model_json_schema()
    schema["properties"]["mode"] = {"type": "string", "const": "comment"}
    pack = context_guard.build(request_id="req-spec", use_case="editor_author", query="댓글 작성",
        knowledge_manifest_sha256="a" * 64, payload_schema=schema, user_identity="test-reviewer", requested_at=now,
        sources=[{"source_id": "doc", "kind": "confluence", "canonical_id": "design", "scope": "DATALAKE",
                  "title": "설계 기준", "url": "https://wiki.example.local/design", "content": "조회 전용이다.",
                  "retrieved_at": now}]).model_dump(mode="json")
    intent = copy.deepcopy(pack["sources"][0])
    intent.update(source_id="intent", kind="ltm", canonical_id="ltm:user-intent:spec", title="Author request", url="",
                  content=encoded({"kind": "comment", "prompt": "검토 요청", "seed_html": "<p>검토 부탁드립니다.</p>"}))
    intent["content_sha256"] = hashlib.sha256(intent["content"].encode()).hexdigest()
    pack["sources"].append(intent)
    return pack, helper


def selected_plan(pack, helper):
    view = ASSET["prepare"](encoded(helper.incoming(pack)))["model_input"]
    source = json.loads(view)["sources"][0]
    unit_id = next(unit["unit_id"] for unit in source["units"] if unit["path"] == "/content")
    selection = {"contract": "editor-draft-spec.v1", "status": "ok", "mode": "comment", "target": "",
                 "target_unit_ids": [], "context_unit_ids": [], "constraint_unit_ids": [unit_id],
                 "history_unit_ids": [], "conflicts": [], "required_link_source_ids": ["doc"], "missing_inputs": []}
    plan = {"contract": "editor-plan.v2", "status": "ok", "missing_inputs": [], "sections": [{"section": "comment", "blocks": [
        {"type": "fact", "unit_id": unit_id, "tag": "p"}, {"type": "link", "source_id": "doc"}]}]}
    return view, selection, plan


def test_selected_plan_and_seed_remain_bound_to_original_pack_through_both_guards(original_pack_case):
    import hashlib
    pack, helper = original_pack_case
    before = copy.deepcopy(pack)
    view, selection, plan = selected_plan(pack, helper)
    prepared = ASSET["prepare_spec"](view, encoded(selection), encoded(pack))
    assert prepared["route"] == "comment"
    purpose = json.loads(prepared["model_input"])["draft_contract"]["purpose"]
    assert "needs_input" not in purpose and "missing_inputs" not in purpose
    constrained = ASSET["enforce_spec"](encoded(plan), view, encoded(selection), encoded(pack))["candidate"]
    remote, payload = helper.both_guards(pack, helper.assembled(pack, constrained))
    assert remote.status == "ok" and payload["content_html"].count("검토 부탁드립니다.") == 1
    assert 'href="https://wiki.example.local/design"' in payload["content_html"]
    assert pack == before
    # IDs are recomputed from the original pack; a selection cannot survive changed evidence.
    pack["sources"][0]["content"] = "새로운 근거다."
    pack["sources"][0]["content_sha256"] = hashlib.sha256("새로운 근거다.".encode()).hexdigest()
    changed_view = ASSET["prepare"](encoded(helper.incoming(pack)))["model_input"]
    with pytest.raises(ValueError, match="^invalid_editor_draft_spec$"):
        ASSET["prepare_spec"](changed_view, encoded(selection), encoded(pack))
    with pytest.raises(ValueError, match="^invalid_editor_draft_spec$"):
        ASSET["prepare_spec"](view, encoded(selection), encoded(pack))


@pytest.mark.parametrize("scenario", ["inline_only", "unselected", "explicit_duplicate", "inline_duplicate", "self_link"])
def test_implicit_links_obey_original_pack_selection_and_multiplicity(original_pack_case, scenario):
    import hashlib
    pack, helper = original_pack_case
    if scenario != "self_link":
        linked = copy.deepcopy(pack["sources"][0])
        linked.update(source_id="linked", canonical_id="reference", title="검토 문서",
                      url="https://wiki.example.local/reference", content="검토 기준이다.")
        linked["content_sha256"] = hashlib.sha256(linked["content"].encode()).hexdigest()
        pack["sources"].append(linked)
    else:
        linked = pack["sources"][0]
    body = "참조 " + linked["url"]
    if scenario == "inline_duplicate":
        body += " 다시 " + linked["url"]
    pack["sources"][0]["content"] = body
    pack["sources"][0]["content_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    before = copy.deepcopy(pack)
    view, selection, plan = selected_plan(pack, helper)
    selection["required_link_source_ids"] = [] if scenario == "unselected" else [linked["source_id"]]
    blocks = plan["sections"][0]["blocks"]
    blocks.pop()  # The implicit anchor supplies the one required link.
    if scenario == "explicit_duplicate":
        blocks.append({"type": "link", "source_id": linked["source_id"]})
    projected = json.loads(ASSET["prepare_spec"](view, encoded(selection), encoded(pack))["model_input"])
    selected_unit = projected["sources"][0]["units"][0]
    assert selected_unit["link_source_ids"] == [linked["source_id"]] * (2 if scenario == "inline_duplicate" else 1)
    constrained = ASSET["enforce_spec"](encoded(plan), view, encoded(selection), encoded(pack))["candidate"]
    remote, payload = helper.both_guards(pack, helper.assembled(pack, constrained))
    if scenario in {"inline_only", "self_link"}:
        assert remote.status == "ok"
        assert payload["content_html"].count('href="' + linked["url"] + '"') == 1
        assert payload["content_html"].count("검토 부탁드립니다.") == 1
        assert linked["source_id"] in payload["source_ids"]
    else:
        assert remote.status == "blocked" and payload is None
    assert pack == before
