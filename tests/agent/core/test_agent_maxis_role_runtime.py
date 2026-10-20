from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.maxis import backend
from app.agent.maxis.contracts import EvidencePackV1
from app.agent.workflow.agents.base import StructuredAgent
from app.agent.workflow.role_manifest import ROLE_SPECS, remote_execution_allowed


DATASET_A = "11111111-1111-4111-8111-111111111111"
AGENT_WORKFLOW_ID = "22222222-2222-4222-8222-222222222222"
PMO_WORKFLOW_ID = "33333333-3333-4333-8333-333333333333"
EDITOR_WORKFLOW_ID = "44444444-4444-4444-8444-444444444444"
MANIFEST_SHA = "a" * 64
SENTINEL_VALUE = "LTM_RELEASE_SENTINEL_V1=runtime-test-release"
SENTINEL_SHA = hashlib.sha256(
    SENTINEL_VALUE.encode("utf-8")
).hexdigest()


@pytest.fixture(autouse=True)
def audit_events(monkeypatch):
    """Keep runtime tests metadata-only and inside their in-memory boundary."""
    from app.agent.maxis import audit

    events = []
    monkeypatch.setenv("LAKE_MAXIS_PROD_ALLOWED_HOSTS", "maxis.example")
    monkeypatch.setattr(audit, "record_audit_event", events.append)
    return events


def _profile(name="dev", fingerprint="profile-a"):
    return {
        "name": name,
        "stage": name,
        "fingerprint": fingerprint,
        "base_url": "http://127.0.0.1:9001/v1" if name == "dev"
        else "https://maxis.example/v1",
        "data_policy": "synthetic_only" if name == "dev" else "internal_read_only",
        "workflow_bundle_version": "bundle-1",
        "knowledge_version": "knowledge-1",
        "knowledge_dataset_id": DATASET_A,
        "knowledge_manifest_sha256": MANIFEST_SHA,
        "knowledge_sentinel_sha256": SENTINEL_SHA,
        "knowledge_document_count": 3,
        "workflows": {
            "agent_core": {
                "binding": "agent_core", "version": AGENT_WORKFLOW_ID,
            },
            "pmo_brief": {"binding": "pmo_brief", "version": PMO_WORKFLOW_ID},
            "editor_author": {
                "binding": "editor_author", "version": EDITOR_WORKFLOW_ID,
            },
        },
        "enabled": True,
    }


def _routes(value="local"):
    return {
        role_id: (value if remote_execution_allowed(role_id) else "local")
        for role_id in ROLE_SPECS
    }


def _runtime_state(role_id="request_architect"):
    routes = _routes("local")
    routes[role_id] = "maxis"
    return {
        "thread_id": "thread-1",
        "user_id": "skcc.x1042",
        "messages": [HumanMessage(content="DL 업무 초안을 작성해 줘")],
        "request_text": "DL 업무 초안을 작성해 줘",
        "correlation_id": "corr-1",
        "correlation_started_at": datetime.now(timezone.utc).isoformat(),
        "runtime_engine": "maxis",
        "runtime_profile": "prod",
        "runtime_profile_fingerprint": "runtime-fingerprint",
        "runtime_role_routes": routes,
        "runtime_engine_source": "explicit",
        "workflow_bundle_version": "bundle-1",
        "knowledge_version": "knowledge-1",
        "knowledge_dataset_id": DATASET_A,
        "knowledge_manifest_sha256": MANIFEST_SHA,
    }


def test_only_semantic_non_identity_roles_are_remote_capable():
    assert remote_execution_allowed("request_architect") is True
    assert remote_execution_allowed("editor_author") is True
    assert remote_execution_allowed("research_analyst") is False
    assert remote_execution_allowed("query_runner") is False
    assert remote_execution_allowed("action_executor") is False
    assert remote_execution_allowed("people_advisor") is False


def test_thread_pin_rejects_profile_or_route_change(monkeypatch):
    current = {"routes": _routes("maxis"), "profile": _profile()}
    monkeypatch.setattr(
        backend, "_canonical_role_routes",
        lambda *, explicit_engine="": dict(current["routes"]),
    )
    monkeypatch.setattr(
        backend, "_profile_snapshot",
        lambda profile_name="", required=False: dict(current["profile"]),
    )
    # This unit isolates immutable pin drift. Authority is covered against the real
    # profile gate in ``test_shadow_profile_gate``.
    monkeypatch.setattr(backend, "_require_authoritative_routes", lambda *_a: None)
    pin = backend.thread_runtime_pin()
    backend.verify_thread_runtime(pin)
    assert pin["knowledge_dataset_id"] == DATASET_A
    assert pin["knowledge_manifest_sha256"] == MANIFEST_SHA

    tampered = {**pin, "knowledge_manifest_sha256": "b" * 64}
    with pytest.raises(backend.RuntimeProfileChanged, match="dataset/manifest"):
        backend.verify_thread_runtime(tampered)

    current["profile"] = _profile(fingerprint="profile-b")
    with pytest.raises(backend.RuntimeProfileChanged, match="프로필이 변경"):
        backend.verify_thread_runtime(pin)

    current["profile"] = _profile()
    current["routes"] = _routes("local")
    with pytest.raises(backend.RuntimeProfileChanged, match="route가 변경"):
        backend.verify_thread_runtime(pin)


def test_thread_pin_rejects_hot_reloaded_registry_contract(monkeypatch):
    current = {"digest": "a" * 64}
    monkeypatch.setattr(
        backend, "_canonical_role_routes",
        lambda *, explicit_engine="": _routes("local"),
    )
    monkeypatch.setattr(
        backend, "_profile_snapshot", lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        backend, "_registry_digest", lambda *, required: current["digest"],
    )
    pin = backend.thread_runtime_pin(engine="local")
    backend.verify_thread_runtime(pin)
    current["digest"] = "b" * 64
    with pytest.raises(backend.RuntimeProfileChanged, match="registry|프로필"):
        backend.verify_thread_runtime(pin)


def test_old_checkpoint_can_only_migrate_to_explicit_local():
    migrated = backend.prepare_thread_runtime({"messages": [HumanMessage(content="old")]})
    assert migrated["runtime_engine"] == "local"
    assert set(migrated["runtime_role_routes"].values()) == {"local"}
    assert migrated["knowledge_dataset_id"] == ""
    assert migrated["knowledge_manifest_sha256"] == ""
    with pytest.raises(backend.RuntimeProfileChanged):
        backend.prepare_thread_runtime({"messages": []}, engine="maxis")


def test_old_checkpoint_with_empty_langgraph_reducers_migrates_to_explicit_local():
    migrated = backend.prepare_thread_runtime({
        "messages": [HumanMessage(content="old")],
        "runtime_role_engines": {},
        "runtime_role_telemetry": {},
        "runtime_role_sidecars": {},
        "runtime_role_questions": {},
        "runtime_last_role": "",
        "runtime_last_role_engine": "",
    })

    assert migrated["runtime_engine"] == "local"
    assert migrated["runtime_profile"] == ""
    assert set(migrated["runtime_role_routes"].values()) == {"local"}


@pytest.mark.parametrize(
    "remote_marker",
    [
        {"runtime_engine": "maxis"},
        {"runtime_profile": "prod"},
        {"runtime_engine_source": "profile"},
        {"runtime_role_routes": {}},
        {"runtime_role_routes": {"request_architect": "shadow"}},
        {"runtime_role_engines": {"request_architect": "maxis"}},
        {"runtime_role_engines": []},
        {"knowledge_dataset_id": DATASET_A},
        {"runtime_role_telemetry": {"request_architect": {"status": "ok"}}},
    ],
)
def test_unpinned_checkpoint_with_remote_or_ambiguous_marker_never_migrates(
    remote_marker,
):
    with pytest.raises(backend.RuntimeProfileChanged, match="기존 대화"):
        backend.prepare_thread_runtime({
            "messages": [HumanMessage(content="old")],
            **remote_marker,
        })


def test_new_all_local_pin_does_not_read_or_bind_active_maxis_profile(monkeypatch):
    monkeypatch.setattr(
        backend, "_canonical_role_routes",
        lambda *, explicit_engine="": _routes("local"),
    )
    monkeypatch.setattr(
        backend, "_profile_snapshot",
        lambda *_a, **_k: pytest.fail("pure local pin must not read an active profile"),
    )

    pin = backend.thread_runtime_pin()

    assert pin["runtime_profile"] == ""
    assert pin["workflow_bundle_version"] == ""
    assert pin["knowledge_dataset_id"] == ""


def test_explicit_profile_still_binds_an_all_local_pin(monkeypatch):
    reads = []
    monkeypatch.setattr(
        backend, "_canonical_role_routes",
        lambda *, explicit_engine="": _routes("local"),
    )

    def profile_snapshot(profile_name="", *, required=False):
        reads.append((profile_name, required))
        return _profile("dev")

    monkeypatch.setattr(backend, "_profile_snapshot", profile_snapshot)

    pin = backend.thread_runtime_pin(profile="dev")

    assert reads == [("dev", False)]
    assert pin["runtime_profile"] == "dev"


def test_approval_resume_persists_legacy_local_pin_before_verification(monkeypatch):
    from app.agent.workflow import session

    state = {"messages": [HumanMessage(content="legacy paused conversation")]}
    updates = []

    class FakeGraph:
        def get_state(self, _config):
            return SimpleNamespace(values=dict(state))

        def update_state(self, _config, patch):
            updates.append(dict(patch))
            state.update(patch)

    monkeypatch.setattr(
        backend, "_canonical_role_routes",
        lambda *, explicit_engine="": _routes("local"),
    )
    monkeypatch.setattr(
        backend, "_profile_snapshot",
        lambda *_a, **_k: pytest.fail("legacy local resume must not read a profile"),
    )

    pinned = session._prepare_resume_runtime(FakeGraph(), "legacy-thread")

    assert len(updates) == 1
    assert pinned["runtime_profile_fingerprint"]
    assert pinned["runtime_profile"] == ""
    assert set(pinned["runtime_role_routes"].values()) == {"local"}


