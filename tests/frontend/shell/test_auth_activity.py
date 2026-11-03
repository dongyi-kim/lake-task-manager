"""유휴 복귀 인증 확인의 프론트 계약.

실제 Jira 확인은 backend 단위 테스트가 검증하고, 여기서는 활동 이벤트가 렌더링 경로를
await하지 않으며 중복 호출과 local/mock 오동작을 만들지 않는 배선을 고정한다.
"""

import json
import shutil
import subprocess

import pytest

from support.paths import STATIC_ROOT


def _source(relative):
    return (STATIC_ROOT / relative).read_text(encoding="utf-8")


def test_auth_activity_continuous_use_resume_offline_and_disposal():
    """Execute the actual event handlers with a fake clock, not source-string contracts."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the frontend event behavior test")
    script = "const source = " + json.dumps(_source("lib/authActivity.js")) + ";\n" + r"""
    const {installAuthActivityProbe} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
    const assert = (value, message) => { if (!value) throw new Error(message); };
    globalThis.CustomEvent = class extends Event { constructor(name) { super(name); } };
    const win = new EventTarget(), doc = new EventTarget();
    win.navigator = {onLine: true}; doc.visibilityState = 'visible';
    let clock = 0, calls = 0, finish, events = 0;
    win.addEventListener('auth-ok', () => events++);
    win.addEventListener('need-login', () => events++);
    const api = {authProbe: () => { calls++; return new Promise(resolve => { finish = resolve; }); }};
    const tick = async () => { for (let n = 0; n < 6; n++) await Promise.resolve(); };
    const settle = async (result = {ok:true}) => { finish(result); await tick(); };
    const stop = installAuthActivityProbe(api, {window:win, document:doc, now:()=>clock});
    await tick(); assert(calls === 1, 'initial visibility must check');
    win.dispatchEvent(new Event('focus')); doc.dispatchEvent(new Event('keydown'));
    await tick(); assert(calls === 1, 'focus and activity must share one request'); await settle();
    for (let n = 1; n <= 60; n++) {
      clock += 30000; doc.dispatchEvent(new Event('keydown')); await tick();
      if (n % 8 === 0) await settle();
    }
    assert(calls === 8, 'continuous typing for 30 minutes must probe every four minutes');
    doc.visibilityState = 'hidden'; clock += 240000;
    win.dispatchEvent(new Event('focus')); await tick(); assert(calls === 8, 'hidden tab must not probe');
    doc.visibilityState = 'visible'; doc.dispatchEvent(new Event('visibilitychange'));
    await tick(); assert(calls === 9, 'resume must probe'); await settle({recovered:true});
    assert(events === 1, 'recovery must notify existing UI');
    win.navigator.onLine = false; clock += 240000;
    doc.dispatchEvent(new Event('pointerdown')); await tick(); assert(calls === 9, 'offline must not request');
    win.navigator.onLine = true; win.dispatchEvent(new Event('online'));
    await tick(); assert(calls === 10, 'online restoration must check');
    stop(); await settle({needLogin:true});
    clock += 240000; win.dispatchEvent(new Event('focus')); await tick();
    assert(calls === 10 && events === 1, 'disposed requests must not start login or retain listeners');
    """
    result = subprocess.run([node, "--input-type=module"], input=script, text=True,
                            capture_output=True, timeout=15, encoding="utf-8")
    assert result.returncode == 0, result.stderr


def test_root_installs_probe_and_api_uses_non_memoized_post():
    root = _source("components/app-root.js")
    api = _source("lib/api.js")
    assert 'import { installAuthActivityProbe } from "../lib/authActivity.js"' in root
    assert "this._stopAuthActivity = installAuthActivityProbe(api)" in root
    assert "if (this._stopAuthActivity) this._stopAuthActivity()" in root
    assert 'authProbe: () => req("/api/auth/probe", { method: "POST"' in api
