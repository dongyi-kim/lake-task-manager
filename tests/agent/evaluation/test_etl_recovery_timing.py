"""Source-grounded ETL cuts, independent of a model's checkpoint vocabulary."""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import anyio
import pytest
from support.evaluation_tmp import external_tmp_path as tmp_path

from tools.codex_mcp_eval.etl.recovery import RecoveryController
from tools.codex_mcp_eval.research_files import ResearchFiles


def response(data, *, mode="hot"):
    return {"ok": True, "partial": False, "missing": 0, "data": data,
            "cache": {"available": True, "state": "fresh", "compliant": True,
                      "invalidated": False, "upstreamValidated": True,
                      "requestedCacheMode": mode, "effectiveCacheMode": mode,
                      "fetchedAt": 1000.0, "freshUntil": 1600.0,
                      "displayUntil": 2800.0, "maxAgeSeconds": 600}}


def search_pages(*, live=False):
    calls = []
    for start in range(2):
        arguments = {"jql": "project = TEST ORDER BY key", "start_at": start,
                     "cache_mode": "live" if live else "hot"}
        if start:
            arguments["snapshot_id"] = "snapshot-one"
        data = {"canonicalJql": "project = TEST ORDER BY key", "snapshotId": "snapshot-one",
                "snapshotCreatedAt": 1000.0, "snapshotSourceFetchedAt": 1000.0,
                "snapshotFrozen": bool(start), "startAt": start, "total": 2,
                "issues": [{"key": f"TEST-{start + 1}"}], "returned": 1,
                "hasMore": not start, "nextStartAt": 1 if not start else None}
        result = response(data, mode=arguments["cache_mode"])
        if live:
            result["cache"].update(freshUntil=1000.0, maxAgeSeconds=0)
            if start:
                result["cache"].update(state="stale", compliant=False, upstreamValidated=False)
        calls.append({"name": "search_issues", "arguments": arguments, "result": result})
    return calls


def evidence_pages(key="TEST-1", kind="comments"):
    calls = []
    for start in range(2):
        arguments = {"key": key, "kind": kind, "start_at": start,
                     "max_results": 1, "record_offset": 0, "cache_mode": "hot"}
        next_call = {"name": "get_evidence_page", "arguments": {**arguments, "start_at": 1}}
        data = {"key": key, "kind": kind, "startAt": start, "total": 2,
                "items": [{"id": str(start + 1), "body": "synthetic source"}],
                "sourceComplete": True, "partial": False, "returned": 1,
                "hasMore": not start, "nextStartAt": 1 if not start else None,
                "nextCall": next_call if not start else None, "recordFormat": "jira-evidence-v2"}
        calls.append({"name": "get_evidence_page", "arguments": arguments,
                      "result": response(data)})
    return calls


def setup_controller(root, prior, *, scenario="group-boundary", save=True):
    controller = RecoveryController(scenario)
    files = ResearchFiles(root, after_write=getattr(controller, "after_write", None))
    controller.files = files
    for index, call in enumerate(prior):
        controller.observe_ltm(call["name"], call["arguments"], call["result"])
        if save:
            files.write_file(f"evidence/page-{index}.json", json.dumps(call["result"]))
    return controller, files


def incoming(controller, call=None):
    call = call or evidence_pages("TEST-2")[0]
    controller.observe_ltm(call["name"], call["arguments"], call["result"])
    return call


@pytest.mark.parametrize("scenario", ["group-boundary", "client-restart", "backend-restart"])
@pytest.mark.parametrize("prior", ["search", "live-search", "comments", "history"])
def test_complete_saved_source_then_real_group_response_cuts(tmp_path, scenario, prior):
    calls = (search_pages(live=prior == "live-search") if "search" in prior
             else evidence_pages(kind=prior))
    controller, files = setup_controller(tmp_path, calls, scenario=scenario)
    assert not controller.stop.is_set()  # Saving a group is not itself a transition.
    target = incoming(controller)
    assert controller.stop.is_set()
    assert controller.trigger["reason"] == "next group response after complete saved source"
    assert controller.trigger["failedGroup"] == {"key": "TEST-2", "kind": "comments"}
    assert controller.trigger["requiresBackendRestart"] is (scenario == "backend-restart")
    controller.settle_cut(settled=True)
    assert controller.trigger["cutPosition"]["position"] == "target_response_unpersisted"
    assert controller.trigger["cutPosition"]["resumeCall"]["arguments"] == target["arguments"]
    assert controller.trigger["cutPosition"]["qualifiesFailedGroupWindow"] is True


