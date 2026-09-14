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
    schema["properties"]["mode"] = {"type": "string", "const": "description"}
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


def _intent(pack, *, seed="<p>오늘 측정 결과를 확인했는데, 응답 시간이</p>", kind="description"):
    row = copy.deepcopy(pack["sources"][0])
    content = _canonical({"kind": kind, "seed_html": seed, "prompt": "이어서 작성해 줘"})
    row.update(source_id="src-intent", kind="ltm", canonical_id="ltm:user-intent:assembler",
               title="LTM guarded user intent", url="", content=content,
               content_sha256=hashlib.sha256(content.encode()).hexdigest())
    pack["sources"].append(row)
    return pack


def _plan(blocks=None, **overrides):
    return {"contract": "editor-plan.v1", "status": "ok", "blocks": blocks if blocks is not None else [
        {"type": "quote", "tag": "p", "source_id": "src-doc", "text": "조회 전용이다."},
    ], "missing_inputs": [], **overrides}


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


def test_source_bound_quote_plan_passes_both_existing_guards(pack):
    before = copy.deepcopy(pack)
    result = _call(pack, _plan())
    remote, payload = _both_guards(pack, result)
    assert remote.status == "ok"
    assert payload.content_html == "<p>조회 전용이다.</p>"
    assert result["claims"][0]["text"] == "조회 전용이다."
    assert result["claims"][0]["kind"] == "fact"
    assert result["payload_claims"] == [{"path": "/content_html", "claim_ids": ["claim-1"]}]
    assert pack == before  # Never mint evidence facts or alter caller evidence.


def test_ordered_mixed_blocks_include_headings_seed_link_and_lists(pack):
    _intent(pack)
    result = _call(pack, _plan([
        {"type": "proposal", "tag": "h2", "text": "배경", "source_ids": ["src-intent"]},
        {"type": "quote", "tag": "p", "text": "조회 전용이다.", "source_id": "src-doc"},
        {"type": "proposal", "tag": "h2", "text": "작업", "source_ids": ["src-intent"]},
        {"type": "quote", "tag": "li", "text": "검증 결과를 기록한다.", "source_id": "src-doc"},
        {"type": "proposal", "tag": "li", "text": "기준 검토를 요청합니다.", "source_ids": ["src-doc"]},
        {"type": "seed", "source_id": "src-intent"},
        {"type": "link", "source_id": "src-doc"},
    ]))
    remote, payload = _both_guards(pack, result)
    assert remote.status == "ok"
    assert "<ul><li>검증 결과를 기록한다.</li><li>제안: 기준 검토를 요청합니다.</li></ul>" in payload.content_html
    assert '<a href="https://wiki.example.local/pages/design">검증 기준</a>' in payload.content_html
    assert "오늘 측정 결과를 확인했는데, 응답 시간이" in payload.content_html
    assert result["claims"][5]["kind"] == "inference"
    assert result["payload"]["source_ids"] == ["src-intent", "src-doc"]
    assert len(result["citations"]) == 2
    assert GUARD._visible(payload.content_html) == GUARD._visible(" ".join(c["text"] for c in result["claims"]))
    assert all(c["locator"] == "" for c in result["citations"])
    assert PROOF not in _canonical({k: result[k] for k in ("payload", "claims", "citations", "warnings")})


@pytest.mark.parametrize("block", [
    {"type": "quote", "tag": "p", "text": "조회가 완료됐다.", "source_id": "src-doc"},
    {"type": "quote", "tag": "p", "text": "조회 전용이다.", "source_id": "invented"},
    {"type": "quote", "tag": "p", "text": '"scope":', "source_id": "src-doc"},
    {"type": "quote", "tag": "p", "text": "조회 전용이다.", "source_id": "src-doc", "extra": True},
    {"type": "proposal", "tag": "h2", "text": "자동 승인", "source_ids": ["src-doc"]},
    {"type": "proposal", "tag": "p", "text": "검토해 주세요", "source_ids": []},
    {"type": "proposal", "tag": "p", "text": "검토해 주세요", "source_ids": ["invented"]},
    {"type": "proposal", "tag": "p", "text": "검토해 주세요", "source_ids": ["src-doc", "src-doc"]},
    {"type": "link", "source_id": "src-doc", "url": "https://elsewhere.example"},
    {"type": "seed", "source_id": "src-doc"},
    {"type": "quote", "tag": "script", "text": "조회 전용이다.", "source_id": "src-doc"},
])
def test_invalid_block_is_closed_empty_failure(pack, block):
    _assert_blocked(_call(pack, _plan([block])))


