"""Routine records for regularly scheduled Procedure work."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RoutineRecord(BaseModel):
    routine_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    procedure_id: str
    cadence: str
    command: list[str] = Field(default_factory=list)
    status: Literal["active", "paused", "retired"] = "active"
    last_run_at: str | None = None
    next_run_hint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