def test_approval_resume_never_repairs_an_unpinned_remote_checkpoint():
    from app.agent.workflow import session

    state = {
        "messages": [HumanMessage(content="ambiguous paused conversation")],
        "runtime_role_routes": {"request_architect": "maxis"},
    }
    updates = []

    class FakeGraph:
        def get_state(self, _config):
            return SimpleNamespace(values=dict(state))

        def update_state(self, _config, patch):
            updates.append(dict(patch))

    with pytest.raises(backend.RuntimeProfileChanged, match="기존 대화"):
        session._prepare_resume_runtime(FakeGraph(), "remote-thread")

    assert updates == []


def test_migrated_local_checkpoint_ignores_a_later_active_profile(monkeypatch):
    monkeypatch.setattr(
        backend, "_canonical_role_routes",
        lambda *, explicit_engine="": _routes("local"),
    )
    profile_reads = []

    def active_profile_snapshot(*_args, **_kwargs):
        profile_reads.append(True)
        return _profile(fingerprint="later-active-profile")

    monkeypatch.setattr(backend, "_profile_snapshot", active_profile_snapshot)
    migrated = backend.prepare_thread_runtime({
        "messages": [HumanMessage(content="old")],
    })

    backend.verify_thread_runtime(migrated)

    assert migrated["runtime_profile"] == ""
    assert profile_reads == []


def test_pinned_checkpoint_cannot_fallback_when_role_route_is_missing(monkeypatch):
    monkeypatch.setattr(
        backend, "_canonical_role_routes",
        lambda *, explicit_engine="": _routes("local"),
    )
    state = {
        "runtime_profile_fingerprint": "pinned-runtime",
        "runtime_role_routes": {"editor_author": "maxis"},
    }

    with pytest.raises(backend.RuntimeProfileChanged, match="route pin"):
        backend.resolve_role("request_architect", state)


def test_pinned_local_route_is_verified_before_local_return(monkeypatch):
    state = {
        "runtime_profile_fingerprint": "pinned-runtime",
        "runtime_role_routes": {"request_architect": "local"},
    }

    def reject_damaged_pin(_state, **_kwargs):
        raise backend.RuntimeProfileChanged("damaged pinned runtime")

    monkeypatch.setattr(backend, "verify_thread_runtime", reject_damaged_pin)
    with pytest.raises(backend.RuntimeProfileChanged, match="damaged pinned runtime"):
        backend.resolve_role("request_architect", state)


def test_unpinned_direct_partial_route_map_remains_compatible(monkeypatch):
    monkeypatch.setattr(
        backend, "verify_thread_runtime",
        lambda *_a, **_k: pytest.fail("an unpinned direct call must not be verified"),
    )

    resolved = backend.resolve_role(
        "request_architect",
        {"runtime_role_routes": {"request_architect": "local"}},
    )

    assert resolved.route == "local"


def test_change_audit_sequence_reuses_correlation_without_content(monkeypatch, audit_events):
    from app.agent import approval
    from app.agent.workflow import graph as workflow_graph

    state = {
        "thread_id": "audit-thread",
        "correlation_id": "corr-change-1",
        "runtime_profile": "dev",
        "runtime_role_routes": _routes("local"),
        "runtime_role_engines": {"work_architect": "maxis"},
        "workflow_bundle_version": "bundle-1",
        "knowledge_version": "knowledge-1",
        "review": {"ok": True},
        "draft": {"mode": "task", "items": [{
            "summary": "감사 연속성 검증", "type": "Task",
        }]},
        # These values must not be accepted by the audit API or ledger schema.
        "approval_token": "RAW_TOKEN_MUST_NOT_CROSS",
        "request_text": "RAW_REQUEST_MUST_NOT_CROSS",
    }
    monkeypatch.setattr(backend, "_profile_snapshot", lambda *_a, **_k: _profile())
    staged = workflow_graph._propose(state)
    token = staged["approval_token"]
    binding = staged["change_audit_binding"]
    try:
        backend.record_change_audit(
            state, event="change.approved", status="succeeded",
            change_binding=binding,
        )
        execution_binding = approval.finalize_change_audit_binding(
            binding, token=token, thread_id=state["thread_id"],
        )
        backend.record_change_audit(
            state, event="change.execution", status="succeeded",
            change_binding=execution_binding,
        )
    finally:
        approval.reject(token)
    assert [row.event for row in audit_events] == [
        "change.proposal", "change.approved", "change.execution",
    ]
    assert {row.correlation_id for row in audit_events} == {"corr-change-1"}
    encoded = json.dumps(
        [row.model_dump(mode="json") for row in audit_events], ensure_ascii=False,
    )
    assert "RAW_TOKEN_MUST_NOT_CROSS" not in encoded
    assert "RAW_REQUEST_MUST_NOT_CROSS" not in encoded
    assert audit_events[0].change_binding.actor_sha256
    assert audit_events[-1].change_binding.receipt_status == "not_issued"


def test_guarded_payload_ignores_role_task_transcript_and_pseudonymizes_people():
    state = _runtime_state("research_analyst")
    result = {
        "scopeProjects": ["DL"],
        "tickets": [{
            "key": "DL-1", "summary": "검증된 업무",
            "assignee": "홍길동", "author": "skcc.x1103",
        }],
    }
    state["query_results"] = [{
        "id": "jira-1", "source": "jira", "result": result,
        "_remoteEvidence": backend.attest_query_result("jira-1", "jira", result),
    }]
    state["query_plan"] = {"queries": [{"id": "jira-1", "source": "jira"}]}
    state["approval_token"] = "approval-must-not-cross"
    payload = backend.role_payload(
        "research_analyst",
        state,
        [
            SystemMessage(content="RAW_SYSTEM_MUST_NOT_CROSS"),
            HumanMessage(content="RAW_ROLE_TASK_MUST_NOT_CROSS"),
        ],
        {"type": "object"},
        output_kind="structured",
    )
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    pack = EvidencePackV1.model_validate_json(
        json.dumps(payload["evidencePack"], ensure_ascii=False), strict=True)

    assert "RAW_SYSTEM_MUST_NOT_CROSS" not in encoded
    assert "RAW_ROLE_TASK_MUST_NOT_CROSS" not in encoded
    assert "approval-must-not-cross" not in encoded
    assert "messages" not in payload and "input" not in payload
    assert "skcc.x1103" not in pack.sources[0].content
    assert "홍길동" not in pack.sources[0].content
    assert "[[person:" in pack.sources[0].content
    assert pack.query.endswith("DL 업무 초안을 작성해 줘")
    # Research stays local until public web/GitHub evidence has a tenant scope and
    # host contract.  Its raw query plan therefore must not acquire an accidental
    # outbound path merely because a guarded pack is constructed in isolation.
    assert remote_execution_allowed("research_analyst") is False
    assert pack.role_context == ()
    assert pack.knowledge_manifest_sha256 == MANIFEST_SHA
    assert payload["runtime"]["knowledgeDatasetId"] == DATASET_A
    assert payload["runtime"]["knowledgeManifestSha256"] == MANIFEST_SHA
    assert pack.sources[0].canonical_id == "DL-1"
    assert pack.sources[0].url == ""  # no navigable URL crosses the MAXIS boundary

    projected = backend._remote_state_patch(
        SimpleNamespace(
            citations=(), claims=(), missing_inputs=(), warnings=(), status="ok",
        ),
        pack,
    )
    assert projected["evidence_workspace"]["sources"][0]["url"] == "/browse/DL-1"


def test_editor_person_ref_is_opaque_outbound_and_rehydrates_only_verified_id():
    state = _runtime_state("editor_author")
    state["_maxis_identity_values"] = ["alice.smith"]
    result = {
        "scopeProjects": ["DL"],
        "ticketContext": '[DL-1] "검토" — Open · 담당 [~alice.smith]',
    }
    state["_maxis_evidence_rows"] = [{
        "id": "editor-ticket", "source": "jira", "result": result,
        "_remoteEvidence": backend.attest_query_result(
            "editor-ticket", "jira", result, scopes=("DL",),
        ),
    }]
    payload = backend.role_payload(
        "editor_author", state, [], {"type": "object"},
        output_kind="structured",
    )
    wire = json.dumps(payload["evidencePack"], ensure_ascii=False)
    mapping = backend.editor_person_reference_map(["alice.smith"])
    marker = next(iter(mapping))
    assert "alice.smith" not in wire
    assert marker in wire
    assert backend._rehydrate_editor_person_references(
        {"content_html": f"<p>{marker} 검토 부탁드립니다.</p>"}, mapping,
    ) == {"content_html": "<p>[~alice.smith] 검토 부탁드립니다.</p>"}
    with pytest.raises(backend.MaxisRuntimeError, match="검증되지 않은"):
        backend._rehydrate_editor_person_references(
            {"content_html": "<p>[[person:000000000000]]</p>"}, mapping,
        )


@pytest.mark.parametrize("profile", ("dev", "prod"))
def test_editor_policy_search_terms_keep_dev_fixture_boundary(profile):
    from app.agent.maxis.contracts import EditorDraftV1
    state = _runtime_state("editor_author")
    state.update(runtime_profile=profile, request_text="본문 써줘")
    payload = backend.role_payload(
        "editor_author", state, [], EditorDraftV1.model_json_schema(),
        output_kind="structured",
    )
    pack = payload["evidencePack"]
    if profile == "dev":
        assert pack["query"] == (
            "Canonical LTM role editor_author. Process this guarded user intent:\n"
            "Synthetic MAXIS integration exercise for canonical role editor_author."
        )
        assert "본문 써줘" not in json.dumps(pack, ensure_ascii=False)
        assert pack["flags"] == ["synthetic_only"]
    else:
        assert pack["query"].startswith(
            "Canonical LTM role editor_author. Process this guarded user intent:\n본문 써줘"
        )
        assert pack["query"].endswith("Policy search terms: HTML TipTap 본문")
        assert json.loads(pack["sources"][0]["content"])["prompt"] == "본문 써줘"


