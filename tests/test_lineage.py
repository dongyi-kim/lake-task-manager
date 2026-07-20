"""계보 스파인 API — 조상(ticket_ancestors) / 형제(ticket_siblings).

핵심: 조상·형제 조회는 티켓단위 캐시(issue:{env}:{key}, epic_children)를 재사용하고
조립 결과도 개별 캐시된다. 좌측 스파인 패널이 이 두 API 로 그려진다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cache import Cache            # noqa: E402
from app.jira_client import JiraClient  # noqa: E402
from app.settings import get_settings   # noqa: E402
from app.world import get_world         # noqa: E402


def _client():
    return JiraClient(get_settings(), Cache(":memory:"))


def _subtask_chain():
    """parent 가 있고 그 parent 가 Epic Link 를 가진 Sub-Task → (sub, parent, epic)."""
    w = get_world()
    for k, it in w.issues.items():
        p = it.get("parentKey")
        if p and w.issues.get(p, {}).get("epicKey"):
            return k, p, w.issues[p]["epicKey"]
    raise AssertionError("no subtask→parent→epic chain in world")


def _key_of_type(t):
    for k, it in get_world().issues.items():
        if it["type"] == t:
            return k
    raise AssertionError("no issue of type " + t)


# ── 조상 ──
def test_ancestors_subtask_is_epic_then_parent():
    sub, parent, epic = _subtask_chain()
    anc = _client().ticket_ancestors(sub)
    assert [a["key"] for a in anc] == [epic, parent]      # 위→아래
    for a in anc:
        assert a["summary"] and a["type"]
        assert a["statusCategory"] in ("todo", "inprogress", "done")


def test_ancestors_carry_progress_pct():
    """스파인 레일에 조상별 진척 바를 그리므로 pct 가 실려야 한다."""
    sub, _parent, _epic = _subtask_chain()
    anc = _client().ticket_ancestors(sub)
    assert anc and all(isinstance(a["pct"], int) for a in anc)
    assert all(0 <= a["pct"] <= 100 for a in anc)


def test_ancestors_epic_has_none():
    assert _client().ticket_ancestors(_key_of_type("Epic")) == []


# ── 형제 ──
def test_siblings_of_subtask_are_parent_subtasks_including_self():
    sub, parent, _epic = _subtask_chain()
    sibs = _client().ticket_siblings(sub)
    assert {s["key"] for s in sibs} == set(get_world().issues[parent]["subtasks"])
    cur = [s for s in sibs if s["current"]]
    assert len(cur) == 1 and cur[0]["key"] == sub        # 현재 티켓이 정확히 하나 표시


def test_siblings_of_task_are_epic_children():
    w = get_world()
    task = next(k for k, it in w.issues.items()
                if it.get("epicKey") and not it.get("parentKey") and it.get("type") != "Epic")
    sibs = _client().ticket_siblings(task)
    assert task in {s["key"] for s in sibs}
    assert all(s["summary"] and s["statusCategory"] in ("todo", "inprogress", "done") for s in sibs)
    assert sum(1 for s in sibs if s["current"]) == 1


def test_siblings_of_epic_empty():
    assert _client().ticket_siblings(_key_of_type("Epic")) == []


# ── 캐시 ──
def test_lineage_reuses_per_ticket_cache():
    """조상 각각이 issue:{env}:{key} 로 개별 캐시되고, 조립 결과도 캐시된다."""
    sub, parent, epic = _subtask_chain()
    c = _client()
    env = c.env
    assert c.cache.get(f"issue:{env}:{epic}") is None
    c.ticket_ancestors(sub)
    assert c.cache.get(f"issue:{env}:{epic}") is not None      # 조상 개별 캐시
    assert c.cache.get(f"issue:{env}:{parent}") is not None
    assert c.cache.get(f"ancestors:{env}:{sub}") is not None   # 조립 결과 캐시
    c.ticket_siblings(sub)
    assert c.cache.get(f"siblings:{env}:{sub}") is not None


# ── 타임라인 ──
def test_timeline_has_created_and_status_and_comment():
    """중요 이벤트(생성/상태/댓글)가 최신순으로 나온다."""
    c = _client()
    w = get_world()
    key = next(k for k, it in w.issues.items() if it.get("resolved") and it.get("comments"))
    tl = c.ticket_timeline(key)
    kinds = {e["kind"] for e in tl}
    assert "created" in kinds and "status" in kinds and "comment" in kinds
    dates = [e["date"] for e in tl if e.get("date")]
    assert dates == sorted(dates, reverse=True)          # 최신순
    for e in tl:
        assert "author" in e and "date" in e


def test_timeline_excludes_description_edits():
    """단순 설명 수정은 제외 — world 에 일부러 description 변경 이력을 심어두었다."""
    c = _client()
    w = get_world()
    # description 변경 이력이 실제로 있는 티켓을 고른다(필터가 무의미해지지 않도록)
    key = next(k for k, it in w.issues.items()
               if any(i.get("field") == "description"
                      for ch in it.get("changelog", []) for i in ch["items"]))
    tl = c.ticket_timeline(key)
    assert tl, "타임라인이 비어 필터 검증 불가"
    assert all((e.get("field") or "").lower() != "description" for e in tl)
    assert "description" not in {e["kind"] for e in tl}


def test_timeline_cached():
    c = _client()
    key = _key_of_type("Bug")
    c.ticket_timeline(key)
    assert c.cache.get(f"timeline:{c.env}:{key}") is not None
