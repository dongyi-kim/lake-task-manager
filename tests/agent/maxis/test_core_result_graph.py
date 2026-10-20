"""One Result semantic author, actual guards and ordinary consumer; no provider."""
import copy
import importlib.util
import json
from pathlib import Path
import socket

import pytest
import yaml

from app.agent.maxis import ContextGuard, ContextPolicy, ResponseGuard, ResponseGuardError
from app.agent.maxis import backend
from app.agent.workflow.agents.base import Agent
from app.agent.workflow.agents.result_integrator import ResultIntegrator
from test_core_request_graph import code
from test_core_control_route import factory_input

ROOT = Path(__file__).resolve().parents[3]
LLM, ASSEMBLER, INPUT = "1909000000001040", "1909000000001041", "1909000000001042"
VERSION, PROOF = "11111111-1111-4111-8111-111111111111", "LTM_RELEASE_SENTINEL_V1=" + "a" * 64


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@pytest.fixture(scope="module")
def nodes():
    dsl = yaml.safe_load((ROOT / "deploy/maxis-dify/dsl/ltm-agent-core.yml").read_text(encoding="utf-8"))
    return {row["id"]: row["data"] for row in dsl["workflow"]["graph"]["nodes"]}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs): raise AssertionError("No model or network")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(Agent, "llm", deny)
    monkeypatch.setattr(backend, "invoke_role", deny)
    monkeypatch.setattr(backend, "resolve_role", lambda *args, **kwargs: backend.RoleResolution(route="maxis"))


@pytest.fixture
def result_case(context_guard, now, nodes):
    context_guard = ContextGuard(ContextPolicy(allowed_scopes={"jira": ("DL",), "confluence": ("DATALAKE",)},
        allowed_context_keys=frozenset({"situation"})), b"test-pseudonym-key-32-bytes-long")
    pack = context_guard.build(request_id="result-native", use_case="result_integrator", query="기록과 미확인 사항 정리",
        payload_schema=backend._TEXT_PAYLOAD_SCHEMA, knowledge_manifest_sha256="a" * 64,
        user_identity="synthetic", requested_at=now, sources=[
            {"source_id": "record-a", "kind": "jira", "canonical_id": "DL-9044", "title": "배치 기록", "scope": "DL",
                "content": "배치 주기를 2시간에서 30분으로 변경", "retrieved_at": now},
            {"source_id": "record-b", "kind": "confluence", "canonical_id": "confluence:123", "title": "관련 문서",
                "scope": "DATALAKE", "content": "확인 기준", "retrieved_at": now, "partial": True}],
        role_context=[{"context_id": "context-a", "name": "situation", "value": {"note": "조회된 기록을 요약하는 단계"}, "observed_at": now}],
        facts=[{"fact_id": "fact-a", "text": "배치 주기를 2시간에서 30분으로 변경", "source_ids": ["record-a"], "observed_at": now}],
        metrics=[{"metric_id": "interval", "label": "기록된 주기", "value": 30, "unit": "분", "formula_version": "source-record.v1", "source_ids": ["record-a"]}])
    guard = code(nodes["1909000000001006"])
    name = guard["_ALLOWED_DOCUMENT_NAMES_BY_ROLE"]["result_integrator"][0]
    policy = {"content": "Synthetic Result policy.", "metadata": {"dataset_id": guard["_DATASET_ID"],
        "document_id": guard["_DOCUMENT_IDS_BY_NAME"][name], "document_name": name}}
    sentinel = {"content": PROOF, "metadata": {"dataset_id": guard["_SENTINEL_DATASET_ID"],
        "document_id": guard["_SENTINEL_DOCUMENT_ID"], "document_name": guard["_SENTINEL_DOCUMENT_NAME"]}}
    incoming = guard["main"]([policy], [sentinel], encoded(pack.model_dump(mode="json")), pack.query,
        "result_integrator", pack.request_id, "remote-role-result.v1", VERSION, VERSION, "kb-test")["model_input"]
    draft = {"contract": "result-draft.v1", "status": "ok", "segments": [{"value": "배치 주기를 2시간에서 30분으로 변경",
        "kind": "fact", "source_ids": ["record-a"]}], "missing_inputs": []}
    return pack, incoming, draft


