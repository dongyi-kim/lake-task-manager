"""Small native status dialog; callers run it outside the tray message pump."""
import sys


def app_responding(port):
    """Check this app's event loop with the existing identity-fenced local probe."""
    import os
    from types import SimpleNamespace
    from app.launcher.supervisor import WORKER_ENV, probe_worker
    owner = SimpleNamespace(owns_pid=lambda pid: pid == os.getpid())
    return probe_worker(f"http://127.0.0.1:{port}", owner,
        os.environ.get(WORKER_ENV, ""), timeout=1)


_STATES = {"healthy": "정상", "starting": "시작 중", "recovering": "복구 중",
    "unresponsive": "응답 없음", "stopped": "중지됨", "recovery_failed": "복구 실패"}
_REASONS = {"worker_exited": "워커 종료", "heartbeat_timeout": "MCP 응답 없음",
    "manual_request": "재기동 요청", "startup_failed": "MCP 시작 실패",
    "recovery_limit": "자동 복구 횟수 제한", "shutdown_timeout": "이전 워커 종료 대기 초과"}


def format_health_status(snapshot):
    app, mcp = snapshot.get("app") or {}, snapshot.get("mcp") or {}
    lines = ["앱: " + _STATES.get(app.get("state"), "미확인"),
        "MCP: " + _STATES.get(mcp.get("state"), "미확인")]
    if app.get("port") is not None:
        lines.append(f"연결 포트: {app['port']}")
    enabled = mcp.get("automaticRecovery")
    lines.append("자동 복구: " + ("켜짐" if enabled is True else "꺼짐" if enabled is False else "미확인"))
    if mcp.get("generation") is not None:
        lines.append(f"MCP 세대: {mcp['generation']}")
    if mcp.get("restartCount") is not None:
        lines.append(f"재기동: {mcp['restartCount']}회")
    if mcp.get("lastRecoveryReason"):
        lines.append("최근 복구 사유: " + _REASONS.get(mcp["lastRecoveryReason"], "미확인"))
    if mcp.get("state") == "recovery_failed":
        lines.append("자동 복구를 마치지 못했습니다. 실행 로그를 확인해 주세요.")
    return "\n".join(lines)


def show_health_status(snapshot):
    message = format_health_status(snapshot)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Lake Task Manager 상태 확인", 0x40 | 0x10000)
    else:
        print(message, file=sys.stderr)