def test_editor_preserves_citeable_intent_without_replacing_verified_evidence():
    from app.agent.maxis.contracts import EditorDraftV1
    state = _runtime_state("editor_author")
    state.update(ticket_key="DL-1", kind="comment", seed_html="<p>미완성 측정 결과</p>")
    result = {"scopeProjects": ["DL"], "tickets": [{"key": "DL-1", "summary": "현재 업무"}]}
    state["_maxis_evidence_rows"] = [{
        "id": "editor-ticket", "source": "jira", "result": result,
        "_remoteEvidence": backend.attest_query_result("editor-ticket", "jira", result, scopes=("DL",)),
    }]
    pack = backend.role_payload("editor_author", state, [], EditorDraftV1.model_json_schema(),
                                output_kind="structured")["evidencePack"]
    assert len(pack["sources"]) == 2
    assert pack["sources"][0]["kind"] == "jira"
    intent = pack["sources"][1]
    assert intent["kind"] == "ltm" and intent["canonical_id"].startswith("ltm:user-intent:")
    assert json.loads(intent["content"]) == {
        "ticket_key": "DL-1", "kind": "comment", "prompt": state["request_text"],
        "seed_html": "<p>미완성 측정 결과</p>",
    }


@pytest.mark.parametrize("role_id", (
    "query_specialist", "work_architect", "auditor", "portfolio_analyst",
))
@pytest.mark.parametrize("profile", ("dev", "prod"))
def test_role_policy_search_preserves_intent_sources_and_dev_isolation(role_id, profile):
    from copy import deepcopy
    from app.agent.workflow.contracts import role_output_schema
    from app.agent.workflow.work_architect.contracts import CREATE_SCHEMA
    from app.agent.workflow.agents.auditor import SCHEMA as audit
    from app.agent.workflow.agents.portfolio_analyst import SCHEMA as portfolio
    schema = {
        "query_specialist": role_output_schema("query_specialist"),
        "work_architect": CREATE_SCHEMA, "auditor": audit, "portfolio_analyst": portfolio,
    }[role_id]
    state = _runtime_state(role_id)
    state.update(runtime_profile=profile, request_text="원문 요청 보존")
    before = deepcopy(state)
    pack = backend.role_payload(role_id, state, [], schema, output_kind="structured")["evidencePack"]
    assert state == before
    if profile == "dev":
        assert pack["query"] == (
            f"Canonical LTM role {role_id}. Process this guarded user intent:\n"
            f"Synthetic MAXIS integration exercise for canonical role {role_id}."
        )
        assert pack["flags"] == ["synthetic_only"]
        assert "원문 요청 보존" not in json.dumps(pack, ensure_ascii=False)
    else:
        assert pack["query"].startswith(
            f"Canonical LTM role {role_id}. Process this guarded user intent:\n원문 요청 보존\n\n"
        )
        assert "Policy search terms:" in pack["query"]
        assert pack["sources"][0]["content"] == "원문 요청 보존"
        assert "Policy search terms:" not in json.dumps(pack["role_context"], ensure_ascii=False)


def test_editor_intent_does_not_displace_ten_verified_sources(monkeypatch):
    monkeypatch.setattr(backend, "_verified_evidence_sources", lambda _state: [{}] * 10)
    with pytest.raises(backend.MaxisRuntimeError, match="공간"):
        backend._context_pack("editor_author", _runtime_state("editor_author"), [], {"type": "object"})


def test_editor_intent_never_silently_truncates_seed():
    state = _runtime_state("editor_author")
    state.update(kind="comment", seed_html="가" * 1501)
    with pytest.raises(backend.MaxisRuntimeError, match="한도"):
        backend._context_pack("editor_author", state, [], {"type": "object"})


def test_editor_intent_removes_nested_seed_urls():
    state = _runtime_state("editor_author")
    state.update(kind="comment", seed_html="<p>자료 https://untrusted.example/private</p>")
    pack = backend._context_pack("editor_author", state, [], {"type": "object"})
    wire = json.dumps(pack.model_dump(mode="json"))
    assert "untrusted.example" not in wire
    assert "[url omitted]" in wire


def test_missing_request_text_never_falls_back_to_messages_or_prompt():
    state = _runtime_state("request_architect")
    state["request_text"] = ""
    state["prompt"] = "RAW_PROMPT_STATE_MUST_NOT_CROSS"
    state["messages"] = [HumanMessage(content="RAW_TRANSCRIPT_MUST_NOT_CROSS")]
    payload = backend.role_payload(
        "request_architect", state,
        [HumanMessage(content="RAW_ROLE_TASK_MUST_NOT_CROSS")],
        {"type": "object"}, output_kind="structured",
    )
    encoded = json.dumps(payload["evidencePack"], ensure_ascii=False)
    assert "RAW_PROMPT_STATE_MUST_NOT_CROSS" not in encoded
    assert "RAW_TRANSCRIPT_MUST_NOT_CROSS" not in encoded
    assert "RAW_ROLE_TASK_MUST_NOT_CROSS" not in encoded
    assert payload["evidencePack"]["sources"] == []
    assert payload["evidencePack"]["freshness"] == "empty"


def test_source_free_prod_role_uses_only_guarded_intent_as_provenance():
    state = _runtime_state("request_architect")
    state["messages"] = [HumanMessage(content="RAW_TRANSCRIPT_MUST_NOT_CROSS")]

    payload = backend.role_payload(
        "request_architect", state,
        [HumanMessage(content="RAW_ROLE_TASK_MUST_NOT_CROSS")],
        {"type": "object"}, output_kind="structured",
    )
    pack = EvidencePackV1.model_validate_json(
        json.dumps(payload["evidencePack"], ensure_ascii=False), strict=True,
    )

    assert len(pack.sources) == 1
    source = pack.sources[0]
    assert source.kind == "ltm"
    assert source.source_id.startswith("src_intent_")
    assert source.canonical_id.startswith("ltm:user-intent:")
    assert source.title == "LTM guarded user intent"
    assert source.content == "DL 업무 초안을 작성해 줘"
    assert source.url == "" and source.scope == ""
    assert source.retrieved_at == pack.requested_at
    assert source.updated_at is None
    assert source.partial is False and source.truncated is False
    assert pack.freshness == "fresh"
    encoded = json.dumps(pack.model_dump(mode="json"), ensure_ascii=False)
    assert "RAW_TRANSCRIPT_MUST_NOT_CROSS" not in encoded
    assert "RAW_ROLE_TASK_MUST_NOT_CROSS" not in encoded


def test_guarded_intent_source_reuses_query_dlp_projection():
    state = _runtime_state("request_architect")
    state["request_text"] = (
        "담당 skcc.x1103에게 https://internal.example/private 검토를 요청해 줘"
    )

    payload = backend.role_payload(
        "request_architect", state, [], {"type": "object"},
        output_kind="structured",
    )
    pack = EvidencePackV1.model_validate_json(
        json.dumps(payload["evidencePack"], ensure_ascii=False), strict=True,
    )

    content = pack.sources[0].content
    assert "skcc.x1103" not in content and "skcc.x1103" not in pack.query
    assert "https://internal.example" not in content
    assert "[[person:" in content
    assert "[url omitted]" in content
    assert content in pack.query


def test_verified_query_source_is_not_diluted_by_user_intent_source():
    state = _runtime_state("request_architect")
    result = {"scopeProjects": ["DL"], "tickets": [{"key": "DL-1"}]}
    state["query_results"] = [{
        "id": "jira-1", "source": "jira", "result": result,
        "_remoteEvidence": backend.attest_query_result(
            "jira-1", "jira", result, scopes=("DL",),
        ),
    }]

    payload = backend.role_payload(
        "request_architect", state, [], {"type": "object"},
        output_kind="structured",
    )
    pack = EvidencePackV1.model_validate_json(
        json.dumps(payload["evidencePack"], ensure_ascii=False), strict=True,
    )

    assert [source.kind for source in pack.sources] == ["jira"]
    assert all(not source.source_id.startswith("src_intent_") for source in pack.sources)


def test_request_architect_receives_only_bounded_explicit_continuation_answer():
    state = _runtime_state("request_architect")
    state.update(
        turn_continuation=True,
        continuation_answer="마감은 2026-09-30으로 해줘",
        messages=[HumanMessage(content="RAW_TRANSCRIPT_MUST_NOT_CROSS")],
    )

    payload = backend.role_payload(
        "request_architect", state,
        [HumanMessage(content="RAW_ROLE_TASK_MUST_NOT_CROSS")],
        {"type": "object"}, output_kind="structured",
    )
    query = payload["evidencePack"]["query"]

    assert "Original request:" in query
    assert "DL 업무 초안을 작성해 줘" in query
    assert "Current continuation answer:" in query
    assert "마감은 2026-09-30으로 해줘" in query
    assert "RAW_TRANSCRIPT_MUST_NOT_CROSS" not in query
    assert "RAW_ROLE_TASK_MUST_NOT_CROSS" not in query


def test_non_request_role_never_receives_continuation_answer():
    state = _runtime_state("work_architect")
    state.update(
        turn_continuation=True,
        continuation_answer="CURRENT_ANSWER_MUST_NOT_CROSS_TO_OTHER_ROLE",
    )
    payload = backend.role_payload(
        "work_architect", state, [], {"type": "object"},
        output_kind="structured",
    )
    assert "CURRENT_ANSWER_MUST_NOT_CROSS_TO_OTHER_ROLE" not in json.dumps(
        payload["evidencePack"], ensure_ascii=False,
    )


def test_dev_profile_replaces_all_content_with_a_fixed_synthetic_fixture():
    state = _runtime_state("request_architect")
    state.update(
        runtime_profile="dev",
        request_text="REAL_INTERNAL_PROMPT_MUST_NOT_CROSS",
        user_id="real.user@example.test",
        query_plan={"private": "REAL_ROLE_CONTEXT_MUST_NOT_CROSS"},
    )
    result = {
        "scopeProjects": ["DL"],
        "tickets": [{"key": "DL-1", "summary": "REAL_TICKET_MUST_NOT_CROSS"}],
    }
    state["query_results"] = [{
        "id": "jira-private", "source": "jira", "result": result,
        "_remoteEvidence": backend.attest_query_result(
            "jira-private", "jira", result,
        ),
    }]

    payload = backend.role_payload(
        "request_architect", state, [], {"type": "object"},
        output_kind="structured",
    )
    pack = EvidencePackV1.model_validate_json(
        json.dumps(payload["evidencePack"], ensure_ascii=False), strict=True,
    )
    encoded = json.dumps(payload["evidencePack"], ensure_ascii=False)

    assert "synthetic_only" in pack.flags
    assert pack.query.endswith("canonical role request_architect.")
    assert len(pack.sources) == 1
    assert pack.sources[0].canonical_id == "ltm:maxis-dev:synthetic-fixture"
    assert pack.scope.model_dump(mode="json") == {
        "projects": [], "spaces": [], "repositories": [],
    }
    assert pack.role_context == ()
    for forbidden in (
        "REAL_INTERNAL_PROMPT_MUST_NOT_CROSS", "REAL_ROLE_CONTEXT_MUST_NOT_CROSS",
        "REAL_TICKET_MUST_NOT_CROSS", "real.user@example.test",
    ):
        assert forbidden not in encoded


