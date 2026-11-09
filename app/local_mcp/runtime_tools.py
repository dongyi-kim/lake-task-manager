"""Observe/request MCP recovery without touching Jira or authentication."""
from typing import Annotated
from pydantic import Field


HEALTH_TOOLS = {"get_mcp_health", "request_mcp_restart"}


def register_runtime_tools(register, runtime):
    def get_mcp_health() -> dict:
        """Read MCP worker state, generation and automatic recovery status; no Jira/auth requests.
        LTM supervises the transport in the background. A dead connection cannot call this tool;
        retry the connection after recovery or check the tray's status item.
        Upstream Jira failures and busy business calls do not themselves mean the MCP worker is dead.
        """
        return runtime.transport_host.health_snapshot()

    def request_mcp_restart(expected_generation: Annotated[int, Field(ge=1)]) -> dict:
        """Queue one MCP transport restart for the generation read from get_mcp_health.
        Duplicate requests coalesce; a stale generation cannot restart its replacement.
        Recovery starts after this response is delivered and the SDK request has finished.
        Business requests are never replayed; preserve clientMutationId on uncertain write outcomes.
        This does not update LTM, rotate tokens or clear Jira caches.
        """
        return runtime.transport_host.request_restart(expected_generation)

    register(get_mcp_health, read_only=True)
    register(request_mcp_restart, read_only=False)
