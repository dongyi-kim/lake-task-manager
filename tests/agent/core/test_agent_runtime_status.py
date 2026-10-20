"""MAXIS feature readiness and bounded paths never borrow local model credentials."""
from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")

from app.agent import config, runtime_status
from app.agent.maxis import backend, profiles
from app.agent.workflow.role_manifest import ROLE_SPECS


def _pin():
    routes = {role: "local" for role in ROLE_SPECS}
    routes.update({role: "maxis" for role in runtime_status.CORE_ROLES})
    routes["editor_author"] = "maxis"
    return {"runtime_profile": "prod", "runtime_engine": "maxis",
            "runtime_role_routes": routes,
            "runtime_capability": runtime_status.INTERNAL_ASSIST_CAPABILITY}


@pytest.fixture
def no_local_models(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("The MAXIS-only capability touched a local model or embedding provider")
    monkeypatch.setattr(config, "get_llm", forbidden)
    monkeypatch.setattr(config, "get_embeddings", forbidden)
    return forbidden


def test_qualified_remote_features_do_not_consult_local_readiness(monkeypatch, no_local_models):
    pin = _pin()
    monkeypatch.setattr(config, "llm_ready", no_local_models)
    monkeypatch.setattr(profiles, "active_profile_name", lambda: "prod")
    monkeypatch.setattr(backend, "_canonical_role_routes", lambda: pin["runtime_role_routes"])
    monkeypatch.setattr(backend, "thread_runtime_pin", lambda: pin)
    monkeypatch.setattr(backend, "resolve_role", lambda *_: backend.RoleResolution(route="maxis"))
    status = runtime_status.status()
    assert status["conversation"] == {
        "engine": "maxis", "profile": "prod",
        "profileLabel": profiles.get("prod").get("display_name") or "MAXIS 연결", "ready": True,
        "reasonCode": "", "reason": "", "nextAction": ""}
    assert status["editor"]["ready"] is True


@pytest.mark.parametrize("profile", ["dev", "maxis-11111111-1111-4111-8111-111111111111"])
def test_bounded_no_local_capability_uses_routes_not_profile_name(profile):
    pin = {**_pin(), "runtime_profile": profile}
    assert runtime_status.capability_for_pin(pin) == runtime_status.INTERNAL_ASSIST_CAPABILITY
    pin["runtime_role_routes"]["request_architect"] = "local"
    assert runtime_status.capability_for_pin(pin) == ""


def test_active_remote_candidate_does_not_make_a_local_editor_ready(monkeypatch):
    pin = _pin()
    pin["runtime_role_routes"]["editor_author"] = "local"
    monkeypatch.setattr(profiles, "active_profile_name", lambda: "prod")
    monkeypatch.setattr(backend, "resolve_role", lambda *_: backend.RoleResolution(route="local"))
    monkeypatch.setattr(config, "llm_ready", lambda: (False, "local connection missing"))
    row = runtime_status.resolve("editor", pin)
    assert row["ready"] is False and row["reasonCode"] == "local_llm_required"


@pytest.mark.parametrize("failure,code", [
    ("Signed external Knowledge eval is missing", "maxis_qualification_required"),
    ("MAXIS kill switch is enabled", "maxis_stopped"),
])
def test_runtime_authority_failure_is_shared_by_status_and_execution(monkeypatch, failure, code):
    def blocked(*_args):
        raise backend.MaxisRuntimeError(failure)
    monkeypatch.setattr(backend, "resolve_role", blocked)
    row = runtime_status.resolve("editor", _pin())
    assert row["ready"] is False and row["reasonCode"] == code
    with pytest.raises(runtime_status.RuntimeUnavailable):
        runtime_status.require("editor", _pin())


def _research_state():
    result = {"tickets": [{"key": "DL-9001", "summary": "실제 조회한 작업",
                            "status": "Open", "description": "검토 요청 작업"}],
              "ticketDetails": [], "total": 1, "complete": True,
              "scopeProjects": ["DL"]}
    row = {"id": "internal-1", "source": "jira", "result": result,
           "_remoteEvidence": backend.attest_query_result(
               "internal-1", "jira", result, scopes=("DL",))}
    return {**_pin(), "intent": "ask", "request_text": "DL-9001 작업을 알려줘",
            "mentioned_keys": ["DL-9001"], "messages": [],
            "query_plan": {"queries": [{"id": "internal-1", "source": "jira"}]},
            "query_results": [row]}


def test_internal_research_projects_attested_facts_without_local_synthesis(no_local_models):
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    result = ResearchAnalyst().node()(_research_state())
    assert not result.get("error")
    assert any(row.get("key") == "DL-9001" for row in result["evidence"])
    assert "의미 판단은 MAXIS" in result["trace"][0]["note"]


def test_internal_research_refuses_missing_or_tampered_acquisition(no_local_models):
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    state = _research_state()
    state["query_results"][0]["result"]["tickets"][0]["summary"] = "tampered"
    assert ResearchAnalyst().node()(state).get("error")
    state["query_results"] = []
    assert ResearchAnalyst().node()(state).get("error")


def test_unsupported_research_stops_before_any_tool(no_local_models):
    from app.agent.workflow.agents.query_runner import QueryRunner
    state = {**_pin(), "query_plan": {"queries": [{"id": "web", "source": "web"}]}}
    with pytest.raises(runtime_status.RuntimeUnavailable, match="외부"):
        QueryRunner().node()(state)


def test_assignee_choice_and_explicit_unassignment_need_no_local_model(no_local_models):
    from app.agent.workflow.agents.people_advisor import PeopleAdvisor
    state = {**_pin(), "draft": {"items": [{"summary": "작업"}]}}
    result = PeopleAdvisor().node()(state)
    assert result["questions"] and not result["assignments"]
    state["draft"]["items"][0].update(assignee_source="user_unassigned", assignee="")
    result = PeopleAdvisor().node()(state)
    assert result["assignments"][0]["user"] == ""
    assert not result.get("questions")


def test_remote_authoring_does_not_load_local_rag(no_local_models):
    from app.agent.workflow.agents.auditor import _rules_for
    from app.agent.workflow.agents.work_architect import _rules_material, _bug_body_for
    assert _rules_for(_pin()) == ""
    assert _rules_material(_pin()) == ""
    assert _bug_body_for({**_pin(), "request_text": "오류가 발생함"}, {"summary": "버그"})


@pytest.mark.parametrize("kind", ["comment", "description"])
def test_remote_editor_uses_genuine_generation_without_local_rag_or_shortcut(
        monkeypatch, no_local_models, kind):
    from app.agent import editor_author
    monkeypatch.setattr(backend, "thread_runtime_pin", _pin)
    monkeypatch.setattr(backend, "resolve_role", lambda *_: backend.RoleResolution(route="maxis", profile="prod"))
    monkeypatch.setattr(editor_author, "_ticket_context", lambda *_: "")
    monkeypatch.setattr(editor_author, "_house_rules", no_local_models)
    monkeypatch.setattr(editor_author, "_deterministic_status_comment", no_local_models)
    seen = []

    def remote(role, state, messages, schema, *, output_kind):
        seen.append((role, messages))
        return backend.RemoteRoleOutput(
            {"mode": kind, "content_html": "<p>MAXIS 검토 요청 초안</p>",
             "source_ids": [], "unsupported_claims": [], "warnings": []}, {},
            {"profile": "prod", "usage": {}})
    monkeypatch.setattr(backend, "invoke_role", remote)
    result = editor_author.compose("__new__", kind, "검토 요청 초안을 작성해 줘")
    assert result["ok"] and seen == [("editor_author", [])]
    assert "MAXIS 검토 요청 초안" in result["html"]


@pytest.fixture
def bounded_graph(monkeypatch, tmp_path, no_local_models):
    """Real compiled graph/Role apply paths; only acquisition and remote transport are doubles."""
    from app.agent import approval
    from app.agent.workflow import graph, session
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.infra import settings

    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    pin = {**_pin(), "runtime_profile_fingerprint": "test-runtime-pin"}
    monkeypatch.setattr(backend, "prepare_thread_runtime", lambda *_a, **_k: dict(pin))
    monkeypatch.setattr(backend, "resolve_role", lambda role, state: backend.RoleResolution(
        route=(state.get("runtime_role_routes") or {}).get(role, "local"), profile="prod"))
    monkeypatch.setattr(session, "_identity", lambda: "integration user")
    controller = {"calls": [], "malformed": False, "disconnected": False}

    def acquire(_self, state):
        specs = (state.get("query_plan") or {}).get("queries") or []
        rows = []
        for spec in specs:
            assert spec["source"] == "jira"
            result = dict(_research_state()["query_results"][0]["result"])
            result["tickets"] = [dict(result["tickets"][0])]
            if controller["malformed"]:
                result["tickets"][0]["key"] = "INVALID-KEY"
            proof = backend.attest_query_result(spec["id"], "jira", result, scopes=("DL",))
            rows.append({"id": spec["id"], "source": "jira", "result": result,
                         "_remoteEvidence": proof})
        return {"query_results": rows, "query_artifacts": {}}

    def remote(role, state, messages, schema, *, output_kind):
        controller["calls"].append(role)
        if controller["disconnected"]:
            raise backend.MaxisRuntimeError("MAXIS 연결을 확인할 수 없습니다.")
        if role == "request_architect":
            payload = {"intent": "ask", "keywords": ["DL-9001"], "sufficient": True,
                       "request_questions": [], "requested_effects": [], "target_selectors": []}
        elif role == "query_specialist":
            payload = {"reads": [{"source": "jira", "subject": "DL-9001",
                                  "where": "key = DL-9001", "exhaustive": True}],
                       "uncertainty": []}
        elif role == "knowledge_curator":
            payload = {"concepts": [], "our_context": "DL-9001 실제 조회한 작업 상태 Open",
                       "references": [], "gaps": []}
        elif role == "result_integrator":
            assert state.get("evidence")
            payload = {"text": "DL-9001 실제 조회한 작업의 현재 상태는 Open입니다."}
        else:
            pytest.fail(f"Unexpected remote Role in read-only flow: {role}")
        return backend.RemoteRoleOutput(payload, {}, {"profile": "prod", "usage": {}})

    monkeypatch.setattr(QueryRunner, "_run", acquire)
    monkeypatch.setattr(backend, "invoke_role", remote)
    approval.clear()
    graph.reset()
    yield controller, session
    graph.reset()
    approval.clear()


def test_bounded_full_graph_reads_and_answers_without_a_local_provider(bounded_graph):
    controller, session = bounded_graph
    result = session.ask("DL-9001 제목과 상태를 확인해", thread_id="bounded-full-read")
    assert result["ok"] is True, result
    assert "실제 조회한 작업" in result["reply"]
    assert "request_architect" in controller["calls"]
    assert "query_specialist" in controller["calls"]
    assert controller["calls"][-1] == "result_integrator"
    assert not result.get("pending")


@pytest.mark.parametrize("failure", ["malformed", "disconnected"])
def test_bounded_full_graph_rejects_bad_sources_and_disconnect_without_local_fallback(
        bounded_graph, failure):
    controller, session = bounded_graph
    controller[failure] = True
    result = session.ask("DL-9001 제목과 상태를 확인해", thread_id="bounded-failure-" + failure)
    assert result["ok"] is False
    assert not result.get("pending")
    assert "result_integrator" not in controller["calls"]
