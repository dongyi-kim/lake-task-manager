# -*- coding: utf-8 -*-
"""Bulk 생성 — 검증 규칙(app/domain/bulk.py)과 Markdown 변환(app/content/mdhtml.py) 회귀.

검증 규칙은 순수 함수라 Jira 없이 돈다(실값 조회는 가짜 lookup 주입). 프론트에도 같은 규칙이
있는데(app/static/lib/bulkSchema.js), **여기서 막는 것을 프론트가 통과시키는 건 괜찮지만 그 반대는
사용자를 속이는 것**이다 — 규칙을 고칠 땐 두 곳을 같이 본다.
"""

from app.content.mdhtml import markdown_to_html
from app.domain.bulk import MAX_ITEMS, to_create_kwargs, validate_bulk


class FakeLookup:
    """실값 대조용 가짜 Jira — 존재하는 티켓/타입/선택지를 고정해 둔다."""

    BADGES = {
        "DL-100": {"key": "DL-100", "type": "Epic"},
        "DL-200": {"key": "DL-200", "type": "Task"},
        "DL-300": {"key": "DL-300", "type": "Sub-Task"},
    }

    def __init__(self, editable=True):
        self._editable = editable
        self.badge_calls = []

    def badge(self, key):
        self.badge_calls.append(key)
        return self.BADGES.get(key)

    def child_types(self, key):
        t = (self.BADGES.get(key) or {}).get("type")
        if t == "Epic":
            return ["Task", "Bug", "Story"]
        if t == "Sub-Task":
            return []
        return ["Sub-Task"]

    def task_types(self):
        return ["Task", "Bug"]

    def priorities(self):
        return ["P1-Critical", "P2-Major"]

    def components(self):
        return ["ETL", "DevOps"]

    def user_exists(self, uid):
        return uid in ("x12345", "test.ui01")

    def may_edit(self, key):
        return self._editable


def _fields(res):
    """오류를 (index, field) 집합으로 — 메시지 문구에 테스트가 묶이지 않게."""
    return {(e["index"], e["field"]) for e in res["errors"]}


# ── 형태(스키마) 검증 ────────────────────────────────────────────────────
def test_mode_and_items_shape():
    assert not validate_bulk("bogus", [{}])["ok"]
    assert not validate_bulk("task", "not-a-list")["ok"]
    assert not validate_bulk("task", [])["ok"]                      # 빈 목록
    over = [{"summary": "x", "type": "Task", "epic": None}] * (MAX_ITEMS + 1)
    assert not validate_bulk("task", over)["ok"]                    # 상한 초과


def test_required_summary_and_type():
    r = validate_bulk("task", [{"epic": None}])
    assert not r["ok"]
    assert (0, "summary") in _fields(r) and (0, "type") in _fields(r)


def test_epic_key_must_be_present_but_null_is_ok():
    """'빠뜨린 것'과 '의도적으로 Epic 없음'을 구분한다 — 핵심 규칙."""
    missing = validate_bulk("task", [{"summary": "A", "type": "Task"}])
    assert (0, "epic") in _fields(missing)

    explicit = validate_bulk("task", [{"summary": "A", "type": "Task", "epic": None}])
    assert explicit["ok"]


def test_subtask_requires_existing_parent_key():
    no_parent = validate_bulk("subtask", [{"summary": "A", "type": "Sub-Task"}])
    assert (0, "parent") in _fields(no_parent)

    null_parent = validate_bulk("subtask", [{"summary": "A", "type": "Sub-Task", "parent": None}])
    assert (0, "parent") in _fields(null_parent)     # null 불가

    bad_key = validate_bulk("subtask", [{"summary": "A", "type": "Sub-Task", "parent": "그냥글자"}])
    assert (0, "parent") in _fields(bad_key)


def test_field_formats():
    r = validate_bulk("task", [{
        "summary": "A", "type": "Task", "epic": None,
        "duedate": "2026/01/01", "priority": 3, "components": "ETL", "labels": [1, 2],
    }])
    f = _fields(r)
    assert (0, "duedate") in f and (0, "priority") in f
    assert (0, "components") in f and (0, "labels") in f


