"""Content inclusion is independent of display detail and never mutates shared views."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.local_mcp.read_tools import register_reads


@pytest.mark.parametrize("detail,include,expected", [
    ("summary", True, {"descriptionHtml"}),
    ("read", False, set()), ("edit", False, set()), ("full", False, set()),
])
def test_ticket_explicit_body_selection(detail, include, expected):
    shared = {"key": "DL-1", "summary": "A ticket", "descriptionHtml": "<p>Display</p>",
              "descriptionEditHtml": "<p>Edit</p>", "descriptionFormat": "html",
              "descriptionSections": [{"html": "<p>Display</p>"}]}
    original = deepcopy(shared)
    calls = []

    def view(key, **kwargs):
        calls.append((key, kwargs))
        return shared

    tools = {}
    register_reads(lambda fn, **kwargs: tools.setdefault(fn.__name__, fn),
                   SimpleNamespace(client=SimpleNamespace(ticket_view=view), check_key=lambda key: None))
    result = tools["get_ticket"]("DL-1", detail=detail, include_description=include)
    assert result["summary"] == "A ticket"
    assert result.keys() & {"descriptionHtml", "descriptionEditHtml", "descriptionFormat", "descriptionSections"} == expected
    assert calls[0][1]["include_description"] is include
    assert shared == original


def test_ticket_compact_keeps_requested_comments_and_coverage():
    raw = [{"id": "11", "body": "Earlier evidence"}, {"id": "12", "body": "Latest evidence"}]
    coverage = {"total": 2, "returned": 2, "startAt": 0, "complete": True, "hasMore": False, "nextStartAt": None}
    shared = {"key": "DL-1", "summary": "Ticket", "comments": raw,
              "commentsCoverage": coverage, "commentsRequested": True, "commentsMissingKeys": []}
    tools = {}
    register_reads(lambda fn, **kwargs: tools.setdefault(fn.__name__, fn),
                   SimpleNamespace(client=SimpleNamespace(ticket_view=lambda *args, **kwargs: shared),
                                   check_key=lambda key: None))
    result = tools["get_ticket"]("DL-1", detail="summary", include_comments=True)
    assert result["comments"] == raw
    assert result["commentsCoverage"] == coverage
    assert result["commentsRequested"] and result["commentsMissingKeys"] == []
