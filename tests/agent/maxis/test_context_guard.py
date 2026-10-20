from __future__ import annotations

from datetime import timedelta
import json

import pytest

from app.agent.maxis import ContextGuard, ContextGuardError, ContextPolicy
from app.agent.maxis.context_guard import (
    MAX_CONTEXT_PACK_UTF8_BYTES,
    MAX_QUERY_UTF8_BYTES,
    MAX_ROLE_CONTEXT_UTF8_BYTES,
    MAX_SOURCE_CHARACTERS,
    MAX_SOURCE_UTF8_BYTES,
    canonical_evidence_pack_json,
)


_PAYLOAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


def _source(index, now, *, content="근거", scope="DL", partial=False):
    return {
        "source_id": f"src-{index}",
        "kind": "jira",
        "canonical_id": f"DL-{9000 + index}",
        "title": f"업무 {index}",
        "scope": scope,
        "url": f"https://jira.example.local/browse/DL-{9000 + index}",
        "content": content,
        "retrieved_at": now,
        "partial": partial,
    }


def test_context_guard_pseudonymizes_and_bounds_sources(context_guard, now):
    rows = [
        _source(
            index,
            now,
            content=("담당 skcc.x1042, owner@example.com " + "가" * 12001),
        )
        for index in range(11)
    ]
    pack = context_guard.build(
        request_id="req-001",
        use_case="task_planner",
        query="skcc.x1042와 owner@example.com 담당 업무",
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="skcc.x1042",
        sources=rows,
        requested_at=now,
    )
    assert len(pack.sources) == 10
    assert all(len(source.content) <= MAX_SOURCE_CHARACTERS for source in pack.sources)
    assert "skcc.x1042" not in pack.query
    assert "owner@example.com" not in pack.query
    assert pack.pseudonymous_user.startswith("ltm_")
    assert pack.partial is True
    assert {"source_limit", "source_truncated"}.issubset(pack.flags)


def test_context_guard_rejects_secret_without_echoing_it(context_guard, now):
    secret = "Authorization: Bearer top-secret-value-12345"
    with pytest.raises(ContextGuardError) as caught:
        context_guard.build(
            request_id="req-001",
            use_case="task_planner",
            query="업무를 정리해 줘",
            payload_schema=_PAYLOAD_SCHEMA,
            user_identity="skcc.x1042",
            sources=[_source(1, now, content=secret)],
            requested_at=now,
        )
    assert caught.value.code == "sensitive_authorization"
    assert "top-secret" not in str(caught.value)


def test_complete_korean_json_is_not_truncated_at_ascii_byte_limit(context_guard, now):
    raw = json.dumps({"summary": "정상 조회 결과 " * 75}, ensure_ascii=False, separators=(",", ":"))
    assert len(raw) < 1500 < len(raw.encode()) < 4500
    pack = context_guard.build(
        request_id="korean-source", use_case="editor_author", query="검토 요청",
        payload_schema=_PAYLOAD_SCHEMA, user_identity="synthetic",
        sources=[_source(1, now, content=raw)], requested_at=now,
    )
    assert pack.sources[0].content == raw
    assert not pack.partial and not pack.sources[0].truncated
    assert len(canonical_evidence_pack_json(pack).encode()) <= 6144


def test_complete_multi_ticket_query_and_context_fit_without_truncation(now):
    guard = ContextGuard(
        ContextPolicy(allowed_scopes={"jira": ("DL",)}, allowed_context_keys={"evidence"}),
        b"test-pseudonym-key-32-bytes-long",
    )
    tickets = [{"key": f"DL-{index}", "description": "검증 전 운영 반영은 금지한다. " * 18,
                "comments": [{"body": "결과표와 소비 여부를 별도로 검증한다. " * 8}]}
               for index in range(5)]
    content = json.dumps({"tickets": tickets, "complete": True}, ensure_ascii=False)
    source = {**_source(1, now, content=content), "url": ""}
    pack = guard.build(
        request_id="multi-ticket-result", use_case="work_architect", query="PoC 계획",
        payload_schema=_PAYLOAD_SCHEMA, user_identity="synthetic",
        sources=[source], requested_at=now,
        role_context=[{"context_id": "ctx-evidence", "name": "evidence",
                       "value": tickets, "observed_at": now}],
    )
    assert len(content) > 1500 and len(content.encode()) > 6144
    assert json.loads(pack.sources[0].content) == json.loads(content)
    assert json.loads(pack.role_context[0].content_json) == tickets
    assert pack.freshness == "fresh" and not pack.partial
    assert len(canonical_evidence_pack_json(pack).encode()) <= MAX_CONTEXT_PACK_UTF8_BYTES


