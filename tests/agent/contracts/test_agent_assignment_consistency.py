"""People assignment ordering and merged-rationale authority regressions."""

from app.agent.workflow.agents.people_advisor import (
    PeopleAdvisor,
    _normalize_resolved_assignment_rationale,
    merge_assignments,
)


ROSTER = (
    "[ETL 로스터·부하]\n"
    "- skcc.x1103 A — 진행중 8건 · 열림 11건 · 최근 완료 4건\n"
    "- skcc.i2011 B — 진행중 12건 · 열림 9건 · 최근 완료 5건\n"
    "- skcc.x1042 C — 진행중 18건 · 열림 17건 · 최근 완료 2건"
)


def _state(item=None, **extra):
    state = {
        "trace": [],
        "draft": {"items": [item or {
            "summary": "[ETL] 통계 파이프라인 구현", "components": ["ETL"],
        }]},
        "similar_history": "",
        "roster_load": ROSTER,
    }
    state.update(extra)
    return state


def test_workload_only_root_uses_complete_roster_after_stale_counts_are_rebound():
    """A model-provided alternate list is not the authority for a lowest-load claim."""
    model = {"assignments": [{
        "index": 0,
        "user": "skcc.i2011",
        # Both counts are attached to the wrong IDs, reproducing the inversion boundary.
        "reasons": ["검증된 관련 이력 근거 없음 · 진행중 8건으로 부하가 가장 낮음"],
        "children": [],
        "alternates": [{"user": "skcc.x1103", "why": "진행중 12건으로 부하가 높음"}],
    }]}

    row = PeopleAdvisor().apply(_state(), model)["assignments"][0]

    assert row["user"] == "skcc.x1103"
    assert "진행중 8건" in row["reasons"][0]
    assert "후보 중 현재 부하가 가장 낮아" in row["reasons"][0]
    assert "진행중 12건" in next(
        alt["why"] for alt in row["alternates"] if alt["user"] == "skcc.i2011")


def test_verified_direct_history_keeps_semantic_precedence_over_lower_workload():
    state = _state(similar_history=(
        '- skcc.i2011 — 유사 2건: DL-7001 "Puffin 조사"(Done) · '
        'DL-7002 "NDV 구현"(Done)'
    ))
    model = {"assignments": [{
        "index": 0, "user": "skcc.i2011",
        "reasons": ["유사 티켓 DL-7001 담당"], "children": [],
        "alternates": [{"user": "skcc.x1103", "why": "진행중 8건"}],
    }]}

    row = PeopleAdvisor().apply(state, model)["assignments"][0]

    assert row["user"] == "skcc.i2011"
    assert row["reasons"] == ["유사 티켓 DL-7001 담당"]
    assert row["alternates"][0]["user"] == "skcc.x1103"
    assert "진행중 8건" in row["alternates"][0]["why"]


def test_user_fixed_assignee_is_never_replaced_by_roster_minimum():
    item = {
        "summary": "[ETL] 통계 파이프라인 구현", "components": ["ETL"],
        "assignee": "skcc.i2011", "assignee_source": "user",
    }
    model = {"assignments": [{
        "index": 0, "user": "skcc.x1103", "reasons": ["진행중 8건"],
        "children": [], "alternates": [],
    }]}

    row = PeopleAdvisor().apply(_state(item), model)["assignments"][0]

    assert row["user"] == "skcc.i2011"
    assert row["reasons"] == ["사용자 지정 담당자"]


