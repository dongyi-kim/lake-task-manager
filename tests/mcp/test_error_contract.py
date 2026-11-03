"""SDK callers can distinguish authentication, permission, conflict and invalid inputs."""
import anyio
import pytest
from fastapi import HTTPException
from mcp import Client

from app.auth.base import UpstreamUnavailable
from app.services.ticket_commands import CommandError


@pytest.mark.parametrize("exception_type", [CommandError, HTTPException])
@pytest.mark.parametrize("status,code", [
    (400, "invalid_request"), (401, "authentication_required"), (403, "permission_denied"),
    (404, "not_found"), (409, "conflict"), (422, "invalid_request"), (500, "internal_error"),
])
def test_sdk_error_contract_preserves_application_status(build_env, exception_type, status, code):
    env = build_env()
    def rejected_command() -> dict:
        if exception_type is CommandError:
            raise CommandError("fixture rejection", status)
        raise HTTPException(status, "fixture rejection")
    env.runtime.register(rejected_command, read_only=False)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("rejected_command", {})
            assert result.is_error and result.structured_content["ok"] is False
            error = result.structured_content["error"]
            assert error["code"] == code and error["status"] == status
            if status == 401:
                assert error["needLogin"] is True
            assert error["retryable"] is False
    anyio.run(scenario)


def test_unreachable_identity_is_not_reported_as_confirmed_logout(build_env):
    env = build_env()
    def unavailable_identity() -> dict:
        raise CommandError("Cannot verify current authority", 401) from UpstreamUnavailable("fixture offline")
    env.runtime.register(unavailable_identity, read_only=False)
    async def scenario():
        async with Client(env.runtime.server) as mcp:
            result = await mcp.call_tool("unavailable_identity", {})
            assert result.is_error
            error = result.structured_content["error"]
            assert error["code"] == "authorization_unavailable" and error["status"] == 401
            assert error["needLogin"] is False and error["retryable"] is True
    anyio.run(scenario)
