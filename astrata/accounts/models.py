"""Account and hosted-bridge control-plane records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccountUserRecord(BaseModel):
    user_id: str = Field(default_factory=lambda: str(uuid4()))
    email: str
    display_name: str = ""
    hosted_bridge_eligible: bool = False
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class InviteCodeRecord(BaseModel):
    code: str
    label: str = ""
    status: str = "open"
    redeemed_by_user_id: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    redeemed_at: str | None = None


class RelayProfileRecord(BaseModel):
    profile_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str | None = None
    label: str = "Default"
    control_posture: str = "local_prime_delegate"
    disclosure_tier: str = "connector_safe"
    default_device_id: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AccountDeviceRecord(BaseModel):
    device_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    label: str = "Astrata Desktop"
    device_kind: str = "desktop"
    status: str = "active"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    last_seen_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeviceLinkRecord(BaseModel):
    link_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    profile_id: str
    device_id: str
    relay_endpoint: str = ""
    link_token_hash: str
    status: str = "active"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    last_seen_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OAuthClientRecord(BaseModel):
    client_id: str = Field(default_factory=lambda: str(uuid4()))
    label: str
    client_kind: str = "chatgpt_connector"
    redirect_uris: tuple[str, ...] = ()
    status: str = "active"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OAuthAuthorizationCodeRecord(BaseModel):
    code: str
    client_id: str
    user_id: str
    profile_id: str
    device_id: str
    redirect_uri: str = ""
    scope: tuple[str, ...] = ("relay:use",)
    status: str = "open"
    created_at: str = Field(default_factory=_now_iso)
    expires_at: str
    redeemed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OAuthAccessTokenRecord(BaseModel):
    token_hash: str
    client_id: str
    user_id: str
    profile_id: str
    device_id: str
    scope: tuple[str, ...] = ("relay:use",)
    status: str = "active"
    created_at: str = Field(default_factory=_now_iso)
    expires_at: str
    revoked_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
