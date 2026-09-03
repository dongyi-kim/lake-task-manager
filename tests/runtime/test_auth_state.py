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
    """직접 Jira 인증 성공은 차단기와 죽음 표시를 함께 푼다."""
    c = _prod_client()
    c.mark_session_dead("세션 만료")
    assert c.upstream_down() is True and c.needs_login() is True
    c.mark_session_alive()
    assert c.upstream_down() is False and c.needs_login() is False


def test_unrelated_cache_success_does_not_clear_confirmed_jira_session_dead():
    """Confluence/부분 조립 캐시 성공은 Jira 인증이 살아 있다는 증거가 아니다."""
    c = _prod_client()
    c._wire_cache()
    c.mark_session_dead("HTTP 401 on Jira")

    c.cache.get_or_set("nonjira:successful-producer", 60, lambda: {"ok": True})

    assert c.upstream_down() is False       # transport 회로는 다시 시도할 수 있게 닫는다
    assert c.needs_login() is True          # 하지만 확인된 Jira 만료는 그대로 유지한다


def test_direct_myself_success_clears_confirmed_session_dead(monkeypatch):
    """세션 dead 해제의 근거는 실제 Jira /myself 성공이다."""
    c = _prod_client()
    c.mark_session_dead("HTTP 401 on Jira")

    class _Alive:
        def get_json(self, path, params=None):
            assert path == "/rest/api/2/myself"
            return {"name": "test.ui01"}

    c._provider, c._provider_built = _Alive(), True
    c._auth_probe_result = {"ok": False, "needLogin": True, "mode": "authenticating"}
    c._auth_probe_at = time.monotonic()
    assert c.direct_session_state() == "alive"
    assert c.upstream_down() is False and c.needs_login() is False
    assert c._auth_probe_result is None


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
        client.mark_session_alive()
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
        client.mark_session_alive()


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


def test_myself_anonymous_200_is_not_cached_or_marked_alive():
    """HTTP 200이라도 anonymous면 인증 성공도 정상 빈 사용자도 아니다."""
    c = _prod_client()
    calls = []

    class _Anonymous:
        def get_json(self, path, params=None, quiet=False):
            calls.append(path)
            return {"name": "anonymous", "displayName": "Anonymous"}

    c._provider, c._provider_built = _Anonymous(), True
    assert c.current_user() == {}
    assert c.needs_login() is True
    assert c.cache.get(f"myself:{c.env}") is None
    assert calls == ["/rest/api/2/myself"]


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


def test_direct_session_probe_does_not_call_network_failure_expired():
    """일시 네트워크 실패는 로그인 창을 띄울 근거가 아니다."""
    from app.auth.base import UpstreamUnavailable

    c = _prod_client()

    class _Unavailable:
        def get_json(self, path, params=None):
            raise UpstreamUnavailable("temporary network loss")

    c._provider, c._provider_built = _Unavailable(), True
    assert c.direct_session_state() == "unknown"
    result = c.proactive_auth_probe()
    assert result["mode"] == "degraded"
    assert result["needLogin"] is False


def test_unknown_probe_does_not_reissue_login_for_previously_dead_session(monkeypatch):
    """이미 dead여도 현재 판정이 망 문제면 새 로그인 이벤트를 만들지 않는다."""
    c = _prod_client()
    c.mark_session_dead("earlier confirmed HTTP 401")
    monkeypatch.setattr(c, "direct_session_state", lambda **_kwargs: "unknown")

    result = c.proactive_auth_probe()

    assert result == {"ok": False, "needLogin": False, "mode": "degraded"}
    assert c.needs_login() is True       # 내부의 확정 상태 자체는 지우지 않는다


