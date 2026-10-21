"""PROD qualification fails closed; no fabricated reviews or network in these tests."""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.maxis import qualification as q, qualification_routes as api, qualification_runs as runs
from tools.agent_eval_protocol import load_protocol

WORKFLOWS = ["agent_core", "editor_author"]


@pytest.mark.parametrize("workflows, count", [(WORKFLOWS, 30), (WORKFLOWS + ["pmo_brief"], 31)])
def test_real_repository_cases_match_selected_workflow_acl(workflows, count):
    import yaml
    from app.agent.maxis.registry import load_registry
    source = yaml.safe_load((q.source_root() / "config/maxis-knowledge.yml").read_text(encoding="utf-8"))
    allowed = {row["id"]: set(row["allowed_roles"]) for row in source["documents"]}
    cases = q.knowledge_cases(workflows)
    assert len(cases) == count
    for case in cases:
        assert load_registry().resolve_role(case.role_id).key in workflows
        if case.expected_outcome == "retrieve":
            assert all(case.role_id in allowed[doc] for doc in case.include_document_ids)
        elif case.expected_outcome == "deny":
            assert all(case.role_id not in allowed[doc] for doc in case.exclude_document_ids)


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    from app.infra import settings
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(q, "binding", lambda profile, workflows: {
        "workflows": q.clean_workflows(workflows), "sourceSha256": profile.get("source", "current")})
    monkeypatch.setattr(api.profiles, "resolve", lambda _: {"stage": "prod", "name": "prod"})
    monkeypatch.setattr(api, "_can_manage", lambda: True)
    monkeypatch.setattr(runs, "_PROCESSES", {})
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def report():
    now = datetime.now(timezone.utc)
    return {
        "contract": q.CONTRACT, "runId": "a" * 32,
        "binding": q.binding({"name": "prod", "stage": "prod"}, WORKFLOWS),
        "startedAt": (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        "completedAt": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "complete": True, "errorCode": "",
        "knowledge": [{
            "kind": "knowledge", "sampleId": f"knowledge:{case.case_id}",
            "id": case.case_id, "roleId": case.role_id, "type": case.case_type,
            "automatedChecksPassed": True, "response": {"answer": "synthetic test output"},
        } for case in q.knowledge_cases(WORKFLOWS)],
        "roles": [{
            "kind": "role", "sampleId": f"role:{repeat}:{case['id']}",
            "caseId": case["id"], "roleId": case["role_id"], "repeat": repeat,
            "outcomeContractPass": True, "response": {"payload": "synthetic test output"},
        } for repeat in range(q.REPETITIONS) for case in q.role_cases(WORKFLOWS)],
    }


def reviewed(raw):
    rubric = load_protocol()["humanRubric"]
    return {
        "reportSha256": q.digest(raw),
        "reviewedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "evaluator": {"agentFamily": "codex", "agentModel": "unit-test-only",
                      "directRawOutputReview": True, "ltmLlmUsedAsJudge": False},
        "cases": {
            row["sampleId"]: {
                "scores": {d["id"]: 4 for d in rubric["dimensions"]},
                "checklistResults": {d["id"]: {
                    item["id"]: {"status": "pass", "evidence": "Synthetic test-only rubric input"}
                    for item in d["checklist"]} for d in rubric["dimensions"]},
                "specializedReviewResults": {
                    item["id"]: {"status": "pass", "evidence": "Synthetic test-only rubric input"}
                    for item in q.review_spec(row)["elements"]},
                "rationale": "Test-only review, never used for runtime qualification",
                "excerpt": "synthetic test output",
            } for row in [*raw["knowledge"], *raw["roles"]]},
    }


