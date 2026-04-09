"""Minimal diagnostics helpers for audit findings."""

from __future__ import annotations

from astrata.audit.review import ReviewFinding


def summarize_findings(findings: list[ReviewFinding]) -> str:
    if not findings:
        return "No findings."
    return "; ".join(finding.summary for finding in findings)