def test_unknown_field_is_warning_not_error():
    r = validate_bulk("task", [{"summary": "A", "type": "Task", "epic": None, "storyPoints": 3}])
    assert r["ok"]                                          # 생성은 막지 않는다
    assert any(w["field"] == "storyPoints" for w in r["warnings"])


def test_description_attachment_and_nonweb_link_warn():
    r = validate_bulk("task", [{
        "summary": "A", "type": "Task", "epic": None,
        "description": "![그림](C:/tmp/a.png)\n\n[문서](file:///tmp/x.docx)",
    }])
    assert r["ok"]                                          # 경고일 뿐 막지 않는다
    assert len([w for w in r["warnings"] if w["field"] == "description"]) >= 2


# ── 실값 대조 ────────────────────────────────────────────────────────────
def test_parent_must_exist_and_have_right_type():
    lk = FakeLookup()
    gone = validate_bulk("subtask", [{"summary": "A", "type": "Sub-Task", "parent": "DL-999"}], lk)
    assert (0, "parent") in _fields(gone)                    # 없는 티켓

    epic_parent = validate_bulk("subtask", [{"summary": "A", "type": "Sub-Task", "parent": "DL-100"}], lk)
    assert (0, "parent") in _fields(epic_parent)             # Epic 은 Sub-Task 의 부모가 될 수 없다

    task_as_epic = validate_bulk("task", [{"summary": "A", "type": "Task", "epic": "DL-200"}], lk)
    assert (0, "epic") in _fields(task_as_epic)              # Task 를 Epic 자리에 넣음

    ok = validate_bulk("subtask", [{"summary": "A", "type": "Sub-Task", "parent": "DL-200"}], lk)
    assert ok["ok"]


def test_type_must_be_creatable_under_parent():
    lk = FakeLookup()
    r = validate_bulk("task", [{"summary": "A", "type": "Epic", "epic": "DL-100"}], lk)
    assert (0, "type") in _fields(r)

    standalone = validate_bulk("task", [{"summary": "A", "type": "Sub-Task", "epic": None}], lk)
    assert (0, "type") in _fields(standalone)                # Epic 없이 Sub-Task 는 못 만든다


def test_priority_and_assignee_checked_against_real_values():
    lk = FakeLookup()
    r = validate_bulk("task", [{
        "summary": "A", "type": "Task", "epic": None,
        "priority": "P9-Nope", "assignee": "홍길동",
    }], lk)
    f = _fields(r)
    assert (0, "priority") in f and (0, "assignee") in f


def test_unknown_component_warns_but_does_not_block():
    """컴포넌트는 목록에 없어도 막지 않는다 — 오타만 경고하고 실제 거절은 Jira 가 말한다."""
    lk = FakeLookup()
    r = validate_bulk("task", [{
        "summary": "A", "type": "Task", "epic": None, "components": ["ETL", "없는모듈"],
    }], lk)
    assert r["ok"]
    assert any(w["field"] == "components" for w in r["warnings"])


def test_permission_is_checked_on_parent():
    r = validate_bulk("subtask", [{"summary": "A", "type": "Sub-Task", "parent": "DL-200"}],
                      FakeLookup(editable=False))
    assert (0, "parent") in _fields(r)


def test_lookup_is_memoized_per_parent():
    """같은 부모가 여러 번 나와도 조회는 한 번 — 100건이 100왕복이 되면 안 된다."""
    lk = FakeLookup()
    items = [{"summary": f"S{i}", "type": "Sub-Task", "parent": "DL-200"} for i in range(10)]
    assert validate_bulk("subtask", items, lk)["ok"]
    assert lk.badge_calls.count("DL-200") == 1


