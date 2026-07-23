"""매니저 화이트리스트 — 권한 규칙이라 회귀하면 조용히 화면이 사라지거나 열린다."""
from types import SimpleNamespace

from app.settings import is_manager


def _s(managers):
    return SimpleNamespace(managers=managers)


def test_empty_whitelist_means_no_restriction():
    """빈 목록은 '아무도 없음' 이 아니라 '미설정' — 안 그러면 config 를 안 채운 설치에서
    아무에게도 아무것도 안 보인다."""
    assert is_manager(_s([]), "anyone") is True
    assert is_manager(_s([]), {"id": "anyone"}) is True


def test_whitelist_matches_by_id():
    st = _s(["pm.kim", "pl.lee"])
    assert is_manager(st, "pm.kim") is True
    assert is_manager(st, "worker.park") is False


def test_case_insensitive_and_trimmed():
    st = _s(["pm.kim"])
    assert is_manager(st, "  PM.Kim  ") is True


def test_dict_matches_any_representation():
    """사용자 표현이 여러 가지다 — 우리 내부 {id: 사번, name: **표시이름**},
    Jira 원본 {name: 사번, key, displayName}. 설정에 적은 값이 어느 필드에 있든 통해야 한다.
    하나만 골라 비교하면 본인은 매니저인데 조용히 거부된다(실제로 겪은 버그)."""
    assert is_manager(_s(["test.ui01"]), {"id": "test.ui01", "name": "UI픽스처01"}) is True
    assert is_manager(_s(["ui픽스처01"]), {"id": "test.ui01", "name": "UI픽스처01"}) is True
    assert is_manager(_s(["skcc.11890"]), {"id": "x", "key": "skcc.11890"}) is True
    assert is_manager(_s(["a@b.com"]), {"id": "x", "emailAddress": "a@b.com"}) is True
    assert is_manager(_s(["test.ui01"]), {"id": "other", "name": "다른사람"}) is False


def test_raw_jira_myself_falls_back_to_name():
    """Jira 원본 /myself 는 id 가 없고 name 이 곧 사번이다."""
    assert is_manager(_s(["pm.kim"]), {"name": "pm.kim", "displayName": "김PM"}) is True


def test_missing_user_is_not_manager():
    assert is_manager(_s(["pm.kim"]), None) is False
    assert is_manager(_s(["pm.kim"]), "") is False


def test_current_user_does_not_cache_failure():
    """실패한 /myself 를 캐시하면, 로그인에 성공해도 TTL 동안 '세션 없음' 이 남아
    매니저 판정이 계속 False 가 된다(prod 에서 겪은 '새로고침해도 안 풀리는 인증 오류')."""
    from app.cache import Cache
    from app.jira_client import JiraClient

    calls = []

    class _P:
        def get_json(self, path, **kw):
            calls.append(path)
            if len(calls) == 1:               # 첫 호출: 아직 세션 없음
                raise RuntimeError("HTTP 401 — 세션 만료 가능")
            return {"name": "pm.kim", "displayName": "김PM SKCC"}

    c = JiraClient.__new__(JiraClient)        # 네트워크/설정 없이 이 메서드만 본다
    c.cache = Cache(":memory:")
    c.env = "prod"
    c.s = SimpleNamespace(cache_ttl_seconds=900)
    c._provider = _P()
    c._provider_built = True   # provider 프로퍼티가 _provider 를 그대로 준다

    assert c.current_user() == {}             # 실패 — 캐시되면 안 된다
    assert c.current_user()["id"] == "pm.kim"  # 로그인 후 즉시 반영
    assert len(calls) == 2


def test_unknown_session_is_not_treated_as_worker():
    """'세션을 아직 못 읽음' 과 '매니저가 아님' 은 다른 상태다. 같이 취급하면 prod 첫 실행에서
    로그인도 못 한 채 '매니저 전용 화면입니다' 만 보게 된다(권한이 아니라 인증 문제인데).
    current_user() 는 실패를 예외가 아니라 **빈 dict** 로 알린다 — 그 경로가 핵심."""
    import app.main as m

    orig = m._client.current_user
    try:
        m._client.current_user = lambda: {}          # 세션 미확인
        m._settings.managers = ["pm.kim"]
        assert m._is_manager() is True               # 막지 않는다
        m._client.current_user = lambda: {"id": "worker.park", "name": "박워커"}
        assert m._is_manager() is False              # 읽혔고 목록에 없으면 워커
        m._client.current_user = lambda: {"id": "pm.kim", "name": "김PM"}
        assert m._is_manager() is True
    finally:
        m._client.current_user = orig
        m._settings.managers = []


# ── 2중 TTL 캐시 — 오프라인/미인증에서 화면을 지키는 지점 ──────────────────
def test_cache_serves_stale_when_upstream_fails():
    """outdated 를 넘겨도 dead 안이면 낡은 값으로 버틴다. 예전엔 producer 예외가 그대로
    올라가 화면이 통째로 비었는데, 정작 같은 데이터가 캐시에 있었다."""
    import time as _t

    from app.cache import Cache
    c = Cache(":memory:", dead_ttl=3600)
    c.set("k", {"v": 1}, ttl=0)                      # 즉시 outdated
    _t.sleep(0.01)

    def boom():
        raise RuntimeError("offline")

    val, hit = c.get_or_set("k", 0, boom)
    assert val == {"v": 1} and hit is True
    assert c.served_stale_at > 0


def test_cache_raises_when_value_is_dead():
    """dead 를 넘긴 값은 없는 것과 같다 — 숨기지 말고 그대로 알린다(그래야 로그인을 막는다)."""
    import time as _t

    from app.cache import Cache
    c = Cache(":memory:", dead_ttl=0)
    c.set("k", {"v": 1}, ttl=0)
    _t.sleep(0.01)
    try:
        c.get_or_set("k", 0, lambda: (_ for _ in ()).throw(RuntimeError("offline")))
        raise AssertionError("dead 값이면 예외가 올라와야 한다")
    except RuntimeError:
        pass


def test_cache_skips_producer_when_upstream_known_down():
    """상류가 죽은 걸 아는 동안엔 붙어 보지 않는다 — prod 는 실패 판정에만 최대 180초를 쓴다."""
    import time as _t

    from app.cache import Cache
    c = Cache(":memory:", dead_ttl=3600)
    c.set("k", {"v": 1}, ttl=0)
    _t.sleep(0.01)
    calls = []
    c.skip_producer = lambda: True
    val, hit = c.get_or_set("k", 0, lambda: calls.append(1) or {"v": 2})
    assert val == {"v": 1} and hit is True and calls == []


def test_has_any_ignores_dead_rows():
    """'캐시로 버틸 수 있는가' 는 dead 를 넘긴 값을 세면 안 된다 — 세면 빈 화면으로 진입한다."""
    import time as _t

    from app.cache import Cache
    c = Cache(":memory:", dead_ttl=0)
    c.set("k", {"v": 1}, ttl=999)
    _t.sleep(0.01)
    assert c.has_any() is False
    c.dead_ttl = 3600
    assert c.has_any() is True
