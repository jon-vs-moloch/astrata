"""Minimal audit review records for early disagreement handling."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewFinding(BaseModel):
    finding_id: str = Field(default_factory=lambda: str(uuid4()))
    severity: Literal["low", "moderate", "high", "critical"] = "moderate"
    summary: str
    evidence: dict[str, object] = Field(default_factory=dict)
    proposed_actions: list[dict[str, object]] = Field(default_factory=list)


class AuditReview(BaseModel):
    review_id: str = Field(default_factory=lambda: str(uuid4()))
    subject_kind: str
    subject_id: str
    status: Literal["open", "resolved"] = "open"
    summary: str = ""
    findings: list[ReviewFinding] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


def open_review(
    *,
    subject_kind: str,
    subject_id: str,
    summary: str,
    findings: list[ReviewFinding] | None = None,
) -> AuditReview:
    return AuditReview(
        subject_kind=subject_kind,
        subject_id=subject_id,
        summary=summary,
        findings=findings or [],
    )
