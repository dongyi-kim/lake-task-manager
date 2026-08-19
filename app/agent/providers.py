"""Provider adapters for chat and embedding model definitions.

Adapters receive already-resolved parameters. They never inspect Role ids or Qwen model names.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelDefinition:
    provider: str
    model: str
    base_url: str = ""
    api_key: str = ""
    api_version: str = ""
    headers: dict = field(default_factory=dict)
    model_profile: str = ""

    def debug(self) -> dict:
        return {"provider": self.provider, "model": self.model, "baseUrl": self.base_url,
                "apiVersion": self.api_version, "headerNames": sorted(self.headers),
                "modelProfile": self.model_profile, "hasApiKey": bool(self.api_key)}


class ProviderAdapter:
    name = ""

    def chat(self, definition: ModelDefinition, parameters: dict):
        raise NotImplementedError

    def embeddings(self, definition: ModelDefinition, parameters: dict):
        raise NotImplementedError


class OpenAIProvider(ProviderAdapter):
    name = "openai"

    def chat(self, definition: ModelDefinition, parameters: dict):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(api_key=definition.api_key, model=definition.model, **parameters)

    def embeddings(self, definition: ModelDefinition, parameters: dict):
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(api_key=definition.api_key, model=definition.model, **parameters)


class OpenAICompatibleProvider(ProviderAdapter):
    name = "openai_compat"

    def chat(self, definition: ModelDefinition, parameters: dict):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(api_key=definition.api_key or "unused", base_url=definition.base_url,
                          model=definition.model, default_headers=definition.headers or None,
                          **parameters)

    def embeddings(self, definition: ModelDefinition, parameters: dict):
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(api_key=definition.api_key or "unused", base_url=definition.base_url,
                                model=definition.model, default_headers=definition.headers or None,
                                **parameters)


class AzureOpenAIProvider(ProviderAdapter):
    name = "aoai"

    def chat(self, definition: ModelDefinition, parameters: dict):
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(azure_endpoint=definition.base_url, api_key=definition.api_key,
                               azure_deployment=definition.model,
                               api_version=definition.api_version, **parameters)

    def embeddings(self, definition: ModelDefinition, parameters: dict):
        from langchain_openai import AzureOpenAIEmbeddings
        return AzureOpenAIEmbeddings(azure_endpoint=definition.base_url,
                                     api_key=definition.api_key,
                                     model=definition.model,
                                     openai_api_version=definition.api_version, **parameters)


_ADAPTERS = {x.name: x for x in (OpenAIProvider(), OpenAICompatibleProvider(),
                                  AzureOpenAIProvider())}


def adapter(name: str) -> ProviderAdapter:
    try:
        return _ADAPTERS[str(name)]
    except KeyError as exc:
        raise ValueError(f"지원하지 않는 provider adapter: {name}") from exc


__all__ = ["ModelDefinition", "ProviderAdapter", "OpenAIProvider",
           "OpenAICompatibleProvider", "AzureOpenAIProvider", "adapter"]
