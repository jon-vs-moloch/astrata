"""Diagnostics helpers for analyzing audit findings and reviews."""

from __future__ import annotations

from collections import Counter

from astrata.audit.review import AuditReview, ReviewFinding


def summarize_findings(findings: list[ReviewFinding]) -> str:
    """Generate a simple text summary of audit findings."""
    if not findings:
        return "No findings."
    return "; ".join(finding.summary for finding in findings)


def count_findings_by_severity(findings: list[ReviewFinding]) -> dict[str, int]:
    """Count findings grouped by severity level."""
    return dict(Counter(f.severity for f in findings))


def average_severity_score(findings: list[ReviewFinding]) -> float:
    """Compute average severity score (1=low, 2=moderate, 3=high, 4=critical)."""
    severity_map = {"low": 1, "moderate": 2, "high": 3, "critical": 4}
    if not findings:
        return 0.0
    scores = [severity_map.get(f.severity, 2) for f in findings]
    return sum(scores) / len(scores)


def validate_findings(findings: list[ReviewFinding]) -> list[str]:
    """Check findings for common issues and return list of validation problems."""
    issues = []
    for finding in findings:
        if not finding.summary.strip():
            issues.append(f"Finding {finding.finding_id}: missing or empty summary")
        if not finding.evidence:
            issues.append(f"Finding {finding.finding_id}: no evidence provided")
    return issues


class AuditDiagnostics:
    """Diagnostic analysis for a single audit review."""

    def __init__(self, review: AuditReview) -> None:
        self.review = review

    def get_basic_stats(self) -> dict[str, int | float | dict[str, int]]:
        """Return basic statistics about the review's findings."""
        findings = self.review.findings
        return {
            "total_findings": len(findings),
            "severity_counts": count_findings_by_severity(findings),
            "average_severity_score": average_severity_score(findings),
        }

    def get_validation_report(self) -> dict[str, list[str]]:
        """Return validation issues for the review."""
        return {"validation_issues": validate_findings(self.review.findings)}

    def generate_report(self) -> str:
        """Generate a comprehensive text report for the audit review."""
        stats = self.get_basic_stats()
        validation = self.get_validation_report()
        lines = [
            f"Audit Review Report for {self.review.subject_kind} '{self.review.subject_id}'",
            f"Status: {self.review.status}",
            f"Summary: {self.review.summary}",
            "",
            "Findings Statistics:",
            f"  Total findings: {stats['total_findings']}",
            f"  Severity breakdown: {stats['severity_counts']}",
            f"  Average severity score: {stats['average_severity_score']:.2f}",
            "",
            "Findings Summary:",
            f"  {summarize_findings(self.review.findings)}",
        ]
        if validation["validation_issues"]:
            lines.extend(
                [
                    "",
                    "Validation Issues:",
                    *[f"  - {issue}" for issue in validation["validation_issues"]],
                ]
            )
        return "\n".join(lines)