def test_probe_lock_timeout_never_opens_a_second_login_flow():
    """먼저 진행 중인 probe를 기다리다 끝난 요청은 인증 만료의 새 증거가 아니다."""
    c = _prod_client()
    c.mark_session_dead("earlier confirmed HTTP 401")

    class _BusyLock:
        @staticmethod
        def acquire(timeout=None):
            assert timeout == 35
            return False

    c._auth_probe_lock = _BusyLock()
    result = c.proactive_auth_probe()

    assert result["pending"] is True and result["needLogin"] is False


def _auth_cookie(name, value, domain="127.0.0.1"):
    return {"name": name, "value": value, "domain": domain, "path": "/"}


def test_activity_myself_persists_rolling_jira_cookie_with_throttle(tmp_path):
    """activity-triggered direct probe는 최신 context cookie를 Jira 파일에만 bounded 저장한다."""
    import json
    from app.auth.sso_store import SsoStore

    c = _prod_client()
    store = SsoStore(str(tmp_path / "state.json"), {
        "jira": c.s.jira_base,
        "confluence": "https://conf.example.test",
    })
    store.save("jira", {"cookies": [_auth_cookie("JSESSIONID", "old")], "origins": []})
    store.save("confluence", {
        "cookies": [_auth_cookie("CONF", "keep", "conf.example.test")], "origins": []})
    conf_before = store.path("confluence").read_bytes()
    snapshots = []

    class _Rolling:
        broken = False

        @staticmethod
        def get_json(path, params=None):
            assert path == "/rest/api/2/myself"
            return {"name": "test.ui01"}

        @staticmethod
        def storage_state_snapshot():
            snapshots.append(1)
            return {"cookies": [
                _auth_cookie("JSESSIONID", "rolled"),
                _auth_cookie("CONF", "do-not-write", "conf.example.test"),
            ], "origins": []}

    c._provider, c._provider_built = _Rolling(), True
    c.sso_store = lambda: store

    assert c.direct_session_state(background=True) == "alive"
    assert c.direct_session_state(background=True) == "alive"

    saved = json.loads(store.path("jira").read_text(encoding="utf-8"))
    assert [(row["name"], row["value"]) for row in saved["cookies"]] == [
        ("JSESSIONID", "rolled")]
    assert store.path("confluence").read_bytes() == conf_before
    assert snapshots == [1]                       # 두 번째 probe는 15분 throttle


def test_new_login_disk_revision_fences_old_provider_cookie_snapshot(tmp_path):
    """snapshot 캡처 도중 새 로그인 파일이 생기면 old rolling state는 저장하지 않는다."""
    import json
    from app.auth.sso_store import SsoStore

    c = _prod_client()
    store = SsoStore(str(tmp_path / "state.json"), {"jira": c.s.jira_base})
    store.save("jira", {"cookies": [_auth_cookie("JSESSIONID", "initial")], "origins": []})

    class _OldProvider:
        broken = False

        @staticmethod
        def get_json(path, params=None):
            return {"name": "test.ui01"}

        @staticmethod
        def storage_state_snapshot():
            # This is the exact race: visible login commits after snapshot work was scheduled but
            # before the old provider attempts its disk write.
            store.save("jira", {
                "cookies": [_auth_cookie("JSESSIONID", "new-login")], "origins": []})
            return {"cookies": [_auth_cookie("JSESSIONID", "old-provider")], "origins": []}

    c._provider, c._provider_built = _OldProvider(), True
    c.sso_store = lambda: store

    assert c.direct_session_state(background=True) == "alive"
    saved = json.loads(store.path("jira").read_text(encoding="utf-8"))
    assert saved["cookies"][0]["value"] == "new-login"
    assert c._session_state_persist_at == 0.0       # 다음 activity에서 다시 시도 가능


