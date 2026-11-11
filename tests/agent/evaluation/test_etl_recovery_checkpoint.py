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
    files = ResearchFiles(root, after_write=controller.after_write)
    controller.files = files
    for index, call in enumerate(calls):
        controller.observe_ltm(call["name"], call["arguments"], call["result"])
        if save:
            files.write_file(f"{prefix}evidence/page-{index}.json", json.dumps(call["result"]))
    return controller, files


def completed(calls, shape, prefix=""):
    call = calls[-1]
    data = call["result"]["data"]
    identity = ({"jql": call["arguments"]["jql"], "snapshotId": data["snapshotId"]}
                if call["name"] == "search_issues" else {"key": data["key"], "kind": data["kind"]})
    reference = f"{prefix}evidence/page-{len(calls) - 1}.json"
    if shape == "flat":
        node = {"status": "complete", **identity}
    elif shape in {"local-arguments", "arguments"}:
        node = {"status": "complete", "arguments" if shape == "arguments" else "lastSavedArguments": deepcopy(call["arguments"]),
                "lastChunk": reference}
        if call["name"] == "search_issues":
            node["snapshotId"] = data["snapshotId"]
    else:
        node = {"state": "complete", "lastSaved": reference}
    map_key = "search" if call["name"] == "search_issues" else data["kind"] + "/" + data["key"]
    return {"groups": {map_key: node, "comments:DL-31006": {"state": "pending",
            "lastSavedArguments": {"key": "DL-31006", "kind": "comments"}}}}


def layout_checkpoint(calls, prefix=""):
    checkpoint = completed(calls, "local-reference", prefix)
    node = next(iter(checkpoint["groups"].values()))
    node["status"] = node.pop("state")
    node["nextCall"] = None
    node["lastEvidence"] = node.pop("lastSaved")
    if calls[-1]["name"] == "search_issues":
        node["snapshot"] = {"id": calls[-1]["result"]["data"]["snapshotId"]}
    else:
        node["cursor"] = {"arguments": deepcopy(calls[-1]["arguments"]), "nextCall": None}
    return checkpoint


@pytest.mark.parametrize("scenario,collection,grouped", [
    ("group-boundary", "live-search", True), ("client-restart", "comments", True),
    ("backend-restart", "comments", False)])
def test_observed_layout_checkpoint_binds_saved_source(tmp_path, sources, scenario, collection, grouped):
    calls = sources[collection]
    controller, files = saved_controller(tmp_path, calls, scenario=scenario, prefix="investigation/run/")
    for index, call in enumerate(calls):
        files.write_file(f"investigation/run/evidence/page-{index}.json", json.dumps({
            "tool": call["name"], "arguments": call["arguments"], "response": call["result"]}))
    checkpoint = layout_checkpoint(calls, "investigation/run/")
    if not grouped:
        checkpoint = next(iter(checkpoint["groups"].values()))
    files.write_file("investigation/run/progress.json", json.dumps(checkpoint))
    assert controller.stop.is_set() and controller.trigger
    assert controller.trigger["requiresBackendRestart"] is (scenario == "backend-restart")


@pytest.mark.parametrize("damage", ["cursor-key", "cursor-kind", "cursor-arguments", "cursor-container",
    "snapshot-id", "snapshot-container", "snapshot-value", "alias-reference", "alias-snapshot-vs-direct",
    "alias-snapshot-vs-arguments", "alias-cursor-vs-direct", "alias-cursor-vs-arguments"])
