"""Acquisition-only metadata: real producer, shared scoped services, no provider IO."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

from support.progress_services import (
    Services, WHEN, assert_public_contract, calls_from, progress_runtime, report_contract,
)

FAILURES = [
    ("ticket_field_history", "changes", ()), ("issue_comments", "comments", ()),
    ("ticket_children", "children", ("ticket_badge",)),
    ("ticket_related", "links", ("linked_get_issue",)),
    ("ticket_documents", "documents", ("confluence_page",)),
    ("ticket_badge", "children", ()), (("get_issue", "T-10"), "links", ()),
    ("confluence_page", "documents", ("second_page",)),
]


@pytest.fixture
def runtime(progress_runtime):
    from app.agent.tools import progress_snapshot

    def invoke(service, collector=None, key=None):
        snapshot = collector or progress_snapshot.ProgressAcquisition()
        result = progress_runtime.invoke(service, key=key, acquisition=snapshot)
        return result, snapshot

    return SimpleNamespace(producer=progress_runtime.producer, metadata=progress_snapshot,
                           invoke=invoke, select=progress_runtime.select, proof=progress_runtime.proof)


def assert_unknown(snapshot):
    assert (snapshot.provider_coverage, snapshot.cache_provenance, snapshot.source_freshness) == (
        "unknown", "unknown", "unknown")


def test_captured_report_preserves_public_contract(runtime, report_contract):
    service = Services()
    result, snapshot = runtime.invoke(service, key=" t-1 ")
    assert_public_contract(runtime, report_contract, result, service)
    assert snapshot.finished
    assert_unknown(snapshot)


def test_raw_owner_history_dates_and_projection_limits(runtime):
    result, snapshot = runtime.invoke(Services())
    branches = snapshot.branches
    target = branches["target"].reads[0].metadata
    assert target["key"] == "t-1"
    assert target["fields"]["parent"] == {"key": "T-90", "id": "parent-raw"}
    assert target["fields"]["issuetype"]["name"] == "Task"
    assert target["fields"]["updated"] == WHEN and result["updated"] == WHEN[:10]
    assert branches["comments"].reads[0].metadata[0]["date"] == WHEN
    history = branches["changes"].reads[0].metadata[0]
    assert (history["date"], history["to"]) == (WHEN[:10], "Done")
    assert branches["children"].reads[0].metadata[0]["key"] == "T-2"
    assert branches["links"].reads[1].arguments == ("T-3",)
    assert branches["links"].reads[1].metadata["key"] == "returned-T-3"
    for name, count in (("changes", 8), ("links", 8), ("documents", 3)):
        assert branches[name].projected_count == count
        assert branches[name].reads[0].returned_count > count
    assert branches["comments"].projection_policy["text_chars"] == 400


def test_document_link_request_and_page_identities_stay_distinct(runtime):
    service = Services()
    _, snapshot = runtime.invoke(service)
    listing, page, unknown = snapshot.branches["documents"].reads
    assert listing.metadata[0]["linkId"] == service.rows["ticket_documents"][0]["linkId"]
    assert listing.metadata[1]["linkId"] is None
    assert all("id" not in row and "space" not in row for row in listing.metadata)
    assert page.arguments == ("31",)
    assert page.metadata["id"] == "returned-31"
    assert page.metadata["space"] == {"key": "DOC", "id": "19"}
    assert listing.metadata[0]["linkId"] != page.metadata["id"]
    assert "linkId" not in page.metadata
    assert "id" not in unknown.metadata and "space" not in unknown.metadata


@pytest.mark.parametrize("failure,branch_name,omitted", FAILURES)
def test_branch_failure_keeps_public_fallback_and_unknowns(runtime, report_contract, failure, branch_name, omitted):
    service = Services(fail=failure)
    result, snapshot = runtime.invoke(service)
    expected = deepcopy(report_contract["report"])
    expected.pop(branch_name)
    if branch_name == "children":
        expected.pop("children_done")
    assert result == expected
    expected_calls = calls_from(report_contract)
    for method, args in list(expected_calls):
        if (method in omitted or ("linked_get_issue" in omitted and method == "get_issue" and args != ("T-1",))
                or ("second_page" in omitted and method == "confluence_page" and args == ("32",))):
            del expected_calls[(method, args)]
    assert Counter(service.calls) == expected_calls
    branch = snapshot.branches[branch_name]
    assert (branch.state, branch.error_type) == ("failed", "LookupError")
    assert any(read.state == "failed" for read in branch.reads)
    assert "synthetic denied" not in repr(snapshot)
    assert snapshot.finished
    assert all(b.state == "returned" for name, b in snapshot.branches.items() if name != branch_name)
    assert_unknown(snapshot)


@pytest.mark.parametrize("kind", ["failed", "missing", "scope"])
def test_target_early_exit_marks_unattempted_branches(runtime, kind):
    service = Services(fail="get_issue" if kind == "failed" else None)
    if kind == "missing":
        service.target = {}
    if kind == "scope":
        service.seed = "NO-1"
    snapshot = runtime.metadata.ProgressAcquisition()
    if kind == "failed":
        with pytest.raises(LookupError, match="synthetic denied"):
            runtime.invoke(service, collector=snapshot)
    else:
        result, _ = runtime.invoke(service, collector=snapshot)
        assert result == {"error": ("T-1 티켓을 찾을 수 없습니다." if kind == "missing" else
                                     "티켓이 search.jira.projects 범위 밖이거나 검색 범위가 비어 있습니다.")}
    assert service.calls == ([] if kind == "scope" else [("get_issue", ("T-1",))])
    assert snapshot.finished
    assert all(b.state == "skipped" for name, b in snapshot.branches.items() if name != "target")
    assert_unknown(snapshot)


def test_pending_during_live_workers_then_empty_uncertainty(runtime):
    service, snapshot = Services(empty=True), runtime.metadata.ProgressAcquisition()
    service.gate, service.hold = threading.Barrier(6), threading.Event()
    assert all(b.state == "pending" for b in snapshot.branches.values())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.invoke, service, collector=snapshot)
        try:
            service.gate.wait(timeout=5)
            assert snapshot.branches["target"].state == "returned"
            assert all(b.state == "pending" for name, b in snapshot.branches.items() if name != "target")
            assert not snapshot.finished
        finally:
            service.hold.set()
        future.result(timeout=5)
    assert snapshot.finished and all(b.state == "returned" for b in snapshot.branches.values())
    assert_unknown(snapshot)


def test_source_metadata_is_copied_and_collector_is_single_use(runtime):
    service = Services()
    before = deepcopy((service.target, service.rows))
    _, snapshot = runtime.invoke(service)
    assert (service.target, service.rows) == before
    service.target["fields"]["parent"]["key"] = "mutated"
    assert snapshot.branches["target"].reads[0].metadata["fields"]["parent"]["key"] == "T-90"
    snapshot.branches["comments"].reads[0].metadata[0]["authorId"] = "mutated"
    assert service.rows["issue_comments"][0]["authorId"] == "past-user"
    with pytest.raises(ValueError, match="single-use"):
        runtime.invoke(service, collector=snapshot)


def test_metadata_copy_failure_preserves_identity_and_producer_result(runtime, report_contract):
    class CannotCopy(str):
        def __deepcopy__(self, memo):
            raise ValueError("private metadata-copy error")
    value, calls = {"id": CannotCopy("raw-1")}, []
    def source(key):
        calls.append(key)
        return value
    branch = runtime.metadata.BranchObservation()
    assert runtime.metadata.observe(branch, "get_issue", source, "T-1") is value
    assert calls == ["T-1"]
    read = branch.reads[0]
    assert (read.state, read.metadata, read.error_type) == ("returned", None, "ValueError")
    assert "private metadata-copy error" not in repr(read)
    service = Services()
    service.target["id"] = value["id"]
    result, snapshot = runtime.invoke(service)
    assert result == report_contract["report"]
    assert Counter(service.calls) == calls_from(report_contract)
    target = snapshot.branches["target"]
    assert target.state == "returned" and target.reads[0].metadata is None
    assert target.reads[0].error_type == "ValueError"
    assert_unknown(snapshot)


def test_five_way_concurrency_and_context_isolation(runtime):
    def one(seed):
        service = Services(seed)
        service.gate = threading.Barrier(5)
        token = runtime.proof.set(seed)
        try:
            _, snapshot = runtime.invoke(service)
        finally:
            runtime.proof.reset(token)
        assert set(service.proofs) == {seed}
        assert len({tid for name, tid in service.threads if name in service.rows}) == 5
        return snapshot
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(one, ("T-101", "T-102")))
    assert a.branches is not b.branches
    assert a.branches["target"].reads[0].metadata["key"] == "t-101"
    assert b.branches["target"].reads[0].metadata["key"] == "t-102"
    assert a.branches["comments"].reads[0].arguments == ("T-101", 6)
    assert b.branches["comments"].reads[0].arguments == ("T-102", 6)


def test_early_link_failure_cancels_queued_reads(runtime, monkeypatch, report_contract):
    service = Services(fail=("get_issue", "T-3"))
    service.link_gate, service.hold = threading.Barrier(4), threading.Event()
    class CancelProbePool(ThreadPoolExecutor):
        def submit(self, fn, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)
            if self._max_workers == 4:
                future.add_done_callback(lambda f: service.hold.wait(5)
                                         if not f.cancelled() and f.exception() else None)
            return future

        def __exit__(self, *args):
            if self._max_workers == 4:
                service.hold.set()
            return super().__exit__(*args)
    monkeypatch.setattr("concurrent.futures.ThreadPoolExecutor", CancelProbePool)
    result, snapshot = runtime.invoke(service)
    expected = deepcopy(report_contract["report"])
    expected.pop("links")
    assert result == expected
    expected_calls = calls_from(report_contract)
    for i in range(7, 11):
        del expected_calls[("get_issue", (f"T-{i}",))]
    assert Counter(service.calls) == expected_calls
    branch = snapshot.branches["links"]
    assert (branch.state, branch.reason) == ("failed", "remaining_link_outcomes_uncollected")
    assert_unknown(snapshot)
