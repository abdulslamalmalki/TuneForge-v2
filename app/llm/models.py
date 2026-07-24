"""Transport-neutral request, response, and error types for model adapters."""

from dataclasses import dataclass

PROVIDER_ERROR_CODES = frozenset(
    {
        "authentication",
        "invalid_response",
        "rate_limited",
        "timeout",
        "transport",
        "unavailable",
    }
)


@dataclass(frozen=True)
class ModelRequest:
    """A provider-independent completion request with JSON-only input and output."""

    operation: str
    contract_version: str
    system_instruction: str
    input_json: str
    output_schema_json: str
    temperature: float
    max_output_tokens: int


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    """Normalized adapter output. Core validation still treats it as untrusted."""

    provider_id: str
    model_id: str
    output_text: str
    finish_reason: str | None = None
    usage: ModelUsage | None = None


class ModelProviderError(RuntimeError):
    """A safe, normalized provider failure without raw upstream payloads."""

    def __init__(self, code: str, safe_message: str) -> None:
        if code not in PROVIDER_ERROR_CODES:
            raise ValueError("Unknown normalized provider error code.")
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
