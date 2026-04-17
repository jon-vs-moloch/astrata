"""Policy for cashing audit findings into follow-up work."""

from __future__ import annotations

import hashlib
from typing import Any

from astrata.audit.review import AuditReview
from astrata.audit.signals import ObservationSignal


def select_audit_followup_policy(
    *,
    review: AuditReview,
    sample_rate: int = 5,
) -> dict[str, Any]:
    findings = list(review.findings or [])
    if findings:
        return {
            "mode": "targeted",
            "reason": "Audit findings should cash out into explicit repair or review work.",
            "followup_specs": _targeted_followups(review),
        }
    if _is_sampled(review=review, sample_rate=sample_rate):
        return {
            "mode": "sampled",
            "reason": "Clean reviews are periodically spot-checked so verification and audit work remain auditable.",
            "followup_specs": [_sampled_followup(review)],
        }
    return {
        "mode": "none",
        "reason": "No follow-up audit work selected for this review.",
        "followup_specs": [],
    }


def select_signal_followup_policy(
    *,
    signal: ObservationSignal,
) -> dict[str, Any]:
    if signal.status != "open":
        return {
            "mode": "none",
            "reason": "Resolved signals do not emit new follow-up work.",
            "followup_specs": [],
        }
    return {
        "mode": "targeted",
        "reason": "Internal surprise/problem signals should cash out into bounded investigation or repair work.",
        "followup_specs": _signal_followups(signal),
    }


def _targeted_followups(review: AuditReview) -> list[dict[str, Any]]:
    severe_findings = [finding for finding in review.findings if finding.severity in {"high", "critical"}]
    top_finding = severe_findings[0] if severe_findings else review.findings[0]
    title_prefix = {
        "verification": "Repair verification path",
        "consensus_judgment": "Repair consensus judgment path",
        "audit_review": "Repair audit review path",
    }.get(str(review.subject_kind or "").strip(), "Repair reviewed system path")
    return [
        {
            "title": f"{title_prefix}: {review.subject_id}",
            "description": (
                f"Audit review for {review.subject_kind} `{review.subject_id}` found a problem that should be repaired or re-greened. "
                f"Primary finding: {top_finding.summary}"
            ),
            "priority": 7 if top_finding.severity in {"high", "critical"} else 5,
            "urgency": 4 if top_finding.severity in {"high", "critical"} else 2,
            "risk": "moderate",
            "completion_type": "review_or_audit",
            "success_criteria": {"audit_findings_resolved": True},
            "task_id_hint": f"audit-repair-{review.subject_kind}-{review.subject_id}",
            "route_preferences": {"preferred_cli_tools": ["kilocode", "gemini-cli"]},
        }
    ]


def _sampled_followup(review: AuditReview) -> dict[str, Any]:
    return {
        "title": f"Spot-check {review.subject_kind}: {review.subject_id}",
        "description": (
            f"Perform a bounded spot-check of the clean {review.subject_kind} review for `{review.subject_id}` to keep audit and verification work calibrated."
        ),
        "priority": 3,
        "urgency": 1,
        "risk": "low",
        "completion_type": "review_or_audit",
        "success_criteria": {"spot_check_completed": True},
        "task_id_hint": f"audit-sample-{review.subject_kind}-{review.subject_id}",
        "route_preferences": {"preferred_cli_tools": ["kilocode", "gemini-cli"]},
    }


def _signal_followups(signal: ObservationSignal) -> list[dict[str, Any]]:
    title_prefix = {
        "surprise": "Investigate surprising system behavior",
        "problem": "Repair observed system problem",
        "drift": "Correct detected system drift",
        "opportunity": "Explore system improvement opportunity",
    }.get(signal.signal_kind, "Investigate internal system signal")
    default_priority = 7 if signal.severity in {"high", "critical"} else 5
    default_urgency = 4 if signal.severity in {"high", "critical"} else 2
    if signal.signal_kind == "opportunity":
        default_priority = min(default_priority, 4)
        default_urgency = min(default_urgency, 2)
    return [
        {
            "title": f"{title_prefix}: {signal.subject_id}",
            "description": (
                f"Internal {signal.signal_kind} signal for {signal.subject_kind} `{signal.subject_id}` should be investigated and either repaired, "
                f"explained, or deliberately ratified. Summary: {signal.summary}"
            ),
            "priority": default_priority,
            "urgency": default_urgency,
            "risk": "moderate",
            "completion_type": "review_or_audit",
            "success_criteria": {"signal_addressed": True, "signal_id": signal.signal_id},
            "task_id_hint": f"signal-{signal.signal_kind}-{signal.subject_kind}-{signal.subject_id}",
            "route_preferences": {"preferred_cli_tools": ["kilocode", "gemini-cli"]},
        }
    ]


def _is_sampled(*, review: AuditReview, sample_rate: int) -> bool:
    normalized_rate = max(1, int(sample_rate or 1))
    subject = f"{review.subject_kind}:{review.subject_id}"
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % normalized_rate == 0