def envelope(raw=None, review=None, *, enroll=True):
    raw = raw or report()
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    fingerprint, _ = q.public_key_identity(public)
    if enroll:
        q.enroll_key(public, fingerprint, confirmed=True)
    payload = {"report": raw, "review": review or reviewed(raw),
               "expiresAt": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")}
    return {"contract": q.CONTRACT, "keyId": fingerprint, "payload": payload,
            "signature": base64.b64encode(key.sign(q.canonical(payload))).decode()}


def test_signed_complete_review_installs_and_is_revocable(isolated):
    signed = envelope()
    response = isolated.post("/api/agent/maxis/qualification/artifacts", json={"artifact": signed})
    assert response.status_code == 200 and response.json()["valid"]
    status = isolated.get("/api/agent/maxis/qualification/status").json()
    assert status["valid"] and status["workflows"] == WORKFLOWS
    assert status["requirements"]["knowledgeCases"] >= 25
    isolated.delete("/api/agent/maxis/qualification/keys/" + signed["keyId"])
    assert not q.status({})["valid"]


@pytest.mark.parametrize("kind", [
    "untrusted", "tamper", "source", "incomplete", "missing_case", "missing_repeat",
    "duplicate_sample", "failed_role", "failed_knowledge", "missing_review",
    "candidate_judge", "future_review", "wrong_review_hash", "low_score",
])
def test_invalid_evidence_never_grants_authority(isolated, kind):
    raw = report()
    if kind == "incomplete":
        raw["complete"] = False
    elif kind == "missing_case":
        raw["knowledge"].pop()
    elif kind == "missing_repeat":
        raw["roles"].pop()
    elif kind == "duplicate_sample":
        raw["roles"][1]["sampleId"] = raw["roles"][0]["sampleId"]
    elif kind == "failed_role":
        raw["roles"][0]["outcomeContractPass"] = False
    elif kind == "failed_knowledge":
        raw["knowledge"][0]["automatedChecksPassed"] = False
    review = reviewed(raw)
    if kind == "missing_review":
        review["cases"].pop(next(iter(review["cases"])))
    elif kind == "candidate_judge":
        review["evaluator"]["ltmLlmUsedAsJudge"] = True
    elif kind == "future_review":
        review["reviewedAt"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    elif kind == "wrong_review_hash":
        review["reportSha256"] = "0" * 64
    elif kind == "low_score":
        next(iter(review["cases"].values()))["scores"] = {
            d["id"]: 3 for d in load_protocol()["humanRubric"]["dimensions"]}
    signed = envelope(raw, review, enroll=kind != "untrusted")
    if kind == "tamper":
        signed["payload"]["report"]["roles"][0]["response"] = {"injected": True}
    with pytest.raises(ValueError):
        q.verify(signed, {"source": "changed"} if kind == "source" else {})
    assert not (q.directory() / "approved.json").exists()


def test_manager_only_keys_runs_raw_review_and_install(isolated, monkeypatch):
    monkeypatch.setattr(api, "_can_manage", lambda: False)
    root = "/api/agent/maxis/qualification"
    for method, path, body in [
        ("get", "/status", None), ("get", "/runs/" + "a" * 32, None),
        ("get", "/runs/" + "a" * 32 + "/review", None),
        ("post", "/runs", {"workflows": WORKFLOWS}),
        ("post", "/keys", {"publicKey": "x", "fingerprint": "x", "confirmed": True}),
        ("delete", "/keys/abc", None), ("post", "/artifacts", {"artifact": {}}),
    ]:
        response = isolated.request(method, root + path, **({"json": body} if body is not None else {}))
        assert response.status_code == 403
    assert not q.directory().exists()


def test_bad_registration_never_echoes_input(isolated):
    response = isolated.post("/api/agent/maxis/qualification/keys", json={
        "publicKey": "not-a-key", "fingerprint": "x", "confirmed": True, "password": "DO-NOT-ECHO"})
    assert response.status_code == 422 and "DO-NOT-ECHO" not in response.text


def test_public_key_requires_explicit_confirmation(isolated):
    signed = envelope()
    public = q.trusted_keys()[signed["keyId"]]["publicKey"]
    with pytest.raises(ValueError, match="confirm_reviewer"):
        q.enroll_key(public, signed["keyId"], confirmed=False)
    with pytest.raises(ValueError, match="confirm_reviewer"):
        q.enroll_key(public, "0" * 64, confirmed=True)


def test_runner_is_subprocess_mock_only_not_application_fixture_patch(isolated, monkeypatch):
    seen = {}
    monkeypatch.setattr(runs, "clean_commit", lambda: "c" * 40)
    monkeypatch.setattr(runs, "preflight", lambda workflows, **_: ({}, {"workflows": workflows}))

    def launch(command, **kwargs):
        seen.update(command=command, **kwargs)
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(runs.subprocess, "Popen", launch)
    result = runs.start(WORKFLOWS)
    assert result["state"] == "queued"
    assert seen["env"]["JIRA_ENV"] == "mock"
    assert seen["env"]["LAKE_CACHE_DIR"]
    assert "maxis_qualification.py" in seen["command"][4]
    assert "--id" in seen["command"] and result["runId"] in seen["command"]
    assert seen["command"][-2:] == ["--profile", "prod"]
    with pytest.raises(ValueError, match="already_running"):
        runs.start(WORKFLOWS)


def test_restart_does_not_misreport_queued_evaluation_as_alive(isolated):
    run_id = "b" * 32
    q.write_json(runs.run_path(run_id) / "progress.json", {
        "runId": run_id, "ownerPid": -1, "state": "running", "completed": 1,
        "total": 99, "errorCode": ""})
    assert runs.progress(run_id)["state"] == "interrupted"


def test_raw_download_contains_blank_review_not_automatic_passes(isolated):
    raw = report()
    q.write_json(runs.run_path(raw["runId"]) / "report.json", raw)
    bundle = runs.review_bundle(raw["runId"])
    assert bundle["reviewTemplate"]["reportSha256"] == q.digest(raw)
    assert all(not case["scores"] and not case["checklistResults"]
               for case in bundle["reviewTemplate"]["cases"].values())


def test_new_attempt_cannot_silently_keep_old_success(isolated):
    signed = envelope()
    q.install(signed, {})
    q.write_json(q.directory() / "current.json", {"runId": "f" * 32})
    assert q.status({})["reasonCode"] == "qualification_superseded_run"


def test_atomic_apply_failure_does_not_switch_active_profile(isolated, monkeypatch):
    from app.agent.maxis import profiles
    writes = []
    monkeypatch.delenv("LAKE_MAXIS_PROFILE", raising=False)
    monkeypatch.setattr(profiles, "kill_switch_enabled", lambda: False)
    monkeypatch.setattr(profiles, "get", lambda _: {})
    monkeypatch.setattr(profiles, "_effective", lambda value: value)
    monkeypatch.setattr(profiles, "_validate", lambda value, **_: {
        "stage": "prod", "data_policy": "internal_read_only", "execution_policy": "qualified",
    })
    monkeypatch.setattr(profiles, "_require_cross_environment_asset_separation", lambda _: None)
    monkeypatch.setattr(profiles, "probe_ok", lambda _: True)
    monkeypatch.setattr(q, "status", lambda _: {"valid": False, "workflows": []})
    monkeypatch.setattr(profiles.prefs, "save_strict", lambda value: writes.append(value))
    with pytest.raises(ValueError, match="qualification"):
        profiles.apply_production(WORKFLOWS)
    assert writes == []


def test_authority_uses_verified_features_only(isolated, monkeypatch):
    from app.agent.maxis import profiles
    monkeypatch.setattr(q, "status", lambda _: {"valid": True, "workflows": WORKFLOWS})
    profiles._require_prod_promotion({"stage": "prod"}, ["agent_core", "editor_author"])
    with pytest.raises(ValueError, match="qualification"):
        profiles._require_prod_promotion({"stage": "prod"}, ["pmo_brief"])
    monkeypatch.setattr(q, "status", lambda _: {"valid": False, "workflows": WORKFLOWS})
    with pytest.raises(ValueError, match="qualification"):
        profiles._require_prod_promotion({"stage": "prod"}, ["agent_core"])


def test_source_binding_includes_runtime_dependencies_outside_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "source_root", lambda: tmp_path)
    for relative in ("app/domain/ticket_actions.py", "app/infra/security.py",
                     "app/jira/client.py", "app/content/description.py", "requirements.txt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("before", encoding="utf-8")
        previous = q.source_digest()
        path.write_text("after", encoding="utf-8")
        assert q.source_digest() != previous, relative


def test_signed_result_and_supersession_are_scoped_to_profile(isolated, monkeypatch):
    monkeypatch.setattr(q, "binding", lambda profile, workflows: {
        "profile": profile["name"], "workflows": q.clean_workflows(workflows)})
    monkeypatch.setattr(api.profiles, "resolve", lambda name: {"name": name, "stage": "prod"})
    selected = {"name": "maxis-connection"}
    raw = report()
    raw["binding"] = q.binding(selected, WORKFLOWS)
    signed = envelope(raw)
    response = isolated.post("/api/agent/maxis/qualification/artifacts?profile=maxis-connection",
                             json={"artifact": signed})
    assert response.status_code == 200, response.text
    assert q.status(selected)["valid"]
    assert not q.status({"name": "prod"})["valid"]
    cross_profile = isolated.post("/api/agent/maxis/qualification/artifacts?profile=prod",
                                  json={"artifact": signed})
    assert cross_profile.status_code == 400
    q.write_json(q.directory("prod") / "current.json", {"runId": "b" * 32})
    assert q.status(selected)["valid"]
    q.write_json(q.directory(selected["name"]) / "current.json", {"runId": "c" * 32})
    assert q.status(selected)["reasonCode"] == "qualification_superseded_run"


def test_runner_and_raw_access_are_scoped_to_profile(isolated, monkeypatch):
    seen = []
    monkeypatch.setattr(runs, "clean_commit", lambda: "c" * 40)
    monkeypatch.setattr(runs, "preflight", lambda workflows, *, profile_name:
                        ({}, {"profile": profile_name, "workflows": workflows}))
    monkeypatch.setattr(runs.subprocess, "Popen", lambda command, **kwargs:
                        (seen.append(command), SimpleNamespace(poll=lambda: None))[1])
    first = isolated.post("/api/agent/maxis/qualification/runs?profile=dev", json={"workflows": WORKFLOWS})
    second = isolated.post("/api/agent/maxis/qualification/runs?profile=maxis-connection",
                           json={"workflows": WORKFLOWS})
    assert first.status_code == second.status_code == 200
    assert seen[0][-2:] == ["--profile", "dev"]
    assert seen[1][-2:] == ["--profile", "maxis-connection"]
    run_id = first.json()["runId"]
    assert isolated.get(f"/api/agent/maxis/qualification/runs/{run_id}?profile=dev").status_code == 200
    assert isolated.get(f"/api/agent/maxis/qualification/runs/{run_id}?profile=prod").status_code == 400
    assert runs.current() is None
