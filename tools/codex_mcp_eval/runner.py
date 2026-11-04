"""One exploratory, versioned Codex/HTTP-MCP battery with retained attempts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.agent_eval_isolation import NETWORK_PREFLIGHT_MARKER
from tools.agent_eval_protocol import (RAW_RESULT_ROOT, build_run_metadata, quantitative_metrics,
    raw_result_path, reserve_raw_result_path, summarize_latency, write_raw_result)
from tools.codex_mcp_eval import cli
from tools.codex_mcp_eval.cases import (BATTERY_VERSION, CASES, CHECKER_DEPENDENCIES, OUTPUT_SCHEMA,
    PROMPT_VERSION, SUITE, SYSTEM_CONTEXT, check, review_contract)
from tools.codex_mcp_eval.fixture import EvaluationFixture

GATE = {"automaticPassRate": 1.0, "materialFalseFacts": 0, "unauthorizedWrites": 0,
        "humanMeanMinimum": 4.5, "humanScale": 5, "directRawReviewRequired": True,
        "qualificationClaim": False}


def _battery(name, today):
    if name == "identities":
        from tools.codex_mcp_eval import identity_cases
        from tools.codex_mcp_eval.identity_fixture import IdentityFixture
        return SimpleNamespace(**{key: getattr(identity_cases, key) for key in (
            "BATTERY_VERSION", "CHECKER_DEPENDENCIES", "OUTPUT_SCHEMA", "PROMPT_VERSION", "SUITE",
            "SYSTEM_CONTEXT", "check", "review_contract", "CASES")}, fixture=IdentityFixture)
    if name == "reporting":
        from tools.codex_mcp_eval import reporting_cases
        from tools.codex_mcp_eval.reporting_fixture import ReportingFixture
        return SimpleNamespace(**{key: getattr(reporting_cases, key) for key in (
            "BATTERY_VERSION", "CHECKER_DEPENDENCIES", "OUTPUT_SCHEMA", "PROMPT_VERSION", "SUITE",
            "SYSTEM_CONTEXT", "check")}, review_contract=lambda: reporting_cases.review_contract(today),
            CASES=reporting_cases.materialize_cases(today), fixture=ReportingFixture)
    return SimpleNamespace(BATTERY_VERSION=BATTERY_VERSION, CASES=CASES,
        CHECKER_DEPENDENCIES=CHECKER_DEPENDENCIES, OUTPUT_SCHEMA=OUTPUT_SCHEMA,
        PROMPT_VERSION=PROMPT_VERSION, SUITE=SUITE, SYSTEM_CONTEXT=SYSTEM_CONTEXT,
        check=check, review_contract=review_contract, fixture=EvaluationFixture)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Exploratory Codex + read-only LTM HTTP MCP quality battery")
    parser.add_argument("--run-group", default=datetime.now().strftime("%Y%m%d-%H%M%S-codex-mcp"))
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--battery", choices=("queries", "reporting", "identities"), default="queries")
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--warm-replay", action="store_true",
                        help="After each measured answer, separately replay successful read calls over HTTP to measure cache reuse")
    parser.add_argument("--model", default=cli.MODEL)
    parser.add_argument("--effort", default=cli.EFFORT)
    parser.add_argument("--repeat-index", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--codex", default=None)
    options = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", options.run_group):
        parser.error("run-group must contain only letters, digits, _ or -")
    try:
        date.fromisoformat(options.date)
    except ValueError:
        parser.error("date must be YYYY-MM-DD")
    if options.repeat_index < 1 or not 10 <= options.timeout <= 3600:
        parser.error("repeat-index must be positive and timeout must be 10..3600 seconds")
    available = {case["id"] for case in _battery(options.battery, options.date).CASES}
    if options.cases and (set(options.cases) - available or len(set(options.cases)) != len(options.cases)):
        parser.error("cases must be unique IDs from the selected battery")
    return options


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _payload(metadata, records, selected, preflight, *, failure=None):
    durations = [row["durationSeconds"] for row in records]
    usage = [row["output"].get("usage") or {} for row in records]
    def total(field):
        values = [row.get(field) for row in usage]
        return sum(values) if values and all(value is not None for value in values) else None
    payload = {
        "suite": metadata["battery"]["name"], "evaluation": metadata, "qualityGate": GATE,
        "humanReview": {"status": "pending", "notReplacedByAutomaticChecks": True},
        "executionComplete": len(records) == len(selected), "preflight": preflight,
        "metrics": quantitative_metrics(attempts=len(records), duration_seconds=sum(durations),
            calls=None, prompt_tokens=total("promptTokens"), completion_tokens=total("completionTokens"),
            total_tokens=total("totalTokens"), cached_tokens=total("cachedTokens"), cost_usd=None),
        "metricAvailability": {"modelCalls": False, "costUsd": False, "timeToFirstToken": False,
                               "latencyIncludes": "Codex process startup + model + MCP; fixture setup excluded"},
        "summary": {"completed": len(records), "total": len(selected),
                    "automaticPass": sum(row["automaticPass"] for row in records),
                    "toolCalls": sum(len(row["output"].get("mcpToolCalls") or []) for row in records),
                    "latency": summarize_latency(durations) if durations else None},
        "cases": records,
    }
    if failure:
        payload.update(infrastructureError=failure, executionComplete=False)
    return payload


def launch(argv=None, *, cli_run=None, fixture_factory=None, identity_fn=None):
    options = parse_args(argv)
    battery = _battery(options.battery, options.date)
    cli_run, fixture_factory, identity_fn = cli_run or cli.run, fixture_factory or battery.fixture, identity_fn or cli.identity
    binary = options.codex or cli.executable()
    identity = identity_fn(binary, model=options.model, effort=options.effort)
    selected = [case for case in battery.CASES if not options.cases or case["id"] in options.cases]
    run_root = RAW_RESULT_ROOT / options.run_group / f"attempt-{options.repeat_index:02d}"
    # Catalog/setup are part of preflight, not measured case time, and cannot warm case caches.
    with fixture_factory(run_root / "preflight-fixture", port=4457, today=options.date) as fixture:
        conformance = fixture.assert_conformance()
        catalog_hash = fixture.catalog_sha256
        scope = {"projects": list(fixture.settings.search_jira_projects),
                 "spaces": list(fixture.settings.search_confluence_spaces)}
    identity.update(provider="codex-chatgpt", toolCatalogSha256=catalog_hash,
                    outputSchemaSha256=_hash(battery.OUTPUT_SCHEMA))
    suite_review, case_specs = battery.review_contract()
    environment = {"LTM_EVAL_RUN_KIND": "exploratory", "LTM_EVAL_RUN_GROUP_ID": options.run_group,
        "LTM_EVAL_REPEAT_INDEX": str(options.repeat_index), "LTM_EVAL_REPETITIONS": str(options.repeat_index),
        "LTM_EVAL_CACHE_POLICY": "fresh-client-and-private-cache-each-case",
        "LTM_EVAL_PROCESS_ISOLATION": "sequential-private-http-server-and-world-each-case",
        "LTM_EVAL_RUNTIME_PROFILE": "external-codex-http-mcp-readonly-v1",
        "LTM_EVAL_DATA_PROFILE": "jira820-mcp@" + options.date,
        "LTM_EVAL_RETRY_POLICY": "no-silent-retry-new-attempt-only"}
    with patch.dict(os.environ, environment):
        metadata = build_run_metadata(suite=battery.SUITE, battery_version=battery.BATTERY_VERSION, cases=battery.CASES,
            selected_case_ids=[case["id"] for case in selected], model=options.model, simple_model="not-applicable",
            prompt_version=battery.PROMPT_VERSION, suite_review_elements=suite_review, case_review_specs=case_specs,
            checker_dependencies=(*battery.CHECKER_DEPENDENCIES, GATE), external_runtime=identity,
            benchmark_context={"fixtureDate": options.date, "timezone": "Asia/Seoul", "readScope": scope,
                               "fixtureConformance": conformance,
                               "outputContract": "external-json-answer-v1", "gate": GATE,
                               **({"warmReplay": "successful-reads-three-http-repetitions-after-answer"}
                                  if options.warm_replay else {})})
    out = reserve_raw_result_path(raw_result_path(battery.SUITE, metadata))
    # No secret, expected answer, source fixture or repository file is put in the model's cwd.
    try:
        preflight = cli_run(binary, "Respond briefly with the token `LTM_PREFLIGHT_OK`", run_root / "preflight-workspace",
                            model=options.model, effort=options.effort, timeout=90)
    except Exception as exc:
        preflight = {"ok": False, "error": "codex_preflight_exception", "errorType": type(exc).__name__}
    write_raw_result(run_root / "preflight.json", {"identity": identity, "output": preflight})
    records = []
    # This verifies authenticated model transport, not instruction-following quality.
    # Punctuation around a successful reply must not abort the actual battery.
    if not preflight.get("ok") or not preflight.get("reply", "").strip():
        write_raw_result(out, _payload(metadata, records, selected, preflight, failure="codex_network_preflight_failed"))
        print("Codex network preflight failed; no cases started", flush=True)
        return 3
    with patch.dict(os.environ, {NETWORK_PREFLIGHT_MARKER: "1"}):
        for case in selected:
            try:
                record = _run_case(case, options, binary, run_root, catalog_hash, cli_run, fixture_factory, battery)
            except Exception as exc:
                record = {"id": case["id"], "inputs": case["prompts"], "output": {}, "durationSeconds": 0,
                          "automaticPass": False, "infrastructureError": "fixture_or_runtime_failed",
                          "errorType": type(exc).__name__}
            records.append(record)
            write_raw_result(out, _payload(metadata, records, selected, preflight))
            print(f"{'PASS' if record['automaticPass'] else 'FAIL'} {case['id']} · {record['durationSeconds']:.1f}s", flush=True)
            # Transport/auth failures cannot be relabelled as eight independent quality failures.
            if record.get("infrastructureError"):
                write_raw_result(out, _payload(metadata, records, selected, preflight, failure=record["infrastructureError"]))
                break
    print(str(out), flush=True)
    return 0 if len(records) == len(selected) and all(row["automaticPass"] for row in records) else 1


def _run_case(case, options, binary, run_root, catalog_hash, cli_run, fixture_factory, battery=None):
    if os.environ.get(NETWORK_PREFLIGHT_MARKER) != "1":
        raise RuntimeError("Use agent_eval_launcher.py codex-mcp; network preflight has not passed")
    directory = run_root / re.sub(r"[^A-Za-z0-9_-]", "-", case["id"])
    battery = battery or _battery(getattr(options, "battery", "queries"), options.date)
    with fixture_factory(directory / "fixture", port=4457, fault=case.get("fault"), today=options.date) as fixture:
        if fixture.catalog_sha256 != catalog_hash:
            raise RuntimeError("Tool catalog changed between preflight and case")
        oracle = fixture.ground_truth()  # Direct immutable store snapshot; no Jira/cache reads.
        prompt = battery.SYSTEM_CONTEXT + f"\n현재 fixture 날짜: {options.date} (Asia/Seoul).\n\n사용자 요청:\n" + "\n".join(case["prompts"])
        output = cli_run(binary, prompt, directory / "workspace", model=options.model, effort=options.effort,
                         mcp_url=fixture.url, token=fixture.token, timeout=options.timeout, schema=battery.OUTPUT_SCHEMA)
        output["mcpToolCalls"] = list(fixture.tool_calls)
        automatic = battery.check(case, output, fixture.tool_calls, oracle)
        isolation = fixture.summary()
        upstream_calls = list(fixture.upstream_calls)
        warm = None
        if getattr(options, "warm_replay", False) and output.get("ok"):
            from tools.codex_mcp_eval.warm_replay import replay
            try:
                warm = replay(fixture, output["mcpToolCalls"])
            except Exception as exc:
                # Cache diagnostics must not discard the paid answer or its measured calls.
                warm = {"measuredAfterAnswer": True, "error": "warm_replay_failed",
                        "errorType": type(exc).__name__}
            after = fixture.summary()
            isolation["worldUnchanged"] &= after["worldUnchanged"]
            isolation["providerStoreUnchanged"] &= after["providerStoreUnchanged"]
        if not isolation["worldUnchanged"] or not isolation["providerStoreUnchanged"]:
            automatic["failures"].append("read_only_world_mutated")
            automatic["pass"] = False
        # The source snapshot is retained for direct review outside the empty Codex workspace.
        write_raw_result(directory / "oracle.json", oracle)
        write_raw_result(directory / "events.json", {"events": output.get("events", [])})
        record = {"id": case["id"], "inputs": case["prompts"], "output": output,
                  "durationSeconds": output["durationSeconds"], "automaticPass": automatic["pass"],
                  "automaticContract": automatic, "isolation": isolation,
                  "upstreamCalls": upstream_calls, "warmReplay": warm,
                  "sourceSnapshot": str((directory / "oracle.json").relative_to(RAW_RESULT_ROOT))}
        if output.get("error") in {"codex_timeout", "codex_execution_failed"}:
            record["infrastructureError"] = output["error"]
        return record
