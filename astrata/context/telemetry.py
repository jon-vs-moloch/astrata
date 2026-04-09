"""Context telemetry for token-pressure tracking and context shaping decisions."""

from __future__ import annotations
from typing import Optional

from pydantic import BaseModel, Field, validator


class ContextTelemetry(BaseModel):
    """Tracks token usage and context pressure for inference calls.

    Used by the context management layer to monitor token budgets,
    detect pressure, and inform context shaping decisions.
    """

    window_tokens: int = Field(default=0, ge=0, description="Maximum context window size in tokens")
    prompt_tokens: int = Field(default=0, ge=0, description="Tokens used in the prompt")
    completion_tokens: int = Field(default=0, ge=0, description="Tokens used in the completion")
    max_pressure_threshold: float = Field(
        default=0.9, ge=0.0, le=1.0, description="Threshold for high pressure warnings"
    )

    @validator("window_tokens", "prompt_tokens", "completion_tokens")
    def validate_non_negative(cls, v):
        if v < 0:
            raise ValueError("Token counts must be non-negative")
        return v

    @property
    def pressure(self) -> float:
        """Calculate current context pressure as fraction of window used."""
        if self.window_tokens <= 0:
            return 0.0
        return min(1.0, (self.prompt_tokens + self.completion_tokens) / self.window_tokens)

    @property
    def is_high_pressure(self) -> bool:
        """Check if current pressure exceeds the threshold."""
        return self.pressure >= self.max_pressure_threshold

    @property
    def remaining_tokens(self) -> int:
        """Calculate remaining tokens in the window."""
        used = self.prompt_tokens + self.completion_tokens
        return max(0, self.window_tokens - used)

    def update_tokens(self, prompt_delta: int = 0, completion_delta: int = 0) -> None:
        """Update token counts by adding deltas.

        Args:
            prompt_delta: Change in prompt tokens (can be negative)
            completion_delta: Change in completion tokens (can be negative)
        """
        self.prompt_tokens = max(0, self.prompt_tokens + prompt_delta)
        self.completion_tokens = max(0, self.completion_tokens + completion_delta)

    def reset(self) -> None:
        """Reset all token counts to zero."""
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def get_pressure_warning(self) -> Optional[str]:
        """Return a warning message if pressure is high, else None."""
        if self.is_high_pressure:
            return f"Context pressure is high: {self.pressure:.1f}"
        return None