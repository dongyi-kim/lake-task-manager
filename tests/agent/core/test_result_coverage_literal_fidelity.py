"""Coverage disclosure changes are required, but unrelated literals are not owned."""
import pytest

from app.agent.workflow.agents import result_integrator as result

HEADING = "### 요청 출처 조사 한계"
MISSING = {"intent": "ask", "request_text": "Confluence wiki를 조사해줘",
           "query_plan": {"queries": [{"id": "wiki", "source": "confluence"}]},
           "query_results": []}
COVERED = {**MISSING, "query_results": [{"id": "wiki", "source": "confluence", "result": {
    "documents": [{"title": "문서", "url": "/spaces/ACME/pages/1"}], "returned": 1}}]}


@pytest.mark.parametrize("state", [{}, COVERED])
@pytest.mark.parametrize("value", ["", "  \r\n\r\n", "  요약  유지.\n\n\n",
    "   ~~~text\r\n    a(  1  )\r\n\r\n\r\n   ~~~\r\n"])
def test_coverage_noop_returns_exact_bytes(state, value):
    assert result._ensure_requested_source_coverage(value, state) == value


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("wrapper", ["fence", "open_fence", "inline", "indent"])
@pytest.mark.parametrize("state", [{}, COVERED, MISSING])
@pytest.mark.parametrize("prefix", ["", "요약"])
def test_literal_generated_heading_and_anchor_are_not_disclosures(newline, wrapper, state, prefix):
    rows = [HEADING, "- fake unavailable source", "", "", "### 근거", "[1] literal"]
    if wrapper == "fence":
        rows = ["   ~~~text", *rows, "   ~~~"]
    elif wrapper == "open_fence":
        rows = ["   ~~~text", *rows]
    elif wrapper == "inline":
        # Matched multiline inline code is bounded by blank lines; use nonblank rows here.
        rows = ["prefix `sample", HEADING, "### 근거", "sample`"]
    else:
        rows = ["    " + row if row else "    " for row in rows]
    literal = newline.join(rows) + newline
    original = (prefix + newline * 2 if prefix else "") + literal
    got = result._ensure_requested_source_coverage(original, state)
    assert literal in got
    if state is MISSING:
        prose = "".join(span for is_literal, span in result._reply_literal_spans(got) if not is_literal)
        assert HEADING in prose and "결론 근거에 사용하지 않음" in prose
        assert result._ensure_requested_source_coverage(got, state) == got
        assert result._ensure_requested_source_coverage(got, COVERED) == original
    else:
        assert got == original


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_missing_coverage_uses_real_anchor_and_preserves_literal(newline):
    literal = newline.join(["   ~~~text", "### 근거", "    keep(  1  )", "", "", "   ~~~"]) + newline
    raw = "요약" + newline + literal + newline + "### 근거" + newline + "[1] 실제 자료"
    got = result._ensure_requested_source_coverage(raw, MISSING)
    assert literal in got
    assert got.index(HEADING) > got.index("   ~~~", got.index("   ~~~") + 1)
    assert got.index(HEADING) < got.rindex("### 근거")
    assert result._ensure_requested_source_coverage(got, MISSING) == got


@pytest.mark.parametrize("raw", ["", "요약", "### 근거\n[1] 실제 자료"])
def test_missing_then_covered_removes_only_real_generated_disclosure(raw):
    once = result._ensure_requested_source_coverage(raw, MISSING)
    assert HEADING in once and "결론 근거에 사용하지 않음" in once
    assert result._ensure_requested_source_coverage(once, MISSING) == once
    assert result._ensure_requested_source_coverage(once, COVERED) == raw


def test_stale_disclosure_regenerated_from_current_failure_state():
    old = "요약\n\n" + HEADING + "\n\n- stale disclosure\n\n### 근거\n[1] 실제 자료"
    got = result._ensure_requested_source_coverage(old, MISSING)
    assert "stale disclosure" not in got
    assert got.count(HEADING) == 1 and "Confluence" in got
    assert "결론 근거에 사용하지 않음" in got
    assert got.endswith("### 근거\n[1] 실제 자료")


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("literal", [
    "~~~text\n검토합니다.\n검토합니다.\n~~~", "~~~text\n검토합니다.\n검토합니다.",
    "    검토합니다.\n    검토합니다.", "`검토합니다.`",
])
def test_resolved_coverage_retains_literal_through_ordinary_apply(newline, literal):
    from langchain_core.messages import HumanMessage

    literal = literal.replace("\n", newline)
    original = "요약" + newline * 2 + literal
    missing = result._ensure_requested_source_coverage(original, MISSING)
    state = {**COVERED, "messages": [HumanMessage(content=COVERED["request_text"])]}
    reply = result.ResultIntegrator().apply_routed(state, {"text": missing})["reply"]
    assert literal in reply
    assert HEADING not in reply
    assert result._ensure_requested_source_coverage(missing, COVERED) == original


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("heading", ["# 원문", "## 원문", "### 원문", "#### 원문",
    "##### 원문", "###### 원문", "**원문**", "### `원문`", "**`원문`**",
    "### 원문 ``code``", " ### 원문", "  ### 원문", "   ### 원문",
    "### \\`escaped", "### `unmatched"])
