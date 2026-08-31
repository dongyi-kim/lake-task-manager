"""워크로드 집계(_fetch_workload) — 실패를 0 으로 위장하지 않는다.

회귀 배경(prod 버그): counts() 가 모든 예외를 삼켜 0 을 반환하고 그 0 이
`workload:{env}:{pid}` 로 캐시돼, **막대만 0 인데 [자세히] 리스트는 정상**으로 보였다.
(상세는 `workload_tickets:{env}:{user}` 라는 다른 캐시 키 + 다른 시점 조회)
"""
from datetime import timedelta
import os
import sys

import pytest

from app.auth.base import SessionExpired      # noqa: E402
from app.infra.cache import Cache                   # noqa: E402
from app.jira.jira_client import JiraClient        # noqa: E402
from app.jira.workload_service import JiraWorkloadMixin  # noqa: E402
from app.infra.settings import get_settings, load_people   # noqa: E402
from app.mock.world import get_world                 # noqa: E402

PLAN = {"modules": ["ETL"], "project_key": "DL"}
PEOPLE = {"ETL": ["skcc.x1042"]}


def _client():
    return JiraClient(get_settings(), Cache(":memory:"))


def test_jira_client_preserves_workload_service_facade_contract():
    assert issubclass(JiraClient, JiraWorkloadMixin)
    for name in ("workload_person", "workload_bucket", "workload_tickets", "activity"):
        assert getattr(JiraClient, name) is getattr(JiraWorkloadMixin, name)


def _total(bundle):
    return sum(bundle["inProgress"]["count"].values()) + sum(bundle["open"]["count"].values())


def test_workload_counts_nonzero_in_mock():
    """기준선: mock 에서는 정상적으로 집계가 나온다(사람마다 0 이 아님)."""
    c = _client()
    rows = c.workload(PLAN, PEOPLE)["ETL"]
    assert rows and rows[0]["id"] == "skcc.x1042"
    assert not rows[0].get("error")
    assert _total(rows[0]) > 0


def test_search_failure_is_not_cached_as_zero():
    """조회 실패 시 0 을 캐시하면 안 된다 — error 표시 + 다음 호출에서 재시도해 정상값."""
    c = _client()
    boom = {"n": 0}
    real_search = c._search

    def flaky(jql, **kw):
        boom["n"] += 1
        raise RuntimeError("transient prod failure")

    c._search = flaky
    rows = c.workload(PLAN, PEOPLE)["ETL"]
    assert rows[0].get("error") is True          # 실패를 드러낸다
    assert _total(rows[0]) == 0                  # 값은 0 이지만
    assert c.cache.get(
        f"workload:{c.env}:skcc.x1042:done:7:assigned:all"
    ) is None   # ★ 캐시되지 않았다

    c._search = real_search                      # 복구 후 재조회 → 정상값
    rows2 = c.workload(PLAN, PEOPLE)["ETL"]
    assert not rows2[0].get("error")
    assert _total(rows2[0]) > 0


def test_session_expired_propagates_not_zero():
    """세션 만료는 0 으로 위장하지 말고 올려보내야 한다(라우트가 401 needLogin 처리)."""
    c = _client()

    def expired(jql, **kw):
        raise SessionExpired("session expired")

    c._search = expired
    with pytest.raises(SessionExpired):
        c.workload(PLAN, PEOPLE)
    assert c.cache.get(
        f"workload:{c.env}:skcc.x1042:done:7:assigned:all"
    ) is None


# ── 모듈/버킷 분할 (병렬 로딩용) ──
def test_workload_module_matches_full_build():
    """모듈별 조립 결과가 전체 조립의 해당 모듈과 같아야 한다(분할해도 값이 안 변함)."""
    from app.domain import workload as wl
    c = _client()
    full = wl.build_workload(c, PLAN, PEOPLE)["modules"][0]
    part = wl.build_workload_module(c, PLAN, PEOPLE, "ETL")
    for k in ("module", "peopleCount", "openTotal", "inProgressTotal", "done7dTotal"):
        assert full[k] == part[k], k
    assert [p["id"] for p in full["people"]] == [p["id"] for p in part["people"]]


