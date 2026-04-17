"""JSON-backed account/auth control-plane scaffold."""

from __future__ import annotations

import json
import secrets
import hashlib
import base64
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

from astrata.accounts.models import (
    AccountDeviceRecord,
    AccountUserRecord,
    DeviceLinkRecord,
    InviteCodeRecord,
    OAuthAccessTokenRecord,
    OAuthAuthorizationCodeRecord,
    OAuthClientRecord,
    RelayProfileRecord,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _normalize_oauth_scope(scope: Any) -> tuple[str, ...]:
    requested: list[str] = []
    if isinstance(scope, str):
        requested = [item.strip() for item in scope.split() if item.strip()]
    elif isinstance(scope, (list, tuple, set)):
        requested = [str(item).strip() for item in scope if str(item).strip()]
    translated: list[str] = []
    for item in requested:
        if item in {"astrata:read", "astrata:write"}:
            item = "relay:use"
        if item not in translated:
            translated.append(item)
    if not translated:
        translated = ["relay:use"]
    return tuple(translated)


def _parse_chatgpt_callback(value: str) -> tuple[str, str] | None:
    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"https", "http"}:
        return None
    if parsed.hostname not in {"chatgpt.com", "chat.openai.com"}:
        return None
    parts = [part for part in str(parsed.path or "").split("/") if part]
    if len(parts) < 4 or parts[0] != "aip" or not parts[1].startswith("g-") or parts[-2:] != ["oauth", "callback"]:
        return None
    core_id = parts[1].split("-", 2)
    return parsed.hostname, "-".join(core_id[:2])


def _oauth_redirect_uris_match(expected: str, actual: str) -> bool:
    if str(expected or "").strip() == str(actual or "").strip():
        return True
    expected_callback = _parse_chatgpt_callback(expected)
    actual_callback = _parse_chatgpt_callback(actual)
    return bool(expected_callback and actual_callback and expected_callback == actual_callback)