def test_local_alias_conflicts_cannot_be_ignored_beside_valid_binding(tmp_path, sources, damage):
    calls = sources["search" if "snapshot" in damage else "comments"]
    controller, files = saved_controller(tmp_path, [*calls, *sources["other"]])
    checkpoint = completed(calls, "local-reference")
    node = next(iter(checkpoint["groups"].values()))
    if damage.startswith("cursor"):
        node["cursor"] = {"arguments": deepcopy(calls[-1]["arguments"])}
        if damage == "cursor-container":
            node["cursor"] = []
        elif damage == "cursor-arguments":
            node["cursor"]["arguments"] = []
        else:
            node["cursor"]["arguments"][damage.removeprefix("cursor-")] = "different"
    elif damage == "alias-reference":
        node["lastEvidence"] = f"evidence/page-{len(calls)}.json"
    elif damage.startswith("alias-cursor"):
        node["cursor"] = {"arguments": deepcopy(calls[-1]["arguments"])}
        if damage.endswith("direct"):
            node["key"] = "different"
        else:
            node["lastSavedArguments"] = {"key": "different", "kind": "comments"}
    else:
        snapshot = calls[-1]["result"]["data"]["snapshotId"]
        node["snapshot"] = {"id": "different"}
        if damage == "snapshot-container":
            node["snapshot"] = []
        elif damage == "snapshot-value":
            node["snapshot"]["id"] = []
        elif damage == "alias-snapshot-vs-direct":
            node["snapshotId"] = snapshot
        elif damage == "alias-snapshot-vs-arguments":
            node["arguments"] = {"snapshot_id": snapshot}
    files.write_file("progress.json", json.dumps(checkpoint))
    assert controller.trigger is None and not controller.stop.is_set()


@pytest.mark.parametrize("alias", ["lastEvidence", "cursor", "snapshot"])
def test_rejected_alias_binding_cannot_borrow_completed_descendant(tmp_path, sources, alias):
    calls = sources["comments"]
    controller, files = saved_controller(tmp_path, [*calls, *sources["other"]])
    child = completed(calls, "local-reference")["groups"]["comments/DL-31005"]
    rejected = {"lastEvidence": "evidence/page-2.json"} if alias == "lastEvidence" else {
        alias: {"arguments": {"key": "different"}} if alias == "cursor" else {"id": "different"}}
    files.write_file("progress.json", json.dumps({**rejected, "arbitrary": child}))
    assert controller.trigger is None and not controller.stop.is_set()


@pytest.mark.parametrize("damage", ["unsaved", "altered", "terminal-only", "record-gap", "path-traversal",
                                  "path-prefix", "path-missing"])
def test_last_evidence_alias_requires_exact_complete_saved_source(tmp_path, sources, damage):
    calls = deepcopy(sources["comments" if damage == "record-gap" else "search"])
    if damage == "terminal-only":
        calls = calls[-1:]
    elif damage == "record-gap":
        calls[0]["result"]["data"]["items"].pop()
    controller, files = saved_controller(tmp_path, calls, save=damage != "unsaved")
    checkpoint = layout_checkpoint(calls)
    node = next(iter(checkpoint["groups"].values()))
    if damage == "altered":
        files.write_file(node["lastEvidence"], "{}")
    elif damage == "path-traversal":
        node["lastEvidence"] = "../evidence/page-2.json"
    elif damage == "path-prefix":
        node["lastEvidence"] = "evidence/"
    elif damage == "path-missing":
        node["lastEvidence"] = "evidence/missing.json"
    files.write_file("progress.json", json.dumps(checkpoint))
    assert controller.trigger is None and not controller.stop.is_set()


@pytest.mark.parametrize("field", ["snapshotId", "snapshot_id"])
@pytest.mark.parametrize("value", [None, "unrelated"])
def test_live_evidence_null_snapshot_is_inapplicable_not_conflicting(tmp_path, sources, field, value):
    calls = sources["comments"]
    assert all(call["result"]["data"].get("snapshotId") is None for call in calls)
    controller, files = saved_controller(tmp_path, calls)
    checkpoint = completed(calls, "local-arguments")
    checkpoint["groups"]["comments/DL-31005"][field] = value
    files.write_file("progress.json", json.dumps(checkpoint))
    assert controller.stop.is_set() is (value is None)


