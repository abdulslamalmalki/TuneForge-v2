"""Explicit provider registry; registration never selects or invokes a provider."""

import re

from app.llm.providers.base import ModelProvider

_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,49}$")


class ProviderRegistryError(ValueError):
    pass


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    def register(self, provider: ModelProvider) -> None:
        if not isinstance(provider, ModelProvider):
            raise ProviderRegistryError("Provider does not implement the model-provider protocol.")
        provider_id = provider.provider_id
        if not _PROVIDER_ID_RE.fullmatch(provider_id):
            raise ProviderRegistryError(
                "Provider ID must be a lowercase stable identifier of at most 50 characters."
            )
        if provider_id in self._providers:
            raise ProviderRegistryError(f"Provider is already registered: {provider_id}")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> ModelProvider:
        try:
            return self._providers[provider_id]
        except KeyError:
            raise ProviderRegistryError(f"Provider is not registered: {provider_id}") from None

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))
