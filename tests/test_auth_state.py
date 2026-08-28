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
import itertools
import queue
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.infra.cache import Cache                    # noqa: E402
from app.infra.settings import get_settings          # noqa: E402
from app.jira.jira_client import JiraClient          # noqa: E402
from app.jira.identity_service import JiraIdentityMixin  # noqa: E402


def test_jira_client_preserves_identity_service_facade_contract():
    assert issubclass(JiraClient, JiraIdentityMixin)
    for name in (
        "current_user", "session_alive", "upstream_state", "_display_name", "user_badge",
    ):
        assert getattr(JiraClient, name) is getattr(JiraIdentityMixin, name)


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


def test_keepalive_renews_only_dead_non_jira_services():
    """세션 데우기 — Jira 는 건너뛰고, 죽은 서비스만 무음갱신을 부른다.

    SSO 쿠키는 도메인별로 따로 만료돼 Jira 만 살아 있는 상태가 흔하다. 그때 검색이 401 을
    맞고 나서야 갱신하면 이미 늦다(그 사이 결과가 비어 보인다) — 미리 데워 둔다."""
    from app.auth.base import SessionExpired

    c = _prod_client()
    c.s.auth_targets = [("Jira", "https://j", ["/rest/api/2/myself"]),
                        ("Confluence", "https://c", ["/rest/api/user/current"])]
    c.s.bitbucket_enabled = False
    hit, renewed = [], []

    class _P:
        def get_json(self, url, params=None, priority=0, quiet=False):
            hit.append(url)
            raise SessionExpired("만료")

    c._provider, c._provider_built = _P(), True
    c._renew_service = lambda name: renewed.append(name) or True

    c.keepalive_auth()
    assert hit == ["https://c/rest/api/user/current"], hit    # Jira 는 안 건드린다
    assert renewed == ["Confluence"]


def test_keepalive_is_prod_only():
    """mock/local 에서는 아무것도 하지 않는다 — 데울 SSO 세션이 없다."""
    c = _prod_client()
    c.env = "mock"
    c.s.auth_targets = [("Confluence", "https://c", ["/x"])]
    called = []
    c._renew_service = lambda name: called.append(name)
    c.keepalive_auth()
    assert called == []


def test_myself_auth_failure_marks_session_dead_without_health_probe():
    """health를 로컬 전용으로 바꿔도 부팅 warm_session이 만료 세션을 상태에 기록한다."""
    from app.auth.base import SessionExpired

    c = _prod_client()

    class _Expired:
        def get_json(self, path, params=None, quiet=False):
            raise SessionExpired("HTTP 401 on /rest/api/2/myself")

    c._provider, c._provider_built = _Expired(), True
    assert c.current_user() == {}
    assert c.needs_login() is True


def test_health_never_calls_jira(monkeypatch):
    """프로세스 health가 상류를 타면 Jira 장애가 곧 localhost 앱 장애가 된다."""
    from fastapi.testclient import TestClient
    import app.main as m

    monkeypatch.setattr(m._client, "needs_login", lambda: False)
    monkeypatch.setattr(m._client, "upstream_state", lambda: {
        "down": True, "reason": "timeout", "hasCache": True, "lastSyncAt": None,
        "servedStaleAt": None,
    })
    monkeypatch.setattr(
        m, "_session_user",
        lambda: (_ for _ in ()).throw(AssertionError("health must not call /myself")),
    )
    body = TestClient(m.app).get("/api/health").json()
    assert body["status"] == "ok" and body["needLogin"] is False


def test_status_distinguishes_transport_stall_from_login(monkeypatch):
    """망은 연결됐지만 provider가 멎은 상태를 인증 만료로 오인하지 않는다."""
    from fastapi.testclient import TestClient
    import app.main as m

    monkeypatch.setattr(m._client, "session_recheck_async", lambda: None)
    monkeypatch.setattr(m._client, "needs_login", lambda: False)
    monkeypatch.setattr(m._client, "upstream_state", lambda: {
        "down": True, "reason": "provider timeout", "hasCache": True,
        "lastSyncAt": 123.0, "servedStaleAt": 124.0,
    })
    monkeypatch.setattr(m, "_probe_online", lambda timeout=1.2: True)
    body = TestClient(m.app).get("/api/status").json()
    assert body["mode"] == "degraded"
    assert body["needLogin"] is False


def test_sso_queue_timeout_breaks_provider_and_future_calls_fail_fast():
    """Playwright worker 한 번이 굳어도 큐의 모든 후속 요청이 180초씩 기다리면 안 된다."""
    from app.auth.base import UpstreamUnavailable
    from app.auth.sso_session import SsoSessionProvider

    p = object.__new__(SsoSessionProvider)
    p._jobs = queue.PriorityQueue()
    p._seq = itertools.count()
    p._broken = threading.Event()
    p._closed = threading.Event()
    p._broken_reason = ""

    class _AliveThread:
        @staticmethod
        def is_alive():
            return True

    p._thread = _AliveThread()
    started = time.monotonic()
    with pytest.raises(UpstreamUnavailable, match="계속 실행"):
        p._submit(lambda: None, wait=0.01)
    assert p.broken is True
    with pytest.raises(UpstreamUnavailable):
        p._submit(lambda: None, wait=10)
    assert time.monotonic() - started < 0.5


def test_broken_provider_rebuild_waits_for_circuit_breaker():
    """장애 직후 Chromium을 연타 생성하지 않고, 차단 시간이 지난 첫 요청만 재생성한다."""
    from app.auth.base import UpstreamUnavailable

    c = _prod_client()
    closed = []

    class _Broken:
        broken = True

        @staticmethod
        def close():
            closed.append(True)

    replacement = object()
    built = []
    c._provider, c._provider_built = _Broken(), True
    c._make_provider = lambda: built.append(True) or replacement
    c.mark_upstream_down("timeout")
    with pytest.raises(UpstreamUnavailable, match="timeout"):
        _ = c.provider
    assert built == [] and closed == []

    c._upstream_down_until = 0
    assert c.provider is replacement
    assert built == [True] and closed == [True]
