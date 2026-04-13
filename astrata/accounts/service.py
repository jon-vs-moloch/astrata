"""Durable registry scaffolding for Astrata Web account auth and device routing."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from astrata.accounts.models import (
    AstrataAccountSession,
    AstrataAccountState,
    AstrataDeviceLink,
    AstrataDeviceRecord,
    AstrataInviteCode,
    AstrataRelayProfileRecord,
    AstrataUser,
)
from astrata.config.settings import Settings, load_settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccountControlPlaneRegistry:
    def __init__(self, *, state_path: Path) -> None:
        self._state_path = state_path
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "AccountControlPlaneRegistry":
        resolved = settings or load_settings()
        return cls(state_path=resolved.paths.data_dir / "account_control_plane.json")

    def ensure_bootstrap_state(self) -> AstrataAccountState:
        state = self._load()
        if not self._state_path.exists():
            self._save(state)
        return state

    def summary(self) -> dict[str, Any]:
        state = self.ensure_bootstrap_state()
        return {
            "status": "in_progress",
            "state_path": str(self._state_path),
            "counts": {
                "users": len(state.users),
                "account_sessions": len(state.account_sessions),
                "relay_profiles": len(state.relay_profiles),
                "devices": len(state.devices),
                "device_links": len(state.device_links),
                "oauth_clients": len(state.oauth_clients),
                "oauth_authorization_codes": len(state.oauth_authorization_codes),
                "oauth_access_tokens": len(state.oauth_access_tokens),
                "gpt_connections": len(state.gpt_connections),
                "invite_codes": len(state.invite_codes),
            },
            "routing_rule": "access token -> user account -> relay profile -> selected device/link -> permitted tools",
            "access_policy": self.access_policy(),
            "current_bootstrap": {
                "public_download_and_install": True,
                "public_local_onboarding": True,
                "desktop_generated_pairing_codes": True,
                "shared_token_fallback": True,
                "invite_required_for_hosted_bridge": True,
                "user_login_required_for_gpt_distribution": True,
                "remote_host_bash_requires_special_acknowledgement": True,
            },
            "next_acceptance_gate": "Astrata Web login must become the identity layer before GPT distribution is considered safe.",
        }

    def schema_manifest(self) -> dict[str, Any]:
        self.ensure_bootstrap_state()
        return {
            "status": "in_progress",
            "entities": [
                {"name": "users", "purpose": "Astrata account identity"},
                {"name": "account_sessions", "purpose": "Astrata Web login sessions"},
                {"name": "relay_profiles", "purpose": "user-owned remote routing targets"},
                {"name": "devices", "purpose": "user-owned desktop clients"},
                {"name": "device_links", "purpose": "device/profile relay binding"},
                {"name": "oauth_clients", "purpose": "registered GPT or connector OAuth clients"},
                {"name": "oauth_authorization_codes", "purpose": "temporary OAuth grant records"},
                {"name": "oauth_access_tokens", "purpose": "issued GPT access tokens"},
                {"name": "gpt_connections", "purpose": "operator-visible connected GPT sessions"},
                {"name": "invite_codes", "purpose": "friendly-tester eligibility for hosted bridge activation"},
            ],
            "phase_order": [
                "account_schema_and_control_plane_surface",
                "desktop_sign_in_and_device_registration",
                "user_centric_gpt_oauth",
                "revocation_and_default_device_controls",
                "queue_and_storage_hardening",
            ],
            "distribution_safe_rule": "Pairing must select a device for an authenticated user; pairing must not serve as identity proof.",
        }

    def access_policy(self) -> dict[str, Any]:
        return {
            "public_access": {
                "download": True,
                "desktop_install": True,
                "local_onboarding": True,
                "local_runtime_bootstrap": True,
                "local_model_downloads": True,
            },
            "invite_gated_access": {
                "hosted_account_activation": True,
                "gpt_bridge_sign_in": True,
                "relay_profile_activation": True,
                "remote_queue_usage": True,
                "hosted_control_plane_features": True,
            },
            "billing_boundary": "cloud_access_layer",
            "policy_rule": "download/install is public; hosted bridge activation is invite-gated until monetization exists",
        }

    def hosted_bridge_eligibility(self, *, email: str | None = None) -> dict[str, Any]:
        state = self.ensure_bootstrap_state()
        user = self._find_user_by_email(state, str(email or "").strip().lower()) if email else None
        if user is None:
            return {
                "status": "invite_required",
                "reason": "No hosted Astrata tester account is active for this email yet.",
                "invite_required": True,
                "public_access": self.access_policy()["public_access"],
            }
        if user.status == "disabled" or user.hosted_bridge_eligibility == "disabled":
            return {
                "status": "disabled",
                "reason": "Hosted bridge access is disabled for this account.",
                "invite_required": False,
                "user": user.model_dump(mode="json"),
                "public_access": self.access_policy()["public_access"],
            }
        if user.hosted_bridge_eligibility in {"eligible", "active"}:
            return {
                "status": user.hosted_bridge_eligibility,
                "reason": "This account may activate hosted bridge features.",
                "invite_required": False,
                "user": user.model_dump(mode="json"),
                "public_access": self.access_policy()["public_access"],
            }
        return {
            "status": "invite_required",
            "reason": "This account may use public Astrata onboarding, but hosted bridge activation still requires an invite.",
            "invite_required": True,
            "user": user.model_dump(mode="json"),
            "public_access": self.access_policy()["public_access"],
        }

    def desktop_status(
        self,
        *,
        profile_id: str | None = None,
        relay_endpoint: str = "",
    ) -> dict[str, Any]:
        state = self.ensure_bootstrap_state()
        profile = self._resolve_profile(state, profile_id=profile_id, relay_endpoint=relay_endpoint)
        if profile is None:
            return {
                "status": "unlinked",
                "phase": "desktop_sign_in_and_device_registration",
                "identity_source": "desktop_bootstrap",
                "distribution_ready": False,
                "user_login_required_for_distribution": True,
                "hosted_bridge_eligibility": {
                    "status": "invite_required",
                    "reason": "Local onboarding may continue, but hosted bridge activation still requires an invited account and a linked relay profile.",
                    "invite_required": True,
                    "public_access": self.access_policy()["public_access"],
                },
                "access_policy": self.access_policy(),
            }

        user = state.users.get(profile.user_id)
        device = state.devices.get(profile.default_device_id or "") if profile.default_device_id else None
        link = None
        if device is not None:
            link = next(
                (
                    candidate
                    for candidate in state.device_links.values()
                    if candidate.device_id == device.device_id and candidate.profile_id == profile.profile_id and candidate.status != "revoked"
                ),
                None,
            )

        return {
            "status": "linked" if user and device and link else "partial",
            "phase": "desktop_sign_in_and_device_registration",
            "identity_source": "desktop_bootstrap",
            "distribution_ready": False,
            "user_login_required_for_distribution": True,
            "hosted_bridge_eligibility": None if user is None else self.hosted_bridge_eligibility(email=user.email),
            "access_policy": self.access_policy(),
            "remote_host_bash": self.remote_host_bash_status(profile_id=profile.profile_id),
            "user": None if user is None else user.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "device": None if device is None else device.model_dump(mode="json"),
            "device_link": None if link is None else link.model_dump(mode="json"),
        }

    def issue_invite_code(
        self,
        *,
        label: str = "",
        max_redemptions: int = 1,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        state = self.ensure_bootstrap_state()
        now = _now_iso()
        invite = AstrataInviteCode(
            code=f"ASTRATA-INVITE-{uuid4().hex[:8].upper()}",
            label=str(label or "").strip(),
            max_redemptions=max(1, int(max_redemptions)),
            expires_at=expires_at,
            updated_at=now,
        )
        state.invite_codes[invite.invite_id] = invite
        state.updated_at = now
        self._save(state)
        return {
            "status": "ok",
            "invite": invite.model_dump(mode="json"),
            "policy_rule": self.access_policy()["policy_rule"],
        }

    def redeem_invite_code(
        self,
        *,
        email: str,
        code: str,
        display_name: str = "",
    ) -> dict[str, Any]:
        normalized_email = str(email or "").strip().lower()
        normalized_code = str(code or "").strip().upper()
        if not normalized_email:
            raise ValueError("email_required")
        if not normalized_code:
            raise ValueError("invite_code_required")
        state = self.ensure_bootstrap_state()
        invite = next(
            (
                candidate
                for candidate in state.invite_codes.values()
                if candidate.code.strip().upper() == normalized_code
            ),
            None,
        )
        if invite is None:
            raise ValueError("invite_code_invalid")
        if invite.status in {"disabled", "expired"}:
            raise ValueError("invite_code_unavailable")
        if invite.redemption_count >= invite.max_redemptions:
            invite.status = "redeemed"
            invite.updated_at = _now_iso()
            self._save(state)
            raise ValueError("invite_code_exhausted")

        now = _now_iso()
        user = self._find_user_by_email(state, normalized_email)
        if user is None:
            user = AstrataUser(
                email=normalized_email,
                display_name=str(display_name or "").strip() or self._display_name_from_email(normalized_email),
                status="active",
                hosted_bridge_eligibility="eligible",
                updated_at=now,
            )
            state.users[user.user_id] = user
        else:
            if display_name.strip():
                user.display_name = display_name.strip()
            user.status = "active"
            user.hosted_bridge_eligibility = "eligible"
            user.updated_at = now

        invite.redemption_count += 1
        invite.redeemed_by_user_id = user.user_id
        invite.redeemed_at = now
        invite.updated_at = now
        if invite.redemption_count >= invite.max_redemptions:
            invite.status = "redeemed"

        state.updated_at = now
        self._save(state)
        return {
            "status": "ok",
            "user": user.model_dump(mode="json"),
            "invite": invite.model_dump(mode="json"),
            "hosted_bridge_eligibility": self.hosted_bridge_eligibility(email=user.email),
        }

    def register_desktop_device(
        self,
        *,
        email: str,
        display_name: str = "",
        device_label: str,
        profile_id: str,
        relay_endpoint: str = "",
        profile_label: str = "",
        control_posture: str = "true_remote_prime",
        disclosure_tier: str = "trusted_remote",
        device_platform: str = "desktop",
    ) -> dict[str, Any]:
        normalized_email = str(email or "").strip().lower()
        if not normalized_email:
            raise ValueError("email_required")
        resolved_profile_id = str(profile_id or "").strip()
        if not resolved_profile_id:
            raise ValueError("profile_id_required")
        resolved_device_label = str(device_label or "").strip()
        if not resolved_device_label:
            raise ValueError("device_label_required")

        state = self.ensure_bootstrap_state()
        now = _now_iso()
        user = self._find_user_by_email(state, normalized_email)
        if user is None:
            user = AstrataUser(
                email=normalized_email,
                display_name=str(display_name or "").strip() or self._display_name_from_email(normalized_email),
                hosted_bridge_eligibility="invite_required",
                updated_at=now,
            )
            state.users[user.user_id] = user
        else:
            if display_name.strip():
                user.display_name = display_name.strip()
            user.updated_at = now

        profile = self._resolve_profile(state, profile_id=resolved_profile_id, relay_endpoint=relay_endpoint)
        if profile is None:
            profile = AstrataRelayProfileRecord(
                profile_id=resolved_profile_id,
                user_id=user.user_id,
                label=str(profile_label or "").strip() or "Astrata Relay",
                control_posture=control_posture,
                disclosure_tier=disclosure_tier,
                updated_at=now,
            )
            state.relay_profiles[profile.profile_id] = profile
        else:
            profile.user_id = user.user_id
            if profile_label.strip():
                profile.label = profile_label.strip()
            profile.control_posture = control_posture or profile.control_posture
            profile.disclosure_tier = disclosure_tier or profile.disclosure_tier
            profile.updated_at = now

        user.default_profile_id = profile.profile_id
        user.updated_at = now

        device = next(
            (
                candidate
                for candidate in state.devices.values()
                if candidate.user_id == user.user_id
                and candidate.platform == device_platform
                and candidate.label.strip().lower() == resolved_device_label.lower()
                and candidate.status != "revoked"
            ),
            None,
        )
        if device is None:
            device = AstrataDeviceRecord(
                user_id=user.user_id,
                label=resolved_device_label,
                platform=device_platform,
                last_seen_at=now,
                updated_at=now,
            )
            state.devices[device.device_id] = device
        else:
            device.status = "active"
            device.label = resolved_device_label
            device.last_seen_at = now
            device.updated_at = now

        link = next(
            (
                candidate
                for candidate in state.device_links.values()
                if candidate.device_id == device.device_id and candidate.profile_id == profile.profile_id
            ),
            None,
        )
        if link is None:
            link = AstrataDeviceLink(
                device_id=device.device_id,
                profile_id=profile.profile_id,
                relay_endpoint=str(relay_endpoint or "").strip(),
                link_token_hash=self._link_token_hash(user.user_id, device.device_id, profile.profile_id),
                status="active",
                last_heartbeat_at=now,
                updated_at=now,
            )
            state.device_links[link.link_id] = link
        else:
            link.status = "active"
            link.relay_endpoint = str(relay_endpoint or "").strip() or link.relay_endpoint
            if not link.link_token_hash:
                link.link_token_hash = self._link_token_hash(user.user_id, device.device_id, profile.profile_id)
            link.last_heartbeat_at = now
            link.updated_at = now

        profile.default_device_id = device.device_id
        profile.updated_at = now

        session = next(
            (
                candidate
                for candidate in state.account_sessions.values()
                if candidate.user_id == user.user_id and candidate.auth_method == "desktop_bootstrap" and candidate.status == "active"
            ),
            None,
        )
        if session is None:
            session = AstrataAccountSession(
                user_id=user.user_id,
                auth_method="desktop_bootstrap",
                status="active",
                created_at=now,
                updated_at=now,
            )
            state.account_sessions[session.session_id] = session
        else:
            session.updated_at = now

        state.updated_at = now
        self._save(state)
        return {
            "status": "ok",
            "phase": "desktop_sign_in_and_device_registration",
            "identity_source": "desktop_bootstrap",
            "hosted_bridge_eligibility": self.hosted_bridge_eligibility(email=user.email),
            "access_policy": self.access_policy(),
            "remote_host_bash": self.remote_host_bash_status(profile_id=profile.profile_id),
            "user": user.model_dump(mode="json"),
            "session": session.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "device": device.model_dump(mode="json"),
            "device_link": link.model_dump(mode="json"),
            "distribution_ready": False,
            "user_login_required_for_distribution": True,
        }

    def remote_host_bash_status(self, *, profile_id: str) -> dict[str, Any]:
        state = self.ensure_bootstrap_state()
        profile = self._resolve_profile(state, profile_id=profile_id)
        warning = (
            ""
            if profile is None
            else profile.remote_host_bash_warning
        )
        warning = warning or "This allows any GPT session authenticated to this Astrata profile to execute arbitrary host shell commands on any connected computer for that profile."
        return {
            "profile_id": profile_id if profile is None else profile.profile_id,
            "enabled": False if profile is None else bool(profile.allow_remote_host_bash),
            "acknowledged_at": None if profile is None else profile.remote_host_bash_acknowledged_at,
            "warning": warning,
            "requires_special_acknowledgement": True,
        }

    def set_remote_host_bash(
        self,
        *,
        profile_id: str,
        enabled: bool,
        warning: str | None = None,
    ) -> dict[str, Any]:
        state = self.ensure_bootstrap_state()
        profile = self._resolve_profile(state, profile_id=profile_id)
        if profile is None:
            raise KeyError(f"Unknown relay profile `{profile_id}`.")
        now = _now_iso()
        profile.allow_remote_host_bash = bool(enabled)
        profile.remote_host_bash_acknowledged_at = now if enabled else None
        profile.remote_host_bash_warning = (
            str(warning).strip()
            if warning is not None and str(warning).strip()
            else "This allows any GPT session authenticated to this Astrata profile to execute arbitrary host shell commands on any connected computer for that profile."
        )
        profile.updated_at = now
        state.updated_at = now
        self._save(state)
        return {
            "status": "ok",
            "profile": profile.model_dump(mode="json"),
            "remote_host_bash": self.remote_host_bash_status(profile_id=profile.profile_id),
        }

    def _load(self) -> AstrataAccountState:
        if not self._state_path.exists():
            return AstrataAccountState()
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return AstrataAccountState()
        if not isinstance(payload, dict):
            return AstrataAccountState()
        try:
            return AstrataAccountState.model_validate(payload)
        except Exception:
            return AstrataAccountState()

    def _save(self, state: AstrataAccountState) -> None:
        payload = state.model_dump(mode="json")
        self._state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _find_user_by_email(self, state: AstrataAccountState, email: str) -> AstrataUser | None:
        normalized = str(email or "").strip().lower()
        if not normalized:
            return None
        return next((user for user in state.users.values() if user.email.strip().lower() == normalized), None)

    def _resolve_profile(
        self,
        state: AstrataAccountState,
        *,
        profile_id: str | None = None,
        relay_endpoint: str = "",
    ) -> AstrataRelayProfileRecord | None:
        resolved_profile_id = str(profile_id or "").strip()
        if resolved_profile_id and resolved_profile_id in state.relay_profiles:
            return state.relay_profiles[resolved_profile_id]
        resolved_endpoint = str(relay_endpoint or "").strip().rstrip("/")
        if not resolved_endpoint:
            return None
        return next(
            (
                profile
                for profile in state.relay_profiles.values()
                if any(
                    link.profile_id == profile.profile_id
                    and str(link.relay_endpoint or "").strip().rstrip("/") == resolved_endpoint
                    for link in state.device_links.values()
                )
            ),
            None,
        )

    def _display_name_from_email(self, email: str) -> str:
        local = str(email or "").split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ").strip()
        return " ".join(part[:1].upper() + part[1:] for part in local.split() if part) or "Astrata User"

    def _link_token_hash(self, user_id: str, device_id: str, profile_id: str) -> str:
        payload = f"{user_id}:{device_id}:{profile_id}:{uuid4()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
