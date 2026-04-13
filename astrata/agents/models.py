"""Models for durable Astrata agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DurableAgentRecord(BaseModel):
    """A durable agent created or adopted by Astrata."""

    agent_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = ""
    title: str
    role: Literal["prime", "assistant", "worker", "local", "fallback"] = "assistant"
    persona_prompt: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    permissions_profile: dict[str, Any] = Field(default_factory=dict)
    inference_binding: dict[str, Any] = Field(default_factory=dict)
    message_policy: dict[str, Any] = Field(default_factory=dict)
    fallback_policy: dict[str, Any] = Field(default_factory=dict)
    allowed_recipients: list[str] = Field(default_factory=list)
    status: Literal["active", "degraded", "offline", "retired"] = "active"
    created_by: str = "system"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
