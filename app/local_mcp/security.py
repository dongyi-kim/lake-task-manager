"""Local socket, browser-origin, and bearer-token boundaries for MCP and its settings."""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import secrets
import threading
from pathlib import Path
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


class Credentials:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data = None

    def _load(self):
        if self._data is None:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data.get("token"), str) or len(data["token"]) < 32:
                    raise ValueError("MCP 인증 파일을 읽을 수 없습니다.")
                self._data = data
            else:
                self._save({"enabled": True, "token": secrets.token_urlsafe(32)})
        return self._data

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(data, output)
            temp.replace(self.path)
            self.path.chmod(0o600)
        finally:
            temp.unlink(missing_ok=True)
        self._data = data

    @property
    def enabled(self):
        with self._lock:
            return self._load().get("enabled") is True

    def token(self):
        with self._lock:
            return self._load()["token"]

    def accepts(self, authorization):
        with self._lock:
            return self.enabled and hmac.compare_digest((authorization or "").encode(), ("Bearer " + self.token()).encode())

    def configure(self, enabled):
        with self._lock:
            self._save({**self._load(), "enabled": bool(enabled)})

    def rotate(self):
        with self._lock:
            self._save({**self._load(), "token": secrets.token_urlsafe(32)})


def local_request_error(scope, port):
    """Trust the ASGI peer only. The launcher disables proxy-header rewriting."""
    peer = (scope.get("client") or ("", 0))[0]
    try:
        address = ipaddress.ip_address(peer)
        if not (address.is_loopback or getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback):
            return "로컬 컴퓨터에서만 MCP에 접근할 수 있습니다."
    except ValueError:
        return "로컬 연결을 확인할 수 없습니다."
    headers = Headers(scope=scope)
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    if headers.get("host", "").lower() not in allowed:
        return "MCP Host가 실행 중인 LTM 주소와 다릅니다."
    origin = headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return "Origin이 올바르지 않습니다."
        if parsed.scheme != "http" or parsed.netloc.lower() not in allowed or parsed.path or parsed.query or parsed.fragment:
            return "다른 웹사이트에서 MCP에 접근할 수 없습니다."
    if headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        return "다른 웹사이트에서 MCP에 접근할 수 없습니다."
    return ""


class LocalGuard:
    def __init__(self, app, *, credentials, port):
        self.app, self.credentials, self.port = app, credentials, port

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        error = local_request_error(scope, self.port)
        status = 403
        if not error and not self.credentials.enabled:
            error, status = "MCP 연결이 꺼져 있습니다.", 503
        if not error and not self.credentials.accepts(Headers(scope=scope).get("authorization")):
            error, status = "MCP 인증 토큰이 필요하거나 만료되었습니다.", 401
        if error:
            response = JSONResponse({"error": error}, status_code=status,
                                    headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)