@pytest.mark.parametrize("text", ["a" * 12001, "😀" * 9001], ids=["character-overflow", "byte-overflow"])
def test_source_character_and_byte_bounds_remain_fail_closed(context_guard, now, text):
    pack = context_guard.build(
        request_id="bounded-source", use_case="editor_author", query="검토 요청",
        payload_schema=_PAYLOAD_SCHEMA, user_identity="synthetic",
        sources=[_source(1, now, content=text)], requested_at=now,
    )
    assert pack.partial and pack.sources[0].truncated
    assert len(pack.sources[0].content) <= MAX_SOURCE_CHARACTERS
    assert len(pack.sources[0].content.encode()) <= MAX_SOURCE_UTF8_BYTES
    assert len(canonical_evidence_pack_json(pack).encode()) <= MAX_CONTEXT_PACK_UTF8_BYTES


def test_direct_pack_cannot_bypass_per_source_utf8_bound(context_guard, now):
    import hashlib
    from app.agent.maxis.context_guard import validate_pack_safety
    pack = context_guard.build(
        request_id="direct-pack", use_case="editor_author", query="검토 요청",
        payload_schema=_PAYLOAD_SCHEMA, user_identity="synthetic",
        sources=[_source(1, now)], requested_at=now,
    )
    content = "😀" * 9001
    source = pack.sources[0].model_copy(update={
        "content": content, "content_sha256": hashlib.sha256(content.encode()).hexdigest()})
    with pytest.raises(ContextGuardError, match="source_too_large"):
        validate_pack_safety(pack.model_copy(update={"sources": (source,)}))


def test_direct_pack_cannot_bypass_role_context_utf8_bound(context_guard, now):
    import hashlib
    from app.agent.maxis.context_guard import validate_pack_safety
    from app.agent.maxis.contracts import RoleContextV1
    pack = context_guard.build(
        request_id="direct-role-context", use_case="work_architect", query="계획",
        payload_schema=_PAYLOAD_SCHEMA, user_identity="synthetic",
        sources=[], requested_at=now,
    )
    raw = json.dumps("가" * 6000, ensure_ascii=False)
    row = RoleContextV1(context_id="ctx-large", name="situation", content_json=raw,
        content_sha256=hashlib.sha256(raw.encode()).hexdigest(), observed_at=now)
    with pytest.raises(ContextGuardError, match="role_context_too_large"):
        validate_pack_safety(pack.model_copy(update={"role_context": (row,)}))


@pytest.mark.parametrize(
    "field", [
        "password", "db_password", "token", "authToken", "api_key",
        "client_secret", "private_key_data", "cookie",
    ]
)
def test_context_guard_rejects_secret_bearing_json_source_fields(
    context_guard, now, field
):
    source = _source(
        1,
        now,
        content=json.dumps({field: "opaque-very-secret-value"}),
    )
    with pytest.raises(ContextGuardError) as caught:
        context_guard.build(
            request_id="req-secret-json",
            use_case="research_analyst",
            query="inspect",
            payload_schema=_PAYLOAD_SCHEMA,
            user_identity="employee",
            sources=[source],
            requested_at=now,
        )
    assert caught.value.code == "forbidden_authority_field"


def test_context_guard_rejects_source_outside_allowlist(context_guard, now):
    with pytest.raises(ContextGuardError) as caught:
        context_guard.build(
            request_id="req-001",
            use_case="task_planner",
            query="업무를 정리해 줘",
            payload_schema=_PAYLOAD_SCHEMA,
            user_identity="skcc.x1042",
            sources=[_source(1, now, scope="OTHER")],
            requested_at=now,
        )
    assert caught.value.code == "scope_not_allowed"


