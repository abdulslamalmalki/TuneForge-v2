"""Narrow protocol implemented by every optional model provider."""

from typing import Protocol, runtime_checkable

from app.llm.models import ModelRequest, ModelResponse


@runtime_checkable
class ModelProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def generate(self, request: ModelRequest) -> ModelResponse: ...
