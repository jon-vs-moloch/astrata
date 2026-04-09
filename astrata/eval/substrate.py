"""Generic evaluation substrate helpers.

Local model evaluation is only one instance of this machinery. The broader
intent is to evaluate any mutation surface Astrata can test: providers, routes,
models, backends, runtime profiles, prompts, procedures, and policy bundles.
"""

from __future__ import annotations

from dataclasses import dataclass

from astrata.variants.trials import TrialDecision, TrialSummary


@dataclass(frozen=True)
class EvalDomain:
    subject_kind: str
    task_class: str
    mutation_surface: str
    environment: str | None = None

    @property
    def rating_domain(self) -> str:
        return f"{self.subject_kind}:{self.task_class}"


@dataclass(frozen=True)
class EvalSummary:
    domain: EvalDomain
    summaries: list[TrialSummary]
    decision: TrialDecision
    rating_leader_variant_id: str | None = None
    rating_snapshot: dict | None = None


def build_eval_domain(
    *,
    subject_kind: str,
    task_class: str,
    mutation_surface: str,
    environment: str | None = None,
) -> EvalDomain:
    return EvalDomain(
        subject_kind=subject_kind,
        task_class=task_class,
        mutation_surface=mutation_surface,
        environment=environment,
    )
