from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.agent.tools.progress_snapshot import ProgressAcquisition, ReadObservation
from app.agent.workflow.progress_evidence import progress_evidence
from app.agent.maxis.backend import _verified_evidence_sources


def sample():
    acquisition = ProgressAcquisition()
    acquisition.begin("DL-42")
    acquisition.finished = True
    for branch in acquisition.branches.values():
        branch.state = "returned"
        branch.reads = [ReadObservation("bounded_read", (), "returned", [])]
    acquisition.branches["target"].reads = [
        ReadObservation("get_issue", ("DL-42",), "returned", {"key": "DL-42"})]
    acquisition.branches["documents"].reads = [
        ReadObservation("confluence_page", ("123",), "returned",
                        {"id": "123", "space": {"key": "DESIGN"}})]
    report = {"key": "DL-42", "title": "진척 확인", "status": "In Progress",
              "comments": [{"text": "성능 측정이 남았습니다."}], "changes": [],
              "children": [], "links": [], "documents": [
                  {"url": "https://wiki.example/pages/viewpage.action?pageId=123",
                   "title": "검증 문서", "excerpt": "문서 전용 내용"}]}
    receipt = {"retrieved_at": datetime.now(timezone.utc), "partial": False,
               "tracked": True, "served_stale": False, "read_count": 3,
               "upstream_observed": True}
    return report, acquisition, receipt


def test_actual_progress_projection_separates_scopes_and_preserves_digest():
    report, acquisition, receipt = sample()
    before = deepcopy(report)
    patch = progress_evidence("DL-42", report, acquisition, receipt)
    sources = _verified_evidence_sources(patch)
    assert report == before
    by_kind = {row["kind"]: row for row in sources}
    assert set(by_kind) == {"jira", "confluence"}
    assert by_kind["jira"]["scope"] == "DL"
    assert by_kind["confluence"]["scope"] == "DESIGN"
    assert "문서 전용 내용" not in by_kind["jira"]["content"]
    assert "문서 전용 내용" in by_kind["confluence"]["content"]
    assert all(not row["partial"] for row in sources)
    assert "progress-report" not in by_kind["jira"]["content"]
    assert all(row["retrieved_at"] == receipt["retrieved_at"].isoformat().replace("+00:00", "Z")
               for row in sources)


@pytest.mark.parametrize("categories", [[], ["done", "done", "indeterminate"],
                                      ["done", "inprogress", "todo"]])
def test_child_counts_are_computed_from_verified_returned_rows(categories):
    report, acquisition, receipt = sample()
    rows = [{"key": f"DL-{index + 50}", "statusCat": status}
            for index, status in enumerate(categories)]
    report["children"] = [{"key": row["key"], "done": row["statusCat"] == "done"}
                          for row in rows]
    acquisition.branches["children"].reads = [
        ReadObservation("ticket_children", ("DL-42",), "returned", rows,
                        returned_count=len(rows))]
    original = deepcopy(report)
    patch = progress_evidence("DL-42", report, acquisition, receipt)
    result = next(row["result"] for row in patch["query_results"] if row["source"] == "jira")
    assert result["children_counts"] == {
        "returned": f"{len(rows)}건 (티켓 {len(rows)}개)",
        "done": f"{categories.count('done')}건 (티켓 {categories.count('done')}개)",
        "not_done": f"{len(rows) - categories.count('done')}건 (티켓 {len(rows) - categories.count('done')}개)",
        "basis": "조회 반환 하위 티켓 개수; SP 기반 진척률이나 전체 프로젝트 규모가 아님"}
    assert report == original


@pytest.mark.parametrize("fault", ["failed", "key", "done", "count", "status", "duplicate",
                                  "stale", "partial", "untracked", "zero_reads"])
def test_unverified_children_do_not_create_derived_counts(fault):
    report, acquisition, receipt = sample()
    report["children"] = [{"key": "DL-50", "done": True}]
    branch = acquisition.branches["children"]
    branch.reads = [ReadObservation("ticket_children", ("DL-42",), "returned",
                                   [{"key": "DL-50", "statusCat": "done"}], returned_count=1)]
    if fault == "failed":
        branch.state = "failed"
    elif fault == "key":
        report["children"][0]["key"] = "DL-51"
    elif fault == "done":
        report["children"][0]["done"] = 1
    elif fault == "count":
        branch.reads[0].returned_count = 2
    elif fault == "status":
        branch.reads[0].metadata[0].pop("statusCat")
    elif fault == "duplicate":
        report["children"] *= 2
        branch.reads[0].metadata *= 2
        branch.reads[0].returned_count = 2
    else:
        receipt.update({"stale": {"served_stale": True}, "partial": {"partial": True},
                        "untracked": {"tracked": False}, "zero_reads": {"read_count": 0}}[fault])
    patch = progress_evidence("DL-42", report, acquisition, receipt)
    result = next(row["result"] for row in patch["query_results"] if row["source"] == "jira")
    assert "children_counts" not in result


