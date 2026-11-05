"""매니저 화이트리스트 — 권한 규칙이라 회귀하면 조용히 화면이 사라지거나 열린다."""
from types import SimpleNamespace

from app.infra.settings import is_manager


def _s(managers):
    return SimpleNamespace(managers=managers)


def test_legacy_manager_setting_does_not_restrict_local_operator():
    for user in (None, "", "worker.park", {"id": "worker.park"}):
        assert is_manager(_s(["pm.kim"]), user) is True


def test_local_feature_gates_do_not_fetch_jira_identity(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m._client, "current_user", lambda: (_ for _ in ()).throw(AssertionError("unexpected /me")))
    monkeypatch.setattr(m, "load_people", lambda: {"One": ["u1"], "Two": ["u2"]})
    assert m._is_manager() and m._may_edit("DL-1")
    assert m._my_modules_or_all() == (False, None)
    assert m._scoped_people() == {"One": ["u1"], "Two": ["u2"]}
    m._require_manager()
    m._require_module_access("Two")
    m._require_person_access("u2")
    m._require_edit("DL-1")


def test_current_user_does_not_cache_failure():
    """실패한 /myself 를 캐시하면, 로그인에 성공해도 TTL 동안 '세션 없음' 이 남아
    매니저 판정이 계속 False 가 된다(prod 에서 겪은 '새로고침해도 안 풀리는 인증 오류')."""
    from app.infra.cache import Cache
    from app.jira.jira_client import JiraClient

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


# ── 2중 TTL 캐시 — 오프라인/미인증에서 화면을 지키는 지점 ──────────────────
def test_cache_serves_stale_when_upstream_fails():
    """outdated 를 넘겨도 dead 안이면 낡은 값으로 버틴다. 예전엔 producer 예외가 그대로
    올라가 화면이 통째로 비었는데, 정작 같은 데이터가 캐시에 있었다."""
    import time as _t

    from app.infra.cache import Cache
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

    from app.infra.cache import Cache
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

    from app.infra.cache import Cache
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

    from app.infra.cache import Cache
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
    from app.infra.cache import Cache
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
    from app.infra.cache import Cache
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