@pytest.mark.parametrize("role_id,canonical", [
    ("request_architect", False), ("query_specialist", False), ("query_specialist", True),
])
def test_dev_profile_can_run_safe_synthetic_probe_from_real_jira_process(
    monkeypatch, role_id, canonical,
):
    from app.agent.maxis import profiles
    from app.agent.maxis import credentials
    from app.agent.maxis import client as client_module
    from app.agent.tools import _ctx

    profile = _profile("dev")
    monkeypatch.setattr(profiles, "resolve", lambda *_args, **_kwargs: dict(profile))
    monkeypatch.setattr(profiles, "kill_switch_enabled", lambda: False)
    _ctx.bind(SimpleNamespace(), SimpleNamespace(
        search_jira_projects=["REAL_JIRA_PROJECT_MUST_NOT_CROSS"],
        search_confluence_spaces=["REAL_CONFLUENCE_SPACE_MUST_NOT_CROSS"],
        search_bitbucket_projects=["REAL_BITBUCKET_REPO_MUST_NOT_CROSS"],
        jira_env="prod", jira_user="real-user", managers=[],
        jira_base="https://real-jira.internal.example",
        confluence_base="https://real-confluence.internal.example",
        bitbucket_base="https://real-bitbucket.internal.example",
    ))
    try:
        metadata = backend._profile_snapshot("dev", required=True)
        state = _runtime_state(role_id)
        state.update(runtime_profile="dev", request_text="REAL_MUST_NOT_CROSS")
        from app.agent.workflow.contracts import role_output_schema
        schema = role_output_schema(role_id) if canonical else {"type": "object"}
        payload = backend.role_payload(
            role_id, state, [], schema,
            output_kind="structured",
        )
    finally:
        _ctx.bind()

    observed = {"calls": 0}

    class CapturingClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def invoke(self, _binding, **kwargs):
            observed["calls"] += 1
            observed["dify_request"] = json.dumps(
                kwargs["inputs"]["context_pack"].model_dump(mode="json"),
                ensure_ascii=False,
            )
            raise RuntimeError("stop after capturing the Dify request")

    monkeypatch.setattr(
        backend, "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(
            route="maxis", binding=SimpleNamespace(), profile="dev",
        ),
    )
    monkeypatch.setattr(backend, "_profile_snapshot", lambda *_a, **_k: dict(profile))
    monkeypatch.setattr(credentials, "get_for", lambda *_a, **_k: "dev-app-key")
    monkeypatch.setattr(client_module, "DifyClient", CapturingClient)
    with pytest.raises(backend.MaxisRuntimeError, match="Role 호출"):
        backend.invoke(role_id, payload, "thread-1", "corr-1")

    assert observed["calls"] == 1
    contexts = payload["evidencePack"]["role_context"]
    assert [row["name"] for row in contexts] == (["query_business_input"] if canonical else [])
    assert metadata["name"] == "dev"
    encoded = json.dumps(payload["evidencePack"], ensure_ascii=False)
    assert "REAL_MUST_NOT_CROSS" not in encoded
    assert payload["evidencePack"]["flags"] == ["synthetic_only"]
    assert payload["evidencePack"]["scope"] == {
        "projects": [], "spaces": [], "repositories": [],
    }
    assert payload["evidencePack"]["sources"][0]["canonical_id"] == (
        "ltm:maxis-dev:synthetic-fixture"
    )
    for forbidden in (
        "REAL_JIRA_PROJECT_MUST_NOT_CROSS",
        "REAL_CONFLUENCE_SPACE_MUST_NOT_CROSS",
        "REAL_BITBUCKET_REPO_MUST_NOT_CROSS",
        "real-jira.internal.example",
        "real-confluence.internal.example",
        "real-bitbucket.internal.example",
    ):
        assert forbidden not in encoded
        assert forbidden not in observed["dify_request"]


def test_synthetic_fixture_scope_is_rechecked_before_dify_request(monkeypatch):
    state = _runtime_state("request_architect")
    state["runtime_profile"] = "dev"
    payload = backend.role_payload(
        "request_architect", state, [], {"type": "object"},
        output_kind="structured",
    )
    payload["evidencePack"]["scope"]["projects"] = ["REAL_PROJECT_TAMPER"]
    monkeypatch.setattr(
        backend, "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(
            route="maxis", binding=SimpleNamespace(), profile="dev",
        ),
    )
    monkeypatch.setattr(backend, "_profile_snapshot", lambda *_a, **_k: _profile("dev"))
    from app.agent.maxis import client as client_module
    monkeypatch.setattr(
        client_module, "DifyClient",
        lambda *_a, **_k: pytest.fail("tampered synthetic scope reached Dify client"),
    )

    with pytest.raises(backend.MaxisRuntimeError, match="합성 fixture"):
        backend.invoke("request_architect", payload, "thread-1", "corr-1")


def test_nested_jira_cloud_person_fields_are_pseudonymized():
    state = _runtime_state("research_analyst")
    result = {
        "scopeProjects": ["DL"],
        "tickets": [{
            "key": "DL-1",
            "assignee": {
                "accountId": "557058:opaque-account-id",
                "account_id": "alice.smith",
                "displayName": "Alice Kim",
                "emailAddress": "alice.kim@example.test",
            },
        }],
    }
    state["query_results"] = [{
        "id": "jira-people", "source": "jira", "result": result,
        "_remoteEvidence": backend.attest_query_result("jira-people", "jira", result),
    }]
    payload = backend.role_payload(
        "research_analyst", state, [], {"type": "object"},
        output_kind="structured",
    )
    content = payload["evidencePack"]["sources"][0]["content"]
    for raw in (
        "557058:opaque-account-id", "alice.smith", "Alice Kim",
        "alice.kim@example.test",
    ):
        assert raw not in content
    assert "[[person:" in content


def test_mixed_project_result_is_partitioned_without_cross_scope_content():
    from app.agent.tools import _ctx

    state = _runtime_state("research_analyst")
    result = {
        "scopeProjects": ["DL", "OTHER"],
        "tickets": [
            {"key": "DL-1", "summary": "DL-only-secret"},
            {"key": "OTHER-2", "summary": "OTHER-only-secret"},
        ],
        "total": 2,
    }
    state["query_results"] = [{
        "id": "mixed", "source": "jira", "result": result,
        "_remoteEvidence": backend.attest_query_result("mixed", "jira", result),
    }]
    _ctx.bind(SimpleNamespace(), SimpleNamespace(
        search_jira_projects=["DL", "OTHER"], search_confluence_spaces=[],
        search_bitbucket_projects=[], jira_env="mock", jira_user="", managers=[],
        jira_base="http://jira.example", confluence_base="", bitbucket_base="",
    ))
    try:
        payload = backend.role_payload(
            "research_analyst", state, [], {"type": "object"},
            output_kind="structured",
        )
    finally:
        _ctx.bind()
    by_scope = {
        source["scope"]: source["content"]
        for source in payload["evidencePack"]["sources"]
    }
    assert set(by_scope) == {"DL", "OTHER"}
    assert "DL-1" in by_scope["DL"] and "OTHER-2" not in by_scope["DL"]
    assert "OTHER-2" in by_scope["OTHER"] and "DL-1" not in by_scope["OTHER"]
    assert "OTHER-only-secret" not in by_scope["DL"]
    assert "DL-only-secret" not in by_scope["OTHER"]


def test_declared_scope_cannot_override_actual_ticket_scope():
    result = {
        "scopeProjects": ["OTHER"],
        "tickets": [{"key": "DL-1", "summary": "actual DL"}],
    }
    with pytest.raises(backend.MaxisRuntimeError, match="canonical scope"):
        backend.attest_query_result("mismatch", "jira", result)


def test_verified_content_removes_nested_urls_before_outbound_pack():
    state = _runtime_state("research_analyst")
    result = {
        "scopeProjects": ["DL"],
        "tickets": [{
            "key": "DL-1",
            "description": "자료 https://outside.example/path?token=not-sent",
        }],
    }
    state["query_results"] = [{
        "id": "jira-1", "source": "jira", "result": result,
        "_remoteEvidence": backend.attest_query_result("jira-1", "jira", result),
    }]

    payload = backend.role_payload(
        "research_analyst", state, [], {"type": "object"},
        output_kind="structured",
    )
    content = payload["evidencePack"]["sources"][0]["content"]
    assert "https://outside.example" not in content
    assert "[url omitted]" in content


def test_portfolio_people_results_are_attested_ltm_aggregate_evidence():
    state = _runtime_state("portfolio_analyst")
    result = {
        "module": "ETL",
        "people": [{"id": "skcc.x1103", "workCount": 4}],
        "complete": True,
    }
    state["query_results"] = [{
        "id": "people-1", "source": "people", "result": result,
        "_remoteEvidence": backend.attest_query_result(
            "people-1", "people", result,
        ),
    }]
    payload = backend.role_payload(
        "portfolio_analyst", state, [], {"type": "object"},
        output_kind="structured",
    )
    pack = EvidencePackV1.model_validate_json(
        json.dumps(payload["evidencePack"], ensure_ascii=False), strict=True,
    )
    assert [source.kind for source in pack.sources] == ["ltm"]
    assert "skcc.x1103" not in pack.sources[0].content
    assert "[[person:" in pack.sources[0].content


