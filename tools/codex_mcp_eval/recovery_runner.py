"""Two ephemeral model sessions share only proven investigation files, not Jira cache."""
from __future__ import annotations

import copy
import os
from types import SimpleNamespace

from tools.agent_eval_protocol import write_raw_result
from tools.agent_eval_isolation import NETWORK_PREFLIGHT_MARKER
from tools.codex_mcp_eval.recovery_fixture import RecoveryFixture
from tools.codex_mcp_eval.work_recovery import RecoveryController, complete_search_membership, persisted_calls
from tools.codex_mcp_eval.work_recovery import SAVED_RESPONSE_DEPENDENCIES
from tools.codex_mcp_eval.recovery_checkpoint import CHECKPOINT_DEPENDENCIES
from tools.codex_mcp_eval.reporting_fixture import periods
from tools.codex_mcp_eval.metrics import aggregate_usage, file_metrics
from tools.codex_mcp_eval.attempts import (snapshot_attempt, mark_infrastructure_failure,
                                         combine_phase_attempts, settle_attempt)

RECOVERY_JQL = ("labels = work_research_fixture AND key in "
                "(DL-94001,DL-94002,DL-94006,DL-94007,DL-94017,DL-94018) ORDER BY key ASC")
RECOVERY_PROMPT = f"""다음 조사만 수행하세요. Jira 검색 조건은
{RECOVERY_JQL}
이며 검색 페이지 크기는 2입니다. 질문의 월 기간에 신규 적재한 고유 테이블 수를 조사하세요.
집계 모집단은 이 JQL의 전체 검색 결과입니다. IN의 key들은 검색 후보 범위이며 각각을 필수
개별 조회 대상으로 지정한 것이 아닙니다. 정상적으로 끝까지 조회한 검색에 반환되지 않은 후보를
모집단에 추가하지 마세요. 검색 실패·누락·불완전 응답은 공개하고 전체 건수를 미확정으로 남기세요.
전체 댓글의 취소·제외, 같은 작업의 재보고, 기간 밖 작업을 반영하고 운영 결정 문서와도 대조하세요.
resultKeys는 위 검색 범위에서 판단에 사용한 key 오름차순이며 reportedTotal은 고유 테이블 수입니다.
research_files는 이 조사의 전용 로컬 파일 기능입니다. 서버의 조사·보고 가이드와 저장·재개 참고를
필요에 따라 읽고 실제 응답 구조를 evidence/에 보존한 다음 progress를 갱신하세요.
manifest와 resume도 유지하세요. 파일·shell·외부 서비스는 이 명시적 파일 기능 외에는 사용하지 마세요.
출력의 progress는 사용자가 요청한 업무 진척률입니다. 조사 수집 완료율과 구분하고,
업무 진척률을 요청하지 않은 질문에서는 null로 두세요. 수집 진행은 조사 파일에 기록하세요.
"""


def recovery_prompt(today):
    start, end = periods(today)["month"]
    return RECOVERY_PROMPT + f"\n집계 기간: {start} 00:00 이상 {end} 00:00 미만, Asia/Seoul."


def recovery_canonical_jql():
    from app.jira.jql import compile_jql
    # WorkFixture fixes the configured search scope to DL; match the actual MCP
    # canonical result, including that guard, rather than trusting request arguments.
    where, order = RECOVERY_JQL.split(" ORDER BY ")
    return compile_jql(f'project in ("DL") AND ({where}) ORDER BY {order}').canonical


def recovery_battery(base, today, scenario, mode):
    from tools.codex_mcp_eval import work_cases
    revision = 2 if scenario == "source-changed" else 1
    case = copy.deepcopy(next(c for c in base.CASES if c["id"] == "WORK-R02-boundary"))
    case["prompts"] = [recovery_prompt(today)]
    def review():
        suite, specs = work_cases.review_contract(today, source_revision=revision)
        spec = specs[case["id"]]
        spec["goal"] = case["prompts"][0]
        expected = spec["elements"][0]["expected"]
        expected["documents"] = ["운영 결정"]
        expected["populationBasis"] = "complete-returned-JQL-membership; IN keys are candidates, not required individual reads"
        expected["recovery"] = {"scenario": scenario, "sourceRevision": revision,
            "cachePolicy": "new-private-database-every-phase", "treatmentField": "run.externalRuntime.recoveryMode",
            "modes": {"uninterrupted": {"phaseCount": 1, "initialSourceRevision": revision},
                      "restarted": {"phaseCount": 2, "initialSourceRevision": 1,
                                    "originalDialogueAvailable": False, "requiresSavedFilesRead": True}}}
        return suite, {case["id"]: spec}
    return SimpleNamespace(**{**vars(base), "SUITE": "codex-mcp-work-recovery", "CASES": [case],
        "BATTERY_VERSION": "4.0.1", "PROMPT_VERSION": "codex-mcp-work-recovery-v3", "SYSTEM_CONTEXT": "", "review_contract": review,
        "CHECKER_DEPENDENCIES": (*base.CHECKER_DEPENDENCIES, RECOVERY_PROMPT, recovery_prompt,
                                  RecoveryController, persisted_calls, complete_search_membership,
                                  *SAVED_RESPONSE_DEPENDENCIES, *CHECKPOINT_DEPENDENCIES,
                                  RECOVERY_JQL, recovery_canonical_jql, scenario)})


