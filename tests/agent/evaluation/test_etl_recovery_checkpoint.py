"""Checkpoint cuts require local identity and genuine, fully saved source coverage."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
from mcp import Client
import pytest
from support.evaluation_tmp import external_tmp_path as tmp_path

from tools.codex_mcp_eval.etl.fixture import EtlFixture
from tools.codex_mcp_eval.etl.recovery import RecoveryController
from tools.codex_mcp_eval.etl.world import build_etl_world
from tools.codex_mcp_eval.research_files import ResearchFiles


QUERY = "key in (DL-31000,DL-31001,DL-31002) ORDER BY key ASC"


@pytest.fixture(scope="module")
def sources():
    with TemporaryDirectory() as directory:
        with EtlFixture(Path(directory) / "fixture", port=0, bundle=build_etl_world(),
                        skill_condition="baseline") as fixture:
            async def collect():
                async with Client(fixture.runtime.server) as client:
                    async def call(name, **arguments):
                        result = await client.call_tool(name, arguments)
                        assert not result.is_error, result.structured_content
                        return deepcopy(fixture.tool_calls[-1])

                    search = [await call("search_issues", jql=QUERY, max_results=1)]
                    while search[-1]["result"]["data"]["hasMore"]:
                        page = search[-1]["result"]["data"]
                        search.append(await call("search_issues", jql=QUERY, max_results=1,
                            start_at=page["nextStartAt"], snapshot_id=page["snapshotId"]))
                    live_search = [await call("search_issues", jql=QUERY, max_results=1, cache_mode="live")]
                    while live_search[-1]["result"]["data"]["hasMore"]:
                        page = live_search[-1]["result"]["data"]
                        live_search.append(await call("search_issues", jql=QUERY, max_results=1,
                            cache_mode="live", start_at=page["nextStartAt"], snapshot_id=page["snapshotId"]))
                    other_search = await call("search_issues", jql="key = DL-31001", max_results=100)
                    comments = [await call("get_evidence_page", key="DL-31005", kind="comments", max_results=2)]
                    comments.append(await call("get_evidence_page", key="DL-31005", kind="comments",
                                               start_at=2, max_results=2))
                    other = await call("get_evidence_page", key="DL-31001", kind="history")
                    fragment = await call("get_evidence_page", key="DL-31005", kind="comments",
                                          start_at=2, record_offset=1, max_results=1)
                    assert fragment["result"]["data"]["items"][0]["recordOffset"] == 1
                    return {"search": search, "live-search": live_search, "comments": comments, "other": [other],
                            "other-search": [other_search], "fragment": [fragment]}
            return anyio.run(collect)


def saved_controller(root, calls, *, scenario="group-boundary", prefix="", save=True):
    controller = RecoveryController(scenario)
    files = ResearchFiles(root)
    controller.files = files
    for index, call in enumerate(calls):
        controller.observe_ltm(call["name"], call["arguments"], call["result"])
        if save:
            files.write_file(f"{prefix}evidence/page-{index}.json", json.dumps(call["result"]))
    return controller, files



@pytest.mark.parametrize("scenario", ["group-boundary", "client-restart", "backend-restart"])
@pytest.mark.parametrize("collection", ["search", "live-search", "comments"])
@pytest.mark.parametrize("prefix", ["", "investigation/run/"])
def test_real_saved_coverage_then_incoming_source_ignores_checkpoint_layout(tmp_path, sources, scenario,
                                                                          collection, prefix):
    controller, files = saved_controller(tmp_path, sources[collection], scenario=scenario, prefix=prefix)
    files.write_file(prefix + "progress.json", json.dumps({"groups": {"search": {
        "state": "complete", "evidence": prefix + "evidence/page-0.json"}}, "lastEvidence": "unused"}))
    assert not controller.stop.is_set()
    target = sources["other"][0]
    from tools.codex_mcp_eval.etl.recovery import eligible_source
    cache = target["result"].get("cache") or {}
    assert eligible_source(target), (target["result"]["data"].get("sourceComplete"),
        target["result"].get("partial"), target["result"].get("missing"),
        cache.get("available"), cache.get("state"), cache.get("compliant"), cache.get("requestedCacheMode"))
    controller.observe_ltm(target["name"], target["arguments"], target["result"])
    assert controller.stop.is_set(), {"incomingCache": target["result"].get("cache"),
        "incomingSourceComplete": target["result"]["data"].get("sourceComplete"),
        "priorCache": sources[collection][-1]["result"].get("cache")}
    assert controller.trigger["failedGroup"] == {"key": "DL-31001", "kind": "history"}
    assert controller.trigger["requiresBackendRestart"] is (scenario == "backend-restart")


@pytest.mark.parametrize("damage", ["unsaved", "altered", "failed", "outside", "near-evidence"])
def test_transition_requires_genuine_successful_evidence_bytes(tmp_path, sources, damage):
    calls = deepcopy(sources["search"])
    if damage == "failed": calls[-1]["result"]["ok"] = False
    controller, files = saved_controller(tmp_path, calls, save=damage not in {"unsaved", "outside", "near-evidence"})
    if damage == "altered": files.write_file(f"evidence/page-{len(calls)-1}.json", "{}")
    elif damage in {"outside", "near-evidence"}:
        for index, call in enumerate(calls):
            files.write_file(("reports" if damage == "outside" else "evidence-old") + f"/page-{index}.json",
                             json.dumps(call["result"]))
    target = sources["other"][0]
    controller.observe_ltm(target["name"], target["arguments"], target["result"])
    assert controller.trigger is None


@pytest.mark.parametrize("damage", ["tail-only", "gap", "mixed-snapshots", "partial", "incomplete-record"])
def test_terminal_search_flag_is_not_complete_saved_population(tmp_path, sources, damage):
    calls = deepcopy(sources["search"])
    if damage == "tail-only": calls = calls[-1:]
    elif damage == "gap": calls = [calls[0], calls[-1]]
    elif damage == "mixed-snapshots": calls[0]["result"]["data"]["snapshotId"] = "another"
    elif damage == "partial": calls[0]["result"]["partial"] = True
    else: calls[0]["result"]["data"]["issues"] = []
    controller, files = saved_controller(tmp_path, calls)
    target = sources["other"][0]
    controller.observe_ltm(target["name"], target["arguments"], target["result"])
    assert controller.trigger is None


@pytest.mark.parametrize("damage", ["tail-only", "partial", "fragmented-record", "gapped-records"])
def test_terminal_evidence_flag_is_not_complete_saved_records(tmp_path, sources, damage):
    calls = deepcopy(sources["comments"])
    if damage == "tail-only": calls = calls[-1:]
    elif damage == "partial": calls[0]["result"]["data"]["partial"] = True
    elif damage == "fragmented-record": calls = [calls[0], *deepcopy(sources["fragment"])]
    else: calls[0]["result"]["data"]["items"].pop()
    controller, files = saved_controller(tmp_path, calls)
    target = sources["other"][0]
    controller.observe_ltm(target["name"], target["arguments"], target["result"])
    assert controller.trigger is None


@pytest.mark.parametrize("suite", ["generic", "etl"])
def test_each_shared_recovery_helper_changes_the_battery_fingerprint(monkeypatch, suite):
    from tools import agent_eval_protocol as protocol
    from tools.codex_mcp_eval import runner
    from tools.codex_mcp_eval.etl.recovery import SOURCE_GROUP_DEPENDENCIES
    from tools.codex_mcp_eval.work_evidence import evidence_collection
    from tools.codex_mcp_eval.work_recovery import SAVED_RESPONSE_DEPENDENCIES
    if suite == "generic":
        from tools.codex_mcp_eval.recovery_runner import recovery_battery
        battery = recovery_battery(runner._battery("work", "2026-11-09"), "2026-11-09", "between-pages", "restarted")
        assert battery.BATTERY_VERSION == "4.0.4"
    else:
        from tools.codex_mcp_eval.etl.recovery_runner import recovery_battery
        battery = recovery_battery(runner._battery("etl", "2026-11-10"), "2026-11-10", "client-restart", "restarted")
        assert battery.BATTERY_VERSION == "3.0.6"
    before = protocol.battery_manifest_sha256(battery.CASES, checker_dependencies=battery.CHECKER_DEPENDENCIES)
    original = protocol.inspect.getsource
    helpers = (*SAVED_RESPONSE_DEPENDENCIES, *((*SOURCE_GROUP_DEPENDENCIES, evidence_collection) if suite == "etl" else ()))
    for helper in helpers:
        with monkeypatch.context() as patch:
            patch.setattr(protocol.inspect, "getsource", lambda value: "changed helper body" if value is helper else original(value))
            assert protocol.battery_manifest_sha256(battery.CASES,
                checker_dependencies=battery.CHECKER_DEPENDENCIES) != before, helper.__name__
