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
    spec = {"contract": "editor-draft-spec.v2", "status": "ok", "mode": "description", "target": "DL-42",
            "current_unit_ids": ["u_target", "u_constraint"], "context_unit_ids": ["u_parent"],
            "history_unit_ids": ["u_history"],
            "conflicts": [], "required_link_source_ids": ["design"], "missing_inputs": []}
    plan = {"contract": "editor-writing.v1", "sections": [
        {"section": "background", "blocks": [{"type": "fact", "unit_id": "u_parent", "tag": "p"}]},
        {"section": "scope", "blocks": [{"type": "fact", "unit_id": "u_target", "tag": "p"}]},
        {"section": "work", "blocks": [{"type": "proposal", "text": "현재 연동을 점검한다.", "tag": "li"}]},
        {"section": "dod", "blocks": [{"type": "fact", "unit_id": "u_constraint", "tag": "p"}]},
    ]}
    # Isolate contract tests from the original-pack renderer. Integration tests
    # below exercise the real dependency helper without this fixture/mock.
    monkeypatch.setitem(ASSET, "_spec_link_dependencies", lambda view, pack: {
        unit["unit_id"]: [] for source in view["sources"] for unit in source["units"]})
    return view, spec, plan


def enforce(sample):
    view, spec, plan = sample
    return json.loads(ASSET["enforce_spec"](encoded(plan), encoded(view), encoded(spec), "{}")["candidate"])


@pytest.mark.parametrize("mode", ["description", "comment"])
def test_v1_successful_writer_input_and_compiled_plan_are_preserved(sample, mode):
    """Golden outputs verified on v1 before the selection-only v2 migration."""
    view, spec, plan = sample
    view["request"]["kind"] = spec["mode"] = mode
    view["draft_contract"] = ASSET["_draft_contract"](mode)
    sections = ["background", "scope", "work", "dod"] if mode == "description" else ["comment"]
    if mode == "comment":
        plan["sections"] = [{"section": "comment", "blocks": [
            {"type": "fact", "unit_id": "u_target", "tag": "p"},
            {"type": "fact", "unit_id": "u_constraint", "tag": "li"},
            {"type": "proposal", "text": "현재 연동을 점검한다.", "tag": "p"}]}]
    expected_units = [{"unit_id": "u_target", "text": "다운스트림 연동", "allowed_sections": sections}]
    if mode == "description":
        expected_units.append({"unit_id": "u_parent", "text": "전체 탐색 기능", "allowed_sections": ["background"]})
    expected_units.append({"unit_id": "u_constraint", "text": "조회 전용", "allowed_sections": sections})
    expected_writer = {"contract": "editor-writer-input.v2",
        "request": {"kind": mode, "target": "DL-42", "prompt": "본문 작성", "seed_visible": ""},
        "draft_contract": {"section_order": sections, "max_blocks": 31, "purpose": (
            "Write only the admitted target purpose, work and DoD; parent is background."
            if mode == "description" else
            "Write one admitted comment using selected facts for known people and criteria; server inserts seed and links.")},
        "units": expected_units}
    expected_compiled = {"contract": "editor-plan.v2", "status": "ok",
                         "sections": copy.deepcopy(plan["sections"]), "missing_inputs": []}
    for section in expected_compiled["sections"]:
        for block in section["blocks"]:
            if block["type"] == "proposal":
                block["source_ids"] = ["intent"]
    expected_compiled["sections"][-1]["blocks"].append({"type": "link", "source_id": "design"})
    prepared = ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")
    assert prepared == {"model_input": encoded(expected_writer), "route": mode, "candidate": ""}
    assert enforce(sample) == expected_compiled


