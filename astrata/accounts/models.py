"""Durable account and device models for Astrata Web's auth control plane."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AstrataUser(BaseModel):
    user_id: str = Field(default_factory=lambda: str(uuid4()))
    email: str
    display_name: str = ""
    status: Literal["active", "invited", "disabled"] = "active"
    hosted_bridge_eligibility: Literal["invite_required", "eligible", "active", "disabled"] = "invite_required"
    default_profile_id: str | None = None
    gpt_onboarded_at: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class AstrataAccountSession(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    auth_method: Literal["magic_link", "passkey", "oauth", "desktop_bootstrap"] = "magic_link"
    status: Literal["active", "revoked", "expired"] = "active"
    expires_at: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class AstrataRelayProfileRecord(BaseModel):
    profile_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    label: str
    control_posture: str = "true_remote_prime"
    disclosure_tier: str = "trusted_remote"
    allow_remote_host_bash: bool = False
    remote_host_bash_acknowledged_at: str | None = None
    remote_host_bash_warning: str = ""
    default_device_id: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class AstrataDeviceRecord(BaseModel):
    device_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    label: str
    platform: str = "desktop"
    status: Literal["active", "offline", "revoked"] = "active"
    last_seen_at: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class AstrataDeviceLink(BaseModel):
    link_id: str = Field(default_factory=lambda: str(uuid4()))
    device_id: str
    profile_id: str
    relay_endpoint: str = ""
    link_token_hash: str = ""
    status: Literal["active", "offline", "revoked"] = "active"
    last_heartbeat_at: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class AstrataOAuthClient(BaseModel):
    client_id: str = Field(default_factory=lambda: str(uuid4()))
    client_name: str
    redirect_uris: list[str] = Field(default_factory=list)
    token_endpoint_auth_method: str = "none"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class AstrataOAuthAuthorizationCode(BaseModel):
    code_id: str = Field(default_factory=lambda: str(uuid4()))
    client_id: str
    user_id: str
    profile_id: str
    device_id: str | None = None
    expires_at: str
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class AstrataOAuthAccessToken(BaseModel):
    token_id: str = Field(default_factory=lambda: str(uuid4()))
    client_id: str
    user_id: str
    profile_id: str
    device_id: str | None = None
    status: Literal["active", "revoked", "expired"] = "active"
    expires_at: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class AstrataGPTConnection(BaseModel):
    connection_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    profile_id: str
    oauth_client_id: str
    status: Literal["active", "revoked", "stale"] = "active"
    last_used_at: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class AstrataInviteCode(BaseModel):
    invite_id: str = Field(default_factory=lambda: str(uuid4()))
    code: str
    label: str = ""
    status: Literal["active", "redeemed", "expired", "disabled"] = "active"
    max_redemptions: int = 1
    redemption_count: int = 0
    expires_at: str | None = None
    redeemed_by_user_id: str | None = None
    redeemed_at: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class AstrataAccountState(BaseModel):
    version: int = 1
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    users: dict[str, AstrataUser] = Field(default_factory=dict)
    account_sessions: dict[str, AstrataAccountSession] = Field(default_factory=dict)
    relay_profiles: dict[str, AstrataRelayProfileRecord] = Field(default_factory=dict)
    devices: dict[str, AstrataDeviceRecord] = Field(default_factory=dict)
    device_links: dict[str, AstrataDeviceLink] = Field(default_factory=dict)
    oauth_clients: dict[str, AstrataOAuthClient] = Field(default_factory=dict)
    oauth_authorization_codes: dict[str, AstrataOAuthAuthorizationCode] = Field(default_factory=dict)
    oauth_access_tokens: dict[str, AstrataOAuthAccessToken] = Field(default_factory=dict)
    gpt_connections: dict[str, AstrataGPTConnection] = Field(default_factory=dict)
    invite_codes: dict[str, AstrataInviteCode] = Field(default_factory=dict)
