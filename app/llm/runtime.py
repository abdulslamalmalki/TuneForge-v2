"""Runtime selection for the one optional presentation provider."""

from app import config
from app.llm.providers.anthropic import AnthropicModelProvider
from app.llm.providers.base import ModelProvider
from app.llm.providers.gemini import GeminiModelProvider
from app.llm.providers.openai import OpenAIModelProvider
from app.llm.providers.openai_compatible import OpenAICompatibleModelProvider
from app.llm.registry import ProviderRegistry


def configured_provider() -> ModelProvider | None:
    """Build the configured adapter without caching credentials or clients globally."""

    if not config.LLM_PROVIDER:
        return None
    common = {
        "model_id": config.LLM_MODEL,
        "timeout_seconds": config.LLM_TIMEOUT_SECONDS,
        "max_response_bytes": config.LLM_MAX_RESPONSE_BYTES,
    }
    if config.LLM_PROVIDER == "openai":
        provider: ModelProvider = OpenAIModelProvider(
            api_key=config.LLM_API_KEY,
            **common,
        )
    elif config.LLM_PROVIDER == "anthropic":
        provider = AnthropicModelProvider(api_key=config.LLM_API_KEY, **common)
    elif config.LLM_PROVIDER == "gemini":
        provider = GeminiModelProvider(api_key=config.LLM_API_KEY, **common)
    elif config.LLM_PROVIDER == "openai_compatible":
        provider = OpenAICompatibleModelProvider(
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            **common,
        )
    else:  # validate_runtime_config prevents this during application startup.
        raise RuntimeError("The configured model provider is not supported.")

    registry = ProviderRegistry()
    registry.register(provider)
    return registry.get(config.LLM_PROVIDER)


def connected_provider_ids() -> tuple[str, ...]:
    """Return public-safe connection metadata without exposing model IDs or secrets."""

    return (config.LLM_PROVIDER,) if config.LLM_PROVIDER else ()