def test_context_guard_marks_mixed_freshness_and_partial_sources(context_guard, now):
    stale = _source(1, now - timedelta(minutes=20))
    fresh = _source(2, now, partial=True)
    pack = context_guard.build(
        request_id="req-001",
        use_case="pmo_brief",
        query="변화를 알려 줘",
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="skcc.x1042",
        sources=[stale, fresh],
        requested_at=now,
    )
    assert pack.freshness == "mixed"
    assert pack.partial is True
    assert {"stale_source", "partial_source"}.issubset(pack.flags)


def test_stale_context_is_not_mislabeled_as_partial(context_guard, now):
    pack = context_guard.build(
        request_id="req-001",
        use_case="pmo_brief",
        query="변화를 알려 줘",
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="skcc.x1042",
        sources=[_source(1, now - timedelta(minutes=20))],
        requested_at=now,
    )
    assert pack.freshness == "stale"
    assert pack.partial is False
    assert pack.flags == ("stale_source",)


def test_context_guard_drops_facts_for_omitted_source_with_explicit_flag(context_guard, now):
    rows = [_source(index, now) for index in range(11)]
    pack = context_guard.build(
        request_id="req-001",
        use_case="task_planner",
        query="업무를 정리해 줘",
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="skcc.x1042",
        sources=rows,
        facts=[{
            "fact_id": "fact-11",
            "text": "잘린 출처의 사실",
            "source_ids": ["src-10"],
            "observed_at": now,
        }],
        requested_at=now,
    )
    assert pack.facts == ()
    assert "fact_source_omitted" in pack.flags


def test_role_context_is_separate_from_evidence_and_safely_pseudonymized(now):
    guard = ContextGuard(
        ContextPolicy(
            allowed_scopes={"jira": ("DL",)},
            allowed_source_hosts={"jira": ("jira.example.local",)},
            allowed_context_keys=frozenset({"request_plan", "draft"}),
        ),
        b"test-pseudonym-key-32-bytes-long",
    )
    pack = guard.build(
        request_id="req-001",
        use_case="work_architect",
        query="업무 초안을 작성해 줘",
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="skcc.x1042",
        sources=[_source(1, now)],
        role_context=[{
            "context_id": "ctx-1",
            "name": "request_plan",
            "value": {"owner": "skcc.x9999", "reviewer": "reviewer@example.com"},
            "observed_at": now,
        }],
        requested_at=now,
    )
    assert len(pack.sources) == 1
    assert len(pack.role_context) == 1
    context_value = json.loads(pack.role_context[0].content_json)
    assert context_value["owner"].startswith("[[person:")
    assert context_value["reviewer"].startswith("[[person:")
    assert pack.role_context[0].content_sha256


def test_arbitrary_jira_mentions_are_always_pseudonymized(context_guard, now):
    source = _source(1, now)
    source["content"] = '{"assignee":"[~alice.smith]","reviewer":"[~외부사용자01]"}'
    pack = context_guard.build(
        request_id="req-001",
        use_case="editor_author",
        query="[~alice.smith]에게 상태 공유를 작성해 줘",
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="operator",
        sources=[source],
        requested_at=now,
    )
    wire = pack.model_dump_json()
    assert "alice.smith" not in wire
    assert "외부사용자01" not in wire
    assert wire.count("[[person:") >= 3


def test_role_context_rejects_unlisted_keys_and_nested_authority(now):
    guard = ContextGuard(
        ContextPolicy(
            allowed_scopes={},
            allowed_context_keys=frozenset({"draft"}),
        ),
        b"test-pseudonym-key-32-bytes-long",
    )
    base = dict(
        request_id="req-001",
        use_case="auditor",
        query="초안을 검토해 줘",
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="skcc.x1042",
        sources=[],
        requested_at=now,
    )
    with pytest.raises(ContextGuardError) as key_error:
        guard.build(**base, role_context=[{
            "context_id": "ctx-1", "name": "raw_state", "value": {},
            "observed_at": now,
        }])
    assert key_error.value.code == "context_key_not_allowed"
    with pytest.raises(ContextGuardError) as authority_error:
        guard.build(**base, role_context=[{
            "context_id": "ctx-1", "name": "draft",
            "value": {"nested": {"approvalToken": "forged"}},
            "observed_at": now,
        }])
    assert authority_error.value.code == "forbidden_authority_field"