@pytest.mark.parametrize("scenario", ["group-boundary", "client-restart", "backend-restart"])
@pytest.mark.parametrize("collection", ["search", "live-search", "comments"])
@pytest.mark.parametrize("shape", ["flat", "arguments", "local-arguments", "local-reference"])
@pytest.mark.parametrize("prefix", ["", "investigation/run/"])
def test_completed_local_checkpoint_cuts_and_retains_pending_group(tmp_path, sources, scenario,
                                                                 collection, shape, prefix):
    calls = sources[collection]
    controller, files = saved_controller(tmp_path, calls, scenario=scenario, prefix=prefix)
    files.write_file(prefix + "progress.json", json.dumps(completed(calls, shape, prefix)))
    assert controller.stop.is_set()
    assert controller.trigger["reason"] == "completed group checkpoint before interruption"
    assert controller.trigger["failedGroup"] == {"key": "DL-31006", "kind": "comments"}
    assert controller.trigger["requiresBackendRestart"] is (scenario == "backend-restart")


@pytest.mark.parametrize("damage", ["wrong-query", "wrong-snapshot", "wrong-key", "wrong-kind",
    "wrong-reference", "conflicting-query", "conflicting-snapshot", "conflicting-key", "conflicting-kind",
    "conflicting-state", "conflicting-arguments", "conflicting-references", "wrong-map-key",
    "sibling-borrowing", "descendant-borrowing", "map-key-only"])
def test_local_identity_conflicts_cannot_borrow_matching_global_response(tmp_path, sources, damage):
    search = "query" in damage or "snapshot" in damage
    calls = sources["search" if search else "comments"]
    controller, files = saved_controller(tmp_path, [*calls, *sources["other"], *sources["other-search"]])
    checkpoint = completed(calls, "local-arguments")
    map_key, node = next(iter(checkpoint["groups"].items()))
    if damage == "wrong-query":
        node["lastSavedArguments"]["jql"] = "key = DL-31001"
    elif damage == "wrong-snapshot":
        node["snapshotId"] = "another"
    elif damage == "wrong-key":
        node["lastSavedArguments"]["key"] = "DL-31001"
    elif damage == "wrong-kind":
        node["lastSavedArguments"]["kind"] = "history"
    elif damage == "wrong-reference":
        node["lastChunk"] = f"evidence/page-{len(calls)}.json"
    elif damage == "conflicting-arguments":
        node["arguments"] = {"key": "DL-31001", "kind": "comments"}
    elif damage == "conflicting-references":
        node["lastSaved"] = f"evidence/page-{len(calls)}.json"
    elif damage.startswith("conflicting-") and damage != "conflicting-state":
        field = damage.removeprefix("conflicting-")
        node[{"query": "jql", "snapshot": "snapshot_id"}.get(field, field)] = "different"
    elif damage == "conflicting-state":
        node["state"] = "pending"
    elif damage == "wrong-map-key":
        checkpoint["groups"]["history/DL-31001"] = checkpoint["groups"].pop(map_key)
    elif damage == "sibling-borrowing":
        checkpoint["identity"] = node.pop("lastSavedArguments")
        node.pop("lastChunk")
    elif damage == "descendant-borrowing":
        node["arbitrary"] = node.pop("lastSavedArguments")
        node.pop("lastChunk")
    elif damage == "map-key-only":
        node.pop("lastSavedArguments")
        node.pop("lastChunk")
    files.write_file("progress.json", json.dumps(checkpoint))
    assert controller.trigger is None and not controller.stop.is_set()


@pytest.mark.parametrize("container", ["arguments", "lastSavedArguments", "arbitrary"])
@pytest.mark.parametrize("conflict", ["wrong-map", "conflicting-parent", "wrong-reference"])
def test_nested_completion_cannot_rehabilitate_a_rejected_binding(tmp_path, sources, container, conflict):
    calls = [*sources["comments"], *sources["other"]]
    controller, files = saved_controller(tmp_path, calls)
    child = {"status": "complete", "key": "DL-31005", "kind": "comments"}
    node = {"status": "complete", "key": "DL-31005", "kind": "comments", container: child}
    map_key = "comments/DL-31005"
    if conflict == "wrong-map":
        map_key = "history/DL-31001"
    elif conflict == "conflicting-parent":
        node.update(key="DL-31001", kind="history")
    else:
        node["lastChunk"] = "evidence/page-2.json"  # Genuine bytes for another group.
    files.write_file("progress.json", json.dumps({"groups": {map_key: node}}))
    assert controller.trigger is None and not controller.stop.is_set()


