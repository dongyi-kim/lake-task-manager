# -*- coding: utf-8 -*-
"""Direct meeting identity and Auditor contracts; no graph/effect execution involved."""

from itertools import permutations

from app.agent.workflow.agents import auditor
from app.agent.workflow.meeting_context import (
    meeting_assignment_bindings, meeting_assignment_source_mapping,
)


def _three_outcome_fixture():
    items = [
        {
            "item_id": "work-item:consumer",
            "summary": "Acme consumer verification",
            "duedate": "2031-04-12",
            "assignee": "acct.consumer",
            "assignee_source": "user",
            "meeting_assignment_ref": "decision-2",
            "outcome_refs": ["outcome:consumer"],
        },
        {
            "item_id": "work-item:privacy",
            "summary": "Acme privacy checklist",
            "duedate": "2031-04-13",
            "assignee_source": "user_unassigned",
            "meeting_assignment_ref": "decision-3",
            "outcome_refs": ["outcome:privacy"],
        },
        {
            "item_id": "work-item:producer",
            "summary": "Acme producer evidence",
            "duedate": "2031-04-11",
            "assignee": "acct.producer",
            "assignee_source": "user",
            "meeting_assignment_ref": "decision-1",
            "outcome_refs": ["outcome:producer"],
        },
    ]
    records = [
        {
            "owner": "Producer Owner",
            "work": "Acme producer evidence",
            "due": "2031-04-11",
            "owner_decision": "assigned",
            "outcome_ref": "outcome:producer",
            "source_evidence": {"kind": "meeting_minutes", "record_id": "decision-1"},
        },
        {
            "owner": "acct.consumer",
            "work": "Acme consumer verification",
            "due": "2031-04-12",
            "owner_decision": "assigned",
            "outcome_ref": "outcome:consumer",
            "source_evidence": {"kind": "meeting_minutes", "record_id": "decision-2"},
        },
        {
            "owner": "",
            "work": "Acme privacy checklist",
            "due": "2031-04-13",
            "owner_decision": "unassigned",
            "outcome_ref": "outcome:privacy",
            "source_evidence": {"kind": "meeting_minutes", "record_id": "decision-3"},
        },
    ]
    people = {
        "Producer Owner": "acct.producer",
        "Consumer Owner": "acct.consumer",
        "Unrelated Observer": "acct.observer",
    }
    return items, records, people


def _state(items):
    return {
        "request_text": "Acme 회의록 결정으로 세 Task를 생성",
        "continuation_contract": {
            "version": "continuation.v1",
            "action": "create",
            "root_request": "Acme 회의록 결정으로 세 Task를 생성",
            "decisions": [],
        },
        "draft": {"mode": "task", "items": items},
    }


def _runtime_state(monkeypatch, items, records, people):
    """Inject outputs of manifested runtime readers, never undeclared state keys."""
    monkeypatch.setattr(auditor, "is_meeting_request", lambda _state: True)
    monkeypatch.setattr(auditor, "meeting_owner_records", lambda _state: list(records))
    monkeypatch.setattr(auditor, "resolved_people", lambda _state: dict(people))
    monkeypatch.setattr(auditor, "meeting_requester_instructors", lambda _state: [])
    return _state(items)


def test_auditor_meeting_authority_reads_only_manifested_runtime_inputs(monkeypatch):
    from app.agent.workflow.role_manifest import ROLE_SPECS

    items, records, people = _three_outcome_fixture()
    state = _runtime_state(monkeypatch, items, records, people)
    manifested = set(ROLE_SPECS["auditor"].input_keys)

    assert {"messages", "request_text", "turn_continuation", "draft"} <= manifested
    assert "meeting_assignment_records" not in state
    assert "meeting_people" not in state
    assert auditor._meeting_assignment_authority(state)["bindings"]


def test_meeting_assignment_bindings_are_stable_typed_and_order_independent():
    items, records, people = _three_outcome_fixture()

    bindings = meeting_assignment_bindings(items, records, people)

    assert [(row["item_id"], row["owner_id"], row["due"]) for row in bindings] == [
        ("work-item:consumer", "acct.consumer", "2031-04-12"),
        ("work-item:privacy", "", "2031-04-13"),
        ("work-item:producer", "acct.producer", "2031-04-11"),
    ]
    assert [row["source_evidence"]["record_id"] for row in bindings] == [
        "decision-2", "decision-3", "decision-1",
    ]
    assert all(set(row) == {"item_id", "owner_id", "due", "source_evidence"}
               for row in bindings)