def test_intent_source_cannot_be_quoted_as_fact(pack):
    _intent(pack)
    _assert_blocked(_call(pack, _plan([
        {"type": "quote", "tag": "p", "text": "LTM guarded user intent", "source_id": "src-intent"},
    ])))


@pytest.mark.parametrize("plan", [
    {"contract": "editor-plan.v1", "status": "ok", "blocks": [], "missing_inputs": []},
    _plan(extra="unsupported"), _plan(contract="remote-role-result.v1"),
    _plan(missing_inputs=["추가 정보"]), _plan(blocks=[{}] * 33),
    '{"contract":"editor-plan.v1","contract":"editor-plan.v1"}',
    '```json\n{}\n```', '{"value":NaN}',
])
def test_strict_plan_shape_and_limits(pack, plan):
    _assert_blocked(_call(pack, plan))


@pytest.mark.parametrize("status,questions", [("needs_input", ["검증 기준을 알려 주세요"]), ("blocked", [])])
def test_non_ok_plan_preserves_only_control_result(pack, status, questions):
    result = _call(pack, _plan(blocks=[], status=status, missing_inputs=questions))
    assert result["status"] == status
    assert result["payload"] == {}
    assert result["claims"] == result["citations"] == result["payload_claims"] == []
    assert result["missing_inputs"] == questions
    assert result["warnings"] == []
    assert _both_guards(pack, result)[0].status == status


@pytest.mark.parametrize("status,questions", [("needs_input", []), ("blocked", ["不要"]), ("other", [])])
def test_invalid_control_status_or_questions_block(pack, status, questions):
    _assert_blocked(_call(pack, _plan(blocks=[], status=status, missing_inputs=questions)))


def test_active_markup_is_escaped_not_executed_and_guard_remains_authority(pack):
    result = _call(pack, _plan([
        {"type": "proposal", "tag": "p", "text": '<script>alert("unsafe")</script>', "source_ids": ["src-doc"]},
    ]))
    if result["status"] == "ok":
        assert "<script>" not in result["payload"]["content_html"]
        assert "&lt;script&gt;" in result["payload"]["content_html"]
        with pytest.raises(ResponseGuardError):
            _both_guards(pack, result)
    else:
        _assert_blocked(result)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,unsafe", "http://wiki.example.local/doc", "https://user:pass@wiki.example.local/doc", "https://wiki.example.local/\npath"])
def test_unsafe_link_cannot_cross_assembler(pack, url):
    pack["sources"][0]["url"] = url
    _assert_blocked(_call(pack, _plan([{"type": "link", "source_id": "src-doc"}])))


@pytest.mark.parametrize("override", [
    {"role_id": "pmo_brief"}, {"correlation_id": "other-request"},
    {"output_contract": "wrong-contract"},
])
def test_context_identity_is_bound(pack, override):
    _assert_blocked(_call(pack, _plan(), **override))


def test_changed_editor_schema_does_not_get_smoke_passthrough(pack):
    schema = json.loads(pack["payload_schema_json"])
    schema["properties"]["content_html"]["maxLength"] = 24001
    encoded, digest = canonical_payload_schema(schema)
    pack.update(payload_schema_json=encoded, payload_schema_sha256=digest)
    _assert_blocked(_call(pack, _plan()))


def test_schema_digest_tamper_blocks(pack):
    pack["payload_schema_sha256"] = "0" * 64
    _assert_blocked(_call(pack, _plan()))


def test_non_editor_contract_is_exact_passthrough_for_final_guard(pack):
    schema = {"type": "object", "properties": {"status": {"const": "retrieved"}}, "required": ["status"], "additionalProperties": False}
    pack["payload_schema_json"], pack["payload_schema_sha256"] = canonical_payload_schema(schema)
    pack.update(freshness="stale", partial=True)  # Editor-only admission must not rewrite smoke.
    candidate = ' { "deliberately_invalid_for_final_guard" : true } '
    result = ASSEMBLER.main(candidate, _canonical(pack), "editor_author", pack["request_id"],
                            "remote-role-result.v1", "wf-assembler", "kb-assembler", PROOF)
    assert result["candidate"] == candidate


