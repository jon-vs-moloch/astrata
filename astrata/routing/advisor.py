"""Empirical route advice from Astrata's execution-route evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from math import log1p
from math import sqrt
from statistics import fmean

from astrata.eval.observations import EvalObservationStore
from astrata.eval.ratings import RatingStore
from astrata.eval.substrate import build_eval_domain
from astrata.scheduling.quota import default_source_limits
from astrata.variants.trials import TrialResult, decide_trial_winner


@dataclass(frozen=True)
class RouteAdvice:
    preferred_providers: tuple[str, ...] = ()
    preferred_cli_tools: tuple[str, ...] = ()
    rationale: str = ""


@dataclass(frozen=True)
class _CandidateStats:
    variant_id: str
    source_id: str
    average_score: float
    observation_count: int
    average_confidence: float
    average_total_wall_seconds: float
    rating: float
    rating_matches: int
    hourly_capacity: int | None

    @property
    def effective_count(self) -> float:
        return float(self.observation_count) + (0.5 * float(self.rating_matches))

    @property
    def uncertainty_bonus(self) -> float:
        count = max(1.0, self.effective_count)
        confidence_factor = max(0.6, 1.1 - (0.4 * min(1.0, self.average_confidence)))
        return 0.18 * confidence_factor / sqrt(count)

    @property
    def lower_confidence_bound(self) -> float:
        return self.average_score - self.uncertainty_bonus

    @property
    def upper_confidence_bound(self) -> float:
        return self.average_score + self.uncertainty_bonus

    @property
    def exploitation_score(self) -> float:
        return self.average_score

    @property
    def capacity_score(self) -> float:
        if self.source_id.startswith("local:"):
            return 0.45
        if self.hourly_capacity is None:
            return 0.75
        bounded = max(1, min(240, int(self.hourly_capacity)))
        return max(0.2, min(1.0, log1p(bounded) / log1p(240)))

    @property
    def system_utility_score(self) -> float:
        capacity_bonus = 0.16 * self.capacity_score
        scarcity_penalty = 0.10 * max(0.0, 0.7 - self.capacity_score)
        speed_bonus = 0.0
        if self.average_total_wall_seconds > 0.0:
            speed_bonus = min(0.06, 0.025 / max(0.25, self.average_total_wall_seconds))
        return self.lower_confidence_bound + capacity_bonus + speed_bonus - scarcity_penalty

    @property
    def information_gain_score(self) -> float:
        freshness_bonus = 0.08 / sqrt(max(1.0, self.effective_count))
        capacity_bonus = 0.08 * self.capacity_score
        speed_bonus = 0.0
        if self.average_total_wall_seconds > 0.0:
            speed_bonus = min(0.05, 0.03 / max(0.25, self.average_total_wall_seconds))
        return self.upper_confidence_bound + freshness_bonus + capacity_bonus + speed_bonus


class RoutePerformanceAdvisor:
    def __init__(
        self,
        *,
        observations: EvalObservationStore,
        ratings: RatingStore,
    ) -> None:
        self._observations = observations
        self._ratings = ratings

    @classmethod
    def from_data_dir(cls, data_dir: Path) -> "RoutePerformanceAdvisor":
        return cls(
            observations=EvalObservationStore(state_path=data_dir / "eval_observations.json"),
            ratings=RatingStore(state_path=data_dir / "local_model_ratings.json"),
        )

    def advise(self, *, task_class: str) -> RouteAdvice:
        domain = build_eval_domain(
            subject_kind="execution_route",
            task_class=task_class,
            mutation_surface="route_choice",
            environment="mixed",
        )
        candidate_stats = self._candidate_stats(task_class=task_class, domain=domain.rating_domain)
        leader = self._ratings.get_domain_leader(domain=domain.rating_domain, min_matches=2)
        if leader and not candidate_stats:
            advice = _variant_id_to_advice(leader)
            if advice is not None:
                return RouteAdvice(
                    preferred_providers=advice[0],
                    preferred_cli_tools=advice[1],
                    rationale=f"rating_leader:{leader}",
                )

        results: list[TrialResult] = []
        for item in self._observations.list(subject_kind="execution_route", task_class=task_class):
            score = item.get("score")
            variant_id = str(item.get("variant_id") or "")
            if not variant_id or not isinstance(score, (int, float)):
                continue
            results.append(
                TrialResult(
                    variant_id=variant_id,
                    subject_kind="execution_route",
                    subject_id=str(item.get("subject_id") or variant_id),
                    score=float(score),
                    passed=bool(item.get("passed")),
                    confidence=float(item.get("confidence") or 0.0),
                    evidence=[str(part) for part in (item.get("evidence") or [])],
                    metadata=dict(item.get("metadata") or {}),
                )
            )
        decision = decide_trial_winner(results, min_observations=2, min_margin=0.03)

        if candidate_stats:
            capability_floor = _capability_floor(task_class)
            capable_candidates = [
                item for item in candidate_stats if item.lower_confidence_bound >= capability_floor
            ]
            selection_pool = capable_candidates or candidate_stats

            if capable_candidates:
                established = max(
                    selection_pool,
                    key=lambda item: (
                        item.capacity_score,
                        item.system_utility_score,
                        item.lower_confidence_bound,
                        item.rating,
                        item.exploitation_score,
                        -item.average_total_wall_seconds,
                        item.variant_id,
                    ),
                )
            else:
                established = max(
                    selection_pool,
                    key=lambda item: (
                        item.lower_confidence_bound,
                        item.rating,
                        item.exploitation_score,
                        item.capacity_score,
                        -item.average_total_wall_seconds,
                        item.variant_id,
                    ),
                )
            exploratory = max(
                selection_pool,
                key=lambda item: (
                    item.information_gain_score,
                    item.upper_confidence_bound,
                    item.rating,
                    item.variant_id,
                ),
            )
            chosen = established
            rationale_prefix = "capable_least_constrained" if capable_candidates else "best_available_route"
            if decision.winner_variant_id and decision.winner_variant_id == established.variant_id:
                rationale_prefix = (
                    "trial_winner_capable_least_constrained"
                    if capable_candidates
                    else "trial_winner_best_available"
                )
            rationale = (
                f"{rationale_prefix}:{established.variant_id}:"
                f"avg={established.average_score:.3f}:n={established.observation_count}:"
                f"capacity={established.capacity_score:.3f}:"
                f"floor={capability_floor:.3f}"
            )
            if exploratory.variant_id != established.variant_id:
                close_margin = established.lower_confidence_bound - exploratory.upper_confidence_bound
                exploratory_is_less_sampled = exploratory.effective_count + 0.75 < established.effective_count
                if close_margin <= 0.04 and exploratory_is_less_sampled:
                    chosen = exploratory
                    rationale = (
                        f"information_gain_explore:{exploratory.variant_id}:"
                        f"avg={exploratory.average_score:.3f}:"
                        f"n={exploratory.observation_count}:"
                        f"bonus={exploratory.uncertainty_bonus:.3f}:"
                        f"capacity={exploratory.capacity_score:.3f}:"
                        f"floor={capability_floor:.3f}"
                    )
            advice = _variant_id_to_advice(chosen.variant_id)
            if advice is not None:
                return RouteAdvice(
                    preferred_providers=advice[0],
                    preferred_cli_tools=advice[1],
                    rationale=rationale,
                )
        return RouteAdvice()

    def _candidate_stats(self, *, task_class: str, domain: str) -> list[_CandidateStats]:
        observation_buckets: dict[str, list[dict[str, object]]] = {}
        for item in self._observations.list(subject_kind="execution_route", task_class=task_class):
            variant_id = str(item.get("variant_id") or "").strip()
            score = item.get("score")
            if not variant_id or not isinstance(score, (int, float)):
                continue
            observation_buckets.setdefault(variant_id, []).append(item)
        rating_bucket = (
            self._ratings.get_snapshot()
            .get("ratings", {})
            .get("by_domain", {})
            .get(domain, {})
        )
        stats: list[_CandidateStats] = []
        for variant_id, items in observation_buckets.items():
            confidences = [float(item.get("confidence") or 0.0) for item in items]
            wall_times = [
                float(item.get("total_wall_seconds") or item.get("execution_seconds") or 0.0)
                for item in items
                if float(item.get("total_wall_seconds") or item.get("execution_seconds") or 0.0) > 0.0
            ]
            rating_entry = dict(rating_bucket.get(variant_id) or {})
            source_id = _variant_id_to_source_id(variant_id)
            limits = default_source_limits()
            stats.append(
                _CandidateStats(
                    variant_id=variant_id,
                    source_id=source_id,
                    average_score=fmean(float(item.get("score") or 0.0) for item in items),
                    observation_count=len(items),
                    average_confidence=fmean(confidences) if confidences else 0.0,
                    average_total_wall_seconds=fmean(wall_times) if wall_times else 0.0,
                    rating=float(rating_entry.get("rating") or 1500.0),
                    rating_matches=int(rating_entry.get("matches") or 0),
                    hourly_capacity=limits.get(source_id),
                )
            )
        return stats


def _variant_id_to_advice(variant_id: str) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    text = str(variant_id or "").strip()
    if not text:
        return None
    if text.startswith("cli:"):
        parts = text.split(":", 2)
        cli_tool = parts[1] if len(parts) > 1 else ""
        if cli_tool:
            return (), (cli_tool,)
        return None
    if text.startswith("local:"):
        return None
    provider = text.split(":", 1)[0].strip().lower()
    if provider:
        return (provider,), ()
    return None


def _variant_id_to_source_id(variant_id: str) -> str:
    text = str(variant_id or "").strip().lower()
    if not text:
        return "unknown"
    if text.startswith("cli:"):
        parts = text.split(":", 2)
        cli_tool = parts[1] if len(parts) > 1 else ""
        return f"cli:{cli_tool}" if cli_tool else "cli"
    if text.startswith("local:"):
        return "local:managed"
    if text.startswith("strata-endpoint:"):
        return "strata-endpoint"
    provider = text.split(":", 1)[0].strip()
    return provider or "unknown"


def _capability_floor(task_class: str) -> float:
    normalized = str(task_class or "general").strip().lower()
    if normalized == "coding":
        return 0.45
    if normalized == "review":
        return 0.40
    return 0.35
