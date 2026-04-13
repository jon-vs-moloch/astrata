"""Public-facing web presence helpers for Astrata."""

from __future__ import annotations

from typing import Any

from astrata.accounts import AccountControlPlaneRegistry
from astrata.config.settings import Settings, load_settings
from astrata.local.catalog import StarterCatalog
from astrata.providers.registry import build_default_registry
from astrata.voice import VoiceService
from astrata.voice.registry import VoiceAssetRegistry


class WebPresenceService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()

    def capabilities(self) -> dict[str, Any]:
        auth_registry = AccountControlPlaneRegistry.from_settings(self.settings)
        return {
            "product": {
                "name": "Astrata",
                "positioning": "operating system for a new mode of computing",
            },
            "services": {
                "public_registry_api": True,
                "provider_catalog": True,
                "model_catalog": True,
                "voice_asset_registry": True,
                "account_auth_control_plane": "in_progress",
                "oauth_provider": "in_progress",
                "device_pairing": "in_progress",
                "per_user_relay_routing": "in_progress",
                "publish_to_internet": True,
                "constellation_backbone_ready": "planned",
            },
            "access_policy": auth_registry.access_policy(),
            "auth_control_plane": auth_registry.summary(),
            "publish_capabilities": [
                {
                    "capability_id": "publish-static-site",
                    "kind": "web_publish",
                    "status": "planned",
                    "description": "Put content onto the public internet as a hosted static site or simple web app.",
                },
                {
                    "capability_id": "host-registry-api",
                    "kind": "api_publish",
                    "status": "available",
                    "description": "Serve public Astrata metadata such as model, provider, and asset registries.",
                },
            ],
        }

    def provider_registry(self) -> dict[str, Any]:
        registry = build_default_registry()
        return {
            "providers": registry.list_available_providers(),
            "inference_sources": registry.list_available_inference_sources(),
        }

    def model_registry(self) -> dict[str, Any]:
        catalog = StarterCatalog()
        return {
            "local_model_catalog": [model.__dict__ for model in catalog.list_models()],
        }

    def voice_registry(self) -> dict[str, Any]:
        registry = VoiceAssetRegistry(path=self.settings.paths.data_dir / "voice_registry.json")
        status = registry.status()
        status["recommended"] = VoiceService(settings=self.settings).status()
        return status

    def download_manifest(self) -> dict[str, Any]:
        auth_registry = AccountControlPlaneRegistry.from_settings(self.settings)
        return {
            "access_policy": auth_registry.access_policy(),
            "download_channels": [
                {
                    "channel_id": "local-repo",
                    "status": "available",
                    "description": "Current local/dev install path from source.",
                    "invite_required": False,
                },
                {
                    "channel_id": "hosted-release",
                    "status": "live",
                    "description": "Hosted download/install surface for Astrata itself.",
                    "invite_required": False,
                },
            ],
            "hosted_bridge_activation": {
                "status": "invite_gated",
                "invite_required": True,
                "description": "Friendly testers may download and install Astrata without an invite, but hosted bridge activation remains invite-gated until monetization is live.",
            },
        }

    def distribution_manifest(self) -> dict[str, Any]:
        auth_registry = AccountControlPlaneRegistry.from_settings(self.settings)
        access_policy = auth_registry.access_policy()
        return {
            "product": {
                "name": "Astrata",
                "distribution_status": "live",
            },
            "access_policy": access_policy,
            "topology": {
                "site": {
                    "provider": "cloudflare_pages",
                    "role": "public download site, release notes, and update metadata",
                    "planned_hostname": "download.astrata.ai",
                },
                "artifact_storage": {
                    "provider": "cloudflare_r2",
                    "role": "desktop installers, update bundles, and release artifacts",
                    "planned_hostname": "releases.astrata.ai",
                },
                "gating_worker": {
                    "provider": "cloudflare_workers",
                    "role": "invite checks, entitlement checks, signed download URLs, and per-channel update manifests",
                    "planned_hostname": "api.astrata.ai",
                },
                "metadata_store": {
                    "provider": "cloudflare_d1",
                    "role": "account, invite, entitlement, channel, and release metadata",
                    "status": "planned",
                },
            },
            "channels": [
                {
                    "channel_id": "edge",
                    "audience": "internal or adventurous users who want every successful build",
                    "cadence": "every_build",
                    "invite_required_for_download": False,
                    "invite_required_for_updates": True,
                    "status": "live",
                },
                {
                    "channel_id": "nightly",
                    "audience": "testers who want the latest promoted daily build",
                    "cadence": "nightly",
                    "invite_required_for_download": False,
                    "invite_required_for_updates": True,
                    "status": "live",
                },
                {
                    "channel_id": "stable",
                    "audience": "general users",
                    "cadence": "manual_release",
                    "invite_required_for_download": False,
                    "invite_required_for_updates": False,
                    "status": "planned",
                },
                {
                    "channel_id": "tester",
                    "audience": "friendly testers before monetization",
                    "cadence": "manual_promote",
                    "invite_required_for_download": False,
                    "invite_required_for_updates": True,
                    "status": "live",
                },
            ],
            "artifacts": [
                {
                    "artifact_id": "desktop-macos-dmg",
                    "platform": "macos",
                    "format": "dmg",
                    "delivery": "r2",
                    "status": "planned",
                },
                {
                    "artifact_id": "desktop-windows-msi",
                    "platform": "windows",
                    "format": "msi",
                    "delivery": "r2",
                    "status": "planned",
                },
                {
                    "artifact_id": "desktop-linux-appimage",
                    "platform": "linux",
                    "format": "AppImage",
                    "delivery": "r2",
                    "status": "planned",
                },
            ],
            "update_contract": {
                "manifest_route": "/api/updates/{channel}",
                "download_route": "/api/distribution",
                "release_index_route": "/api/downloads",
                "policy_rule": access_policy["policy_rule"],
            },
        }

    def update_manifest(self, channel: str = "stable") -> dict[str, Any]:
        normalized = str(channel or "stable").strip().lower() or "stable"
        distribution = self.distribution_manifest()
        known_channels = {entry["channel_id"]: entry for entry in distribution["channels"]}
        selected = known_channels.get(normalized, known_channels["stable"])
        invite_required = bool(selected["invite_required_for_updates"])
        return {
            "product": "Astrata",
            "channel": selected["channel_id"],
            "status": selected["status"],
            "invite_required": invite_required,
            "cadence": selected["cadence"],
            "access_policy": distribution["access_policy"],
            "release": {
                "version": None,
                "notes_url": None,
                "published_at": None,
            },
            "artifacts": {
                "macos": {
                    "url": None,
                    "signature_url": None,
                    "format": "dmg",
                },
                "windows": {
                    "url": None,
                    "signature_url": None,
                    "format": "msi",
                },
                "linux": {
                    "url": None,
                    "signature_url": None,
                    "format": "AppImage",
                },
            },
            "gating": {
                "worker_required": invite_required,
                "reason": (
                    "This channel should be served through the Cloudflare Worker so invite and entitlement checks can run before the updater receives artifact URLs."
                    if invite_required
                    else "This channel may be served publicly once a release exists."
                ),
            },
        }

    def auth_control_plane(self) -> dict[str, Any]:
        registry = AccountControlPlaneRegistry.from_settings(self.settings)
        return registry.summary()

    def auth_schema(self) -> dict[str, Any]:
        registry = AccountControlPlaneRegistry.from_settings(self.settings)
        return registry.schema_manifest()
