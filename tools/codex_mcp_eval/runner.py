"""One versioned Codex/HTTP-MCP battery with explicit qualification design."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from uuid import uuid4
from datetime import date, datetime
from pathlib import Path
from functools import partial
from types import SimpleNamespace
from unittest.mock import patch

from tools.agent_eval_isolation import NETWORK_PREFLIGHT_MARKER
from tools.agent_eval_protocol import (RAW_RESULT_ROOT, build_run_metadata,
    raw_result_path, reserve_raw_result_path, summarize_latency, write_raw_result)
from tools.codex_mcp_eval import cli
from tools.codex_mcp_eval.cases import (BATTERY_VERSION, CASES, CHECKER_DEPENDENCIES, OUTPUT_SCHEMA,
    PROMPT_VERSION, SUITE, SYSTEM_CONTEXT, check, review_contract)
from tools.codex_mcp_eval.fixture import EvaluationFixture
from tools.codex_mcp_eval.attempts import (snapshot_attempt, mark_infrastructure_failure, persist_result,
                                         settle_attempt, observer_offsets)
from tools.codex_mcp_eval.metrics import (file_metrics, model_metrics, protocol_metrics,
                                        upstream_summary as _upstream_summary)

GATE = {"automaticPassRate": 1.0, "materialFalseFacts": 0, "unauthorizedWrites": 0,
        "zeroMajorSemanticElements": ["uncertainty_disclosure", "progress_denominator"],
        "humanMeanMinimum": 4.5, "humanScale": 5, "directRawReviewRequired": True,
        "qualificationClaim": False}


def _battery(name, today):
    if name == "etl":
        from tools.codex_mcp_eval.etl import cases as etl_cases
        from tools.codex_mcp_eval.etl.fixture import EtlFixture
        return SimpleNamespace(**{key: getattr(etl_cases, key) for key in (
            "BATTERY_VERSION", "CHECKER_DEPENDENCIES", "OUTPUT_SCHEMA", "PROMPT_VERSION", "SUITE",
            "SYSTEM_CONTEXT", "check", "schema_for_case")}, review_contract=lambda: etl_cases.review_contract(today),
            CASES=etl_cases.materialize_cases(today), fixture=EtlFixture)
    if name == "work":
        from tools.codex_mcp_eval import work_cases
        from tools.codex_mcp_eval.work_fixture import WorkFixture
        return SimpleNamespace(**{key: getattr(work_cases, key) for key in (
            "BATTERY_VERSION", "CHECKER_DEPENDENCIES", "OUTPUT_SCHEMA", "PROMPT_VERSION", "SUITE",
            "SYSTEM_CONTEXT", "check", "schema_for_case")}, review_contract=lambda: work_cases.review_contract(today),
            CASES=work_cases.materialize_cases(today), fixture=WorkFixture)
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
    parser = argparse.ArgumentParser(description="Codex + read-only LTM HTTP MCP quality battery")
    parser.add_argument("--run-group", default=datetime.now().strftime("%Y%m%d-%H%M%S-codex-mcp"))
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--battery", choices=("queries", "reporting", "identities", "work", "etl"), default="queries")
    parser.add_argument("--etl-phase", choices=("exploratory", "pilot", "formal"), default="exploratory")
    parser.add_argument("--etl-arm", choices=("A", "B", "C"),
                        help="Record the actual candidate arm; C exposes enhanced skills, A/B baseline skills")
    parser.add_argument("--skill-condition", choices=("baseline", "candidate"), default="baseline")
    parser.add_argument("--skill-transport", choices=("default", "fallback"), default="default")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--research-files", action="store_true",
                        help="Expose the same private investigation-file MCP capability to every arm")
    parser.add_argument("--recovery-scenario", choices=("between-pages", "after-response", "record-chunk",
                                                      "corrupt-progress", "expired-snapshot", "source-changed",
                                                      "search-pagination", "comments-pagination", "history-pagination",
                                                      "group-boundary", "client-restart", "backend-restart"))
    parser.add_argument("--recovery-mode", choices=("uninterrupted", "restarted"), default="restarted")
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--warm-replay", action="store_true",
                        help="After each measured answer, separately replay successful read calls over HTTP to measure cache reuse")
    parser.add_argument("--model", default=cli.MODEL)
    parser.add_argument("--effort", default=cli.EFFORT)
    parser.add_argument("--repeat-index", type=int, default=1)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--run-kind", choices=("exploratory", "qualification"), default="exploratory")
    parser.add_argument("--candidate-order-index", type=int)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--codex", default=None)
    options = parser.parse_args(argv)
    if options.repetitions is None:
        options.repetitions = options.repeat_index if options.run_kind == "exploratory" else 1
    if options.battery not in {"work", "etl"} and (options.skill_condition != "baseline" or options.skill_transport != "default"):
        parser.error("Skill comparison options require the work or ETL battery")
    if options.research_files and options.battery not in {"work", "etl"}:
        parser.error("research-files requires the work or ETL battery")
    if options.battery != "etl" and (options.etl_arm or options.etl_phase != "exploratory"):
        parser.error("ETL phase/arm options require the ETL battery")
    if options.battery == "etl":
        from tools.codex_mcp_eval.etl.cases import REFERENCE_DATE
        from tools.codex_mcp_eval.etl.qualification import expected_case_ids, validate_selection
        from tools.codex_mcp_eval.etl.recovery import RECOVERY_SCENARIOS
        if options.date != REFERENCE_DATE or options.model != "gpt-6.1-sol" or options.effort != "high":
            parser.error("ETL freezes date=2026-11-10, model=gpt-6.1-sol and effort=high")
        options.research_files = True
        if options.etl_arm:
            if options.etl_arm in {"A", "B"} and options.skill_condition != "baseline":
                parser.error("ETL A/B require baseline skills")
            options.skill_condition = "candidate" if options.etl_arm == "C" else "baseline"
        if options.recovery_scenario:
            if options.recovery_scenario not in RECOVERY_SCENARIOS:
                parser.error("ETL requires an ETL recovery scenario")
            case_id = "ETL-RECOVERY-" + options.recovery_scenario
            if options.cases and options.cases != [case_id]:
                parser.error("ETL recovery uses the exact selected scenario case")
            options.cases = [case_id]
            if options.etl_phase == "pilot" or options.etl_arm == "B":
                parser.error("ETL recovery is outside pilot and B's representative subset")
        elif options.etl_phase != "exploratory":
            if not options.etl_arm:
                parser.error("ETL pilot/formal requires the recorded arm")
            options.cases = options.cases or list(expected_case_ids(options.etl_phase, options.etl_arm))
            try:
                validate_selection(options.etl_phase, options.etl_arm, options.cases)
            except ValueError as exc:
                parser.error(str(exc))
        if options.etl_phase == "pilot" and options.run_kind != "exploratory":
            parser.error("ETL pilot is separate exploratory evidence")
        if options.etl_phase == "formal" and options.run_kind != "qualification":
            parser.error("ETL formal requires qualification metadata")
        if options.etl_phase == "formal" and not options.etl_arm:
            parser.error("ETL formal requires the recorded arm")
        if options.etl_phase == "exploratory" and options.run_kind == "qualification":
            parser.error("ETL qualification requires the exact formal schedule")
    elif options.recovery_scenario:
        if options.battery != "work" or options.skill_condition != "candidate":
            parser.error("Recovery evaluation requires --battery work --skill-condition candidate")
        if options.recovery_scenario not in {"between-pages", "after-response", "record-chunk",
                                              "corrupt-progress", "expired-snapshot", "source-changed"}:
            parser.error("Work battery requires a work recovery scenario")
        if options.cases and options.cases != ["WORK-R02-boundary"]:
            parser.error("Recovery evaluation uses WORK-R02-boundary only")
        options.cases = ["WORK-R02-boundary"]
    if options.battery in {"work", "etl"}:
        options.output_root = (options.output_root or Path(tempfile.gettempdir()) / "ltm-evaluation-results").resolve()
        if options.output_root.is_relative_to(Path(__file__).resolve().parents[2]):
            parser.error("Work evaluation output must stay outside the repository")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", options.run_group):
        parser.error("run-group must contain only letters, digits, _ or -")
    try:
        date.fromisoformat(options.date)
    except ValueError:
        parser.error("date must be YYYY-MM-DD")
    if not 1 <= options.repeat_index <= options.repetitions or not 10 <= options.timeout <= 3600:
        parser.error("repeat-index must be 1..repetitions and timeout must be 10..3600 seconds")
    if options.candidate_order_index is not None and options.candidate_order_index < 1:
        parser.error("candidate-order-index must be positive")
    if options.run_kind == "qualification":
        if options.repetitions < 5 or options.candidate_order_index is None:
            parser.error("Qualification requires at least 5 repetitions and a recorded candidate-order-index")
        if options.cases and not options.recovery_scenario and options.battery != "etl":
            parser.error("Qualification requires the full battery without --cases")
    if options.battery == "etl" and options.recovery_scenario:
        from tools.codex_mcp_eval.etl.recovery import recovery_cases
        available = {case["id"] for case in recovery_cases()}
    else:
        available = {case["id"] for case in _battery(options.battery, options.date).CASES}
    if options.cases and (set(options.cases) - available or len(set(options.cases)) != len(options.cases)):
        parser.error("cases must be unique IDs from the selected battery")
    return options


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _etl_cohort(battery, options):
    """Declare a scheduled six-case cohort as such, preserving the 28-case base.

    Formal A/C use the full battery; B has an explicit representative suite and
    pilot evidence has its own suite. Focused exploratory runs stay focused.
    """
    if options.battery != "etl" or options.etl_phase == "exploratory" or options.recovery_scenario:
        return battery
    from tools.codex_mcp_eval.etl.qualification import expected_case_ids, SCHEDULE_VERSION
    ids = expected_case_ids(options.etl_phase, options.etl_arm)
    cases = [row for row in battery.CASES if row["id"] in ids]
    if len(cases) == len(battery.CASES):
        return battery
    def review():
        suite, specs = battery.review_contract()
        return suite, {key: specs[key] for key in ids}
    return SimpleNamespace(**{**vars(battery), "CASES": cases,
        "SUITE": battery.SUITE + ("-pilot" if options.etl_phase == "pilot" else "-representative"),
        "review_contract": review, "CHECKER_DEPENDENCIES": (*battery.CHECKER_DEPENDENCIES, SCHEDULE_VERSION, ids)})


def _payload(metadata, records, selected, preflight, *, failure=None):
    durations = [row["durationSeconds"] for row in records]
    metrics, coverage = model_metrics(records)
    cumulative, cumulative_coverage = model_metrics([
        {"output": preflight, "durationSeconds": preflight.get("durationSeconds", 0)}, *records])
    ltm_calls = sum(len(row["output"].get("mcpToolCalls") or []) for row in records)
    file_calls = sum((row.get("fileMetrics") or {}).get("calls", 0) for row in records)
    wire_metrics = protocol_metrics(records)
    payload = {
        "suite": metadata["battery"]["name"], "evaluation": metadata, "qualityGate": GATE,
        "humanReview": {"status": "pending", "notReplacedByAutomaticChecks": True},
        "executionComplete": len(records) == len(selected), "preflight": preflight,
        "metrics": metrics, "usageCoverage": coverage,
        "cumulativeModelMetrics": cumulative, "cumulativeUsageCoverage": cumulative_coverage,
        "metricAvailability": {"modelCalls": False, "costUsd": False, "timeToFirstToken": False,
                               "upstreamCountIncludes": "observed case provider dispatches, including pages and failed calls; internal transport/auth coverage is declared by fixture observerCoverage; preflight/setup excluded",
                               "discoveryProtocolRequests": wire_metrics["observed"],
                               "mcpCountIncludes": "toolCalls counts handler invocations; mcpWire counts guarded HTTP dispatches, including validation/capacity failures, with discovery separate",
                               "cumulativeModelMetricsIncludes": "network preflight and every case phase, including failed/interrupted attempts; diagnostic replay excluded",
                               "latencyIncludes": "Codex process startup + model + MCP; fixture setup excluded"},
        "summary": {"completed": len(records), "total": len(selected),
                    "automaticPass": sum(row["automaticPass"] for row in records),
                    "toolCalls": ltm_calls + file_calls, "ltmToolCalls": ltm_calls, "fileToolCalls": file_calls,
                    "mcpWire": wire_metrics,
                    "upstream": _upstream_summary(records),
                    "jiraApi": _upstream_summary(records, service="jira"),
                    "confluenceApi": _upstream_summary(records, service="confluence"),
                    "latency": summarize_latency(durations) if durations else None},
        "cases": records,
    }
    applications = [row["skillApplication"] for row in records if "skillApplication" in row]
    if applications:
        payload["summary"]["skillAdoption"] = {
            "offeredCases": sum(row["offered"] for row in applications),
            "entrypointReadCases": sum(row["entrypointRead"] for row in applications),
            "resourceReadCases": sum(row["candidateResourceReads"] > 0 for row in applications),
            "qualityMetric": False, "comparisonPolicy": "intention-to-treat-no-nonadopter-exclusion"}
    if failure:
        payload.update(infrastructureError=failure, executionComplete=False)
    return payload


def launch(argv=None, *, cli_run=None, fixture_factory=None, identity_fn=None):
    # Runtime databases, secrets, model cwd and output-schema scratch are always
    # disposable. Paid raw evidence and investigation files keep their separately
    # authorized durable destination, even when fixture or model setup fails.
    with tempfile.TemporaryDirectory() as runtime_root:
        return _launch(argv, cli_run=cli_run, fixture_factory=fixture_factory,
                       identity_fn=identity_fn, runtime_root=Path(runtime_root))


def _launch(argv=None, *, cli_run=None, fixture_factory=None, identity_fn=None, runtime_root):
    options = parse_args(argv)
    battery = _etl_cohort(_battery(options.battery, options.date), options)
    if options.recovery_scenario:
        if options.battery == "etl":
            from tools.codex_mcp_eval.etl.recovery_runner import recovery_battery
        else:
            from tools.codex_mcp_eval.recovery_runner import recovery_battery
        battery = recovery_battery(battery, options.date, options.recovery_scenario, options.recovery_mode)
    cli_run, fixture_factory, identity_fn = cli_run or cli.run, fixture_factory or battery.fixture, identity_fn or cli.identity
    if options.battery in {"work", "etl"}:
        fixture_factory = partial(fixture_factory, skill_condition=options.skill_condition,
                                  skill_transport=options.skill_transport)
    if options.battery == "etl":
        from tools.codex_mcp_eval.etl.world import build_etl_world
        # Immutable authored data is built once; every case still gets its own
        # public world/provider/cache. No branch checkout or source copy exists.
        battery.world_bundle = build_etl_world()
        fixture_factory = partial(fixture_factory, bundle=battery.world_bundle)
    runtime_fixture = fixture_factory
    def fixture_factory(_artifact_directory, **kwargs):
        return runtime_fixture(runtime_root / "fixtures" / uuid4().hex, **kwargs)
    runtime_cli = cli_run
    def cli_run(binary, prompt, workspace, **kwargs):
        return runtime_cli(binary, prompt, runtime_root / "workspaces" / uuid4().hex / Path(workspace).name, **kwargs)
    if options.research_files and (not options.recovery_scenario or options.battery == "etl"):
        original_factory = fixture_factory
        def fixture_factory(run_dir, **kwargs):
            if "files_root" in kwargs:
                return original_factory(run_dir, **kwargs)
            files_root = Path(run_dir).parent / (Path(run_dir).name + "-investigation")
            files_root.mkdir(parents=True, exist_ok=False)
            return original_factory(run_dir, files_root=files_root, **kwargs)
    binary = options.codex or cli.executable()
    identity = identity_fn(binary, model=options.model, effort=options.effort)
    selected = [case for case in battery.CASES if not options.cases or case["id"] in options.cases]
    artifact_root = options.output_root or RAW_RESULT_ROOT
    run_root = artifact_root / options.run_group / f"attempt-{options.repeat_index:02d}"
    # Reserve before preflight or artifact writes; an existing attempt is never
    # reused even if its later protocol metadata/claim has not been generated.
    run_root.mkdir(parents=True, exist_ok=False)
    skills = {}
    # Catalog/setup are part of preflight, not measured case time, and cannot warm case caches.
    with fixture_factory(run_root / "preflight-fixture", port=4457, today=options.date) as fixture:
        conformance = fixture.assert_conformance()
        catalog_hash = fixture.catalog_sha256
        scope = {"projects": list(fixture.settings.search_jira_projects),
                 "spaces": list(fixture.settings.search_confluence_spaces)}
        if options.battery in {"work", "etl"}:
            skills = {"skillCondition": options.skill_condition, "skillTransport": options.skill_transport,
                      "skillManifestSha256": _hash(fixture.runtime.skill_catalog.list_skills()),
                      "runtimeInstructionsSha256": _hash(fixture.runtime.server.instructions)}
            if options.recovery_scenario:
                skills.update(recoveryScenario=options.recovery_scenario, recoveryMode=options.recovery_mode,
                              fileCapability="rooted-evaluation-only-mcp-v1")
            elif options.research_files:
                skills.update(fileCapability="rooted-evaluation-only-mcp-v1")
            # Exposure is the treatment; source-conformance is shared by both arms.
            conformance = {key: value for key, value in conformance.items()
                           if key not in {"skillCondition", "skillTransport"}}
        if options.battery == "etl":
            from tools.agent_eval_protocol import git_snapshot
            from tools.codex_mcp_eval.etl.qualification import REPRESENTATIVE_CASE_IDS, SCHEDULE_VERSION, SCHEDULE_STATUS
            from tools.codex_mcp_eval.etl.skills import DEPLOYED_COMMIT
            backend_commit = git_snapshot()["commit"]
            skills.update(etlArm=options.etl_arm, etlPhase=options.etl_phase,
                backendCommit=backend_commit, scheduleVersion=SCHEDULE_VERSION, scheduleStatus=SCHEDULE_STATUS,
                representativeCaseIds=list(REPRESENTATIVE_CASE_IDS), researchFilesEqualAcrossArms=True,
                researchSkillsOffered=True,
                researchSkillSourceCommit=DEPLOYED_COMMIT if options.skill_condition == "baseline" else backend_commit,
                dataManifestSha256=conformance["dataManifest"]["data_sha256"])
    identity.update(provider="codex-chatgpt", toolCatalogSha256=catalog_hash, **skills,
                    outputSchemaSha256=_hash(battery.OUTPUT_SCHEMA))
    if hasattr(battery, "schema_for_case"):
        identity["caseOutputSchemaSha256"] = {case["id"]: _hash(battery.schema_for_case(case)) for case in battery.CASES}
    suite_review, case_specs = battery.review_contract()
    environment = {"LTM_EVAL_RUN_KIND": options.run_kind, "LTM_EVAL_RUN_GROUP_ID": options.run_group,
        "LTM_EVAL_REPEAT_INDEX": str(options.repeat_index), "LTM_EVAL_REPETITIONS": str(options.repetitions),
        "LTM_EVAL_CANDIDATE_ORDER_INDEX": str(options.candidate_order_index or "unrecorded"),
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
                               "modelInvocationTimeoutSeconds": options.timeout, "preflightTimeoutSeconds": 90,
                               "fixtureConformance": conformance,
                               "outputContract": "external-json-answer-v1", "gate": GATE,
                               **({"warmReplay": "successful-reads-three-http-repetitions-after-answer"}
                                  if options.warm_replay else {})})
    if options.run_kind == "qualification" and not metadata["qualificationEligible"]:
        raise RuntimeError("Invalid qualification: " + "; ".join(metadata["qualificationIneligibilityReasons"]))
    out = reserve_raw_result_path(raw_result_path(battery.SUITE, metadata, root=artifact_root))
    # No secret, expected answer, source fixture or repository file is put in the model's cwd.
    try:
        preflight = cli_run(binary, "Respond briefly with the token `LTM_PREFLIGHT_OK`", run_root / "preflight-workspace",
                            model=options.model, effort=options.effort, timeout=90)
    except Exception as exc:
        preflight = {"ok": False, "error": "codex_preflight_exception", "errorType": type(exc).__name__}
    if not persist_result(run_root / "preflight.json", {"identity": identity, "output": preflight},
                          writer=write_raw_result):
        return 3
    records = []
    # This verifies authenticated model transport, not instruction-following quality.
    # Punctuation around a successful reply must not abort the actual battery.
    if not preflight.get("ok") or not preflight.get("reply", "").strip():
        persist_result(out, _payload(metadata, records, selected, preflight, failure="codex_network_preflight_failed"),
                       writer=write_raw_result)
        print("Codex network preflight failed; no cases started", flush=True)
        return 3
    with patch.dict(os.environ, {NETWORK_PREFLIGHT_MARKER: "1"}):
        for case in selected:
            try:
                record = _run_case(case, options, binary, run_root, catalog_hash, cli_run, fixture_factory, battery,
                                   artifact_root=artifact_root)
            except Exception as exc:
                record = {"id": case["id"], "inputs": case["prompts"], "output": {}, "durationSeconds": 0,
                          "automaticPass": False, "infrastructureError": "fixture_or_runtime_failed",
                          "errorType": type(exc).__name__}
            if options.battery in {"work", "etl"}:
                reads = [row for row in record.get("skillReads", []) if row["uri"].startswith(
                    ("skill://ltm-work-research/", "skill://ltm-work-reporting/"))]
                record["skillApplication"] = {"offered": options.battery == "etl" or options.skill_condition == "candidate",
                    "entrypointRead": any(row["uri"].endswith("/SKILL.md") for row in reads),
                    "candidateResourceReads": len(reads),
                    "attribution": "observed-resource-reads" if reads else (
                        "exposure-only" if options.skill_condition == "candidate" else "baseline"),
                    "qualityMetric": False}
            records.append(record)
            if not persist_result(out, _payload(metadata, records, selected, preflight,
                    failure=record.get("infrastructureError")), writer=write_raw_result):
                print("Evaluation result write failed; retained persistence-failure sidecar", flush=True)
                return 1
            print(f"{'PASS' if record['automaticPass'] else 'FAIL'} {case['id']} · {record['durationSeconds']:.1f}s", flush=True)
            # Transport/auth failures cannot be relabelled as eight independent quality failures.
            if record.get("infrastructureError"):
                break
    print(str(out), flush=True)
    return 0 if len(records) == len(selected) and all(row["automaticPass"] for row in records) else 1


def _run_case(case, options, binary, run_root, catalog_hash, cli_run, fixture_factory, battery=None, *, artifact_root=None):
    if os.environ.get(NETWORK_PREFLIGHT_MARKER) != "1":
        raise RuntimeError("Use agent_eval_launcher.py codex-mcp; network preflight has not passed")
    if getattr(options, "recovery_scenario", None):
        if getattr(options, "battery", None) == "etl":
            from tools.codex_mcp_eval.etl.recovery_runner import run_recovery_case
            return run_recovery_case(case, options, binary, run_root, cli_run, battery,
                artifact_root=artifact_root or RAW_RESULT_ROOT, fixture_factory=fixture_factory,
                bundle=getattr(battery, "world_bundle", None))
        else:
            from tools.codex_mcp_eval.recovery_runner import run_recovery_case
            return run_recovery_case(case, options, binary, run_root, cli_run, battery,
                                     artifact_root=artifact_root or RAW_RESULT_ROOT)
    directory = run_root / re.sub(r"[^A-Za-z0-9_-]", "-", case["id"])
    battery = battery or _battery(getattr(options, "battery", "queries"), options.date)
    record = {"id": case["id"], "inputs": case["prompts"], "output": {},
              "durationSeconds": 0, "automaticPass": False, "modelAttempted": False}
    stage, fixture, trace_limits = "fixture_setup_failed", None, None
    try:
        with fixture_factory(directory / "fixture", port=4457, fault=case.get("fault"), today=options.date) as fixture:
            if fixture.catalog_sha256 != catalog_hash:
                raise RuntimeError("Tool catalog changed between preflight and case")
            oracle = fixture.ground_truth()
            prompt = battery.SYSTEM_CONTEXT + f"\n현재 fixture 날짜: {options.date} (Asia/Seoul).\n\n사용자 요청:\n" + "\n".join(case["prompts"])
            stage, record["modelAttempted"] = "model_invocation_failed", True
            output = cli_run(binary, prompt, directory / "workspace", model=options.model, effort=options.effort,
                mcp_url=fixture.url, token=fixture.token, timeout=options.timeout,
                **({"files_url": fixture.files_url} if getattr(fixture, "files_url", None) else {}),
                schema=battery.schema_for_case(case) if hasattr(battery, "schema_for_case") else battery.OUTPUT_SCHEMA)
            # Capture paid evidence before any checker, summary, writer or teardown.
            record["output"] = output
            stage = "attempt_snapshot_failed"
            snapshot_attempt(record, fixture, output)
            stage = "attempt_checkpoint_failed"
            write_raw_result(directory / "model-attempt.json", record)
            stage = "observer_settlement_failed"
            settle_attempt(record, fixture)
            stage = "fixture_summary_failed"
            record["isolation"] = isolation = fixture.summary()
            stage = "checker_failed"
            automatic = battery.check(case, record["output"], record["output"].get("mcpToolCalls", []), oracle)
            record.update(automaticContract=automatic, automaticPass=automatic["pass"], warmReplay=None)
            if getattr(options, "warm_replay", False) and output.get("ok"):
                from tools.codex_mcp_eval.warm_replay import replay
                if record["settlement"].get("complete") is False:
                    record["warmReplay"] = {"measuredAfterAnswer": True, "skipped": "paid_observers_unsettled"}
                else:
                    trace_limits = observer_offsets(fixture)
                    record["paidTraceLimits"] = trace_limits
                    try:
                        record["warmReplay"] = replay(fixture, record["output"]["mcpToolCalls"])
                    except Exception as exc:
                        record["warmReplay"] = {"measuredAfterAnswer": True, "error": "warm_replay_failed",
                                                "errorType": type(exc).__name__}
                stage = "fixture_summary_failed"
                after = fixture.summary()
                isolation["worldUnchanged"] &= after["worldUnchanged"]
                isolation["providerStoreUnchanged"] &= after["providerStoreUnchanged"]
            if not isolation["worldUnchanged"] or not isolation["providerStoreUnchanged"]:
                automatic["failures"].append("read_only_world_mutated")
                automatic["pass"] = record["automaticPass"] = False
            stage = "source_snapshot_write_failed"
            write_raw_result(directory / "oracle.json", oracle)
            record["sourceSnapshot"] = str((directory / "oracle.json").relative_to(artifact_root or RAW_RESULT_ROOT))
            stage = "events_write_failed"
            write_raw_result(directory / "events.json", {"events": output.get("events", [])})
            if output.get("error") in {"codex_timeout", "codex_execution_failed", "codex_postprocess_failed"}:
                record["infrastructureError"] = output["error"]
            stage = "fixture_teardown_failed"
    except Exception as exc:
        if fixture is not None and not record.get("attemptCaptured"):
            try:
                snapshot_attempt(record, fixture)
            except Exception as capture_error:
                mark_infrastructure_failure(record, capture_error, "attempt_snapshot_failed")
        mark_infrastructure_failure(record, exc, stage)
        try:
            write_raw_result(directory / "model-attempt-failure.json", record)
        except Exception as write_error:
            mark_infrastructure_failure(record, write_error, "failure_checkpoint_failed")
    finally:
        if fixture is not None and record.get("modelAttempted"):
            try:
                settle_attempt(record, fixture, limits=trace_limits, after_teardown=True)
                write_raw_result(directory / "model-attempt-final.json", record)
            except Exception as exc:
                mark_infrastructure_failure(record, exc, "final_attempt_capture_failed")
    return record
