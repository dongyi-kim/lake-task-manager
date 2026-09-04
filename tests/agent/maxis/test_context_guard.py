from __future__ import annotations

from datetime import timedelta
import json

import pytest

from app.agent.maxis import ContextGuard, ContextGuardError, ContextPolicy


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
            content=("담당 skcc.x1042, owner@example.com " + "가" * 1700),
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
    assert all(len(source.content) <= 1500 for source in pack.sources)
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
        "value": "가" * 3900,
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
