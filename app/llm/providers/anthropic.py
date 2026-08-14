"""Anthropic Messages API adapter for TuneForge's exact presentation contract."""

from typing import Any

import httpx

from app.llm.models import ModelProviderError, ModelRequest, ModelResponse, ModelUsage
from app.llm.providers.http_json import optional_int, post_json, require_text


class AnthropicModelProvider:
    provider_id = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str,
        timeout_seconds: float,
        max_response_bytes: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._transport = transport

    def generate(self, request: ModelRequest) -> ModelResponse:
        value = post_json(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
            payload={
                "model": self._model_id,
                "max_tokens": request.max_output_tokens,
                "system": request.system_instruction,
                "messages": [{"role": "user", "content": request.input_json}],
            },
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
            transport=self._transport,
        )
        usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=str(value.get("model") or self._model_id),
            output_text=_response_text(value),
            finish_reason=str(value.get("stop_reason")) if value.get("stop_reason") else None,
            usage=ModelUsage(
                input_tokens=optional_int(usage.get("input_tokens")),
                output_tokens=optional_int(usage.get("output_tokens")),
            ),
        )


def _response_text(value: dict[str, Any]) -> str:
    content = value.get("content")
    if not isinstance(content, list):
        raise ModelProviderError("invalid_response", "Anthropic returned no content collection.")
    texts = [
        require_text(block.get("text"))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return require_text("".join(texts))