@pytest.mark.parametrize("checkpoint", [{}, {"state": "complete", "lastChunk": "evidence/page-1.json"},
    {"groups": {"search": {"state": "complete", "evidence": "evidence/page-1.json"}}},
    {"lastEvidence": "irrelevant", "groups": {"conflicting": {"state": "failed"}}}])
def test_checkpoint_vocabulary_neither_triggers_nor_blocks_source_cut(tmp_path, checkpoint):
    controller, files = setup_controller(tmp_path, search_pages())
    files.write_file("progress.json", json.dumps(checkpoint))
    assert not controller.stop.is_set()
    incoming(controller)
    assert controller.stop.is_set()


@pytest.mark.parametrize("damage", ["unsaved", "altered", "outside-evidence", "tail-only", "gap",
    "wrong-snapshot", "partial", "missing", "invalidated", "unanchored-stale", "new-unsaved-first", "failure"])
def test_incomplete_or_untrusted_search_cannot_donate_a_cut(tmp_path, damage):
    calls = search_pages()
    if damage == "tail-only": calls = calls[-1:]
    elif damage == "gap": calls[0]["result"]["data"]["issues"] = []
    elif damage == "wrong-snapshot": calls[-1]["result"]["data"]["snapshotId"] = "foreign"
    elif damage in {"partial", "missing"}: calls[0]["result"][damage] = True if damage == "partial" else 1
    elif damage == "invalidated": calls[0]["result"]["cache"]["invalidated"] = True
    elif damage == "unanchored-stale": calls[-1]["result"]["cache"]["state"] = "stale"
    controller, files = setup_controller(tmp_path, calls, save=damage not in {"unsaved", "outside-evidence"})
    if damage == "altered": files.write_file("evidence/page-1.json", "{}")
    elif damage == "outside-evidence":
        for index, call in enumerate(calls): files.write_file(f"reports/page-{index}.json", json.dumps(call["result"]))
    elif damage == "new-unsaved-first":
        fresh = deepcopy(calls[0]); fresh["result"]["data"]["snapshotId"] = "new-snapshot"
        incoming(controller, fresh)
    elif damage == "failure":
        incoming(controller, {"name": "search_issues", "arguments": calls[0]["arguments"],
                              "result": {"ok": False, "error": {"code": "permission_denied"}}})
    incoming(controller)
    assert controller.trigger is None


@pytest.mark.parametrize("damage", ["source-incomplete", "partial", "missing", "invalidated", "stale",
                                  "record-gap", "duplicate-id", "identity"])
def test_incomplete_or_invalid_evidence_cannot_donate_a_cut(tmp_path, damage):
    calls = evidence_pages()
    if damage == "source-incomplete": calls[0]["result"]["data"]["sourceComplete"] = False
    elif damage in {"partial", "missing"}: calls[0]["result"][damage] = True if damage == "partial" else 1
    elif damage in {"invalidated", "stale"}: calls[0]["result"]["cache"].update(
        {"invalidated": True} if damage == "invalidated" else {"state": "stale", "compliant": False})
    elif damage == "record-gap": calls[0]["result"]["data"]["items"] = []
    elif damage == "duplicate-id": calls[-1]["result"]["data"]["items"][0]["id"] = "1"
    else: calls[-1]["result"]["data"]["key"] = "FOREIGN-1"
    controller, files = setup_controller(tmp_path, calls)
    incoming(controller)
    assert controller.trigger is None


def test_same_group_continuation_and_guides_are_not_transitions(tmp_path, monkeypatch):
    calls = evidence_pages()
    controller, files = setup_controller(tmp_path, calls[:1])
    with monkeypatch.context() as patch:
        patch.setattr(files, "list_files", lambda: pytest.fail("same-group read scanned evidence"))
        incoming(controller, calls[-1])
        controller.observe_ltm("get_content_guide", {}, {"ok": True, "data": {}})
    assert controller.trigger is None


@pytest.mark.parametrize("field,value", [("available", False), ("compliant", False)])
def test_fresh_search_label_is_not_enough_for_source_validity(tmp_path, field, value):
    calls = search_pages()
    calls[0]["result"]["cache"][field] = value
    controller, files = setup_controller(tmp_path, calls)
    incoming(controller)
    assert controller.trigger is None


@pytest.mark.parametrize("damage", ["identity", "partial", "incomplete", "missing", "invalidated"])
def test_invalid_incoming_response_cannot_name_a_failed_group(tmp_path, damage):
    controller, files = setup_controller(tmp_path, search_pages())
    call = evidence_pages("TEST-2")[0]
    if damage == "identity": call["result"]["data"]["key"] = "OTHER-1"
    elif damage == "partial": call["result"]["partial"] = True
    elif damage == "incomplete": call["result"]["data"]["sourceComplete"] = False
    elif damage == "missing": call["result"]["missing"] = 1
    else: call["result"]["cache"]["invalidated"] = True
    incoming(controller, call)
    assert controller.trigger is None