def test_authored_due_never_selects_assignment_source_record(monkeypatch):
    items, records, people = _three_outcome_fixture()
    items[0]["duedate"] = "2031-04-11"  # another outcome's authoritative deadline

    bindings = meeting_assignment_bindings(items, records, people)
    consumer = next(row for row in bindings if row["item_id"] == "work-item:consumer")
    errors = auditor._meeting_assignment_errors(
        _runtime_state(monkeypatch, items, records, people),
    )

    assert consumer["owner_id"] == "acct.consumer"
    assert consumer["due"] == "2031-04-12"
    assert any(row["index"] == 0 and row["field"] == "duedate"
               and row["expected"] == "2031-04-12"
               and row["actual"] == "2031-04-11" for row in errors)


def test_explicit_outcome_reference_precedes_misleading_work_terms():
    items, records, people = _three_outcome_fixture()
    refs = {
        "work-item:consumer": "outcome:consumer",
        "work-item:privacy": "outcome:privacy",
        "work-item:producer": "outcome:producer",
    }
    for item in items:
        item["outcome_refs"] = [refs[item["item_id"]]]
    for record, outcome in zip(
            records, ("outcome:producer", "outcome:consumer", "outcome:privacy")):
        record["outcome_id"] = outcome
    records[0]["work"], records[1]["work"] = records[1]["work"], records[0]["work"]

    bindings = meeting_assignment_bindings(items, records, people)

    assert {row["item_id"]: row["owner_id"] for row in bindings} == {
        "work-item:consumer": "acct.consumer",
        "work-item:privacy": "",
        "work-item:producer": "acct.producer",
    }


def test_same_summary_assignments_bind_by_stable_source_ref_across_permutations():
    """Mutable titles and collection order can never select another sibling's scalars."""
    items, records, people = _three_outcome_fixture()
    source_refs = {
        "work-item:consumer": "meeting-source:consumer",
        "work-item:privacy": "meeting-source:privacy",
        "work-item:producer": "meeting-source:producer",
    }
    record_refs = (
        "meeting-source:producer",
        "meeting-source:consumer",
        "meeting-source:privacy",
    )
    for item in items:
        item["summary"] = "Shared release follow-up"
        item["meeting_assignment_ref"] = source_refs[item["item_id"]]
    for record, source_ref in zip(records, record_refs):
        record["work"] = "Shared release follow-up"
        record["source_evidence"] = {
            "kind": "meeting_minutes", "record_id": source_ref,
        }

    expected = {
        "work-item:consumer": ("acct.consumer", "2031-04-12"),
        "work-item:privacy": ("", "2031-04-13"),
        "work-item:producer": ("acct.producer", "2031-04-11"),
    }
    for item_order in permutations(items):
        for record_order in permutations(records):
            bindings = meeting_assignment_bindings(item_order, record_order, people)
            assert {
                row["item_id"]: (row["owner_id"], row["due"])
                for row in bindings
            } == expected


def test_same_summary_without_explicit_identity_is_fail_closed(monkeypatch):
    items, records, people = _three_outcome_fixture()
    for item in items:
        item["summary"] = "Shared release follow-up"
        item.pop("meeting_assignment_ref")
        item.pop("outcome_refs")
    for record in records:
        record["work"] = "Shared release follow-up"
        record.pop("outcome_ref")

    bindings = meeting_assignment_bindings(items, records, people)
    errors = auditor._meeting_assignment_errors(
        _runtime_state(monkeypatch, items, records, people),
    )

    assert bindings == []
    assert {row["index"] for row in errors if row["index"] >= 0} == {0, 1, 2}
    assert all(row["source"] == "meeting_assignment" for row in errors)


def test_same_summary_model_cannot_swap_a_source_bound_sibling_owner(monkeypatch):
    items, records, people = _three_outcome_fixture()
    for row in [*items, *records]:
        row["summary" if "summary" in row else "work"] = "Shared release follow-up"
    false_swap = {
        "index": 0, "check": "request", "finding_kind": "field_mismatch",
        "field": "assignee", "expected": "acct.producer",
        "actual": "acct.consumer",
        "message": "두 번째 sibling을 첫 번째 source owner로 바꿔야 합니다.",
        "fix": "acct.producer로 변경하세요.",
    }

    blocking, advice = auditor._partition_model_problems(
        _runtime_state(monkeypatch, items, records, people), [false_swap],
    )

    assert blocking == [] and advice == []


