"""Single-instance detection and handoff for the desktop launcher."""

from __future__ import annotations

import json
import socket
import time
import urllib.request


def wait_port_free(port, timeout=12):
    """Wait briefly for the previous instance to release its configured port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.4)
            try:
                listening = sock.connect_ex(("127.0.0.1", int(port))) == 0
            except Exception:
                listening = False
        if not listening:
            return True
        time.sleep(0.3)
    return False


def port_is_listening(port):
    """Return whether the configured local TCP port currently has a listener."""
    with socket.socket() as sock:
        sock.settimeout(0.4)
        try:
            return sock.connect_ex(("127.0.0.1", int(port))) == 0
        except Exception:
            return False


def ltm_health(base):
    """Identify LTM by its health contract instead of trusting any server on the port."""
    try:
        body = json.loads(urllib.request.urlopen(base + "/api/health", timeout=1.5).read() or b"{}")
    except Exception:
        return None
    if body.get("status") != "ok" or body.get("env") not in {"mock", "local", "prod"}:
        return None
    if "projectKey" not in body or "rev" not in body:
        return None
    return body


def disk_rev():
    """Return the release tag or code revision currently present on disk."""
    from app.infra.version import code_rev

    return code_rev()


def running_rev(base):
    """Return the revision captured by the already-running instance."""
    try:
        body = urllib.request.urlopen(base + "/api/app/rev", timeout=2).read()
        return ((json.loads(body) or {}).get("rev") or "").strip()
    except Exception:
        return ""


def quit_existing(base, port):
    """Ask an older tray instance to stop cleanly, then wait for its port."""
    try:
        request = urllib.request.Request(base + "/api/app/quit", data=b"", method="POST")
        body = urllib.request.urlopen(request, timeout=8).read()
        action = (json.loads(body or b"{}") or {}).get("action")
    except Exception:
        action = None
    if action != "quit":
        return False
    return wait_port_free(port)


def signal_existing_instance(settings):
    """Focus the current instance, or replace it when its code revision is stale."""
    port = settings.app_port
    base = f"http://127.0.0.1:{port}"
    if not ltm_health(base):
        return False

    disk, running = disk_rev(), running_rev(base)
    if disk and running and disk != running:
        print(f"실행 중인 앱이 옛 버전입니다(실행 {running} ≠ 최신 {disk}) — 종료 후 최신으로 다시 시작합니다.")
        if quit_existing(base, port):
            return False
        print("(옛 인스턴스를 자동 종료할 수 없어 기존 창을 사용합니다.)")

    action = None
    try:
        request = urllib.request.Request(base + "/api/app/open", data=b"", method="POST")
        body = urllib.request.urlopen(request, timeout=8).read()
        if body:
            action = (json.loads(body) or {}).get("action")
    except Exception:
        pass
    if action == "focus":
        print("Lake Task Manager 가 이미 실행 중입니다 — 기존 창을 앞으로 가져왔습니다.")
    elif action == "open":
        print("Lake Task Manager 백엔드가 실행 중입니다 — 새 앱 창을 엽니다.")
    else:
        print(f"Lake Task Manager 가 이미 실행 중입니다: {base}")
    return True
