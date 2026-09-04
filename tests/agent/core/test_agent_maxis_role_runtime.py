from __future__ import annotations

import hashlib
import json
import time
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
            "agent_core": {"binding": "agent_core", "version": "wf-core-1"},
            "pmo_brief": {"binding": "pmo_brief", "version": "wf-pmo-1"},
            "editor_author": {"binding": "editor_author", "version": "wf-editor-1"},
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
    assert [row.name for row in pack.role_context] == ["query_plan"]
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
    assert pack.role_context == ()
    for forbidden in (
        "REAL_INTERNAL_PROMPT_MUST_NOT_CROSS", "REAL_ROLE_CONTEXT_MUST_NOT_CROSS",
        "REAL_TICKET_MUST_NOT_CROSS", "real.user@example.test",
    ):
        assert forbidden not in encoded


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


def test_interview_continuation_refreshes_role_context_observation_time():
    from app.agent.workflow.session import _turn_start_patch

    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    prior = _runtime_state("knowledge_curator")
    prior.update({
        "correlation_id": "corr-kept",
        "correlation_started_at": old,
        "situation": "직전 질문까지 정리한 상황",
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
    assert pack.freshness == "fresh"


def test_stale_or_partial_work_evidence_is_blocked_before_network(monkeypatch):
    state = _runtime_state("work_architect")
    result = {"scopeProjects": ["DL"], "tickets": [{"key": "DL-1"}]}
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    state["query_results"] = [{
        "id": "jira-1", "source": "jira", "result": result,
        "_remoteEvidence": backend.attest_query_result(
            "jira-1", "jira", result, retrieved_at=old,
        ),
    }]
    payload = backend.role_payload(
        "work_architect", state, [], {"type": "object"},
        output_kind="structured",
    )
    assert payload["evidencePack"]["freshness"] == "stale"
    monkeypatch.setattr(
        backend, "resolve_role",
        lambda *_a, **_k: backend.RoleResolution(
            route="maxis", binding=SimpleNamespace(), profile="prod"),
    )
    monkeypatch.setattr(backend, "_profile_snapshot", lambda *_a, **_k: _profile("prod"))
    with pytest.raises(backend.MaxisRuntimeError, match="freshness"):
        backend.invoke("work_architect", payload, "thread-1", "corr-1")


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
        assert payload["evidencePack"]["freshness"] == "stale"
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
    state = _runtime_state("request_architect")
    runtime_route = {"value": "maxis"}
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {"value": {"type": "string"}}, "required": ["value"],
    }
    payload = backend.role_payload(
        "request_architect", state, [], schema, output_kind="structured")
    binding = SimpleNamespace(
        output_contract="remote-role-result.v1", max_response_bytes=1048576,
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
            answer = {
                "contract": "remote-role-result.v1",
                "role_id": "request_architect",
                "status": status,
                "payload": ({"value": observed["remote_value"]}
                            if status == "ok" else {}),
                "claims": ([{
                    "claim_id": "value_claim",
                    "kind": "open_fact",
                    "text": observed["remote_value"],
                    "citation_ids": [],
                }] if status == "ok" else []),
                "payload_claims": ([{
                    "path": "/value", "claim_ids": ["value_claim"],
                }] if status == "ok" else []),
                "citations": [],
                "missing_inputs": (["완료 기준을 알려 주세요"]
                                   if status == "needs_input" else []),
                "warnings": [],
                "workflow_version": "wf-core-1",
                "knowledge_version": "knowledge-1",
                "knowledge_manifest_sha256": observed["remote_manifest"],
                "knowledge_proof": observed["knowledge_proof"],
            }
            return SimpleNamespace(
                answer=json.dumps(answer, ensure_ascii=False), message_id="message-1",
                latency_ms=12,
                retrieval=SimpleNamespace(
                    dataset_verified=True,
                    resource_count=2,
                    scored_resource_count=2,
                    minimum_score=0.4,
                    maximum_score=0.9,
                ),
            )

    monkeypatch.setattr(client_module, "DifyClient", FakeClient)
    result = backend.invoke("request_architect", payload, "thread-1", "corr-1")

    assert isinstance(observed["inputs"]["context_pack"], EvidencePackV1)
    assert set(observed["inputs"]) == {
        "context_pack", "role_id", "correlation_id", "output_contract",
        "workflow_version", "knowledge_version",
    }
    assert observed["user"] == observed["inputs"]["context_pack"].pseudonymous_user
    assert observed["workflow_id"] == "wf-core-1"
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
    assert all(event.correlation_id == "corr-1" for event in audit_events)
    assert all(not hasattr(event, "prompt") and not hasattr(event, "answer")
               for event in audit_events)

    observed["knowledge_proof"] = "LTM_RELEASE_SENTINEL_V1=drifted"
    with pytest.raises(backend.MaxisRuntimeError, match="Knowledge release"):
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert audit_events[-1].error_code == "knowledge_sentinel_mismatch"

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
    with pytest.raises(backend.MaxisRuntimeError, match="적용 가능한 결과"):
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert [event.status for event in audit_events[-2:]] == ["started", "blocked"]
    assert audit_events[-1].error_code == "remote_status_blocked"

    observed["remote_status"] = "needs_input"
    with pytest.raises(backend.MaxisNeedsInput) as needs:
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert needs.value.missing_inputs == ("완료 기준을 알려 주세요",)
    assert needs.value.state_patch["quality_gate"]["ok"] is False
    assert [event.status for event in audit_events[-2:]] == ["started", "succeeded"]

    observed.update(remote_status="ok", remote_value="[[person:0123456789ab]]")
    with pytest.raises(backend.MaxisRuntimeError, match="person reference"):
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert audit_events[-1].error_code == "person_reference_unresolved"

    observed.update(remote_value="remote", remote_manifest="b" * 64)
    with pytest.raises(backend.MaxisRuntimeError, match="응답 계약"):
        backend.invoke("request_architect", payload, "thread-1", "corr-1")
    assert audit_events[-1].status == "blocked"
    assert audit_events[-1].error_code == "response_knowledge_manifest_mismatch"


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