def test_workload_buckets_match_combined():
    """버킷 3개를 따로 부른 결과 == workload_tickets 한 번에 부른 결과."""
    c = _client()
    user = PEOPLE["ETL"][0]
    combined = c.workload_tickets(user)
    for b in ("open", "inProgress", "done7d"):
        assert c.workload_bucket(user, b) == combined[b], b
    assert c.workload_bucket(user, "nope") is None


def test_workload_bucket_cached_individually():
    c = _client()
    user = PEOPLE["ETL"][0]
    c.workload_bucket(user, "inProgress")
    assert c.cache.get(f"workload_bucket:{c.env}:{user}:inProgress:all") is not None
    assert c.cache.get(f"workload_bucket:{c.env}:{user}:done7d") is None   # 부른 것만 캐시


def test_subtask_inherits_parent_epic_in_summary_and_detail():
    """SubTask 는 직접 Epic Link 가 없어도 Parent Task 의 Epic 으로 집계돼야 한다."""
    world = get_world()
    case = None
    for key, issue in world.issues.items():
        parent = world.issues.get(issue.get("parentKey")) or {}
        recent_open = (issue.get("statusCategory") == "todo"
                       and (world.today - issue["updated"]).days <= 14)
        if (issue.get("type") == "Sub-Task" and issue.get("assignee")
                and parent.get("epicKey")
                and (issue.get("statusCategory") == "inprogress" or recent_open)):
            case = (key, issue, parent)
            break
    assert case, "Parent Epic 이 있는 미완료 SubTask fixture 가 필요하다"

    key, issue, parent = case
    bucket = "inProgress" if issue["statusCategory"] == "inprogress" else "open"
    c = _client()

    detail = next(t for t in c.workload_bucket(issue["assignee"], bucket)
                  if t["key"] == key)
    assert detail["epic"] == parent["epicKey"]
    assert detail["epicName"]

    summary = c.workload_person(issue["assignee"])[bucket]
    assert summary["epics"][parent["epicKey"]]["count"] >= 1


# ── '최근 완료' 기간 필터 (1·2·4주) ──────────────────────────────────────────
# 리포트된 버그: **일부 사람만** 완료 Task 가 누락됐다. 원인은 옛 질의가 `resolved >= -7d`
# 하나만 봤다는 것 — Resolved 를 거치지 않고 Closed 로 바로 가거나 resolution 없이 완료로
# 넘어가면 resolutiondate 가 **비어 있어** 그 사람의 완료가 통째로 빠졌다.

def test_wl_done_days_only_allows_known_values():
    """임의 숫자를 그대로 JQL 에 넣지 않는다(주입·오타 방어)."""
    assert JiraClient.wl_done_days(14) == 14
    assert JiraClient.wl_done_days("28") == 28
    for bad in (1, 999, -7, None, "abc", "7 OR 1=1"):
        assert JiraClient.wl_done_days(bad) == JiraClient.WL_DONE_DEFAULT


def test_wl_done_jql_covers_empty_resolutiondate():
    """완료 판정은 statusCategory, 시점은 resolved 없으면 updated 로 폴백해야 한다."""
    jql = JiraClient.wl_done_jql(14)
    assert "statusCategory = Done" in jql
    assert "resolved >= -14d" in jql
    assert "resolved IS EMPTY AND updated >= -14d" in jql   # ← 누락되던 그 부류


def test_workload_bucket_period_widens_result():
    """기간을 넓히면 완료 목록은 **실제로 늘어난다**(부분집합 + 진짜 증가).
    빈 목록끼리 비교하면 부분집합은 늘 참이라 아무것도 증명 못 한다 — 완료가 있는 인력으로 본다.

    대상 인력을 **찾아서** 본다. world 는 rng 시퀀스가 조금만 움직여도 누가 최근에 뭘 끝냈는지가
    통째로 바뀌어서, 특정 id 를 박아두면 데이터와 무관한 이유로 깨진다(실제로 겪음).
    """
    c = _client()
    roster = [u for ids in load_people().values() for u in ids]
    assert roster, "인력 명단이 비어 있다"

    for user in roster:
        keys = {d: {t["key"] for t in c.workload_bucket(user, "done7d", d)}
                for d in JiraClient.WL_DONE_DAYS}
        if keys[7] and keys[7] < keys[14] < keys[28]:
            break
    else:
        pytest.fail("기간을 넓혔을 때 완료가 실제로 늘어나는 인력이 하나도 없다 — world 가 얕다")
    # 버킷 캐시는 기간별로 갈려야 한다 — 안 그러면 1주 결과가 4주 자리에 앉는다
    for d in JiraClient.WL_DONE_DAYS:
        assert c.cache.get(f"workload_bucket:{c.env}:{user}:done7d:{d}") is not None


