"""Audit helpers for reviewing cheap-lane consensus judgments."""

from __future__ import annotations

from astrata.audit.review import AuditReview, ReviewFinding, open_review


def review_consensus_judgment(
    *,
    task_id: str,
    consensus: dict[str, object],
) -> AuditReview:
    findings: list[ReviewFinding] = []
    required_reviews = max(2, int(consensus.get("required_reviews") or 2))
    results = list(consensus.get("results") or [])
    status = str(consensus.get("status") or "").strip().lower()
    successful = [item for item in results if str(item.get("status") or "").strip().lower() == "applied"]
    normalized = [_normalize_text(item.get("principal_response")) for item in successful]
    distinct_responses = sorted({item for item in normalized if item})

    if status == "approved" and len(successful) < required_reviews:
        findings.append(
            ReviewFinding(
                severity="critical",
                summary="Consensus was approved without enough successful worker reviews.",
                evidence={
                    "required_reviews": required_reviews,
                    "successful_reviews": len(successful),
                },
            )
        )
    if status == "approved" and len(distinct_responses) > 1:
        findings.append(
            ReviewFinding(
                severity="critical",
                summary="Consensus was approved even though cheap workers disagreed on the principal response.",
                evidence={
                    "responses": distinct_responses,
                    "required_reviews": required_reviews,
                },
            )
        )
    if status == "disagreement" and len(successful) >= required_reviews and len(distinct_responses) <= 1:
        findings.append(
            ReviewFinding(
                severity="high",
                summary="Consensus was marked disagreement even though the available worker reviews agree.",
                evidence={
                    "responses": distinct_responses,
                    "required_reviews": required_reviews,
                    "successful_reviews": len(successful),
                },
            )
        )
    if status in {"approved", "disagreement"} and not results:
        findings.append(
            ReviewFinding(
                severity="high",
                summary="Consensus reached a terminal judgment without preserving worker review evidence.",
                evidence={"status": status},
            )
        )
    worker_ids = [str(item).strip() for item in list(consensus.get("worker_ids") or []) if str(item).strip()]
    if len(worker_ids) >= 2 and len(set(worker_ids)) != len(worker_ids):
        findings.append(
            ReviewFinding(
                severity="moderate",
                summary="Consensus review reused the same worker identity more than once.",
                evidence={"worker_ids": worker_ids},
            )
        )

    summary = (
        "Consensus judgment is internally consistent with the preserved worker evidence."
        if not findings
        else "Consensus judgment requires review because it conflicts with preserved worker evidence."
    )
    review = open_review(
        subject_kind="consensus_judgment",
        subject_id=task_id,
        summary=summary,
        findings=findings,
    )
    review.status = "resolved" if not findings else "open"
    return review


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())
