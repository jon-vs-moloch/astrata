"""MCP bridge and hosted relay records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MCPBridgeBinding(BaseModel):
    bridge_id: str
    direction: Literal["inbound", "outbound"]
    transport: str
    agent_id: str
    role: str = "peer"
    endpoint: str | None = None
    can_be_prime: bool = False
    can_receive_subtasks: bool = True
    allowed_tools: tuple[str, ...] = ()
    exposed_resources: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class MCPBridgeEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    bridge_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)


class HostedMCPRelayProfile(BaseModel):
    profile_id: str
    label: str
    user_id: str | None = None
    default_device_id: str | None = None
    exposure: str = "chatgpt"
    control_posture: str = "local_prime_delegate"
    disclosure_tier: str = "connector_safe"
    auth_token: str | None = None
    allowed_tools: tuple[str, ...] = (
        "search",
        "fetch",
        "submit_task",
        "get_task_status",
        "list_capabilities",
        "message_prime",
    )
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class HostedMCPRelayLink(BaseModel):
    profile_id: str
    bridge_id: str
    device_id: str | None = None
    link_token: str | None = None
    status: Literal["online", "offline"] = "offline"
    last_heartbeat_at: str | None = None
    advertised_capabilities: dict[str, Any] = Field(default_factory=dict)


class HostedMCPRelayRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    profile_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    source_connector: str = "remote_connector"
    target_controller: str = "prime"
    task_id: str = ""
    session_id: str = ""
    status: Literal["queued", "acknowledged"] = "queued"
    created_at: str = Field(default_factory=_now_iso)
    acknowledged_at: str | None = None


class HostedMCPRelayResult(BaseModel):
    request_id: str
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)


class HostedMCPRelaySessionMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    request_id: str = ""
    sender: Literal["remote", "local"] = "remote"
    kind: str = "message"
    content: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)


class HostedMCPRelaySession(BaseModel):
    profile_id: str
    session_id: str
    messages: list[HostedMCPRelaySessionMessage] = Field(default_factory=list)
    remote_last_seen_at: str = ""
    local_last_seen_at: str = ""
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
