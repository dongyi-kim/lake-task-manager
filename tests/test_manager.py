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


def test_dict_uses_id_not_display_name():
    """current_user() 는 {id: 사번, name: 표시이름} 이다. name 을 먼저 보면 표시이름과
    사번을 비교하게 된다(실제로 겪은 버그)."""
    st = _s(["test.ui01"])
    assert is_manager(st, {"id": "test.ui01", "name": "UI픽스처01"}) is True
    assert is_manager(st, {"id": "other", "name": "test.ui01"}) is False


def test_raw_jira_myself_falls_back_to_name():
    """Jira 원본 /myself 는 id 가 없고 name 이 곧 사번이다."""
    assert is_manager(_s(["pm.kim"]), {"name": "pm.kim", "displayName": "김PM"}) is True


def test_missing_user_is_not_manager():
    assert is_manager(_s(["pm.kim"]), None) is False
    assert is_manager(_s(["pm.kim"]), "") is False
