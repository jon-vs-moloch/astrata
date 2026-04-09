"""Handoff record models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HandoffRecord(BaseModel):
    handoff_id: str = Field(default_factory=lambda: str(uuid4()))
    source_controller: str
    target_controller: str
    task_id: str
    status: Literal["queued", "accepted", "deferred", "blocked", "refused"] = "queued"
    reason: str = ""
    route: dict[str, Any] = Field(default_factory=dict)
    envelope: dict[str, Any] = Field(default_factory=dict)
    source_decision: dict[str, Any] = Field(default_factory=dict)
    target_decision: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
    responded_at: str | None = None
