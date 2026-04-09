from astrata.variants.trials import TrialResult, decide_trial_winner, summarize_trials


def test_summarize_trials_aggregates_variant_observations():
    summaries = summarize_trials(
        [
            TrialResult(variant_id="baseline", score=0.5, passed=False, confidence=0.6, evidence=["slow"]),
            TrialResult(variant_id="candidate", score=0.9, passed=True, confidence=0.8, evidence=["fast"]),
            TrialResult(variant_id="candidate", score=0.8, passed=True, confidence=0.7, evidence=["stable"]),
        ]
    )
    assert summaries[0].variant_id == "candidate"
    assert summaries[0].observation_count == 2
    assert summaries[0].average_score > summaries[1].average_score
    assert "fast" in summaries[0].evidence


def test_decide_trial_winner_requires_clear_margin():
    decision = decide_trial_winner(
        [
            TrialResult(variant_id="baseline", score=0.70, passed=True, confidence=0.7),
            TrialResult(variant_id="candidate", score=0.90, passed=True, confidence=0.8),
        ],
        min_margin=0.05,
    )
    assert decision.winner_variant_id == "candidate"
    assert decision.margin >= 0.05
    assert "candidate" in decision.rationale


def test_decide_trial_winner_stays_conservative_when_results_are_close():
    decision = decide_trial_winner(
        [
            TrialResult(variant_id="baseline", score=0.70, passed=True, confidence=0.7),
            TrialResult(variant_id="candidate", score=0.72, passed=True, confidence=0.8),
        ],
        min_margin=0.05,
    )
    assert decision.winner_variant_id is None
    assert "too close" in decision.rationale