@pytest.mark.parametrize("fault", ["target", "failure", "skipped", "stale", "untracked",
                                   "document_space", "document_identity", "document_failed"])
def test_missing_or_stale_proof_never_becomes_complete_or_borrows_jira_scope(fault):
    report, acquisition, receipt = sample()
    if fault == "target":
        acquisition.branches["target"].reads[0].metadata["key"] = "DL-99"
    elif fault in {"failure", "skipped"}:
        acquisition.branches["comments"].state = "failed" if fault == "failure" else "skipped"
    elif fault == "stale":
        receipt["served_stale"] = True
    elif fault == "untracked":
        receipt["tracked"] = False
    elif fault == "document_space":
        acquisition.branches["documents"].reads[0].metadata.pop("space")
    elif fault == "document_identity":
        acquisition.branches["documents"].reads[0].metadata["id"] = "456"
    else:
        acquisition.branches["documents"].state = "failed"
    sources = _verified_evidence_sources(progress_evidence("DL-42", report, acquisition, receipt))
    jira = next(row for row in sources if row["kind"] == "jira")
    assert jira["partial"]
    assert "문서 전용 내용" not in jira["content"]
    if fault in {"target", "document_space", "document_identity"}:
        assert all(row["kind"] != "confluence" for row in sources)
    if fault == "target":
        assert "성능 측정" not in jira["content"]


@pytest.mark.parametrize("operation,branch,field", [
    ("get_issue", "links", "status"), ("ticket_badge", "children", "assigneeId"),
])
def test_mismatched_nested_identity_removes_content_not_only_coverage(operation, branch, field):
    report, acquisition, receipt = sample()
    report[branch] = [{"key": "DL-43", field: "Wrong-owner-closed"}]
    acquisition.branches[branch].reads.append(
        ReadObservation(operation, ("DL-43",), "returned", {"key": "DL-99"}))
    sources = _verified_evidence_sources(progress_evidence("DL-42", report, acquisition, receipt))
    jira = next(row for row in sources if row["kind"] == "jira")
    assert jira["partial"] and "Wrong-owner-closed" not in jira["content"]


def test_real_compacting_producer_keeps_empty_reads_and_failed_branches_distinct(monkeypatch):
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage
    from app.agent.tools import _ctx
    from app.agent.workflow.agents.portfolio_analyst import _ticket_progress_material
    from app.infra.cache import Cache
    from support.progress_services import Services

    cache = Cache(":memory:")
    class Source(Services):
        def __getattr__(self, name):
            original = super().__getattr__(name)
            def call(*args):
                return cache.get_or_set(name, 60, lambda: original(*args))[0]
            return call
    source = Source(seed="DL-42", empty=True)
    source.cache = cache
    source.target["fields"]["status"] = {"name": "Open"}
    monkeypatch.setattr(_ctx, "_bound", (source, SimpleNamespace(search_jira_projects=["DL"])))
    from app.agent import tools
    monkeypatch.setitem(tools.BY_NAME, "get_epic_tree",
                        SimpleNamespace(invoke=lambda _: {"children": []}))
    state = {"messages": [HumanMessage(content="DL-42 진척")], "intent": "progress",
             "runtime_role_routes": {"result_integrator": "maxis"}}
    try:
        clean = _ticket_progress_material(state)
        assert clean["complete"], clean
        assert clean["snapshot"]["tickets"][0]["children"] == []
        assert len(clean["query_results"]) == 1
        assert not _verified_evidence_sources(clean)[0]["partial"]
        # A failed call is not an empty successful read; use a new cache key.
        source.fail = "issue_comments"
        cache.invalidate("issue_comments")
        failed = _ticket_progress_material(state)
        assert not failed["complete"]
        assert _verified_evidence_sources(failed)[0]["partial"]
    finally:
        cache.close()