@pytest.mark.parametrize(
    "value", ["a" * 8190, "가" * 5460 + "ab", "🙂" * 4095 + "ab"],
    ids=["ascii", "korean", "emoji"],
)
def test_role_context_guard_keeps_exact_utf8_boundary_and_rejects_overflow(now, value):
    guard = ContextGuard(
        ContextPolicy(allowed_scopes={}, allowed_context_keys=frozenset({"situation"})),
        b"test-pseudonym-key-32-bytes-long",
    )
    base = dict(
        request_id="req-context-boundary", use_case="knowledge_curator",
        query="정리해 줘", payload_schema=_PAYLOAD_SCHEMA, user_identity="operator",
        sources=[], requested_at=now,
    )
    row = {"context_id": "ctx-1", "name": "situation", "value": value, "observed_at": now}
    encoded = json.dumps(value, ensure_ascii=False)
    assert len(encoded) == 8192 or len(encoded.encode("utf-8")) == MAX_ROLE_CONTEXT_UTF8_BYTES

    pack = guard.build(**base, role_context=[row])

    assert json.loads(pack.role_context[0].content_json) == value
    assert pack.partial is False
    with pytest.raises(ContextGuardError) as caught:
        guard.build(**base, role_context=[{**row, "value": value + "a"}])
    assert caught.value.code == "role_context_too_large"


def test_stale_role_context_controls_freshness_without_sources(now):
    guard = ContextGuard(
        ContextPolicy(
            allowed_scopes={},
            allowed_context_keys=frozenset({"draft"}),
            freshness_seconds=900,
        ),
        b"test-pseudonym-key-32-bytes-long",
    )
    pack = guard.build(
        request_id="req-001",
        use_case="work_architect",
        query="초안을 이어서 작성해 줘",
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="skcc.x1042",
        sources=[],
        role_context=[{
            "context_id": "ctx-1",
            "name": "draft",
            "value": {"summary": "과거 초안"},
            "observed_at": now - timedelta(minutes=20),
        }],
        requested_at=now,
    )
    assert pack.freshness == "stale"
    assert "stale_role_context" in pack.flags


def test_context_guard_rejects_source_host_outside_kind_allowlist(now):
    guard = ContextGuard(
        ContextPolicy(
            allowed_scopes={"jira": ("DL",)},
            allowed_source_hosts={"jira": ("jira.example.local",)},
        ),
        b"test-pseudonym-key-32-bytes-long",
    )
    source = _source(1, now)
    source["url"] = "https://lookalike.example/browse/DL-9001"
    with pytest.raises(ContextGuardError) as caught:
        guard.build(
            request_id="req-001",
            use_case="task_planner",
            query="업무를 정리해 줘",
            payload_schema=_PAYLOAD_SCHEMA,
            user_identity="skcc.x1042",
            sources=[source],
            requested_at=now,
        )
    assert caught.value.code == "source_host_not_allowed"


def test_context_guard_requires_host_policy_for_nonempty_urls(now):
    guard = ContextGuard(
        ContextPolicy(allowed_scopes={"jira": ("DL",)}),
        b"test-pseudonym-key-32-bytes-long",
    )
    with pytest.raises(ContextGuardError) as caught:
        guard.build(
            request_id="req-001",
            use_case="task_planner",
            query="업무를 정리해 줘",
            payload_schema=_PAYLOAD_SCHEMA,
            user_identity="skcc.x1042",
            sources=[_source(1, now)],
            requested_at=now,
        )
    assert caught.value.code == "source_host_not_configured"


def test_context_guard_pseudonymizes_other_sk_group_identity_prefix(context_guard, now):
    pack = context_guard.build(
        request_id="req-001",
        use_case="task_planner",
        query="skhy.x1234 담당 업무를 정리해 줘",
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="skcc.x1042",
        sources=[_source(1, now, content="담당자는 skhy.x1234이다")],
        requested_at=now,
    )
    assert "skhy.x1234" not in pack.query
    assert "skhy.x1234" not in pack.sources[0].content


def test_ltm_aggregate_source_needs_no_external_scope_or_url(now):
    guard = ContextGuard(
        ContextPolicy(allowed_scopes={}),
        b"test-pseudonym-key-32-bytes-long",
    )
    pack = guard.build(
        request_id="req-001",
        use_case="portfolio_analyst",
        query="팀 집계를 요약해 줘",
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="skcc.x1042",
        sources=[{
            "source_id": "src-ltm-1",
            "kind": "ltm",
            "canonical_id": "ltm-query:people:q-1",
            "title": "LTM 가명 집계",
            "scope": "",
            "url": "",
            "content": '{"activePeople":3,"owner":"skhy.x1234"}',
            "retrieved_at": now,
        }],
        requested_at=now,
    )
    assert pack.sources[0].kind == "ltm"
    assert "skhy.x1234" not in pack.sources[0].content


