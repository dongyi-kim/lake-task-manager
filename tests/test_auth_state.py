# -*- coding: utf-8 -*-
"""인증 상태 판정 — '첫 기동에 데이터가 안 뜨고 수동 새로고침해야 한다' 회귀.

**리포트된 버그**: prod 앱을 켜면 각 탭이 계속 '불러오는 중' 이고, 로그인을 마쳐도
화면이 살아나지 않아 결국 Ctrl+Shift+R 을 눌러야 했다.

원인 사슬:
  1. needs_login() 이 '세션 **파일**이 하나라도 있나' 만 봤다. prod 첫 기동의 대부분은
     **파일은 있고 쿠키만 만료된** 상태 → False(=인증됨) 로 답했다.
  2. /api/status 가 그 값을 그대로 내보냈다 → 세션이 죽었는데 needLogin=false.
  3. 프론트의 인증 감시자(api.js watchAuth)는 그 한 값만 보고 '인증 완료' 로 판단해
     **거짓 auth-ok** 를 쏘고 감시를 멈췄다 → 진짜 로그인이 끝나도 아무도 화면에
     알려 주지 않는다.
  4. 회로차단기(20초)가 살아 있는 동안만 needLogin 이 우연히 true 였다가, 20초가
     지나면 false 로 돌아갔다 — '처음엔 되는 것 같다가 안 된다' 의 정체.

여기서 고정하는 것: **호출이 실제로 실패해 죽은 것으로 확인되면** 성공할 때까지
needs_login 이 True 를 유지한다(시간으로 풀리지 않는다).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.infra.cache import Cache                    # noqa: E402
from app.infra.settings import get_settings          # noqa: E402
from app.jira.jira_client import JiraClient          # noqa: E402


def _prod_client():
    """prod 판정 경로를 타되 상류는 건드리지 않는 클라이언트(세션 파일은 '있다')."""
    c = JiraClient(get_settings(), Cache(":memory:"))
    c.env = "prod"
    store = c.sso_store()
    store.any_exists = lambda: True                  # 파일은 있다 — 쿠키만 만료된 상황
    c.sso_store = lambda: store
    return c


def test_file_exists_alone_does_not_mean_logged_in():
    """세션 파일이 있어도, 호출이 죽은 것으로 확인되면 로그인이 필요하다."""
    c = _prod_client()
    assert c.needs_login() is False                  # 아직 실패한 적 없음
    c.mark_session_dead("세션 만료")
    assert c.needs_login() is True


def test_session_dead_does_not_expire_with_the_circuit_breaker():
    """★ 이 버그의 핵심 — 차단기(20초)가 풀려도 '죽음' 은 안 풀린다.

    예전엔 needLogin 이 차단기에만 얹혀 있어, 20초가 지나면 세션이 죽은 채로
    '인증됨' 이 됐다(그 순간 프론트가 거짓 auth-ok 를 쐈다)."""
    c = _prod_client()
    c.mark_session_dead("세션 만료")
    c._upstream_down_until = 0                       # 차단기만 만료시킨다(시간 경과 흉내)
    assert c.upstream_down() is False
    assert c.needs_login() is True                   # 그래도 로그인은 여전히 필요하다


def test_upstream_success_clears_both():
    """성공하면 차단기와 죽음 표시가 함께 풀린다(예전엔 이 함수를 아무도 안 불렀다)."""
    c = _prod_client()
    c.mark_session_dead("세션 만료")
    assert c.upstream_down() is True and c.needs_login() is True
    c.mark_upstream_ok()
    assert c.upstream_down() is False and c.needs_login() is False


def test_new_session_clears_dead_mark():
    """로그인 후 provider 를 갈아끼우면 죽음 표시도 지운다(다시 실패하면 다시 선다)."""
    c = _prod_client()
    c.mark_session_dead("세션 만료")
    c.reset_provider()
    assert c.needs_login() is False


def test_cache_reports_upstream_success():
    """캐시가 상류 성공을 알려 줘야 죽음 표시가 풀린다 — 성공을 아는 곳이 거기뿐이다."""
    seen = []
    cache = Cache(":memory:")
    cache.on_upstream_ok = lambda: seen.append(1)
    cache.get_or_set("k", 60, lambda: {"v": 1})
    assert seen == [1]
    cache.get_or_set("k", 60, lambda: {"v": 2})      # 캐시 히트는 상류 성공이 아니다
    assert seen == [1]


def test_status_endpoint_tells_the_truth_after_a_failed_call():
    """/api/status 는 세션이 죽은 걸 확인한 뒤에는 needLogin 을 켜야 한다.
    프론트의 인증 감시자가 보는 값이 이것 하나다."""
    from fastapi.testclient import TestClient
    import app.main as m

    client = m._client
    env = client.env
    store = client.sso_store()
    old_any, old_store, old_env = store.any_exists, client.sso_store, m._settings.jira_env
    store.any_exists = lambda: True
    client.sso_store = lambda: store
    client.env = "prod"
    m._settings.jira_env = "prod"
    try:
        c = TestClient(m.app)
        client.mark_upstream_ok()
        assert c.get("/api/status").json()["needLogin"] is False
        client.mark_session_dead("세션 만료")
        client._upstream_down_until = 0               # 차단기가 아니라 '죽음' 이 근거여야 한다
        body = c.get("/api/status").json()
        assert body["needLogin"] is True
    finally:
        store.any_exists = old_any
        client.sso_store = old_store
        client.env = env
        m._settings.jira_env = old_env
        client.mark_upstream_ok()