@pytest.mark.parametrize("prefix", ["", "요약\n\n"])
@pytest.mark.parametrize("literal", [
    "  ~~~~text\n검토합니다.\n~~~\n검토합니다.\n\n\n  ~~~~~",
    "  ````text\n검토합니다.\n```\n검토합니다.\n  ````",
    "  ~~~text\n검토합니다.\n검토합니다.",
    "    검토합니다.\n    검토합니다.", "`검토합니다.`",
])
def test_coverage_keeps_heading_attached_to_trailing_literal(newline, heading, prefix, literal):
    from langchain_core.messages import HumanMessage

    literal = literal.replace("\n", newline)
    original = prefix.replace("\n", newline) + heading + newline * 2 + literal
    missing = result._ensure_requested_source_coverage(original, MISSING)
    assert heading + newline * 2 + literal in missing
    assert result._ensure_requested_source_coverage(missing, MISSING) == missing
    assert result._ensure_requested_source_coverage(missing, COVERED) == original
    prose = "".join(part for opaque, part in result._reply_literal_spans(missing) if not opaque)
    assert HEADING in prose and "결론 근거에 사용하지 않음" in prose
    state = {**COVERED, "messages": [HumanMessage(content=COVERED["request_text"])]}
    reply = result.ResultIntegrator().apply_routed(state, {"text": missing})["reply"]
    assert heading.strip() in reply and literal in reply and HEADING not in reply


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("closed", [False, True])
@pytest.mark.parametrize("heading", ["### 원문", "### `원문`", "**`원문`**", "  ### 원문",
    "prefix `open\n### code`", "prefix ``open\n### `first` and ``second``"])
def test_heading_literal_crosses_real_guards_then_ordinary_apply(newline, closed, heading, monkeypatch):
    import copy
    import importlib.util
    import json
    from datetime import datetime, timezone
    from pathlib import Path
    from langchain_core.messages import HumanMessage
    from app.agent.maxis import backend
    from app.agent.maxis.context_guard import ContextGuard, ContextPolicy
    from app.agent.maxis.response_guard import ResponseGuard

    literal = newline.join(["  ~~~~text", "검토합니다.", "~~~", "검토합니다.", "", ""])
    literal += "  ~~~~~" if closed else "검토합니다."
    heading = heading.replace("\n", newline)
    original = heading + newline * 2 + literal
    text = result._ensure_requested_source_coverage(original, MISSING)
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    version, proof = "11111111-1111-4111-8111-111111111111", "LTM_RELEASE_SENTINEL_V1=" + "a" * 64
    pack = ContextGuard(ContextPolicy(allowed_scopes={}), b"synthetic-result-heading-key-32b").build(
        request_id="heading-boundary", use_case="result_integrator", query="검토 요청",
        payload_schema=backend._TEXT_PAYLOAD_SCHEMA, knowledge_manifest_sha256="a" * 64,
        user_identity="synthetic-user", requested_at=now, sources=[{
            "source_id": "intent-a", "kind": "ltm", "canonical_id": "ltm:user-intent:heading",
            "title": "Request", "scope": "", "content": "검토 요청", "retrieved_at": now}])
    envelope = {"contract": "remote-role-result.v1", "role_id": "result_integrator", "status": "ok",
        "payload": {"text": text}, "claims": [{"claim_id": "claim-1", "kind": "recommendation",
            "text": text, "citation_ids": ["cite-1"]}],
        "payload_claims": [{"path": "/text", "claim_ids": ["claim-1"]}],
        "citations": [{"citation_id": "cite-1", "source_id": "intent-a", "locator": ""}],
        "missing_inputs": [], "warnings": [], "workflow_version": version,
        "knowledge_version": "kb-test", "knowledge_manifest_sha256": "a" * 64, "knowledge_proof": proof}
    raw = json.dumps(envelope, ensure_ascii=False)
    path = Path(result.__file__).resolve().parents[4] / "deploy/maxis-dify/code/contract_guard.py"
    spec = importlib.util.spec_from_file_location("heading_contract_guard", path)
    remote_guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(remote_guard)
    remote = remote_guard.main(raw, json.dumps(pack.model_dump(mode="json")), "result_integrator",
        pack.request_id, "remote-role-result.v1", version, version, "kb-test", [{"content": proof}])
    assert json.loads(remote["result"]) == envelope
    accepted, payload = ResponseGuard().validate_remote_role(raw, expected_role_id="result_integrator",
        payload_model=backend._TEXT_PAYLOAD_SCHEMA, evidence=pack, expected_knowledge_manifest_sha256="a" * 64)
    sidecar = backend._remote_state_patch(accepted, pack)
    before = copy.deepcopy((payload, sidecar))
    monkeypatch.setattr(backend, "resolve_role", lambda *a, **kw: backend.RoleResolution(route="maxis"))
    state = {**COVERED, "messages": [HumanMessage(content=COVERED["request_text"])]}
    output = result.ResultIntegrator().apply_routed(state, backend.RemoteRoleOutput(payload, sidecar))
    assert (payload, sidecar) == before and output["quality_gate"]["ok"] is True
    assert output["evidence_workspace"] == sidecar["evidence_workspace"]
    assert heading.strip() in output["reply"] and literal in output["reply"] and HEADING not in output["reply"]