def test_messages_have_no_markdown_markers():
    """오류/경고 문구는 화면이 **그대로 글자로** 그린다 — 마크다운을 쓰면 별표가 그대로 보인다.

    실제로 assignee 오류에 `**이메일 @ 앞부분**` 이 남아 화면에 별표가 노출됐다. 문구를 새로
    쓸 때마다 눈으로 잡지 말고 여기서 잡는다."""
    lk = FakeLookup()
    res = [
        validate_bulk("task", [{}]),                                        # 필수 누락
        validate_bulk("task", [{"summary": "A", "type": "Epic", "epic": "DL-100",
                                "duedate": "어제", "priority": 3, "labels": [1],
                                "components": "ETL", "storyPoints": 3}], lk),
        validate_bulk("task", [{"summary": "A", "type": "Task", "epic": "DL-999",
                                "priority": "없음", "assignee": "홍길동",
                                "components": ["없는모듈"],
                                "description": "![그림](C:/a.png) [문서](file:///x)"}], lk),
        validate_bulk("subtask", [{"summary": "A", "type": "Sub-Task", "parent": None}], lk),
        validate_bulk("subtask", [{"summary": "A", "type": "Sub-Task", "parent": "DL-200"}],
                      FakeLookup(editable=False)),
        validate_bulk("bogus", [{}]),
    ]
    msgs = [m["message"] for r in res for m in r["errors"] + r["warnings"]]
    assert len(msgs) > 12                                    # 문구를 실제로 훑었는지 자체 확인
    for m in msgs:
        for mark in ("**", "__", "`"):
            assert mark not in m, f"문구에 마크다운({mark}) 이 남았다: {m!r}"


# ── 생성 인자 변환 ───────────────────────────────────────────────────────
def test_to_create_kwargs_maps_parent_by_mode():
    task = to_create_kwargs("task", {"summary": " A ", "type": "Task", "epic": "DL-100",
                                     "components": [], "labels": ["x"]})
    assert task["parent_key"] == "DL-100" and task["summary"] == "A"
    assert task["components"] is None and task["labels"] == ["x"]     # 빈 배열은 None

    sub = to_create_kwargs("subtask", {"summary": "B", "type": "Sub-Task", "parent": "DL-200"})
    assert sub["parent_key"] == "DL-200"

    none_epic = to_create_kwargs("task", {"summary": "C", "type": "Task", "epic": None})
    assert none_epic["parent_key"] is None


# ── Markdown → 에디터 HTML ───────────────────────────────────────────────
def test_markdown_checkbox_shape_matches_editor():
    """체크박스는 TipTap taskList 형태여야 wiki/prod 양쪽이 체크박스로 저장한다."""
    h = markdown_to_html("- [ ] 안 함\n- [x] 함")
    assert 'data-type="taskList"' in h
    assert 'data-checked="false"' in h and 'data-checked="true"' in h


def test_markdown_table_and_lists():
    h = markdown_to_html("| A | B |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in h and "<th>A</th>" in h and "<td>1</td>" in h
    assert "<ul><li>x</li></ul>" in markdown_to_html("- x")
    assert "<ol><li>x</li></ol>" in markdown_to_html("1. x")


def test_markdown_escapes_html_and_keeps_only_web_links():
    assert "&lt;script&gt;" in markdown_to_html("<script>alert(1)</script>")

    web = markdown_to_html("[문서](https://example.com/a)")
    assert '<a href="https://example.com/a">문서</a>' in web

    local = markdown_to_html("[문서](file:///tmp/x.docx)")
    assert "<a " not in local and "문서" in local          # 링크로 만들지 않고 글자로 남긴다

    img = markdown_to_html("![그림](C:/tmp/a.png)")
    assert "<img" not in img and "그림" in img              # 첨부는 만들 수 없다


def test_markdown_paragraph_keeps_line_breaks():
    """평문을 그냥 넣으면 prod(HTML 저장)에서 줄바꿈이 뭉친다 — <br> 로 살려야 한다."""
    assert markdown_to_html("첫 줄\n둘째 줄") == "<p>첫 줄<br>둘째 줄</p>"
    assert markdown_to_html("") == "" and markdown_to_html(None) == ""
