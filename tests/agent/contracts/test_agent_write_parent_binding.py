from __future__ import annotations


def test_partial_parent_success_never_reindexes_children_to_another_parent():
    from app.agent.tools.write_tools import _bind_children_to_created_parents

    children = [
        {"parent_index": 0, "summary": "child of failed parent"},
        {"parent_index": 1, "summary": "child of successful parent"},
    ]
    created = [{"index": 1, "key": "ACME-202", "summary": "second parent"}]

    rows, failed = _bind_children_to_created_parents(
        children, created, parent_count=2,
    )

    assert rows == [{
        "summary": "child of successful parent",
        "type": "Sub-Task",
        "parent": "ACME-202",
    }]
    assert failed == [{
        "index": 0,
        "summary": "child of failed parent",
        "error": "상위 항목 index 0가 생성되지 않아 Sub-Task를 만들지 않았습니다.",
    }]


def test_duplicate_or_invalid_parent_receipts_fail_closed():
    from app.agent.tools.write_tools import _bind_children_to_created_parents

    children = [
        {"parent_index": 0, "summary": "ambiguous child"},
        {"parent_index": 3, "summary": "invalid child"},
    ]
    created = [
        {"index": 0, "key": "ACME-210"},
        {"index": 0, "key": "ACME-211"},
    ]

    rows, failed = _bind_children_to_created_parents(
        children, created, parent_count=2,
    )

    assert rows == []
    assert [row["index"] for row in failed] == [0, 1]
    assert all("만들지 않았습니다" in row["error"] for row in failed)


def test_create_tickets_reports_child_of_failed_parent_and_creates_only_bound_child(
    monkeypatch,
):
    from app.agent.tools import write_tools
    from app.domain import bulk

    calls = []

    class Client:
        desc_field_value = staticmethod(lambda value: value)

        @staticmethod
        def bulk_lookup():
            return object()

        @staticmethod
        def bulk_create(mode, rows, desc_to_field=None):
            calls.append((mode, rows))
            if mode == "task":
                return {
                    "ok": False,
                    "created": [{"index": 1, "key": "ACME-302", "summary": "second"}],
                    "failed": [{"index": 0, "summary": "first", "error": "denied"}],
                }
            return {
                "ok": True,
                "created": [{"index": 0, "key": "ACME-303", "summary": rows[0]["summary"]}],
                "failed": [],
            }

    monkeypatch.setattr(write_tools, "client", lambda: Client())
    monkeypatch.setattr(write_tools.approval, "consume", lambda *args: (True, ""))
    monkeypatch.setattr(bulk, "validate_bulk", lambda *args: {"ok": True, "errors": []})

    result = write_tools.create_tickets.invoke({
        "mode": "task",
        "items": [{"summary": "first"}, {"summary": "second"}],
        "children": [
            {"parent_index": 0, "summary": "orphan"},
            {"parent_index": 1, "summary": "bound"},
        ],
        "approval_token": "approved",
    })

    assert calls[1] == ("subtask", [{
        "summary": "bound", "type": "Sub-Task", "parent": "ACME-302",
    }])
    assert {row["summary"] for row in result["failed"]} == {"first", "orphan"}
    assert {row["key"] for row in result["created"]} == {"ACME-302", "ACME-303"}
    assert result["ok"] is False
