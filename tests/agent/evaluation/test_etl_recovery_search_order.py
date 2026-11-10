"""A completed population later cannot repair failed-search-first ordering."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace

import anyio
from mcp import Client
import pytest

from tools.codex_mcp_eval.etl.fixture import EtlFixture
from tools.codex_mcp_eval.etl.recovery import RecoveryController
from tools.codex_mcp_eval.etl.recovery_runner import audit_failed_group_first, run_recovery_case
from tools.codex_mcp_eval.etl.world import build_etl_world
from tools.codex_mcp_eval.work_evidence import evidence_collection
from tools.codex_mcp_eval.work_recovery import complete_search_membership, persisted_calls


QUERY = "key in (DL-31000,DL-31001,DL-31002) ORDER BY key ASC"
KEYS = {"DL-31000", "DL-31001", "DL-31002"}
ORDER_FAILURE = ["next_group_started_before_failed_search_completed"]


@pytest.fixture(scope="module")
def bundle():
    return build_etl_world()


@pytest.fixture(scope="module")
def recorded_trace(bundle):
    # Exercise the real fixture/provider/SDK and rooted file service once. Tests
    # below isolate or damage copies of these actual responses, never fake their
    # successful source coverage. All runtime resources close before assertions.
    with TemporaryDirectory() as directory:
        root = Path(directory)
        files_root = root / "files"
        files_root.mkdir()
        controller = RecoveryController("search-pagination")
        with EtlFixture(root / "fixture", port=0, files_root=files_root,
                        bundle=bundle, controller=controller,
                        skill_condition="candidate") as fixture:
            async def collect():
                async with Client(fixture.runtime.server) as client:
                    async def call(name, arguments):
                        response = await client.call_tool(name, arguments)
                        assert not response.is_error, response.structured_content
                        assert response.structured_content["ok"] is True
                        observed = deepcopy(fixture.tool_calls[-1])
                        assert observed["name"] == name
                        assert observed["result"] == response.structured_content
                        return observed

                    async def search(**options):
                        return await call("search_issues", {
                            "jql": QUERY, "detail": "summary", "max_results": 1, **options})

                    async def evidence():
                        return await call("get_evidence_page", {
                            "key": "DL-31000", "kind": "comments"})

                    first = await search()
                    page = first["result"]["data"]
                    assert page["hasMore"] is True and page["nextStartAt"] == 1
                    assert controller.stop.is_set()
                    trigger = deepcopy(controller.trigger)

                    # This is the actual bad ordering: B completes, another group
                    # starts, and only afterwards does A become fully proved.
                    unrelated = await call("search_issues", {
                        "jql": "key = DL-31000 ORDER BY key ASC", "max_results": 100})
                    early_evidence = await evidence()
                    eventual = await search(max_results=100)
                    await fixture.files.server.call_tool("write_file", {
                        "path": "evidence/search-complete.json",
                        "content": json.dumps(eventual["result"], ensure_ascii=False)})
                    saved_complete = persisted_calls(fixture.files, [eventual])
                    assert saved_complete == [eventual]
                    validated_complete = await search(max_results=100, start_at=0,
                        snapshot_id=eventual["result"]["data"]["snapshotId"])
                    validated_evidence = await evidence()

                    # A saved first page plus exact continuations is a distinct
                    # positive path; an observed-but-unsaved prefix is not proof.
                    prefix = await search()
                    assert persisted_calls(fixture.files, [prefix]) == []
                    await fixture.files.server.call_tool("write_file", {
                        "path": "evidence/search-prefix.json",
                        "content": json.dumps(prefix["result"], ensure_ascii=False)})
                    saved_prefix = persisted_calls(fixture.files, [prefix])
                    assert saved_prefix == [prefix]
                    tails = []
                    data = prefix["result"]["data"]
                    while data["hasMore"]:
                        next_call = await search(start_at=data["nextStartAt"],
                            snapshot_id=data["snapshotId"])
                        tails.append(next_call)
                        data = next_call["result"]["data"]
                    contiguous_evidence = await evidence()

                    # A fresh complete replacement must be allowed even when its
                    # snapshot differs from the interrupted cursor.
                    replacement = await search(max_results=100, cache_mode="live")
                    replacement_evidence = await evidence()
                    return {"trigger": trigger, "first": first,
                        "canonical": page["canonicalJql"], "unrelated": unrelated,
                        "earlyEvidence": early_evidence, "eventual": eventual,
                        "savedComplete": saved_complete, "validatedComplete": validated_complete,
                        "validatedEvidence": validated_evidence,
                        "savedPrefix": saved_prefix, "tails": tails,
                        "contiguousEvidence": contiguous_evidence,
                        "replacement": replacement,
                        "replacementEvidence": replacement_evidence}

            trace = anyio.run(collect)
    return trace


def test_trigger_binds_the_actual_interrupted_canonical_query(recorded_trace):
    trace = recorded_trace
    assert trace["trigger"].get("canonicalJql") == trace["canonical"]
    assert trace["trigger"]["snapshotId"] == trace["first"]["result"]["data"]["snapshotId"]


def test_actual_unrelated_terminal_then_evidence_fails_despite_eventual_population(recorded_trace):
    trace = recorded_trace
    resumed = [trace["unrelated"], trace["earlyEvidence"], trace["eventual"]]
    assert trace["unrelated"]["result"]["data"]["canonicalJql"] != trace["canonical"]
    assert complete_search_membership(resumed, trace["canonical"]) == KEYS
    # The runner's eventual population condition can pass. It cannot erase the
    # fact that the unrelated group was entered before this population existed.
    assert audit_failed_group_first(trace["trigger"], resumed) == ORDER_FAILURE


def test_saved_actual_prefix_and_contiguous_resumption_allow_the_next_group(recorded_trace):
    trace = recorded_trace
    proof = [*trace["savedPrefix"], *trace["tails"]]
    assert complete_search_membership(trace["tails"], trace["canonical"]) is None
    assert complete_search_membership(proof, trace["canonical"]) == KEYS
    assert audit_failed_group_first(trace["trigger"], [*proof, trace["contiguousEvidence"]]) == []


def test_explicit_persisted_prefix_is_used_without_mutating_the_recorded_attempts(recorded_trace):
    trace = deepcopy(recorded_trace)
    before = deepcopy(trace)
    resumed = [*trace["tails"], trace["contiguousEvidence"]]
    assert audit_failed_group_first(trace["trigger"], resumed,
                                   prefix_calls=trace["savedPrefix"]) == []
    assert audit_failed_group_first(trace["trigger"], resumed) == ORDER_FAILURE
    assert trace == before


def test_actual_live_revalidated_replacement_snapshot_allows_the_next_group(recorded_trace):
    trace = recorded_trace
    replacement = trace["replacement"]
    assert replacement["result"]["data"]["snapshotId"] != trace["trigger"]["snapshotId"]
    assert replacement["result"]["cache"]["upstreamValidated"] is True
    assert complete_search_membership([replacement], trace["canonical"]) == KEYS
    assert audit_failed_group_first(trace["trigger"], [replacement, trace["replacementEvidence"]]) == []


@pytest.mark.parametrize("damage", [
    "unrelated", "tail-only", "gap", "failed", "partial", "stale", "invalidated"])
def test_nonproof_terminal_cannot_unlock_a_group_even_if_population_is_later_repaired(recorded_trace, damage):
    trace = deepcopy(recorded_trace)
    terminal = trace["eventual"]
    if damage == "unrelated":
        proof = [trace["unrelated"]]
    elif damage == "tail-only":
        proof = [trace["tails"][-1]]
    elif damage == "gap":
        proof = [*trace["savedPrefix"], trace["tails"][-1]]
    else:
        proof = [terminal]
        if damage == "failed":
            terminal["result"]["ok"] = False
            terminal["isError"] = True
        elif damage == "partial":
            terminal["result"]["partial"] = True
        elif damage == "stale":
            terminal["result"]["cache"]["state"] = "stale"
        elif damage == "invalidated":
            terminal["result"]["cache"]["invalidated"] = True
    assert complete_search_membership(proof, trace["canonical"]) is None
    # This actual evidence call occurred after the original terminal/tail pages
    # and before the replacement; do not repurpose the earlier adverse boundary.
    resumed = [*proof, trace["contiguousEvidence"], trace["replacement"]]
    assert complete_search_membership(resumed, trace["canonical"]) == KEYS
    assert audit_failed_group_first(trace["trigger"], resumed) == ORDER_FAILURE


@pytest.mark.parametrize("late_search", [True, False], ids=["search-completes-after-evidence", "sequential-positive"])
def test_actual_completion_order_controls_search_proof_not_dispatch_position(bundle, monkeypatch, late_search):
    with TemporaryDirectory() as directory:
        controller = RecoveryController("search-pagination")
        with EtlFixture(Path(directory) / "fixture", port=0, bundle=bundle,
                        controller=controller, skill_condition="candidate") as fixture:
            entered, release = Event(), Event()
            completion_order = []

            async def collect():
                async with Client(fixture.runtime.server) as client:
                    first = await client.call_tool("search_issues", {
                        "jql": QUERY, "detail": "summary", "max_results": 1})
                    assert not first.is_error
                    trigger = deepcopy(controller.trigger)
                    original = fixture.client.search_issues_page

                    def held_search(*args, **kwargs):
                        result = original(*args, **kwargs)
                        entered.set()
                        assert release.wait(timeout=5), "Search completion barrier was not released"
                        return result

                    monkeypatch.setattr(fixture.client, "search_issues_page", held_search)
                    offset = len(fixture.tool_calls)

                    async def search():
                        response = await client.call_tool("search_issues", {
                            "jql": QUERY, "detail": "summary", "max_results": 100})
                        assert not response.is_error, response.structured_content
                        completion_order.append("search")

                    async def evidence():
                        response = await client.call_tool("get_evidence_page", {
                            "key": "DL-31000", "kind": "comments"})
                        assert not response.is_error, response.structured_content
                        completion_order.append("evidence")

                    if late_search:
                        async with anyio.create_task_group() as group:
                            group.start_soon(search)
                            try:
                                with anyio.fail_after(8):
                                    assert await anyio.to_thread.run_sync(entered.wait, 3)
                                    await evidence()
                            finally:
                                release.set()
                    else:
                        release.set()
                        with anyio.fail_after(8):
                            await search()
                            await evidence()
                    return trigger, deepcopy(fixture.tool_calls[offset:])

            trigger, calls = anyio.run(collect)
    assert [call["name"] for call in calls] == ["search_issues", "get_evidence_page"]
    assert completion_order == (["evidence", "search"] if late_search else ["search", "evidence"])
    assert complete_search_membership(calls, trigger["canonicalJql"]) == KEYS
    assert audit_failed_group_first(trigger, calls) == (ORDER_FAILURE if late_search else [])


def test_missing_global_completion_markers_cannot_be_assumed_sequential(recorded_trace):
    trace = recorded_trace
    calls = deepcopy([trace["replacement"], trace["replacementEvidence"]])
    for call in calls:
        call.pop("startOrder", None)
        call.pop("endOrder", None)
    assert complete_search_membership(calls, trace["canonical"]) == KEYS
    assert audit_failed_group_first(trace["trigger"], calls) == ORDER_FAILURE


@pytest.mark.parametrize("validation_before", [False, True],
                         ids=["late-validation-rejected", "prior-validation-positive"])
def test_saved_complete_search_requires_current_validation_before_next_group(recorded_trace, validation_before):
    trace = recorded_trace
    calls = ([trace["validatedComplete"], trace["validatedEvidence"]] if validation_before else
             [trace["contiguousEvidence"], trace["replacement"]])
    assert complete_search_membership([*trace["savedComplete"], *calls], trace["canonical"]) == KEYS
    if validation_before:
        assert calls[0]["result"]["data"]["snapshotId"] == trace["savedComplete"][0]["result"]["data"]["snapshotId"]
    else:
        assert calls[0]["endOrder"] < calls[1]["startOrder"]
    assert audit_failed_group_first(trace["trigger"], calls, prefix_calls=trace["savedComplete"]) == (
        [] if validation_before else ORDER_FAILURE)


@pytest.fixture(scope="module", params=[("DL-31005", "comments", 2, 3), ("DL-31004", "history", 1, 2)],
                ids=["comments", "history"])
def evidence_trace(bundle, request):
    """Real two-page sources, with a held terminal response and durable prefix."""
    from app.jira import evidence_service

    key, kind, page_size, expected_total = request.param
    with TemporaryDirectory() as directory:
        root = Path(directory)
        files = root / "files"
        files.mkdir()
        controller = RecoveryController(kind + "-pagination")
        with EtlFixture(root / "fixture", port=0, files_root=files, bundle=bundle,
                        controller=controller, skill_condition="candidate") as fixture:
            entered, release = Event(), Event()
            completion_order = []

            async def collect():
                async with Client(fixture.runtime.server) as client:
                    async def page(start=0):
                        response = await client.call_tool("get_evidence_page", {
                            "key": key, "kind": kind, "start_at": start, "max_results": page_size})
                        assert not response.is_error, response.structured_content
                        assert response.structured_content["ok"] is True
                        return response

                    async def other():
                        response = await client.call_tool("get_evidence_page", {
                            "key": "DL-31006", "kind": "comments", "max_results": 1})
                        assert not response.is_error, response.structured_content
                        return response

                    first = await page()
                    assert first.structured_content["data"]["total"] == expected_total
                    assert first.structured_content["data"]["hasMore"] is True
                    tail_start = first.structured_content["data"]["nextStartAt"]
                    assert tail_start == page_size
                    prefix = deepcopy(fixture.tool_calls[-1])
                    trigger = deepcopy(controller.trigger)
                    assert trigger["key"] == key and trigger["kind"] == kind
                    await fixture.files.server.call_tool("write_file", {
                        "path": "evidence/source-prefix.json",
                        "content": json.dumps(first.structured_content, ensure_ascii=False)})
                    saved = persisted_calls(fixture.files, [prefix])
                    assert saved == [prefix]
                    original = evidence_service.get_evidence_page

                    def held_page(client, requested_key, requested_kind, start, *args):
                        result = original(client, requested_key, requested_kind, start, *args)
                        if (requested_key, requested_kind, start) == (key, kind, tail_start):
                            entered.set()
                            assert release.wait(timeout=5), "Evidence completion barrier was not released"
                        return result

                    async def terminal():
                        result = await page(tail_start)
                        assert result.structured_content["data"]["hasMore"] is False
                        completion_order.append("target")

                    offset = len(fixture.tool_calls)
                    with pytest.MonkeyPatch.context() as patch:
                        patch.setattr(evidence_service, "get_evidence_page", held_page)
                        async with anyio.create_task_group() as group:
                            group.start_soon(terminal)
                            try:
                                with anyio.fail_after(8):
                                    assert await anyio.to_thread.run_sync(entered.wait, 3)
                                    await other()
                                    completion_order.append("other")
                            finally:
                                release.set()
                    concurrent = [prefix, *deepcopy(fixture.tool_calls[offset:])]
                    assert completion_order == ["other", "target"]

                    # A genuine terminal tail alone cannot prove its missing
                    # first page, even if a later fresh collection repairs it.
                    offset = len(fixture.tool_calls)
                    await page(tail_start)
                    await other()
                    await page()
                    await page(tail_start)
                    tail_repair = deepcopy(fixture.tool_calls[offset:])

                    offset = len(fixture.tool_calls)
                    await page()
                    await page(tail_start)
                    await other()
                    sequential = deepcopy(fixture.tool_calls[offset:])
                    return {"key": key, "kind": kind, "trigger": trigger, "saved": saved,
                            "concurrent": concurrent, "tailRepair": tail_repair, "sequential": sequential}

            return anyio.run(collect)


def test_actual_evidence_completion_must_precede_the_next_group(evidence_trace):
    trace = evidence_trace
    calls = trace["concurrent"]
    assert evidence_collection(calls, trace["key"], trace["kind"])["complete"] is True
    assert calls[1]["startOrder"] < calls[2]["startOrder"] < calls[2]["endOrder"] < calls[1]["endOrder"]
    assert audit_failed_group_first(trace["trigger"], calls) == [
        "next_group_started_before_failed_group_completed"]


def test_actual_evidence_tail_then_other_group_cannot_be_repaired_later(evidence_trace):
    trace = evidence_trace
    calls = trace["tailRepair"]
    assert evidence_collection(calls[:1], trace["key"], trace["kind"])["complete"] is False
    assert evidence_collection(calls, trace["key"], trace["kind"])["complete"] is True
    assert audit_failed_group_first(trace["trigger"], calls) == [
        "next_group_started_before_failed_group_completed"]


def test_actual_evidence_sequential_collection_and_saved_prefix_allow_next_group(evidence_trace):
    trace = evidence_trace
    calls = trace["sequential"]
    assert evidence_collection(calls, trace["key"], trace["kind"])["complete"] is True
    assert audit_failed_group_first(trace["trigger"], calls) == []
    assert evidence_collection(calls[1:], trace["key"], trace["kind"])["complete"] is False
    assert evidence_collection([*trace["saved"], *calls[1:]], trace["key"], trace["kind"])["complete"] is True
    assert audit_failed_group_first(trace["trigger"], calls[1:], prefix_calls=trace["saved"]) == []


def test_actual_evidence_without_completion_markers_cannot_unlock_group(evidence_trace):
    trace = evidence_trace
    calls = deepcopy(trace["sequential"])
    for call in calls:
        call.pop("startOrder", None)
        call.pop("endOrder", None)
    assert evidence_collection(calls, trace["key"], trace["kind"])["complete"] is True
    assert audit_failed_group_first(trace["trigger"], calls) == [
        "next_group_started_before_failed_group_completed"]


@pytest.mark.parametrize("current_validation", [True, False], ids=["current-contiguous-tails", "saved-complete-no-current-read"])
def test_actual_runner_accepts_saved_prefix_and_current_contiguous_tails_without_recounting(bundle, monkeypatch,
                                                                                        current_validation):
    from tools.agent_eval_isolation import NETWORK_PREFLIGHT_MARKER

    class ScopedFixture(EtlFixture):
        def ground_truth(self):
            # Literal public three-key test scope; no production answers or
            # private business aggregation is substituted into the MCP server.
            return {"cases": {"public-search": {"keys": sorted(KEYS)}}}

    active = []

    def factory(path, **kwargs):
        fixture = ScopedFixture(path, **{**kwargs, "port": 0})
        active[:] = [fixture]
        return fixture

    def cli_run(_binary, _prompt, _workspace, **kwargs):
        fixture = active[0]
        first = kwargs.get("stop_event") is not None

        async def collect():
            async with Client(fixture.runtime.server) as client:
                if first:
                    response = await client.call_tool("search_issues", {
                        "jql": QUERY, "detail": "summary", "max_results": 1})
                    assert not response.is_error
                    if not current_validation:
                        response = await client.call_tool("search_issues", {
                            "jql": QUERY, "detail": "summary", "max_results": 100})
                        assert not response.is_error
                    await fixture.files.server.call_tool("write_file", {
                        "path": "evidence/prefix.json",
                        "content": json.dumps(response.structured_content, ensure_ascii=False)})
                else:
                    fragments, offset = [], 0
                    while True:
                        saved = await fixture.files.server.call_tool("read_file", {
                            "path": "evidence/prefix.json", "offset": offset})
                        data = saved.structured_content
                        fragments.append(data["content"])
                        if data["nextOffset"] is None:
                            break
                        offset = data["nextOffset"]
                    data = json.loads("".join(fragments))["data"]
                    while data["hasMore"]:
                        response = await client.call_tool("search_issues", {
                            "jql": QUERY, "detail": "summary", "max_results": 1,
                            "start_at": data["nextStartAt"], "snapshot_id": data["snapshotId"]})
                        assert not response.is_error, response.structured_content
                        data = response.structured_content["data"]
                    response = await client.call_tool("get_evidence_page", {
                        "key": "DL-31000", "kind": "comments"})
                    assert not response.is_error

        anyio.run(collect)
        return {"ok": True, "reply": "Read the complete public three-key population.",
                "intentionalInterruption": first, "durationSeconds": 1,
                "usage": {"promptTokens": 10, "completionTokens": 2,
                          "totalTokens": 12 if first else None}, "events": []}

    options = SimpleNamespace(date="2026-11-10", model="offline-stub", effort="high", timeout=30,
        recovery_scenario="search-pagination", recovery_mode="restarted",
        skill_condition="candidate", skill_transport="default")
    case = {"id": "public-search-recovery", "sourceCaseId": "public-search", "prompts": [QUERY]}
    battery = SimpleNamespace(SYSTEM_CONTEXT="", schema_for_case=lambda _case: {},
                              check=lambda *_args: {"pass": True, "failures": []})
    monkeypatch.setenv(NETWORK_PREFLIGHT_MARKER, "1")
    with TemporaryDirectory() as directory:
        root = Path(directory)
        record = run_recovery_case(case, options, "offline-stub", root / "run", cli_run,
            battery, artifact_root=root, fixture_factory=factory, bundle=bundle)
    assert len(record["phases"]) == 2
    trigger = record["recovery"]["trigger"]
    assert complete_search_membership(record["phases"][1]["mcpToolCalls"], trigger["canonicalJql"]) is None
    assert complete_search_membership(record["groundingCalls"], trigger["canonicalJql"]) == KEYS
    assert record["recovery"]["eligiblePersistedCalls"] == 1
    assert record["durationSeconds"] == 2
    assert record["output"]["usage"]["promptTokens"] == 20
    assert record["output"]["usage"]["totalTokens"] is None
    assert record.get("infrastructureError") is None
    assert record["automaticContract"]["failures"] == ([] if current_validation else [
        *ORDER_FAILURE, "recovery_population_not_proved"])
    assert record["automaticPass"] is current_validation
