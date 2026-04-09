from astrata.context.budget import ContextBudget
from astrata.context.shaping import (
    calculate_compaction_ratio,
    estimate_compaction_impact,
    select_optimal_strategy,
    should_compact_context,
)
from astrata.context.telemetry import ContextTelemetry


def test_context_telemetry_tracks_pressure_and_remaining_tokens():
    telemetry = ContextTelemetry(window_tokens=1000, prompt_tokens=700, completion_tokens=150)
    assert telemetry.pressure == 0.85
    assert telemetry.is_high_pressure is False
    assert telemetry.remaining_tokens == 150
    telemetry.update_tokens(prompt_delta=50)
    assert telemetry.is_high_pressure is True
    assert telemetry.get_pressure_warning() is not None


def test_context_shaping_helpers_compute_compaction_strategy():
    telemetry = ContextTelemetry(window_tokens=1000, prompt_tokens=800, completion_tokens=150)
    budget = ContextBudget(
        max_window_tokens=1000,
        conversation_history_tokens=400,
        retrieval_tokens=200,
        task_specific_tokens=100,
    )
    assert should_compact_context(telemetry=telemetry, budget=budget)
    ratio = calculate_compaction_ratio(telemetry, budget, target_pressure=0.8)
    assert 0.0 < ratio < 1.0
    impact = estimate_compaction_impact(budget, ratio, strategy="summarize")
    assert impact["conversation_history_tokens"] > 0
    assert select_optimal_strategy(telemetry, budget) == "summarize"

    tight_budget = ContextBudget(
        max_window_tokens=1000,
        conversation_history_tokens=780,
        retrieval_tokens=150,
        task_specific_tokens=40,
    )
    assert select_optimal_strategy(telemetry, tight_budget) == "truncate"
