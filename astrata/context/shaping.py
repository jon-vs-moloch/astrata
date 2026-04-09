"""Minimal context shaping helpers."""

from __future__ import annotations

from astrata.context.budget import ContextBudget
from astrata.context.telemetry import ContextTelemetry


def should_compact_context(*, telemetry: ContextTelemetry, budget: ContextBudget, threshold: float = 0.85) -> bool:
    if budget.max_window_tokens <= 0:
        return False
    return telemetry.pressure >= threshold
