"""Google Gemini generateContent adapter for TuneForge's presentation contract."""

from typing import Any
from urllib.parse import quote

import httpx

from app.llm.models import ModelProviderError, ModelRequest, ModelResponse, ModelUsage
from app.llm.providers.http_json import (
    optional_int,
    post_json,
    require_output_schema,
    require_text,
)


class GeminiModelProvider:
    provider_id = "gemini"

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
        model_path = quote(self._model_id, safe="-._")
        value = post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_path}:generateContent",
            headers={"x-goog-api-key": self._api_key},
            payload={
                "systemInstruction": {"parts": [{"text": request.system_instruction}]},
                "contents": [
                    {"role": "user", "parts": [{"text": request.input_json}]}
                ],
                "generationConfig": {
                    "maxOutputTokens": request.max_output_tokens,
                    "responseMimeType": "application/json",
                    "responseJsonSchema": require_output_schema(request.output_schema_json),
                },
            },
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
            transport=self._transport,
        )
        usage = value.get("usageMetadata") if isinstance(value.get("usageMetadata"), dict) else {}
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=str(value.get("modelVersion") or self._model_id),
            output_text=_response_text(value),
            finish_reason=_finish_reason(value),
            usage=ModelUsage(
                input_tokens=optional_int(usage.get("promptTokenCount")),
                output_tokens=optional_int(usage.get("candidatesTokenCount")),
            ),
        )


def _candidate(value: dict[str, Any]) -> dict[str, Any]:
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise ModelProviderError("invalid_response", "Gemini returned no candidate.")
    return candidates[0]


def _response_text(value: dict[str, Any]) -> str:
    candidate = _candidate(value)
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise ModelProviderError("invalid_response", "Gemini returned no content parts.")
    texts = [
        require_text(part.get("text"))
        for part in parts
        if isinstance(part, dict) and "text" in part
    ]
    return require_text("".join(texts))


def _finish_reason(value: dict[str, Any]) -> str | None:
    reason = _candidate(value).get("finishReason")
    return str(reason) if reason else None
