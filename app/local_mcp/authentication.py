"""Shared MCP authentication recovery; credentials and browser UI stay owned by LTM."""

import threading
import time
from typing import Literal

from fastapi import HTTPException
from app.auth.base import SessionExpired, UpstreamError, UpstreamUnavailable
from app.services.ticket_commands import CommandError


AUTH_TOOLS = frozenset({"get_auth_status", "authenticate"})
CONFIG_WRITES = frozenset({"create_wbs_task", "update_wbs_task", "delete_wbs_task",
                          "set_module_people", "rename_people_module", "delete_people_module"})


class AuthenticationUnavailable(RuntimeError):
    def __init__(self, state):
        self.state = state
        super().__init__(state["message"])


def authentication_error(exc):
    # UpstreamError inherits SessionExpired for historical reasons, including validation/5xx.
    if isinstance(exc, SessionExpired):
        return not isinstance(exc, UpstreamError) or exc.status == 401
    if isinstance(exc, (CommandError, HTTPException)):
        return (getattr(exc, "status", getattr(exc, "status_code", None)) == 401
                and not isinstance(exc.__cause__, UpstreamUnavailable))
    return False


def services_for(name, arguments):
    if name in AUTH_TOOLS:
        return ()
    if name in {"search_confluence_documents", "read_confluence_document"}:
        return ("confluence",)
    if name == "search_workspace":
        return tuple(dict.fromkeys(arguments.get("sources") or ("jira", "confluence")))
    return ("jira",)


def error_payload(state):
    login = state["needLogin"]
    result = {"code": "authentication_required" if login else "authentication_pending" if state["pending"]
              else "upstream_unavailable", "status": 401 if login else 503,
              "service": state["service"], "message": state["message"], "needLogin": login,
              "retryable": not login, "retryAfterSeconds": 2 if state["pending"] else 20}
    if login:
        result["nextAction"] = {"tool": "authenticate", "arguments": {
            "service": state["service"], "interactive": True}}
    return result


class Authentication:
    def __init__(self, runtime, request_login=None):
        self.runtime, self.request_login = runtime, request_login
        self._confluence_lock = threading.Lock()
        self._confluence_result = None
        self._confluence_at = 0.0
        self._confluence_generation = None

    @staticmethod
    def state(service, result):
        ready, login, pending = bool(result.get("ok")), bool(result.get("needLogin")), bool(result.get("pending"))
        mode = "authenticated" if ready else "login_required" if login else "checking" if pending else "unavailable"
        return {"service": service, "state": mode, "authenticated": ready, "needLogin": login,
                "pending": pending, "recovered": bool(result.get("recovered")),
                "message": "인증이 확인되었습니다." if ready else
                "LTM 로그인 화면에서 인증을 완료해 주세요." if login else
                "인증 확인 중입니다. 잠시 후 같은 요청으로 다시 확인해 주세요." if pending else
                "서비스 연결을 확인하지 못했습니다. 로그인 만료로 판정하지 않습니다."}

    def check(self, service="jira", *, force=False):
        client = self.runtime.client
        if client.env != "prod":
            return self.state(service, {"ok": True})
        if service == "jira":
            return self.state(service, client.proactive_auth_probe(force=force, wait=True))
        return self._confluence(force=force)

    def _confluence(self, *, force):
        client = self.runtime.client
        started = time.monotonic()
        if not self._confluence_lock.acquire(timeout=30):
            return self.state("confluence", {"pending": True})
        try:
            generation = client._provider_generation
            if (self._confluence_result is not None and generation == self._confluence_generation
                    and time.monotonic() - self._confluence_at < 20
                    and (not force or self._confluence_at >= started)):
                return dict(self._confluence_result)
            base = (self.runtime.settings.confluence_base or "").rstrip("/")
            result = {"ok": False}
            provider = None
            if base:
                try:
                    provider = client.provider
                    generation = client._provider_generation
                    # This shared client path already silently renews Confluence and retries once.
                    raw = client._conf_get_json(base + "/rest/api/user/current") or {}
                    anonymous = raw.get("type") == "anonymous" or str(raw.get("username", "")).lower() == "anonymous"
                    if anonymous:
                        # Some Confluence installations return anonymous identity with HTTP 200.
                        # That is the same expiry signal as 401, not a reason to skip silent SSO.
                        client._renew_service("Confluence")
                        raw = client._conf_get_json(base + "/rest/api/user/current") or {}
                    identity = raw.get("username") or raw.get("userKey") or raw.get("accountId")
                    result = {"ok": bool(identity) and str(identity).lower() != "anonymous"
                              and raw.get("type") != "anonymous"}
                    result["recovered"] = anonymous and result["ok"]
                    if str(identity).lower() == "anonymous" or raw.get("type") == "anonymous":
                        result["needLogin"] = True
                except SessionExpired as exc:
                    result = {"ok": False, "needLogin": authentication_error(exc)}
                except (UpstreamUnavailable, TimeoutError, ConnectionError):
                    result = {"ok": False}
            if generation != client._provider_generation or provider is not None and provider is not client._provider:
                # Another login replaced the provider while this probe was in flight.
                return self.state("confluence", {"pending": True})
            state = self.state("confluence", result)
            self._confluence_result, self._confluence_at = state, time.monotonic()
            self._confluence_generation = generation
            return dict(state)
        finally:
            self._confluence_lock.release()

    def authenticate(self, service, interactive):
        state = self.check(service, force=True)
        if state["needLogin"] and interactive:
            if self.request_login is None:
                state["message"] = "LTM 앱의 로그인 버튼에서 인증을 완료해 주세요."
            else:
                request = self.request_login(service)
                state.update(loginStarted=bool(request.get("started")),
                             pending=bool(request.get("pending")))
                if state["pending"]:
                    state["state"] = "login_in_progress"
                    state["nextAction"] = {"tool": "get_auth_status", "arguments": {"service": service}}
        elif state["needLogin"]:
            state["nextAction"] = error_payload(state)["nextAction"]
        return state


def register_authentication(register, runtime):
    def get_auth_status(service: Literal["jira", "confluence"] = "jira") -> dict:
        """Check authentication and silently renew when possible. No login window is opened.
        service selects jira/confluence independently. Returns authenticated, state, needLogin,
        pending and message. Poll after interactive authentication; only authenticated=true confirms
        the original tool can resume. An unavailable/pending result is not proof of logout or zero data.
        """
        return runtime.authentication.check(service)

    def authenticate(service: Literal["jira", "confluence"] = "jira", interactive: bool = False) -> dict:
        """Refresh LTM's authentication. interactive=true starts one shared LTM login flow only if
        silent recovery fails. Ask the user to complete that window, then poll get_auth_status and
        resume the original call with the SAME mutation ID and arguments. Never request cookies.
        Returns authentication state, pending/loginStarted and nextAction when applicable;
        a started login is not a completed login. Network failures do not automatically open a window.
        """
        return runtime.authentication.authenticate(service, interactive)

    register(get_auth_status, read_only=True)
    register(authenticate, read_only=False)
