"""Durable-ish shapes for local runtime state."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RuntimeSelection(BaseModel):
    runtime_key: str = "default"
    backend_id: str
    model_id: str | None = None
    mode: Literal["managed", "external"] = "managed"
    profile_id: str | None = None
    endpoint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeHealthSnapshot(BaseModel):
    backend_id: str
    ok: bool
    status: str = "unknown"
    endpoint: str | None = None
    detail: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