def test_explicitly_unassigned_child_stays_unassigned_in_display_and_payload():
    item = {
        "summary": "[ETL] 통계 파이프라인 구현", "components": ["ETL"],
        "children": [
            {"summary": "담당자를 비워 둘 검증", "assignee": "",
             "assignee_source": "user_unassigned"},
            {"summary": "구현"},
        ],
    }
    model = {"assignments": [{
        "index": 0, "user": "skcc.x1103", "reasons": ["진행중 8건"],
        "children": [
            {"index": 0, "user": "skcc.i2011", "why": "진행중 12건"},
            {"index": 1, "user": "skcc.x1103", "why": "진행중 8건"},
        ],
        "alternates": [],
    }]}

    row = PeopleAdvisor().apply(_state(item), model)["assignments"][0]
    by_child = {child["index"]: child for child in row["children"]}

    assert by_child[0] == {"index": 0, "user": "", "why": "사용자 지정 미할당"}
    assert by_child[1]["user"] == "skcc.x1103"
    merged = merge_assignments({"rationale": "첫 자식 담당자는 미할당.", "items": [item]}, [row])
    assert not merged["items"][0]["children"][0].get("assignee")
    assert merged["items"][0]["children"][1]["assignee"] == "skcc.x1103"
    assert "미할당" in merged["rationale"]


def test_user_assignment_authority_survives_when_roster_lookup_is_unavailable():
    item = {
        "summary": "외부 모듈 작업", "components": ["UNKNOWN"],
        "children": [{
            "summary": "의도적 미할당", "assignee": "",
            "assignee_source": "user_unassigned",
        }],
    }
    model = {"assignments": [{
        "index": 0, "user": "skcc.x9999", "reasons": ["모델 추천"],
        "children": [{"index": 0, "user": "skcc.x9999", "why": "모델 추천"}],
        "alternates": [],
    }]}

    row = PeopleAdvisor().apply(_state(item, roster_load=""), model)["assignments"][0]

    assert row["children"] == [
        {"index": 0, "user": "", "why": "사용자 지정 미할당"},
    ]


def test_assignment_merge_removes_only_resolved_unassigned_rationale():
    draft = {
        "rationale": (
            "마감일은 사용자 지정값을 유지함. 담당자는 미정 상태로 둠.\n"
            "단계별 Sub-Task로 분할함."
        ),
        "items": [{
            "summary": "root",
            "children": [{"summary": "design"}, {"summary": "verify"}],
        }],
    }
    assignments = [{
        "index": 0, "user": "skcc.x1103", "reasons": ["진행중 8건"],
        "children": [
            {"index": 0, "user": "skcc.x1103", "why": "진행중 8건"},
            {"index": 1, "user": "skcc.i2011", "why": "진행중 12건"},
        ],
    }]

    merged = merge_assignments(draft, assignments)

    assert "미정" not in merged["rationale"] and "미할당" not in merged["rationale"]
    assert "마감일은 사용자 지정값을 유지함" in merged["rationale"]
    assert "단계별 Sub-Task로 분할함" in merged["rationale"]


def test_unresolved_payload_keeps_unassigned_rationale_truthful():
    draft = {
        "rationale": "담당자는 미정 상태로 둠.",
        "items": [{"summary": "root", "assignee": "skcc.x1103",
                   "children": [{"summary": "still open"}]}],
    }

    normalized = _normalize_resolved_assignment_rationale(draft)

    assert normalized["rationale"] == "담당자는 미정 상태로 둠."


def test_graph_join_exposes_clean_rationale_with_authoritative_assignments():
    from app.agent.workflow.graph import _merge_assignments

    state = {
        "request_text": "ETL 통계 파이프라인 Task를 만들어줘",
        "messages": [],
        "draft": {
            "rationale": "범위는 1차 구현. 담당자는 미정 상태로 둠.",
            "items": [{"summary": "[ETL] 통계 파이프라인 1차 구현"}],
        },
        "assignments": [{
            "index": 0, "user": "skcc.x1103", "reasons": ["진행중 8건"],
            "children": [], "alternates": [],
        }],
    }

    merged = _merge_assignments(state)

    assert merged["draft"]["items"][0]["assignee"] == "skcc.x1103"
    assert "미정" not in merged["draft"]["rationale"]
    assert merged["assignments"][0]["user"] == "skcc.x1103"
