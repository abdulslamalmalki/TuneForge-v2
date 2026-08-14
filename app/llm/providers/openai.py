"""OpenAI Responses API adapter for TuneForge's exact presentation contract."""

from typing import Any

import httpx

from app.llm.models import ModelProviderError, ModelRequest, ModelResponse, ModelUsage
from app.llm.providers.http_json import (
    optional_int,
    post_json,
    require_output_schema,
    require_text,
)


class OpenAIModelProvider:
    provider_id = "openai"

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
        payload = {
            "model": self._model_id,
            "instructions": request.system_instruction,
            "input": request.input_json,
            "max_output_tokens": request.max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "tuneforge_presentation",
                    "strict": True,
                    "schema": require_output_schema(request.output_schema_json),
                }
            },
        }
        value = post_json(
            "https://api.openai.com/v1/responses",
            headers={"authorization": f"Bearer {self._api_key}"},
            payload=payload,
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
            transport=self._transport,
        )
        output_text = _response_text(value)
        usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=str(value.get("model") or self._model_id),
            output_text=output_text,
            finish_reason=str(value.get("status")) if value.get("status") else None,
            usage=ModelUsage(
                input_tokens=optional_int(usage.get("input_tokens")),
                output_tokens=optional_int(usage.get("output_tokens")),
            ),
        )


def _response_text(value: dict[str, Any]) -> str:
    output = value.get("output")
    if not isinstance(output, list):
        raise ModelProviderError("invalid_response", "OpenAI returned no output collection.")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "output_text":
                texts.append(require_text(block.get("text")))
    return require_text("".join(texts))