def _pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(str(verifier or "").encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class AccountControlPlaneRegistry:
    def __init__(self, *, state_path: Path) -> None:
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls, settings) -> "AccountControlPlaneRegistry":
        return cls(state_path=settings.paths.data_dir / "account_control_plane.json")

    def access_policy(self) -> dict[str, Any]:
        policy = {
            "policy_rule": "download/install is public; hosted bridge activation is invite-gated",
            "public_access": {
                "download": True,
                "desktop_install": True,
                "local_first_onboarding": True,
                "local_onboarding": True,
                "public_registry_api": True,
            },
            "invite_gated": {
                "hosted_bridge_activation": True,
                "gpt_oauth": True,
                "remote_queue_usage": True,
            },
        }
        policy["invite_gated_access"] = {
            "hosted_account_activation": True,
            "gpt_bridge_sign_in": True,
            "relay_profile_activation": True,
            "remote_queue_usage": True,
            "hosted_control_plane_features": True,
        }
        return policy

    def hosted_bridge_eligibility(self, *, email: str | None = None) -> dict[str, Any]:
        if email:
            user = self.user_for_email(email)
            if user and user.hosted_bridge_eligible:
                return {"status": "eligible", "invite_required": False, "user_id": user.user_id}
        return {"status": "invite_required", "invite_required": True}

    def summary(self) -> dict[str, Any]:
        payload = self._load()
        users = list((payload.get("users") or {}).values())
        invites = list((payload.get("invites") or {}).values())
        profiles = list((payload.get("relay_profiles") or {}).values())
        devices = list((payload.get("devices") or {}).values())
        links = list((payload.get("device_links") or {}).values())
        clients = list((payload.get("oauth_clients") or {}).values())
        tokens = list((payload.get("oauth_access_tokens") or {}).values())
        return {
            "status": "in_progress",
            "current_bootstrap": {
                "invite_required_for_hosted_bridge": True,
                "pairing_is_not_identity": True,
                "device_links_require_account_ownership": True,
            },
            "counts": {
                "users": len(users),
                "open_invites": sum(1 for item in invites if item.get("status") == "open"),
                "relay_profiles": len(profiles),
                "devices": len(devices),
                "active_device_links": sum(1 for item in links if item.get("status") == "active"),
                "oauth_clients": len(clients),
                "active_oauth_tokens": sum(1 for item in tokens if item.get("status") == "active"),
            },
            "hosted_bridge_eligibility": self.hosted_bridge_eligibility(),
        }

    def schema_manifest(self) -> dict[str, Any]:
        return {
            "status": "in_progress",
            "records": [
                "users",
                "account_sessions",
                "relay_profiles",
                "devices",
                "device_links",
                "oauth_clients",
                "oauth_authorization_codes",
                "oauth_access_tokens",
                "gpt_connections",
                "relay_requests",
                "relay_results",
            ],
            "routing_rule": "access token -> user account -> relay profile -> selected device/link -> permitted tools",
        }

    def issue_invite_code(self, *, label: str = "") -> dict[str, Any]:
        payload = self._load()
        invites = dict(payload.get("invites") or {})
        code = secrets.token_urlsafe(12)
        invite = InviteCodeRecord(code=code, label=label)
        invites[code] = invite.model_dump(mode="json")
        payload["invites"] = invites
        self._save(payload)
        return {"status": "ok", "invite": invite.model_dump(mode="json")}

    def redeem_invite_code(
        self,
        *,
        email: str,
        display_name: str = "",
        invite_code: str | None = None,
        code: str | None = None,
    ) -> dict[str, Any]:
        normalized_email = str(email or "").strip().lower()
        payload = self._load()
        invites = dict(payload.get("invites") or {})
        invite_value = str(invite_code or code or "").strip()
        raw_invite = invites.get(invite_value)
        if not isinstance(raw_invite, dict) or raw_invite.get("status") != "open":
            return {"status": "invalid_invite", "hosted_bridge_eligibility": self.hosted_bridge_eligibility()}
        users = dict(payload.get("users") or {})
        existing = next((item for item in users.values() if item.get("email") == normalized_email), None)
        user = AccountUserRecord(
            user_id=str((existing or {}).get("user_id") or ""),
            email=normalized_email,
            display_name=display_name or str((existing or {}).get("display_name") or ""),
            hosted_bridge_eligible=True,
            created_at=str((existing or {}).get("created_at") or _now_iso()),
            updated_at=_now_iso(),
        )
        if not user.user_id:
            user = user.model_copy(update={"user_id": secrets.token_hex(16)})
        users[user.user_id] = user.model_dump(mode="json")
        raw_invite["status"] = "redeemed"
        raw_invite["redeemed_by_user_id"] = user.user_id
        raw_invite["redeemed_at"] = _now_iso()
        invites[invite_value] = raw_invite
        profiles = dict(payload.get("relay_profiles") or {})
        if not any(item.get("user_id") == user.user_id for item in profiles.values()):
            profile = RelayProfileRecord(user_id=user.user_id, label=f"{user.display_name or user.email} default")
            profiles[profile.profile_id] = profile.model_dump(mode="json")
        payload["users"] = users
        payload["invites"] = invites
        payload["relay_profiles"] = profiles
        self._save(payload)
        return {
            "status": "ok",
            "user": user.model_dump(mode="json"),
            "hosted_bridge_eligibility": self.hosted_bridge_eligibility(email=normalized_email),
        }

    def register_device(
        self,
        *,
        email: str | None = None,
        user_id: str | None = None,
        label: str = "Astrata Desktop",
        device_kind: str = "desktop",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        user = self._resolve_user(email=email, user_id=user_id)
        if user is None:
            return {"status": "unknown_user"}
        if not user.hosted_bridge_eligible:
            return {"status": "hosted_bridge_not_enabled", "user_id": user.user_id}
        payload = self._load()
        devices = dict(payload.get("devices") or {})
        device = AccountDeviceRecord(
            user_id=user.user_id,
            label=label or "Astrata Desktop",
            device_kind=device_kind or "desktop",
            metadata=dict(metadata or {}),
        )
        devices[device.device_id] = device.model_dump(mode="json")
        payload["devices"] = devices
        self._save(payload)
        return {"status": "ok", "device": device.model_dump(mode="json")}

    def register_desktop_device(
        self,
        *,
        email: str,
        device_label: str = "Astrata Desktop",
        profile_id: str | None = None,
        relay_endpoint: str = "",
        display_name: str = "",
    ) -> dict[str, Any]:
        normalized_email = str(email or "").strip().lower()
        payload = self._load()
        users = dict(payload.get("users") or {})
        existing = next((item for item in users.values() if item.get("email") == normalized_email), None)
        user = (
            AccountUserRecord(**existing)
            if isinstance(existing, dict)
            else AccountUserRecord(email=normalized_email, display_name=display_name, hosted_bridge_eligible=False)
        )
        if display_name and not user.display_name:
            user = user.model_copy(update={"display_name": display_name, "updated_at": _now_iso()})
        users[user.user_id] = user.model_dump(mode="json")
        payload["users"] = users

        profiles = dict(payload.get("relay_profiles") or {})
        resolved_profile: RelayProfileRecord | None = None
        if profile_id:
            raw_profile = profiles.get(profile_id)
            if isinstance(raw_profile, dict):
                resolved_profile = RelayProfileRecord(**raw_profile)
        if resolved_profile is None:
            if profile_id:
                resolved_profile = RelayProfileRecord(
                    profile_id=profile_id,
                    user_id=user.user_id,
                    label=f"{user.display_name or user.email} default",
                )
            else:
                resolved_profile = self.default_relay_profile_for_user(user.user_id)
        if resolved_profile is None:
            resolved_profile = self.default_relay_profile_for_user(user.user_id)
        if resolved_profile is None:
            resolved_profile = RelayProfileRecord(user_id=user.user_id, label=f"{user.display_name or user.email} default")
        elif resolved_profile.user_id is None:
            resolved_profile = resolved_profile.model_copy(update={"user_id": user.user_id, "updated_at": _now_iso()})
        profiles[resolved_profile.profile_id] = resolved_profile.model_dump(mode="json")
        payload["relay_profiles"] = profiles
        self._save(payload)

        payload = self._load()
        devices = dict(payload.get("devices") or {})
        device = AccountDeviceRecord(
            user_id=user.user_id,
            label=device_label or "Astrata Desktop",
            device_kind="desktop",
            metadata={"registered_via": "desktop_bootstrap"},
        )
        devices[device.device_id] = device.model_dump(mode="json")
        payload["devices"] = devices
        self._save(payload)
        link_result = self.link_device_to_profile(
            device_id=device.device_id,
            profile_id=resolved_profile.profile_id,
            relay_endpoint=relay_endpoint,
            metadata={"paired_by": "desktop_bootstrap"},
        )
        profile = self.get_profile(resolved_profile.profile_id) or resolved_profile
        return {
            "status": link_result.get("status"),
            "user": user.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "device": device.model_dump(mode="json"),
            "device_link": link_result.get("device_link"),
            "link_token": link_result.get("link_token"),
            "hosted_bridge_eligibility": self.hosted_bridge_eligibility(email=normalized_email),
            "access_policy": self.access_policy(),
            "remote_host_bash": self.remote_host_bash_status(profile_id=profile.profile_id),
        }

    def link_device_to_profile(
        self,
        *,
        device_id: str,
        profile_id: str,
        relay_endpoint: str = "",
        link_token: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._load()
        devices = dict(payload.get("devices") or {})
        profiles = dict(payload.get("relay_profiles") or {})
        raw_device = devices.get(device_id)
        raw_profile = profiles.get(profile_id)
        if not isinstance(raw_device, dict):
            return {"status": "unknown_device", "device_id": device_id}
        if not isinstance(raw_profile, dict):
            return {"status": "unknown_profile", "profile_id": profile_id}
        if raw_device.get("status") != "active":
            return {"status": "inactive_device", "device_id": device_id}
        if raw_profile.get("user_id") != raw_device.get("user_id"):
            return {"status": "ownership_mismatch", "device_id": device_id, "profile_id": profile_id}

        raw_token = str(link_token or secrets.token_urlsafe(32))
        link = DeviceLinkRecord(
            user_id=str(raw_device["user_id"]),
            profile_id=profile_id,
            device_id=device_id,
            relay_endpoint=relay_endpoint,
            link_token_hash=_hash_secret(raw_token),
            metadata=dict(metadata or {}),
        )
        links = dict(payload.get("device_links") or {})
        links[link.link_id] = link.model_dump(mode="json")
        raw_profile["default_device_id"] = device_id
        raw_profile["updated_at"] = _now_iso()
        profiles[profile_id] = raw_profile
        payload["relay_profiles"] = profiles
        payload["device_links"] = links
        self._save(payload)
        return {"status": "ok", "device_link": link.model_dump(mode="json"), "link_token": raw_token}

    def pair_device_for_user(
        self,
        *,
        email: str,
        label: str = "Astrata Desktop",
        device_kind: str = "desktop",
        relay_endpoint: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        user = self.user_for_email(email)
        if user is None:
            return {"status": "unknown_user"}
        profile = self.default_relay_profile_for_user(user.user_id)
        if profile is None:
            return {"status": "missing_relay_profile", "user_id": user.user_id}
        device_result = self.register_device(
            user_id=user.user_id,
            label=label,
            device_kind=device_kind,
            metadata=metadata,
        )
        if device_result.get("status") != "ok":
            return device_result
        link_result = self.link_device_to_profile(
            device_id=str(device_result["device"]["device_id"]),
            profile_id=profile.profile_id,
            relay_endpoint=relay_endpoint,
            metadata={"paired_by": "local_desktop", **dict(metadata or {})},
        )
        return {
            "status": link_result.get("status"),
            "user": user.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "device": device_result.get("device"),
            "device_link": link_result.get("device_link"),
            "link_token": link_result.get("link_token"),
        }

    def default_relay_profile_for_user(self, user_id: str) -> RelayProfileRecord | None:
        for raw in dict(self._load().get("relay_profiles") or {}).values():
            if isinstance(raw, dict) and raw.get("user_id") == user_id:
                return RelayProfileRecord(**raw)
        return None

    def get_profile(self, profile_id: str) -> RelayProfileRecord | None:
        raw = dict(self._load().get("relay_profiles") or {}).get(profile_id)
        return RelayProfileRecord(**raw) if isinstance(raw, dict) else None

    def list_profiles(self) -> list[RelayProfileRecord]:
        profiles = [
            RelayProfileRecord(**raw)
            for raw in dict(self._load().get("relay_profiles") or {}).values()
            if isinstance(raw, dict)
        ]
        return sorted(profiles, key=lambda item: item.updated_at, reverse=True)

    def get_device(self, device_id: str) -> AccountDeviceRecord | None:
        raw = dict(self._load().get("devices") or {}).get(device_id)
        return AccountDeviceRecord(**raw) if isinstance(raw, dict) else None

    def device_links_for_profile(self, profile_id: str) -> list[DeviceLinkRecord]:
        profile = self.get_profile(profile_id)
        links = []
        for raw in dict(self._load().get("device_links") or {}).values():
            if isinstance(raw, dict) and raw.get("profile_id") == profile_id:
                hydrated = dict(raw)
                hydrated.setdefault("user_id", None if profile is None else profile.user_id)
                links.append(DeviceLinkRecord(**hydrated))
        return sorted(links, key=lambda item: item.updated_at, reverse=True)

    def active_device_link_for_profile(self, profile_id: str) -> DeviceLinkRecord | None:
        for link in self.device_links_for_profile(profile_id):
            if link.status == "active":
                return link
        return None

    def remote_host_bash_status(self, *, profile_id: str) -> dict[str, Any]:
        profile = self.get_profile(profile_id)
        metadata = {} if profile is None else dict(profile.metadata or {})
        enabled = bool(metadata.get("remote_host_bash_enabled"))
        return {
            "enabled": enabled,
            "requires_special_acknowledgement": True,
            "acknowledged_at": metadata.get("remote_host_bash_acknowledged_at"),
            "profile_id": profile_id,
        }

    def set_remote_host_bash(self, *, profile_id: str, enabled: bool) -> dict[str, Any]:
        payload = self._load()
        profiles = dict(payload.get("relay_profiles") or {})
        raw_profile = profiles.get(profile_id)
        if not isinstance(raw_profile, dict):
            return {"status": "unknown_profile", "profile_id": profile_id}
        profile = RelayProfileRecord(**raw_profile)
        metadata = dict(profile.metadata or {})
        metadata["remote_host_bash_enabled"] = bool(enabled)
        metadata["remote_host_bash_acknowledged_at"] = _now_iso() if enabled else None
        profile = profile.model_copy(update={"metadata": metadata, "updated_at": _now_iso()})
        profiles[profile.profile_id] = profile.model_dump(mode="json")
        payload["relay_profiles"] = profiles
        self._save(payload)
        return {
            "status": "ok",
            "profile": profile.model_dump(mode="json"),
            "remote_host_bash": self.remote_host_bash_status(profile_id=profile.profile_id),
        }

    def verify_device_link(
        self,
        *,
        profile_id: str,
        device_id: str | None = None,
        link_token: str | None = None,
    ) -> dict[str, Any]:
        payload = self._load()
        profiles = dict(payload.get("relay_profiles") or {})
        raw_profile = profiles.get(profile_id)
        if not isinstance(raw_profile, dict):
            return {"status": "unknown_profile", "authorized": False}
        devices = dict(payload.get("devices") or {})
        links = self.device_links_for_profile(profile_id)
        for link in links:
            if link.status != "active":
                continue
            if device_id and link.device_id != device_id:
                continue
            raw_device = devices.get(link.device_id)
            if not isinstance(raw_device, dict) or raw_device.get("status") != "active":
                continue
            if raw_device.get("user_id") != raw_profile.get("user_id"):
                continue
            if link_token and link.link_token_hash != _hash_secret(link_token):
                continue
            return {
                "status": "ok",
                "authorized": True,
                "profile_id": profile_id,
                "device_id": link.device_id,
                "link_id": link.link_id,
            }
        return {"status": "no_active_owned_device_link", "authorized": False, "profile_id": profile_id}

    def register_oauth_client(
        self,
        *,
        label: str,
        redirect_uris: tuple[str, ...] | list[str] = (),
        client_kind: str = "chatgpt_connector",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._load()
        clients = dict(payload.get("oauth_clients") or {})
        client = OAuthClientRecord(
            label=label,
            client_kind=client_kind,
            redirect_uris=tuple(str(uri).strip() for uri in redirect_uris if str(uri).strip()),
            metadata=dict(metadata or {}),
        )
        clients[client.client_id] = client.model_dump(mode="json")
        payload["oauth_clients"] = clients
        self._save(payload)
        return {"status": "ok", "client": client.model_dump(mode="json")}

    def list_oauth_clients(self) -> list[dict[str, Any]]:
        clients = [
            OAuthClientRecord(**raw).model_dump(mode="json")
            for raw in dict(self._load().get("oauth_clients") or {}).values()
            if isinstance(raw, dict)
        ]
        return sorted(clients, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def list_oauth_access_tokens(self) -> list[dict[str, Any]]:
        tokens = [
            OAuthAccessTokenRecord(**raw).model_dump(mode="json")
            for raw in dict(self._load().get("oauth_access_tokens") or {}).values()
            if isinstance(raw, dict)
        ]
        return sorted(tokens, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def issue_oauth_authorization_code(
        self,
        *,
        client_id: str,
        email: str | None = None,
        user_id: str | None = None,
        profile_id: str | None = None,
        device_id: str | None = None,
        redirect_uri: str = "",
        scope: tuple[str, ...] | list[str] = ("relay:use",),
        ttl_seconds: int = 600,
        code_challenge: str = "",
        code_challenge_method: str = "",
    ) -> dict[str, Any]:
        payload = self._load()
        raw_client = dict(payload.get("oauth_clients") or {}).get(client_id)
        if not isinstance(raw_client, dict) or raw_client.get("status") != "active":
            return {"status": "unknown_oauth_client"}
        client = OAuthClientRecord(**raw_client)
        normalized_redirect = str(redirect_uri or "").strip()
        if client.redirect_uris and not any(
            _oauth_redirect_uris_match(candidate, normalized_redirect) for candidate in set(client.redirect_uris)
        ):
            return {"status": "redirect_uri_not_allowed"}
        normalized_code_challenge = str(code_challenge or "").strip()
        normalized_code_challenge_method = str(code_challenge_method or "").strip()
        if normalized_code_challenge and normalized_code_challenge_method not in {"S256"}:
            return {"status": "unsupported_code_challenge_method"}
        user = self._resolve_user(email=email, user_id=user_id)
        if user is None:
            return {"status": "unknown_user"}
        if not user.hosted_bridge_eligible:
            return {"status": "hosted_bridge_not_enabled", "user_id": user.user_id}
        profile = None
        if profile_id:
            raw_profile = dict(payload.get("relay_profiles") or {}).get(profile_id)
            profile = RelayProfileRecord(**raw_profile) if isinstance(raw_profile, dict) else None
        else:
            profile = self.default_relay_profile_for_user(user.user_id)
        if profile is None or profile.user_id != user.user_id:
            return {"status": "profile_not_owned"}
        selected_device_id = str(device_id or profile.default_device_id or "").strip()
        authz = self.verify_device_link(profile_id=profile.profile_id, device_id=selected_device_id or None)
        if not authz.get("authorized"):
            return {"status": "no_active_owned_device_link", "profile_id": profile.profile_id}
        selected_device_id = str(authz["device_id"])
        code = secrets.token_urlsafe(32)
        record = OAuthAuthorizationCodeRecord(
            code=code,
            client_id=client_id,
            user_id=user.user_id,
            profile_id=profile.profile_id,
            device_id=selected_device_id,
            redirect_uri=normalized_redirect,
            scope=_normalize_oauth_scope(scope),
            expires_at=(_now() + timedelta(seconds=max(60, int(ttl_seconds or 600)))).isoformat(),
            metadata={
                "code_challenge": normalized_code_challenge,
                "code_challenge_method": normalized_code_challenge_method or None,
            },
        )
        codes = dict(payload.get("oauth_authorization_codes") or {})
        codes[code] = record.model_dump(mode="json")
        payload["oauth_authorization_codes"] = codes
        self._save(payload)
        return {"status": "ok", "authorization_code": record.model_dump(mode="json")}

    def exchange_oauth_authorization_code(
        self,
        *,
        client_id: str,
        code: str,
        redirect_uri: str = "",
        ttl_seconds: int = 3600,
        code_verifier: str = "",
    ) -> dict[str, Any]:
        payload = self._load()
        codes = dict(payload.get("oauth_authorization_codes") or {})
        raw_code = codes.get(code)
        if not isinstance(raw_code, dict):
            return {"status": "invalid_grant"}
        record = OAuthAuthorizationCodeRecord(**raw_code)
        if record.client_id != client_id:
            return {"status": "invalid_grant"}
        if record.status != "open":
            return {"status": "invalid_grant"}
        normalized_redirect = str(redirect_uri or "").strip()
        if record.redirect_uri and not _oauth_redirect_uris_match(record.redirect_uri, normalized_redirect):
            return {"status": "invalid_grant"}
        expires_at = _parse_iso(record.expires_at)
        if expires_at is None or expires_at <= _now():
            raw_code["status"] = "expired"
            codes[code] = raw_code
            payload["oauth_authorization_codes"] = codes
            self._save(payload)
            return {"status": "expired_grant"}
        metadata = dict(record.metadata or {})
        expected_challenge = str(metadata.get("code_challenge") or "").strip()
        if expected_challenge:
            normalized_verifier = str(code_verifier or "").strip()
            if not normalized_verifier:
                return {"status": "missing_code_verifier"}
            if _pkce_s256(normalized_verifier) != expected_challenge:
                return {"status": "invalid_grant"}
        authz = self.verify_device_link(profile_id=record.profile_id, device_id=record.device_id)
        if not authz.get("authorized"):
            return {"status": "no_active_owned_device_link"}
        raw_token = secrets.token_urlsafe(48)
        token = OAuthAccessTokenRecord(
            token_hash=_hash_secret(raw_token),
            client_id=client_id,
            user_id=record.user_id,
            profile_id=record.profile_id,
            device_id=record.device_id,
            scope=record.scope,
            expires_at=(_now() + timedelta(seconds=max(300, int(ttl_seconds or 3600)))).isoformat(),
        )
        raw_code["status"] = "redeemed"
        raw_code["redeemed_at"] = _now_iso()
        codes[code] = raw_code
        tokens = dict(payload.get("oauth_access_tokens") or {})
        tokens[token.token_hash] = token.model_dump(mode="json")
        payload["oauth_authorization_codes"] = codes
        payload["oauth_access_tokens"] = tokens
        self._save(payload)
        return {
            "status": "ok",
            "access_token": raw_token,
            "token_type": "Bearer",
            "expires_in": max(300, int(ttl_seconds or 3600)),
            "profile_id": token.profile_id,
            "device_id": token.device_id,
            "scope": list(token.scope),
        }

    def resolve_oauth_access_token(self, access_token: str) -> dict[str, Any]:
        token_hash = _hash_secret(access_token)
        payload = self._load()
        raw = dict(payload.get("oauth_access_tokens") or {}).get(token_hash)
        if not isinstance(raw, dict):
            return {"status": "invalid_token", "authorized": False}
        token = OAuthAccessTokenRecord(**raw)
        if token.status != "active":
            return {"status": token.status, "authorized": False}
        expires_at = _parse_iso(token.expires_at)
        if expires_at is None or expires_at <= _now():
            raw["status"] = "expired"
            tokens = dict(payload.get("oauth_access_tokens") or {})
            tokens[token_hash] = raw
            payload["oauth_access_tokens"] = tokens
            self._save(payload)
            return {"status": "expired_token", "authorized": False}
        authz = self.verify_device_link(profile_id=token.profile_id, device_id=token.device_id)
        if not authz.get("authorized"):
            return {"status": "no_active_owned_device_link", "authorized": False}
        return {
            "status": "ok",
            "authorized": True,
            "client_id": token.client_id,
            "user_id": token.user_id,
            "profile_id": token.profile_id,
            "device_id": token.device_id,
            "scope": list(token.scope),
        }

    def revoke_oauth_access_token(self, access_token: str) -> dict[str, Any]:
        token_hash = _hash_secret(access_token)
        payload = self._load()
        tokens = dict(payload.get("oauth_access_tokens") or {})
        raw = tokens.get(token_hash)
        if not isinstance(raw, dict):
            return {"status": "not_found"}
        raw["status"] = "revoked"
        raw["revoked_at"] = _now_iso()
        tokens[token_hash] = raw
        payload["oauth_access_tokens"] = tokens
        self._save(payload)
        return {"status": "ok"}

    def user_for_email(self, email: str) -> AccountUserRecord | None:
        normalized = str(email or "").strip().lower()
        for raw in dict(self._load().get("users") or {}).values():
            if isinstance(raw, dict) and raw.get("email") == normalized:
                return AccountUserRecord(**raw)
        return None

    def desktop_status(self, *, profile_id: str | None = None, relay_endpoint: str = "") -> dict[str, Any]:
        profiles = self.list_profiles()
        selected = self.get_profile(profile_id) if profile_id else (profiles[0] if profiles else None)
        if selected is None:
            return {
                "status": "unlinked",
                "profile": None,
                "device": None,
                "device_link": None,
                "user": None,
            }
        user = self._resolve_user(user_id=selected.user_id)
        device = self.get_device(str(selected.default_device_id or "")) if selected.default_device_id else None
        link = self.active_device_link_for_profile(selected.profile_id)
        status = "linked" if user is not None and device is not None and link is not None else "partial"
        if link is None and relay_endpoint:
            for candidate in self.device_links_for_profile(selected.profile_id):
                if str(candidate.relay_endpoint or "").strip() == str(relay_endpoint or "").strip():
                    link = candidate
                    break
        return {
            "status": status,
            "profile": None if selected is None else selected.model_dump(mode="json"),
            "device": None if device is None else device.model_dump(mode="json"),
            "device_link": None if link is None else link.model_dump(mode="json"),
            "user": None if user is None else user.model_dump(mode="json"),
        }

    def _resolve_user(self, *, email: str | None = None, user_id: str | None = None) -> AccountUserRecord | None:
        target_user_id = str(user_id or "").strip()
        if target_user_id:
            raw = dict(self._load().get("users") or {}).get(target_user_id)
            return AccountUserRecord(**raw) if isinstance(raw, dict) else None
        if email:
            return self.user_for_email(email)
        return None

    def _load(self) -> dict[str, Any]:
        empty = {
            "users": {},
            "invites": {},
            "relay_profiles": {},
            "devices": {},
            "device_links": {},
            "oauth_clients": {},
            "oauth_authorization_codes": {},
            "oauth_access_tokens": {},
        }
        if not self.state_path.exists():
            return dict(empty)
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return dict(empty)
        if not isinstance(payload, dict):
            return dict(empty)
        payload.setdefault("users", {})
        payload.setdefault("invites", {})
        payload.setdefault("relay_profiles", {})
        payload.setdefault("devices", {})
        payload.setdefault("device_links", {})
        payload.setdefault("oauth_clients", {})
        payload.setdefault("oauth_authorization_codes", {})
        payload.setdefault("oauth_access_tokens", {})
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