def test_unattested_tampered_or_unsupported_evidence_fails_closed():
    state = _runtime_state("research_analyst")
    state["query_results"] = [{
        "id": "old", "source": "jira",
        "result": {"scopeProjects": ["DL"], "tickets": []},
    }]
    with pytest.raises(backend.MaxisRuntimeError, match="조회 증명"):
        backend.role_payload(
            "research_analyst", state, [], {}, output_kind="structured")

    result = {"scopeProjects": ["DL"], "tickets": []}
    proof = backend.attest_query_result("jira-1", "jira", result)
    proof["scopes"] = ["OTHER"]
    state["query_results"] = [{
        "id": "jira-1", "source": "jira", "result": result,
        "_remoteEvidence": proof,
    }]
    with pytest.raises(backend.MaxisRuntimeError, match="증명을 검증"):
        backend.role_payload(
            "research_analyst", state, [], {}, output_kind="structured")

    unsupported = {"results": [{"path": "local.txt"}]}
    state["query_results"] = [{
        "id": "file-1", "source": "filesystem", "result": unsupported,
        "_remoteEvidence": backend.attest_query_result(
            "file-1", "filesystem", unsupported,
        ),
    }]
    with pytest.raises(backend.MaxisRuntimeError, match="아직 지원하지 않는"):
        backend.role_payload(
            "research_analyst", state, [], {}, output_kind="structured")


def test_downstream_remote_role_omits_local_only_web_and_github_rows():
    state = _runtime_state("knowledge_curator")
    jira = {"scopeProjects": ["DL"], "tickets": [{"key": "DL-1"}]}
    state.update({
        "situation": "로컬 조사 결과를 정리했다.",
        "evidence": ["공개 자료는 로컬 Research Analyst가 교차 확인했다."],
        "query_results": [
            {
                "id": "web-1", "source": "web",
                "result": {"results": [{"url": "https://example.com/internal"}]},
            },
            {
                "id": "github-1", "source": "github",
                "result": {"results": [{"url": "https://github.com/acme/private"}]},
            },
            {
                "id": "jira-1", "source": "jira", "result": jira,
                "_remoteEvidence": backend.attest_query_result("jira-1", "jira", jira),
            },
        ],
    })

    payload = backend.role_payload(
        "knowledge_curator", state, [], {"type": "object"},
        output_kind="structured",
    )
    pack = EvidencePackV1.model_validate_json(
        json.dumps(payload["evidencePack"], ensure_ascii=False), strict=True,
    )

    assert [source.kind for source in pack.sources] == ["jira"]
    rendered = pack.model_dump_json()
    assert "example.com/internal" not in rendered
    assert "github.com/acme/private" not in rendered
    assert {row.name for row in pack.role_context} == {"situation", "evidence"}


def test_portfolio_remote_context_projects_prefetched_pmo_material():
    state = _runtime_state("portfolio_analyst")
    state.update({
        "intent": "progress",
        "mentioned_keys": ["DL-1"],
        "module": "ETL",
        "portfolio_snapshot": {
            "version": "portfolio.snapshot.v1",
            "materials": [{"kind": "ticket_progress", "complete": True}],
        },
        "group_activity": "모듈 활동을 결정적으로 취합했다.",
        "ticket_progress": "DL-1의 완료·잔여 작업을 결정적으로 취합했다.",
    })

    payload = backend.role_payload(
        "portfolio_analyst", state, [], {"type": "object"},
        output_kind="structured",
    )
    pack = EvidencePackV1.model_validate_json(
        json.dumps(payload["evidencePack"], ensure_ascii=False), strict=True,
    )

    assert [row.name for row in pack.role_context] == [
        "intent", "mentioned_keys", "module", "portfolio_snapshot",
        "group_activity", "ticket_progress",
    ]


@pytest.mark.parametrize(("situation", "omitted"), [
    pytest.param("a" * 3898, False, id="ascii-exact-char-limit"),
    pytest.param("a" * 3899, True, id="ascii-over-char-limit"),
    pytest.param("가" * 1332, False, id="korean-below-byte-limit"),
    pytest.param("가" * 1332 + "ab", False, id="korean-exact-byte-limit"),
    pytest.param("가" * 1332 + "abc", True, id="korean-over-byte-limit"),
    pytest.param("가" * 1500, True, id="korean-original-reproduction"),
    pytest.param("🙂" * 999 + "ab", False, id="emoji-exact-byte-limit"),
    pytest.param("🙂" * 1000, True, id="emoji-over-byte-limit"),
])
def test_role_context_size_boundaries_preserve_state_and_mark_omission(situation, omitted):
    state = _runtime_state("knowledge_curator")
    state["situation"] = situation
    before = deepcopy(state)

    payload = backend.role_payload(
        "knowledge_curator", state, [], {"type": "object"},
        output_kind="structured",
    )
    pack = EvidencePackV1.model_validate_json(
        json.dumps(payload["evidencePack"], ensure_ascii=False), strict=True,
    )

    assert state == before
    assert len(pack.role_context) == 1
    row = pack.role_context[0]
    expected = {"omitted": True, "originalChars": len(backend._canonical_json(situation))}
    assert json.loads(row.content_json) == (expected if omitted else situation)
    assert row.partial is omitted
    assert pack.partial is omitted
    assert ("partial_role_context" in pack.flags) is omitted
    assert row.observed_at == datetime.fromisoformat(state["correlation_started_at"])


@pytest.mark.parametrize("situation", ["직전 질문까지 정리한 상황", "가" * 1500], ids=["small", "omitted"])
def test_interview_continuation_preserves_carried_role_context_observation_time(situation):
    from app.agent.workflow.session import _turn_start_patch

    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    prior = _runtime_state("knowledge_curator")
    prior.update({
        "correlation_id": "corr-kept",
        "correlation_started_at": old,
        "situation": situation,
    })
    turn = _turn_start_patch(
        "마감은 9월 30일이야",
        prior,
        question_receipt_projection={"field": "duedate"},
    )

    assert turn["correlation_id"] == "corr-kept"
    assert turn["correlation_started_at"] != old
    payload = backend.role_payload(
        "knowledge_curator", {**prior, **turn}, [], {"type": "object"},
        output_kind="structured",
    )
    pack = EvidencePackV1.model_validate_json(
        json.dumps(payload["evidencePack"], ensure_ascii=False), strict=True,
    )
    assert pack.role_context
    assert pack.role_context[0].observed_at.isoformat() == datetime.fromisoformat(old).isoformat()
    # The newly observed user-intent provenance is fresh, but it must not refresh the
    # one-hour-old carried RoleContext. Aggregate freshness therefore stays visibly mixed.
    assert pack.freshness == "mixed"
    assert pack.sources[0].canonical_id.startswith("ltm:user-intent:")
    assert "stale_role_context" in pack.flags
    if situation == "가" * 1500:
        assert json.loads(pack.role_context[0].content_json) == {
            "omitted": True, "originalChars": 1502,
        }
        assert pack.role_context[0].partial is True

    # A Role that actually recomputes the value changes the digest and receives the
    # current turn timestamp instead of inheriting the stale provenance entry.
    refreshed = {**prior, **turn, "situation": "현재 턴에서 다시 계산한 상황"}
    payload = backend.role_payload(
        "knowledge_curator", refreshed, [], {"type": "object"},
        output_kind="structured",
    )
    refreshed_pack = EvidencePackV1.model_validate_json(
        json.dumps(payload["evidencePack"], ensure_ascii=False), strict=True,
    )
    assert refreshed_pack.freshness == "fresh"


@pytest.mark.parametrize("role_id", ("work_architect", "auditor", "editor_author"))
def test_stale_or_partial_work_evidence_is_blocked_before_network(monkeypatch, role_id):
    state = _runtime_state(role_id)
    result = {"scopeProjects": ["DL"], "tickets": [{"key": "DL-1"}]}
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    state["query_results"] = [{
        "id": "jira-1", "source": "jira", "result": result,
        "_remoteEvidence": backend.attest_query_result(
            "jira-1", "jira", result, retrieved_at=old,
        ),
    }]
    payload = backend.role_payload(
        role_id, state, [], {"type": "object"},
        output_kind="structured",
    )
    assert payload["evidencePack"]["freshness"] in {"stale", "mixed"}
    monkeypatch.setattr(
        backend, "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(
            route="maxis", binding=SimpleNamespace(), profile="prod"),
    )
    monkeypatch.setattr(backend, "_profile_snapshot", lambda *_a, **_k: _profile("prod"))
    with pytest.raises(backend.MaxisRuntimeError, match="freshness"):
        backend.invoke(role_id, payload, "thread-1", "corr-1")


def test_query_runner_preserves_23h_swr_cache_age_and_blocks_remote_draft(monkeypatch):
    from app.agent import tools as tool_registry
    from app.agent.tools import _ctx
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.infra.cache import Cache

    cache = Cache(":memory:", dead_ttl=24 * 3600)
    cached = {"scopeProjects": ["DL"], "tickets": [], "returned": 0, "total": 0}
    cache.set("stale-jql", cached, ttl=0)
    fetched_at = time.time() - 23 * 3600
    cache._conn.execute(
        "UPDATE cache SET fetched_at=?, ttl=0 WHERE key=?", (fetched_at, "stale-jql")
    )
    cache._conn.commit()

    class StaleJql:
        @staticmethod
        def invoke(_args):
            value, freshness = cache.get_or_set_swr(
                "stale-jql", 0,
                lambda: (_ for _ in ()).throw(RuntimeError("offline")),
                lambda *_args: None,
            )
            assert freshness == "stale"
            return value

    settings = SimpleNamespace(
        search_jira_projects=["DL"], search_confluence_spaces=[],
        search_bitbucket_projects=[], jira_env="mock", jira_user="",
        managers=[], jira_base="http://jira.example",
        confluence_base="", bitbucket_base="",
    )
    fake_client = SimpleNamespace(cache=cache)
    _ctx.bind(fake_client, settings)
    monkeypatch.setitem(tool_registry.BY_NAME, "run_jql_v2", StaleJql())
    try:
        state = _runtime_state("work_architect")
        state.update({
            "intent": "ask",
            "query_plan": {"queries": [{
                "id": "jira-stale", "source": "jira", "query": "stale",
                "where": "status is not EMPTY", "completeness": "page",
            }]},
        })
        queried = QueryRunner()._run(state)
        proof = queried["query_evidence_attestations"]["jira:jira-stale"]
        observed = datetime.fromisoformat(proof["retrievedAt"].replace("Z", "+00:00"))
        assert abs(observed.timestamp() - fetched_at) < 2
        assert proof["partial"] is True
        assert proof["provenance"]["servedStale"] is True
        assert cache.served_stale_at > 0

        state.update(queried)
        payload = backend.role_payload(
            "work_architect", state, [], {"type": "object"},
            output_kind="structured",
        )
        # A fresh request citation does not refresh the stale connector observation.
        assert payload["evidencePack"]["freshness"] == "mixed"
        assert "stale_source" in payload["evidencePack"]["flags"]
        assert payload["evidencePack"]["partial"] is True
        monkeypatch.setattr(
            backend, "resolve_role",
            lambda *_a, **_k: backend.RoleResolution(
                route="maxis", binding=SimpleNamespace(), profile="prod"
            ),
        )
        monkeypatch.setattr(backend, "_profile_snapshot", lambda *_a, **_k: _profile("prod"))
        from app.agent.maxis import client as client_module
        monkeypatch.setattr(
            client_module, "DifyClient",
            lambda *_a, **_k: pytest.fail("stale draft must be blocked before network"),
        )
        with pytest.raises(backend.MaxisRuntimeError, match="freshness"):
            backend.invoke("work_architect", payload, "thread-1", "corr-1")
    finally:
        _ctx.bind()
        cache.close()


