"""Backend contracts for managed local inference runtimes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field
from astrata.inference.contracts import BackendCapabilitySet


class BackendHealth(BaseModel):
    ok: bool
    status: str = "unknown"
    detail: str | None = None
    endpoint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BackendLaunchSpec(BaseModel):
    command: list[str]
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    endpoint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LocalBackend(ABC):
    """A managed local inference backend."""

    @property
    @abstractmethod
    def backend_id(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def build_launch_spec(self, **kwargs: Any) -> BackendLaunchSpec:
        raise NotImplementedError

    @abstractmethod
    def healthcheck(self, **kwargs: Any) -> BackendHealth:
        raise NotImplementedError

    def capabilities(self) -> BackendCapabilitySet:
        return BackendCapabilitySet(backend_id=self.backend_id)