def test_swapped_source_refs_conflict_with_explicit_upstream_outcome_identity():
    """When upstream carries the relation, the source wire rejects a unique-but-wrong ref."""
    items = [
        {"summary": "Shared follow-up", "outcome_refs": ["outcome:a"],
         "meeting_assignment_ref": "meeting-source:b"},
        {"summary": "Shared follow-up", "outcome_refs": ["outcome:b"],
         "meeting_assignment_ref": "meeting-source:a"},
    ]
    records = [
        {"work": "Shared follow-up", "owner": "acct.a", "due": "2031-04-11",
         "owner_decision": "assigned", "outcome_ref": "outcome:a",
         "source_evidence": {"record_id": "meeting-source:a"}},
        {"work": "Shared follow-up", "owner": "acct.b", "due": "2031-04-12",
         "owner_decision": "assigned", "outcome_ref": "outcome:b",
         "source_evidence": {"record_id": "meeting-source:b"}},
    ]

    mapping, issues = meeting_assignment_source_mapping(items, records)

    assert mapping == {}
    assert {row["index"] for row in issues if row["index"] >= 0} == {0, 1}
    assert all("outcome identity" in row["message"] for row in issues if row["index"] >= 0)


def test_valid_source_ref_permutation_without_upstream_relation_is_untrusted():
    """Opaque ids prove record existence, not which indistinguishable sibling selected it."""
    items = [
        {"summary": "Shared follow-up", "outcome_refs": ["outcome:a"],
         "meeting_assignment_ref": "meeting-source:b"},
        {"summary": "Shared follow-up", "outcome_refs": ["outcome:b"],
         "meeting_assignment_ref": "meeting-source:a"},
    ]
    records = [
        {"work": "Shared follow-up", "owner": "acct.a", "due": "2031-04-11",
         "owner_decision": "assigned",
         "source_evidence": {"record_id": "meeting-source:a"}},
        {"work": "Shared follow-up", "owner": "acct.b", "due": "2031-04-12",
         "owner_decision": "assigned",
         "source_evidence": {"record_id": "meeting-source:b"}},
    ]

    mapping, issues = meeting_assignment_source_mapping(items, records)

    assert mapping == {}
    assert {row["index"] for row in issues if row["index"] >= 0} == {0, 1}
    assert all("typed outcome/item identity" in row["message"]
               for row in issues if row["index"] >= 0)


def test_overlapping_outcome_sets_cannot_authorize_swapped_source_refs():
    """Set overlap is not sibling identity; only the exact non-empty set may bind."""
    items = [
        {"outcome_refs": ["outcome:a"], "meeting_assignment_ref": "meeting-source:b"},
        {"outcome_refs": ["outcome:b"], "meeting_assignment_ref": "meeting-source:a"},
    ]
    records = [
        {"outcome_refs": ["outcome:a", "outcome:b"],
         "source_evidence": {"record_id": "meeting-source:a"}},
        {"outcome_refs": ["outcome:a", "outcome:b"],
         "source_evidence": {"record_id": "meeting-source:b"}},
    ]

    mapping, issues = meeting_assignment_source_mapping(items, records)

    assert mapping == {}
    assert {row["index"] for row in issues if row["index"] >= 0} == {0, 1}


def test_single_root_single_record_remains_legacy_unambiguous():
    record = {"owner": "acct.a", "owner_decision": "assigned",
              "source_evidence": {"record_id": "meeting-source:a"}}

    assert meeting_assignment_source_mapping([{}], [record]) == ({0: 0}, [])
    assert meeting_assignment_source_mapping(
        [{"meeting_assignment_ref": "meeting-source:a"}], [record],
    ) == ({0: 0}, [])
    mapping, issues = meeting_assignment_source_mapping(
        [{"meeting_assignment_ref": "meeting-source:unknown"}], [record],
    )
    assert mapping == {} and issues


def test_empty_source_due_is_authoritative_absence_for_auditor(monkeypatch):
    item = {
        "item_id": "work-item:a", "summary": "Shared follow-up",
        "outcome_refs": ["outcome:a"], "meeting_assignment_ref": "meeting-source:a",
        "assignee": "acct.a", "assignee_source": "user", "duedate": "2099-01-01",
    }
    record = {
        "work": "Shared follow-up", "owner": "acct.a", "due": "",
        "owner_decision": "assigned", "outcome_ref": "outcome:a",
        "source_evidence": {"record_id": "meeting-source:a"},
    }

    errors = auditor._meeting_assignment_errors(
        _runtime_state(monkeypatch, [item], [record], {"Owner A": "acct.a"}),
    )

    assert any(row["field"] == "duedate" and row["expected"] == "empty"
               and row["actual"] == "2099-01-01" for row in errors)