@pytest.mark.parametrize("container", ["arguments", "lastSavedArguments", "arbitrary"])
@pytest.mark.parametrize("conflict", ["wrong-map", "conflicting-parent", "wrong-reference"])
def test_nested_pending_cannot_override_the_next_valid_failed_group(tmp_path, sources, container, conflict):
    calls = [*sources["comments"], *sources["other"], *sources["search"]]
    controller, files = saved_controller(tmp_path, calls)
    child = {"state": "pending", "key": "DL-31005", "kind": "comments"}
    node = {"state": "pending", "key": "DL-31005", "kind": "comments", container: child}
    map_key = "comments/DL-31005"
    if conflict == "wrong-map":
        map_key = "history/DL-31001"
    elif conflict == "conflicting-parent":
        node.update(key="DL-31001", kind="history")
    else:
        # A rejected completed binding cannot donate its child's pending identity.
        node.update(state="complete", lastChunk="evidence/page-2.json")
    search = completed(sources["search"], "flat")["groups"]["search"]
    files.write_file("progress.json", json.dumps({"groups": {map_key: node, "search": search,
        "history/DL-31002": {"state": "pending"}}}))
    assert controller.stop.is_set() and controller.trigger
    assert controller.trigger["failedGroup"] == {"key": "DL-31002", "kind": "history"}


@pytest.mark.parametrize("field", ["status", "state"])
@pytest.mark.parametrize("shape", ["flat", "local-arguments", "local-reference"])
def test_overall_run_state_preserves_explicit_groups_without_promoting_metadata(tmp_path, sources, field, shape):
    calls = sources["comments"]
    controller, files = saved_controller(tmp_path, calls)
    checkpoint = {field: "collecting", "arbitrary": {
        "state": "pending", "key": "DL-31001", "kind": "history"},
        "groups": completed(calls, shape)["groups"]}
    files.write_file("progress.json", json.dumps(checkpoint))
    assert controller.stop.is_set() and controller.trigger
    assert controller.trigger["failedGroup"] == {"key": "DL-31006", "kind": "comments"}


@pytest.mark.parametrize("damage", ["unsaved", "altered", "failed", "reference-missing", "reference-outside",
                                  "reference-traversal", "reference-near-evidence"])
def test_reference_requires_exact_successful_saved_bytes(tmp_path, sources, damage):
    calls = deepcopy(sources["search"])
    if damage == "failed":
        calls[-1]["result"]["ok"] = False
    controller, files = saved_controller(tmp_path, calls, save=damage != "unsaved")
    checkpoint = completed(calls, "local-reference")
    node = checkpoint["groups"]["search"]
    if damage == "altered":
        changed = deepcopy(calls[-1]["result"])
        changed["data"]["issues"] = []
        files.write_file(node["lastSaved"], json.dumps(changed))
    elif damage == "reference-missing":
        node["lastSaved"] = "evidence/missing.json"
    elif damage == "reference-outside":
        files.write_file("reports/response.json", json.dumps(calls[-1]["result"]))
        node["lastSaved"] = "reports/response.json"
    elif damage == "reference-traversal":
        node["lastSaved"] = "../evidence/page-2.json"
    elif damage == "reference-near-evidence":
        files.write_file("evidence-old/response.json", json.dumps(calls[-1]["result"]))
        node["lastSaved"] = "evidence-old/response.json"
    files.write_file("progress.json", json.dumps(checkpoint))
    assert controller.trigger is None and not controller.stop.is_set()


@pytest.mark.parametrize("damage", ["tail-only", "gap", "mixed-snapshots", "partial", "incomplete-record"])
def test_terminal_search_flag_is_not_complete_saved_population(tmp_path, sources, damage):
    calls = deepcopy(sources["search"])
    if damage == "tail-only":
        calls = calls[-1:]
    elif damage == "gap":
        calls = [calls[0], calls[-1]]
    elif damage == "mixed-snapshots":
        calls[0]["result"]["data"]["snapshotId"] = "another"
    elif damage == "partial":
        calls[0]["result"]["partial"] = True
    else:
        calls[0]["result"]["data"]["issues"] = []
    controller, files = saved_controller(tmp_path, calls)
    files.write_file("progress.json", json.dumps(completed(calls, "flat")))
    assert controller.trigger is None and not controller.stop.is_set()