@pytest.mark.parametrize("duplicate", [False, True])
def test_target_description_needs_one_current_selection_not_two_labels(sample, duplicate):
    """CMP3 shape: one target description can express both fact and constraint."""
    view, spec, plan = sample
    view["sources"][0]["units"].append({"unit_id": "u_target_description", "entity_key": "DL-42",
                                       "path": "/description", "text": "30분 주기 조회만 허용"})
    spec["current_unit_ids"].append("u_target_description")
    plan["sections"][3]["blocks"] = [{"type": "fact", "unit_id": "u_target_description", "tag": "p"}]
    if duplicate:
        spec["current_unit_ids"].append("u_target_description")
        with pytest.raises(ValueError, match="^invalid_editor_draft_spec$"):
            ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")
        assert enforce(sample)["status"] == "blocked"
    else:
        writer = json.loads(ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")["model_input"])
        assert sum(row["unit_id"] == "u_target_description" for row in writer["units"]) == 1
        assert enforce(sample)["status"] == "ok"


def test_v1_selection_has_no_runtime_fallback(sample):
    view, spec, _ = sample
    spec["contract"] = "editor-draft-spec.v1"
    spec["target_unit_ids"] = ["u_target"]
    spec["constraint_unit_ids"] = ["u_constraint"]
    del spec["current_unit_ids"]
    with pytest.raises(ValueError, match="^invalid_editor_draft_spec$"):
        ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")
    assert enforce(sample)["status"] == "blocked"


def test_narrow_writer_view_excludes_history_conflicts_unselected_and_knowledge(sample):
    view, spec, plan = sample
    original = copy.deepcopy(sample)
    first = ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")
    assert first == ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")
    assert first["route"] == "description"
    writer = json.loads(first["model_input"])
    assert writer["contract"] == "editor-writer-input.v2"
    assert "knowledge_context" not in writer and "entities" not in writer
    assert {u["unit_id"] for u in writer["units"]} == {"u_target", "u_parent", "u_constraint"}
    assert "source_ids" not in encoded(writer) and "required_link" not in encoded(writer)
    assert writer["draft_contract"]["max_blocks"] == 31  # Reserve the server-owned link.
    parent = next(u for u in writer["units"] if u["unit_id"] == "u_parent")
    assert parent["allowed_sections"] == ["background"]
    compiled = enforce(sample)
    assert compiled["contract"] == "editor-plan.v2" and compiled["status"] == "ok"
    assert compiled["sections"][2]["blocks"][0]["source_ids"] == ["intent"]
    assert compiled["sections"][-1]["blocks"][-1] == {"type": "link", "source_id": "design"}
    assert sample == original


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "cross_context", "cross_history", "mode", "target",
                                     "parent_current", "sibling_current", "sibling_context", "cycle_context", "extra",
                                     "unavailable_link", "intent_link", "blocking_conflict", "selected_conflict",
                                     "nonbool", "missing_with_ok", "legacy_target", "legacy_constraint"])
def test_invalid_selection_is_content_free_and_never_reaches_writer(sample, mutation):
    view, spec, _ = sample
    if mutation == "unknown": spec["current_unit_ids"] = ["private-unknown"]
    elif mutation == "duplicate": spec["current_unit_ids"] *= 2
    elif mutation == "cross_context": spec["context_unit_ids"].append("u_constraint")
    elif mutation == "cross_history": spec["history_unit_ids"].append("u_target")
    elif mutation == "mode": spec["mode"] = "comment"
    elif mutation == "target": spec["target"] = "DL-40"
    elif mutation == "parent_current": spec["current_unit_ids"] = ["u_parent"]; spec["context_unit_ids"] = []
    elif mutation == "sibling_current": spec["current_unit_ids"] = ["u_sibling"]
    elif mutation == "sibling_context": spec["context_unit_ids"] = ["u_sibling"]
    elif mutation == "legacy_target": spec["target_unit_ids"] = []
    elif mutation == "legacy_constraint": spec["constraint_unit_ids"] = []
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


@pytest.mark.parametrize("blocking", ["false", "true", "False", "True"])
def test_conflict_boolean_strings_are_not_coerced(sample, blocking):
    view, spec, _ = sample
    spec["conflicts"] = [{"unit_ids": ["u_history", "u_current"], "blocking": blocking}]
    with pytest.raises(ValueError, match="^invalid_editor_draft_spec$"):
        ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")
    assert enforce(sample)["status"] == "blocked"


@pytest.mark.parametrize("unit_id", ["u_parent", "u_history", "u_sibling", "u_other", "u_current"])
def test_writer_cannot_promote_context_or_unselected_units_into_work(sample, unit_id):
    sample[2]["sections"][2]["blocks"] = [{"type": "fact", "unit_id": unit_id, "tag": "p"}]
    assert enforce(sample)["status"] == "blocked"