def test_no_source_sentinel_rejects_untyped_meeting_scalars_at_auditor(monkeypatch):
    items = [
        {"item_id": "work-item:a", "summary": "Shared follow-up",
         "outcome_refs": ["outcome:a"], "meeting_assignment_ref": "meeting-source:a",
         "assignee": "acct.a", "assignee_source": "user", "duedate": "2031-04-11"},
        {"item_id": "work-item:b", "summary": "Shared follow-up",
         "outcome_refs": ["outcome:b"], "meeting_assignment_ref": "meeting-source:none",
         "assignee": "acct.invented", "assignee_source": "user",
         "duedate": "2099-01-01"},
    ]
    record = {
        "work": "Shared follow-up", "owner": "acct.a", "due": "2031-04-11",
        "owner_decision": "assigned", "outcome_ref": "outcome:a",
        "source_evidence": {"record_id": "meeting-source:a"},
    }

    errors = auditor._meeting_assignment_errors(
        _runtime_state(monkeypatch, items, [record], {"Owner A": "acct.a"}),
    )

    assert {(row["index"], row["field"]) for row in errors} >= {
        (1, "assignee"), (1, "duedate"),
    }


def test_no_source_sentinel_accepts_exact_separate_typed_scalars(monkeypatch):
    request = "회의 기록에서 Task 2건을 만들고 둘 다 마감은 2031-04-12"
    items = [
        {"item_id": "work-item:a", "outcome_refs": ["outcome:a"],
         "meeting_assignment_ref": "meeting-source:a", "assignee": "acct.a",
         "assignee_source": "user", "duedate": "2031-04-11"},
        {"item_id": "work-item:b", "outcome_refs": ["outcome:b"],
         "meeting_assignment_ref": "meeting-source:none", "assignee": "skcc.b1234",
         "assignee_source": "user", "duedate": "2031-04-12"},
    ]
    record = {
        "owner": "acct.a", "due": "2031-04-11", "owner_decision": "assigned",
        "outcome_ref": "outcome:a",
        "source_evidence": {"record_id": "meeting-source:a"},
    }
    state = _runtime_state(monkeypatch, items, [record], {"Owner A": "acct.a"})
    state["request_text"] = request
    state["continuation_contract"] = {
        "version": "continuation.v1", "action": "create", "root_request": request,
        "decisions": [{"field": "assignee", "value": "skcc.b1234"}],
    }

    assert not [row for row in auditor._meeting_assignment_errors(state)
                if row.get("index") == 1 and row.get("field") in {"assignee", "duedate"}]


def test_grounding_contract_bounds_people_to_assignment_authority(monkeypatch):
    items, records, people = _three_outcome_fixture()

    contract = auditor._audit_grounding_contract(
        _runtime_state(monkeypatch, items, records, people),
    )

    assert contract["meeting_assignment_bindings"]
    assert contract["bounded_identity_map"] == {
        "Consumer Owner": "acct.consumer",
        "Producer Owner": "acct.producer",
    }
    assert "Unrelated Observer" not in str(contract)


def test_canonical_display_name_id_false_finding_is_deterministically_rejected(
        monkeypatch):
    items, records, people = _three_outcome_fixture()
    state = _runtime_state(monkeypatch, items, records, people)
    false_finding = {
        "index": 0,
        "check": "request",
        "finding_kind": "field_mismatch",
        "field": "assignee",
        "expected": "Consumer Owner",
        "actual": "acct.consumer",
        "message": (
            "Acme consumer 담당자가 Consumer Owner가 아닌 acct.consumer로 "
            "잘못 매핑되어 있습니다."
        ),
        "fix": "담당자를 Consumer Owner로 변경하세요.",
    }

    blocking, advice = auditor._partition_model_problems(state, [false_finding])

    assert blocking == [] and advice == []


def test_rejected_identity_finding_does_not_return_as_synthetic_axis_failure(monkeypatch):
    items, records, people = _three_outcome_fixture()
    state = _runtime_state(monkeypatch, items, records, people)
    monkeypatch.setattr(auditor, "_machine_check", lambda _state: {
        "ok": True, "errors": [], "warnings": [], "text": "ok",
    })
    reviewed = auditor.Auditor().apply(state, {
        "grounded": True,
        "rule_compliant": True,
        "answers_request": False,
        "problems": [{
            "index": 0,
            "check": "request",
            "finding_kind": "field_mismatch",
            "field": "assignee",
            "expected": "Consumer Owner",
            "actual": "acct.consumer",
            "message": (
                "Acme consumer 담당자가 Consumer Owner가 아닌 acct.consumer로 "
                "잘못 매핑되어 있습니다."
            ),
            "fix": "담당자를 Consumer Owner로 변경하세요.",
        }],
    })["review"]

    assert reviewed["ok"] is True
    assert reviewed["checks"]["answers_request"] is True
    assert reviewed["problems"] == []


