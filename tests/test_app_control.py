"""앱 창 제어 브리지(단일 인스턴스 실행) 회귀 테스트.

run.py 의 3분기 동작(꺼짐→기동 / 창 있음→포커스 / 백엔드만→새 창)은 백엔드의
request_focus_or_open() 판정에 달려 있다. 그 판정과 focus 소비 규약을 고정한다.
"""
import app.main as m


def _reset():
    m._app_ctrl["open_hook"] = None
    m._app_ctrl["restart_hook"] = None
    m._app_ctrl["live"] = 0
    m._app_ctrl["focus"].clear()


def test_no_hook_no_window_returns_none():
    _reset()
    assert m.request_focus_or_open() == {"action": "none"}


def test_hook_opens_when_no_live_window():
    _reset()
    calls = []
    m.set_open_window_hook(lambda: calls.append(1))
    assert m.request_focus_or_open() == {"action": "open"}
    assert calls == [1]
    _reset()


def test_focus_when_window_live_and_hook_not_called():
    _reset()
    calls = []
    m.set_open_window_hook(lambda: calls.append(1))
    m.note_window_opened()
    assert m.request_focus_or_open() == {"action": "focus"}
    assert calls == []                       # 창이 있으면 새로 열지 않는다
    # focus 요청은 창 루프가 한 번만 소비한다
    assert m.consume_focus_request() is True
    assert m.consume_focus_request() is False
    m.note_window_closed()
    _reset()


def test_counts_never_go_negative():
    _reset()
    m.note_window_closed()                   # 과다 감소 방어
    assert m.live_window_count() == 0
    _reset()


def test_open_endpoint(client=None):
    _reset()
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    assert c.post("/api/app/open").json() == {"action": "none"}
    _reset()


def test_restart_hook():
    _reset()
    assert m.request_restart() == {"action": "none"}     # 훅 없으면 스스로 재시작 못 함
    calls = []
    m.set_restart_hook(lambda: calls.append(1))
    assert m.request_restart() == {"action": "restart"}
    assert calls == [1]
    _reset()


def test_update_endpoint_shape():
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    body = c.get("/api/update").json()
    for k in ("available", "behind", "current", "ok", "checkedAt"):
        assert k in body
    assert isinstance(body["available"], bool)
