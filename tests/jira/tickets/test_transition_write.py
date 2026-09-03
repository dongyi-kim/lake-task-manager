"""상태 전이가 **실제로 돈다**.

prod 에서 'name components is not defined' 로 죽은 적이 있다 — create_child 용 코드가 같은
두 줄을 공유하는 do_transition 에도 들어갔다. 쓰기 경로는 화면으로만 확인하면 이런 게 남는다.
"""
import pytest


@pytest.fixture()
def client():
    from app.main import _client
    return _client


def _first_transition(client, key):
    ts = client.transitions(key) or []
    assert ts, f"{key} 에 가능한 전이가 없다"
    return ts[0]


def test_transition_runs(client):
    key = "DL-9011"                       # Open 상태 픽스처
    # Exercise the fieldless path; Done is intentionally sorted first but requires the full
    # completion form and is covered by ``test_transition_with_screen_fields`` below.
    t = next(item for item in (client.transitions(key) or [])
             if not (item.get("fields") or {}).get("fields"))
    client.do_transition(key, t["id"])
    b = client.ticket_badge(key)
    assert b and b.get("status")


def test_transition_with_screen_fields(client):
    """전이 화면 입력(담당자·코멘트·해결책·시간)이 함께 가도 죽지 않는다."""
    key = "DL-9012"
    t = _first_transition(client, key)
    client.do_transition(key, t["id"], assignee="test.ui01",
                         comment="자동 검증", time_spent="1h", resolution="Done")
    b = client.ticket_badge(key)
    assert b and b.get("status")
