"""Atomic semantic gates run in literals; their no-op result must not reformat bytes."""
import pytest

from app.agent.workflow.evidence_index import (
    build_atomic_fact_ledger, enforce_atomic_fact_boundaries,
)


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("value", [
    "  unchanged  .\n\n\n", "   ~~~text\n  keep(  1  )\n\n\n   ~~~\n",
    "    literal\n    literal\n", "`  inline  `\n",
])
def test_atomic_fact_noop_preserves_reply_bytes(newline, value):
    raw = value.replace("\n", newline)
    facts = build_atomic_fact_ledger({"materialized_ticket_sources": {"ticketDetails": [
        {"key": "ACME-10", "status": "Closed", "duedate": "2026-09-30", "updated": "2026-08-17"},
    ]}})
    assert enforce_atomic_fact_boundaries(raw, []) == raw
    assert enforce_atomic_fact_boundaries(raw, facts) == raw


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("wrapper", ["{claim}", "~~~text\n{claim}\n~~~", "`{claim}`", "    {claim}"])
@pytest.mark.parametrize("kind", ["date", "due", "status"])
def test_atomic_fact_semantic_repairs_still_run_inside_literals(newline, wrapper, kind):
    details = []
    if kind == "date":
        claim = "2026-08-11부터 2026-08-25(금요일)까지 한 주이므로 즉시 배포를 권고합니다."
        expected, forbidden = "정확히 14일", "배포를 권고"
    elif kind == "due":
        details = [{"key": "ACME-10", "duedate": "2026-09-30", "updated": "2026-08-17"},
                   {"key": "ACME-11", "parentKey": "ACME-10", "updated": "2026-08-18"}]
        claim = "ACME-11의 마감은 2026-09-30"
        expected, forbidden = "마감 확인되지 않음", "2026-09-30"
    else:
        details = [{"key": "ACME-81", "status": "Closed", "updated": "2026-08-10"},
                   {"key": "ACME-82", "status": "In Progress", "updated": "2026-08-10"}]
        claim = "ACME-81 및 ACME-82는 Jira 상태 In Progress입니다."
        expected, forbidden = "ACME-81=Closed; ACME-82=In Progress", "ACME-81 및 ACME-82는"
    facts = build_atomic_fact_ledger({"materialized_ticket_sources": {"ticketDetails": details}})
    raw = wrapper.format(claim=claim).replace("\n", newline)
    got = enforce_atomic_fact_boundaries(raw, facts)
    assert expected in got and forbidden not in got
    if kind == "date":
        assert "2026-08-25(화요일)" in got


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_supported_shared_status_is_an_exact_noop(newline):
    facts = build_atomic_fact_ledger({"materialized_ticket_sources": {"ticketDetails": [
        {"key": "ACME-83", "status": "Closed", "updated": "2026-08-10"},
        {"key": "ACME-84", "status": "Closed", "updated": "2026-08-10"},
    ]}})
    raw = "   ~~~text" + newline + "ACME-83 및 ACME-84는 Jira 상태 Closed입니다." + newline + "   ~~~" + newline
    assert enforce_atomic_fact_boundaries(raw, facts) == raw