def _phase(directory, index, options, binary, cli_run, files_root, controller, revision, fixture_factory):
    record = {"output": {}, "durationSeconds": 0, "modelAttempted": False,
              "sourceRevision": revision, "cachePolicy": "new-private-database-every-phase"}
    oracle, fixture, stage = {}, None, "recovery_fixture_setup_failed"
    try:
        with fixture_factory(directory / f"fixture-{index}", files_root=files_root, controller=controller,
                             port=4457, today=options.date, skill_condition="candidate",
                             skill_transport=options.skill_transport, source_revision=revision) as fixture:
            oracle = fixture.ground_truth()
            oracle["cases"]["WORK-R02-boundary"]["documents"] = ["운영 결정"]
            prompt = recovery_prompt(options.date)
            if index:
                prompt += ("\n새 세션입니다. 이전 대화는 없습니다. 조사 파일을 먼저 읽고 중단한 조사를 재개하세요. "
                           "resume가 없거나 손상됐으면 목록·manifest·정상 근거에서 상태를 복구하세요. "
                           "현재 근거가 달라졌을 수 있으므로 가이드에 따라 재검증하세요.")
            record["prompt"] = prompt
            stage, record["modelAttempted"] = "recovery_model_invocation_failed", True
            output = cli_run(binary, prompt, directory / f"workspace-{index}", model=options.model,
                effort=options.effort, mcp_url=fixture.url, files_url=fixture.files_url, token=fixture.token,
                timeout=options.timeout, schema=options.work_output_schema,
                **({"stop_event": controller.stop} if controller else {}))
            record["output"] = output
            stage = "recovery_attempt_snapshot_failed"
            snapshot_attempt(record, fixture, output)
            stage = "recovery_attempt_checkpoint_failed"
            write_raw_result(directory / f"phase-{index}-attempt.json", record)
            stage = "recovery_observer_settlement_failed"
            settle_attempt(record, fixture)
            stage = "recovery_summary_failed"
            record["isolation"] = fixture.summary()
            record["persistedCalls"] = persisted_calls(fixture.files, fixture.tool_calls)
            stage = "recovery_source_write_failed"
            write_raw_result(directory / f"phase-{index}-oracle.json", oracle)
            stage = "recovery_events_write_failed"
            write_raw_result(directory / f"phase-{index}-events.json", {"events": output.get("events", [])})
            stage = "recovery_fixture_teardown_failed"
    except Exception as exc:
        if fixture is not None and not record.get("attemptCaptured"):
            try:
                snapshot_attempt(record, fixture)
            except Exception as capture_error:
                mark_infrastructure_failure(record, capture_error, "recovery_attempt_snapshot_failed")
        mark_infrastructure_failure(record, exc, stage)
        try:
            write_raw_result(directory / f"phase-{index}-failure.json", record)
        except Exception as write_error:
            mark_infrastructure_failure(record, write_error, "recovery_failure_checkpoint_failed")
    finally:
        if fixture is not None and record.get("modelAttempted"):
            try:
                settle_attempt(record, fixture, after_teardown=True)
                write_raw_result(directory / f"phase-{index}-attempt-final.json", record)
            except Exception as exc:
                mark_infrastructure_failure(record, exc, "recovery_final_capture_failed")
    return record, oracle


def run_recovery_case(case, options, binary, run_root, cli_run, battery, *, artifact_root,
                      fixture_factory=RecoveryFixture):
    if os.environ.get(NETWORK_PREFLIGHT_MARKER) != "1":
        raise RuntimeError("Recovery evaluation requires the approved launcher preflight")
    phases = []
    try:
        return _run_recovery_case(case, options, binary, run_root, cli_run, battery,
            artifact_root=artifact_root, fixture_factory=fixture_factory, retained_phases=phases)
    except Exception as exc:
        record = {"id": case["id"], "inputs": case.get("prompts", []), "automaticPass": False}
        combine_phase_attempts(record, phases)
        mark_infrastructure_failure(record, exc, "recovery_processing_failed")
        return record


