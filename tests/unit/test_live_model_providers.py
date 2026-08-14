"""Mocked transport coverage for every optional production model adapter."""

import json

import httpx
import pytest

from app import config
from app.llm.models import ModelProviderError, ModelRequest
from app.llm.providers.anthropic import AnthropicModelProvider
from app.llm.providers.gemini import GeminiModelProvider
from app.llm.providers.openai import OpenAIModelProvider
from app.llm.providers.openai_compatible import OpenAICompatibleModelProvider
from app.llm.runtime import configured_provider


def _request() -> ModelRequest:
    return ModelRequest(
        operation="present_citation_first_envelope",
        contract_version="1.0",
        system_instruction="Use only supplied TuneForge evidence.",
        input_json='{"required_draft":{"outcome":"supported"}}',
        output_schema_json=json.dumps(
            {
                "type": "object",
                "properties": {"outcome": {"type": "string"}},
                "required": ["outcome"],
                "additionalProperties": False,
            }
        ),
        temperature=0.0,
        max_output_tokens=256,
    )


def _common(provider_type, transport, **values):
    return provider_type(
        model_id=values.pop("model_id", "test-model"),
        timeout_seconds=2,
        max_response_bytes=64 * 1024,
        transport=transport,
        **values,
    )


def test_openai_adapter_uses_responses_api_and_normalizes_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer secret"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["instructions"] == "Use only supplied TuneForge evidence."
        assert body["text"]["format"]["strict"] is True
        assert "secret" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "model": "gpt-test",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"outcome":"supported"}'}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
        )

    provider = _common(
        OpenAIModelProvider,
        httpx.MockTransport(handler),
        api_key="secret",
    )
    response = provider.generate(_request())
    assert response.provider_id == "openai"
    assert response.model_id == "gpt-test"
    assert response.output_text == '{"outcome":"supported"}'
    assert response.usage.input_tokens == 10


def test_anthropic_adapter_uses_messages_api_and_normalizes_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == "secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content)
        assert body["system"] == "Use only supplied TuneForge evidence."
        assert body["messages"][0]["content"] == _request().input_json
        return httpx.Response(
            200,
            json={
                "model": "claude-test",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": '{"outcome":"supported"}'}],
                "usage": {"input_tokens": 12, "output_tokens": 5},
            },
        )

    provider = _common(
        AnthropicModelProvider,
        httpx.MockTransport(handler),
        api_key="secret",
    )
    response = provider.generate(_request())
    assert response.provider_id == "anthropic"
    assert response.finish_reason == "end_turn"
    assert response.usage.output_tokens == 5


def test_gemini_adapter_uses_generate_content_and_normalizes_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models/gemini-test:generateContent")
        assert request.headers["x-goog-api-key"] == "secret"
        body = json.loads(request.content)
        assert body["systemInstruction"]["parts"][0]["text"].startswith("Use only")
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        return httpx.Response(
            200,
            json={
                "modelVersion": "gemini-test-001",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [{"text": '{"outcome":"supported"}'}]
                        },
                    }
                ],
                "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 3},
            },
        )

    provider = _common(
        GeminiModelProvider,
        httpx.MockTransport(handler),
        api_key="secret",
        model_id="gemini-test",
    )
    response = provider.generate(_request())
    assert response.provider_id == "gemini"
    assert response.model_id == "gemini-test-001"
    assert response.finish_reason == "STOP"


def test_openai_compatible_adapter_supports_keyless_loopback_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://127.0.0.1:11434/v1/chat/completions"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][0]["role"] == "system"
        return httpx.Response(
            200,
            json={
                "model": "local-test",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"outcome":"supported"}'},
                    }
                ],
                "usage": {"prompt_tokens": 9, "completion_tokens": 4},
            },
        )

    provider = _common(
        OpenAICompatibleModelProvider,
        httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434/v1",
        api_key="",
    )
    assert provider.generate(_request()).output_text == '{"outcome":"supported"}'


@pytest.mark.parametrize(
    "base_url",
    [
        "http://model.example/v1",
        "ftp://localhost/v1",
        "https://user:secret@example.com/v1",
        "https://example.com/v1?token=secret",
    ],
)
def test_compatible_adapter_rejects_unsafe_base_urls(base_url):
    with pytest.raises(ValueError):
        _common(
            OpenAICompatibleModelProvider,
            httpx.MockTransport(lambda request: httpx.Response(500)),
            base_url=base_url,
            api_key="",
        )


@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [(401, "authentication"), (429, "rate_limited"), (500, "unavailable")],
)
def test_provider_http_failures_are_normalized_without_upstream_body(status_code, error_code):
    provider = _common(
        OpenAIModelProvider,
        httpx.MockTransport(
            lambda request: httpx.Response(
                status_code,
                json={"error": "raw upstream secret"},
            )
        ),
        api_key="secret",
    )
    with pytest.raises(ModelProviderError) as caught:
        provider.generate(_request())
    assert caught.value.code == error_code
    assert "upstream secret" not in str(caught.value)


def test_provider_response_limit_is_enforced_before_json_parsing():
    provider = OpenAIModelProvider(
        api_key="secret",
        model_id="test",
        timeout_seconds=2,
        max_response_bytes=16,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b'{"oversized":"response body"}',
                headers={"content-type": "application/json"},
            )
        ),
    )
    with pytest.raises(ModelProviderError) as caught:
        provider.generate(_request())
    assert caught.value.code == "invalid_response"


def test_runtime_factory_is_disabled_by_default_and_selects_configured_adapter(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "")
    assert configured_provider() is None

    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "gpt-test")
    monkeypatch.setattr(config, "LLM_API_KEY", "secret")
    provider = configured_provider()
    assert isinstance(provider, OpenAIModelProvider)
    assert provider.provider_id == "openai"
