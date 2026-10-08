"""Local integration checks for the stdlib-only Dify editor plan assembler."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from app.agent.maxis import EditorDraftV1, EvidencePackV1, ResponseGuard, ResponseGuardError
from app.agent.maxis.contracts import canonical_payload_schema


ROOT = Path(__file__).resolve().parents[3]
PROOF = "LTM_RELEASE_SENTINEL_V1=assembler-test-only"
WORKFLOW = "11111111-1111-4111-8111-111111111111"


def _load(name):
    path = ROOT / "deploy" / "maxis-dify" / "code" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ASSEMBLER = _load("editor_assembler")
GUARD = _load("contract_guard")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.fixture
def pack(context_guard, now):
    schema = EditorDraftV1.model_json_schema()
    schema["properties"]["mode"] = {"type": "string", "const": "comment"}
    return context_guard.build(
        request_id="req-assembler", use_case="editor_author",
        knowledge_manifest_sha256="a" * 64,
        query="근거를 보존하며 본문 초안을 작성해 줘", payload_schema=schema,
        user_identity="test-reviewer", requested_at=now,
        sources=[{
            "source_id": "src-doc", "kind": "confluence", "canonical_id": "doc-design",
            "title": "검증 기준", "scope": "DATALAKE",
            "url": "https://wiki.example.local/pages/design",
            "content": '{"scope":"조회 전용이다.","criteria":["검증 결과를 기록한다.","상태는 확인 필요다."]}',
            "retrieved_at": now,
        }],
    ).model_dump(mode="json")


def _intent(pack, *, seed="<p>오늘 측정 결과를 확인했는데, 응답 시간이</p>", kind="comment"):
    row = copy.deepcopy(pack["sources"][0])
    content = _canonical({"kind": kind, "seed_html": seed, "prompt": "이어서 작성해 줘"})
    row.update(source_id="src-intent", kind="ltm", canonical_id="ltm:user-intent:assembler",
               title="LTM guarded user intent", url="", content=content,
               content_sha256=hashlib.sha256(content.encode()).hexdigest())
    pack["sources"].append(row)
    return pack


def _call(pack, plan, **overrides):
    args = dict(candidate=_canonical(plan) if not isinstance(plan, str) else plan,
                context_pack=_canonical(pack), role_id="editor_author",
                correlation_id=pack["request_id"], output_contract="remote-role-result.v1",
                workflow_version=WORKFLOW, knowledge_version="kb-assembler",
                knowledge_proof=PROOF)
    args.update(overrides)
    result = ASSEMBLER.main(**args)
    assert set(result) == {"candidate"}
    return json.loads(result["candidate"])


def _both_guards(pack, candidate):
    guarded = GUARD.main(
        candidate=_canonical(candidate), context_pack=_canonical(pack),
        role_id="editor_author", correlation_id=pack["request_id"],
        output_contract="remote-role-result.v1", workflow_version=WORKFLOW,
        executed_workflow_id=WORKFLOW, knowledge_version="kb-assembler",
        sentinel_result=[{"content": PROOF}],
    )
    remote, payload = ResponseGuard().validate_remote_role(
        guarded["result"], expected_role_id="editor_author", payload_model=EditorDraftV1,
        evidence=EvidencePackV1.model_validate_json(_canonical(pack)),
        expected_knowledge_manifest_sha256=pack["knowledge_manifest_sha256"],
    )
    return remote, payload


def _assert_blocked(result):
    assert result["status"] == "blocked"
    assert result["payload"] == {}
    assert result["claims"] == result["citations"] == result["payload_claims"] == []
    assert result["warnings"] == ["editor_assembler:invalid_plan"]



def _set_content(source, value):
    source["content"] = value if isinstance(value, str) else _canonical(value)
    source["content_sha256"] = hashlib.sha256(source["content"].encode()).hexdigest()


def _mode(pack, value):
    schema = EditorDraftV1.model_json_schema()
    if value:
        schema["properties"]["mode"] = {"type": "string", "const": value}
    pack["payload_schema_json"], pack["payload_schema_sha256"] = canonical_payload_schema(schema)


def _context(pack, name, value):
    encoded = _canonical(value)
    pack["role_context"].append({"context_id": "ctx-" + str(len(pack["role_context"])), "name": name,
        "content_json": encoded, "content_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "observed_at": pack["requested_at"], "partial": False})


def _fact(pack, text="조회 전용이다.", tag="p"):
    _, lookup = ASSEMBLER._catalog(ASSEMBLER._sources(pack))
    unit = next(key for key, row in lookup.items() if row["text"] == text)
    return {"type": "fact", "unit_id": unit, "tag": tag}


@pytest.mark.parametrize("kind,content", [
    ("jira", {"description": "검토 안내\nh3. 완료 기준\n* 검증 결과를 기록한다."}),
    ("jira", {"latestComment": {"text": "h3. 완료 기준\n# 검증 결과를 기록한다."}}),
    ("jira", {"comments": [{"body": "h3. 완료 기준\n** 검증 결과를 기록한다."}]}),
    ("confluence", "h3. 완료 기준\n* 검증 결과를 기록한다."),
    ("confluence", {"body": "h3. 완료 기준\n* 검증 결과를 기록한다."}),
])
def test_body_wiki_display_removes_only_prefix_and_passes_both_guards(pack, kind, content):
    source = pack["sources"][0]
    source["kind"] = kind
    _set_content(source, content)
    before = copy.deepcopy(pack)
    result = _call(pack, _plan(pack, [_fact(pack, "완료 기준"),
                                    _fact(pack, "검증 결과를 기록한다.", "li")]))
    remote, payload = _both_guards(pack, result)
    assert remote.status == "ok"
    assert payload.content_html == "<p>완료 기준</p><ul><li>검증 결과를 기록한다.</li></ul>"
    assert [claim["text"] for claim in result["claims"]] == ["완료 기준", "검증 결과를 기록한다."]
    assert all(citation["source_id"] == source["source_id"] for citation in result["citations"])
    assert pack == before


@pytest.mark.parametrize("kind,content", [
    ("jira", {"key": "h2. identifier", "status": "* pending", "summary": "h3. summary"}),
    ("jira", "h2. plain source\n* not a typed body"),
    ("jira", {"metadata": {"description": "h2. metadata"}}),
    ("confluence", {"summary": "h2. metadata", "title": "* literal title"}),
    ("bitbucket", {"description": "h2. commit text\n* literal text"}),
    ("web", {"body": "h2. plain text\n* literal text"}),
])
def test_wiki_display_does_not_rewrite_metadata_or_other_source_kinds(pack, kind, content):
    source = pack["sources"][0]
    source.update(kind=kind, title="h2. literal source title")
    _set_content(source, content)
    catalog, _ = ASSEMBLER._catalog(ASSEMBLER._sources(pack))
    expected = [(path, text) for path, text in ASSEMBLER._original_units(source)
                if not (kind == "jira" and path == "/title")]
    assert [(unit["path"], unit["text"]) for unit in catalog[0]["units"]] == expected
    assert catalog[0]["title"] == "h2. literal source title"


@pytest.mark.parametrize("body", [
    "h2. before\n{code:python}\n* literal code\n{code}\n# after",
    "h2. before\n{noformat}\n* literal code\n{noformat}\n# after",
    "h2. before\n```python\n* literal code\n```\n# after",
    "h2. before\n~~~\n* literal code\n~~~\n# after",
    "h2. before\n<pre>\n* literal code\n</pre>\n# after",
    "h2. before\n<code class=\"literal\">\n* literal code\n</code>\n# after",
    "h2. before\n{code}\n* unterminated code",
])
def test_code_marked_leaf_is_entirely_excluded_from_wiki_display(pack, body):
    source = pack["sources"][0]
    source["kind"] = "jira"
    _set_content(source, {"description": body})
    catalog, _ = ASSEMBLER._catalog(ASSEMBLER._sources(pack))
    expected = [(path, text) for path, text in ASSEMBLER._original_units(source) if path != "/title"]
    assert [(unit["path"], unit["text"]) for unit in catalog[0]["units"]] == expected
    assert catalog[0]["title"] == source["title"]


def test_wiki_display_preserves_nonsyntax_numbers_negation_and_inline_markers(pack):
    source = pack["sources"][0]
    source["kind"] = "jira"
    lines = ["h3.0 version", "h7. literal", "h2.no whitespace", "3. 측정값은 1.2초다.",
             "완료 아님: h2. 작업", "*inline*", "h2.", "*", "#value"]
    _set_content(source, {"description": "\n".join(lines)})
    catalog, _ = ASSEMBLER._catalog(ASSEMBLER._sources(pack))
    assert [unit["text"] for unit in catalog[0]["units"]] == lines


def _proposal(text="확인해 주세요.", refs=None, tag="p"):
    return {"type": "proposal", "text": text, "source_ids": ["src-doc"] if refs is None else refs, "tag": tag}


def _plan(pack, blocks=None, mode="comment", status="ok", questions=None):
    names = ["background", "scope", "work", "dod"] if mode == "description" else ["comment"]
    return {"contract": "editor-plan.v2", "status": status, "missing_inputs": questions or [],
            "sections": [{"section": name, "blocks": copy.deepcopy(blocks if blocks is not None else [_fact(pack)])}
                         for name in names] if status == "ok" else []}


def test_extract_selected_fact_passes_both_unchanged_guards_without_minting_evidence(pack):
    before = copy.deepcopy(pack)
    result = _call(pack, _plan(pack))
    remote, payload = _both_guards(pack, result)
    assert remote.status == "ok"
    assert payload.content_html == "<p>조회 전용이다.</p>"
    assert result["claims"][0] == {"claim_id": "claim-1", "text": "조회 전용이다.",
        "kind": "fact", "citation_ids": ["cite-1"]}
    assert result["payload_claims"] == [{"path": "/content_html", "claim_ids": ["claim-1"]}]
    assert pack == before


def test_description_has_server_headings_and_ordered_claim_coverage(pack):
    _mode(pack, "description")
    result = _call(pack, _plan(pack, mode="description"))
    remote, payload = _both_guards(pack, result)
    assert remote.status == "ok"
    assert [r["text"] for r in result["claims"][::2]] == ["배경", "작업 범위", "작업", "완료 조건 (DoD)"]
    assert payload.content_html.count("<h2>") == 4
    assert GUARD._visible(payload.content_html) == GUARD._visible(" ".join(c["text"] for c in result["claims"]))


@pytest.mark.parametrize("mode,names", [("comment", ["background", "scope", "work", "dod"]),
    ("description", ["comment"]), ("description", ["scope", "background", "work", "dod"]),
    ("description", ["background", "scope", "work"]), ("comment", ["comment", "comment"])])
def test_wrong_missing_repeated_or_reordered_section_is_not_repaired(pack, mode, names):
    _mode(pack, mode)
    plan = _plan(pack)
    plan["sections"] = [{"section": name, "blocks": [_fact(pack)]} for name in names]
    _assert_blocked(_call(pack, plan))


@pytest.mark.parametrize("block", [
    {"type": "fact", "tag": "p", "unit_id": "unknown"},
    {"type": "fact", "tag": "p", "unit_id": []},
    {"type": "quote", "tag": "p", "source_id": "src-doc", "text": "조회 전용이다."},
    {"type": "seed", "source_id": "src-intent"},
    {"type": "proposal", "tag": "h2", "text": "배경", "source_ids": ["src-doc"]},
    {"type": "proposal", "tag": "p", "text": "확인", "source_ids": []},
    {"type": "proposal", "tag": "p", "text": "확인", "source_ids": ["bad"]},
    {"type": "proposal", "tag": "p", "text": "확인", "source_ids": ["src-doc", "src-doc"]},
    {"type": "link", "source_id": "src-doc", "url": "https://elsewhere.example"},
    {"type": "link", "source_id": "src-intent"}, {"type": [], "tag": "p"}])
def test_model_cannot_author_facts_seed_headings_links_or_unknown_units(pack, block):
    _intent(pack)
    _assert_blocked(_call(pack, _plan(pack, [block])))


@pytest.mark.parametrize("extra", [{"text": "invented"}, {"source_id": "src-doc"}, {"extra": True}, {"tag": "script"}])
def test_fact_fields_cannot_override_server_unit(pack, extra):
    block = _fact(pack)
    block.update(extra)
    _assert_blocked(_call(pack, _plan(pack, [block])))


def test_stale_unit_replay_after_source_change_is_rejected(pack):
    plan = _plan(pack)
    _set_content(pack["sources"][0], {"scope": "검토 전용이다."})
    _assert_blocked(_call(pack, plan))


@pytest.mark.parametrize("seed", ["", "<p></p>"])
def test_empty_seed_does_not_generate_a_seed_block(pack, seed):
    _intent(pack, seed=seed)
    assert _both_guards(pack, _call(pack, _plan(pack)))[1].content_html == "<p>조회 전용이다.</p>"


def test_absent_seed_does_not_generate_a_seed_block(pack):
    _intent(pack)
    _set_content(pack["sources"][-1], {"kind": "comment", "prompt": "확인 요청"})
    assert _both_guards(pack, _call(pack, _plan(pack)))[1].content_html == "<p>조회 전용이다.</p>"


@pytest.mark.parametrize("name", ["seed_html", "editor_request"])
def test_seed_is_automatic_exactly_once_first_with_matching_context(pack, name):
    seed = "<p>측정 결과가 생각보다</p>"
    _intent(pack, seed=seed)
    _context(pack, name, {"kind": "comment", "seed_html": seed} if name == "editor_request" else seed)
    plan = _plan(pack, [_proposal("실제 결과와 비교 기준을 확인해 주세요.", ["src-intent"])])
    before = copy.deepcopy(plan)
    result = _call(pack, plan)
    remote, payload = _both_guards(pack, result)
    assert remote.status == "ok" and payload.content_html.startswith(seed)
    assert payload.content_html.count("측정 결과가 생각보다") == 1
    assert result["claims"][0]["kind"] == "inference"
    assert result["claims"][1]["text"].startswith("제안: ")
    assert plan == before


@pytest.mark.parametrize("name", ["seed_html", "editor_request"])
@pytest.mark.parametrize("seed", ["", "<p>다른 본문</p>"])
def test_explicit_empty_or_mismatched_seed_is_not_overwritten(pack, name, seed):
    _intent(pack)
    _context(pack, name, {"kind": "comment", "seed_html": seed} if name == "editor_request" else seed)
    _assert_blocked(_call(pack, _plan(pack)))


@pytest.mark.parametrize("present", [True, False])
def test_context_seed_requires_exact_intent_source(pack, present):
    if present: _intent(pack, seed="")
    _context(pack, "seed_html", "<p>원래 내용</p>")
    _assert_blocked(_call(pack, _plan(pack)))


@pytest.mark.parametrize("same", [True, False])
def test_multiple_seed_sources_are_ambiguous_even_with_equal_text(pack, same):
    _intent(pack)
    other = copy.deepcopy(pack["sources"][-1])
    other.update(source_id="other-intent", canonical_id="ltm:user-intent:other")
    if not same: _set_content(other, {"kind": "comment", "seed_html": "<p>다른 내용</p>"})
    pack["sources"].append(other)
    _assert_blocked(_call(pack, _plan(pack)))


def test_missing_aggregate_seed_is_not_an_instruction_to_clear(pack):
    _intent(pack)
    _context(pack, "editor_request", {"kind": "comment"})
    assert _both_guards(pack, _call(pack, _plan(pack)))[0].status == "ok"


@pytest.mark.parametrize("field,value", [("kind", "description"), ("prompt", "다른 요청")])
def test_trusted_request_conflict_is_not_model_resolved(pack, field, value):
    _intent(pack)
    _context(pack, "editor_request", {field: value})
    _assert_blocked(_call(pack, _plan(pack)))


def test_unbound_mode_needs_trusted_kind(pack):
    plan = _plan(pack)
    _mode(pack, None)
    _assert_blocked(_call(pack, plan))
    _context(pack, "editor_request", {"kind": "comment"})
    assert _both_guards(pack, _call(pack, plan))[0].status == "ok"


@pytest.mark.parametrize("status,questions", [("needs_input", ["정확한 기준을 알려 주세요."]), ("blocked", [])])
def test_control_result_has_no_seed_claims_or_partial_body(pack, status, questions):
    _intent(pack)
    pack.update(freshness="stale", partial=True)
    result = _call(pack, _plan(pack, status=status, questions=questions))
    assert result["status"] == status and result["payload"] == {}
    assert result["claims"] == result["citations"] == result["payload_claims"] == []
    assert result["missing_inputs"] == questions
    assert _both_guards(pack, result)[0].status == status


@pytest.mark.parametrize("mutation", ["empty", "extra", "version", "missing", "33blocks", "5sections",
                                      "emptysection", "badsection", "badstatus"])
def test_plan_shape_counts_and_status_are_strict(pack, mutation):
    plan = _plan(pack)
    if mutation == "empty": plan["sections"] = []
    elif mutation == "extra": plan["extra"] = True
    elif mutation == "version": plan["contract"] = "editor-plan.v1"
    elif mutation == "missing": plan["missing_inputs"] = ["question"]
    elif mutation == "33blocks": plan["sections"][0]["blocks"] *= 33
    elif mutation == "5sections": plan["sections"] *= 5
    elif mutation == "emptysection": plan["sections"][0]["blocks"] = []
    elif mutation == "badsection": plan["sections"][0]["unexpected"] = True
    else: plan["status"] = "other"
    _assert_blocked(_call(pack, plan))


@pytest.mark.parametrize("raw", ['{"contract":"x","contract":"y"}', '~~~json\n{}\n~~~', '{"value":NaN}'])
def test_json_parse_failure_is_not_repaired(pack, raw):
    _assert_blocked(_call(pack, raw))


@pytest.mark.parametrize("level,field,value", [("pack", "freshness", "stale"), ("pack", "freshness", "mixed"),
    ("pack", "freshness", None), ("pack", "partial", True), ("pack", "partial", 0), ("pack", "partial", None),
    ("source", "partial", True), ("source", "truncated", True), ("source", "partial", "false"),
    ("context", "partial", True)])
def test_existing_freshness_and_completeness_gate(pack, level, field, value):
    plan = _plan(pack)
    _context(pack, "seed_html", "")
    target = pack if level == "pack" else pack["sources" if level == "source" else "role_context"][0]
    target[field] = value
    _assert_blocked(_call(pack, plan))


@pytest.mark.parametrize("override", [{"role_id": "pmo_brief"}, {"correlation_id": "other"},
    {"output_contract": "other"}, {"workflow_version": {}}, {"knowledge_version": float("nan")},
    {"knowledge_proof": object()}, {"knowledge_proof": "x" * 241}, {"role_id": "\ud800"}])
def test_bad_metadata_never_raises_or_reflects_error(pack, override):
    _assert_blocked(_call(pack, _plan(pack), **override))


@pytest.mark.parametrize("mutation", ["hash", "bool", "limit", "extra"])
def test_changed_editor_schema_never_becomes_legacy_passthrough(pack, mutation):
    plan = _plan(pack)
    schema = json.loads(pack["payload_schema_json"])
    if mutation == "hash": pack["payload_schema_sha256"] = "0" * 64
    else:
        if mutation == "bool": schema["properties"]["content_html"]["minLength"] = True
        elif mutation == "limit": schema["properties"]["content_html"]["maxLength"] = 24001
        else: schema["properties"]["approval"] = {"type": "string"}
        raw = _canonical(schema)
        pack.update(payload_schema_json=raw, payload_schema_sha256=hashlib.sha256(raw.encode()).hexdigest())
    _assert_blocked(_call(pack, plan))


def test_unregistered_non_editor_schema_does_not_bypass_assembler(pack):
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"state": {"const": "ready"}}, "required": ["state"]}
    pack["payload_schema_json"], pack["payload_schema_sha256"] = canonical_payload_schema(schema)
    raw = ' { "invalid_for_final_guard" : true } '
    result = ASSEMBLER.main(raw, _canonical(pack), "editor_author", pack["request_id"],
        "remote-role-result.v1", WORKFLOW, "kb-test", PROOF)
    _assert_blocked(json.loads(result["candidate"]))


def test_proposal_is_labelled_but_does_not_become_fact_or_bypass_numeric_guard(pack):
    result = _call(pack, _plan(pack, [_proposal("이미 완료했습니다.")]))
    assert result["status"] == "ok" and result["claims"][0]["kind"] == "recommendation"
    assert result["claims"][0]["text"] == "제안: 이미 완료했습니다."
    assert pack["facts"] == []
    with pytest.raises(ResponseGuardError):
        _both_guards(pack, _call(pack, _plan(pack, [_proposal("개선율은 99%입니다.")])))


def test_proposal_prefix_counts_toward_1200_chars(pack):
    assert len(_call(pack, _plan(pack, [_proposal("가" * 1196)]))["claims"][0]["text"]) == 1200
    _assert_blocked(_call(pack, _plan(pack, [_proposal("가" * 1197)])))


def test_active_markup_escaped_and_release_proof_not_reflected(pack):
    result = _call(pack, _plan(pack, [_proposal('<script>alert("unsafe")</script>')]))
    # Escaped markup must also satisfy visible-claim coverage; rejection is safer
    # than returning a partially sanitized candidate as the old v1 contract did.
    if result["status"] == "ok":
        assert "<script>" not in result["payload"]["content_html"]
        assert "&lt;script&gt;" in result["payload"]["content_html"]
        with pytest.raises(ResponseGuardError): _both_guards(pack, result)
    else:
        _assert_blocked(result)
    _assert_blocked(_call(pack, _plan(pack, [_proposal(PROOF)])))


def test_long_seed_splits_claims_without_visible_change(pack):
    seed = "prior seed " * 112
    _intent(pack, seed="<p>" + seed + "</p>")
    result = _call(pack, _plan(pack))
    assert result["status"] == "ok" and all(len(row["text"]) <= 1200 for row in result["claims"])
    assert " ".join(c["text"] for c in result["claims"] if c["kind"] == "inference") == " ".join(seed.split())
    assert _both_guards(pack, result)[0].status == "ok"


def test_40_claim_limit_counts_seed_and_server_headings(pack):
    _mode(pack, "description")
    _intent(pack, kind="description", seed="<p>" + "word " * 1500 + "</p>")
    _assert_blocked(_call(pack, _plan(pack, [_fact(pack)] * 8, mode="description")))


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,bad", "http://wiki.example.local/doc",
    "https://user:pass@wiki.example.local/doc", "https://wiki.example.local/\npath"])
def test_source_link_authority_is_strict(pack, url):
    plan = _plan(pack, [{"type": "link", "source_id": "src-doc"}])
    pack["sources"][0]["url"] = url
    _assert_blocked(_call(pack, plan))


def test_nested_wiki_brackets_transform_html_and_claim_together(pack):
    source = pack["sources"][0]
    source.update(title="[설계] 검증 기준", url="https://wiki.example.local/pages/[설계]+기준")
    raw = "결과는 [[설계] 검증 기준|" + source["url"] + "] 문서에 기록한다."
    _set_content(source, {"text": raw})
    expected = "결과는 [설계] 검증 기준 문서에 기록한다."
    result = _call(pack, _plan(pack, [_fact(pack, expected)]))
    remote, payload = _both_guards(pack, result)
    assert remote.status == "ok"
    assert '<a href="https://wiki.example.local/pages/[설계]+기준">[설계] 검증 기준</a>' in payload.content_html
    assert result["claims"][0]["text"] == expected
    assert source["content"] == _canonical({"text": raw})


def test_cross_source_anchor_has_both_citations(pack):
    other = copy.deepcopy(pack["sources"][0])
    other.update(source_id="src-ticket", kind="jira", canonical_id="ticket-view", title="업무 기준",
                 url="https://jira.example.local/browse/VIEW-2")
    _set_content(other, {"description": "기준은 [검증 기준|" + pack["sources"][0]["url"] + "] 참조."})
    pack["sources"].append(other)
    result = _call(pack, _plan(pack, [_fact(pack, "기준은 검증 기준 참조.")]))
    assert _both_guards(pack, result)[0].status == "ok"
    assert {c["source_id"] for c in result["citations"]} == {"src-doc", "src-ticket"}


@pytest.mark.parametrize("text", ["[위조|https://unknown.example/a]", "[위조|javascript:alert(1)]",
    "https://wiki.example.local/pages/design-evil", "https://wiki.example.local/pages/design?x=evil",
    "https://wiki.example.local/pages/design.evil", "https://wiki.example.local/pages/design#evil"])
def test_unknown_or_prefix_url_does_not_gain_authority(pack, text):
    _set_content(pack["sources"][0], {"text": text})
    with pytest.raises(ValueError): ASSEMBLER._catalog(ASSEMBLER._sources(pack))


def test_person_marker_is_source_bound_text_for_local_resolver(pack):
    marker = "[[person:123456789abc]]"
    _set_content(pack["sources"][0], {"assignee": marker})
    result = _call(pack, _plan(pack, [_fact(pack, marker)]))
    remote, payload = _both_guards(pack, result)
    assert remote.status == "ok" and payload.content_html == "<p>" + marker + "</p>"
    assert result["claims"][0]["text"] == marker


def test_model_cannot_invent_person_or_author_urls(pack):
    _assert_blocked(_call(pack, _plan(pack, [_proposal("[[person:123456789abc]] 확인")])))
    _assert_blocked(_call(pack, _plan(pack, [_proposal("https://wiki.example.local/pages/design 확인")])))


def test_plain_known_url_followed_by_sentence_punctuation_is_exact_anchor(pack):
    _set_content(pack["sources"][0], "기준은 https://wiki.example.local/pages/design. 확인한다.")
    result = _call(pack, _plan(pack, [_fact(pack, "기준은 검증 기준 . 확인한다.")]))
    remote = _both_guards(pack, result)[0]
    assert remote.status == "ok", (result["warnings"], remote.warnings)
    assert '</a>. 확인한다.' in result["payload"]["content_html"]


def test_long_factual_line_is_rejected_not_silently_cropped(pack):
    _set_content(pack["sources"][0], "a" * 1201)
    with pytest.raises(ValueError): ASSEMBLER._catalog(ASSEMBLER._sources(pack))


def test_line_units_preserve_exact_source_path_and_line_text(pack):
    _set_content(pack["sources"][0], {"body": "조건: 변경 금지\n수치는 확인 필요\n\n추가 검토"})
    catalog, _ = ASSEMBLER._catalog(ASSEMBLER._sources(pack))
    rows = {row["path"]: row["text"] for row in catalog[0]["units"]}
    assert rows["/content/body/line:0"] == "조건: 변경 금지"
    assert rows["/content/body/line:1"] == "수치는 확인 필요"
    assert rows["/content/body/line:3"] == "추가 검토"


def test_numeric_boolean_leaves_are_not_promoted_into_generated_factual_sentences(pack):
    _set_content(pack["sources"][0], {"count": 7, "approved": True, "scope": "조회 전용이다."})
    catalog, _ = ASSEMBLER._catalog(ASSEMBLER._sources(pack))
    assert {row["text"] for row in catalog[0]["units"]} == {"검증 기준", "조회 전용이다."}


@pytest.mark.parametrize("kind", ["fact", "proposal"])
def test_model_cannot_repeat_automatic_seed_through_another_block(pack, kind):
    _intent(pack, seed="<p>조회 전용이다.</p>")
    block = _fact(pack) if kind == "fact" else _proposal("조회   전용이다. 추가로 확인해 주세요.")
    _assert_blocked(_call(pack, _plan(pack, [block])))


def test_seed_repetition_across_multiple_model_blocks_is_also_rejected(pack):
    _intent(pack, seed="<p>조회 전용이다. 검증 결과를 기록한다.</p>")
    _assert_blocked(_call(pack, _plan(pack, [_fact(pack), _fact(pack, "검증 결과를 기록한다.")])))


def test_metadata_word_is_not_an_active_data_url_scheme(pack):
    _set_content(pack["sources"][0], {"note": "metadata: read only"})
    result = _call(pack, _plan(pack, [_fact(pack, "metadata: read only")]))
    assert _both_guards(pack, result)[0].status == "ok"
    assert _call(pack, _plan(pack, [_proposal("metadata: read only 확인")]))["status"] == "ok"


@pytest.mark.parametrize("text", ["data:text/html,unsafe", "javascript:alert(1)", "링크: data:text/html,unsafe"])
def test_real_active_scheme_is_not_mistaken_for_metadata(pack, text):
    _set_content(pack["sources"][0], text)
    with pytest.raises(ValueError): ASSEMBLER._catalog(ASSEMBLER._sources(pack))


@pytest.mark.parametrize("envelope", ["issues", "list"])
def test_unowned_jira_wrapper_title_is_not_a_selectable_unit(pack, envelope):
    source = pack["sources"][0]
    source.update(kind="jira", canonical_id="DL-42", title="Jira result wrapper", url="")
    issues = [{"key": "DL-42", "summary": "대상 작업", "description": "30분 주기 보존",
               "parent": {"key": "DL-40"}},
              {"key": "DL-40", "summary": "상위 작업", "description": "상위 배경"}]
    _set_content(source, {"complete": True, "issues": issues, "total": 2} if envelope == "issues" else issues)
    before = copy.deepcopy(pack)

    catalog, lookup = ASSEMBLER._catalog(ASSEMBLER._sources(pack), "DL-42")

    assert catalog[0]["title"] == "Jira result wrapper"  # Still source metadata, never evidence.
    assert all(row["path"] != "/title" for row in catalog[0]["units"])
    by_text = {row["text"]: row for row in catalog[0]["units"]}
    for text in ("대상 작업", "30분 주기 보존"):
        assert by_text[text]["entity_key"] == "DL-42"
        assert lookup[by_text[text]["unit_id"]]["text"] == text
    for text in ("상위 작업", "상위 배경"):
        assert by_text[text]["entity_key"] == "DL-40"
    assert pack == before


def test_owned_issue_title_and_document_evidence_stay_selectable(pack):
    document = copy.deepcopy(pack["sources"][0])
    source = pack["sources"][0]
    source.update(source_id="src-ticket", kind="jira", canonical_id="DL-42", title="직접 조회한 티켓", url="")
    _set_content(source, {"key": "DL-42", "summary": "대상 작업", "description": "30분 주기 보존"})
    pack["sources"].append(document)
    _intent(pack)
    before = copy.deepcopy(pack)

    catalog, _ = ASSEMBLER._catalog(ASSEMBLER._sources(pack), "DL-42")

    assert len(catalog) == 2  # Intent remains a separate seed/request source.
    title = next(row for row in catalog[0]["units"] if row["path"] == "/title")
    assert title["entity_key"] == "DL-42" and title["text"] == "직접 조회한 티켓"
    assert {row["text"] for row in catalog[1]["units"]} >= {"검증 기준", "조회 전용이다."}
    assert pack == before


@pytest.mark.parametrize("with_document", [False, True])
def test_empty_jira_wrapper_does_not_bypass_existing_no_units_guard(pack, with_document):
    document = copy.deepcopy(pack["sources"][0])
    source = pack["sources"][0]
    source.update(source_id="src-empty", kind="jira", canonical_id="jira-search-empty", title="Empty Jira search", url="")
    _set_content(source, {"complete": True, "issues": [], "total": 0})
    if with_document:
        pack["sources"].append(document)
    before = copy.deepcopy(pack)

    with pytest.raises(ValueError, match="^invalid_editor_plan$"):
        ASSEMBLER._catalog(ASSEMBLER._sources(pack), "DL-42")

    assert pack == before  # No fake owner/fact and no silent evidence-source removal.