@pytest.mark.parametrize("payload_schema", [
    {"type": "array", "items": {"type": "string"}},
    {
        "type": "object",
        "properties": {"value": {"$ref": "https://schemas.example.test/value"}},
    },
])
def test_context_guard_rejects_invalid_or_external_payload_schema(
    context_guard, now, payload_schema,
):
    with pytest.raises(ContextGuardError) as caught:
        context_guard.build(
            request_id="req-schema",
            use_case="work_architect",
            query="초안을 작성해 줘",
            payload_schema=payload_schema,
            user_identity="operator",
            sources=[],
            requested_at=now,
        )
    assert caught.value.code == "invalid_payload_schema"


def test_context_guard_rejects_authority_field_in_payload_schema(context_guard, now):
    with pytest.raises(ContextGuardError) as caught:
        context_guard.build(
            request_id="req-schema-authority",
            use_case="work_architect",
            query="초안을 작성해 줘",
            payload_schema={
                "type": "object",
                "properties": {"approval_token": {"type": "string"}},
            },
            user_identity="operator",
            sources=[],
            requested_at=now,
        )
    assert caught.value.code == "forbidden_authority_field"


def test_context_guard_reduces_low_priority_role_context_to_total_budget(now):
    keys = tuple(f"context_{index}" for index in range(8))
    guard = ContextGuard(
        ContextPolicy(allowed_scopes={}, allowed_context_keys=keys),
        b"test-pseudonym-key-32-bytes-long",
    )
    rows = [{
        "context_id": f"rctx-{index}",
        "name": key,
        "value": "a" * 8000,
        "observed_at": now,
        "partial": False,
    } for index, key in enumerate(keys)]

    pack = guard.build(
        request_id="req-context-budget",
        use_case="knowledge_curator",
        query="정리해 줘",
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="employee",
        role_context=rows,
        requested_at=now,
        sources=[],
    )

    assert pack.partial is True
    assert {"partial_role_context", "role_context_limit"}.issubset(pack.flags)
    assert len(pack.role_context) < len(rows)
    assert json.loads(pack.role_context[-1].content_json)["omitted"] is True


def test_context_guard_budgets_exact_serialized_json_including_escaping(
    context_guard, now,
):
    # Bound the exact wire representation: escaping and multibyte text must consume
    # the same conservative budget as the serialized Dify paragraph.
    rows = [
        _source(index, now, content=("\\\"" * 6000))
        for index in range(10)
    ]
    pack = context_guard.build(
        request_id="req-exact-wire-budget",
        use_case="task_planner",
        query="\\" * MAX_QUERY_UTF8_BYTES,
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="employee",
        sources=rows,
        requested_at=now,
    )
    wire = canonical_evidence_pack_json(pack)

    assert len(wire.encode("utf-8")) <= MAX_CONTEXT_PACK_UTF8_BYTES
    assert len(pack.query.encode("utf-8")) <= MAX_QUERY_UTF8_BYTES
    assert pack.partial is True
    assert {"total_truncated", "source_truncated"}.issubset(pack.flags)


def test_context_guard_accepts_exact_query_byte_boundary_and_rejects_overflow(
    context_guard, now,
):
    exact = "가" * 341 + "a"
    assert len(exact.encode("utf-8")) == MAX_QUERY_UTF8_BYTES
    pack = context_guard.build(
        request_id="req-query-boundary",
        use_case="task_planner",
        query=exact,
        payload_schema=_PAYLOAD_SCHEMA,
        user_identity="employee",
        sources=[],
        requested_at=now,
    )
    assert pack.query == exact
    assert "query_truncated" not in pack.flags

    with pytest.raises(ContextGuardError) as caught:
        context_guard.build(
            request_id="req-query-overflow",
            use_case="task_planner",
            query=exact + "a",
            payload_schema=_PAYLOAD_SCHEMA,
            user_identity="employee",
            sources=[],
            requested_at=now,
        )
    assert caught.value.code == "query_too_large"
