"""Meta-review helpers for auditing audit reviews themselves."""

from __future__ import annotations

from astrata.audit.review import AuditReview, ReviewFinding, open_review


def review_audit_review(*, review: AuditReview) -> AuditReview:
    findings: list[ReviewFinding] = []
    if not str(review.subject_kind or "").strip() or not str(review.subject_id or "").strip():
        findings.append(
            ReviewFinding(
                severity="high",
                summary="Audit review is missing a subject reference.",
                evidence={
                    "subject_kind": review.subject_kind,
                    "subject_id": review.subject_id,
                },
            )
        )
    if review.status == "resolved" and review.findings:
        findings.append(
            ReviewFinding(
                severity="high",
                summary="Audit review is marked resolved even though it still contains findings.",
                evidence={
                    "status": review.status,
                    "finding_count": len(review.findings),
                },
            )
        )
    if review.status == "open" and not review.findings:
        findings.append(
            ReviewFinding(
                severity="moderate",
                summary="Audit review is open but does not contain any findings.",
                evidence={"status": review.status},
            )
        )
    if review.status == "resolved" and any(finding.severity in {"high", "critical"} for finding in review.findings):
        findings.append(
            ReviewFinding(
                severity="critical",
                summary="Audit review resolved despite high-severity findings.",
                evidence={
                    "status": review.status,
                    "severities": [finding.severity for finding in review.findings],
                },
            )
        )

    summary = (
        "Audit review is structurally self-consistent."
        if not findings
        else "Audit review requires review because its own state is internally inconsistent."
    )
    meta_review = open_review(
        subject_kind="audit_review",
        subject_id=review.review_id,
        summary=summary,
        findings=findings,
    )
    meta_review.status = "resolved" if not findings else "open"
    return meta_review