def both(nodes, pack, result):
    remote = json.loads(code(nodes["1909000000001005"])["main"](encoded(result), encoded(pack.model_dump(mode="json")),
        "result_integrator", pack.request_id, "remote-role-result.v1", VERSION, VERSION, "kb-test", PROOF)["result"])
    def local():
        return ResponseGuard().validate_remote_role(encoded(result), expected_role_id="result_integrator",
            payload_model=backend._TEXT_PAYLOAD_SCHEMA, evidence=pack, expected_knowledge_manifest_sha256="a" * 64)
    return remote, local


def test_result_is_enrolled_with_exact_original_input_selectors(nodes, result_case):
    _, incoming, _ = result_case
    assert code(nodes["1909000000001024"])["main"](incoming) == {"route": "result"}
    assert nodes[INPUT]["variables"] == [{"variable": "model_input", "value_selector": ["1909000000001006", "model_input"]}]
    assert nodes[ASSEMBLER]["variables"] == nodes[INPUT]["variables"] + [{"variable": "candidate", "value_selector": [LLM, "text"]}]
    assert [ASSEMBLER, "candidate"] in nodes["1909000000001027"]["variables"]
    assert nodes[LLM]["model"] == nodes["1909000000001004"]["model"]
    assert nodes[LLM]["memory"] is None


def test_projection_preserves_all_admitted_business_context_without_metadata(nodes, result_case):
    _, incoming, _ = result_case
    original = json.loads(incoming)
    view = json.loads(code(nodes[INPUT])["main"](incoming)["result_input"])
    expected = copy.deepcopy(original["context_pack"])
    for key in ("contract", "payload_schema_json", "payload_schema_sha256", "knowledge_manifest_sha256"): del expected[key]
    for key in ("sources", "role_context"):
        for row in expected[key]: row.pop("content_sha256")
    assert view == {"contract": "result-input.v1", "context": expected, "knowledge_context": original["knowledge_context"]}
    assert len(view["context"]["sources"]) == 2 and view["context"]["sources"][1]["partial"] is True
    assert PROOF not in encoded(view) and VERSION not in encoded(view)


@pytest.mark.parametrize("fault", ["role", "use_case", "schema", "digest", "unbound"])
def test_result_schema_disagreement_is_denied_not_generic_fallback(nodes, result_case, fault):
    _, incoming, _ = result_case
    value, route = json.loads(incoming), code(nodes["1909000000001024"])
    if fault == "role": value["role_id"] = "auditor"
    elif fault == "use_case": value["context_pack"]["use_case"] = "auditor"
    elif fault == "schema": value["context_pack"]["payload_schema_json"] = "{}"
    elif fault == "digest": value["context_pack"]["payload_schema_sha256"] = "a" * 64
    else: route["_TEXT_SCHEMA_JSON"] = None
    with pytest.raises(ValueError, match="invalid_core_control_input"): route["main"](encoded(value))


def test_reserved_control_still_precedes_result(nodes):
    assert code(nodes["1909000000001024"])["main"](encoded(factory_input())) == {"route": "control"}


@pytest.mark.parametrize("kind", ["fact", "inference", "recommendation", "open_fact"])
def test_one_value_authors_payload_claim_and_order_without_relabel(nodes, result_case, kind):
    pack, incoming, draft = result_case
    draft["segments"][0]["kind"] = kind
    draft["segments"] *= 2
    result = json.loads(code(nodes[ASSEMBLER])["main"](incoming, encoded(draft))["candidate"])
    assert result["payload"]["text"] == "\n\n".join(row["value"] for row in draft["segments"])
    assert [r["kind"] for r in result["claims"]] == [kind, kind]
    assert result["payload_claims"] == [{"path": "/text", "claim_ids": ["claim-1", "claim-2"]}]
    remote, local = both(nodes, pack, result)
    assert remote == result and local()[0].status == "ok"


