"""Models for variants, experiments, and bounded experimentation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VariantDefinition(BaseModel):
    """Definition of a variant, specifying its changes and metadata."""

    variant_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str = ""
    changes: list[dict[str, Any]] = Field(default_factory=list)
    strategy: str  # e.g., "prompt", "route", "config"
    created_at: str = Field(default_factory=_now_iso)


class Experiment(BaseModel):
    """An experiment comparing multiple variants."""

    experiment_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str = ""
    variant_ids: list[str] = Field(default_factory=list)
    status: Literal["active", "completed", "cancelled"] = "active"
    created_at: str = Field(default_factory=_now_iso)
    completed_at: str | None = None


class VariantRecord(BaseModel):
    """Record of a variant applied to a subject."""

    record_id: str = Field(default_factory=lambda: str(uuid4()))
    variant_id: str
    experiment_id: str | None = None
    subject_kind: str
    subject_id: str
    strategy: str
    status: Literal["candidate", "active", "retired"] = "candidate"
    notes: str = ""
    created_at: str = Field(default_factory=_now_iso)