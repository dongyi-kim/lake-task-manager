"""progress.epic_progress 순수 로직 검증 (Jira 불필요)."""
import os
import sys

from app.domain import progress   # noqa: E402


def test_done_total_and_mock():
    issues = [
        {"type": "Story", "sp": 5, "statusCategory": "done", "labels": []},
        {"type": "Story", "sp": 5, "statusCategory": "todo", "labels": []},
        {"type": "Story", "sp": 8, "statusCategory": "todo", "labels": ["mock"]},
        {"type": "Bug", "sp": 0, "statusCategory": "done", "labels": []},   # 제외
        {"type": "Story", "sp": 0, "statusCategory": "todo", "labels": []},  # SP=0 무해
    ]
    r = progress.epic_progress(issues)
    assert r["totalSp"] == 18          # 5+5+8+0 (Bug 제외)
    assert r["doneSp"] == 5
    assert r["mockSp"] == 8
    assert r["excludedIssues"] == 1
    assert r["progressPct"] == round(5 / 18 * 100, 1)


def test_empty_is_zero():
    r = progress.epic_progress([])
    assert r["progressPct"] == 0.0
    assert r["totalSp"] == 0


def test_bug_excluded_even_with_sp():
    # 이중 안전장치: Bug 타입은 SP 가 있어도 제외
    issues = [{"type": "Bug", "sp": 5, "statusCategory": "done", "labels": []}]
    r = progress.epic_progress(issues)
    assert r["totalSp"] == 0
    assert r["excludedIssues"] == 1


def test_missing_sp_default():
    # 누락(None): Bug->0(어차피 제외), 나머지->1. 명시적 0 은 그대로 0.
    assert progress.sp_of({"type": "Story", "sp": None}) == 1
    assert progress.sp_of({"type": "Sub-task", "sp": None}) == 1
    assert progress.sp_of({"type": "Bug", "sp": None}) == 0
    assert progress.sp_of({"type": "Story", "sp": 0}) == 0
    assert progress.sp_of({"type": "Story", "sp": 5}) == 5


def test_missing_sp_in_epic_progress():
    issues = [
        {"type": "Story", "sp": None, "statusCategory": "done", "labels": []},   # ->1, done
        {"type": "Story", "sp": None, "statusCategory": "todo", "labels": []},   # ->1
        {"type": "Story", "sp": 3, "statusCategory": "done", "labels": []},
    ]
    r = progress.epic_progress(issues)
    assert r["totalSp"] == 5           # 1 + 1 + 3
    assert r["doneSp"] == 4            # 1 + 3


def test_count_by_type():
    issues = [{"type": "Story"}, {"type": "Story"}, {"type": "Sub-task"}]
    c = progress.count_by_type(issues)
    assert c == {"Story": 2, "Sub-task": 1}