@pytest.mark.parametrize("damage", ["tail-only", "partial", "fragmented-record", "gapped-records"])
def test_terminal_evidence_flag_is_not_complete_saved_records(tmp_path, sources, damage):
    calls = deepcopy(sources["comments"])
    if damage == "tail-only":
        calls = calls[-1:]
    elif damage == "partial":
        calls[0]["result"]["data"]["partial"] = True
    elif damage == "fragmented-record":
        calls = [calls[0], *deepcopy(sources["fragment"])]
    else:
        calls[0]["result"]["data"]["items"].pop()
    controller, files = saved_controller(tmp_path, calls)
    files.write_file("progress.json", json.dumps(completed(calls, "flat")))
    assert controller.trigger is None and not controller.stop.is_set()


@pytest.mark.parametrize("suite", ["generic", "etl"])
def test_each_shared_recovery_helper_changes_the_battery_fingerprint(monkeypatch, suite):
    from tools import agent_eval_protocol as protocol
    from tools.codex_mcp_eval import runner
    from tools.codex_mcp_eval.recovery_checkpoint import CHECKPOINT_DEPENDENCIES
    from tools.codex_mcp_eval.work_recovery import SAVED_RESPONSE_DEPENDENCIES
    if suite == "generic":
        from tools.codex_mcp_eval.recovery_runner import recovery_battery
        battery = recovery_battery(runner._battery("work", "2026-11-09"), "2026-11-09", "between-pages", "restarted")
        assert battery.BATTERY_VERSION == "4.0.3"
    else:
        from tools.codex_mcp_eval.etl.recovery_runner import recovery_battery
        battery = recovery_battery(runner._battery("etl", "2026-11-10"), "2026-11-10", "client-restart", "restarted")
        assert battery.BATTERY_VERSION == "3.0.5"
    before = protocol.battery_manifest_sha256(battery.CASES, checker_dependencies=battery.CHECKER_DEPENDENCIES)
    original = protocol.inspect.getsource
    for helper in (*SAVED_RESPONSE_DEPENDENCIES, *CHECKPOINT_DEPENDENCIES):
        with monkeypatch.context() as patch:
            patch.setattr(protocol.inspect, "getsource", lambda value: "changed helper body" if value is helper else original(value))
            assert protocol.battery_manifest_sha256(battery.CASES,
                checker_dependencies=battery.CHECKER_DEPENDENCIES) != before, helper.__name__


def test_nonprogress_write_skips_deep_evidence_scan_but_progress_checks_bytes(tmp_path, sources, monkeypatch):
    controller, files = saved_controller(tmp_path, sources["comments"], save=False)
    def forbidden_scan():
        pytest.fail("non-progress write attempted a deep saved-evidence scan")
    with monkeypatch.context() as patch:
        patch.setattr(files, "list_files", forbidden_scan)
        controller.after_write("investigation/evidence/page.json", "{}")
    files.write_file("progress.json", json.dumps(completed(sources["comments"], "flat")))
    assert controller.trigger is None and not controller.stop.is_set()


@pytest.mark.parametrize("field,value", [("status", []), ("state", {}), ("arguments", None),
                                        ("lastSavedArguments", []), ("lastSaved", {})])
def test_malformed_local_fields_do_not_trigger_or_abort_file_write(tmp_path, sources, field, value):
    calls = sources["comments"]
    controller, files = saved_controller(tmp_path, calls)
    checkpoint = completed(calls, "flat")
    node = checkpoint["groups"]["comments/DL-31005"]
    node[field] = value
    files.write_file("progress.json", json.dumps(checkpoint))
    assert controller.trigger is None and not controller.stop.is_set()
