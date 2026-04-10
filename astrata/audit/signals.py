"""Durable internal observation signals that should cash out into work.

TODO: Generalize this out of `astrata.audit` into a broader system responses/events
substrate. The current `ObservationSignal` model is already useful beyond audit:
verification, controller handoffs, coordination outcomes, policy conflicts, and
other durable "we noticed this" events should eventually share one inspectable,
work-producing response layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from astrata.audit.review import AuditReview


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ObservationSignal(BaseModel):
    signal_id: str = Field(default_factory=lambda: str(uuid4()))
    signal_kind: Literal["surprise", "problem", "drift", "opportunity"] = "problem"
    subject_kind: str
    subject_id: str
    summary: str
    severity: Literal["low", "moderate", "high", "critical"] = "moderate"
    evidence: dict[str, object] = Field(default_factory=dict)
    proposed_actions: list[dict[str, object]] = Field(default_factory=list)
    status: Literal["open", "resolved"] = "open"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


def open_signal(
    *,
    signal_kind: Literal["surprise", "problem", "drift", "opportunity"],
    subject_kind: str,
    subject_id: str,
    summary: str,
    severity: Literal["low", "moderate", "high", "critical"] = "moderate",
    evidence: dict[str, object] | None = None,
    proposed_actions: list[dict[str, object]] | None = None,
) -> ObservationSignal:
    return ObservationSignal(
        signal_kind=signal_kind,
        subject_kind=subject_kind,
        subject_id=subject_id,
        summary=summary,
        severity=severity,
        evidence=dict(evidence or {}),
        proposed_actions=list(proposed_actions or []),
    )


def signals_from_inference_telemetry(
    inference_telemetry: dict[str, object] | None,
) -> list[ObservationSignal]:
    payload = dict(inference_telemetry or {})
    window_hours = payload.get("window_hours")
    signals: list[ObservationSignal] = []

    unjustified_prime_attempts = int(payload.get("unjustified_prime_attempts") or 0)
    if unjustified_prime_attempts > 0:
        examples = list(payload.get("unjustified_prime_examples") or [])
        signals.append(
            open_signal(
                signal_kind="drift",
                subject_kind="inference_policy",
                subject_id="prime_admission_basis",
                summary=(
                    f"Observed {unjustified_prime_attempts} Prime invocation"
                    f"{'' if unjustified_prime_attempts == 1 else 's'} without a recorded admission basis."
                ),
                severity="high" if unjustified_prime_attempts >= 2 else "moderate",
                evidence={
                    "unjustified_prime_attempts": unjustified_prime_attempts,
                    "examples": examples[:5],
                    "window_hours": window_hours,
                },
                proposed_actions=[
                    {
                        "type": "investigate_prime_policy_drift",
                        "expected_basis": [
                            "direct_more_efficient",
                            "catastrophic_or_protected",
                            "opportunistic_course_correction",
                        ],
                    }
                ],
            )
        )

    avoidable_prime_attempts = int(payload.get("avoidable_prime_attempts") or 0)
    if avoidable_prime_attempts > 0:
        examples = list(payload.get("avoidable_prime_examples") or [])
        signals.append(
            open_signal(
                signal_kind="opportunity",
                subject_kind="inference_policy",
                subject_id="prime_pressure_reduction",
                summary=(
                    f"Observed {avoidable_prime_attempts} avoidable Prime invocation"
                    f"{'' if avoidable_prime_attempts == 1 else 's'} that should be pushed onto cheaper capable routes."
                ),
                severity="moderate",
                evidence={
                    "avoidable_prime_attempts": avoidable_prime_attempts,
                    "examples": examples[:5],
                    "window_hours": window_hours,
                },
                proposed_actions=[
                    {
                        "type": "reduce_prime_pressure",
                        "goal": "move_work_to_cheaper_capable_routes",
                    }
                ],
            )
        )

    return signals


def signals_from_review(review: AuditReview) -> list[ObservationSignal]:
    signals: list[ObservationSignal] = []
    findings = list(review.findings or [])
    if not findings:
        return signals
    for finding in findings[:3]:
        signal_kind, subject_kind, subject_id = _signal_descriptor_from_finding(
            review=review,
            finding_summary=str(finding.summary or ""),
            proposed_actions=list(finding.proposed_actions or []),
        )
        signals.append(
            open_signal(
                signal_kind=signal_kind,
                subject_kind=subject_kind,
                subject_id=subject_id,
                summary=str(finding.summary or review.summary or f"{review.subject_kind} requires attention."),
                severity=finding.severity,
                evidence={
                    "review_id": review.review_id,
                    "review_subject_kind": review.subject_kind,
                    "review_subject_id": review.subject_id,
                    "finding_id": finding.finding_id,
                    "finding_evidence": dict(finding.evidence or {}),
                },
                proposed_actions=list(finding.proposed_actions or []),
            )
        )
    return signals


def _signal_descriptor_from_finding(
    *,
    review: AuditReview,
    finding_summary: str,
    proposed_actions: list[dict[str, object]],
) -> tuple[Literal["surprise", "problem", "drift", "opportunity"], str, str]:
    lowered = finding_summary.strip().lower()
    action_types = {
        str(dict(action).get("type") or "").strip().lower()
        for action in proposed_actions
        if isinstance(action, dict)
    }
    if "review_prime_routing" in action_types or "admission basis" in lowered:
        return "drift", "inference_policy", "prime_admission_basis"
    if "verifier passed" in lowered or "verifier rejected" in lowered:
        return "problem", "verification", str(review.subject_id)
    if "contradict" in lowered or "conflict" in lowered:
        return "surprise", str(review.subject_kind), str(review.subject_id)
    return "problem", str(review.subject_kind), str(review.subject_id)
