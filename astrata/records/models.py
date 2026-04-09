"""Core durable record types used by the Phase 0 loop."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskRecord(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    description: str
    priority: int = 0
    urgency: int = 0
    provenance: dict[str, Any] = Field(default_factory=dict)
    permissions: dict[str, Any] = Field(default_factory=dict)
    risk: str = "moderate"
    status: Literal["pending", "working", "blocked", "complete", "failed", "satisfied", "superseded"] = "pending"
    dependencies: list[str] = Field(default_factory=list)
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    completion_policy: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class AttemptRecord(BaseModel):
    attempt_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    actor: str
    provenance: dict[str, Any] = Field(default_factory=dict)
    attempt_reason: str = ""
    outcome: Literal["running", "succeeded", "failed", "blocked", "cancelled"] = "failed"
    result_summary: str = ""
    failure_kind: str | None = None
    degraded_reason: str | None = None
    verification_status: Literal["unverified", "passed", "failed", "uncertain"] = "unverified"
    audit_status: Literal["none", "open", "resolved"] = "none"
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    followup_actions: list[dict[str, Any]] = Field(default_factory=list)
    started_at: str = Field(default_factory=_now_iso)
    ended_at: str | None = None


class ArtifactRecord(BaseModel):
    artifact_id: str = Field(default_factory=lambda: str(uuid4()))
    artifact_type: str
    title: str
    description: str = ""
    content_summary: str = ""
    status: Literal["good", "degraded", "broken"] = "good"
    lifecycle_state: str = "draft"
    install_state: str = "proposed"
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class VerificationRecord(BaseModel):
    verification_id: str = Field(default_factory=lambda: str(uuid4()))
    target_kind: str
    target_id: str
    verifier: str
    result: Literal["pass", "fail", "uncertain"] = "uncertain"
    confidence: float = 0.0
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