def test_model_stated_actual_cannot_override_matching_current_assignment(monkeypatch):
    items, records, people = _three_outcome_fixture()
    state = _runtime_state(monkeypatch, items, records, people)
    stale_finding = {
        "index": 0,
        "check": "request",
        "finding_kind": "field_mismatch",
        "field": "assignee",
        "expected": "Consumer Owner",
        "actual": "acct.producer",
        "message": "현재 담당자가 acct.producer라서 Consumer Owner와 다릅니다.",
        "fix": "담당자를 Consumer Owner로 변경하세요.",
    }

    blocking, advice = auditor._partition_model_problems(state, [stale_finding])

    assert blocking == [] and advice == []


def test_model_stated_expected_cannot_override_matching_source_assignment(monkeypatch):
    items, records, people = _three_outcome_fixture()
    state = _runtime_state(monkeypatch, items, records, people)
    source_contradicting_finding = {
        "index": 0,
        "check": "request",
        "finding_kind": "field_mismatch",
        "field": "assignee",
        "expected": "acct.producer",
        "actual": "acct.consumer",
        "message": "현재 담당자를 다른 outcome의 담당자로 바꿔야 합니다.",
        "fix": "acct.producer로 변경하세요.",
    }

    blocking, advice = auditor._partition_model_problems(
        state, [source_contradicting_finding],
    )

    assert blocking == [] and advice == []
    assert auditor._meeting_assignment_errors(state) == []


def test_wrong_canonical_meeting_owner_is_machine_error_and_not_suppressed(
        monkeypatch):
    items, records, people = _three_outcome_fixture()
    items[0]["assignee"] = "acct.producer"
    state = _runtime_state(monkeypatch, items, records, people)

    errors = auditor._meeting_assignment_errors(state)
    finding = {
        "index": 0,
        "check": "request",
        "finding_kind": "field_mismatch",
        "field": "assignee",
        "expected": "Consumer Owner",
        "actual": "acct.producer",
        "message": "Consumer Owner 작업이 acct.producer에 잘못 배정되었습니다.",
        "fix": "acct.consumer로 복원하세요.",
    }
    blocking, _advice = auditor._partition_model_problems(state, [finding])

    assert any(row["field"] == "assignee"
               and row["expected"] == "acct.consumer"
               and row["actual"] == "acct.producer" for row in errors)
    assert blocking == [finding]


def test_identity_prose_without_typed_field_relation_is_not_suppressed():
    items, records, people = _three_outcome_fixture()
    state = _state(items)
    untyped = {
        "index": 0,
        "check": "request",
        "message": "Consumer Owner가 아닌 acct.consumer로 잘못 매핑되어 있습니다.",
        "fix": "담당자를 바꾸세요.",
    }

    blocking, advice = auditor._partition_model_problems(state, [untyped])

    assert blocking == [untyped]
    assert advice == []


def test_per_finding_signatures_detect_repair_subset_recurrence():
    items, records, people = _three_outcome_fixture()
    state = _state(items)
    first = auditor._typed_review_contract(state, {
        "ok": False,
        "errors": [
            {
                "index": 0, "field": "assignee", "source": "meeting_assignment",
                "expected": "acct.consumer", "actual": "acct.producer",
                "message": "consumer owner mismatch",
            },
            {
                "index": 2, "field": "duedate", "source": "final_authority",
                "expected": "2031-04-11", "actual": "2031-04-14",
                "message": "producer due mismatch",
            },
        ],
        "problems": [],
    })
    assert all(row["finding_signature"].startswith("finding:")
               and row["authority"] for row in first["findings"])

    state["draft"]["repair_attempt"] = {
        "defect_signature": first["defect_signature"],
        "payload_digest": first["payload_digest"],
    }
    subset = auditor._typed_review_contract(state, {
        "ok": False,
        "errors": [{
            "index": 2, "field": "duedate", "source": "final_authority",
            "expected": "2031-04-11", "actual": "2031-04-14",
            "message": "producer due mismatch",
        }],
        "problems": [],
    })

    assert subset["repeated_defect"] is True
    assert subset["findings"][0]["repeated"] is True
    assert subset["findings"][0]["finding_signature"] == first["findings"][1][
        "finding_signature"
    ]
