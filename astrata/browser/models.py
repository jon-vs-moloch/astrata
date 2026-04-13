"""Models for Astrata's internal browser substrate."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BrowserPageSnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: f"snapshot-{uuid4()}")
    session_id: str
    requested_url: str
    final_url: str
    title: str = ""
    selector: str | None = None
    wait_ms: int = 350
    viewport: dict[str, int] = Field(default_factory=dict)
    full_page: bool = False
    screenshot_path: str | None = None
    html_path: str | None = None
    readable_text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)


class BrowserSession(BaseModel):
    session_id: str = Field(default_factory=lambda: f"browser-session-{uuid4()}")
    label: str = ""
    start_url: str = ""
    last_url: str = ""
    status: str = "idle"
    latest_snapshot_id: str | None = None
    snapshot_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class BrowserInteractionRecord(BaseModel):
    interaction_id: str = Field(default_factory=lambda: f"interaction-{uuid4()}")
    session_id: str
    action: str
    selector: str | None = None
    text: str | None = None
    delta_y: int | None = None
    requested_url: str = ""
    final_url: str = ""
    title: str = ""
    screenshot_path: str | None = None
    html_path: str | None = None
    readable_text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
