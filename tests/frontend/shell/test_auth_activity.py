"""유휴 복귀 인증 확인의 프론트 계약.

실제 Jira 확인은 backend 단위 테스트가 검증하고, 여기서는 활동 이벤트가 렌더링 경로를
await하지 않으며 중복 호출과 local/mock 오동작을 만들지 않는 배선을 고정한다.
"""

from support.paths import STATIC_ROOT


def _source(relative):
    return (STATIC_ROOT / relative).read_text(encoding="utf-8")


def test_auth_activity_probe_is_idle_gated_single_flight_and_disposable():
    source = _source("lib/authActivity.js")
    assert "AUTH_IDLE_MS = 5 * 60 * 1000" in source
    assert "AUTH_COOLDOWN_MS = 60 * 1000" in source
    assert "if (inFlight || at - lastProbeAt < cooldownMs)" in source
    assert 'win.addEventListener("focus", activity' in source
    assert 'doc.addEventListener("visibilitychange", visible' in source
    assert 'doc.addEventListener("pointerdown", activity' in source
    assert 'doc.addEventListener("keydown", activity' in source
    assert 'if (result.recovered) win.dispatchEvent(new CustomEvent("auth-ok"))' in source
    assert 'else if (result.needLogin) win.dispatchEvent(new CustomEvent("need-login"))' in source
    assert 'win.removeEventListener("focus", activity)' in source


def test_root_installs_probe_and_api_uses_non_memoized_post():
    root = _source("components/app-root.js")
    api = _source("lib/api.js")
    assert 'import { installAuthActivityProbe } from "../lib/authActivity.js"' in root
    assert "this._stopAuthActivity = installAuthActivityProbe(api)" in root
    assert "if (this._stopAuthActivity) this._stopAuthActivity()" in root
    assert 'authProbe: () => req("/api/auth/probe", { method: "POST"' in api
