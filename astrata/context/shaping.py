"""Context shaping utilities for efficient token management.

This module provides functions to determine when and how to compact context
based on telemetry and budget constraints, supporting downstream efficiency
in core execution pipelines.
"""

from __future__ import annotations
from typing import Literal

from astrata.context.budget import ContextBudget
from astrata.context.telemetry import ContextTelemetry


CompactionStrategy = Literal["truncate", "summarize", "prioritize"]


def should_compact_context(*, telemetry: ContextTelemetry, budget: ContextBudget, threshold: float = 0.85) -> bool:
    """Determine if context compaction is recommended based on current pressure.

    Args:
        telemetry: Current context telemetry data.
        budget: Context budget allocation.
        threshold: Pressure threshold above which compaction is recommended.

    Returns:
        True if compaction should be considered.
    """
    if budget.max_window_tokens <= 0:
        return False
    return telemetry.pressure >= threshold


def calculate_compaction_ratio(telemetry: ContextTelemetry, budget: ContextBudget, target_pressure: float = 0.8) -> float:
    """Calculate the compaction ratio needed to achieve target pressure.

    Args:
        telemetry: Current context telemetry data.
        budget: Context budget allocation.
        target_pressure: Desired pressure level after compaction.

    Returns:
        Compaction ratio (0.0 to 1.0) where 1.0 means no compaction.
    """
    if telemetry.window_tokens <= 0:
        return 1.0
    current_pressure = telemetry.pressure
    if current_pressure <= target_pressure:
        return 1.0
    # Simple linear scaling - could be made more sophisticated
    return target_pressure / current_pressure


def estimate_compaction_impact(budget: ContextBudget, compaction_ratio: float, strategy: CompactionStrategy = "truncate") -> dict[str, int]:
    """Estimate token savings from applying compaction to conversation history.

    Args:
        budget: Context budget allocation.
        compaction_ratio: Ratio of content to retain (0.0 to 1.0).
        strategy: Compaction strategy to estimate.

    Returns:
        Dictionary with estimated token savings by category.
    """
    conversation_tokens = budget.conversation_history_tokens
    estimated_saved = int(conversation_tokens * (1 - compaction_ratio))

    # Different strategies might save different amounts or affect different categories
    if strategy == "truncate":
        return {"conversation_history_tokens": estimated_saved}
    elif strategy == "summarize":
        # Summarization might save more but affect retrieval too
        retrieval_saved = int(budget.retrieval_tokens * (1 - compaction_ratio) * 0.5)
        return {
            "conversation_history_tokens": estimated_saved,
            "retrieval_tokens": retrieval_saved,
        }
    elif strategy == "prioritize":
        # Prioritization might save across multiple categories
        task_saved = int(budget.task_specific_tokens * (1 - compaction_ratio) * 0.3)
        return {
            "conversation_history_tokens": estimated_saved,
            "task_specific_tokens": task_saved,
        }
    return {"conversation_history_tokens": estimated_saved}


def select_optimal_strategy(telemetry: ContextTelemetry, budget: ContextBudget) -> CompactionStrategy:
    """Select the most appropriate compaction strategy based on current state.

    Args:
        telemetry: Current context telemetry data.
        budget: Context budget allocation.

    Returns:
        Recommended compaction strategy.
    """
    pressure = telemetry.pressure
    available_tokens = budget.available_tokens

    # High pressure with low availability suggests aggressive truncation
    if pressure > 0.9 and available_tokens < budget.max_window_tokens * 0.1:
        return "truncate"

    # Moderate pressure allows for smarter summarization
    if pressure > 0.8:
        return "summarize"

    # Low pressure can use prioritization
    return "prioritize"