def test_lexical_paraphrase_stays_blocked_after_mechanical_equality(nodes, result_case):
    pack, incoming, draft = result_case
    draft["segments"][0]["value"] = "DL-9044에서 배치 주기를 2시간에서 30분으로 변경한 기록이 확인되었습니다."
    result = json.loads(code(nodes[ASSEMBLER])["main"](incoming, encoded(draft))["candidate"])
    assert result["payload"]["text"] == result["claims"][0]["text"]
    remote, local = both(nodes, pack, result)
    assert remote["warnings"] == ["contract_guard:claim_not_grounded"]
    with pytest.raises(ResponseGuardError, match="citation_assertion_mismatch"): local()


@pytest.mark.parametrize("fault", ["prefix", "missing_binding"])
def test_independent_payload_is_red_while_same_value_assembly_is_green(nodes, result_case, fault):
    pack, incoming, draft = result_case
    result = json.loads(code(nodes[ASSEMBLER])["main"](incoming, encoded(draft))["candidate"])
    assert both(nodes, pack, result)[0] == result
    if fault == "prefix": result["payload"]["text"] = "기록: " + result["payload"]["text"]
    else: result["payload_claims"] = []
    reason = "payload_claim_text_mismatch" if fault == "prefix" else "payload_claim_coverage_missing"
    remote, local = both(nodes, pack, result)
    assert remote["warnings"] == ["contract_guard:" + reason]
    with pytest.raises(ResponseGuardError, match=reason): local()


@pytest.mark.parametrize("fault", ["count", "length", "surrogate", "unknown_source", "duplicate_key"])
def test_bounds_and_malformed_drafts_are_rejected_without_repair(nodes, result_case, fault):
    _, incoming, draft = result_case
    if fault == "count": draft["segments"] *= 41
    elif fault == "length": draft["segments"][0]["value"] = "x" * 1201
    elif fault == "surrogate": draft["segments"][0]["value"] = "PLACEHOLDER"
    elif fault == "unknown_source": draft["segments"][0]["source_ids"] = ["DL-9044"]
    raw = encoded(draft)
    if fault == "surrogate": raw = raw.replace("PLACEHOLDER", "\\ud800")
    elif fault == "duplicate_key": raw = raw.replace('"status":"ok"', '"status":"ok","status":"ok"')
    with pytest.raises(ValueError, match="invalid_core_result_draft"): code(nodes[ASSEMBLER])["main"](incoming, raw)


def test_actual_guarded_ordinary_apply_preserves_sidecar_and_uses_no_bypass(nodes, result_case, monkeypatch):
    pack, incoming, draft = result_case
    draft["segments"] = [{"value": "확인 범위 검토 필요", "kind": "recommendation", "source_ids": ["record-a", "record-b"]}]
    result = json.loads(code(nodes[ASSEMBLER])["main"](incoming, encoded(draft))["candidate"])
    remote, validate = both(nodes, pack, result)
    assert remote == result
    envelope, payload = validate()
    sidecar = backend._remote_state_patch(envelope, pack)
    before = copy.deepcopy(sidecar)
    state = {"messages": [], "request_text": "확인 범위를 정리", "trace": [], "thread_id": "result-native-test",
        "runtime_role_routes": {"result_integrator": "maxis"}}
    patch = ResultIntegrator().apply_routed(state, backend.RemoteRoleOutput(payload, sidecar))
    assert sidecar == before and patch["evidence_workspace"] == sidecar["evidence_workspace"]
    assert patch["runtime_last_role_engine"] == "maxis" and patch["reply"] == patch["messages"][0].content
    assert "확인 범위 검토 필요" in patch["reply"]
    assert not any(key.startswith(("_deterministic", "_typed_terminal")) for key in state)
    from app.agent.workflow.session import _shape
    public = _shape(state["thread_id"], {**state, **patch})
    assert public["reply"] == patch["reply"] and not public.get("pending")
    assert PROOF not in public["reply"] and VERSION not in public["reply"]
    assert not patch.get("approval_token") and not patch.get("execution_receipt")


