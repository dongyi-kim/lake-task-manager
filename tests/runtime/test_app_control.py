"""앱 창 제어 브리지(단일 인스턴스 실행) 회귀 테스트.

run.py 의 3분기 동작(꺼짐→기동 / 창 있음→포커스 / 백엔드만→새 창)은 백엔드의
request_focus_or_open() 판정에 달려 있다. 그 판정과 focus 소비 규약을 고정한다.
"""
import app.main as m
from support.paths import REPO_ROOT


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


def test_tray_sso_refresh_never_waits_for_network_in_menu_callback():
    """트레이 메뉴 콜백에서 probe_sso를 직접 부르면 종료 메뉴까지 같이 얼어붙는다."""
    src = (REPO_ROOT / "run.py").read_text(encoding="utf-8")
    assert "def probe_sso_async(icon=None):" in src
    assert 'lambda icon, item: probe_sso_async(icon)' in src
    assert 'lambda icon, item: (probe_sso(), icon.update_menu())' not in src


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
    """배포는 릴리즈 태그 단위 — '몇 커밋 뒤처짐(behind)' 이 아니라 current/latest 를 준다."""
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    body = c.get("/api/update").json()
    for k in ("available", "current", "latest", "pinned", "ok", "checkedAt"):
        assert k in body
    assert isinstance(body["available"], bool)
    assert "behind" not in body        # 옛 화면이 이걸 읽고 '0개 업데이트' 를 띄우지 않게


def test_update_check_pinned_never_nags():
    r"""config\lake-task-manager.rev 로 버전을 고정한 PC 는 알림을 받으면 안 된다.
    (고정한 사람에게 매번 뜨는 알림은 거짓 알림이고, 끌 방법이 없다.)"""
    import os
    import tempfile
    from app.infra.update_check import UpdateChecker

    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "config"))
        with open(os.path.join(d, "config", "lake-task-manager.rev"), "w") as f:
            f.write("v2026.01.01\n")
        u = UpdateChecker(d)
        u._refresh()                      # 네트워크를 타지 않는다(고정이면 바로 결론)
        st = u.get()
        assert st["pinned"] == "v2026.01.01"
        assert st["available"] is False and st["ok"] is True


def test_update_check_latest_means_not_pinned():
    """'latest' 나 빈 값은 고정이 아니다 — 그때만 최신 릴리즈를 확인한다."""
    import os
    import tempfile
    from app.infra.version import pinned_rev

    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "config"))
        p = os.path.join(d, "config", "lake-task-manager.rev")
        for val, want in (("latest", ""), ("LATEST\n", ""), ("", ""),
                          ("v2026.08.03", "v2026.08.03")):
            with open(p, "w") as f:
                f.write(val)
            assert pinned_rev(d) == want, val
    assert pinned_rev(os.path.join(d, "없는폴더")) == ""      # 파일 없음 → 고정 아님


def test_update_check_uses_explicit_public_tls_context(monkeypatch):
    """GitHub 조회가 Windows 사용자 인증서 저장소를 암묵적으로 열면 안 된다."""
    import app.infra.update_check as update_check

    sentinel = object()
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def geturl(self):
            return "https://github.com/dongyi-kim/lake-task-manager/releases/tag/v2026.08.16"

    def fake_urlopen(request, *, timeout, context):
        seen.append((request.full_url, timeout, context))
        return Response()

    monkeypatch.setattr(update_check, "public_ssl_context", lambda: sentinel)
    monkeypatch.setattr(update_check.urllib.request, "urlopen", fake_urlopen)

    assert update_check.latest_tag(timeout=3) == "v2026.08.16"
    assert len(seen) == 1
    assert seen[0][1:] == (3, sentinel)


def test_public_tls_context_loads_only_the_file_ca_bundle(monkeypatch):
    """Explicit cafile keeps create_default_context from loading native roots."""
    import app.infra.public_tls as public_tls

    sentinel = object()
    seen = []

    def fake_context(*, cafile):
        seen.append(cafile)
        return sentinel

    public_tls.public_ssl_context.cache_clear()
    monkeypatch.setattr(public_tls.ssl, "create_default_context", fake_context)
    monkeypatch.setattr(public_tls, "public_ca_bundle", lambda: "project-ca.pem")
    try:
        assert public_tls.public_ssl_context() is sentinel
        assert seen == ["project-ca.pem"]
    finally:
        public_tls.public_ssl_context.cache_clear()


def test_assets_endpoint_lists_module_graph():
    """자가복구 새로고침(index.html 감시자)이 캐시를 갈아끼울 대상 목록.

    **진입점 하나로는 부족하다** — app.js 만 새로 받아도 그게 import 하는 모듈들이 옛 캐시면
    똑같이 흰 화면이다. 그래서 js/css 를 전부 준다. 여기가 비면 안전장치가 무력해진다."""
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    assets = c.get("/api/app/assets").json()["assets"]
    assert "/app.js" in assets
    assert "/components/app-root.js" in assets
    assert any(a.startswith("/vendor/") and a.endswith(".js") for a in assets)   # Vue 자체도 대상
    assert any(a.endswith(".css") for a in assets)
    assert all(a.startswith("/") and not a.endswith(".html") for a in assets)


def test_update_check_dev_session_never_nags():
    r"""LAKE_REV 로 띄운 개발 세션(bin\test_run.bat)도 알림을 띄우면 안 된다.

    미릴리즈 ref 는 current('main'·SHA) != latest(태그) 라 늘 '업데이트 있음' 이 되는데,
    눌러 재시작해도 그 세션은 환경변수를 물려받아 같은 ref 로 다시 뜬다 → 배지가 영영
    안 사라진다. 고정(pinned)과 같은 취급으로 막는다."""
    import os
    import tempfile
    from app.infra.update_check import UpdateChecker

    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "config"))          # 고정 파일은 없다(= latest 채널)
        old = os.environ.get("LAKE_REV")
        os.environ["LAKE_REV"] = "main"
        try:
            u = UpdateChecker(d)
            u._refresh()                                 # 네트워크를 타지 않는다
            st = u.get()
            assert st["pinned"] == "main"
            assert st["available"] is False and st["ok"] is True
        finally:
            if old is None:
                os.environ.pop("LAKE_REV", None)
            else:
                os.environ["LAKE_REV"] = old
