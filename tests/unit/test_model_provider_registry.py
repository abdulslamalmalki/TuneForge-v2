"""Provider registry and fake-adapter contract tests."""

import pytest

from app.llm.models import ModelRequest, ModelResponse
from app.llm.providers.base import ModelProvider
from app.llm.providers.fake import FakeModelProvider
from app.llm.registry import ProviderRegistry, ProviderRegistryError


def _request() -> ModelRequest:
    return ModelRequest(
        operation="test",
        contract_version="1.0",
        system_instruction="Return JSON.",
        input_json="{}",
        output_schema_json="{}",
        temperature=0.0,
        max_output_tokens=10,
    )


def test_registry_registers_and_resolves_protocol_implementations():
    provider = FakeModelProvider(
        [ModelResponse(provider_id="claude", model_id="test", output_text="{}")],
        provider_id="claude",
    )
    assert isinstance(provider, ModelProvider)
    registry = ProviderRegistry()
    registry.register(provider)
    assert registry.provider_ids() == ("claude",)
    assert registry.get("claude") is provider


def test_registry_rejects_duplicate_invalid_and_unknown_provider_ids():
    registry = ProviderRegistry()
    registry.register(FakeModelProvider([], provider_id="openai"))
    with pytest.raises(ProviderRegistryError, match="already registered"):
        registry.register(FakeModelProvider([], provider_id="openai"))
    with pytest.raises(ProviderRegistryError, match="lowercase stable identifier"):
        ProviderRegistry().register(FakeModelProvider([], provider_id="Open AI"))
    with pytest.raises(ProviderRegistryError, match="not registered"):
        registry.get("gemini")

    class MissingGenerate:
        provider_id = "invalid"

    with pytest.raises(ProviderRegistryError, match="does not implement"):
        ProviderRegistry().register(MissingGenerate())


def test_fake_provider_records_transport_neutral_requests():
    response = ModelResponse(provider_id="fake", model_id="fake-1", output_text="{}")
    provider = FakeModelProvider([response])
    request = _request()
    assert provider.generate(request) == response
    assert provider.requests == [request]
