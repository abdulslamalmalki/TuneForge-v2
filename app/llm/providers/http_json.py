"""Bounded JSON transport shared by optional model-provider adapters."""

import json
from collections.abc import Mapping
from typing import Any

import httpx

from app.llm.models import ModelProviderError


def post_json(
    url: str,
    *,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout_seconds: float,
    max_response_bytes: int,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """POST one JSON request without redirects or unbounded response buffering."""

    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            transport=transport,
        ) as client:
            with client.stream("POST", url, headers=dict(headers), json=dict(payload)) as response:
                if response.status_code >= 300:
                    _raise_status(response.status_code)
                content_type = response.headers.get("content-type", "").lower()
                if "application/json" not in content_type:
                    raise ModelProviderError(
                        "invalid_response", "The model provider returned a non-JSON response."
                    )
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_response_bytes:
                        raise ModelProviderError(
                            "invalid_response", "The model provider response exceeded the limit."
                        )
                    chunks.append(chunk)
    except ModelProviderError:
        raise
    except httpx.TimeoutException:
        raise ModelProviderError("timeout", "The model provider timed out.") from None
    except httpx.RequestError:
        raise ModelProviderError("transport", "The model provider could not be reached.") from None

    try:
        value = json.loads(b"".join(chunks), object_pairs_hook=_object_without_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ModelProviderError(
            "invalid_response", "The model provider returned invalid JSON."
        ) from None
    if not isinstance(value, dict):
        raise ModelProviderError(
            "invalid_response", "The model provider response was not a JSON object."
        )
    return value


def require_output_schema(request_schema_json: str) -> dict[str, Any]:
    try:
        schema = json.loads(request_schema_json)
    except json.JSONDecodeError:
        raise ModelProviderError(
            "invalid_response", "The local output schema is invalid."
        ) from None
    if not isinstance(schema, dict):
        raise ModelProviderError("invalid_response", "The local output schema is invalid.")
    return schema


def require_text(value: object, message: str = "The model provider returned no text.") -> str:
    if not isinstance(value, str) or not value:
        raise ModelProviderError("invalid_response", message)
    return value


def optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _raise_status(status_code: int) -> None:
    if status_code in {401, 403}:
        raise ModelProviderError("authentication", "Model provider authentication failed.")
    if status_code == 429:
        raise ModelProviderError("rate_limited", "The model provider rate limit was reached.")
    if status_code in {408, 504}:
        raise ModelProviderError("timeout", "The model provider timed out.")
    if status_code >= 500:
        raise ModelProviderError("unavailable", "The model provider is unavailable.")
    raise ModelProviderError("invalid_response", "The model provider rejected the request.")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key.")
        value[key] = item
    return value
