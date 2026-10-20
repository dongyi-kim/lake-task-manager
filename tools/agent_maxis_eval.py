"""MAXIS-only adapter over the existing five scenario batteries.

Use agent_eval_launcher.py maxis, never this worker directly. Remote preflight,
published-profile pinning, private synthetic Jira, immutable attempts and a local
LLM/embedding tripwire are mandatory. This measures stability, not qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SUITES = {
    "conversation": "agent_lang_ab",
    "create": "agent_create_suite",
    "compose": "agent_compose_eval",
    "meeting": "agent_meeting_eval",
    "context": "agent_context_change_eval",
}
PIN_ENV = "LTM_EVAL_MAXIS_PIN"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("suite", choices=(*SUITES, "all"))
    result.add_argument("--profile", required=True)
    result.add_argument("--case", action="append", default=[])
    result.add_argument("--repeat", type=int, choices=range(1, 6), default=1)
    result.add_argument("--confirm-remote", action="store_true")
    return result


def _pin(profile: str) -> dict:
    from app.agent.maxis import backend, profiles
    if profiles.active_profile_name() != profile:
        raise ValueError("MAXIS evaluation requires the explicitly applied profile")
    saved = backend.thread_runtime_pin()
    if saved["runtime_role_routes"].get("editor_author") != "maxis":
        raise ValueError("Apply the MAXIS Editor route before evaluation")
    return {"agent": backend.thread_runtime_pin(engine="maxis", profile=profile),
            "editor": saved}


def _digest(pin: dict) -> str:
    return hashlib.sha256(json.dumps(pin, sort_keys=True).encode()).hexdigest()


def verify_handoff(profile: str) -> dict:
    from tools.agent_eval_isolation import EvaluationPreflightError, NETWORK_PREFLIGHT_MARKER
    # Refuse before resolving keys or opening a socket.
    if os.getenv(NETWORK_PREFLIGHT_MARKER) != "1" or not os.getenv(PIN_ENV):
        raise EvaluationPreflightError("network-authorization", "maxis")
    if os.getenv("JIRA_ENV") != "mock":
        raise EvaluationPreflightError("synthetic-data-required", "maxis")
    if _digest(_pin(profile)) != os.environ[PIN_ENV]:
        raise EvaluationPreflightError("profile-changed", "maxis")
    return {"provider": "maxis", "status": "launcher-verified", "identity": os.environ[PIN_ENV]}


def launch(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    if not args.confirm_remote:
        raise SystemExit("Add --confirm-remote only after authorizing real MAXIS calls.")
    if os.getenv("JIRA_ENV") != "mock":
        raise SystemExit("JIRA_ENV=mock is required; no real Jira evaluation is permitted.")
    if args.suite == "all" and args.case:
        raise SystemExit("--case requires a single suite")
    # No DEV paths, model names, server URLs or credentials are embedded here.
    from app.agent.maxis import profiles
    from app.agent.routes import _run_maxis_probe
    from tools.agent_eval_isolation import (
        EFFECTIVE_PROVIDER_IDENTITY_ENV, NETWORK_PREFLIGHT_MARKER,
    )
    profile, keys, _ = profiles.start_probe(args.profile)
    pin = _pin(args.profile)
    group = "maxis-stability-" + uuid4().hex[:12]
    from tools.agent_eval_protocol import reserve_raw_result_path, write_raw_result
    probe_path = ROOT / ".cache/agent-evaluation" / group / "preflight.json"
    reserve_raw_result_path(probe_path)
    write_raw_result(probe_path, {"phase": "started", "complete": False})
    try:
        probe = _run_maxis_probe(profile, keys)
        write_raw_result(probe_path, {"phase": "complete", "complete": True, "result": probe})
    except BaseException as exc:
        write_raw_result(probe_path, {"phase": "interrupted", "complete": False,
                                     "errorType": type(exc).__name__})
        raise
    finally:
        keys.clear()
    if not probe.get("ok"):
        print(json.dumps({"preflight": "failed", "workflows": probe.get("workflows")},
                         ensure_ascii=False), flush=True)
        return 3
    if _pin(args.profile) != pin:
        raise SystemExit("Profile changed during preflight; apply and check again.")
    child_env = {
        **os.environ, NETWORK_PREFLIGHT_MARKER: "1",
        PIN_ENV: _digest(pin), EFFECTIVE_PROVIDER_IDENTITY_ENV: _digest(pin),
        "LAKE_AGENT_PROVIDER": "maxis",
        "LTM_EVAL_RUN_GROUP_ID": group,
        "LTM_EVAL_REPETITIONS": str(args.repeat),
        "LTM_EVAL_RUNTIME_PROFILE": "maxis-only-synthetic",
        "LTM_EVAL_RUN_KIND": "exploratory",
        "LTM_AGENT_STRUCTURED_OUTPUT_FALLBACK": "forbid",
    }
    failures = 0
    for repeat in range(1, args.repeat + 1):
        for suite in (SUITES if args.suite == "all" else [args.suite]):
            command = [sys.executable, "-B", "-X", "utf8", "-u", str(Path(__file__)),
                       suite, "--profile", args.profile]
            for selected in args.case:
                command.extend(("--case", selected))
            completed = subprocess.run(command, cwd=ROOT, env={
                **child_env, "LTM_EVAL_REPEAT_INDEX": str(repeat),
            }, check=False)
            failures += completed.returncode != 0
            if completed.returncode:
                return 3  # Preserve failed attempt; diagnose before spending on another group.
    return 1 if failures else 0


def technical_failures(outputs: list[dict], *, editor: bool) -> list[str]:
    """Blocking execution/contract failures, including refused unsafe generation.

    A contentConflict is not a network outage, but is still a failed user flow.
    Only style/quality-checker misses with a usable response may continue. This
    diagnostic runner stops on blockers; an incomplete group never qualifies.
    """
    from tools.agent_eval_contracts import turn_execution_flaws
    failures = [] if editor else turn_execution_flaws(outputs)
    for index, output in enumerate(outputs):
        if editor:
            if output.get("needsInfo") is True and (output.get("questions") or output.get("error")):
                continue  # Explicit missing input is not generation success or HTTP failure.
            if not output.get("ok") or not str(output.get("html") or "").strip():
                failures.append(f"turn[{index}] editor generation failed/empty")
        else:
            if not str(output.get("reply") or "").strip():
                failures.append(f"turn[{index}] empty Agent reply")
        if output.get("exceptionType"):
            failures.append(f"turn[{index}] unhandled {output['exceptionType']}")
    return failures


def run(argv: list[str]) -> int:
    from app.agent import config
    previous = config.get_llm, config.get_embeddings
    try:
        return _run(argv)
    finally:
        config.get_llm, config.get_embeddings = previous


def _run(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    verify_handoff(args.profile)
    from tools.agent_eval_isolation import begin_case, configure_process_isolation, finish_case
    configure_process_isolation("maxis-" + args.suite)
    from app.agent import config, editor_author
    from app.agent.workflow import session
    from tools.agent_eval_protocol import (
        build_run_metadata, raw_result_path, reserve_raw_result_path, write_raw_result,
        quantitative_metrics,
    )

    def forbidden(*_a, **_kw):
        raise RuntimeError("MAXIS evaluation attempted a local LLM or embedding call")

    config.get_llm = forbidden
    config.get_embeddings = forbidden
    module = importlib.import_module("tools." + SUITES[args.suite])
    cases = module.SCENARIOS if args.suite == "conversation" else module.CASES
    selected = [row for row in cases if not args.case or row[0] in args.case]
    if not selected or set(args.case) - {row[0] for row in cases}:
        raise ValueError("Unknown case ID; refusing empty/partial accidental selection")
    metadata = build_run_metadata(
        suite="editor" if args.suite == "compose" else args.suite,
        battery_version=module.BATTERY_VERSION, cases=cases,
        selected_case_ids=[row[0] for row in selected],
        model="server-managed-unobserved", simple_model="server-managed-unobserved",
        prompt_version=module.PROMPT_VERSION,
        suite_review_elements=module.SUITE_REVIEW_ELEMENTS,
        case_review_specs=module.CASE_REVIEW_SPECS,
        checker_dependencies=(technical_failures,),
    )
    target = raw_result_path("maxis-" + args.suite, metadata)
    reserve_raw_result_path(target)
    report = {"evaluation": metadata, "cases": [], "complete": False,
              "purpose": "blocking execution/contract stability; not operational or quality qualification",
              "stopPolicy": "stop-on-first-blocking-failure; quality-only checker misses continue"}
    print("RAW", target, flush=True)
    write_raw_result(target, report)
    for case in selected:
        outputs, records = [], []
        row = {"id": case[0], "turns": records, "complete": False}
        report["cases"].append(row)
        write_raw_result(target, report)
        try:
            isolation = begin_case(case[0], maxis_profile=args.profile)
        except Exception as exc:
            row.update(technicalFailures=["case setup failed"], errorType=type(exc).__name__)
            write_raw_result(target, report)
            return 3
        thread_id = ""
        prompts = [case[2]] if args.suite == "compose" else (
            case[1] if args.suite == "conversation" else case[2])
        for prompt in prompts:
            record = {"input": prompt, "status": "started"}
            records.append(record)
            write_raw_result(target, report)
            turn_start = time.monotonic()
            try:
                verify_handoff(args.profile)
                if args.suite == "compose":
                    output = editor_author.compose(**prompt)
                else:
                    output = session.ask(prompt, thread_id=thread_id,
                                         engine="maxis", profile=args.profile)
                    thread_id = output.get("thread_id") or thread_id
                    record["output"] = output
                    write_raw_result(target, report)
                    output["evaluationEvidence"] = session.evaluation_snapshot(thread_id)
                record["output"] = output
                write_raw_result(target, report)
                verify_handoff(args.profile)
            except BaseException as exc:
                # Never persist provider exceptions, URLs, credentials or reasoning.
                record.update(status="interrupted", errorType=type(exc).__name__,
                              seconds=round(time.monotonic() - turn_start, 3))
                row["technicalFailures"] = ["turn interrupted; output may be incomplete"]
                write_raw_result(target, report)
                return 3
            outputs.append(output)
            record.update(output=output, status="completed",
                          seconds=round(time.monotonic() - turn_start, 3))
            write_raw_result(target, report)
            if technical_failures([output], editor=args.suite == "compose"):
                break
        failures = technical_failures(outputs, editor=args.suite == "compose")
        try:
            isolated = finish_case(isolation)
        except RuntimeError:
            isolated = {**isolation, "worldUnchanged": False}
            failures.append("synthetic fixture mutated without approval")
        checker = None
        if len(case) == 4:
            try:
                checker = bool(case[3](outputs[-1]) if args.suite == "compose"
                               else case[3](outputs[-1], outputs))
            except Exception as exc:
                checker = {"errorType": type(exc).__name__}
        row.update({
            "technicalFailures": failures, "complete": len(records) == len(prompts),
            "failureCategory": ("content_contract" if any(o.get("contentConflict") for o in outputs)
                                else "execution_or_contract") if failures else None,
            "existingQualityChecker": checker, "isolation": isolated,
            "seconds": round(sum(r["seconds"] for r in records), 3),
        })
        usages = [output.get("usage") or {} for output in outputs]
        row["metrics"] = quantitative_metrics(
            attempts=len(records), duration_seconds=row["seconds"],
            calls=sum(u.get("calls") or 0 for u in usages),
            prompt_tokens=sum(u.get("promptTokens") or 0 for u in usages),
            completion_tokens=sum(u.get("completionTokens") or 0 for u in usages),
            total_tokens=sum(u.get("totalTokens") or 0 for u in usages),
            cached_tokens=sum(u.get("cachedTokens") or 0 for u in usages),
            cost_usd=(sum(u["costUsd"] for u in usages)
                      if all(isinstance(u.get("costUsd"), (int, float)) for u in usages) else None),
        )
        # These are request-level reported metadata, never native-node model totals.
        metrics = row["metrics"]
        complete = all(u.get("usageComplete") is True and all(
            isinstance(u.get(key), int) for key in ("promptTokens", "completionTokens", "totalTokens"))
                       for u in usages)
        metrics.update(
            usageComplete=complete, usageScope="reported_response_metadata",
            remoteRequests=metrics["calls"], nativeModelCalls=None,
            unknownUsageCalls=sum(int(u.get("unknownUsageCalls") or (
                1 if not isinstance(u.get("totalTokens"), int) else 0)) for u in usages),
        )
        for key in ("promptTokens", "completionTokens", "totalTokens", "cachedTokens"):
            if not complete or any(not isinstance(u.get(key), int) for u in usages):
                metrics["reported" + key[0].upper() + key[1:]] = metrics[key]
                metrics[key] = None
        write_raw_result(target, report)
        print(json.dumps({"case": case[0], "technicalFailures": failures,
                          "qualityChecker": checker, "seconds": row["seconds"]},
                         ensure_ascii=False), flush=True)
        if failures:
            return 3
    report["complete"] = True
    report["technicalFailureCount"] = sum(bool(row["technicalFailures"]) for row in report["cases"])
    write_raw_result(target, report)
    return 1 if report["technicalFailureCount"] else 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
