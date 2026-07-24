"""Scripted provider used to test the core boundary without a network or API key."""

from collections.abc import Iterable

from app.llm.models import ModelProviderError, ModelRequest, ModelResponse


class FakeModelProvider:
    def __init__(
        self,
        responses: Iterable[ModelResponse | ModelProviderError],
        *,
        provider_id: str = "fake",
    ) -> None:
        self._provider_id = provider_id
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise ModelProviderError("unavailable", "No scripted model response is available.")
        response = self._responses.pop(0)
        if isinstance(response, ModelProviderError):
            raise response
        return response
