"""Network-free checks of the closed DEV Role component evaluation boundary."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.maxis.knowledge_eval import KnowledgeEvalError, RetrievalIdentityMap
from app.agent.maxis.credentials import get_for as resolve_workflow_key
from tools.maxis_role_remote_cases import load_remote_cases


ROOT = Path(__file__).resolve().parents[3]
DATASET = "11111111-1111-4111-8111-111111111111"
DOCUMENT = "22222222-2222-4222-8222-222222222222"
SENTINEL_DATASET = "33333333-3333-4333-8333-333333333333"
SENTINEL_DOCUMENT = "44444444-4444-4444-8444-444444444444"
PROOF = "LTM_RELEASE_SENTINEL_V1=synthetic-fixture"
MANIFEST = "a" * 64
CHUNK = "합성 평가 정책은 업무 초안만 작성하며 실제 시스템 변경 권한을 부여하지 않는다. " * 5


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy/maxis-dify" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "deploy/maxis-dify"))
    module = _load("role_component_runner_test", "run_role_evals.py")
    # run() prepares child-process metadata; don't leak it into unrelated tests.
    monkeypatch.setattr(module, "os", SimpleNamespace(environ=dict(os.environ)))
    monkeypatch.setattr(module.credentials, "get_for", lambda *a, **kw: pytest.fail("unmocked credential access"))
    monkeypatch.setattr(module, "make_client", lambda *a, **kw: pytest.fail("unmocked transport access"))
    return module


def _profile():
    return {"base_url": "https://dify.example.invalid/v1", "fingerprint": "b" * 32,
            "knowledge_version": "synthetic-knowledge-v1", "knowledge_dataset_id": DATASET,
            "knowledge_sentinel_sha256": hashlib.sha256(PROOF.encode()).hexdigest(),
            "workflow_bundle_version": "synthetic-bundle-v1",
            "workflows": {key: {"version": value} for key, value in (
                ("agent_core", "55555555-5555-4555-8555-555555555555"),
                ("pmo_brief", "66666666-6666-4666-8666-666666666666"),
                ("editor_author", "77777777-7777-4777-8777-777777777777"))}}


def _identity(role, *, allowed=True):
    return RetrievalIdentityMap(
        dataset_id=DATASET, sentinel_dataset_id=SENTINEL_DATASET,
        knowledge_version="synthetic-knowledge-v1", manifest_sha256=MANIFEST,
        document_ids_by_uuid={DOCUMENT: "synthetic-policy"},
        document_ids_by_name={"synthetic-policy.md": "synthetic-policy"},
        allowed_document_ids_by_role={role: frozenset({"synthetic-policy"} if allowed else ())},
        segment_sha256_by_document={}, canonical_text_by_document={"synthetic-policy": " ".join(CHUNK.split())},
        sentinel_uuid=SENTINEL_DOCUMENT, sentinel_name="synthetic-sentinel.md")


def _raw(role="request_architect", **overrides):
    answer = {"contract": "remote-role-result.v1", "role_id": role, "status": "needs_input",
              "payload": {}, "claims": [], "payload_claims": [], "citations": [],
              "missing_inputs": ["작업 범위를 확인해 주세요"], "warnings": [],
              "workflow_version": _profile()["workflows"]["agent_core"]["version"],
              "knowledge_version": "synthetic-knowledge-v1", "knowledge_manifest_sha256": MANIFEST,
              "knowledge_proof": PROOF}
    answer.update(overrides)
    return {"event": "message", "answer": json.dumps(answer, ensure_ascii=False), "metadata": {
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "retriever_resources": [{"dataset_id": DATASET, "document_id": DOCUMENT,
            "document_name": "synthetic-policy.md", "content": CHUNK}]}}


def _transport(runner, monkeypatch, *, raw=None, error=None):
    requests, key_reads = [], []

    class Client:
        closed = False
        successes = 0

        def _request_validated_chat_bytes(self, binding, **kwargs):
            requests.append((binding, kwargs))
            if error:
                raise error
            return json.dumps(raw or _raw()).encode(), 200, 1, 1, self

        def record_success(self):
            self.successes += 1

        def close(self):
            self.closed = True

    client = Client()
    monkeypatch.setattr(runner, "make_client", lambda *a: client)
    # Keep the real workflow-key validation; fake only secure storage/environment.
    monkeypatch.setattr(runner.credentials, "environment_overrides", lambda *a: {})
    monkeypatch.setattr(runner.credentials, "load_for", lambda *a: {
        key: "synthetic-test-key" for key in runner.credentials.WORKFLOW_KEYS})
    monkeypatch.setattr(runner.credentials, "get_for", lambda *a: key_reads.append(a) or resolve_workflow_key(*a))
    return client, requests, key_reads


def _attempt(runner, *, identity=None):
    case = runner.select_cases(["role-request"])[0]
    return runner.attempt(case, _profile(), {"knowledge_manifest_sha256": MANIFEST},
                          identity or _identity(case["role_id"]))


def test_direct_run_rejects_before_profiles_or_credentials(runner, monkeypatch):
    monkeypatch.delenv("LTM_EVAL_NETWORK_PREFLIGHTED", raising=False)
    monkeypatch.setattr(runner, "_preflight", lambda: pytest.fail("profile read before authorization"))
    with pytest.raises(KnowledgeEvalError, match="network_authorization_required"):
        runner.run("baseline")


@pytest.mark.parametrize("selected", [[], ["unknown"], ["role-query", "role-query"], "role-query", [None]])
def test_selection_is_closed_nonempty_unique(runner, selected):
    with pytest.raises(ValueError):
        runner.select_cases(selected)


def test_selection_returns_fixed_canonical_order(runner):
    assert [case["id"] for case in runner.select_cases(["role-query", "role-request"])] == ["role-request", "role-query"]
    assert len(runner.select_cases()) == 11


@pytest.mark.parametrize("argv", [[], ["--help"], ["--label", "baseline", "--unknown"],
    ["--label", "baseline", "--cases", "role-query", "role-query"],
    ["--label", "candidate", "--cases", "untrusted-input.json"]])
def test_launcher_rejects_or_help_before_network(runner, monkeypatch, argv):
    launcher = _load("role_component_launcher_test", "launch_role_evals.py")
    monkeypatch.setattr(launcher, "_probe_dev_endpoint", lambda *a, **kw: pytest.fail("network called"))
    with pytest.raises(SystemExit):
        launcher.main(argv)


def test_launcher_hands_off_only_successful_preflight_to_selected_child(runner, monkeypatch):
    launcher = _load("role_component_launcher_success_test", "launch_role_evals.py")
    captured = []
    monkeypatch.setattr(launcher, "credential_free_dev_api_base", lambda: _profile()["base_url"])
    monkeypatch.setattr(launcher, "_probe_dev_endpoint", lambda *a, **kw: "d" * 64)
    monkeypatch.setattr(launcher.subprocess, "run", lambda command, **kw: captured.append((command, kw)) or SimpleNamespace(returncode=3))
    assert launcher.main(["--label", "candidate", "--cases", "role-query"]) == 3
    command, options = captured[0]
    assert command[-4:] == ["--label", "candidate", "--cases", "role-query"]
    assert options["env"][launcher.NETWORK_PREFLIGHT_MARKER] == "1"
    assert options["env"][launcher.MAXIS_EVAL_DESTINATION_IDENTITY_ENV] == "d" * 64
    assert options["cwd"] == ROOT and options["check"] is False


def test_destination_change_rejects_before_profile_lookup(runner, monkeypatch):
    monkeypatch.setattr(runner, "_require_network_handoff", lambda: "0" * 64)
    monkeypatch.setattr(runner, "_credential_free_dev_api_base", lambda: _profile()["base_url"])
    monkeypatch.setattr(runner, "_preflight", lambda: pytest.fail("changed destination profile lookup"))
    with pytest.raises(KnowledgeEvalError, match="destination_changed"):
        runner.run("baseline", ["role-request"])


def test_stateless_request_has_only_bound_guarded_input(runner, monkeypatch):
    client, requests, keys = _transport(runner, monkeypatch)
    row = _attempt(runner)
    assert row["automaticContractPass"] is True and row["status"] == "needs_input"
    assert row["responseReceived"] and row["requestAttempted"] and client.closed
    assert row["usage"]["total_tokens"] == 15
    binding, outgoing = requests[0]
    assert keys == [("dev", "agent_core")]
    assert binding.conversation_mode == "stateless"
    assert outgoing["workflow_id"] == _profile()["workflows"][binding.key]["version"]
    assert outgoing["expected_knowledge_dataset_id"] == DATASET
    assert set(outgoing["inputs"]) == {"context_pack", "role_id", "correlation_id", "output_contract", "workflow_version", "knowledge_version"}
    pack = outgoing["inputs"]["context_pack"]
    assert pack.request_id == outgoing["inputs"]["correlation_id"]
    assert pack.query == outgoing["query"] and pack.pseudonymous_user == outgoing["user"]
    assert pack.knowledge_manifest_sha256 == MANIFEST
    encoded = json.dumps(pack.model_dump(mode="json"), ensure_ascii=False)
    for forbidden in ("specialized_review", "majorWhen", "benchmark_refs", "case_metadata_sha256", "local_builder_no_io"):
        assert forbidden not in encoded
    assert "knowledge_proof" not in row["response"] and PROOF not in json.dumps(row)


@pytest.mark.parametrize('case', load_remote_cases(), ids=lambda case: case['id'])
def test_every_role_uses_registered_workflow_credential(runner, monkeypatch, case):
    binding = runner.load_registry().resolve_role(case['role_id'])
    raw = _raw(role=case['role_id'], workflow_version=_profile()['workflows'][binding.key]['version'])
    client, requests, keys = _transport(runner, monkeypatch, raw=raw)
    row = runner.attempt(case, _profile(), {'knowledge_manifest_sha256': MANIFEST}, _identity(case['role_id']))
    assert keys == [('dev', binding.key)]
    assert len(requests) == 1 and row['responseReceived'] and client.closed
    assert row['contractOk'] and not row['errorCode']


def test_responseguard_failure_keeps_response_usage_and_closes(runner, monkeypatch):
    client, _, _ = _transport(runner, monkeypatch, raw=_raw(role_id="auditor"))
    row = _attempt(runner)
    assert row["status"] == "error" and row["errorCode"] == "role_mismatch"
    assert row["retrievalOk"] and not row["contractOk"]
    assert row["response"]["role_id"] == "auditor" and row["usage"]["total_tokens"] == 15
    assert row["responseReceived"] and client.closed
    assert row['failure']['stage'] == 'response_guard'
    assert row['failure']['httpStatus'] == 200
    assert row['failure']['nativeModelCallCount'] is None


@pytest.mark.parametrize('field,value', [('event', 'error'), ('answer', None), ('answer', 123)])
def test_malformed_envelope_retains_observed_usage(runner, monkeypatch, field, value):
    raw = _raw()
    raw[field] = value
    _transport(runner, monkeypatch, raw=raw)
    row = _attempt(runner)
    assert row['responseReceived'] and row['errorCode'] == 'invalid_response'
    assert row['usage']['total_tokens'] == 15 and not row['contractOk']


@pytest.mark.parametrize("overrides", [{"workflow_version": "wrong-workflow"},
    {"knowledge_version": "wrong-knowledge"}, {"knowledge_proof": "LTM_RELEASE_SENTINEL_V1=wrong"}])
def test_response_must_match_pinned_binding(runner, monkeypatch, overrides):
    _transport(runner, monkeypatch, raw=_raw(**overrides))
    row = _attempt(runner)
    assert row["errorCode"] == "response_binding_mismatch" and not row["contractOk"]
    assert row["retrievalOk"] and row["responseReceived"]


@pytest.mark.parametrize("mutation,expected", [("chunk", "retrieval_segment_hash_mismatch"),
    ("dataset", "retrieval_dataset_mismatch"), ("missing", "retrieval_identity_unavailable")])
def test_retrieval_integrity_failure_separate_from_valid_output(runner, monkeypatch, mutation, expected):
    raw = _raw()
    if mutation == "chunk":
        raw["metadata"]["retriever_resources"][0]["content"] = "not a manifest-bound policy chunk " * 10
    elif mutation == "dataset":
        raw["metadata"]["retriever_resources"][0]["dataset_id"] = SENTINEL_DOCUMENT
    else:
        raw["metadata"].pop("retriever_resources")
    _transport(runner, monkeypatch, raw=raw)
    row = _attempt(runner)
    assert row["contractOk"] and not row["retrievalOk"] and not row["automaticContractPass"]
    assert row["errorCode"] == "" and row["retrievalErrorCode"] == expected


def test_retrieved_document_requires_role_acl(runner, monkeypatch):
    _transport(runner, monkeypatch)
    row = _attempt(runner, identity=_identity("request_architect", allowed=False))
    assert row["retrieval"]["ledgerVerified"] and not row["retrieval"]["roleAclOk"]
    assert row["retrievalErrorCode"] == "retrieval_role_acl_mismatch"
    assert row["contractOk"] and not row["automaticContractPass"]


def test_transport_failure_closes_and_preserves_unknown_usage(runner, monkeypatch):
    client, requests, _ = _transport(runner, monkeypatch, error=KnowledgeEvalError("timeout"))
    row = _attempt(runner)
    assert row["errorCode"] == "timeout" and not row["responseReceived"]
    assert row["usage"] is None and row["requestAttempted"] and client.closed
    assert len(requests) == 1


@pytest.mark.parametrize('status', [400, 422, 401, 403])
def test_http_error_preserves_provenance_without_counting_a_model_call(runner, monkeypatch, status):
    from app.agent.maxis.errors import classify_http_status
    from app.agent.maxis.telemetry import request_observation
    error = classify_http_status(status)
    error.telemetry = request_observation(transport_status='failed', http_status=status, latency_ms=12)
    _transport(runner, monkeypatch, error=error)
    row = _attempt(runner)
    assert row['failure']['stage'] == 'http_response' and row['failure']['httpStatus'] == status
    assert row['failure']['requestIdSha256'] == hashlib.sha256(row['pack']['request_id'].encode()).hexdigest()
    assert row['failure']['nativeModelCallCount'] is None and row['nativeLlmCalls'] is None
    assert row['usage'] is None and not row['responseReceived'] and not row['automaticContractPass']
    measured, _ = runner.metrics([row])
    assert measured['calls'] == 0 and measured['nativeLlmCalls'] is None


def test_keyboard_interrupt_retains_attempt_and_closes(runner, monkeypatch):
    client, requests, _ = _transport(runner, monkeypatch, error=KeyboardInterrupt())
    row = _attempt(runner)
    assert row["errorCode"] == "interrupted" and row["requestAttempted"]
    assert not row["responseReceived"] and row["usage"] is None and client.closed
    assert len(requests) == 1


@pytest.mark.parametrize('failure', [None, KnowledgeEvalError('timeout')])
def test_request_latency_excludes_setup_and_validation(runner, monkeypatch, failure):
    _transport(runner, monkeypatch, error=failure)
    ticks = iter([0.0, 2.0, 5.0, 8.0])
    monkeypatch.setattr(runner, 'time', SimpleNamespace(perf_counter=lambda: next(ticks)))
    row = _attempt(runner)
    assert row['preparationMs'] == 2000
    assert row['latencyMs'] == 3000 and row['attemptElapsedMs'] == 8000


def test_failure_before_request_has_unknown_request_latency(runner, monkeypatch):
    monkeypatch.setattr(runner, 'build', lambda *a: (_ for _ in ()).throw(ValueError('synthetic build error')))
    row = _attempt(runner)
    assert row['latencyMs'] is None and row['requestAttempted'] is False
    assert row['failure']['stage'] == 'preparation'
    assert row['failure']['transportStatus'] == 'not_attempted'
    measured, _ = runner.metrics([row])
    assert measured['durationSeconds'] is None


def test_close_failure_does_not_discard_response_or_usage(runner, monkeypatch):
    client, _, _ = _transport(runner, monkeypatch)

    def failed_close():
        raise OSError("synthetic close failure")

    monkeypatch.setattr(client, "close", failed_close)
    row = _attempt(runner)
    assert row["closeFailed"] and row["errorCode"] == "client_close_failed"
    assert row["responseReceived"] and row["usage"]["total_tokens"] == 15
    assert row["response"]["status"] == "needs_input" and not row["automaticContractPass"]


def test_mixed_metrics_keep_missing_observations_unknown(runner):
    rows = [{"attemptId": str(index), "roleId": "request_architect", "status": status,
             "responseReceived": received, "latencyMs": latency, "usage": usage,
             "nativeLlmCalls": None, "costUsd": None}
            for index, (status, received, latency, usage) in enumerate([
                ("needs_input", True, 1000, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
                ("error", False, 2000, None)])]
    metrics, observations = runner.metrics(rows)
    assert metrics["attempts"] == 2 and metrics["calls"] == 1 and metrics["durationSeconds"] == 3
    assert metrics["totalTokens"] is None and metrics["costUsd"] is None
    assert observations["overall"]["tokens"]["total_tokens"]["observedTotal"] == 15
    assert observations["overall"]["nativeLlmCalls"]["total"] is None


def _run_setup(runner, monkeypatch, tmp_path):
    profile = _profile()
    endpoint = runner._destination_identity(profile["base_url"])
    monkeypatch.setattr(runner, "_require_network_handoff", lambda: endpoint)
    monkeypatch.setattr(runner, "_credential_free_dev_api_base", lambda: profile["base_url"])
    monkeypatch.setattr(runner, "_preflight", lambda: (profile, {"knowledge_manifest_sha256": MANIFEST}, None, None))
    monkeypatch.setattr(runner, "_binding_unchanged", lambda *a: True)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    config = tmp_path / "deploy/maxis-dify/environments/dev.yml"
    config.parent.mkdir(parents=True)
    config.write_text("model:\n  name: synthetic-test-model\n", encoding="utf-8")
    monkeypatch.setattr(runner, "source_binding", lambda: {key: "c" * 64 for key in runner.HARNESS_FILES})
    monkeypatch.setattr(runner, "build_run_metadata", lambda **kw: {
        "benchmarkKey": "benchmark", "candidateKey": "candidate", "qualificationIneligibilityReasons": []})


def test_run_stops_after_transport_failure_retains_attempt_and_pending(runner, monkeypatch, tmp_path):
    _run_setup(runner, monkeypatch, tmp_path)
    attempted = []

    def failed(case, *args):
        attempted.append(case["id"])
        return {"attemptId": case["id"], "caseId": case["id"], "roleId": case["role_id"],
                "status": "error", "errorCode": "timeout", "responseReceived": False,
                "contractOk": False, "retrievalOk": False, "latencyMs": 100, "usage": None}

    monkeypatch.setattr(runner, "attempt", failed)
    path = runner.run("baseline", ["role-request", "role-query"])
    result = json.loads(path.read_text(encoding="utf-8"))
    assert attempted == ["role-request"] and result["notAttemptedCaseIds"] == ["role-query"]
    assert not result["complete"] and result["stopReason"] == "timeout"
    assert result["cases"][0]["errorCode"] == "timeout" and result["metrics"]["attempts"] == 1
    assert not result["releaseEligible"] and not result["semanticCorrectnessAssessed"]


def test_interruption_of_last_case_does_not_mark_run_complete(runner, monkeypatch, tmp_path):
    _run_setup(runner, monkeypatch, tmp_path)
    client, requests, _ = _transport(runner, monkeypatch, error=KeyboardInterrupt())
    path = runner.run("baseline", ["role-request"])
    result = json.loads(path.read_text(encoding="utf-8"))
    assert not result["complete"] and result["stopReason"] == "interrupted"
    assert result["notAttemptedCaseIds"] == [] and result["inFlightCaseId"] is None
    assert result["cases"][0]["errorCode"] == "interrupted" and len(requests) == 1 and client.closed


def test_crash_retains_inflight_checkpoint_separate_from_unattempted(runner, monkeypatch, tmp_path):
    _run_setup(runner, monkeypatch, tmp_path)

    def process_exit(*args):
        raise SystemExit(9)

    monkeypatch.setattr(runner, "attempt", process_exit)
    with pytest.raises(SystemExit):
        runner.run("candidate", ["role-request", "role-query"])
    paths = list((tmp_path / ".cache/agent-evaluation").glob("*/candidate.json"))
    assert len(paths) == 1
    result = json.loads(paths[0].read_text(encoding="utf-8"))
    assert not result["complete"] and result["inFlightCaseId"] == "role-request"
    assert result["notAttemptedCaseIds"] == ["role-query"] and result["cases"] == []


def test_binding_change_before_attempt_opens_no_credentials(runner, monkeypatch, tmp_path):
    _run_setup(runner, monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_binding_unchanged", lambda *args: False)
    monkeypatch.setattr(runner, "attempt", lambda *a: pytest.fail("changed binding called"))
    path = runner.run("candidate", ["role-request"])
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["stopReason"] == "binding_changed" and not result["complete"]
    assert result["cases"] == [] and result["notAttemptedCaseIds"] == ["role-request"]


def test_retrieval_query_source_change_stops_before_next_attempt(runner, monkeypatch, tmp_path):
    source_binding = runner.source_binding
    _run_setup(runner, monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "source_binding", source_binding)
    monkeypatch.setattr(runner, "evaluation_source_file_sha256", lambda: {})
    query_name = "app/agent/maxis/retrieval_query.py"
    for name in (*runner.HARNESS_FILES, *runner.CANDIDATE_FILES, query_name):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# synthetic source\n", encoding="utf-8")
    attempted = []

    def changed_source(case, *args):
        attempted.append(case["id"])
        (tmp_path / query_name).write_text("# changed source\n", encoding="utf-8")
        return {"attemptId": case["id"], "caseId": case["id"], "roleId": case["role_id"],
                "status": "needs_input", "errorCode": "", "responseReceived": True,
                "contractOk": True, "retrievalOk": True, "latencyMs": 100, "usage": None}

    monkeypatch.setattr(runner, "attempt", changed_source)
    path = runner.run("candidate", ["role-request", "role-query"])
    result = json.loads(path.read_text(encoding="utf-8"))
    assert attempted == ["role-request"]
    assert result["stopReason"] == "binding_changed"
    assert result["bindingUnchanged"] is False and not result["complete"]
    assert result["notAttemptedCaseIds"] == ["role-query"]
    assert query_name in result["metadata"]["maxisBinding"]["sourceFiles"]


def test_resolved_cache_escape_rejected(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    original_resolve = Path.resolve
    cache = tmp_path / ".cache/agent-evaluation"

    def redirected(path, *args, **kwargs):
        if path == cache:
            return original_resolve(tmp_path.parent / "outside-evaluation-cache")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected)
    with pytest.raises(ValueError, match="inside workspace"):
        runner.artifact_path("run-group", "baseline")


def test_artifact_group_cannot_escape_cache(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="inside workspace"):
        runner.artifact_path("../../../escape", "baseline")
