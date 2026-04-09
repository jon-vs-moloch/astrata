"""Communication record models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommunicationRecord(BaseModel):
    communication_id: str = Field(default_factory=lambda: str(uuid4()))
    conversation_id: str = ""
    channel: str
    kind: str = "notice"
    sender: str
    recipient: str
    intent: str = ""
    status: Literal["queued", "delivered", "acknowledged", "resolved"] = "queued"
    priority: int = 0
    urgency: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    related_task_ids: list[str] = Field(default_factory=list)
    related_attempt_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now_iso)
    delivered_at: str | None = None
    acknowledged_at: str | None = None
    resolved_at: str | None = None
