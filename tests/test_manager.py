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
