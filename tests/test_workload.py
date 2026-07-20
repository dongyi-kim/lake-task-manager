"""워크로드 집계(_fetch_workload) — 실패를 0 으로 위장하지 않는다.

회귀 배경(prod 버그): counts() 가 모든 예외를 삼켜 0 을 반환하고 그 0 이
`workload:{env}:{pid}` 로 캐시돼, **막대만 0 인데 [자세히] 리스트는 정상**으로 보였다.
(상세는 `workload_tickets:{env}:{user}` 라는 다른 캐시 키 + 다른 시점 조회)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.auth.base import SessionExpired      # noqa: E402
from app.cache import Cache                   # noqa: E402
from app.jira_client import JiraClient        # noqa: E402
from app.settings import get_settings         # noqa: E402

PLAN = {"modules": ["ETL"], "project_key": "DL"}
PEOPLE = {"ETL": ["skcc.x1042"]}


def _client():
    return JiraClient(get_settings(), Cache(":memory:"))


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
    assert c.cache.get(f"workload:{c.env}:skcc.x1042") is None   # ★ 캐시되지 않았다

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
    assert c.cache.get(f"workload:{c.env}:skcc.x1042") is None
