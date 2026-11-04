"""Shared output types; validate contracts without rewriting existing wire payloads."""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, model_validator


class OpenModel(BaseModel):
    """Known fields are typed; upstream extension fields remain intact."""

    model_config = ConfigDict(extra="allow", strict=True)


class CacheMetadata(OpenModel):
    available: bool
    state: Literal["fresh", "stale", "dead", "unknown"]
    invalidated: bool | None = None
    sourceCount: int | None = None
    fetchedAt: float | None = None
    freshUntil: float | None = None
    displayUntil: float | None = None
    policyRevision: int | None = None


class RecoveryAction(OpenModel):
    tool: str
    arguments: dict[str, JsonValue] | None = None


class ToolError(OpenModel):
    # UI/shared production handlers use error/detail, while native MCP errors use
    # message. Describe all existing variants without manufacturing wire fields.
    model_config = ConfigDict(extra="allow", strict=True, json_schema_extra={
        "anyOf": [{"required": [name], "properties": {name: {"type": "string"}}}
                  for name in ("message", "error", "detail")],
    })

    code: str
    message: str | None = None
    error: str | None = None
    detail: str | None = None
    status: int | None = None
    retryable: bool | None = None
    needLogin: bool | None = None
    service: str | None = None
    nextAction: RecoveryAction | None = None
    clientMutationId: str | None = None
    repeatWrite: bool | None = None
    retryWithSameMutationId: bool | None = None
    outcomeUnknown: bool | None = None
    operationResult: JsonValue | None = Field(default=None, description=(
        "Original operation response if output validation fails. The operation may already have "
        "completed; inspect this result and never blindly repeat a write."))

    @model_validator(mode="after")
    def has_explanation(self):
        if not any(isinstance(getattr(self, name), str) for name in ("message", "error", "detail")):
            raise ValueError("An error must include message, error or detail text")
        return self


Data = TypeVar("Data")


class Success(OpenModel, Generic[Data]):
    model_config = ConfigDict(extra="forbid", strict=True)

    ok: Literal[True]
    data: Data
    cache: CacheMetadata | None = None
    partial: bool | None = None
    missing: int | None = None
    authentication: dict[str, ToolError] | None = None


class Failure(OpenModel, Generic[Data]):
    model_config = ConfigDict(extra="forbid", strict=True)

    ok: Literal[False]
    error: ToolError
    data: Data | None = None
    partial: bool | None = None
    authentication: dict[str, ToolError] | None = None


class ToolOutput(RootModel[Success[Data] | Failure[Data]], Generic[Data]):
    """Existing success/error/partial envelope, with tool-specific data."""

    model_config = ConfigDict(json_schema_extra={"type": "object"})


class ReadSuccess(Success[Data], Generic[Data]):
    cache: CacheMetadata
    partial: bool
    missing: int


class ReadToolOutput(RootModel[ReadSuccess[Data] | Failure[Data]], Generic[Data]):
    model_config = ConfigDict(json_schema_extra={"type": "object"})


def output_model(name, data_type, read_only=False):
    envelope = ReadToolOutput if read_only else ToolOutput
    return type(f"{name}Output", (envelope[data_type],), {"__module__": __name__})
