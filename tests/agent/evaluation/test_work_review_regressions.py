"""Final-review regressions for recovery safety, provenance and comparison identity."""
import json
from types import SimpleNamespace

import pytest
from support.evaluation_tmp import external_tmp_path as tmp_path

from app.local_mcp.skill_catalog import SkillCatalog
from tools.codex_mcp_eval import runner, work_cases
from tools.codex_mcp_eval.work_data import build_work_data
from tools.codex_mcp_eval.work_fixture import WorkFixture
from tools.codex_mcp_eval.work_recovery import RecoveryController, RECOVERY_SCENARIOS
from tools.codex_mcp_eval.research_files import ResearchFiles


def test_external_retained_output_uses_independent_cleaned_os_scratch(tmp_path, monkeypatch):
    import tempfile
    scratch = tmp_path / "system-temp"
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(scratch))
    with WorkFixture(tmp_path / "retained", port=0, today="2026-11-09") as fixture:
        actual = fixture.run_dir
        assert actual.is_relative_to(scratch)
        assert fixture.assert_conformance()["cacheWarmed"] is False
    assert not actual.exists()


def test_guide_only_reply_does_not_establish_person_ambiguity():
    case = next(c for c in work_cases.CASES if c["id"] == "WORK-R05-boundary")
    answer = {"reply": "회사 미확인, 후보 확인 필요", "resultKeys": [], "reportedTotal": None,
              "progress": None, "sources": [], "operations": []}
    output = {"ok": True, "reply": answer["reply"], "structuredAnswer": answer}
    calls = [{"name": "get_content_guide", "result": {"ok": True, "data": {"text": "guide"}}}]
    result = work_cases.check(case, output, calls, build_work_data("2026-11-09")["oracle"])
    assert not result["pass"] and "ambiguous_people_not_observed" in result["failures"]


@pytest.mark.parametrize("scenario", RECOVERY_SCENARIOS)
def test_invented_checkpoint_metadata_never_triggers_a_fault(tmp_path, scenario):
    controller = RecoveryController(scenario)
    files = ResearchFiles(tmp_path, after_write=controller.after_write)
    controller.files = files
    files.write_file("evidence/fake.json", '{"recordJson":"invented","nextRecordOffset":10}')
    files.write_file("progress.json", '{"generation":1,"snapshotId":"invented","nextStartAt":10}')
    controller.observe_ltm("get_evidence_page", {"key": "DL-1", "start_at": 10},
                           {"ok": True, "data": {"items": []}})
    assert not controller.stop.is_set()


def test_packaged_checkpoint_arguments_match_an_observed_saved_continuation(tmp_path):
    controller = RecoveryController("between-pages")
    files = ResearchFiles(tmp_path, after_write=controller.after_write)
    controller.files = files
    response = {"ok": True, "data": {"key": "DEMO-1", "kind": "comments", "startAt": 0,
        "items": [{"id": str(i)} for i in range(5)], "hasMore": True, "nextStartAt": 5}}
    controller.observe_ltm("get_evidence_page", {"key": "DEMO-1", "kind": "comments"}, response)
    files.write_file("evidence/page.json", json.dumps(response))
    example = json.loads(SkillCatalog(include_research=True).guide("ltm-work-reporting",
                                "references/checkpoint-example.json")["text"])
    files.write_file("progress.json", json.dumps(example["progress"]))
    assert controller.stop.is_set()


def test_source_changed_review_contract_matches_final_oracle_and_real_prompt():
    from tools.codex_mcp_eval.recovery_runner import recovery_battery
    battery = recovery_battery(runner._battery("work", "2026-11-09"), "2026-11-09", "source-changed", "restarted")
    _, specs = battery.review_contract()
    expected = specs["WORK-R02-boundary"]["elements"][0]["expected"]
    assert expected["total"] == 1 and "DL-94018" in expected["keys"] and "DL-94017" not in expected["keys"]
    assert expected["documents"] == ["운영 결정"]
    assert "DL-94018" in battery.CASES[0]["prompts"][0]
    assert battery.SUITE == "codex-mcp-work-recovery"


