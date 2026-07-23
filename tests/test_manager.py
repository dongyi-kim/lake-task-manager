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


# ── 편집에 예민한 데이터는 매번 재검증 ────────────────────────────────────
def test_edit_sensitive_keys_revalidate_even_when_fresh():
    """티켓 본문·코멘트는 사람이 방금 고쳤을 수 있다. TTL 이 15분이면 내가 쓴 댓글이 15분 동안
    안 보이는데, 그건 캐시가 아니라 버그로 보인다 → 캐시로 즉시 그리되 매번 뒤에서 갱신한다."""
    from app.cache import Cache
    c = Cache(":memory:", dead_ttl=3600)
    sched = []
    c.always_revalidate = ("comments:",)
    c.revalidator = lambda k, ttl, prod: sched.append(k)

    c.set("comments:x:DL-1", [{"id": 1}], ttl=999)          # 아주 신선한 값
    val, hit = c.get_or_set("comments:x:DL-1", 999, lambda: [])
    assert val == [{"id": 1}] and hit is True               # 화면은 즉시 캐시로
    assert sched == ["comments:x:DL-1"]                     # 그래도 갱신은 걸린다

    c.set("wbs:x", {"a": 1}, ttl=999)                       # 목록·롤업은 대상 아님
    c.get_or_set("wbs:x", 999, lambda: {})
    assert sched == ["comments:x:DL-1"]


def test_no_revalidate_while_upstream_down():
    """상류가 죽은 걸 아는 동안 갱신을 걸면 큐만 쌓이고 아무것도 최신이 되지 않는다."""
    from app.cache import Cache
    c = Cache(":memory:", dead_ttl=3600)
    sched = []
    c.always_revalidate = ("issue:",)
    c.revalidator = lambda k, ttl, prod: sched.append(k)
    c.skip_producer = lambda: True
    c.set("issue:x:DL-1", {"k": 1}, ttl=999)
    c.get_or_set("issue:x:DL-1", 999, lambda: {})
    assert sched == []


# ── 쓰기 우선순위 — 단일 상류 큐에서 쓰기가 밀리지 않아야 한다 ─────────────
def test_write_jobs_jump_the_queue():
    """prod 는 상류가 단일 큐(Playwright 스레드 1개)라 순서가 곧 성패다. 읽기가 앞에 쌓여
    있으면 쓰기가 그만큼 늦어지고, 타임아웃까지 밀리면 사용자가 쓴 글이 그대로 사라진다."""
    import queue as _q
    from itertools import count

    from app.auth.base import PRIO_BACKGROUND, PRIO_USER, PRIO_WRITE

    assert PRIO_WRITE < PRIO_USER < PRIO_BACKGROUND      # 작은 값이 먼저

    jobs, seq = _q.PriorityQueue(), count()
    for prio, name in [(PRIO_BACKGROUND, "swr"), (PRIO_USER, "read1"),
                       (PRIO_USER, "read2"), (PRIO_WRITE, "comment")]:
        jobs.put((prio, next(seq), name))
    assert jobs.get()[2] == "comment"                    # 나중에 넣어도 맨 앞
    assert [jobs.get()[2] for _ in range(3)] == ["read1", "read2", "swr"]


def test_prio_scope_restores_outer_context():
    """중첩됐을 때 0 으로 되돌리면 바깥 문맥이 사라진다 — 쓰기 문맥 안에서 백그라운드 블록을
    잠깐 쓰면 나머지 쓰기 호출이 우선순위를 잃는다."""
    from app.auth.base import (PRIO_BACKGROUND, PRIO_USER, PRIO_WRITE,
                               background_upstream, upstream_priority, write_upstream)
    assert upstream_priority() == PRIO_USER
    with write_upstream():
        assert upstream_priority() == PRIO_WRITE
        with background_upstream():
            assert upstream_priority() == PRIO_BACKGROUND
        assert upstream_priority() == PRIO_WRITE          # 되돌아와야 한다
    assert upstream_priority() == PRIO_USER
