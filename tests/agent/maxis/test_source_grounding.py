"""Canonical source decoding through local, template and emitted guard boundaries."""
import importlib.util
import json
from pathlib import Path

import pytest

from types import ModuleType
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from app.agent.maxis import backend, response_guard as LOCAL
from app.agent.maxis.context_guard import ContextGuardError
from app.agent.tools import _ctx
from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient
from app.agent.editor_author import _ticket_context

ROOT = Path(__file__).resolve().parents[3]


def load(path):
    spec = importlib.util.spec_from_file_location("ground_test_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REMOTE = load(ROOT / "deploy/maxis-dify/code/contract_guard.py")
RENDERER = load(ROOT / "deploy/maxis-dify/contract_guard_asset.py")
EMITTED_SOURCE = RENDERER.render_contract_guard(
    (ROOT / "deploy/maxis-dify/code/contract_guard.py").read_text(encoding="utf-8"),
    (ROOT / RENDERER.HELPER_PATH).read_text(encoding="utf-8"))
EMITTED = ModuleType("emitted_contract_guard")
exec(compile(EMITTED_SOURCE, "emitted_contract_guard", "exec"), EMITTED.__dict__)
GUARDS = (LOCAL, REMOTE, EMITTED)


def capture():
    settings = deepcopy(get_settings())
    assert settings.jira_env == "mock"
    settings.search_jira_projects = ["DL"]
    cache = Cache(":memory:")
    client = JiraClient(settings, cache)
    assert type(client.provider).__name__ == "InProcessProvider"
    previous = _ctx._bound
    _ctx.bind(client, settings)
    try:
        marker = backend.begin_local_acquisition()
        context = _ticket_context("DL-9044", "comment")
        provenance = backend.finish_local_acquisition(marker)
        result = {"ticketContext": context}
        proof = backend.attest_query_result("editor-ticket", "jira", result,
            scopes=("DL",), retrieved_at=provenance["retrieved_at"],
            partial=True if provenance["partial"] else None, provenance=provenance)
        state = {"request_text": "현재 맥락을 확인", "correlation_id": "editor-grounding-capture",
                 "correlation_started_at": datetime.now(timezone.utc).isoformat(),
                 "knowledge_manifest_sha256": "a" * 64,
                 "_maxis_evidence_rows": [{"id": "editor-ticket", "source": "jira",
                     "result": result, "_remoteEvidence": proof}]}
        pack = backend._context_pack("editor_author", state, [], backend._TEXT_PAYLOAD_SCHEMA)
        source = next(row for row in pack.sources if row.kind == "jira")
        record = {"path": "actual _ticket_context; same compose attestation assembly; actual _context_pack",
            "schemaQualification": "text probe schema, not full Editor payload or compose entry",
            "actualContextNewlines": context.count("\n"), "contextSha256": hashlib.sha256(context.encode()).hexdigest(),
            "tracked": provenance["tracked"], "actualTrackedReads": provenance["read_count"],
            "sourceBytes": len(source.content.encode()), "partial": pack.partial,
            "truncated": source.truncated, "flags": list(pack.flags),
            "escapedNewlinesPresent": "\\n" in source.content,
            "pack": pack.model_dump(mode="json")}
        return pack, record
    finally:
        _ctx._bound = previous
        client.provider.close()
        cache.close()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pack_for(context_guard, now, content):
    return context_guard.build(request_id="normalize-test", use_case="result_integrator",
        query="자료를 확인", payload_schema=backend._TEXT_PAYLOAD_SCHEMA,
        user_identity="fixture", requested_at=now, knowledge_manifest_sha256="a" * 64,
        sources=[{"source_id": "source-one", "kind": "jira", "canonical_id": "DL-9044",
            "title": "기록", "scope": "DL", "url": "", "content": content, "retrieved_at": now}])


def candidate(pack, text, source_id=None):
    return {"contract": "remote-role-result.v1", "role_id": pack.use_case, "status": "ok",
        "payload": {"text": text}, "claims": [{"claim_id": "claim-one", "kind": "fact", "text": text,
            "citation_ids": ["cite-one"]}], "payload_claims": [{"path": "/text", "claim_ids": ["claim-one"]}],
        "citations": [{"citation_id": "cite-one", "source_id": source_id or pack.sources[0].source_id, "locator": ""}],
        "missing_inputs": [], "warnings": [], "workflow_version": "11111111-1111-4111-8111-111111111111",
        "knowledge_version": "probe", "knowledge_proof": "LTM_RELEASE_SENTINEL_V1=probe",
        "knowledge_manifest_sha256": "a" * 64}


def remote_result(guard, pack, result):
    args = (encode(result), encode(pack.model_dump(mode="json")), pack.use_case,
        pack.request_id, result["contract"], result["workflow_version"], result["workflow_version"],
        "probe", [{"content": result["knowledge_proof"]}])
    actual = json.loads(guard.main(*args)["result"])
    assert json.loads(EMITTED.main(*args)["result"]) == actual
    return actual


def local_result(guard, pack, result):
    return guard.ResponseGuard().validate_remote_role(encode(result), expected_role_id=pack.use_case,
        payload_model=backend._TEXT_PAYLOAD_SCHEMA, evidence=pack,
        expected_knowledge_manifest_sha256="a" * 64)


@pytest.mark.parametrize("body", ["첫 줄\n다음 줄", "첫 줄\r\n다음 줄", "첫 줄\t다음 줄",
    "첫 줄\n\n다음 줄", "첫 줄\n반복\n반복", "  첫 줄\r\n\n반복\n반복\t끝  "])
def test_exact_false_refusals_resolved_without_mutating_original_pack(context_guard, now, body):
    pack = pack_for(context_guard, now, encode({"description": body, "key": "DL-9044"}))
    before = pack.model_dump(mode="json")
    result = candidate(pack, "DL-9044\n\n> " + body.replace("\n", "\n> "))
    assert remote_result(REMOTE, pack, result) == result
    local_result(LOCAL, pack, result)
    assert pack.model_dump(mode="json") == before


def test_actual_current_editor_source_path_before_after():
    pack, record = capture()
    source = next(row for row in pack.sources if row.kind == "jira")
    assert record["tracked"] and record["actualTrackedReads"] > 0
    assert not pack.partial and not source.truncated and "\\n\\n최근 코멘트" in source.content
    assert hashlib.sha256(source.content.encode()).hexdigest() == source.content_sha256
    before = pack.model_dump(mode="json")
    result = candidate(pack, "최근 코멘트", source.source_id)
    assert remote_result(REMOTE, pack, result) == result
    local_result(LOCAL, pack, result)
    assert pack.model_dump(mode="json") == before


@pytest.mark.parametrize("data", [
    {"status": "Closed", "priority": "High"},
    {"aa": ["bb", {"cc": "dd\nee\r\nff\tgg"}], "empty": ""},
    {"key\\part": "literal \\nnext", "quote": 'a "quoted" value'},
    {"한국어": "é e\u0301 😀 한글", "controls": "aa\u0000bb\u2028cc"},
    {"digits": [0, 123, -12, True, False, None]},
    {"a": "<span", "b": ">actual text"},
    {"a": "&am", "b": "p;literal", "c": "&amp;lt;tag&amp;gt;"},
])
def test_exact_token_equivalence_to_keys_and_scalar_values(data):
    values = []
    def walk(item):
        if isinstance(item, dict):
            for key, value in item.items():
                values.append(key)
                walk(value)
        elif isinstance(item, list):
            for value in item: walk(value)
        else: values.append(item if isinstance(item, str) else json.dumps(item))
    walk(data)
    for guard in GUARDS:
        expected = set().union(*(guard._ground_tokens(value) for value in values))
        for ascii_mode in (False, True):
            raw = json.dumps(data, ensure_ascii=ascii_mode, separators=(",", ":"))
            actual = set().union(*(guard._ground_tokens(part) for part in guard.source_lexical_parts(raw)))
            assert actual == expected


@pytest.mark.parametrize("raw", ["plain first\nsecond", "[ordinary citation] next", "{unfinished",
    '{"x":"first\\nsecond",}', '{"x":"first\\nsecond","x":"other"}',
    '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', '{"x":"\\ud800"}',
    "[" * 9 + '"aa\\nbb"' + "]" * 9, json.dumps(["aa"] * 101),
    json.dumps({"x": "a" * 1500}), '"first\\nsecond"', "null", "true", "123"])
def test_unsupported_decode_is_exact_existing_literal_interpretation(raw):
    for guard in GUARDS:
        assert guard.source_lexical_parts(raw) == (raw,)
        assert set().union(*(guard._ground_tokens(part) for part in guard.source_lexical_parts(raw))) == guard._ground_tokens(raw)


def test_no_double_decode_or_cross_value_collision_or_numeric_reformat():
    for guard in GUARDS:
        raw = '{"left":"ab","cd":["ef","gh"],"quoted":"{\\"x\\":\\"aa\\\\nbb\\"}","number":1e+02}'
        parts = guard.source_lexical_parts(raw)
        assert "1e+02" in parts and "100.0" not in parts
        assert '{"x":"aa\\nbb"}' in parts
        tokens = set().union(*(guard._ground_tokens(part) for part in parts))
        assert not ({"abcd", "cdef", "efgh", "bb"} & tokens)
        assert {"ab", "cd", "ef", "gh", "nbb"} <= tokens


def test_exact_resource_boundaries_and_utf8_bytes():
    for guard in GUARDS:
        at_limit = '{"x":"' + "a" * 1492 + '"}'
        assert len(at_limit.encode()) == 1500
        assert guard.source_lexical_parts(at_limit) != (at_limit,)
        over_limit = at_limit[:-2] + 'a"}'
        assert guard.source_lexical_parts(over_limit) == (over_limit,)
        multibyte = '{"x":"' + "가" * 498 + '"}'
        assert len(multibyte) < 1500 < len(multibyte.encode())
        assert guard.source_lexical_parts(multibyte) == ("x", "가" * 498)
        at_byte_limit = '["' + "😀" * 1001 + "가" * 164 + '"]'
        assert len(at_byte_limit.encode()) == 4500 and len(at_byte_limit) <= 1500
        assert guard.source_lexical_parts(at_byte_limit) != (at_byte_limit,)
        over_byte_limit = at_byte_limit[:-2] + '가"]'
        assert guard.source_lexical_parts(over_byte_limit) == (over_byte_limit,)
        depth8 = "[" * 8 + '"aa\\nbb"' + "]" * 8
        assert guard.source_lexical_parts(depth8) == ("aa\nbb",)
        hundred = json.dumps(["aa"] * 100)
        assert guard.source_lexical_parts(hundred) == tuple(["aa"] * 100)


def test_split_markup_and_entity_parts_use_one_visible_pass(context_guard, now):
    raw = encode({"a": "<span", "b": "middle", "c": ">actual text", "d": "&am", "e": "p;entity"})
    pack = pack_for(context_guard, now, raw)
    result = candidate(pack, "span middle actual text am entity")
    for remote, local in ((REMOTE, LOCAL),):
        assert remote_result(remote, pack, result) == result
        local_result(local, pack, result)
    for guard in GUARDS:
        parts = guard.source_lexical_parts('{"x":"&amp;lt;hidden&amp;gt;"}')
        tokens = set().union(*(guard._ground_tokens(part) for part in parts))
        assert {"lt", "hidden", "gt"} <= tokens


def test_raw_escape_artifact_no_longer_falsely_grounds(context_guard, now):
    pack = pack_for(context_guard, now, '{"description":"first\\nsecond"}')
    forged = candidate(pack, "nsecond")
    assert remote_result(REMOTE, pack, forged)["status"] == "blocked"
    with pytest.raises(LOCAL.ResponseGuardError, match="citation_assertion_mismatch"):
        local_result(LOCAL, pack, forged)


def test_structural_predicate_support_preserved(context_guard, now):
    pack = pack_for(context_guard, now, '{"status":"Closed","priority":"High"}')
    result = candidate(pack, "status Closed priority High")
    for remote, local in ((REMOTE, LOCAL),):
        assert remote_result(remote, pack, result) == result
        local_result(local, pack, result)


def test_dlp_happens_before_decoding_and_no_second_pass_recovers_identity(context_guard, now):
    raw = '{"text":"\\u0061lice@example.test first\\nsecond"}'
    pack = pack_for(context_guard, now, raw)
    content = pack.sources[0].content
    assert "alice@example.test" not in content and "person:" in content
    for guard in GUARDS:
        assert all("alice@example.test" not in part for part in guard.source_lexical_parts(content))
    with pytest.raises(ContextGuardError, match="sensitive"):
        pack_for(context_guard, now, '{"text":"\\u0070assword=synthetic-secret"}')


@pytest.mark.parametrize("raw", [
    '{"a":"first\\nsecond","\\u0061":"other"}',
    '{"ok":"first\\nsecond","nested":{"x":1,"x":2}}',
    '{"ok":"first\\nsecond","bad":"\\udfff"}',
    '{"ok":"first\\nsecond","\\ud800":"bad"}',
    '{"ok":"first\\nsecond","bad":-Infinity}',
    '{"ok":"first\\nsecond","bad":1e309}',
    '{"ok":"first\\nsecond","bad":01}',
    '{"ok":"first\\nsecond","bad":.1}',
    '{"ok":"first\\nsecond","bad":1.}',
    '{"ok":"first\\nsecond"} trailing',
    '{"ok":"first\\nsecond"}{"next":"other"}',
    '{"ok":"first\\nsecond"/* comment */}',
    '{"ok":"raw\nnewline"}',
    '\ufeff{"ok":"first\\nsecond"}',
    '{"ok":"first\\nsecond","bad":' + json.dumps([0] * 101) + '}',
    '{"ok":"first\\nsecond","bad":' + '[' * 8 + '0' + ']' * 8 + '}',
    '[' * 749 + '0' + ']' * 749,
])
def test_unsupported_never_leaks_an_earlier_decoded_part(raw):
    for guard in GUARDS:
        parts = guard.source_lexical_parts(raw)
        assert parts == (raw,)
        assert set().union(*(guard._ground_tokens(p) for p in parts)) == guard._ground_tokens(raw)


@pytest.mark.parametrize("lexeme", ["-0", "-0.0", "1E+002", "1.2300", "1e-999", "9" * 300])
def test_numeric_spelling_not_python_float_or_int_roundtrip(lexeme):
    raw = '{"number":' + lexeme + '}'
    for guard in GUARDS:
        assert guard.source_lexical_parts(raw) == ("number", lexeme)


def test_escaped_keys_surrogate_pairs_and_distinct_unicode_keys():
    raw = '{"first\\nsecond":"\\ud83d\\ude00","é":"composed","é":"decomposed"}'
    for guard in GUARDS:
        assert guard.source_lexical_parts(raw) == (
            "first\nsecond", "😀", "é", "composed", "é", "decomposed")


@pytest.mark.parametrize("raw", ["plain\\ntext", '"first\\nsecond"', "  42  ",
    "{plain prose}", "[reference] prose", "\ud800", b'{"x":"a"}', None])
def test_literal_fallback_returns_the_same_object(raw):
    for guard in GUARDS:
        assert guard.source_lexical_parts(raw)[0] is raw


def test_dictionary_collection_boundary_and_utf8_boundary():
    hundred = json.dumps({f"k{i}": "x" for i in range(100)}, separators=(",", ":"))
    hundred_one = json.dumps({f"k{i}": "x" for i in range(101)}, separators=(",", ":"))
    at_bytes = '{"x":"' + "가" * 497 + 'a"}'
    over_bytes = at_bytes[:-2] + 'a"}'
    assert len(at_bytes.encode()) == 1500
    for guard in GUARDS:
        assert len(guard.source_lexical_parts(hundred)) == 200
        assert guard.source_lexical_parts(hundred_one) == (hundred_one,)
        assert guard.source_lexical_parts(at_bytes) != (at_bytes,)
        assert guard.source_lexical_parts(over_bytes) == ("x", "가" * 497 + "aa")


def test_keys_values_do_not_complete_markup_or_character_entities():
    data = {"<span": "middle", ">actual": "text", "&am": "p;entity", "&#x": "61;literal"}
    for guard in GUARDS:
        expected = set().union(*(guard._ground_tokens(p) for kv in data.items() for p in kv))
        actual = set().union(*(guard._ground_tokens(p) for p in guard.source_lexical_parts(json.dumps(data))))
        assert actual == expected
        assert {"span", "middle", "actual", "text", "entity", "literal"} <= actual


def test_metadata_and_source_body_not_reassembled_as_markup(context_guard, now):
    pack = pack_for(context_guard, now, json.dumps({"body": ">alpha beta gamma"}))
    source = pack.sources[0].model_copy(update={"title": "<span"})
    pack = pack.model_copy(update={"sources": (source,)})
    result = candidate(pack, "alpha beta gamma")
    assert remote_result(REMOTE, pack, result) == result
    local_result(LOCAL, pack, result)


@pytest.mark.parametrize("overlap,accepted", [(6, False), (7, True)])
def test_existing_seventy_percent_fact_threshold(context_guard, now, overlap, accepted):
    terms = "alpha beta gamma delta epsilon zeta eta theta iota kappa".split()
    pack = pack_for(context_guard, now, json.dumps({"body": " ".join(terms[:overlap])}))
    result = candidate(pack, " ".join(terms))
    for remote, local in ((REMOTE, LOCAL),):
        response = remote_result(remote, pack, result)
        if accepted:
            assert response == result
            local_result(local, pack, result)
        else:
            assert response["warnings"] == ["contract_guard:claim_not_grounded"]
            with pytest.raises(local.ResponseGuardError, match="citation_assertion_mismatch"):
                local_result(local, pack, result)


@pytest.mark.parametrize("kind", ["inference", "recommendation"])
def test_nonfact_kind_does_not_gain_a_new_lexical_rule(context_guard, now, kind):
    pack = pack_for(context_guard, now, '{"body":"alpha"}')
    result = candidate(pack, "omega")
    result["claims"][0]["kind"] = kind
    for remote, local in ((REMOTE, LOCAL),):
        assert remote_result(remote, pack, result) == result
        local_result(local, pack, result)


@pytest.mark.parametrize("text,error", [
    ("alpha 99%", "unverified_numeric_claim ($.payload.text)"),
    ("alpha DL-9999", "citation_assertion_mismatch (claims[0].text)"),
    ("alpha https://wrong.example.test/unknown", "unverified_url ($.payload.text)"),
])
def test_local_numeric_identifier_url_checks_are_unchanged(context_guard, now, text, error):
    pack = pack_for(context_guard, now, '{"body":"alpha 50% DL-9044"}')
    result = candidate(pack, text)
    with pytest.raises(LOCAL.ResponseGuardError) as failure:
        local_result(LOCAL, pack, result)
    assert str(failure.value) == error


def test_uncited_decoded_source_cannot_supply_grounding(context_guard, now):
    pack = pack_for(context_guard, now, '{"body":"alpha"}')
    other = pack.sources[0].model_copy(update={"source_id": "source-other", "content": '{"body":"first\\nsecond"}'})
    pack = pack.model_copy(update={"sources": (pack.sources[0], other)})
    result = candidate(pack, "second")
    assert remote_result(REMOTE, pack, result)["warnings"] == ["contract_guard:claim_not_grounded"]
    with pytest.raises(LOCAL.ResponseGuardError, match="citation_assertion_mismatch"):
        local_result(LOCAL, pack, result)
    result["citations"][0]["source_id"] = "source-other"
    assert remote_result(REMOTE, pack, result) == result
    local_result(LOCAL, pack, result)


def test_nested_encoded_json_stays_literal_and_dlp_stays_upstream(context_guard, now):
    nested = json.dumps({"inner": "first\nsecond"}, separators=(",", ":"))
    pack = pack_for(context_guard, now, json.dumps({"outer": nested}))
    for guard in GUARDS:
        parts = guard.source_lexical_parts(pack.sources[0].content)
        assert nested in parts
        tokens = set().union(*(guard._ground_tokens(p) for p in parts))
        assert "nsecond" in tokens and "second" not in tokens
    clean = pack_for(context_guard, now, '{"body":"\\u0061lice@example.test first\\nsecond"}')
    for guard in GUARDS:
        assert all("alice@example.test" not in part for part in guard.source_lexical_parts(clean.sources[0].content))