@pytest.mark.parametrize("scenario", RECOVERY_SCENARIOS)
def test_recovery_treatments_share_the_same_benchmark_manifest(scenario):
    from tools.agent_eval_protocol import battery_manifest_sha256, normalize_specialized_review_specs
    from tools.codex_mcp_eval.recovery_runner import recovery_battery
    manifests = []
    for mode in ("uninterrupted", "restarted"):
        battery = recovery_battery(runner._battery("work", "2026-11-09"), "2026-11-09", scenario, mode)
        registry = normalize_specialized_review_specs(battery.CASES, *battery.review_contract())
        manifests.append(battery_manifest_sha256(battery.CASES, registry,
                                                 checker_dependencies=battery.CHECKER_DEPENDENCIES))
    assert manifests[0] == manifests[1]


@pytest.mark.parametrize("forbidden", [[], ["command_execution"]])
@pytest.mark.parametrize("scenario", ["between-pages", "source-changed"])
def test_recovery_retains_all_phase_violations_and_counts_executed_calls(tmp_path, monkeypatch, forbidden, scenario):
    from tools.agent_eval_isolation import NETWORK_PREFLIGHT_MARKER
    from tools.codex_mcp_eval import recovery_runner
    monkeypatch.setenv(NETWORK_PREFLIGHT_MARKER, "1")
    def phase(directory, index, options, binary, cli_run, files_root, controller, revision, fixture_factory):
        if controller:
            controller._trigger("observed test boundary")
        calls = [{"name": f"executed-{index}-{i}"} for i in range(2 if index == 0 else 1)]
        if index == 0:
            calls[0]["name"] = "get_tickets"
        else:
            # This test isolates cross-phase violations/counting. Its resumed
            # phase still needs a valid, fully observed population (empty here).
            calls[0] = {"name": "search_issues", "result": {"ok": True, "partial": False,
                "missing": 0, "cache": {"state": "fresh"}, "data": {
                    "canonicalJql": recovery_runner.recovery_canonical_jql(), "snapshotId": "resumed",
                    "startAt": 0, "total": 0, "returned": 0, "issues": [],
                    "hasMore": False, "nextStartAt": None}}}
        return {"output": {"ok": index == 1, "intentionalInterruption": index == 0,
                           "forbiddenToolEvents": forbidden if index == 0 else [], "durationSeconds": 1},
            "mcpToolCalls": calls, "persistedCalls": calls[:1], "fileCalls": [{"name": "read_file", "ok": True,
                "result": {"content": "saved"}, "durationSeconds": 0.1}],
            "skillReads": [{"uri": "skill://ltm-work-reporting/SKILL.md"}], "upstreamCalls": [],
            "isolation": {"worldUnchanged": True, "providerStoreUnchanged": True}}, {
                "cases": {"WORK-R02-boundary": {"keys": []}}}
    monkeypatch.setattr(recovery_runner, "_phase", phase)
    def check(case, output, calls, oracle):
        assert len(calls) == (1 if scenario == "source-changed" else 2), "grounding evidence differs from executed calls"
        return {"pass": not output.get("forbiddenToolEvents"),
                "failures": ["unapproved_tool_used"] if output.get("forbiddenToolEvents") else []}
    battery = SimpleNamespace(OUTPUT_SCHEMA={}, check=check)
    options = SimpleNamespace(recovery_mode="restarted", recovery_scenario=scenario)
    case = {"id": "WORK-R02-boundary"}
    record = recovery_runner.run_recovery_case(case, options, "stub", tmp_path, None, battery, artifact_root=tmp_path)
    assert record["output"]["forbiddenToolEvents"] == forbidden
    assert record["automaticPass"] == (not forbidden)
    assert len(record["output"]["mcpToolCalls"]) == 3
    assert len(record["groundingCalls"]) == (1 if scenario == "source-changed" else 2)
    assert record["recovery"]["eligiblePersistedCalls"] == (0 if scenario == "source-changed" else 1)
    assert record["fileMetrics"]["calls"] == 2
    summary = runner._payload({"battery": {"name": "test"}}, [record], [case], {})["summary"]
    assert summary["toolCalls"] == 5 and summary["fileToolCalls"] == 2 and summary["ltmToolCalls"] == 3
