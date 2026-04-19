"""Durable chat thread metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatThreadRecord(BaseModel):
    thread_id: str = Field(default_factory=lambda: str(uuid4()))
    conversation_id: str
    title: str = ""
    chat_kind: Literal["agent", "model"] = "agent"
    agent_mode: Literal["persistent", "ephemeral", "temporary"] | None = "persistent"
    agent_id: str | None = None
    contact_id: str = "principal"
    provider_id: str | None = None
    model_id: str | None = None
    endpoint_runtime_key: str | None = None
    memory_policy: dict[str, Any] = Field(default_factory=dict)
    permissions_profile: dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "archived", "deleted"] = "active"
    created_by: str = "principal"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    archived_at: str | None = None
    deleted_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
