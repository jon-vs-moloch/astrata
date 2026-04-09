"""Base strategy interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from astrata.providers.base import CompletionRequest


@dataclass(frozen=True)
class StrategyContext:
    request: CompletionRequest
    endpoint_type: str
    strategy_id: str
    memory_policy: str
    continuity: str
    runtime_key: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyResult:
    content: str
    strategy_id: str
    runtime_key: str
    metadata: dict[str, Any] = field(default_factory=dict)


class InferenceStrategy(ABC):
    @property
    @abstractmethod
    def strategy_id(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def execute(self, context: StrategyContext) -> StrategyResult:
        raise NotImplementedError
