"""Minimal context telemetry for early token-pressure tracking."""

from __future__ import annotations

from pydantic import BaseModel


class ContextTelemetry(BaseModel):
    window_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def pressure(self) -> float:
        if self.window_tokens <= 0:
            return 0.0
        return min(1.0, (self.prompt_tokens + self.completion_tokens) / self.window_tokens)
