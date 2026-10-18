"""Shared service-shaped data and scoped real progress-producer harness."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

WHEN = "2026-10-18T10:23:45.987654+09:00"


class Services:
    """Service-shaped source data, with controls only for scheduling/failure tests."""
    def __init__(self, seed="T-1", fail=None, empty=False):
        self.seed, self.fail = seed, fail
        self.calls, self.proofs, self.threads = [], [], []
        self.lock = threading.Lock()
        self.gate = self.hold = self.link_gate = None
        self.proof = None
        self.target = {"id": "raw-1", "key": seed.lower(), "fields": {
            "summary": "Target", "issuetype": {"id": "7", "name": "Task"},
            "parent": {"key": "T-90", "id": "parent-raw"}, "updated": WHEN}}
        self.rows = {
            "ticket_field_history": [{"date": WHEN[:10], "author": "past-user", "field": "status",
                                      "from": "Open", "to": "Done"} for _ in range(10)],
            "issue_comments": [{"id": "comment-1", "date": WHEN, "authorId": "past-user", "html": "x" * 420}],
            "ticket_children": [{"key": "T-2", "summary": "Child", "statusCat": "done", "updated": WHEN}],
            "ticket_related": [{"key": f"T-{i}", "rel": "mentioned"} for i in range(3, 12)],
            "ticket_documents": [{"title": "Remote link", "url": "https://example.invalid/pages/31",
                                  "linkId": "remote-link-9", "mention": False, "rel": "reference"},
                                 {"title": "Body reference", "url": "https://example.invalid/pages/32",
                                  "linkId": None, "mention": False, "rel": ""},
                                 {"title": "Unresolved", "url": "https://example.invalid/no-id",
                                  "linkId": None, "mention": True, "rel": "mentioned"},
                                 {"title": "Fourth", "url": "https://example.invalid/pages/33",
                                  "linkId": "remote-link-10", "mention": False, "rel": ""}],
        }
        if empty:
            self.rows = {name: [] for name in self.rows}

    def __getattr__(self, operation):
        def call(*args):
            with self.lock:
                self.calls.append((operation, args))
                self.proofs.append(self.proof.get() if self.proof is not None else None)
                self.threads.append((operation, threading.get_ident()))
            if self.gate is not None and operation in self.rows:
                self.gate.wait(timeout=5)
                if self.hold is not None:
                    assert self.hold.wait(timeout=5), "test release timed out"
            if self.link_gate is not None and operation == "get_issue" and args[0] != self.seed:
                self.link_gate.wait(timeout=5)
                if args[0] != "T-3":
                    assert self.hold.wait(timeout=5), "test release timed out"
            if self.fail == operation or self.fail == (operation, args[0]):
                raise LookupError("synthetic denied; do not capture exception text")
            if operation == "get_issue":
                return self.target if args[0] == self.seed else {
                    "key": "returned-" + args[0], "fields": {"updated": WHEN, "issuetype": {"name": "Bug"}}}
            if operation == "ticket_badge":
                return {"key": args[0], "assigneeId": "child-user", "updated": WHEN}
            if operation == "confluence_page":
                page = {"version": {"when": WHEN}, "body": {"storage": {"value": "z" * 920}}}
                if args[0] == "31":
                    page.update({"id": "returned-31", "space": {"key": "DOC", "id": "19"}})
                return page
            return self.rows[operation]
        return call


@pytest.fixture
def report_contract():
    """Data captured from the pre-capture producer, not a second implementation."""
    path = Path(__file__).resolve().parents[1] / "agent/core/fixtures" / "progress_report_contract.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def progress_runtime(monkeypatch):
    from app.agent.tools import _ctx, survey_tools

    selected = ContextVar("progress_test_service")
    proof = ContextVar("progress_test_provenance", default="test-window")
    class RoutedClient:
        def __getattr__(self, name):
            return getattr(selected.get(), name)
    @contextmanager
    def select(service):
        service.proof = proof
        token = selected.set(service)
        try:
            yield
        finally:
            selected.reset(token)

    def invoke(service, key=None, **options):
        with select(service):
            return survey_tools.progress_report(key or service.seed, comment_limit=6, **options)

    # Reuse the existing test-injection seam and restore the exact previous binding.
    with monkeypatch.context() as scoped:
        scoped.setattr(_ctx, "_bound", (RoutedClient(), SimpleNamespace(search_jira_projects=["T"])))
        yield SimpleNamespace(producer=survey_tools, invoke=invoke, select=select, proof=proof)


def calls_from(contract):
    return Counter((method, tuple(args)) for method, args in contract["calls"])


def assert_public_contract(runtime, report_contract, result, service):
    """Check the full public report, actual decorated tool and exact service calls."""
    assert result == report_contract["report"]
    assert json.dumps(result, ensure_ascii=False) == json.dumps(report_contract["report"], ensure_ascii=False)
    assert Counter(service.calls) == calls_from(report_contract)
    assert set(service.proofs) == {"test-window"}
    assert "acquisition" not in result
    tool = runtime.producer.get_ticket_progress
    assert tool.args_schema.model_json_schema() == report_contract["tool_schema"]
    public_service = Services()
    with runtime.select(public_service):
        assert tool.invoke({"ticket_key": "T-1"}) == report_contract["report"]
    expected = calls_from(report_contract)
    del expected[("issue_comments", ("T-1", 6))]
    expected[("issue_comments", ("T-1", 10))] = 1
    assert Counter(public_service.calls) == expected