def test_invoke_sends_only_guarded_pack_and_pinned_metadata(monkeypatch, audit_events):
    import httpx
    from app.agent.maxis.registry import WorkflowBinding

    state = _runtime_state("request_architect")
    runtime_route = {"value": "maxis"}
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {"value": {"type": "string"}}, "required": ["value"],
    }
    payload = backend.role_payload(
        "request_architect", state, [], schema, output_kind="structured")
    binding = WorkflowBinding(
        key="agent_core", app_type="chatflow", endpoint="/chat-messages",
        response_mode="blocking", conversation_mode="stateless",
        api_key_env="LAKE_MAXIS_AGENT_CORE_API_KEY",
        output_contract="remote-role-result.v1", max_response_bytes=1048576,
        roles=("request_architect",),
    )
    monkeypatch.setattr(
        backend, "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(
            route=runtime_route["value"], binding=binding, profile="prod",
            workflow_bundle_version="bundle-1", knowledge_version="knowledge-1",
            knowledge_dataset_id=DATASET_A,
            knowledge_manifest_sha256=MANIFEST_SHA,
        ),
    )
    monkeypatch.setattr(backend, "_profile_snapshot", lambda *_a, **_k: _profile("prod"))

    from app.agent.maxis import client as client_module
    from app.agent.maxis import credentials
    real_client = client_module.DifyClient

    monkeypatch.setattr(credentials, "get_for", lambda *_a: "secret-read-locally")
    observed = {
        "remote_status": "ok",
        "remote_value": "remote",
        "remote_manifest": MANIFEST_SHA,
        "knowledge_proof": SENTINEL_VALUE,
    }

    class FakeClient:
        def __init__(self, config):
            observed["config"] = config

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def invoke(self, _binding, **kwargs):
            observed.update(kwargs)
            status = observed["remote_status"]
            evidence = kwargs["inputs"]["context_pack"]
            assert len(evidence.sources) == 1
            intent_source_id = evidence.sources[0].source_id
            answer = {
                "contract": "remote-role-result.v1",
                "role_id": "request_architect",
                "status": status,
                "payload": ({"value": observed["remote_value"]}
                            if status == "ok" else {}),
                "claims": ([{
                    "claim_id": "value_claim",
                    "kind": "recommendation",
                    "text": observed["remote_value"],
                    "citation_ids": ["value_citation"],
                }] if status == "ok" else []),
                "payload_claims": ([{
                    "path": "/value", "claim_ids": ["value_claim"],
                }] if status == "ok" else []),
                "citations": ([{
                    "citation_id": "value_citation",
                    "source_id": intent_source_id,
                    "locator": "",
                }] if status == "ok" else []),
                "missing_inputs": (["완료 기준을 알려 주세요"]
                                   if status == "needs_input" else []),
                "warnings": observed.get("remote_warnings", []),
                "workflow_version": AGENT_WORKFLOW_ID,
                "knowledge_version": "knowledge-1",
                "knowledge_manifest_sha256": observed["remote_manifest"],
                "knowledge_proof": observed["knowledge_proof"],
            }
            def handler(request):
                if observed.get("http_error"):
                    return httpx.Response(503, text="private upstream content")
                return httpx.Response(200, json={
                    "event": "message", "message_id": "message-1",
                    "conversation_id": "private-conversation-id",
                    "workflow_run_id": EDITOR_WORKFLOW_ID,
                    "answer": json.dumps(answer, ensure_ascii=False),
                    "metadata": {
                        "usage": {"prompt_tokens": 8, "completion_tokens": 7, "total_tokens": 15},
                        "retriever_resources": [
                            {"dataset_id": DATASET_A, "score": score} for score in (0.4, 0.9)
                        ],
                    },
                })
            with httpx.Client(transport=httpx.MockTransport(handler)) as http:
                return real_client(observed["config"], client=http).invoke(_binding, **kwargs)

    monkeypatch.setattr(client_module, "DifyClient", FakeClient)
    result = backend.invoke("request_architect", payload, "thread-1", "corr-1")

    assert isinstance(observed["inputs"]["context_pack"], EvidencePackV1)
    assert set(observed["inputs"]) == {
        "context_pack", "role_id", "correlation_id", "output_contract",
        "workflow_version", "knowledge_version",
    }
    assert observed["user"] == observed["inputs"]["context_pack"].pseudonymous_user
    assert observed["workflow_id"] == AGENT_WORKFLOW_ID
    assert observed["expected_knowledge_dataset_id"] == DATASET_A
    assert observed["inputs"]["context_pack"].knowledge_manifest_sha256 == MANIFEST_SHA
    assert "outputSchema" not in json.dumps(
        observed["inputs"]["context_pack"].model_dump(mode="json"))
    assert result["output"] == {"value": "remote"}
    assert result["metadata"]["knowledgeDatasetId"] == DATASET_A
    assert result["metadata"]["knowledgeManifestSha256"] == MANIFEST_SHA
    assert result["metadata"]["knowledgeRetrieval"] == {
        "verified": True,
        "resourceCount": 2,
        "scoredResourceCount": 2,
        "minimumScore": 0.4,
        "maximumScore": 0.9,
    }
    assert result["statePatch"]["quality_gate"]["schemaValid"] is True
    assert [event.status for event in audit_events] == ["started", "succeeded"]
    request_metrics = audit_events[-1].remote_request
    assert request_metrics.transport_status == "succeeded"
    assert request_metrics.result_status == "completed"
    assert request_metrics.quality_status == "not_evaluated"
    assert request_metrics.total_tokens == 15
    assert request_metrics.native_model_call_count is None
    assert request_metrics.reported_cost is None
    assert request_metrics.workflow_run_id == EDITOR_WORKFLOW_ID
    assert request_metrics.requested_workflow_id == AGENT_WORKFLOW_ID
    assert audit_events[-1].runtime_latency_ms >= 0
    assert result["metadata"]["requestTelemetry"]["result_status"] == "completed"
    assert all(event.correlation_id == "corr-1" for event in audit_events)
    assert all(not hasattr(event, "prompt") and not hasattr(event, "answer")
               for event in audit_events)

    observed["knowledge_proof"] = "LTM_RELEASE_SENTINEL_V1=drifted"
    with pytest.raises(backend.MaxisRuntimeError, match="Knowledge release"):
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert audit_events[-1].error_code == "knowledge_sentinel_mismatch"
    assert audit_events[-1].remote_request.transport_status == "succeeded"
    assert audit_events[-1].remote_request.result_status == "blocked"

    # Shadow evaluation invokes this same boundary asynchronously and must discard
    # the same drifted result instead of persisting it as a comparison candidate.
    runtime_route["value"] = "shadow"
    with pytest.raises(backend.MaxisRuntimeError, match="Knowledge release"):
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert audit_events[-1].error_code == "knowledge_sentinel_mismatch"
    runtime_route["value"] = "maxis"
    observed["knowledge_proof"] = SENTINEL_VALUE

    # A Dify blocked state is a control outcome, never applicable partial state.
    observed["remote_status"] = "blocked"
    with pytest.raises(backend.MaxisBlockedResult, match="적용 가능한 결과") as blocked:
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert blocked.value.recovery.code == "unusable_result"
    assert "이 응답은 변경에 적용하지 않았습니다" in str(blocked.value)
    assert not hasattr(blocked.value, "state_patch")
    assert not hasattr(blocked.value, "missing_inputs")
    assert [event.status for event in audit_events[-2:]] == ["started", "blocked"]
    assert audit_events[-1].error_code == "remote_status_blocked"
    assert audit_events[-1].remote_request.result_status == "blocked"

    observed["remote_warnings"] = ["contract_guard:payload_type_mismatch"]
    with pytest.raises(backend.MaxisBlockedResult) as blocked:
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert blocked.value.recovery.code == "invalid_result_contract"
    assert "필수 필드" in str(blocked.value)

    # A rejected remote result still has to match the pinned release. Its warning
    # codes cannot produce trusted recovery guidance before that check passes.
    observed["knowledge_proof"] = "LTM_RELEASE_SENTINEL_V1=drifted"
    with pytest.raises(backend.MaxisRuntimeError, match="Knowledge release") as drifted:
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert not isinstance(drifted.value, backend.MaxisBlockedResult)
    assert audit_events[-1].error_code == "knowledge_sentinel_mismatch"
    observed["knowledge_proof"] = SENTINEL_VALUE
    observed["remote_warnings"] = []

    observed["remote_status"] = "needs_input"
    with pytest.raises(backend.MaxisNeedsInput) as needs:
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert needs.value.missing_inputs == ("완료 기준을 알려 주세요",)
    assert needs.value.state_patch["quality_gate"]["ok"] is False
    assert [event.status for event in audit_events[-2:]] == ["started", "succeeded"]
    assert audit_events[-1].remote_request.result_status == "needs_input"
    assert needs.value.metadata["requestTelemetry"]["result_status"] == "needs_input"

    observed.update(remote_status="ok", remote_value="[[person:0123456789ab]]")
    with pytest.raises(backend.MaxisRuntimeError, match="person reference"):
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert audit_events[-1].error_code == "person_reference_unresolved"

    observed.update(remote_value="remote", remote_manifest="b" * 64)
    with pytest.raises(backend.MaxisRuntimeError, match="응답 계약"):
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert audit_events[-1].status == "blocked"
    assert audit_events[-1].error_code == "response_knowledge_manifest_mismatch"

    observed["http_error"] = True
    with pytest.raises(backend.MaxisRuntimeError, match="호출에 실패"):
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert audit_events[-1].remote_request.transport_status == "failed"
    assert audit_events[-1].remote_request.http_status == 503
    assert audit_events[-1].remote_request.result_status == "failed"
    assert audit_events[-1].remote_request.total_tokens is None
    serialized = json.dumps([event.model_dump(mode="json") for event in audit_events])
    assert "private upstream" not in serialized
    assert "secret-read-locally" not in serialized
    assert "private-conversation" not in serialized


