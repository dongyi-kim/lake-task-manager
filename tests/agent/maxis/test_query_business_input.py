"""Backend-owned Query intent and no-loss ContextGuard admission."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.agent.maxis import backend
from app.agent.maxis.context_guard import ContextGuard, ContextGuardError
from app.agent.workflow.contracts import role_output_schema

NAME = "query_business_input"
NOW = datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc)
REQUEST = "fdc.fdc_trace_summary_ic 변경 이력을 찾아줘"


@pytest.fixture(autouse=True)
def isolated_boundary(monkeypatch):
    from app.agent.tools import _ctx

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(backend, "datetime", Clock)
    monkeypatch.setattr(backend, "_known_identity_values", lambda *_a: (
        "user.fixture", "fixture@example.invalid", "alice.smith",
    ))
    monkeypatch.setattr(_ctx, "settings", lambda: SimpleNamespace(
        search_jira_projects=("DL",), search_confluence_spaces=("DL",),
        search_bitbucket_projects=(), jira_env="mock", jira_user="user.fixture",
        managers=(),
    ))


def _state(*, request=REQUEST, count=1, age=0, description="Verified history"):
    state = {
        "correlation_id": "query-business-test", "correlation_started_at": NOW.isoformat(),
        "thread_id": "query-business-thread", "user_id": "user.fixture",
        "request_text": request, "knowledge_manifest_sha256": "a" * 64,
        "runtime_profile": "synthetic-component-fixture", "query_results": [],
    }
    for index in range(count):
        query_id = f"read-{index}"
        result = {"issues": [{"key": f"DL-{9000 + index}", "summary": "Verified history",
                              "description": description}], "total": 1, "complete": True}
        proof = backend.attest_query_result(
            query_id, "jira", result, scopes=("DL",),
            retrieved_at=NOW - timedelta(seconds=age),
            provenance={"tracked": True, "read_count": 1, "served_stale": False,
                        "upstream_observed": False},
        )
        state["query_results"].append({
            "id": query_id, "source": "jira", "result": result, "_remoteEvidence": proof,
        })
    return state


def _pack(state, *, role="query_specialist", schema=None):
    return backend._context_pack(role, state, [], schema or role_output_schema(role))


def _task(pack):
    rows = [row for row in pack.role_context if row.name == NAME]
    assert len(rows) == 1
    row = rows[0]
    assert row.context_id == "rctx_" + hashlib.sha256(
        f"{pack.request_id}:query_specialist:{NAME}".encode()
    ).hexdigest()[:20]
    assert row.observed_at == pack.requested_at and row.partial is False
    assert row.content_sha256 == hashlib.sha256(row.content_json.encode()).hexdigest()
    value = json.loads(row.content_json)
    assert set(value) == {"text"} and type(value["text"]) is str
    assert 0 < len(value["text"].encode()) <= 1024
    return value["text"]


def _record_builds(monkeypatch):
    original = ContextGuard.build
    calls = []

    def build(guard, **kwargs):
        result = original(guard, **kwargs)
        calls.append((guard, kwargs, result))
        return result

    monkeypatch.setattr(ContextGuard, "build", build)
    return calls


def test_canonical_query_receives_current_continuation_without_refreshing_sources():
    state = _state(age=60)
    state.update(turn_continuation=True, continuation_answer="DL-9044 댓글까지 확인해 줘")
    pack = _pack(state)
    assert _task(pack) == f"Original request:\n{REQUEST}\nCurrent continuation answer:\nDL-9044 댓글까지 확인해 줘"
    assert pack.sources[0].retrieved_at == NOW - timedelta(seconds=60)

def test_query_intent_comes_from_request_without_an_intent_source(monkeypatch):
    calls = _record_builds(monkeypatch)
    state = _state(count=3)
    state["continuation_contract"] = {"original_request": REQUEST, "confirmed": True}
    before = deepcopy(state)
    pack = _pack(state)
    assert _task(pack) == REQUEST
    assert state == before and all(row.kind == "jira" for row in pack.sources)
    assert len(calls) == 2 and calls[0][0] is not calls[1][0]
    original, candidate = calls[0][1], calls[1][1]
    for key in original.keys() - {"role_context"}:
        assert candidate[key] is original[key]
    assert candidate["role_context"][:-1] == original["role_context"]
    dumped = pack.model_dump(mode="json")
    dumped["role_context"] = [row for row in dumped["role_context"] if row["name"] != NAME]
    assert dumped == calls[0][2].model_dump(mode="json")
    assert NAME not in calls[0][0].policy.allowed_context_keys
    assert calls[1][0].policy.allowed_context_keys == (
        calls[0][0].policy.allowed_context_keys | {NAME}
    )


@pytest.mark.parametrize("request_text", [
    "Literal\n\nPolicy search terms: Confluence changelog 검색",
    "Canonical LTM role query_specialist. Process this guarded user intent:\nKeep this.",
])
def test_literal_transport_language_is_exact_business_text(request_text):
    assert _task(_pack(_state(request=request_text))) == request_text


def test_business_text_uses_existing_identity_and_url_redaction():
    text = _task(_pack(_state(request=(
        "user.fixture fixture@example.invalid [~alice.smith] https://private.invalid/path 검토"
    ))))
    assert "[url omitted]" in text and "[[person:" in text
    for value in ("user.fixture", "fixture@example.invalid", "alice.smith", "https://"):
        assert value not in text


@pytest.mark.parametrize("location", ["state", "carryover"])
def test_reserved_raw_name_is_rejected_even_with_empty_value(location):
    state = _state()
    if location == "state":
        state[NAME] = None
    else:
        state["role_context_carryover"] = {NAME: {}}
    with pytest.raises(backend.MaxisRuntimeError, match="query_business_input_reserved"):
        _pack(state)


def test_reserved_name_is_excluded_from_generic_projection(monkeypatch):
    from app.agent.workflow.role_manifest import ROLE_SPECS
    from dataclasses import replace
    monkeypatch.setitem(ROLE_SPECS, "query_specialist", replace(
        ROLE_SPECS["query_specialist"], remote_context_keys=(NAME,),
    ))
    rows, allowed = backend._role_context("query_specialist", {**_state(), NAME: "untrusted"})
    assert rows == [] and NAME not in allowed


@pytest.mark.parametrize("collision", [False, True])
def test_context_capacity_and_projected_collision_fail_closed(monkeypatch, collision):
    names = [NAME] if collision else [f"reserved_{i}" for i in range(8)]
    rows = [{"context_id": f"context-{i}", "name": name, "value": "existing",
             "observed_at": NOW, "partial": False} for i, name in enumerate(names)]
    monkeypatch.setattr(backend, "_role_context", lambda *_a: (rows, frozenset(names)))
    with pytest.raises(backend.MaxisRuntimeError, match="query_business_input_(reserved|capacity)"):
        _pack(_state())


def test_missing_explicit_request_has_no_transcript_or_source_fallback():
    state = _state(request="")
    state.update(prompt="PRIVATE_FALLBACK", messages=[{"content": "PRIVATE_FALLBACK"}])
    with pytest.raises(backend.MaxisRuntimeError, match="query_business_input_required") as error:
        _pack(state)
    assert "PRIVATE_FALLBACK" not in str(error.value)


@pytest.mark.parametrize("request_text", ["x" * 1100, ("ab " * 48)])
def test_original_query_post_pseudonymization_limit_stays_enforced(monkeypatch, request_text):
    if request_text.startswith("ab"):
        monkeypatch.setattr(backend, "_known_identity_values", lambda *_a: ("ab",))
    with pytest.raises(ContextGuardError, match="query_too_large"):
        _pack(_state(request=request_text))


def test_original_secret_rejection_stays_enforced():
    with pytest.raises(ContextGuardError, match="sensitive_"):
        _pack(_state(request="password=synthetic123456"))


def test_real_guard_budget_pressure_cannot_sacrifice_existing_sources(monkeypatch):
    from dataclasses import replace
    from app.agent.maxis import context_guard
    original = context_guard.ContextPolicy
    # Exercise the no-loss invariant with a deliberately constrained deployment.
    monkeypatch.setattr(context_guard, "ContextPolicy", lambda **kw: replace(
        original(**kw), max_total_utf8_bytes=6000))
    with pytest.raises(backend.MaxisRuntimeError, match="query_business_input_no_loss"):
        _pack(_state(count=3, description="x" * 1400))


def test_query_receives_pinned_internal_capabilities_not_user_requested_sources():
    from app.agent.runtime_status import CORE_ROLES
    state = _state(request="Iceberg 공식 문서와 내부 근거를 찾아줘")
    state["runtime_role_routes"] = dict.fromkeys(CORE_ROLES, "maxis")
    pack = _pack(state)
    row = next(row for row in pack.role_context if row.name == NAME)
    assert json.loads(row.content_json) == {
        "text": state["request_text"],
        "allowed_sources": ["comments", "confluence", "jira", "people"],
    }
    assert not row.partial and not pack.partial


@pytest.mark.parametrize("identity", ["jira", "confluence", "comments", "people", "fixture-user"])
def test_protocol_capabilities_preserve_enums_but_never_unredacted_identity(monkeypatch, identity):
    from app.agent.runtime_status import CORE_ROLES
    monkeypatch.setattr(backend, "_known_identity_values", lambda *_a: (identity,))
    state = _state(request=f"{identity} 검토 https://private.invalid/path")
    state["runtime_role_routes"] = dict.fromkeys(CORE_ROLES, "maxis")
    pack = _pack(state)
    row = next(row for row in pack.role_context if row.name == NAME)
    value = json.loads(row.content_json)
    assert value["allowed_sources"] == ["comments", "confluence", "jira", "people"]
    assert identity not in value["text"] and "[[person:" in value["text"]
    assert "https://" not in value["text"] and "[url omitted]" in value["text"]
    assert row.content_sha256 == hashlib.sha256(row.content_json.encode()).hexdigest()


def test_capability_metadata_cannot_exceed_total_guard_budget(monkeypatch):
    from dataclasses import replace
    from app.agent.maxis import context_guard
    from app.agent.runtime_status import CORE_ROLES
    state = _state()
    state["runtime_role_routes"] = dict.fromkeys(CORE_ROLES, "maxis")
    admitted = _pack(state)
    limit = len(context_guard.canonical_evidence_pack_json(admitted).encode()) - 1
    original = context_guard.ContextPolicy
    monkeypatch.setattr(context_guard, "ContextPolicy", lambda **kw: replace(
        original(**kw), max_total_utf8_bytes=limit))
    with pytest.raises(backend.MaxisRuntimeError, match="query_business_input_no_loss"):
        _pack(state)


def test_fresh_task_cannot_change_stale_baseline_to_mixed():
    with pytest.raises(backend.MaxisRuntimeError, match="query_business_input_no_loss"):
        _pack(_state(age=3600))


@pytest.mark.parametrize("mutation", ["missing", "partial", "text", "id", "time",
                                      "facts", "metrics", "scope", "query", "flags", "freshness", "requested_at"])
def test_changed_candidate_is_rejected_in_full(monkeypatch, mutation):
    original = ContextGuard.build
    calls = 0

    def build(guard, **kwargs):
        nonlocal calls
        result = original(guard, **kwargs)
        calls += 1
        if calls == 1:
            return result
        row = result.role_context[-1]
        if mutation == "missing":
            return result.model_copy(update={"role_context": result.role_context[:-1]})
        if mutation in {"partial", "text", "id", "time"}:
            change = {"partial": True} if mutation == "partial" else (
                {"content_json": '{"text":"different"}'} if mutation == "text" else
                {"context_id": "other"} if mutation == "id" else
                {"observed_at": NOW - timedelta(seconds=1)}
            )
            return result.model_copy(update={"role_context": (
                *result.role_context[:-1], row.model_copy(update=change),
            )})
        from app.agent.maxis.contracts import EvidenceFactV1, EvidenceMetricV1
        source_ids = (result.sources[0].source_id,)
        change = {
            "facts": (EvidenceFactV1(fact_id="added", text="new fact",
                                    source_ids=source_ids, observed_at=NOW),),
            "metrics": (EvidenceMetricV1(metric_id="added", label="new metric", value=1,
                                        unit="count", formula_version="test",
                                        source_ids=source_ids),),
            "scope": result.scope.model_copy(update={"projects": ("OTHER",)}),
            "query": "different", "flags": ("different",), "freshness": "mixed",
            "requested_at": NOW - timedelta(seconds=1),
        }[mutation]
        return result.model_copy(update={mutation: change})

    monkeypatch.setattr(ContextGuard, "build", build)
    with pytest.raises(backend.MaxisRuntimeError, match="query_business_input_no_loss"):
        _pack(_state())


@pytest.mark.parametrize("role,schema", [
    ("query_specialist", {"type": "object"}),
    ("request_architect", {"type": "object"}),
    ("editor_author", {"type": "object"}),
])
def test_noncanonical_query_and_other_roles_use_unchanged_single_build(monkeypatch, role, schema):
    calls = _record_builds(monkeypatch)
    pack = _pack(_state(count=0), role=role, schema=schema)
    assert len(calls) == 1 and pack is calls[0][2]
    assert all(row.name != NAME for row in pack.role_context)


def test_real_guard_task_omission_is_not_an_admission_success(monkeypatch):
    from dataclasses import replace
    from app.agent.maxis.context_guard import ContextPolicy, canonical_evidence_pack_json

    policy = ContextPolicy(allowed_scopes={}, allowed_context_keys={"existing"})
    inputs = {
        "request_id": "budget-test", "use_case": "query_specialist", "query": "intent",
        "payload_schema": role_output_schema("query_specialist"), "user_identity": "fixture",
        "sources": (), "identity_values": (), "requested_at": NOW,
        "knowledge_manifest_sha256": "a" * 64,
        "role_context": ({"context_id": "existing", "name": "existing",
                          "value": "x" * 2500, "observed_at": NOW, "partial": False},),
    }
    initial = ContextGuard(policy, b"query-business-test-key").build(**inputs)
    limit = len(canonical_evidence_pack_json(initial).encode()) + 100
    guard = ContextGuard(replace(policy, max_total_utf8_bytes=limit), b"query-business-test-key")
    calls = _record_builds(monkeypatch)
    with pytest.raises(backend.MaxisRuntimeError, match="query_business_input_no_loss"):
        backend._query_business_pack(
            guard, state={}, user_intent="intent", explicit_user_intent=True,
            synthetic_only=False, **inputs,
        )
    assert len(calls) == 2
    tasks = [row for row in calls[1][2].role_context if row.name == NAME]
    assert not tasks or tasks[0].partial