def test_provider_generation_fences_snapshot_captured_during_reset(tmp_path):
    """디스크가 아직 안 바뀌었어도 교체된 provider의 snapshot은 폐기한다."""
    import json
    from app.auth.sso_store import SsoStore

    c = _prod_client()
    store = SsoStore(str(tmp_path / "state.json"), {"jira": c.s.jira_base})
    store.save("jira", {"cookies": [_auth_cookie("JSESSIONID", "initial")], "origins": []})

    class _OldProvider:
        broken = False

        @staticmethod
        def get_json(path, params=None):
            return {"name": "test.ui01"}

        @staticmethod
        def storage_state_snapshot():
            c.reset_provider()
            return {"cookies": [_auth_cookie("JSESSIONID", "detached-old")], "origins": []}

        @staticmethod
        def close():
            return None

    c._provider, c._provider_built = _OldProvider(), True
    c.sso_store = lambda: store

    assert c.direct_session_state(background=True) == "alive"
    saved = json.loads(store.path("jira").read_text(encoding="utf-8"))
    assert saved["cookies"][0]["value"] == "initial"
    assert c._provider is None and c._provider_built is False
    assert c._session_state_persist_at == 0.0


def test_provider_storage_snapshot_is_marshaled_to_owner_thread():
    """BrowserContext.storage_state를 FastAPI worker에서 직접 만지지 않는다."""
    import inspect
    from app.auth.sso_session import SsoSessionProvider

    source = inspect.getsource(SsoSessionProvider.storage_state_snapshot)
    assert "self._context.storage_state()" in source
    assert "self._submit(capture, PRIO_BACKGROUND)" in source


def test_proactive_probe_silently_renews_an_expired_jira_session(monkeypatch):
    """유휴 복귀 확인이 만료를 찾으면 보이는 창 전에 silent SSO를 한 번 시도한다."""
    c = _prod_client()
    states = iter(["expired", "alive"])
    renewed = []
    monkeypatch.setattr(c, "direct_session_state", lambda **_kwargs: next(states))
    monkeypatch.setattr(c, "_renew_service", lambda name: renewed.append(name) or True)

    result = c.proactive_auth_probe()

    assert renewed == ["Jira"]
    assert result == {"ok": True, "needLogin": False, "recovered": True, "mode": "ok"}
    assert c.needs_login() is False


def test_proactive_probe_is_prod_only_and_does_not_build_provider():
    c = _prod_client()
    c.env = "mock"
    result = c.proactive_auth_probe()
    assert result["skipped"] is True and result["needLogin"] is False
    assert c._provider_built is False


def test_proactive_probe_reuses_recent_result(monkeypatch):
    """focus와 visibilitychange가 연달아 와도 direct Jira probe는 한 번뿐이다."""
    c = _prod_client()
    calls = []
    monkeypatch.setattr(c, "direct_session_state", lambda **_kwargs: calls.append(1) or "alive")
    first = c.proactive_auth_probe()
    second = c.proactive_auth_probe()
    assert first["ok"] is True
    assert second["ok"] is True and second["cached"] is True
    assert calls == [1]


def test_auth_probe_endpoint_is_a_noop_outside_prod(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as m

    monkeypatch.setattr(m._client, "proactive_auth_probe", lambda: {
        "ok": True, "needLogin": False, "skipped": True, "mode": "local",
    })
    body = TestClient(m.app).post("/api/auth/probe").json()
    assert body["ok"] is True and body["skipped"] is True


def test_auth_exception_does_not_turn_an_unknown_probe_into_login(monkeypatch):
    """401처럼 보인 첫 오류 뒤 직접 확인도 망 때문에 실패하면 인증 만료로 단정하지 않는다."""
    import json
    import app.main as m
    from app.auth.base import SessionExpired

    monkeypatch.setattr(m._settings, "jira_env", "prod")
    monkeypatch.setattr(m._client, "direct_session_state", lambda: "unknown")
    marked = []
    monkeypatch.setattr(m._client, "mark_upstream_down", lambda reason="": marked.append(reason))
    response = m._on_session_expired(None, SessionExpired("HTTP 401 during network flap"))
    body = json.loads(response.body)
    assert response.status_code == 503
    assert body["needLogin"] is False and body["retryable"] is True
    assert marked


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
