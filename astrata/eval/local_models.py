"""Empirical evaluation helpers for one eval subject: local models."""

from __future__ import annotations

from dataclasses import dataclass

from astrata.eval.ratings import RatingStore
from astrata.eval.substrate import EvalSummary, build_eval_domain
from astrata.local.telemetry import LocalModelTelemetryStore
from astrata.variants.trials import TrialResult, decide_trial_winner, summarize_trials


@dataclass(frozen=True)
class LocalModelEvalSummary(EvalSummary):
    task_class: str = "general"


def summarize_local_model_evals(
    *,
    telemetry: LocalModelTelemetryStore,
    task_class: str,
    ratings: RatingStore | None = None,
) -> LocalModelEvalSummary:
    domain = build_eval_domain(
        subject_kind="local_model",
        task_class=task_class,
        mutation_surface="model_profile",
        environment="local_runtime",
    )
    results: list[TrialResult] = []
    for item in telemetry.all_observations():
        if not isinstance(item, dict):
            continue
        if str(item.get("task_class") or "general") != task_class:
            continue
        model_path = str(item.get("model_path") or "")
        if not model_path:
            continue
        score = item.get("score")
        if not isinstance(score, (int, float)):
            continue
        note = item.get("note")
        source = item.get("source")
        evidence = []
        if source:
            evidence.append(f"source:{source}")
        if note:
            evidence.append(str(note))
        results.append(
            TrialResult(
                variant_id=model_path,
                subject_kind="local_model",
                subject_id=model_path,
                score=float(score),
                passed=bool(item.get("success")),
                confidence=0.6,
                evidence=evidence,
                metadata={"task_class": task_class},
            )
        )
    summaries = summarize_trials(results)
    decision = decide_trial_winner(results, min_observations=2, min_margin=0.05)
    rating_leader_variant_id = ratings.get_domain_leader(domain=domain.rating_domain, min_matches=2) if ratings else None
    rating_snapshot = ratings.get_snapshot() if ratings else None
    return LocalModelEvalSummary(
        domain=domain,
        summaries=summaries,
        decision=decision,
        rating_leader_variant_id=rating_leader_variant_id,
        rating_snapshot=rating_snapshot,
        task_class=task_class,
    )


def decide_local_model_winner(
    *,
    telemetry: LocalModelTelemetryStore,
    task_class: str,
    ratings: RatingStore | None = None,
) -> str | None:
    summary = summarize_local_model_evals(telemetry=telemetry, task_class=task_class, ratings=ratings)
    return summary.decision.winner_variant_id or summary.rating_leader_variant_id
