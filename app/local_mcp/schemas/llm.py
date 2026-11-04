"""Typed connection inputs and secret-free output contracts for local LLM settings."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .common import JsonValue

Provider = Literal["aoai", "openai", "openai_compat", "fake"]


class ConnectionSecrets(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    aoaiEndpoint: str | None = None
    aoaiApiKey: SecretStr | None = None
    openaiApiKey: SecretStr | None = None
    compatBaseUrl: str | None = None
    compatApiKey: SecretStr | None = None
    compatHeaders: SecretStr | None = Field(default=None, description="Write-only JSON object of string headers.")
    simpleBaseUrl: str | None = None
    simpleApiKey: SecretStr | None = None
    simpleHeaders: SecretStr | None = None
    embeddingBaseUrl: str | None = None
    embeddingApiKey: SecretStr | None = None
    embeddingHeaders: SecretStr | None = None

class LlmConfigChanges(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str | None = None
    chatModel: str | None = None
    chatModelSimple: str | None = None
    embedModel: str | None = None
    apiVersion: str | None = None
    chatModelProfile: str | None = None
    chatModelSimpleProfile: str | None = None
    embeddingProvider: Literal["", "aoai", "openai", "openai_compat", "fake"] | None = None
    embeddingApiVersion: str | None = None
    embedRevision: str | None = None
    embedPrecision: str | None = None
    embedDimension: str | None = None
    embedNormalization: str | None = None
    secrets: ConnectionSecrets | None = Field(default=None, description=(
        "Only supplied fields change. Empty string clears a stored value; omit to preserve. "
        "Keys and headers are write-only; URLs use the same fields as the LTM settings UI."))


class LlmConfig(LlmConfigChanges):
    id: str
    name: str
    provider: Provider
    active: bool
    authOk: bool
    modelsOk: bool
    verified: bool
    secrets: dict[str, str] = Field(description=(
        "Base URLs plus configured/empty indicators for keys and headers. No key suffixes or secret values."))


class LlmPage(BaseModel):
    startAt: int
    returned: int
    total: int
    hasMore: bool
    nextStartAt: int | None


class LlmSettings(LlmPage):
    configs: list[LlmConfig]
    activeConfigId: str
    runtimeConfigSource: Literal["none", "named", "environment"]
    envSupplied: bool
    envOverrides: dict[str, str]


class LlmModels(LlmPage):
    kind: Literal["chat", "simple", "embed"]
    models: list[str]
    partial: bool
    diagnostics: dict[str, JsonValue]


class LlmCheck(BaseModel):
    stage: Literal["auth", "models"]
    passed: bool
    config: LlmConfig
    result: dict[str, JsonValue] = Field(description=(
        "Redacted diagnostics; oversized results contain truncated, originalBytes and preview. "
        "The outer passed field is the check outcome even when diagnostics are shortened."))


class LlmDeleted(BaseModel):
    id: str
    deleted: Literal[True]


DATA_MODELS = {"get_llm_settings": LlmSettings, "create_llm_config": LlmConfig,
               "update_llm_config": LlmConfig, "delete_llm_config": LlmDeleted,
               "list_llm_models": LlmModels, "check_llm_connection": LlmCheck,
               "activate_llm_config": LlmConfig}
