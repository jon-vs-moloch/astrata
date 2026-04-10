"""Base provider abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str | None = None
    name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompletionRequest(BaseModel):
    messages: list[Message]
    model: str | None = None
    temperature: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompletionResponse(BaseModel):
    provider: str
    model: str | None = None
    content: str
    raw: dict[str, Any] = Field(default_factory=dict)


class Provider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def is_configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def default_model(self) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "is_configured": self.is_configured(),
            "default_model": self.default_model(),
        }

    def get_quota_windows(self, route: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
        return None


def assert_projected_memory_request(request: CompletionRequest, *, provider_name: str) -> None:
    metadata = dict(request.metadata or {})
    for key in ("memory_pages", "memory_records", "raw_memory", "raw_memory_pages", "memory_payloads"):
        if key in metadata:
            raise RuntimeError(
                f"{provider_name} refused request: raw memory records may not be sent to a remote provider."
            )

    projected = metadata.get("memory_context")
    if projected is None:
        return
    if isinstance(projected, str):
        return
    if isinstance(projected, list) and all(isinstance(item, str) for item in projected):
        return
    raise RuntimeError(
        f"{provider_name} refused request: memory context must be projected text snippets, not raw memory structures."
    )
