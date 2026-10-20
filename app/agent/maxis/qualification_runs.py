"""Isolated synthetic PROD evaluations, with no local LLM or live Jira access."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.agent.maxis import qualification as q

_LOCK = threading.Lock()
_PROCESSES: dict[str, subprocess.Popen] = {}


def instant() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_path(run_id: str, *, profile_name: str = "prod") -> Path:
    if not q._UUID.fullmatch(str(run_id)):
        raise ValueError("qualification_run_invalid")
    return q.directory(profile_name) / "runs" / run_id


def progress(run_id: str, *, profile_name: str = "prod") -> dict:
    path = run_path(run_id, profile_name=profile_name)
    result = q.read_json(path / "progress.json")
    process = _PROCESSES.get(run_id)
    if result["state"] in {"queued", "running"} and (
        (process is not None and process.poll() is not None)
        or (process is None and result.get("ownerPid") != os.getpid())
    ):
        result.update(state="interrupted", errorCode="qualification_interrupted")
        q.write_json(path / "progress.json", result)
    return result


def current(*, profile_name: str = "prod") -> dict | None:
    path = q.directory(profile_name) / "current.json"
    if not path.exists():
        return None
    return progress(q.read_json(path)["runId"], profile_name=profile_name)


def preflight(workflows: list[str], *, profile_name: str = "prod") -> tuple[dict, dict]:
    from app.agent.maxis import profiles
    from app.agent.maxis.destination_policy import canonical_maxis_api_base
    profile = profiles.resolve(profile_name)
    canonical_maxis_api_base(profile["base_url"], stage=profile["stage"], require_https=True)
    if not profile["verified"]:
        raise ValueError("qualification_probe_required")
    # Evaluation is synthetic and candidate-scoped; activation is not a prerequisite.
    for key in q.clean_workflows(workflows):
        if not profile["workflows"][key]["version"]:
            raise ValueError("qualification_workflow_missing")
    return profile, q.binding(profile, workflows)


def clean_commit() -> str:
    root = q.source_root().as_posix()
    try:
        command = ["git", "-c", "safe.directory=" + root, "-C", root]
        status = subprocess.run([*command, "status", "--porcelain", "--untracked-files=normal"],
                                capture_output=True, check=True, timeout=15)
        head = subprocess.run([*command, "rev-parse", "HEAD"],
                              capture_output=True, check=True, timeout=15)
        if status.stdout.strip():
            raise ValueError("qualification_clean_commit_required")
        return head.stdout.decode("ascii").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("qualification_clean_commit_required") from exc


def start(workflows: list[str], *, profile_name: str = "prod") -> dict:
    from app.infra.settings import CACHE_DIR
    workflows = q.clean_workflows(workflows)
    with _LOCK:
        running = current(profile_name=profile_name)
        if running and running["state"] in {"queued", "running"}:
            raise ValueError("qualification_already_running")
        commit = clean_commit()
        _, binding = preflight(workflows, profile_name=profile_name)
        run_id = uuid.uuid4().hex
        path = run_path(run_id, profile_name=profile_name)
        path.mkdir(parents=True, exist_ok=False)
        q.write_json(path / "request.json", {"profile": profile_name, "workflows": workflows, "binding": binding, "commit": commit})
        state = {"runId": run_id, "profile": profile_name, "state": "queued", "completed": 0,
                 "total": len(q.knowledge_cases(workflows)) + q.REPETITIONS * len(q.role_cases(workflows)),
                 "errorCode": "", "ownerPid": os.getpid()}
        q.write_json(path / "progress.json", state)
        env = dict(os.environ, JIRA_ENV="mock", LAKE_CACHE_DIR=str(CACHE_DIR),
                   PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
        try:
            # Role fixture builders replace process globals. Never run these in the
            # application worker, even in a thread; they cannot touch live Jira here.
            process = subprocess.Popen(
                [sys.executable, "-B", "-X", "utf8",
                 str(q.source_root() / "tools/maxis_qualification.py"), "run", "--id", run_id,
                 "--profile", profile_name],
                cwd=str(q.source_root()), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as exc:
            state.update(state="failed", errorCode="qualification_launch_failed")
            q.write_json(path / "progress.json", state)
            raise ValueError("qualification_launch_failed") from exc
        _PROCESSES[run_id] = process
        q.write_json(q.directory(profile_name) / "current.json", {"runId": run_id})
        return state


def review_bundle(run_id: str, *, profile_name: str = "prod") -> dict:
    path = run_path(run_id, profile_name=profile_name)
    report = q.read_json(path / "report.json")
    from tools.agent_eval_protocol import load_protocol
    rubric = load_protocol()["humanRubric"]
    return {
        "report": report,
        "rubric": rubric,
        "reviewSpecs": {row["sampleId"]: q.review_spec(row)
                        for row in [*report["knowledge"], *report["roles"]]},
        "reviewTemplate": {
            "reportSha256": q.digest(report), "reviewedAt": "",
            "evaluator": {"agentFamily": "", "agentModel": "",
                          "directRawOutputReview": False, "ltmLlmUsedAsJudge": False},
            # Never generate passing judgments. A separate reviewer reads every output.
            "cases": {row["sampleId"]: {"scores": {}, "checklistResults": {},
                      "specializedReviewResults": {}, "failureCodes": [],
                      "rationale": "", "excerpt": ""}
                      for row in [*report["knowledge"], *report["roles"]]},
        },
    }


def knowledge_attempt(case, profile: dict, identity, suite) -> dict:
    from app.agent.maxis import credentials, knowledge_eval as ke
    from app.agent.maxis.client import _strict_json_loads
    binding = ke.workflow_binding_for_case(case)
    pack = ke.build_synthetic_pack(
        case, suite_sha256=suite.sha256,
        knowledge_manifest_sha256=identity.manifest_sha256, requested_at=datetime.now(timezone.utc),
    )
    version = profile["workflows"][binding.key]["version"]
    client = ke.make_client(profile, binding)
    response, observation, error = {}, None, ""
    try:
        body, status, latency, _, breaker = client._request_validated_chat_bytes(
            binding, api_key=credentials.get_for(profile["name"], binding.key),
            query=pack.query, user=pack.pseudonymous_user,
            inputs={"context_pack": pack, "role_id": case.role_id,
                    "correlation_id": pack.request_id, "output_contract": binding.output_contract,
                    "workflow_version": version, "knowledge_version": profile["knowledge_version"]},
            workflow_id=version, expected_knowledge_dataset_id=identity.dataset_id,
        )
        breaker.record_success()
        response = _strict_json_loads(body)
        observation = ke.evaluate_raw_response(
            case=case, pack=pack, body=body, status_code=status, latency_ms=latency,
            identity=identity, workflow_version=version, knowledge_version=profile["knowledge_version"],
            knowledge_sentinel_sha256=profile["knowledge_sentinel_sha256"],
        )
    except Exception as exc:
        error = ke.safe_remote_error(exc)
    finally:
        client.close()
    return {**ke.case_result(case, workflow_key=binding.key, observation=observation, error_code=error),
            "kind": "knowledge", "sampleId": f"knowledge:{case.case_id}",
            "request": pack.model_dump(mode="json"),
            "response": response if isinstance(response, dict) else {}}


def execute(run_id: str, *, profile_name: str = "prod") -> None:
    """Subprocess entry point. All raw output stays in the local ignored cache."""
    from app.agent.maxis import knowledge_eval as ke
    from app.agent.maxis.knowledge_config import resolve_path
    path = run_path(run_id, profile_name=profile_name)
    state = q.read_json(path / "progress.json")
    request = q.read_json(path / "request.json")
    report = {"contract": q.CONTRACT, "runId": run_id, "binding": request["binding"],
              "startedAt": instant(), "completedAt": "", "complete": False,
              "errorCode": "", "knowledge": [], "roles": []}
    try:
        if request.get("profile", "prod") != profile_name:
            raise ValueError("qualification_binding_changed")
        profile, binding = preflight(request["workflows"], profile_name=profile_name)
        if clean_commit() != request["commit"]:
            raise ValueError("qualification_binding_changed")
        if binding != request["binding"]:
            raise ValueError("qualification_binding_changed")
        identity = ke.load_identity_map(
            environment_path=resolve_path(profile["knowledge_environment_binding"]),
            knowledge_manifest_path=resolve_path(profile["knowledge_source_manifest"]), stage=profile["stage"],
        )
        if identity.manifest_sha256 != profile["knowledge_manifest_sha256"]:
            raise ValueError("qualification_binding_changed")
        for case in q.knowledge_cases(request["workflows"]):
            allowed = identity.allowed_document_ids_by_role.get(case.role_id, frozenset())
            if ((case.expected_outcome == "retrieve"
                 and not set(case.include_document_ids).issubset(allowed))
                    or (case.expected_outcome == "deny"
                        and not set(case.exclude_document_ids).isdisjoint(allowed))):
                raise ValueError("qualification_case_scope_invalid")
        suite = ke.load_suite()
        sys.path.insert(0, str(q.source_root() / "deploy/maxis-dify"))
        from run_role_evals import attempt
        state.update(state="running")
        q.write_json(path / "progress.json", state)

        def check_current():
            _, current_binding = preflight(request["workflows"], profile_name=profile_name)
            if current_binding != binding:
                raise ValueError("qualification_binding_changed")

        def record():
            state["completed"] = len(report["knowledge"]) + len(report["roles"])
            q.write_json(path / "progress.json", state)
            q.write_json(path / "report.json", report)

        for case in q.knowledge_cases(request["workflows"]):
            check_current()
            report["knowledge"].append(knowledge_attempt(case, profile, identity, suite))
            record()
        for repetition in range(q.REPETITIONS):
            for case in q.role_cases(request["workflows"]):
                check_current()
                row = attempt(case, profile, {"knowledge_manifest_sha256": identity.manifest_sha256}, identity)
                report["roles"].append({**row, "kind": "role", "repeat": repetition,
                                        "sampleId": f"role:{repetition}:{case['id']}"})
                record()
        check_current()
        if clean_commit() != request["commit"]:
            raise ValueError("qualification_binding_changed")
        report["commit"] = request["commit"]
        report.update(complete=True, completedAt=instant())
        q.validate_report(report, profile)
        state.update(state="awaiting_review")
    except Exception as exc:
        import re
        code = str(exc)
        if not re.fullmatch(r"qualification_[a-z_]+", code):
            code = "qualification_evaluation_failed"
        report["errorCode"] = code
        state.update(state="failed", errorCode=code)
    finally:
        report["completedAt"] = instant()
        q.write_json(path / "report.json", report)
        q.write_json(path / "progress.json", state)
