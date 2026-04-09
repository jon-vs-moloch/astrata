"""Bounded trial helpers for comparing candidate improvements."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field


class TrialResult(BaseModel):
    variant_id: str
    subject_kind: str = "route"
    subject_id: str = ""
    task_id: str | None = None
    score: float
    passed: bool = False
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrialSummary(BaseModel):
    variant_id: str
    observation_count: int
    average_score: float
    pass_rate: float
    average_confidence: float
    evidence: list[str] = Field(default_factory=list)


class TrialDecision(BaseModel):
    winner_variant_id: str | None = None
    margin: float = 0.0
    rationale: str = ""
    summaries: list[TrialSummary] = Field(default_factory=list)


def summarize_trials(results: list[TrialResult]) -> list[TrialSummary]:
    grouped: dict[str, list[TrialResult]] = defaultdict(list)
    for result in results:
        grouped[result.variant_id].append(result)
    summaries: list[TrialSummary] = []
    for variant_id, observations in grouped.items():
        observation_count = len(observations)
        average_score = mean(result.score for result in observations)
        pass_rate = sum(1 for result in observations if result.passed) / observation_count
        average_confidence = mean(result.confidence for result in observations)
        evidence: list[str] = []
        for result in observations:
            for item in result.evidence:
                if item not in evidence:
                    evidence.append(item)
        summaries.append(
            TrialSummary(
                variant_id=variant_id,
                observation_count=observation_count,
                average_score=average_score,
                pass_rate=pass_rate,
                average_confidence=average_confidence,
                evidence=evidence[:6],
            )
        )
    summaries.sort(
        key=lambda summary: (
            summary.average_score,
            summary.pass_rate,
            summary.average_confidence,
            summary.observation_count,
            summary.variant_id,
        ),
        reverse=True,
    )
    return summaries


def decide_trial_winner(
    results: list[TrialResult],
    *,
    min_observations: int = 1,
    min_margin: float = 0.05,
) -> TrialDecision:
    summaries = summarize_trials(results)
    if not summaries:
        return TrialDecision(rationale="No trial observations are available.", summaries=[])
    viable = [summary for summary in summaries if summary.observation_count >= min_observations]
    if len(viable) < 2:
        return TrialDecision(
            winner_variant_id=None,
            margin=0.0,
            rationale="Not enough viable variants have sufficient observations yet.",
            summaries=summaries,
        )
    leader = viable[0]
    runner_up = viable[1]
    margin = leader.average_score - runner_up.average_score
    if margin < min_margin:
        return TrialDecision(
            winner_variant_id=None,
            margin=margin,
            rationale="Top variants are still too close to justify promotion.",
            summaries=summaries,
        )
    rationale = (
        f"Variant `{leader.variant_id}` leads `{runner_up.variant_id}` by {margin:.3f} "
        f"with pass rate {leader.pass_rate:.2f} over {leader.observation_count} observations."
    )
    return TrialDecision(
        winner_variant_id=leader.variant_id,
        margin=margin,
        rationale=rationale,
        summaries=summaries,
    )
