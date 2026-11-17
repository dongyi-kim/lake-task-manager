"""Single-instance detection and handoff for the desktop launcher."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
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


def _health_body(url, *, accept_unresponsive=False):
    """Read a small identity response, including a live worker's deliberate 503."""
    try:
        try:
            response = urllib.request.urlopen(url, timeout=1.5)
        except urllib.error.HTTPError as exc:
            if not accept_unresponsive or exc.code != 503:
                exc.close()
                return None
            response = exc
        with response:
            data = response.read(4097)
        if len(data) > 4096:
            return None
        body = json.loads(data or b"{}")
        return body if isinstance(body, dict) else None
    except Exception:
        return None


def ltm_health(base):
    """Identify the existing app independently of business capacity or recovery.

    The current live contract uses memory only. A recovering worker is still an
    existing LTM instance; starting another process cannot help it recover.
    Older releases retain the full-health identity fallback.
    """
    body = _health_body(base + "/api/health/live", accept_unresponsive=True)
    if body is not None:
        resources = body.get("resources")
        if (body.get("status") in ("ok", "unresponsive")
                and type(body.get("pid")) is int and body["pid"] > 0
                and isinstance(body.get("instance"), str)
                and isinstance(resources, dict)
                and type(resources.get("busy")) is bool and type(resources.get("supported")) is bool):
            return body
    body = _health_body(base + "/api/health")
    if body is None:
        return None
    if body.get("status") != "ok" or body.get("env") not in ("mock", "local", "prod"):
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