def test_workload_person_cache_key_includes_period():
    c = _client()
    pid = PEOPLE["ETL"][0]
    c.workload_person(pid, 7, "all")
    c.workload_person(pid, 28, "1m")
    assert c.cache.get(f"workload:{c.env}:{pid}:done:7:assigned:all") is not None
    assert c.cache.get(f"workload:{c.env}:{pid}:done:28:assigned:1m") is not None
    # 잘못된 기간은 기본값으로 정규화 — 없는 키를 만들지 않는다
    c.workload_person(pid, 999, "not-a-window")
    assert c.cache.get(f"workload:{c.env}:{pid}:done:999:assigned:not-a-window") is None


# ── 할당(Open + In-Progress) 갱신기간 + MyTasks cache sharing ───────────────

def _assigned_issue(key, updated, status_category):
    name = "Open" if status_category == "todo" else "In Progress"
    return {
        "key": key,
        "fields": {
            "summary": key,
            "updated": updated,
            "components": [],
            "issuetype": {"name": "Task", "subtask": False},
            "status": {"name": name, "statusCategory": {"key": status_category}},
        },
    }


def test_wl_assigned_window_only_allows_known_values():
    assert JiraClient.wl_assigned_window("1w") == "1w"
    assert JiraClient.wl_assigned_window("1M") == "1m"
    assert JiraClient.wl_assigned_window("all") == "all"
    for bad in (None, "", "2w", "7 OR 1=1"):
        assert JiraClient.wl_assigned_window(bad) == JiraClient.WL_ASSIGNED_DEFAULT


@pytest.mark.parametrize("bucket,status_category", [
    ("open", "todo"),
    ("inProgress", "inprogress"),
])
def test_assigned_window_widens_both_open_and_in_progress(bucket, status_category):
    """두 미완료 상태 모두 1주 ⊂ 1달 ⊂ 전체이며 숨은 200건 cap이 없다."""
    c = _client()
    today = c.s_today()
    source = [
        _assigned_issue("DL-RECENT", (today - timedelta(days=3)).isoformat(), status_category),
        _assigned_issue("DL-MONTH", (today - timedelta(days=20)).isoformat(), status_category),
        _assigned_issue("DL-OLD", (today - timedelta(days=60)).isoformat(), status_category),
    ]
    calls = []

    def fake_search(jql, **kwargs):
        calls.append((jql, kwargs))
        return list(source)

    c._search = fake_search
    keys = {
        window: {row["key"] for row in c.workload_bucket(
            "test.person", bucket, assigned_window=window)}
        for window in JiraClient.WL_ASSIGNED_WINDOWS
    }
    assert keys["1w"] == {"DL-RECENT"}
    assert keys["1m"] == {"DL-RECENT", "DL-MONTH"}
    assert keys["all"] == {"DL-RECENT", "DL-MONTH", "DL-OLD"}
    assert all("updated" not in jql.lower() for jql, _kwargs in calls)
    assert all(kwargs.get("max_results", "missing") is None for _jql, kwargs in calls)


def test_mytasks_all_assigned_leaves_are_reused_by_every_workload_window():
    """MyTasks 기본 방문 뒤 Workload 1주·1달·전체는 broad leaf를 Jira에 다시 묻지 않는다."""
    from app.domain.mytasks import build_my_tasks

    c = _client()
    user = (c.current_user() or {}).get("id")
    assert user
    build_my_tasks(
        c, user=user, scope="assignee", open_filter="all", prog_filter="all",
        done_filter="1w", defer_children=True,
    )

    fetched = []
    real_fetch = c._fetch_jql_leaf

    def spy_fetch(leaf, fields, light):
        fetched.append(leaf)
        return real_fetch(leaf, fields, light)

    c._fetch_jql_leaf = spy_fetch
    for window in JiraClient.WL_ASSIGNED_WINDOWS:
        for template in JiraClient.WL_BUCKETS.values():
            rows = c._search(template.format(u=user), max_results=None)
            assert isinstance(rows, list)
    assert fetched == []
