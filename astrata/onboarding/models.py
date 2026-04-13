"""Durable onboarding records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OnboardingStepRecord(BaseModel):
    step_id: str
    title: str
    description: str
    category: Literal["inference", "security", "identity", "autonomy", "constellation", "other"] = "other"
    status: Literal["pending", "active", "complete", "blocked", "skipped"] = "pending"
    blocking: bool = True
    can_auto_advance: bool = False
    notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=_now_iso)


class OnboardingPlan(BaseModel):
    plan_id: str = "primary-onboarding"
    title: str = "Astrata Onboarding"
    description: str = "First-run setup and orientation for a new Astrata installation."
    status: Literal["pending", "active", "complete", "blocked"] = "pending"
    steps: list[OnboardingStepRecord] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
