"""Keep Work Architect's requested work citeable beside connector evidence."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.maxis import ResponseGuard, ResponseGuardError, backend
from app.agent.maxis.context_guard import canonical_evidence_pack_json
from app.agent.maxis.context_guard import ContextGuardError
from app.agent.maxis.contracts import EvidencePackV1
from app.agent.workflow.work_architect.contracts import CREATE_SCHEMA


REQUEST = "Chrome에서 2홉 이상 펼치면 빈 화면이다. 그래프 표시를 기대한다. Bug 초안을 작성해 줘."
FACT = "기존 리니지 뷰어 작업"
MANIFEST = "a" * 64


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    from app.agent.tools import _ctx
    from app.infra import settings

    monkeypatch.setattr(_ctx, "settings", lambda: SimpleNamespace(
        search_jira_projects=("DL",), search_confluence_spaces=(),
        search_bitbucket_projects=(), jira_env="mock", jira_user="user.fixture",
        managers=(),
    ))
    monkeypatch.setattr(settings, "load_people", lambda: {})


def _state(*, count=1, age_seconds=0):
    now = datetime.now(timezone.utc)
    observed = now - timedelta(seconds=age_seconds)
    state = {
        "thread_id": "intent-regression", "correlation_id": "intent-regression-1",
        "correlation_started_at": now.isoformat(), "user_id": "user.fixture",
        "runtime_profile": "synthetic-component-fixture",
        "knowledge_manifest_sha256": MANIFEST, "request_text": REQUEST,
        "query_results": [],
    }
    for index in range(count):
        query_id = f"jira-{index}"
        result = {"issues": [{"key": f"DL-{9000 + index}", "summary": FACT}],
                  "total": 1, "complete": True}
        state["query_results"].append({
            "id": query_id, "source": "jira", "result": result,
            "_remoteEvidence": backend.attest_query_result(
                query_id, "jira", result, scopes=("DL",), retrieved_at=observed,
            ),
        })
    return state


def _pack(state, *, role="work_architect", schema=None):
    return backend._context_pack(role, state, [], schema or {"type": "object"})


def _intent(pack):
    matches = [row for row in pack.sources
               if row.canonical_id.startswith("ltm:user-intent:")]
    assert len(matches) == 1
    return matches[0]


def _draft_response(pack, *, proposal="Chrome에서 2홉 빈 화면 재현을 확인"):
    intent = _intent(pack)
    read = next(row for row in pack.sources if row.kind == "jira")
    payload = {"questions": [], "mode": "task", "items": [{
        "type": "Bug", "summary": proposal, "background": FACT,
        "scope_in": [proposal], "dod": [proposal],
    }]}
    return {
        "contract": "remote-role-result.v1", "role_id": "work_architect",
        "status": "ok", "payload": payload,
        "claims": [
            {"claim_id": "request", "kind": "recommendation", "text": proposal,
             "citation_ids": ["intent"]},
            {"claim_id": "existing", "kind": "fact", "text": FACT,
             "citation_ids": ["read"]},
        ],
        "payload_claims": [
            {"path": path, "claim_ids": ["request"]} for path in (
                "/items/0/summary", "/items/0/scope_in/0", "/items/0/dod/0",
            )
        ] + [{"path": "/items/0/background", "claim_ids": ["existing"]}],
        "citations": [
            {"citation_id": "intent", "source_id": intent.source_id},
            {"citation_id": "read", "source_id": read.source_id},
        ],
        "missing_inputs": [], "warnings": [], "workflow_version": "wf-1",
        "knowledge_version": "kb-1", "knowledge_manifest_sha256": MANIFEST,
        "knowledge_proof": "LTM_RELEASE_SENTINEL_V1=intent-regression",
    }


def _validate(answer, pack):
    return ResponseGuard().validate_remote_role(
        json.dumps(answer, ensure_ascii=False), expected_role_id="work_architect",
        payload_model=CREATE_SCHEMA, evidence=pack,
        expected_knowledge_manifest_sha256=MANIFEST,
    )


def test_source_backed_work_draft_can_cite_request_and_read_separately():
    state = _state()
    before = deepcopy(state)
    pack = _pack(state, schema=CREATE_SCHEMA)
    intent = _intent(pack)
    assert [row.kind for row in pack.sources] == ["jira", "ltm"]
    assert intent.content == REQUEST
    assert intent.scope == intent.url == ""
    assert intent.partial is False and intent.truncated is False
    assert not pack.partial and pack.freshness == "fresh"
    assert len(canonical_evidence_pack_json(pack).encode("utf-8")) <= 6144
    assert state == before
    read_only = _pack({**state, "request_text": ""}, schema=CREATE_SCHEMA)
    assert pack.sources[0] == read_only.sources[0]
    answer = _draft_response(pack)
    remote, payload = _validate(answer, pack)
    assert remote.status == "ok" and payload == answer["payload"]


def test_work_prompt_explains_compact_intent_and_optional_draft_metadata():
    prompt = (Path(__file__).resolve().parents[3] /
              "deploy/maxis-dify/prompts/agent_core.md").read_text(encoding="utf-8")
    for fragment in (
        'exactly `{"source_content_index": N}`', "zero-based",
        "integer in range", "`ltm:user-intent:`", "actual source row's `source_id`",
        "Omit unknown optional parent, owner, and date fields",
        "their absence alone does not prevent a draft",
        "never infer legality from a related ticket",
        "cited recommendations, not verified external facts",
    ):
        assert fragment in prompt
    assert "unresolved owner/date becomes input gap" not in prompt


def test_canonical_work_draft_needs_no_optional_execution_metadata():
    from tools.maxis_role_remote_cases import build_remote_fixture
    import runpy

    built = build_remote_fixture(
        "role-work", observed_at=datetime.now(timezone.utc), manifest_sha256=MANIFEST,
    )
    pack = EvidencePackV1.model_validate_json(json.dumps(built["payload"]["evidencePack"]))
    intent = _intent(pack)
    answer = _draft_response(pack, proposal=intent.content)
    answer["workflow_version"] = "10000000-0000-4000-8000-000000000001"
    item = answer["payload"]["items"][0]
    item["background"] = intent.content
    answer["claims"] = [answer["claims"][0]]
    answer["citations"] = [answer["citations"][0]]
    answer["citations"][0]["locator"] = ""
    answer["payload_claims"][-1]["claim_ids"] = ["request"]
    assert not {"parent", "epic", "assignee", "owner", "duedate"} & item.keys()
    assert _validate(answer, pack)[1] == answer["payload"]

    root = Path(__file__).resolve().parents[3]
    guard = runpy.run_path(str(root / "deploy/maxis-dify/code/contract_guard.py"))["main"]
    result = guard(
        candidate=json.dumps(answer, ensure_ascii=False),
        context_pack=canonical_evidence_pack_json(pack), role_id="work_architect",
        correlation_id=pack.request_id, output_contract="remote-role-result.v1",
        workflow_version=answer["workflow_version"],
        executed_workflow_id=answer["workflow_version"],
        knowledge_version=answer["knowledge_version"],
        sentinel_result=[{"content": answer["knowledge_proof"]}],
    )
    guarded = json.loads(result["result"])
    assert guarded["status"] == "ok" and guarded["warnings"] == [], guarded["warnings"]
    assert guarded["payload"] == answer["payload"]


def test_user_request_cannot_attest_external_facts():
    pack = _pack(_state(), schema=CREATE_SCHEMA)
    answer = _draft_response(pack)
    answer["claims"][0]["kind"] = "fact"
    with pytest.raises(ResponseGuardError) as caught:
        _validate(answer, pack)
    assert caught.value.code == "intent_source_fact_forbidden"


def test_recommendation_cannot_borrow_numeric_request_from_unrelated_read():
    state = _state()
    state["request_text"] += " 3회 재현해 줘."
    pack = _pack(state, schema=CREATE_SCHEMA)
    answer = _draft_response(pack, proposal="3회 재현 확인")
    assert _validate(answer, pack)[0].status == "ok"
    answer["claims"][0]["citation_ids"] = ["read"]
    with pytest.raises(ResponseGuardError) as caught:
        _validate(answer, pack)
    assert caught.value.code == "citation_assertion_mismatch"


@pytest.mark.parametrize("request_text", (None, "", "  "))
def test_missing_request_does_not_recover_transcript_or_prompt(request_text):
    state = _state()
    state.update(request_text=request_text, prompt="STALE_PROMPT_MUST_NOT_CROSS",
                 messages=[{"content": "STALE_TRANSCRIPT_MUST_NOT_CROSS"}],
                 continuation_answer="UNRELATED_ANSWER_MUST_NOT_CROSS")
    pack = _pack(state)
    assert [row.kind for row in pack.sources] == ["jira"]
    assert "MUST_NOT_CROSS" not in canonical_evidence_pack_json(pack)


def test_intent_keeps_existing_identity_and_url_projection():
    state = _state()
    state["request_text"] = (
        "담당 alice.fixture에게 https://internal.example/private 검토를 요청해 줘"
    )
    state["_maxis_identity_values"] = ["alice.fixture"]
    pack = _pack(state)
    intent = _intent(pack)
    wire = canonical_evidence_pack_json(pack)
    assert "alice.fixture" not in wire and "internal.example" not in wire
    assert "[[person:" in intent.content and "[url omitted]" in intent.content
    assert pack.query == "Use the guarded user intent source."


def test_fresh_request_does_not_refresh_stale_connector_source():
    pack = _pack(_state(age_seconds=3600))
    assert _intent(pack).retrieved_at == pack.requested_at
    assert pack.freshness == "mixed" and "stale_source" in pack.flags
    assert (pack.requested_at - pack.sources[0].retrieved_at).total_seconds() >= 3600


def test_work_intent_never_displaces_ten_verified_sources():
    state = _state(count=10)
    before = deepcopy(state)
    with pytest.raises(backend.MaxisRuntimeError, match="공간"):
        _pack(state)
    assert state == before


def test_nine_read_sources_keep_their_slots_with_work_intent():
    pack = _pack(_state(count=9))
    assert len(pack.sources) == 10
    assert [row.canonical_id for row in pack.sources[:9]] == [
        f"DL-{9000 + index}" for index in range(9)
    ]
    assert _intent(pack) == pack.sources[-1]
    assert len(canonical_evidence_pack_json(pack).encode("utf-8")) <= 6144


@pytest.mark.parametrize("role", (
    "request_architect", "query_specialist", "knowledge_curator", "auditor",
    "portfolio_analyst", "result_integrator",
))
def test_other_role_read_provenance_policy_is_unchanged(role):
    assert [row.kind for row in _pack(_state(), role=role).sources] == ["jira"]


def test_dev_uses_only_fixed_synthetic_source():
    state = _state()
    state["runtime_profile"] = "dev"
    pack = _pack(state)
    assert len(pack.sources) == 1
    assert pack.sources[0].canonical_id == "ltm:maxis-dev:synthetic-fixture"
    assert "Chrome" not in canonical_evidence_pack_json(pack)


def test_source_free_work_intent_still_has_one_source():
    pack = _pack(_state(count=0))
    assert len(pack.sources) == 1 and _intent(pack).content == REQUEST


def _expand_intent_refs(value, source):
    if value == {"source_content_index": 1}:
        return source.content
    if isinstance(value, dict):
        return {key: _expand_intent_refs(item, source) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_intent_refs(item, source) for item in value]
    return value


def test_canonical_remote_work_fixture_keeps_full_sources_and_context():
    from tools.maxis_role_remote_cases import build_remote_fixture

    built = build_remote_fixture(
        "role-work", observed_at=datetime.now(timezone.utc), manifest_sha256=MANIFEST,
    )
    pack = EvidencePackV1.model_validate_json(json.dumps(built["payload"]["evidencePack"]))
    source = _intent(pack)
    assert pack.freshness == "fresh" and pack.partial is False
    assert all(not row.truncated and row.content for row in pack.sources)
    assert source.content == built["state"]["request_text"]
    for row in pack.role_context:
        restored = _expand_intent_refs(json.loads(row.content_json), source)
        assert restored == built["state"][row.name]
    assert json.loads(pack.sources[0].content)["issues"][0]["description"] == (
        "리니지 뷰어 2홉·모니터링 작업"
    )
    assert pack.query.startswith("Use the guarded user intent source.")
    assert pack.query.endswith("Policy search terms: Epic Sub-Task 완료 조건")
    assert len(canonical_evidence_pack_json(pack).encode("utf-8")) <= 6144


def test_request_plan_references_only_exact_duplicates_without_mutating_state():
    state = _state()
    state["request_plan"] = {
        "goal": REQUEST, "unknown": {"keep": REQUEST},
        "tasks": [{"id": str(index), "instruction": text, "depends_on": ["keep"]}
                  for index, text in enumerate((REQUEST, REQUEST + " ", REQUEST.lower(),
                                                 "독립 작업", "prefix " + REQUEST))],
    }
    before = deepcopy(state)
    pack = _pack(state)
    source = _intent(pack)
    plan = json.loads(pack.role_context[0].content_json)
    assert plan["goal"] == plan["tasks"][0]["instruction"] == {
        "source_content_index": 1,
    }
    assert _expand_intent_refs(plan, source) == before["request_plan"]
    assert plan["unknown"] == before["request_plan"]["unknown"]
    assert plan["tasks"][1:] == before["request_plan"]["tasks"][1:]
    assert state == before


def test_referenced_request_plan_preserves_carried_observation_time():
    state = _state()
    plan = {"goal": REQUEST, "tasks": [{"instruction": REQUEST}]}
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    state.update(request_plan=plan, role_context_carryover={"request_plan": {
        "sha256": hashlib.sha256(backend._canonical_json(plan).encode("utf-8")).hexdigest(),
        "observed_at": old.isoformat(),
    }})
    pack = _pack(state)
    row = pack.role_context[0]
    assert row.observed_at == old
    assert json.loads(row.content_json)["goal"] == {"source_content_index": 1}
    assert pack.freshness == "mixed" and "stale_role_context" in pack.flags


def test_reference_query_does_not_admit_original_query_overflow():
    state = _state()
    state["request_text"] = "x" * 1100
    with pytest.raises(ContextGuardError, match="query_too_large"):
        _pack(state)


def test_reference_query_keeps_original_pseudonymization_byte_limit():
    state = _state()
    state.update(request_text=" ".join(["abc"] * 45), _maxis_identity_values=["abc"])
    with pytest.raises(ContextGuardError, match="query_too_large"):
        _pack(state)


def test_original_and_reference_queries_use_the_same_normalized_identities(monkeypatch):
    from app.agent.maxis.context_guard import ContextGuard

    guarded = ContextGuard.guard_query
    observed = []
    def record(self, query, identities):
        observed.append(identities)
        return guarded(self, query, identities)
    monkeypatch.setattr(ContextGuard, "guard_query", record)
    _pack({**_state(), "user_id": "1", "_maxis_identity_values": ["1"]})
    assert len(observed) == 2 and observed[0] == observed[1]
    assert len(observed[0]) == len(set(observed[0]))


@pytest.mark.parametrize("identity", ("1", "content"))
def test_system_index_survives_short_identity_without_exempting_user_text(identity):
    state = _state()
    state.update(user_id=identity, request_plan={
        "goal": REQUEST, "tasks": [{"instruction": REQUEST}],
        "unknown": {"source_ref": "/sources/1/content"},
    })
    pack = _pack(state)
    plan = json.loads(pack.role_context[0].content_json)
    index = plan["goal"]["source_content_index"]
    assert type(index) is int and index == 1
    assert pack.sources[index] == _intent(pack)
    assert plan["tasks"][0]["instruction"] == {"source_content_index": index}
    assert "[[person:" in plan["unknown"]["source_ref"]
    assert not pack.partial


@pytest.mark.parametrize("condition", ("fresh", "stale-context", "truncated-intent"))
def test_work_reference_freshness_is_enforced_before_credential_lookup(monkeypatch, condition):
    from app.agent.maxis import credentials
    from tools.maxis_role_remote_cases import build_remote_fixture

    if condition == "fresh":
        payload = build_remote_fixture(
            "role-work", observed_at=datetime.now(timezone.utc), manifest_sha256=MANIFEST,
        )["payload"]
    else:
        state = _state(count=2 if condition == "truncated-intent" else 1)
        plan = {"goal": REQUEST, "tasks": [{"instruction": REQUEST}]}
        state["request_plan"] = plan
        if condition == "stale-context":
            state["role_context_carryover"] = {"request_plan": {
                "sha256": hashlib.sha256(backend._canonical_json(plan).encode()).hexdigest(),
                "observed_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            }}
        else:
            for row in state["query_results"]:
                row["result"]["issues"][0]["description"] = "자료" * 220
                row["_remoteEvidence"] = backend.attest_query_result(
                    row["id"], "jira", row["result"], scopes=("DL",),
                )
        payload = backend.role_payload("work_architect", state, [], CREATE_SCHEMA,
                                       output_kind="structured")
        if condition == "truncated-intent":
            assert payload["evidencePack"]["partial"]
            assert payload["evidencePack"]["sources"][-1]["truncated"]
    payload["runtime"]["knowledgeDatasetId"] = "fixture-dataset"
    metadata = {"workflows": {"agent_core": {"version": "wf-1"}},
                "knowledge_version": "kb-1", "knowledge_dataset_id": "fixture-dataset",
                "knowledge_manifest_sha256": MANIFEST, "knowledge_sentinel_sha256": "b" * 64}
    monkeypatch.setattr(backend, "resolve_role", lambda *_a: backend.RoleResolution(
        route="maxis", binding=SimpleNamespace(), profile="prod"))
    monkeypatch.setattr(backend, "_profile_snapshot", lambda *_a, **_k: metadata)
    monkeypatch.setattr(backend, "_record_remote_audit", lambda **_kwargs: None)
    lookups = []
    monkeypatch.setattr(credentials, "get_for", lambda *_args: lookups.append(True) or "")
    expected_error = "API key" if condition == "fresh" else "freshness"
    with pytest.raises(backend.MaxisRuntimeError, match=expected_error):
        backend.invoke("work_architect", payload, "fixture-thread", "fixture-correlation")
    assert len(lookups) == int(condition == "fresh")
