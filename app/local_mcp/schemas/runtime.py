"""Explicit local MCP lifecycle contracts, independent of business-system state."""
from typing import Literal
from pydantic import Field
from app.local_mcp.schemas.common import OpenModel


class McpHealth(OpenModel):
    state: Literal["starting", "healthy", "recovering", "unresponsive", "stopped", "recovery_failed"]
    generation: int = Field(ge=0)
    workerAlive: bool
    automaticRecovery: bool
    drainingWork: bool = Field(default=False, description="Recovery is waiting for already admitted work to finish; requests are never replayed.")
    consecutiveFailures: int = Field(ge=0)
    restartCount: int = Field(ge=0)
    restartLimit: int = Field(ge=0)
    lastRecoveryReason: str | None
    lastHeartbeatAgeSeconds: float | None


class McpRestartRequest(OpenModel):
    accepted: bool
    status: Literal["queued", "already_queued", "generation_changed", "not_running", "recovery_failed"]
    generation: int = Field(ge=0)


DATA_MODELS = {"get_mcp_health": McpHealth, "request_mcp_restart": McpRestartRequest}
