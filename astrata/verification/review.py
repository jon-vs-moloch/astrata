"""Verification review for checking whether the verifier agrees with reality."""

from __future__ import annotations

from pathlib import Path

from astrata.audit.review import AuditReview, ReviewFinding, open_review
from astrata.records.models import VerificationRecord
from astrata.verification.basic import inspect_expected_paths


def review_verification(
    *,
    project_root: Path,
    candidate_key: str,
    expected_paths: list[str],
    implementation: dict[str, object],
    verification: VerificationRecord,
) -> AuditReview:
    inspection = inspect_expected_paths(project_root, expected_paths)
    findings: list[ReviewFinding] = []

    verification_result = verification.result
    missing = inspection["missing"]
    syntax = inspection["python_syntax"]
    syntax_errors = {path: status for path, status in syntax.items() if status != "ok"}
    implementation_status = str(implementation.get("status") or "unknown")

    if implementation_status == "applied" and verification_result != "pass" and not missing and not syntax_errors:
        findings.append(
            ReviewFinding(
                severity="high",
                summary="Verifier rejected a candidate whose expected outputs exist and parse cleanly.",
                evidence={
                    "candidate_key": candidate_key,
                    "verification_result": verification_result,
                    "inspection": inspection,
                },
            )
        )

    if verification_result == "pass" and (missing or syntax_errors):
        findings.append(
            ReviewFinding(
                severity="critical",
                summary="Verifier passed a candidate despite missing files or syntax errors.",
                evidence={
                    "candidate_key": candidate_key,
                    "verification_result": verification_result,
                    "inspection": inspection,
                },
            )
        )

    if implementation_status != "applied" and verification_result == "pass":
        findings.append(
            ReviewFinding(
                severity="moderate",
                summary="Verifier passed a candidate even though the implementation did not report success.",
                evidence={
                    "candidate_key": candidate_key,
                    "implementation": implementation,
                    "verification_result": verification_result,
                },
            )
        )

    summary = (
        "Verification is internally consistent with filesystem reality."
        if not findings
        else "Verification requires review because its conclusion conflicts with observed reality."
    )
    review = open_review(
        subject_kind="verification",
        subject_id=verification.verification_id,
        summary=summary,
        findings=findings,
    )
    review.status = "resolved" if not findings else "open"
    return review