@pytest.mark.parametrize("operation", ["changelog", "remote_links", "documents", "related", "children"])
def test_actual_jira_failure_cannot_become_successful_empty_evidence(operation):
    from types import SimpleNamespace
    from app.jira.jira_client import JiraClient
    from app.infra.cache import Cache
    client = object.__new__(JiraClient)
    client.cache = Cache(":memory:")
    client.s = SimpleNamespace(cache_ttl_seconds=3600, confluence_base="")
    client.env = "mock"
    def offline(*args, **kwargs):
        raise OSError("synthetic offline")
    client._provider = SimpleNamespace(get_json=offline)
    client._provider_built = True
    client._swr = lambda key, build: client.cache.get_or_set(key, 3600, build)[0]
    client.ticket_view = offline
    client.ticket_ancestors = lambda key: []
    client.ticket_children = lambda key: []
    client.get_issue = offline
    client._child_keys = lambda *args, **kwargs: ["DL-43"]
    client.prefetch_issues = lambda *args, **kwargs: None
    client.ticket_badge = lambda key: None
    calls = {
        "changelog": lambda: client.ticket_field_history("DL-42"),
        "remote_links": lambda: client._remote_links("DL-42"),
        "documents": lambda: client.ticket_documents("DL-42"),
        "related": lambda: client.ticket_related("DL-42"),
        "children": lambda: JiraClient.ticket_children(client, "DL-42"),
    }
    # Best-effort UI is permitted to return empty, but that cached projection
    # must not be reused as an authoritative empty acquisition.
    if operation in {"changelog", "remote_links", "children"}:
        assert calls[operation]() == []
    marker = client.cache.provenance_marker(max_age_seconds=900)
    try:
        with pytest.raises((OSError, ValueError)):
            calls[operation]()
        proof = client.cache.provenance_since(marker)
        assert proof["count"] == 0 and not proof["upstream"]
    finally:
        client.cache.provenance_since(marker)
        client.cache.close()


def test_confluence_evidence_requests_scope_and_preserves_ui_fallback():
    from types import SimpleNamespace
    from app.jira.jira_client import JiraClient
    from app.infra.cache import Cache
    client = object.__new__(JiraClient)
    client.cache = Cache(":memory:")
    client.s = SimpleNamespace(confluence_base="")
    requests = []
    def offline(url, params):
        requests.append(params["expand"])
        raise OSError("synthetic offline")
    client._conf_get_json = offline
    try:
        assert client.confluence_page("123") == {}
        observer = client.cache.provenance_marker()
        assert client.confluence_page("123") == {}
        client.cache.provenance_since(observer)
        bounded = client.cache.provenance_marker(max_age_seconds=900)
        with pytest.raises(OSError):
            client.confluence_page("123")
        client.cache.provenance_since(bounded)
        assert requests == ["body.storage", "body.storage", "body.storage,space,version"]
        assert client.confluence_page("123") == {}
    finally:
        client.cache.close()


@pytest.mark.parametrize("age", [800, 1800])
def test_bounded_aggregate_never_retimes_leaf_cache(age):
    import time
    from types import SimpleNamespace
    from app.jira.jira_client import JiraClient
    from app.infra.cache import Cache
    client = object.__new__(JiraClient)
    client.cache = Cache(":memory:")
    client.s, client.env = SimpleNamespace(cache_ttl_seconds=3600), "mock"
    client._child_keys = lambda *args, **kwargs: ["DL-43"]
    client.prefetch_issues = lambda *args, **kwargs: None
    client._swr = lambda key, build: client.cache.get_or_set(key, 3600, build)[0]
    client.cache.set("leaf", {"key": "DL-43"}, 3600)
    original = time.time() - age
    client.cache._conn.execute("UPDATE cache SET fetched_at=? WHERE key='leaf'", (original,))
    client.cache._conn.commit()
    def offline():
        raise OSError("synthetic offline")
    client.ticket_badge = lambda key: client.cache.get_or_set("leaf", 3600, offline)[0]
    try:
        for _ in range(2):
            marker = client.cache.provenance_marker(max_age_seconds=900)
            assert client.ticket_children("DL-42") == [{"key": "DL-43"}]
            proof = client.cache.provenance_since(marker)
            assert proof["oldestFetchedAt"] == original
            assert proof["servedStale"] == (age > 900)
            assert not proof["upstream"]
        assert client.cache.get("children:mock:DL-42") is None
        assert client.cache.get("children:mock:DL-42:evidence") is None
    finally:
        client.cache.close()