def test_role_payload_binds_exact_structured_and_text_schemas_inside_evidence_pack():
    state = _runtime_state("request_architect")
    structured_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "string", "maxLength": 80},
                "maxItems": 3,
            },
        },
        "required": ["items"],
    }
    structured = backend.role_payload(
        "request_architect", state, [], structured_schema,
        output_kind="structured",
    )
    structured_pack = EvidencePackV1.model_validate_json(
        json.dumps(structured["evidencePack"]), strict=True,
    )
    assert structured_pack.payload_schema() == structured_schema
    assert len(structured_pack.payload_schema_sha256) == 64
    assert "outputSchema" not in structured

    text_state = _runtime_state("result_integrator")
    text = backend.role_payload(
        "result_integrator", text_state, [], {}, output_kind="text",
    )
    text_pack = EvidencePackV1.model_validate_json(
        json.dumps(text["evidencePack"]), strict=True,
    )
    assert text_pack.payload_schema() == {
        "type": "object",
        "additionalProperties": False,
        "properties": {"text": {"type": "string", "minLength": 1}},
        "required": ["text"],
    }

@pytest.mark.parametrize("mismatch", ["dataset", "runtime_manifest", "pack_manifest"])
def test_invoke_rejects_knowledge_pin_mismatch_before_network(
    mismatch, monkeypatch, audit_events,
):
    state = _runtime_state("request_architect")
    payload = backend.role_payload(
        "request_architect", state, [], {"type": "object"},
        output_kind="structured",
    )
    if mismatch == "dataset":
        payload["runtime"]["knowledgeDatasetId"] = (
            "22222222-2222-4222-8222-222222222222"
        )
    elif mismatch == "runtime_manifest":
        payload["runtime"]["knowledgeManifestSha256"] = "b" * 64
    else:
        payload["evidencePack"]["knowledge_manifest_sha256"] = "b" * 64

    monkeypatch.setattr(
        backend, "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(
            route="maxis", binding=SimpleNamespace(), profile="prod",
        ),
    )
    monkeypatch.setattr(backend, "_profile_snapshot", lambda *_a, **_k: _profile("prod"))
    from app.agent.maxis import client as client_module

    monkeypatch.setattr(
        client_module, "DifyClient",
        lambda *_a, **_k: pytest.fail("knowledge mismatch must block before network"),
    )
    with pytest.raises(backend.MaxisRuntimeError, match="dataset/manifest"):
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert [event.status for event in audit_events] == ["started", "blocked"]
    assert audit_events[-1].error_code == "knowledge_manifest_mismatch"


class _DemoStructured(StructuredAgent):
    name = "knowledge_curator"

    def system(self, state):
        return "local system"

    def task(self, state):
        return "local task"

    def schema(self):
        return {
            "type": "object", "additionalProperties": False,
            "properties": {"value": {"type": "string"}}, "required": ["value"],
        }

    def apply(self, state, out):
        return {"answer": out["value"]}


def test_remote_result_still_passes_local_schema_apply_and_safe_sidecar(monkeypatch):
    agent = _DemoStructured()
    monkeypatch.setattr(
        backend, "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(route="maxis"),
    )
    monkeypatch.setattr(
        backend, "invoke_role",
        lambda *_a, **_k: backend.RemoteRoleOutput(
            {"value": "remote"},
            {
                "evidence_workspace": {"sources": [], "claims": []},
                "freshness": {"status": "empty"},
                "quality_gate": {"ok": True},
            },
            {
                "latencyMs": 125,
                "workflowBundleVersion": "bundle-1",
                "usage": {
                    "promptTokens": 7, "completionTokens": 3, "totalTokens": 10,
                },
            },
        ),
    )
    monkeypatch.setattr(
        agent, "_invoke_local_structured",
        lambda *_a, **_k: pytest.fail("local provider must not be called"),
    )
    out = agent.node()({
        "messages": [HumanMessage(content="request")],
        "runtime_role_routes": {"knowledge_curator": "maxis"},
    })
    assert out["answer"] == "remote"
    assert out["quality_gate"] == {"ok": True}
    assert out["runtime_role_engines"] == {"knowledge_curator": "maxis"}
    assert out["runtime_last_role_engine"] == "maxis"
    assert out["runtime_role_telemetry"]["knowledge_curator"]["totalTokens"] == 10

    from app.agent.workflow.session import _usage_with_remote
    usage = _usage_with_remote({
        "calls": 1, "promptTokens": 2, "completionTokens": 1,
        "totalTokens": 3, "model": "local-model", "costUsd": 0.01,
    }, out)
    assert usage["calls"] == 2 and usage["totalTokens"] == 13
    assert usage["remoteCalls"] == 1
    assert usage["byNode"]["knowledge_curator"]["seconds"] == 0.125
    assert usage["callsDetail"][-1]["engine"] == "maxis"
    assert "costUsd" not in usage


def test_remote_needs_input_uses_typed_questions_for_every_agent_runtime(monkeypatch):
    from app.agent.workflow.agents.portfolio_analyst import PortfolioAnalyst
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    monkeypatch.setattr(
        backend, "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(route="maxis"),
    )

    def needs_input(role_id, *_args, **_kwargs):
        raise backend.MaxisNeedsInput(
            role_id,
            ["대상 데이터셋을 알려 주세요.", "완료 기준을 알려 주세요.",
             "적용 범위를 알려 주세요.", "네 번째 질문은 노출하지 마세요."],
            state_patch={
                "evidence_workspace": {"sources": [], "claims": []},
                "freshness": {"status": "empty"},
                "quality_gate": {"ok": True},
            },
            metadata={
                "latencyMs": 25,
                "usage": {"promptTokens": 4, "completionTokens": 2, "totalTokens": 6},
            },
        )

    monkeypatch.setattr(backend, "invoke_role", needs_input)
    for agent in (_DemoStructured(), ResultIntegrator(), PortfolioAnalyst()):
        state = {
            "messages": [HumanMessage(content="업무를 정리해 줘")],
            "request_text": "업무를 정리해 줘",
            "runtime_role_routes": {agent.name: "maxis"},
        }
        result = agent.node()(state)

        assert "error" not in result
        assert len(result["questions"]) == 3
        assert all(question["contract"] == "question.v1"
                   and question["kind"] == "text"
                   and question["field"].startswith(f"maxis:{agent.name}:")
                   and question["required_input"] is True
                   and question["why_required"]
                   for question in result["questions"])
        assert result["runtime_role_sidecars"][agent.name]["quality_gate"] == {
            "ok": True,
        }
        assert result["runtime_role_telemetry"][agent.name]["totalTokens"] == 6


def test_auditor_needs_input_cannot_route_to_approval():
    from app.agent.workflow.graph import route_after_auditor

    assert route_after_auditor({
        "questions": [{
            "contract": "question.v1",
            "question": "완료 기준을 알려 주세요.",
            "kind": "text",
            "required_input": True,
        }],
        # The deterministic fan-out join can independently find the current draft
        # structurally valid; the interview still owns routing precedence.
        "review": {"ok": True},
    }) == "respond"


def test_remote_fanout_sidecars_join_without_concurrent_state_updates(monkeypatch):
    """Both remote branches may publish metadata in the same LangGraph superstep."""
    from langgraph.graph import END, START, StateGraph

    from app.agent.workflow import graph as workflow_graph
    from app.agent.workflow.agents.auditor import Auditor
    from app.agent.workflow.agents.people_advisor import PeopleAdvisor
    from app.agent.workflow.state import AgentState, Node

    monkeypatch.setattr(
        backend, "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(route="maxis"),
    )
    people = PeopleAdvisor()
    auditor = Auditor()

    def remote(role: str, output: dict) -> backend.RemoteRoleOutput:
        return backend.RemoteRoleOutput(
            output,
            {
                "evidence_workspace": {
                    "sources": [{"sourceId": role}], "claims": [],
                },
                "freshness": {"status": "fresh", "role": role},
                "quality_gate": {"ok": True, "role": role},
            },
            {"usage": {"totalTokens": 1}, "latencyMs": 1},
        )

    graph = StateGraph(AgentState)
    graph.add_node(
        Node.PEOPLE_ADVISOR,
        lambda state: people.apply_routed(
            state, remote("people_advisor", {"assignments": [], "caution": ""}),
        ),
    )
    graph.add_node(
        Node.AUDITOR,
        lambda state: auditor.apply_routed(state, remote("auditor", {
            "grounded": True, "rule_compliant": True, "answers_request": True,
            "problems": [], "summary": "검증 통과",
        })),
    )
    graph.add_node("merge_assignments", workflow_graph._merge_assignments)
    graph.add_edge(START, Node.PEOPLE_ADVISOR)
    graph.add_edge(START, Node.AUDITOR)
    graph.add_edge(Node.PEOPLE_ADVISOR, "merge_assignments")
    graph.add_edge(Node.AUDITOR, "merge_assignments")
    graph.add_edge("merge_assignments", END)

    result = graph.compile().invoke({
        "messages": [HumanMessage(content="검증용 Task를 만들어줘")],
        "request_text": "검증용 Task를 만들어줘",
        "intent": "plan_work",
        "runtime_role_routes": {
            "people_advisor": "maxis", "auditor": "maxis",
        },
        "draft": {"mode": "task", "items": [{
            "item_id": "work-1", "summary": "회귀 테스트 구현", "type": "Task",
            "description": (
                "## 배경 및 목적\n병렬 상태 병합을 검증한다.\n\n"
                "## 작업 범위\n원격 메타데이터를 병합한다.\n\n"
                "## 완료 조건 (DoD)\n- 두 분기가 완료된다.\n- join 결과를 확인한다."
            ),
        }]},
    })

    assert set(result["runtime_role_sidecars"]) == {"people_advisor", "auditor"}
    # The explicit join mirrors the serial schedule: Auditor is the final projection.
    assert result["quality_gate"] == {"ok": True, "role": "auditor"}
    assert result["freshness"]["role"] == "auditor"
    assert result["runtime_last_role"] == "auditor"


