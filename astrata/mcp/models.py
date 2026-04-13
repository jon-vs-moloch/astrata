"""Models for Astrata's inbound, outbound, and hosted MCP bridge layers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MCPBridgeBinding(BaseModel):
    """Describes one MCP relationship crossing the Astrata boundary."""

    bridge_id: str = Field(default_factory=lambda: str(uuid4()))
    direction: Literal["inbound", "outbound"]
    transport: Literal["stdio", "streamable_http"] = "streamable_http"
    agent_id: str
    role: Literal["prime", "assistant", "worker", "peer"] = "peer"
    server_label: str = ""
    endpoint: str = ""
    command: tuple[str, ...] = ()
    can_be_prime: bool = False
    can_receive_subtasks: bool = True
    accepts_sensitive_payloads: bool = False
    online: bool = True
    auth_mode: str = "none"
    allowed_tools: tuple[str, ...] = ()
    exposed_resources: tuple[str, ...] = ()
    notes: str = ""
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class MCPBridgeEvent(BaseModel):
    """Durable event describing a protocol-level bridge action."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    bridge_id: str
    direction: Literal["inbound", "outbound"]
    event_type: str
    task_id: str = ""
    tool_name: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)


class HostedMCPRelayProfile(BaseModel):
    """Defines one hosted connector-facing MCP profile."""

    profile_id: str = Field(default_factory=lambda: str(uuid4()))
    label: str
    exposure: Literal["chatgpt", "gemini", "claude", "generic"] = "generic"
    control_posture: Literal["true_remote_prime", "peer", "local_prime_delegate", "local_prime_customer"] = (
        "local_prime_delegate"
    )
    local_prime_behavior: Literal["absent", "subordinate", "authoritative", "collaborative"] = "authoritative"
    remote_agent_id: str = ""
    relay_endpoint: str = ""
    auth_mode: str = "token"
    auth_token: str = ""
    allowed_tools: tuple[str, ...] = ()
    max_disclosure_tier: Literal["public", "connector_safe", "trusted_remote", "local_only", "enclave_only"] = (
        "connector_safe"
    )
    local_link_required: bool = True
    online: bool = True
    notes: str = ""
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class HostedMCPRelayLink(BaseModel):
    """Represents Astrata's outbound authenticated link to a hosted relay."""

    link_id: str = Field(default_factory=lambda: str(uuid4()))
    profile_id: str
    bridge_id: str = ""
    local_agent_id: str = "astrata-local"
    backend_url: str = ""
    status: Literal["online", "offline", "degraded"] = "offline"
    last_heartbeat_at: str = ""
    queue_depth: int = 0
    failure_reason: str = ""
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class HostedMCPRelayRequest(BaseModel):
    """Durable relay request record for queued or forwarded connector work."""

    request_id: str = Field(default_factory=lambda: str(uuid4()))
    profile_id: str
    tool_name: str
    task_id: str
    external_request_id: str = ""
    bridge_id: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    source_connector: str = ""
    target_controller: str = "prime"
    requested_disclosure_tier: Literal["public", "connector_safe", "trusted_remote", "local_only", "enclave_only"] = (
        "connector_safe"
    )
    triage_lane: str = ""
    triage_urgency: str = ""
    triage_action: str = ""
    triage_reason: str = ""
    triage_sla_seconds: int = 0
    requires_attention: bool = False
    triage_audit_tags: tuple[str, ...] = ()
    status: Literal["queued", "forwarded", "completed", "rejected"] = "queued"
    queue_reason: str = ""
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class HostedMCPRelayEvent(BaseModel):
    """Durable event describing hosted relay behavior and operator-visible telemetry."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    profile_id: str
    link_id: str = ""
    request_id: str = ""
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