def test_proof_in_candidate_never_enters_blocked_content(pack):
    result = _call(pack, _plan([
        {"type": "proposal", "tag": "p", "text": PROOF, "source_ids": ["src-doc"]},
    ]))
    _assert_blocked(result)
    assert PROOF not in _canonical({k: result[k] for k in ("payload", "claims", "warnings", "missing_inputs")})


def test_long_seed_splits_claims_without_changing_visible_text(pack):
    seed = "prior seed " * 112  # >1200 chars, within the unchanged source envelope limit.
    _intent(pack, seed="<p>" + seed + "</p>")
    result = _call(pack, _plan([{"type": "seed", "source_id": "src-intent"}]))
    assert result["status"] == "ok"
    assert len(result["claims"]) > 1
    assert all(len(row["text"]) <= 1200 for row in result["claims"])
    assert GUARD._visible(result["payload"]["content_html"]) == " ".join(seed.split())
    assert GUARD._visible(result["payload"]["content_html"]) == " ".join(row["text"] for row in result["claims"])
    assert _both_guards(pack, result)[0].status == "ok"


def test_seed_claim_budget_overflow_blocks_without_truncating(pack):
    _intent(pack, seed="<p>" + ("기존 " * 6500) + "</p>")
    blocks = [{"type": "quote", "tag": "p", "source_id": "src-doc", "text": "조회 전용이다."}] * 31
    _assert_blocked(_call(pack, _plan([*blocks, {"type": "seed", "source_id": "src-intent"}])))


@pytest.mark.parametrize("override", [
    {"workflow_version": {"unexpected": "object"}},
    {"knowledge_version": float("nan")},
    {"knowledge_proof": object()},
    {"knowledge_proof": "x" * 241},
    {"role_id": "\ud800"},
])
def test_invalid_metadata_never_raises_or_reflects_exception(pack, override):
    _assert_blocked(_call(pack, _plan(), **override))


def test_mode_enum_requires_trusted_request_mode_and_rejects_conflict(pack):
    pack["payload_schema_json"], pack["payload_schema_sha256"] = canonical_payload_schema(EditorDraftV1.model_json_schema())
    _assert_blocked(_call(pack, _plan()))
    _intent(pack, kind="comment", seed="")
    result = _call(pack, _plan())
    assert result["payload"]["mode"] == "comment"
    assert _both_guards(pack, result)[0].status == "ok"
    schema = EditorDraftV1.model_json_schema()
    schema["properties"]["mode"] = {"type": "string", "const": "description"}
    pack["payload_schema_json"], pack["payload_schema_sha256"] = canonical_payload_schema(schema)
    _assert_blocked(_call(pack, _plan()))


@pytest.mark.parametrize("field", ["extra", "approval", "status"])
def test_extra_editor_schema_fields_are_not_silently_accepted(pack, field):
    schema = json.loads(pack["payload_schema_json"])
    schema["properties"][field] = {"type": "string"}
    encoded = _canonical(schema)
    pack.update(payload_schema_json=encoded, payload_schema_sha256=hashlib.sha256(encoded.encode()).hexdigest())
    _assert_blocked(_call(pack, _plan()))


def test_boolean_schema_limit_is_not_equal_to_integer_one(pack):
    schema = json.loads(pack["payload_schema_json"])
    schema["properties"]["content_html"]["minLength"] = True
    encoded = _canonical(schema)
    pack.update(payload_schema_json=encoded, payload_schema_sha256=hashlib.sha256(encoded.encode()).hexdigest())
    _assert_blocked(_call(pack, _plan()))


def _comment_schema(pack):
    schema = EditorDraftV1.model_json_schema()
    schema["properties"]["mode"] = {"type": "string", "const": "comment"}
    pack["payload_schema_json"], pack["payload_schema_sha256"] = canonical_payload_schema(schema)


