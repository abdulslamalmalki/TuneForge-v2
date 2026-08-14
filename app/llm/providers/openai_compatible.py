"""OpenAI-compatible chat-completions adapter, including local loopback models."""

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.llm.models import ModelProviderError, ModelRequest, ModelResponse, ModelUsage
from app.llm.providers.http_json import optional_int, post_json, require_text


class OpenAICompatibleModelProvider:
    provider_id = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        timeout_seconds: float,
        max_response_bytes: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._endpoint = _chat_completions_endpoint(base_url)
        self._api_key = api_key
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._transport = transport

    def generate(self, request: ModelRequest) -> ModelResponse:
        headers = {"authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        value = post_json(
            self._endpoint,
            headers=headers,
            payload={
                "model": self._model_id,
                "messages": [
                    {"role": "system", "content": request.system_instruction},
                    {"role": "user", "content": request.input_json},
                ],
                "temperature": request.temperature,
                "max_tokens": request.max_output_tokens,
                "response_format": {"type": "json_object"},
                "stream": False,
            },
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
            transport=self._transport,
        )
        choice = _first_choice(value)
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ModelProviderError(
                "invalid_response", "The compatible provider returned no message."
            )
        usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
        return ModelResponse(
            provider_id=self.provider_id,
            model_id=str(value.get("model") or self._model_id),
            output_text=require_text(message.get("content")),
            finish_reason=str(choice.get("finish_reason")) if choice.get("finish_reason") else None,
            usage=ModelUsage(
                input_tokens=optional_int(usage.get("prompt_tokens")),
                output_tokens=optional_int(usage.get("completion_tokens")),
            ),
        )


def _first_choice(value: dict[str, Any]) -> dict[str, Any]:
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelProviderError("invalid_response", "The compatible provider returned no choice.")
    return choices[0]


def _chat_completions_endpoint(base_url: str) -> str:
    value = urlsplit(base_url.strip())
    if value.scheme not in {"http", "https"} or not value.hostname:
        raise ValueError("The compatible provider base URL must be an absolute HTTP(S) URL.")
    if value.username or value.password or value.query or value.fragment:
        raise ValueError(
            "The compatible provider base URL cannot contain credentials or parameters."
        )
    if value.scheme == "http" and value.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Plain HTTP is permitted only for a loopback model endpoint.")
    path = value.path.rstrip("/")
    if path.endswith("/chat/completions"):
        endpoint_path = path
    else:
        endpoint_path = f"{path}/chat/completions"
    return urlunsplit((value.scheme, value.netloc, endpoint_path, "", ""))