def _run_recovery_case(case, options, binary, run_root, cli_run, battery, *, artifact_root,
                      fixture_factory=RecoveryFixture, retained_phases=None):
    if os.environ.get(NETWORK_PREFLIGHT_MARKER) != "1":
        raise RuntimeError("Recovery evaluation requires the approved launcher preflight")
    directory = run_root / case["id"]
    files_root = directory / "investigation"
    files_root.mkdir(parents=True, exist_ok=False)
    restarted = options.recovery_mode == "restarted"
    controller = RecoveryController(options.recovery_scenario) if restarted else None
    final_revision = 2 if options.recovery_scenario == "source-changed" else 1
    options.work_output_schema = battery.schema_for_case(case) if hasattr(battery, "schema_for_case") else battery.OUTPUT_SCHEMA
    first, oracle = _phase(directory, 0, options, binary, cli_run, files_root, controller,
                           1 if restarted else final_revision, fixture_factory)
    phases, failures = retained_phases, []
    phases.append(first)
    if first.get("infrastructureError"):
        raise RuntimeError("Recovery first phase failed")
    if restarted:
        if not controller.trigger or not first["output"].get("intentionalInterruption"):
            failures.append("recovery_fault_not_triggered")
        else:
            if options.recovery_scenario == "corrupt-progress":
                # This controlled fault is host-side, after the model has stopped.
                path = controller.files._path(controller.trigger["path"])
                path.write_text('{"generation":', encoding="utf-8")
            resumed, oracle = _phase(directory, 1, options, binary, cli_run, files_root, None,
                                     final_revision, fixture_factory)
            phases.append(resumed)
            if resumed.get("infrastructureError"):
                raise RuntimeError("Recovery resumed phase failed")
            if not any(c["name"] == "read_file" and c["ok"] for c in resumed["fileCalls"]):
                failures.append("resume_files_not_read")
    last = phases[-1]
    reusable = first["persistedCalls"] if len(phases) == 2 else []
    if final_revision == 2:
        # A new search membership and edited records must be confirmed in the fresh phase.
        reusable = [c for c in reusable if c["name"] not in {"search_issues", "get_tickets", "read_confluence_document"}
                    and (c.get("arguments") or {}).get("key") not in {"DL-94002", "DL-94017"}]
    evidence = [*reusable, *last["mcpToolCalls"]]
    output = copy.deepcopy(last["output"])
    output["mcpToolCalls"] = [c for p in phases for c in p["mcpToolCalls"]]
    output["forbiddenToolEvents"] = [event for p in phases
                                    for event in p["output"].get("forbiddenToolEvents", [])]
    automatic = battery.check(case, output, evidence, oracle)
    population = complete_search_membership(last["mcpToolCalls"], recovery_canonical_jql())
    if population is None or population != set(oracle["cases"][case["id"]]["keys"]):
        automatic["failures"].append("recovery_population_not_proved")
    automatic["failures"].extend(failures)
    if not all(p["isolation"]["worldUnchanged"] and p["isolation"]["providerStoreUnchanged"] for p in phases):
        automatic["failures"].append("read_only_world_mutated")
    if not any(r["uri"].endswith("/SKILL.md") and "/ltm-work-" in r["uri"]
               for p in phases for r in p["skillReads"]):
        automatic["failures"].append("candidate_skill_not_read")
    automatic["pass"] = not automatic["failures"]
    duration = sum(p["output"].get("durationSeconds", 0) for p in phases)
    output["durationSeconds"] = duration
    output["usage"], usage_coverage = aggregate_usage(p["output"] for p in phases)
    record = {"id": case["id"], "inputs": [p.get("prompt", RECOVERY_PROMPT) for p in phases], "output": output,
        "durationSeconds": duration, "automaticPass": automatic["pass"], "automaticContract": automatic,
        "isolation": last["isolation"], "upstreamCalls": [c for p in phases for c in p["upstreamCalls"]],
        "skillReads": [r for p in phases for r in p["skillReads"]], "phases": phases,
        "groundingCalls": evidence,
        **({"protocolCalls": [call for phase in phases for call in phase["protocolCalls"]]}
           if all("protocolCalls" in phase for phase in phases) else {}),
        "usageCoverage": usage_coverage,
        "fileMetrics": file_metrics([c for p in phases for c in p["fileCalls"]]),
        "recovery": {"scenario": options.recovery_scenario, "mode": options.recovery_mode,
                     "trigger": controller.trigger if controller else None,
                     "eligiblePersistedCalls": len(reusable), "controlledSourceRevision": final_revision},
        "sourceSnapshot": str((directory / f"phase-{len(phases)-1}-oracle.json").relative_to(artifact_root))}
    if not last["output"].get("ok") and not failures:
        record["infrastructureError"] = last["output"].get("error", "codex_execution_failed")
    return record