def _seed_context(pack, seed, name="seed_html"):
    value = {"kind": "comment", "seed_html": seed} if name == "editor_request" else seed
    encoded = _canonical(value)
    pack["role_context"].append({"context_id": "ctx-seed", "name": name,
        "content_json": encoded, "content_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "observed_at": pack["requested_at"], "partial": False})


@pytest.mark.parametrize("blocks", [
    [{"type": "quote", "tag": "p", "text": "조회 전용이다.", "source_id": "src-doc"}],
    [{"type": "seed", "source_id": "src-intent"}] * 2,
    [{"type": "quote", "tag": "p", "text": "조회 전용이다.", "source_id": "src-doc"},
     {"type": "seed", "source_id": "src-intent"}],
    [{"type": "seed", "source_id": "src-doc"}],
], ids=["omitted", "repeated", "not-first", "wrong-source"])
def test_comment_seed_is_mandatory_exactly_once_first_and_source_bound(pack, blocks):
    _comment_schema(pack)
    _intent(pack, kind="comment")
    _assert_blocked(_call(pack, _plan(blocks)))


@pytest.mark.parametrize("name", ["seed_html", "editor_request"])
def test_comment_seed_matches_context_and_passes_both_guards_without_proposal_label(pack, name):
    _comment_schema(pack)
    seed = "<p>현재 작성 중인 문장을</p>"
    _intent(pack, kind="comment", seed=seed)
    _seed_context(pack, seed, name)
    plan = _plan([
        {"type": "seed", "source_id": "src-intent"},
        {"type": "proposal", "tag": "p", "text": "확인해 주세요.", "source_ids": ["src-intent"]},
    ])
    before = copy.deepcopy(plan)
    result = _call(pack, plan)
    remote, payload = _both_guards(pack, result)
    assert remote.status == "ok"
    assert payload.content_html == "<p>현재 작성 중인 문장을</p><p>제안: 확인해 주세요.</p>"
    assert result["claims"][0]["text"] == "현재 작성 중인 문장을"
    assert result["claims"][1]["text"] == "제안: 확인해 주세요."
    assert plan == before


@pytest.mark.parametrize("intent_present", [False, True])
@pytest.mark.parametrize("name", ["seed_html", "editor_request"])
def test_nonempty_context_seed_requires_matching_intent_seed(pack, intent_present, name):
    _comment_schema(pack)
    _seed_context(pack, "<p>사용자가 작성한 본문</p>", name)
    if intent_present:
        _intent(pack, kind="comment", seed="<p>서로 다른 본문</p>")
    _assert_blocked(_call(pack, _plan([{"type": "seed", "source_id": "src-intent"}])))


@pytest.mark.parametrize("same_text", [False, True])
def test_multiple_nonempty_seed_sources_are_ambiguous_not_model_selected(pack, same_text):
    _comment_schema(pack)
    _intent(pack, kind="comment", seed="<p>현재 문장</p>")
    other = copy.deepcopy(pack["sources"][-1])
    other.update(source_id="other-intent", canonical_id="ltm:user-intent:other")
    if not same_text:
        other["content"] = _canonical({"kind": "comment", "seed_html": "<p>다른 문장</p>"})
        other["content_sha256"] = hashlib.sha256(other["content"].encode()).hexdigest()
    pack["sources"].append(other)
    _assert_blocked(_call(pack, _plan([{"type": "seed", "source_id": "src-intent"}])))


def test_empty_comment_seed_does_not_force_a_seed_block(pack):
    _comment_schema(pack)
    _intent(pack, kind="comment", seed="")
    assert _both_guards(pack, _call(pack, _plan()))[0].status == "ok"


@pytest.mark.parametrize("status,questions", [("needs_input", ["본문을 확인해 주세요"]), ("blocked", [])])
def test_control_outcome_never_requires_seed_or_freshness(pack, status, questions):
    _comment_schema(pack)
    _intent(pack, kind="comment")
    pack.update(freshness="stale", partial=True)
    result = _call(pack, _plan(blocks=[], status=status, missing_inputs=questions))
    assert result["status"] == status
    assert result["payload"] == {}
    assert result["warnings"] == []
    assert _both_guards(pack, result)[0].status == status


@pytest.mark.parametrize("tag", ["p", "li"])
def test_proposal_label_is_visible_but_does_not_make_unsupported_text_a_fact(pack, tag):
    plan = _plan([{"type": "proposal", "tag": tag, "text": "이미 완료했습니다.", "source_ids": ["src-doc"]}])
    result = _call(pack, plan)
    assert result["status"] == "ok"
    assert result["claims"][0]["text"] == "제안: 이미 완료했습니다."
    assert result["claims"][0]["kind"] == "recommendation"
    assert "제안: 이미 완료했습니다." in result["payload"]["content_html"]
    assert pack["facts"] == []
    # This factual-sounding proposal remains a semantic-quality failure for direct
    # review. A visible type label is not evidence or an approval of its assertion.


def test_proposal_prefix_counts_toward_claim_text_limit(pack):
    block = {"type": "proposal", "tag": "p", "text": "가" * 1196, "source_ids": ["src-doc"]}
    assert len(_call(pack, _plan([block]))["claims"][0]["text"]) == 1200
    block["text"] += "나"
    _assert_blocked(_call(pack, _plan([block])))


def test_headings_remain_unprefixed_and_ungrounded_numbers_still_fail_local_guard(pack):
    result = _call(pack, _plan([{"type": "proposal", "tag": "h2", "text": "배경", "source_ids": ["src-doc"]}]))
    assert result["payload"]["content_html"] == "<h2>배경</h2>"
    assert result["claims"][0]["text"] == "배경"
    assert _both_guards(pack, result)[0].status == "ok"
    bad = _call(pack, _plan([{"type": "proposal", "tag": "p", "text": "개선율은 99%입니다.", "source_ids": ["src-doc"]}]))
    with pytest.raises(ResponseGuardError):
        _both_guards(pack, bad)


@pytest.mark.parametrize("level,field,value", [
    ("pack", "freshness", "stale"), ("pack", "freshness", "mixed"),
    ("pack", "freshness", "empty"), ("pack", "freshness", None),
    ("pack", "partial", True), ("pack", "partial", None), ("pack", "partial", 0),
    ("source", "partial", True), ("source", "truncated", True),
    ("source", "partial", "false"), ("context", "partial", True),
])
def test_editor_success_requires_declared_fresh_complete_evidence(pack, level, field, value):
    _seed_context(pack, "")
    target = pack if level == "pack" else (pack["sources"][0] if level == "source" else pack["role_context"][0])
    target[field] = value
    _assert_blocked(_call(pack, _plan()))


def test_optional_partial_flags_default_false_like_contract_models(pack):
    _seed_context(pack, "")
    pack["sources"][0].pop("partial")
    pack["sources"][0].pop("truncated")
    pack["role_context"][0].pop("partial")
    assert _both_guards(pack, _call(pack, _plan()))[0].status == "ok"


@pytest.mark.parametrize("name", ["seed_html", "editor_request"])
def test_explicit_empty_context_seed_conflicts_with_nonempty_intent(pack, name):
    _comment_schema(pack)
    _intent(pack, kind="comment", seed="<p>기존 본문</p>")
    _seed_context(pack, "", name)
    _assert_blocked(_call(pack, _plan([{"type": "seed", "source_id": "src-intent"}])))


def _aggregate_request(pack, value):
    encoded = _canonical(value)
    pack["role_context"].append({"context_id": "ctx-request", "name": "editor_request",
        "content_json": encoded, "content_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "observed_at": pack["requested_at"], "partial": False})


def test_absent_aggregate_seed_is_not_treated_as_explicit_empty_seed(pack):
    _comment_schema(pack)
    _intent(pack, kind="comment", seed="<p>기존 본문</p>")
    _aggregate_request(pack, {"kind": "comment"})
    result = _call(pack, _plan([{"type": "seed", "source_id": "src-intent"}]))
    assert _both_guards(pack, result)[0].status == "ok"


def test_aggregate_kind_conflict_with_bound_mode_blocks(pack):
    _aggregate_request(pack, {"kind": "comment"})  # The schema is description-bound.
    _assert_blocked(_call(pack, _plan()))


def test_aggregate_kind_conflict_with_intent_mode_blocks(pack):
    pack["payload_schema_json"], pack["payload_schema_sha256"] = canonical_payload_schema(EditorDraftV1.model_json_schema())
    _intent(pack, kind="comment", seed="")
    _aggregate_request(pack, {"kind": "description"})
    _assert_blocked(_call(pack, _plan()))


def test_unbound_mode_can_use_explicit_aggregate_kind_without_inventing_seed(pack):
    pack["payload_schema_json"], pack["payload_schema_sha256"] = canonical_payload_schema(EditorDraftV1.model_json_schema())
    _aggregate_request(pack, {"kind": "comment"})
    result = _call(pack, _plan())
    assert result["payload"]["mode"] == "comment"
    assert _both_guards(pack, result)[0].status == "ok"
