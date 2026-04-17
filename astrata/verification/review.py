"""Verification review for checking whether the verifier agrees with reality."""

from __future__ import annotations

from pathlib import Path

from astrata.audit.review import AuditReview, ReviewFinding, open_review
from astrata.records.models import VerificationRecord
from astrata.routing.prime_policy import prime_burden_summary
from astrata.verification.basic import inspect_expected_paths


def review_verification(
    *,
    project_root: Path,
    candidate_key: str,
    expected_paths: list[str],
    implementation: dict[str, object],
    verification: VerificationRecord,
    attempt: dict[str, object] | None = None,
    task_payload: dict[str, object] | None = None,
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

    if attempt is not None:
        burden = prime_burden_summary(attempt=attempt, task_payload=task_payload)
        if burden.get("unjustified_prime"):
            findings.append(
                ReviewFinding(
                    severity="moderate",
                    summary="Prime was invoked without a recorded admission basis; route policy should be reviewed and corrected.",
                    evidence={
                        "candidate_key": candidate_key,
                        "route": burden["route"],
                        "task_id": burden["task_id"],
                        "task_class": burden["task_class"],
                        "prime_admission_basis": burden.get("prime_admission_basis") or [],
                    },
                    proposed_actions=[
                        {
                            "type": "review_prime_routing",
                            "task_id": burden["task_id"],
                            "expected_basis": [
                                "direct_more_efficient",
                                "catastrophic_or_protected",
                                "opportunistic_course_correction",
                            ],
                        }
                    ],
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
