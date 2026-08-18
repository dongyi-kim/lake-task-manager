"""Shared runner for multi-turn manual Agent scenario batteries.

This module is deliberately runtime-neutral when imported by tests.  A suite calls
``run_scenario_suite`` only from ``__main__``; that function then configures the
process-private cache before importing the live Agent session.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from tools.agent_eval_contracts import (
    AUTOMATIC_CONTRACT_DEPENDENCIES,
    automatic_contract_flaws,
)
from tools.agent_eval_isolation import (begin_case, configure_process_isolation, finish_case,
                                        preflight_evaluation_provider)
from tools.agent_eval_protocol import (
    build_run_metadata,
    quantitative_metrics,
    raw_result_path,
    reserve_raw_result_path,
    write_raw_result,
)


ScenarioCheck = Callable[[dict[str, Any], list[dict[str, Any]]], bool]
Scenario = tuple[str, str, list[str], ScenarioCheck]


def validate_eval_argv(argv: Sequence[str]) -> None:
    """Reject control-looking arguments before a manual runner can start a battery.

    These scripts historically treated ``--help`` and misspelled options as an omitted model,
    which silently launched the full default battery. Only the one documented option is valid.
    """
    raw = list(argv)
    for index, arg in enumerate(raw):
        if arg == "--out":
            if index + 1 >= len(raw) or raw[index + 1].startswith("-"):
                raise SystemExit("--out requires a result path; evaluation not started")
            continue
        if arg.startswith("--out="):
            if not arg.split("=", 1)[1].strip():
                raise SystemExit("--out requires a result path; evaluation not started")
            continue
        if arg.startswith("-"):
            raise SystemExit(f"unsupported evaluation option: {arg}; evaluation not started")


def configure_model_routing(model: str, simple_model: str) -> str:
    """Respect an explicitly injected provider while preserving cloud defaults."""
    provider = str(os.environ.get("LAKE_AGENT_PROVIDER") or "openai").strip().lower()
    os.environ["LAKE_AGENT_PROVIDER"] = provider
    prefix = {"openai": "LAKE_AGENT_OPENAI_CHAT",
              "openai_compat": "LAKE_AGENT_COMPAT_CHAT",
              "aoai": "LAKE_AGENT_AOAI_CHAT"}.get(provider)
    if not prefix:
        raise ValueError(f"battery에서 지원하지 않는 provider: {provider}")
    os.environ[prefix] = model
    os.environ[prefix + "_SIMPLE"] = simple_model
    return provider


def parse_scenario_args(
    argv: Sequence[str], *, default_model: str = "gpt-4o",
) -> tuple[str, set[str], str | None]:
    raw = list(argv)
    validate_eval_argv(raw)
    requested_out = None
    for index, arg in enumerate(raw):
        if arg.startswith("--out="):
            requested_out = arg.split("=", 1)[1]
        elif arg == "--out" and index + 1 < len(raw):
            requested_out = raw[index + 1]
    positional = [
        arg for index, arg in enumerate(raw)
        if not arg.startswith("-") and not (index and raw[index - 1] == "--out")
    ]
    model = positional[0] if positional and not positional[0].isupper() else default_model
    selected = {
        value.upper() for value in positional[(1 if positional and model == positional[0] else 0):]
        if value
    }
    return model, selected, requested_out


def pending_items(output: dict[str, Any]) -> list[dict[str, Any]]:
    return list((output.get("pending") or {}).get("items") or output.get("draft_items") or [])


def selected_scenarios(cases: Sequence[Scenario], selected: set[str]) -> list[Scenario]:
    if not selected:
        return list(cases)
    return [case for case in cases if case[0].upper() in selected]


def _usage(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    total = {
        "calls": 0,
        "promptTokens": 0,
        "completionTokens": 0,
        "totalTokens": 0,
        "cachedTokens": 0,
        "costUsd": 0.0,
    }
    for record in records:
        for turn in record.get("turns") or []:
            usage = ((turn.get("output") or {}).get("usage") or {})
            for key in ("calls", "promptTokens", "completionTokens", "totalTokens", "cachedTokens"):
                total[key] += usage.get(key) or 0
            total["costUsd"] += usage.get("costUsd") or 0
    total["costUsd"] = round(total["costUsd"], 6)
    return total


def _payload(
    *, suite: str, model: str, simple_model: str, prompt_version: str,
    evaluation: dict[str, Any], records: Sequence[dict[str, Any]], total_cases: int,
) -> dict[str, Any]:
    usage = _usage(records)
    duration = round(sum(float(record.get("durationSeconds") or 0) for record in records), 1)
    passed = sum(1 for record in records if record.get("automaticPass"))
    return {
        "suite": suite,
        "model": model,
        "simpleModel": simple_model,
        "promptVersion": prompt_version,
        "evaluation": evaluation,
        "executionComplete": len(records) == total_cases,
        "metrics": quantitative_metrics(
            attempts=len(records),
            duration_seconds=duration,
            calls=usage["calls"],
            prompt_tokens=usage["promptTokens"],
            completion_tokens=usage["completionTokens"],
            total_tokens=usage["totalTokens"],
            cached_tokens=usage["cachedTokens"],
            cost_usd=usage["costUsd"],
        ),
        "summary": {
            "automaticPass": passed,
            "completed": len(records),
            "total": total_cases,
            "durationSeconds": duration,
            **usage,
        },
        "cases": list(records),
    }


def run_scenario_suite(
    *, suite: str, battery_version: str, cases: Sequence[Scenario], model: str,
    simple_model: str, prompt_version: str, suite_review_elements: Sequence[dict[str, Any]],
    case_review_specs: dict[str, dict[str, Any]], selected: set[str] | None = None,
    requested_out: str | os.PathLike[str] | None = None,
    checker_dependencies: Sequence[Any] = (),
) -> Path:
    configure_process_isolation(suite)
    # A manual quality battery must never inherit a caller's production Jira mode.
    os.environ["JIRA_ENV"] = "mock"
    os.environ.setdefault("LAKE_AGENT_PROVIDER", "openai")
    os.environ["LAKE_AGENT_SKIP_VERIFY"] = "1"
    configure_model_routing(model, simple_model)
    preflight_evaluation_provider()

    # Import only after cache/provider environment is complete.
    from app.agent.workflow import session

    run_cases = selected_scenarios(cases, selected or set())
    evaluation = build_run_metadata(
        suite=suite,
        battery_version=battery_version,
        cases=cases,
        selected_case_ids=[case[0] for case in run_cases],
        model=model,
        simple_model=simple_model,
        prompt_version=prompt_version,
        suite_review_elements=suite_review_elements,
        case_review_specs=case_review_specs,
        checker_dependencies=(*AUTOMATIC_CONTRACT_DEPENDENCIES, *checker_dependencies),
    )
    out_path = reserve_raw_result_path(
        raw_result_path(suite, evaluation, requested=requested_out),
    )
    records: list[dict[str, Any]] = []
    for case_id, description, prompts, checker in run_cases:
        isolation_start = begin_case(case_id)
        started = time.time()
        thread_id = ""
        turns: list[dict[str, Any]] = []
        isolation: dict[str, Any] = {}
        automatic_pass = False
        automatic_flaws: list[str] = []
        error = ""
        try:
            outputs: list[dict[str, Any]] = []
            for prompt in prompts:
                turn_started = time.time()
                output = session.ask(prompt, thread_id=thread_id)
                thread_id = output.get("thread_id") or thread_id
                output["evaluationEvidence"] = session.evaluation_snapshot(thread_id)
                outputs.append(output)
                turns.append({
                    "input": prompt,
                    "durationSeconds": round(time.time() - turn_started, 1),
                    "output": output,
                })
            automatic_flaws = automatic_contract_flaws(outputs)
            automatic_pass = bool(checker(outputs[-1], outputs) and not automatic_flaws)
            isolation = finish_case(isolation_start)
        except Exception as exc:  # one failure must not discard the remaining battery
            error = str(exc)
            try:
                isolation = finish_case(isolation_start)
            except Exception as isolation_error:
                error = f"{error}; isolation failure: {isolation_error}"
        record = {
            "id": case_id,
            "description": description,
            "inputs": prompts,
            "automaticPass": automatic_pass,
            "automaticContractFlaws": automatic_flaws,
            "durationSeconds": round(time.time() - started, 1),
            "turns": turns,
            "isolation": isolation,
        }
        if error:
            record["error"] = error
        records.append(record)
        write_raw_result(
            out_path,
            _payload(
                suite=suite,
                model=model,
                simple_model=simple_model,
                prompt_version=prompt_version,
                evaluation=evaluation,
                records=records,
                total_cases=len(run_cases),
            ),
        )
        mark = "PASS" if automatic_pass else "FAIL"
        print(f"{mark} {case_id} · {record['durationSeconds']}s", flush=True)

    print(json.dumps(_payload(
        suite=suite,
        model=model,
        simple_model=simple_model,
        prompt_version=prompt_version,
        evaluation=evaluation,
        records=records,
        total_cases=len(run_cases),
    )["summary"], ensure_ascii=False), flush=True)
    print(f"-> {out_path}", flush=True)
    return out_path


__all__ = [
    "Scenario", "parse_scenario_args", "pending_items", "run_scenario_suite",
    "selected_scenarios", "validate_eval_argv",
]