@pytest.mark.parametrize("status,missing", [("needs_input", ["요청 범위를 확인해 주세요"]), ("blocked", [])])
def test_non_ok_is_safe_empty_output_not_a_fabricated_fact_or_terminal_receipt(nodes, result_case, status, missing):
    pack, incoming, draft = result_case
    draft.update(status=status, segments=[], missing_inputs=missing)
    result = json.loads(code(nodes[ASSEMBLER])["main"](incoming, encoded(draft))["candidate"])
    remote, local = both(nodes, pack, result)
    assert remote == result and local()[0].status == status
    assert result["payload"] == {} and result["claims"] == result["citations"] == result["payload_claims"] == []
    assert result["missing_inputs"] == missing and "execution_receipt" not in result


@pytest.mark.parametrize("count,length", [(40, 3), (1, 1200)])
def test_existing_maximum_claim_count_and_length_are_preserved(nodes, result_case, count, length):
    pack, incoming, draft = result_case
    draft["segments"] = [{"value": "검" * length, "kind": "recommendation", "source_ids": ["record-a"]}] * count
    result = json.loads(code(nodes[ASSEMBLER])["main"](incoming, encoded(draft))["candidate"])
    assert len(result["claims"]) == count and len(result["claims"][0]["text"]) == length
    assert result["payload"]["text"] == "\n\n".join(row["value"] for row in draft["segments"])
    remote, local = both(nodes, pack, result)
    assert remote == result and local()[0].status == "ok"


def test_oversize_draft_is_not_truncated_to_an_ok_answer(nodes, result_case):
    _, incoming, draft = result_case
    draft["segments"] = [{"value": "검" * 1200, "kind": "recommendation", "source_ids": ["record-a"]}] * 10
    assert len(encoded(draft).encode("utf-8")) > 32768
    with pytest.raises(ValueError, match="invalid_core_result_draft"):
        code(nodes[ASSEMBLER])["main"](incoming, encoded(draft))


@pytest.mark.parametrize("fault", ["hash", "correlation", "bytes", "extra", "noncanonical"])
def test_input_projection_refuses_invalid_original_input_without_repair(nodes, result_case, fault):
    _, incoming, _ = result_case
    value = json.loads(incoming)
    if fault == "hash": value["context_pack"]["sources"][0]["content"] += " unbound"
    elif fault == "correlation": value["correlation_id"] = "another-request"
    elif fault == "bytes": value["knowledge_context"][0]["content"] = "검" * 40001
    elif fault == "extra": value["raw_messages"] = []
    raw = encoded(value) + (" " if fault == "noncanonical" else "")
    with pytest.raises(ValueError, match="invalid_core_result_input"): code(nodes[INPUT])["main"](raw)


def test_canonical_role_semantics_are_reused_but_local_only_inputs_are_not_claimed(nodes):
    path = ROOT / "deploy/maxis-dify/core_result_contract.py"
    spec = importlib.util.spec_from_file_location("result_contract_test", path)
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    local = (ROOT / "app/agent/prompts/roles/result_integrator.md").read_text(encoding="utf-8")
    adapter = (ROOT / "deploy/maxis-dify/prompts/core_result.md").read_text(encoding="utf-8")
    system = contract.build_result_system_prompt(local, adapter)
    assert system == nodes[LLM]["prompt_template"][0]["text"] and len(system.encode("utf-8")) <= 8192
    assert "## Inputs" not in system and "## Preflight Check" not in system
    for title in ("Purpose", "Response Construction", "Output Contract", "Ticket Token Selection", "Stop and Escalate"):
        section = local.split("## " + title + "\n", 1)[1].split("\n## ", 1)[0].strip()
        assert section in system
    with pytest.raises(ValueError, match="requires review"):
        contract.build_result_system_prompt(local + "\n## New input assumption\n", adapter)
    with pytest.raises(ValueError, match="requires review"):
        contract.build_result_system_prompt(local, "x" * 8193)
    changed = copy.deepcopy(backend._TEXT_PAYLOAD_SCHEMA)
    changed["properties"]["text"]["maxLength"] = 1000
    with pytest.raises(ValueError, match="unsupported_result_payload_schema"):
        contract.build_draft_schema(changed)