def test_new_complete_traversal_can_recover_from_observed_permission_failure(tmp_path):
    controller, files = setup_controller(tmp_path, search_pages())
    incoming(controller, {"name": "search_issues", "arguments": search_pages()[0]["arguments"],
                          "result": {"ok": False, "error": {"code": "permission_denied"}}})
    for index, call in enumerate(search_pages()):
        incoming(controller, call)
        files.write_file(f"evidence/revalidated-{index}.json", json.dumps(call["result"]))
    assert controller.trigger is None
    incoming(controller)
    assert controller.stop.is_set()


@pytest.mark.parametrize("tail", ["none", "prefix", "complete"])
def test_settled_cut_records_actual_cancellation_tail_without_deleting_it(tmp_path, tail):
    controller, files = setup_controller(tmp_path, search_pages())
    target = incoming(controller)
    assert controller.trigger is not None
    if tail != "none": files.write_file("evidence/target-0.json", json.dumps(target["result"]))
    if tail == "complete":
        last = evidence_pages("TEST-2")[-1]
        incoming(controller, last)
        files.write_file("evidence/target-1.json", json.dumps(last["result"]))
    controller.settle_cut(settled=True)
    cut = controller.trigger["cutPosition"]
    assert cut["position"] == {"none": "target_response_unpersisted", "prefix": "target_prefix_saved",
                               "complete": "target_group_already_complete"}[tail]
    assert cut["qualifiesFailedGroupWindow"] is (tail != "complete")
    if tail == "prefix": assert cut["resumeCall"] == target["result"]["data"]["nextCall"]
    if tail == "complete": assert files._path("evidence/target-1.json").exists()


def test_settlement_does_not_qualify_prior_source_lost_in_cancellation_tail(tmp_path):
    controller, files = setup_controller(tmp_path, search_pages())
    incoming(controller)
    assert controller.trigger is not None
    files.write_file("evidence/page-1.json", "{}")
    controller.settle_cut(settled=True)
    assert controller.trigger["cutPosition"]["position"] == "completed_source_no_longer_saved"
    assert controller.trigger["cutPosition"]["qualifiesFailedGroupWindow"] is False


@pytest.mark.parametrize("settled", [False, None])
def test_unsettled_cut_does_not_claim_a_verified_position(tmp_path, monkeypatch, settled):
    controller, files = setup_controller(tmp_path, search_pages())
    incoming(controller)
    monkeypatch.setattr(files, "list_files", lambda: pytest.fail("unsettled evidence inventory inspected"))
    controller.settle_cut(settled=settled)
    assert controller.trigger["cutPosition"]["position"] == "observer_settlement_unproved"
    assert controller.trigger["cutPosition"]["qualifiesFailedGroupWindow"] is False


@pytest.mark.parametrize("scenario,kind", [("search-pagination", "search"),
    ("comments-pagination", "comments"), ("history-pagination", "history")])
def test_existing_page_response_cuts_are_unchanged(tmp_path, scenario, kind):
    calls = search_pages() if kind == "search" else evidence_pages(kind=kind)
    controller, files = setup_controller(tmp_path, [], scenario=scenario)
    incoming(controller, calls[0])
    assert controller.stop.is_set()
    assert controller.trigger["reason"] == "real response before remaining pages"


@pytest.mark.parametrize("damage", [None, "gap", "digest"])
def test_saved_fragments_require_original_contiguous_digest(tmp_path, damage):
    text = json.dumps({"id": "record-one", "body": "neutral synthetic text"})
    cut = len(text) // 2
    calls = evidence_pages()[:1]
    calls = [deepcopy(calls[0]), deepcopy(calls[0])]
    for index, call in enumerate(calls):
        call["arguments"]["record_offset"] = cut if index else 0
        page = call["result"]["data"]
        page.update(total=1, hasMore=not index, nextStartAt=0 if not index else None)
        page["items"] = [{"id": "record-one", "sourceIndex": 0, "recordOffset": cut if index else 0,
            "recordJson": text[cut:] if index else text[:cut], "totalCharacters": len(text),
            "recordFormat": "jira-evidence-v2",
            "recordSha256": sha256(text.encode()).hexdigest(), "nextRecordOffset": cut if not index else None}]
    if damage == "gap": calls[1]["result"]["data"]["items"][0]["recordOffset"] += 1
    elif damage == "digest": calls[1]["result"]["data"]["items"][0]["recordSha256"] = "0" * 64
    controller, files = setup_controller(tmp_path, calls)
    incoming(controller)
    assert controller.stop.is_set() is (damage is None)