@pytest.mark.parametrize("mutation", ["explicit_link", "unselected_proposal_source", "unit_as_source", "invented_plan_key", "reopen_interview"])
def test_writer_closure_is_checked_even_when_original_source_exists(sample, mutation):
    plan = sample[2]
    if mutation == "explicit_link": plan["sections"][-1]["blocks"].append({"type": "link", "source_id": "design"})
    elif mutation == "unselected_proposal_source": plan["sections"][2]["blocks"][0]["source_ids"] = ["other"]
    elif mutation == "unit_as_source": plan["sections"][2]["blocks"][0]["source_ids"] = ["u_target"]
    elif mutation == "invented_plan_key": plan["raw_html"] = "private"
    else: plan.update(status="needs_input", sections=[], missing_inputs=["다시 확인해 주세요."])
    assert enforce(sample)["status"] == "blocked"


def test_proposals_only_cite_intent_not_parent_or_selected_fact_sources(sample):
    view, spec, plan = sample
    ticket = view["sources"][0]
    parent = ticket["units"].pop(1)
    view["sources"].append({"source_id": "parent", "kind": "jira", "title": "부모",
                            "available_link": True, "units": [parent]})
    writer = json.loads(ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")["model_input"])
    assert "parent" not in writer["request"] and "sources" not in writer
    assert enforce(sample)["sections"][2]["blocks"][0]["source_ids"] == ["intent"]
    plan["sections"][2]["blocks"][0]["source_ids"] = ["parent"]
    assert enforce(sample)["status"] == "blocked"


@pytest.mark.parametrize("section_index", [0, 3])
def test_each_required_link_can_only_be_used_once_across_all_sections(sample, section_index):
    sample[2]["sections"][section_index]["blocks"].append({"type": "link", "source_id": "design"})
    assert enforce(sample)["status"] == "blocked"


@pytest.mark.parametrize("text", ["https://wiki.example.local/forged", "[[person:abcdef123456]] 확인 요청", "@담당자 확인 요청"])
def test_writer_prose_cannot_author_link_or_person_syntax(sample, text):
    sample[2]["sections"][2]["blocks"][0]["text"] = text
    assert enforce(sample)["status"] == "blocked"


def test_comment_projection_omits_parent_context_even_if_selected(sample):
    view, spec, _ = sample
    view["request"]["kind"] = spec["mode"] = "comment"
    view["draft_contract"] = ASSET["_draft_contract"]("comment")
    writer = json.loads(ASSET["prepare_spec"](encoded(view), encoded(spec), "{}")["model_input"])
    assert {u["unit_id"] for u in writer["units"]} == {"u_target", "u_constraint"}


def test_missing_intent_cannot_be_replaced_by_selected_sources(sample):
    sample[0]["request"]["source_ids"] = []
    assert enforce(sample)["status"] == "blocked"


def test_inserted_links_count_toward_original_assembler_block_limit(sample):
    sample[2]["sections"][2]["blocks"] *= 29  # 32 writer blocks plus one server link.
    assert enforce(sample)["status"] == "blocked"
    sample[2]["sections"][2]["blocks"].pop()
    assert enforce(sample)["status"] == "ok"


@pytest.mark.parametrize("status", ["needs_input", "blocked"])
def test_non_ok_spec_skips_writer_and_cannot_be_changed_by_later_output(sample, status):
    view, spec, _ = sample
    spec.update(status=status, current_unit_ids=[], context_unit_ids=[], required_link_source_ids=[],
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
    schema = json.loads((ROOT / "deploy/maxis-dify/contracts/editor-draft-spec.v2.schema.json").read_text(encoding="utf-8"))
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
    selection = {"contract": "editor-draft-spec.v2", "status": "ok", "mode": "comment", "target": "",
                 "current_unit_ids": [unit_id], "context_unit_ids": [],
                 "history_unit_ids": [], "conflicts": [], "required_link_source_ids": ["doc"], "missing_inputs": []}
    plan = {"contract": "editor-writing.v1", "sections": [{"section": "comment", "blocks": [
        {"type": "fact", "unit_id": unit_id, "tag": "p"}]}]}
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


@pytest.mark.parametrize("mutation", ["valid", "forged_source", "forged_person", "raw_mention", "raw_url", "repeat_seed"])
def test_compiled_review_through_original_assembler_and_both_guards(original_pack_case, mutation):
    import hashlib
    pack, helper = original_pack_case
    marker = "[[person:123456789abc]]"
    pack["sources"][0]["content"] = "검토자 " + marker + " — 2홉 100노드에서 조회 기준을 검토한다."
    pack["sources"][0]["content_sha256"] = hashlib.sha256(pack["sources"][0]["content"].encode()).hexdigest()
    view, selection, plan = selected_plan(pack, helper)
    proposal = {"type": "proposal", "text": "변경 범위와 제외 사항의 검토를 요청합니다.", "tag": "p"}
    if mutation == "forged_source": proposal["source_ids"] = ["doc"]
    elif mutation == "forged_person": proposal["text"] = "[[person:aaaaaaaaaaaa]] 검토 요청"
    elif mutation == "raw_mention": proposal["text"] = "@담당자 검토 요청"
    elif mutation == "raw_url": proposal["text"] = "https://wiki.example.local/design 검토 요청"
    elif mutation == "repeat_seed": proposal["text"] = "검토 부탁드립니다."
    plan["sections"][0]["blocks"].append(proposal)
    constrained = ASSET["enforce_spec"](encoded(plan), view, encoded(selection), encoded(pack))["candidate"]
    remote, payload = helper.both_guards(pack, helper.assembled(pack, constrained))
    if mutation != "valid":
        assert remote.status == "blocked" and payload is None
        return
    assert remote.status == "ok" and payload["content_html"].count(marker) == 1
    assert payload["content_html"].count("검토 부탁드립니다.") == 1
    assert "2홉 100노드" in payload["content_html"] and payload["content_html"].count("<a ") == 1
    compiled = json.loads(constrained)
    assert compiled["sections"][0]["blocks"][1]["source_ids"] == ["intent"]
    assert compiled["sections"][0]["blocks"][0] == plan["sections"][0]["blocks"][0]


def test_valid_missing_measurement_skips_writer_through_both_guards(original_pack_case):
    pack, helper = original_pack_case
    view, selection, _ = selected_plan(pack, helper)
    selection.update(status="needs_input", current_unit_ids=[], required_link_source_ids=[],
                     missing_inputs=["측정 p95 값과 비교 기준을 알려주세요."])
    stopped = ASSET["prepare_spec"](view, encoded(selection), encoded(pack))
    assert stopped["route"] == "stop" and stopped["model_input"] == "{}"
    constrained = ASSET["enforce_spec"](stopped["candidate"], view, encoded(selection), encoded(pack))["candidate"]
    remote, payload = helper.both_guards(pack, helper.assembled(pack, constrained))
    assert remote.status == "needs_input" and payload is None
    assert list(remote.missing_inputs) == selection["missing_inputs"]


def test_wrapper_title_not_offered_to_selector_and_parent_scope_guard_stays_closed(original_pack_case):
    """Minimize the native failure: no parent, Jira envelope title has no owner."""
    import hashlib
    pack, helper = original_pack_case
    source, intent = pack["sources"]
    source.update(kind="jira", canonical_id="DL-42", title="Jira result wrapper", url="",
                  content=encoded({"complete": True, "issues": [{"key": "DL-42",
                      "summary": "대상 작업", "description": "30분 주기 보존"}], "total": 1}))
    request = json.loads(intent["content"])
    request["ticket_key"] = "DL-42"
    intent["content"] = encoded(request)
    for row in pack["sources"]:
        row["content_sha256"] = hashlib.sha256(row["content"].encode()).hexdigest()
    before = copy.deepcopy(pack)

    view = ASSET["prepare"](encoded(helper.incoming(pack)))["model_input"]
    projected = json.loads(view)
    units = projected["sources"][0]["units"]
    assert all(row["path"] != "/title" for row in units)
    assert projected["request"]["seed_visible"] == "검토 부탁드립니다."
    unit_id = next(row["unit_id"] for row in units if row["text"] == "30분 주기 보존")
    selection = {"contract": "editor-draft-spec.v2", "status": "ok", "mode": "comment", "target": "DL-42",
                 "current_unit_ids": [unit_id], "context_unit_ids": [],
                 "history_unit_ids": [], "conflicts": [], "required_link_source_ids": [], "missing_inputs": []}
    prepared = ASSET["prepare_spec"](view, encoded(selection), encoded(pack))
    assert prepared["route"] == "comment"
    assert json.loads(prepared["model_input"])["units"][0]["text"] == "30분 주기 보존"
    # Actual target facts cannot be relabeled as parent context to bypass the guard.
    selection.update(current_unit_ids=[], context_unit_ids=[unit_id])
    with pytest.raises(ValueError, match="^invalid_editor_draft_spec$"):
        ASSET["prepare_spec"](view, encoded(selection), encoded(pack))
    assert pack == before


@pytest.mark.parametrize("constraint_owner", ["target", "parent", "sibling"])
def test_description_family_scope_through_original_pack_and_both_guards(original_pack_case, constraint_owner):
    import hashlib
    from app.agent.maxis import EditorDraftV1
    pack, helper = original_pack_case
    schema = EditorDraftV1.model_json_schema()
    schema["properties"]["mode"] = {"type": "string", "const": "description"}
    helper.set_schema(pack, schema)
    pack["sources"][0].update(kind="jira", content=encoded({"issues": [
        {"key": "DL-42", "parentKey": "DL-40", "summary": "다운스트림 연동", "description": "조회 전용"},
        {"key": "DL-40", "summary": "전체 탐색 기능"},
        {"key": "DL-41", "parentKey": "DL-40", "summary": "업스트림 완료"}]}))
    pack["sources"][1]["content"] = encoded({"kind": "description", "ticket_key": "DL-42", "prompt": "본문 작성"})
    for source in pack["sources"]:
        source["content_sha256"] = hashlib.sha256(source["content"].encode()).hexdigest()
    before = copy.deepcopy(pack)
    view = ASSET["prepare"](encoded(helper.incoming(pack)))["model_input"]
    units = {u["text"]: u["unit_id"] for u in json.loads(view)["sources"][0]["units"]}
    constraint = {"target": "조회 전용", "parent": "전체 탐색 기능", "sibling": "업스트림 완료"}[constraint_owner]
    selection = {"contract": "editor-draft-spec.v2", "status": "ok", "mode": "description", "target": "DL-42",
                 "current_unit_ids": [units["다운스트림 연동"], units[constraint]],
                 "context_unit_ids": [units["전체 탐색 기능"]] if constraint_owner != "parent" else [],
                 "history_unit_ids": [], "conflicts": [],
                 "required_link_source_ids": [], "missing_inputs": []}
    plan = {"contract": "editor-writing.v1", "sections": [
        {"section": "background", "blocks": [{"type": "fact", "unit_id": units["전체 탐색 기능"], "tag": "p"}]},
        {"section": "scope", "blocks": [{"type": "fact", "unit_id": units["다운스트림 연동"], "tag": "p"}]},
        {"section": "work", "blocks": [{"type": "proposal", "text": "하위 조회 경로를 연동한다.", "tag": "li"}]},
        {"section": "dod", "blocks": [{"type": "proposal", "text": "연동 후 조회 결과를 확인한다.", "tag": "li"}]}]}
    if constraint_owner != "target":
        with pytest.raises(ValueError, match="^invalid_editor_draft_spec$"):
            ASSET["prepare_spec"](view, encoded(selection), encoded(pack))
    else:
        assert ASSET["prepare_spec"](view, encoded(selection), encoded(pack))["route"] == "description"
    compiled = ASSET["enforce_spec"](encoded(plan), view, encoded(selection), encoded(pack))["candidate"]
    remote, payload = helper.both_guards(pack, helper.assembled(pack, compiled))
    if constraint_owner == "target":
        assert remote.status == "ok" and "다운스트림 연동" in payload["content_html"]
        assert "업스트림 완료" not in payload["content_html"]
        assert json.loads(compiled)["sections"][2]["blocks"][0]["source_ids"] == ["intent"]
    else:
        assert remote.status == "blocked" and payload is None
    assert pack == before


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
    if scenario == "explicit_duplicate":
        blocks.append({"type": "link", "source_id": linked["source_id"]})
    projected = json.loads(ASSET["prepare_spec"](view, encoded(selection), encoded(pack))["model_input"])
    assert "link_source_ids" not in encoded(projected)
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