def test_remote_fanout_needs_input_joins_questions_and_routes_to_response(monkeypatch):
    """Two remote interview outcomes share no channel until the production join."""
    from langgraph.graph import END, START, StateGraph

    from app.agent.workflow import graph as workflow_graph
    from app.agent.workflow.agents.auditor import Auditor
    from app.agent.workflow.agents.people_advisor import PeopleAdvisor
    from app.agent.workflow.state import AgentState, Node

    monkeypatch.setattr(
        backend, "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(route="maxis"),
    )

    def needs_input(role_id, *_args, **_kwargs):
        questions = {
            "people_advisor": ["담당 후보 범위를 알려 주세요.", "제외할 사람을 알려 주세요."],
            "auditor": ["완료 기준을 알려 주세요.", "예외 승인 기준을 알려 주세요."],
        }[role_id]
        raise backend.MaxisNeedsInput(
            role_id,
            questions,
            state_patch={
                "evidence_workspace": {"sources": [], "claims": []},
                "freshness": {"status": "empty", "role": role_id},
                "quality_gate": {"ok": False, "role": role_id},
            },
            metadata={
                "latencyMs": 1,
                "usage": {"promptTokens": 1, "completionTokens": 1, "totalTokens": 2},
            },
        )

    monkeypatch.setattr(backend, "invoke_role", needs_input)
    graph = StateGraph(AgentState)
    graph.add_node(Node.PEOPLE_ADVISOR, PeopleAdvisor().node())
    graph.add_node(Node.AUDITOR, Auditor().node())
    graph.add_node("merge_assignments", workflow_graph._merge_assignments)
    # Use a real AgentState channel as the route sentinel. Unknown node-output keys
    # are intentionally discarded by StateGraph and cannot prove which edge ran.
    graph.add_node("responded", lambda _state: {"interpretation": "responded"})
    graph.add_node("unexpected", lambda _state: {"error": "unexpected route"})
    graph.add_edge(START, Node.PEOPLE_ADVISOR)
    graph.add_edge(START, Node.AUDITOR)
    graph.add_edge(Node.PEOPLE_ADVISOR, "merge_assignments")
    graph.add_edge(Node.AUDITOR, "merge_assignments")
    graph.add_conditional_edges(
        "merge_assignments",
        workflow_graph.route_after_auditor,
        {"respond": "responded", "propose": "unexpected", "revise": "unexpected"},
    )
    graph.add_edge("responded", END)
    graph.add_edge("unexpected", END)

    result = graph.compile().invoke({
        "messages": [HumanMessage(content="검증용 Task를 만들어줘")],
        "request_text": "검증용 Task를 만들어줘",
        "intent": "plan_work",
        "runtime_role_routes": {
            "people_advisor": "maxis", "auditor": "maxis",
        },
        "draft": {"mode": "task", "items": [{
            "item_id": "work-1", "summary": "입력 요청 회귀", "type": "Task",
            "description": (
                "## 배경 및 목적\n동시 입력 요청을 검증한다.\n\n"
                "## 작업 범위\n역할별 질문을 병합한다.\n\n"
                "## 완료 조건 (DoD)\n- 응답 경로로 종료한다."
            ),
        }]},
    })

    assert result.get("interpretation") == "responded", result
    assert not result.get("error"), result.get("error")
    assert set(result["runtime_role_questions"]) == {"people_advisor", "auditor"}
    assert len(result["questions"]) == 3
    assert [row["field"] for row in result["questions"]] == [
        "maxis:auditor:1", "maxis:auditor:2", "maxis:people_advisor:1",
    ]
    assert result.get("approval_token") in (None, "")


def test_shadow_result_is_discarded_from_state_and_ui_sidecar(monkeypatch):
    agent = _DemoStructured()
    called = []
    monkeypatch.setattr(
        backend, "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(route="shadow"),
    )
    monkeypatch.setattr(
        agent, "_invoke_local_structured", lambda *_a, **_k: {"value": "local"},
    )
    monkeypatch.setattr(
        backend, "run_shadow", lambda *_a, **_k: called.append("discarded"),
    )
    out = agent.node()({"messages": [HumanMessage(content="request")]})
    assert out == {"answer": "local"}
    assert called == ["discarded"]


def test_session_shape_projects_no_source_content_or_unknown_remote_fields():
    from app.agent.workflow.session import _shape

    shaped = _shape("thread-1", {
        "runtime_engine": "local",
        "runtime_profile": "dev",
        "workflow_bundle_version": "bundle-1",
        "knowledge_version": "knowledge-1",
        "knowledge_dataset_id": DATASET_A,
        "knowledge_manifest_sha256": MANIFEST_SHA,
        "correlation_id": "corr-1",
        "runtime_role_engines": {
            "request_architect": "local", "portfolio_analyst": "maxis",
        },
        "runtime_last_role": "portfolio_analyst",
        "runtime_last_role_engine": "maxis",
        "evidence_workspace": {
            "sources": [{
                "sourceId": "src-1", "kind": "jira", "canonicalId": "jira:DL-1",
                "title": "DL-1", "content": "RAW MUST NOT LEAK",
                "providerTrace": "RAW TRACE", "url": "", "retrievedAt": "now",
            }],
            "claims": [{"kind": "fact", "text": "검증됨", "sourceIds": ["src-1"]}],
            "missingInputs": [], "warnings": [], "partial": False,
        },
        "reply": "done",
    })
    encoded = json.dumps(shaped, ensure_ascii=False)
    assert shaped["engine"] == "local"
    assert shaped["knowledgeDatasetId"] == DATASET_A
    assert shaped["knowledgeManifestSha256"] == MANIFEST_SHA
    assert shaped["roleEngines"]["portfolio_analyst"] == "maxis"
    assert shaped["lastRoleEngine"] == "maxis"
    assert shaped["evidenceWorkspace"]["sources"][0]["sourceId"] == "src-1"
    assert "RAW MUST NOT LEAK" not in encoded
    assert "RAW TRACE" not in encoded


@pytest.mark.parametrize("mutation", [
    "missing", "extra", "name", "text", "shape", "id", "time", "partial", "hash",
    "schema", "source", "scope", "query", "pseudonym", "flags",
])
def test_dev_query_context_is_checked_before_credentials_and_client(monkeypatch, mutation):
    from app.agent.maxis import credentials
    from app.agent.maxis import client as client_module
    from app.agent.maxis.contracts import canonical_payload_schema
    from app.agent.workflow.contracts import role_output_schema

    state = _runtime_state("query_specialist")
    state.update(runtime_profile="dev", request_text="PRIVATE_TEXT_MUST_NOT_CROSS")
    payload = backend.role_payload(
        "query_specialist", state, [], role_output_schema("query_specialist"),
        output_kind="structured",
    )
    pack = payload["evidencePack"]
    assert len(pack["role_context"]) == 1
    row = pack["role_context"][0]
    if mutation == "missing":
        pack["role_context"] = []
    elif mutation == "extra":
        pack["role_context"].append({**row, "name": "extra", "context_id": "extra"})
    elif mutation == "name":
        row["name"] = "foreign_context"
    elif mutation in {"text", "shape"}:
        value = {"text": "PRIVATE_TEXT_MUST_NOT_CROSS"}
        if mutation == "shape":
            value = {"text": json.loads(row["content_json"])["text"], "extra": True}
        row["content_json"] = backend._canonical_json(value)
        row["content_sha256"] = hashlib.sha256(row["content_json"].encode()).hexdigest()
    elif mutation == "id":
        row["context_id"] = "another-request"
    elif mutation == "time":
        row["observed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    elif mutation == "partial":
        row["partial"] = True
    elif mutation == "hash":
        row["content_sha256"] = "0" * 64
    elif mutation == "schema":
        pack["payload_schema_json"], pack["payload_schema_sha256"] = canonical_payload_schema(
            {"type": "object"})
    elif mutation == "source":
        pack["sources"][0]["content"] = "different synthetic source"
        pack["sources"][0]["content_sha256"] = hashlib.sha256(
            pack["sources"][0]["content"].encode()).hexdigest()
    elif mutation == "scope":
        pack["scope"]["projects"] = ["PRIVATE_PROJECT"]
    elif mutation == "query":
        pack["query"] = "PRIVATE_TEXT_MUST_NOT_CROSS"
    elif mutation == "pseudonym":
        pack["pseudonymous_user"] = "ltm_" + "0" * 16
    else:
        pack["flags"] = []

    monkeypatch.setattr(backend, "resolve_role", lambda *_a, **_k: backend.RoleResolution(
        route="maxis", binding=SimpleNamespace(), profile="dev"))
    monkeypatch.setattr(backend, "_profile_snapshot", lambda *_a, **_k: _profile("dev"))
    monkeypatch.setattr(credentials, "get_for", lambda *_a, **_k: pytest.fail(
        "invalid synthetic Query reached credentials"))
    monkeypatch.setattr(client_module, "DifyClient", lambda *_a, **_k: pytest.fail(
        "invalid synthetic Query reached client construction"))
    with pytest.raises(backend.MaxisRuntimeError, match="합성 fixture|계약 검증") as error:
        backend.invoke("query_specialist", payload, "thread-1", "corr-1")
    assert "PRIVATE_" not in str(error.value)