@pytest.mark.parametrize("scenario", ["client-restart", "backend-restart"])
@pytest.mark.parametrize("tail", ["none", "prefix", "complete"])
def test_runner_checks_settled_fault_position_and_keeps_real_restart_metadata(tmp_path, monkeypatch,
                                                                           scenario, tail):
    from tools.agent_eval_isolation import NETWORK_PREFLIGHT_MARKER
    from tools.codex_mcp_eval.etl.recovery_runner import run_recovery_case

    active, instances = [], []

    class Fixture:
        """Synthetic transport/settlement only; no server, subject model or answer oracle."""
        def __init__(self, path, *, files_root, controller, **kwargs):
            self.files, self.controller = ResearchFiles(files_root), controller
            if controller: controller.files = self.files
            self.cache_path = path / "private-cache"
            self.url = self.files_url = "synthetic://unused"
            self.token = "synthetic"
            self.tool_calls, self.upstream_calls, self.protocol_calls = [], [], []
            self.transport_calls, self.skill_reads = [], []
            active[:] = [self]
            instances.append(self)

        def __enter__(self): return self
        def close(self): pass
        def ground_truth(self): return {"cases": {"synthetic": {"keys": ["TEST-1", "TEST-2"]}}}
        def summary(self): return {"worldUnchanged": True, "providerStoreUnchanged": True}
        def settle_observers(self, **kwargs): return {"complete": True, "timedOut": False, "active": {}}

        def respond(self, call):
            row = deepcopy(call)
            row.update(complete=True, startOrder=2 * len(self.tool_calls) + 1,
                       endOrder=2 * len(self.tool_calls) + 2)
            self.tool_calls.append(row)
            if self.controller:
                self.controller.observe_ltm(row["name"], row["arguments"], row["result"])

    target = evidence_pages("TEST-2")
    if tail == "complete":
        target = target[:1]
        target[0]["result"]["data"].update(total=1, hasMore=False, nextStartAt=None, nextCall=None)

    def cli(_binary, _prompt, _workspace, **kwargs):
        fixture = active[0]
        first = "stop_event" in kwargs
        if not first:
            async def read_saved():
                await fixture.files.server.call_tool("read_file", {"path": "evidence/search-0.json"})
            anyio.run(read_saved)
        for index, call in enumerate(search_pages()):
            fixture.respond(call)
            fixture.files.write_file(f"evidence/search-{index}.json", json.dumps(call["result"]))
        for index, call in enumerate(target):
            fixture.respond(call)
            if first:
                assert kwargs["stop_event"].is_set()
                if tail != "none": fixture.files.write_file("evidence/target-0.json", json.dumps(call["result"]))
                break
            fixture.files.write_file(f"evidence/resumed-{index}.json", json.dumps(call["result"]))
        return {"ok": not first, "completed": not first, "intentionalInterruption": first,
                "reply": "synthetic transport observation", "usage": {}, "durationSeconds": 1, "events": []}

    options = SimpleNamespace(date="2026-11-10", model="synthetic", effort="high", timeout=10,
        recovery_scenario=scenario, recovery_mode="restarted", skill_condition="baseline", skill_transport="default")
    case = {"id": "synthetic-recovery", "sourceCaseId": "synthetic", "prompts": ["neutral synthetic scope"]}
    battery = SimpleNamespace(SYSTEM_CONTEXT="", schema_for_case=lambda _: {},
                              check=lambda *_: {"pass": True, "failures": []})
    monkeypatch.setenv(NETWORK_PREFLIGHT_MARKER, "1")
    record = run_recovery_case(case, options, "no-model", tmp_path / "run", cli, battery,
        artifact_root=tmp_path, fixture_factory=Fixture, bundle=object())
    assert record.get("infrastructureError") is None
    assert record["recovery"]["clientSessionCount"] == 2
    assert record["recovery"]["backendRestarted"] is (scenario == "backend-restart")
    assert len(instances) == (2 if scenario == "backend-restart" else 1)
    assert record["recovery"]["trigger"]["cutPosition"]["qualifiesFailedGroupWindow"] is (tail != "complete")
    assert record["automaticContract"]["failures"] == ([] if tail != "complete" else [
        "recovery_fault_position_not_exercised"])
    assert record["automaticPass"] is (tail != "complete")
