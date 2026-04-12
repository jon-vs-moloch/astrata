"""Aggregation and action helpers for Astrata's first local UI shell."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Update-channel constants (mirrors astrata.distribution.release.CHANNELS).
# Kept here so the UI service has no import dependency on the release module.
# ---------------------------------------------------------------------------
_UPDATE_CHANNELS: dict[str, dict[str, object]] = {
    "edge": {
        "cadence": "every_build",
        "invite_required": True,
        "description": "Every successful build. Highest velocity, highest risk.",
    },
    "nightly": {
        "cadence": "nightly",
        "invite_required": True,
        "description": "Latest promoted daily build for fast-follow testers.",
    },
    "tester": {
        "cadence": "manual_promote",
        "invite_required": True,
        "description": "Friendly-tester channel before monetization.",
    },
    "stable": {
        "cadence": "manual_release",
        "invite_required": False,
        "description": "General-availability release channel.",
    },
}
_DEFAULT_UPDATE_CHANNEL = "tester"

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import socket
import subprocess
from typing import Any
import urllib.error
import urllib.request

from astrata.accounts.service import AccountControlPlaneRegistry
from astrata.comms.intake import process_inbound_messages
from astrata.comms.lanes import PrincipalMessageLane
from astrata.comms.runtime import LaneRuntime
from astrata.config.settings import Settings, load_settings
from astrata.context import build_quota_snapshot, summarize_inference_activity
from astrata.agents import DurableAgentRegistry
from astrata.governance.documents import load_governance_bundle
from astrata.local.backends.llama_cpp import LlamaCppBackend
from astrata.local.hardware import probe_thermal_state
from astrata.local.runtime.client import LocalRuntimeClient
from astrata.local.runtime.manager import LocalRuntimeManager
from astrata.local.runtime.models import RuntimeHealthSnapshot
from astrata.local.runtime.processes import ManagedProcessController
from astrata.local.thermal import ThermalController
from astrata.loop0.runner import Loop0Runner
from astrata.mcp.relay import HostedMCPRelayService
from astrata.providers.registry import build_default_registry
from astrata.records.communications import CommunicationRecord
from astrata.records.models import AttemptRecord, ArtifactRecord, TaskRecord, VerificationRecord
from astrata.routing.policy import RouteChooser
from astrata.scheduling.quota import QuotaPolicy, default_source_limits
from astrata.storage.db import AstrataDatabase
from astrata.startup.diagnostics import (
    generate_python_preflight_report,
    load_preflight_report,
    load_runtime_report,
    run_startup_reflection,
)
from astrata.voice import VoiceService


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MessageDraft:
    message: str
    recipient: str = "prime"
    conversation_id: str = ""
    intent: str = "principal_message"
    kind: str = "request"


class AstrataUIService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()

    def ensure_startup_reports(self) -> dict[str, Any]:
        preflight = load_preflight_report(self.settings) or generate_python_preflight_report(self.settings)
        reflection = run_startup_reflection(self.settings, db=self._db())
        return {"preflight": preflight, "runtime": reflection.report}

    def snapshot(self) -> dict[str, Any]:
        db = self._db()
        registry = build_default_registry()
        chooser = RouteChooser(registry)
        bundle = load_governance_bundle(self.settings.paths.project_root)
        tasks = self._tasks(db)
        attempts = self._attempts(db)
        artifacts = self._artifacts(db)
        verifications = self._verifications(db)
        principal_lane = PrincipalMessageLane(db=db)
        principal_messages = list(reversed(principal_lane.list_messages(recipient="principal")))[:8]
        astrata_messages = list(reversed(principal_lane.list_messages(recipient="astrata")))[:8]
        prime_messages = list(reversed(principal_lane.list_messages(recipient="prime")))[:8]
        local_messages = list(reversed(principal_lane.list_messages(recipient="local")))[:8]
        default_route = None
        try:
            default_route = chooser.choose(priority=0, urgency=0, risk="moderate").__dict__
        except Exception:
            default_route = None
        startup_preflight = load_preflight_report(self.settings) or generate_python_preflight_report(self.settings)
        startup_runtime = load_runtime_report(self.settings)
        if startup_runtime is None:
            startup_runtime = run_startup_reflection(self.settings, db=db).report
        inference_telemetry = summarize_inference_activity(
            attempts=[attempt.model_dump(mode="json") for attempt in attempts],
            tasks=[task.model_dump(mode="json") for task in tasks],
            quota_snapshots=self._quota_snapshots(db, registry),
        )
        history = self._history_snapshot(
            tasks=tasks,
            attempts=attempts,
            artifacts=artifacts,
            verifications=verifications,
            communications=self._communications(db),
            inference_telemetry=inference_telemetry,
        )
        return {
            "generated_at": _now_iso(),
            "product": {
                "name": "Astrata",
                "tagline": "local-first recursive principal harness",
            },
            "governance": {
                "constitution_path": bundle.constitution.path,
                "project_spec_path": None if bundle.project_spec is None else bundle.project_spec.path,
                "planning_docs": {name: doc.path for name, doc in bundle.planning_docs.items()},
            },
            "providers": {
                "available": registry.list_available_providers(),
                "inference_sources": registry.list_available_inference_sources(),
                "default_route": default_route,
            },
            "agents": {
                "prime": self._agent_snapshot("prime"),
                "reception": self._agent_snapshot("reception"),
                "local": self._agent_snapshot("local"),
            },
            "startup": {
                "preflight": startup_preflight,
                "runtime": startup_runtime,
            },
            "desktop_backend": self._desktop_backend_snapshot(),
            "relay": self._relay_snapshot(),
            "account_auth": self._account_auth_snapshot(),
            "local_runtime": self._local_runtime_snapshot(),
            "voice": VoiceService(settings=self.settings).status(),
            "queue": {
                "counts": dict(Counter(task.status for task in tasks)),
                "recent_tasks": [self._task_summary(task) for task in tasks[:8]],
            },
            "attempts": {
                "counts": dict(Counter(attempt.outcome for attempt in attempts)),
                "recent_attempts": [self._attempt_summary(attempt) for attempt in attempts[:8]],
            },
            "inference": inference_telemetry,
            "history": history,
            "communications": {
                "principal_inbox": [self._message_summary(message) for message in principal_messages],
                "operator_inbox": [self._message_summary(message) for message in principal_messages],
                "astrata_inbox": [self._message_summary(message) for message in astrata_messages],
                "prime_inbox": [self._message_summary(message) for message in prime_messages],
                "local_inbox": [self._message_summary(message) for message in local_messages],
                "prime_conversation": [self._message_summary(message) for message in self.lane_conversation("prime", db=db)[-16:]],
                "local_conversation": [self._message_summary(message) for message in self.lane_conversation("local", db=db)[-16:]],
                "lane_counts": {
                    "principal": self._lane_count(principal_lane, "principal"),
                    "operator": self._lane_count(principal_lane, "principal"),
                    "astrata": self._lane_count(principal_lane, "astrata"),
                    "prime": self._lane_count(principal_lane, "prime"),
                    "local": self._lane_count(principal_lane, "local"),
                },
            },
            "artifacts": {
                "counts": dict(Counter(artifact.artifact_type for artifact in artifacts)),
                "recent": [self._artifact_summary(artifact) for artifact in artifacts[:6]],
            },
            "verifications": {
                "counts": dict(Counter(verification.result for verification in verifications)),
                "recent": [verification.model_dump(mode="json") for verification in verifications[:6]],
            },
            "update_channel": self._update_channel_snapshot(),
        }

    def send_message(self, draft: MessageDraft) -> dict[str, Any]:
        db = self._db()
        lane = PrincipalMessageLane(db=db)
        record = lane.send(
            sender="principal",
            recipient=draft.recipient,
            conversation_id=draft.conversation_id or lane.default_conversation_id(draft.recipient),
            kind=draft.kind,
            intent=draft.intent,
            payload={"message": draft.message},
        )
        result: dict[str, Any] = {"message": record.model_dump(mode="json")}
        if draft.recipient in {"prime", "local"}:
            runtime = LaneRuntime(settings=self.settings, db=db)
            result["turn"] = runtime.handle_message(record).as_dict()
        return result

    def task_detail(self, task_id: str) -> dict[str, Any]:
        db = self._db()
        task = next((task for task in self._tasks(db) if task.task_id == task_id), None)
        if task is None:
            return {"status": "not_found", "task_id": task_id}
        attempts = [attempt for attempt in self._attempts(db) if attempt.task_id == task_id]
        artifacts = [artifact for artifact in self._artifacts(db) if self._artifact_relates_to_task(artifact, task_id)]
        verifications = [
            verification for verification in self._verifications(db) if verification.target_id in {task_id, *[attempt.attempt_id for attempt in attempts]}
        ]
        messages = self._messages_for_task(db, task_id)
        return {
            "status": "ok",
            "task": self._task_summary(task),
            "attempts": [self._attempt_summary(attempt) for attempt in attempts],
            "artifacts": [self._artifact_summary(artifact) for artifact in artifacts],
            "verifications": [verification.model_dump(mode="json") for verification in verifications],
            "messages": [self._message_summary(message) for message in messages],
            "relationships": self._task_relationships(db, task),
            "blockers": self._task_blockers(db, task),
        }

    def lane_conversation(self, lane: str, *, db: AstrataDatabase | None = None) -> list[CommunicationRecord]:
        lane_name = str(lane).strip().lower()
        if not lane_name:
            return []
        db = db or self._db()
        default_conversation_id = PrincipalMessageLane(db=db).default_conversation_id(lane_name)
        messages = []
        for record in self._communications(db):
            sender = str(record.sender or "").strip().lower()
            recipient = str(record.recipient or "").strip().lower()
            conversation_id = str(record.conversation_id or "").strip().lower()
            if conversation_id == default_conversation_id:
                messages.append(record)
                continue
            if sender == lane_name or recipient == lane_name:
                messages.append(record)
                continue
            if lane_name == "system" and recipient == "astrata":
                messages.append(record)
        return sorted(messages, key=lambda item: item.created_at)

    def acknowledge_message(self, communication_id: str) -> dict[str, Any]:
        db = self._db()
        lane = PrincipalMessageLane(db=db)
        updated = lane.acknowledge(communication_id)
        if updated is None:
            return {"status": "not_found", "communication_id": communication_id}
        return updated.model_dump(mode="json")

    def run_loop(self, *, steps: int = 1) -> dict[str, Any]:
        db = self._db()
        lane_runtime = LaneRuntime(settings=self.settings, db=db)
        lane_turns = lane_runtime.process_pending_turns(lane="prime", limit=5)
        lane_turns.extend(lane_runtime.process_pending_turns(lane="local", limit=5))
        inbox = process_inbound_messages(
            db=db,
            project_root=self.settings.paths.project_root,
            recipient="astrata",
            limit=5,
        )
        runner = Loop0Runner(settings=self.settings, db=db)
        result = runner.run_steps(max(1, steps))
        return {"inbox": inbox, "lane_turns": lane_turns, "loop0": result}

    def start_local_runtime(self, *, model_id: str | None = None, profile_id: str | None = None) -> dict[str, Any]:
        manager = self._local_runtime_manager()
        recommendation = manager.recommend(thermal_preference=self.settings.local_runtime.thermal_preference)
        thermal_state = probe_thermal_state(preference=self.settings.local_runtime.thermal_preference)
        thermal_controller = ThermalController(state_path=self.settings.paths.data_dir / "thermal_state.json")
        thermal_decision = thermal_controller.evaluate(thermal_state)
        if not thermal_decision.should_start_new_local_work:
            return {
                "status": "deferred_for_thermal",
                "thermal_state": {
                    "preference": thermal_state.preference,
                    "thermal_pressure": thermal_state.thermal_pressure,
                    "detail": thermal_state.detail,
                },
                "thermal_decision": thermal_decision.__dict__,
            }
        model = manager.model_registry().get(model_id) if model_id else recommendation.model
        if model is None:
            return {
                "status": "no_model",
                "recommendation": {
                    "model": None if recommendation.model is None else recommendation.model.model_dump(mode="json"),
                    "profile_id": recommendation.profile_id,
                    "reason": recommendation.reason,
                },
            }
        status = manager.start_managed(
            backend_id="llama_cpp",
            model_id=model.model_id,
            binary_path=self.settings.local_runtime.llama_cpp_binary,
            host=self.settings.local_runtime.llama_cpp_host,
            port=self.settings.local_runtime.llama_cpp_port,
            profile_id=profile_id or recommendation.profile_id,
        )
        return {
            "status": "started",
            "model": model.model_dump(mode="json"),
            "managed_process": None if status is None else self._managed_process_summary(status),
        }

    def ensure_local_runtime(self, *, model_id: str | None = None, profile_id: str | None = None) -> dict[str, Any]:
        manager = self._local_runtime_manager()
        recommendation = manager.recommend(thermal_preference=self.settings.local_runtime.thermal_preference)
        thermal_state = probe_thermal_state(preference=self.settings.local_runtime.thermal_preference)
        thermal_controller = ThermalController(state_path=self.settings.paths.data_dir / "thermal_state.json")
        thermal_decision = thermal_controller.evaluate(thermal_state)
        managed = manager.managed_status()
        health = manager.health(
            config={
                "host": self.settings.local_runtime.llama_cpp_host,
                "port": self.settings.local_runtime.llama_cpp_port,
            }
        )
        if managed is not None and managed.running and health is not None and health.ok:
            return {
                "status": "already_running",
                "managed_process": self._managed_process_summary(managed),
                "health": None if health is None else health.model_dump(mode="json"),
            }
        direct_health = self._configured_local_runtime_health(manager)
        if direct_health is not None and direct_health.ok:
            manager.select_runtime(
                backend_id=direct_health.backend_id,
                model_id=model_id or (None if recommendation.model is None else recommendation.model.model_id),
                mode="external",
                profile_id=profile_id or recommendation.profile_id,
                endpoint=direct_health.endpoint,
                metadata={"adopted_existing_endpoint": True},
            )
            return {
                "status": "already_running",
                "managed_process": None if managed is None else self._managed_process_summary(managed),
                "health": direct_health.model_dump(mode="json"),
                "adopted_existing_endpoint": True,
            }
        if not thermal_decision.should_start_new_local_work:
            return {
                "status": "deferred_for_thermal",
                "thermal_state": {
                    "preference": thermal_state.preference,
                    "thermal_pressure": thermal_state.thermal_pressure,
                    "detail": thermal_state.detail,
                },
                "thermal_decision": thermal_decision.__dict__,
            }
        return self.start_local_runtime(model_id=model_id, profile_id=profile_id)

    def stop_local_runtime(self) -> dict[str, Any]:
        manager = self._local_runtime_manager()
        status = manager.stop_managed()
        return {
            "status": "stopped",
            "managed_process": self._managed_process_summary(status),
        }

    def relay_pairing(self, *, profile_id: str | None = None, label: str = "Astrata Desktop", ttl_minutes: int = 15) -> dict[str, Any]:
        profile = self._preferred_relay_profile(profile_id=profile_id)
        if profile is None:
            return {"status": "unavailable", "reason": "no_relay_profile"}
        relay_endpoint = str(profile.relay_endpoint or "").strip().rstrip("/")
        auth_token = str(profile.auth_token or "").strip()
        if not relay_endpoint:
            return {"status": "unavailable", "reason": "missing_relay_endpoint", "profile": profile.model_dump(mode="json")}
        if not auth_token:
            return {"status": "unavailable", "reason": "missing_relay_auth_token", "profile": profile.model_dump(mode="json")}
        payload = {
            "profile_id": profile.profile_id,
            "label": label,
            "ttl_minutes": max(1, min(60, int(ttl_minutes or 15))),
        }
        account = self._account_auth_snapshot(profile_id=profile.profile_id)
        linked_user = account.get("user") or {}
        linked_device = account.get("device") or {}
        if account.get("status") in {"linked", "partial"}:
            if linked_user.get("user_id"):
                payload["user_id"] = str(linked_user["user_id"])
            if linked_device.get("device_id"):
                payload["device_id"] = str(linked_device["device_id"])
        request = urllib.request.Request(
            relay_endpoint + "/relay/pairing/create",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Authorization": f"Bearer {auth_token}",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36 AstrataDesktop/0.1"
                ),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10.0) as response:
                body = json.loads(response.read().decode("utf-8") or "{}")
            return {
                "status": "ok",
                "profile": profile.model_dump(mode="json"),
                "pairing": body,
                "connector_urls": self._relay_connector_urls(relay_endpoint),
            }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return {
                "status": "failed",
                "reason": f"http_{exc.code}",
                "detail": detail,
                "profile": profile.model_dump(mode="json"),
                "connector_urls": self._relay_connector_urls(relay_endpoint),
            }
        except Exception as exc:
            return {
                "status": "failed",
                "reason": str(exc),
                "profile": profile.model_dump(mode="json"),
                "connector_urls": self._relay_connector_urls(relay_endpoint),
            }

    def register_account_device(
        self,
        *,
        email: str,
        display_name: str = "",
        device_label: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        profile = self._preferred_relay_profile(profile_id=profile_id)
        if profile is None:
            return {"status": "unavailable", "reason": "no_relay_profile"}
        relay_endpoint = str(profile.relay_endpoint or "").strip().rstrip("/")
        registry = self._account_registry()
        try:
            result = registry.register_desktop_device(
                email=email,
                display_name=display_name,
                device_label=(device_label or self._default_device_label()).strip(),
                profile_id=profile.profile_id,
                relay_endpoint=relay_endpoint,
                profile_label=str(profile.label or "").strip(),
                control_posture=str(profile.control_posture or "").strip() or "true_remote_prime",
                disclosure_tier=str(profile.max_disclosure_tier or "").strip() or "trusted_remote",
                device_platform=self._device_platform_label(),
            )
        except ValueError as exc:
            return {"status": "failed", "reason": str(exc)}
        return {
            **result,
            "connector_urls": self._relay_connector_urls(relay_endpoint),
        }

    def redeem_invite_code(
        self,
        *,
        email: str,
        invite_code: str,
        display_name: str = "",
    ) -> dict[str, Any]:
        registry = self._account_registry()
        try:
            result = registry.redeem_invite_code(
                email=email,
                code=invite_code,
                display_name=display_name,
            )
        except ValueError as exc:
            return {
                "status": "failed",
                "reason": str(exc),
                "hosted_bridge_eligibility": registry.hosted_bridge_eligibility(email=email),
                "access_policy": registry.access_policy(),
            }
        return {
            **result,
            "access_policy": registry.access_policy(),
        }

    # ── Preferences (persisted user settings) ────────────────────────────

    def get_preferences(self) -> dict[str, Any]:
        """Return the current persisted preferences, with defaults filled in."""
        prefs = self._load_preferences()
        channel = str(prefs.get("update_channel") or _DEFAULT_UPDATE_CHANNEL).strip().lower()
        if channel not in _UPDATE_CHANNELS:
            channel = _DEFAULT_UPDATE_CHANNEL
        prefs["update_channel"] = channel
        return prefs

    def set_preferences(self, data: dict[str, Any]) -> dict[str, Any]:
        """Merge *data* into the persisted preferences and return the updated state."""
        prefs = self._load_preferences()
        if "update_channel" in data:
            channel = str(data["update_channel"] or _DEFAULT_UPDATE_CHANNEL).strip().lower()
            if channel not in _UPDATE_CHANNELS:
                raise ValueError(f"Unknown update channel: {channel!r}. Valid: {list(_UPDATE_CHANNELS)}")
            prefs["update_channel"] = channel
        self._save_preferences(prefs)
        return self.get_preferences()

    def _preferences_path(self) -> Path:
        return self.settings.paths.data_dir / "preferences.json"

    def _load_preferences(self) -> dict[str, Any]:
        path = self._preferences_path()
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def _save_preferences(self, prefs: dict[str, Any]) -> None:
        path = self._preferences_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")

    def _update_channel_snapshot(self) -> dict[str, Any]:
        prefs = self.get_preferences()
        current = str(prefs.get("update_channel") or _DEFAULT_UPDATE_CHANNEL)
        channels = [
            {
                "channel_id": channel_id,
                **{k: v for k, v in meta.items()},
                "selected": channel_id == current,
            }
            for channel_id, meta in _UPDATE_CHANNELS.items()
        ]
        return {
            "selected": current,
            "channels": channels,
        }

    def _db(self) -> AstrataDatabase:
        db = AstrataDatabase(self.settings.paths.data_dir / "astrata.db")
        db.initialize()
        return db

    def _account_registry(self) -> AccountControlPlaneRegistry:
        return AccountControlPlaneRegistry.from_settings(self.settings)

    def _relay_service(self) -> HostedMCPRelayService:
        return HostedMCPRelayService.from_settings(self.settings)

    def _relay_snapshot(self) -> dict[str, Any]:
        relay = self._relay_service()
        summary = relay.telemetry_summary()
        selected = self._preferred_relay_profile()
        return {
            **summary,
            "selected_profile": None if selected is None else selected.model_dump(mode="json"),
            "connector_urls": {} if selected is None else self._relay_connector_urls(str(selected.relay_endpoint or "").strip()),
        }

    def _account_auth_snapshot(self, *, profile_id: str | None = None) -> dict[str, Any]:
        selected = self._preferred_relay_profile(profile_id=profile_id)
        relay_endpoint = "" if selected is None else str(selected.relay_endpoint or "").strip()
        registry = self._account_registry()
        snapshot = registry.desktop_status(
            profile_id=None if selected is None else selected.profile_id,
            relay_endpoint=relay_endpoint,
        )
        return {
            **snapshot,
            "access_policy": registry.access_policy(),
            "device_label_suggestion": self._default_device_label(),
            "selected_relay_profile": None if selected is None else selected.model_dump(mode="json"),
            "connector_urls": {} if selected is None else self._relay_connector_urls(relay_endpoint),
        }

    def _preferred_relay_profile(self, profile_id: str | None = None):
        relay = self._relay_service()
        if profile_id:
            return relay.get_profile(profile_id)
        profiles = relay.list_profiles()
        if not profiles:
            return None
        chatgpt_profiles = [profile for profile in profiles if str(profile.exposure or "").strip().lower() == "chatgpt"]
        return (chatgpt_profiles or profiles)[0]

    def _relay_connector_urls(self, relay_endpoint: str) -> dict[str, str]:
        base = str(relay_endpoint or "").strip().rstrip("/")
        if not base:
            return {}
        return {
            "relay": f"{base}/mcp",
            "openapi": f"{base}/gpt/openapi.json",
            "privacy": f"{base}/privacy",
            "oauth_authorization_server": f"{base}/.well-known/oauth-authorization-server",
            "oauth_protected_resource": f"{base}/.well-known/oauth-protected-resource",
        }

    def _default_device_label(self) -> str:
        host = str(socket.gethostname() or "").strip()
        if not host:
            host = "This Desktop"
        return host

    def _device_platform_label(self) -> str:
        system = str(platform.system() or "").strip().lower()
        if system == "darwin":
            return "desktop-macos"
        if system == "windows":
            return "desktop-windows"
        if system == "linux":
            return "desktop-linux"
        return "desktop"

    def _agent_snapshot(self, agent_id: str) -> dict[str, Any] | None:
        registry = DurableAgentRegistry.from_settings(self.settings)
        registry.ensure_bootstrap_agents()
        agent = registry.get(agent_id)
        if agent is None:
            return None
        binding = dict(agent.inference_binding or {})
        fallback_policy = dict(agent.fallback_policy or {})
        fallback_id = str(fallback_policy.get("fallback_agent_id") or "").strip() or None
        fallback_agent = registry.get(fallback_id) if fallback_id else None
        display_route = self._route_display(binding)
        return {
            "agent_id": agent.agent_id,
            "title": agent.title,
            "role": agent.role,
            "status": agent.status,
            "inference_binding": binding,
            "display_route": display_route,
            "fallback_agent_id": fallback_id,
            "fallback_title": None if fallback_agent is None else fallback_agent.title,
            "fallback_route": None if fallback_agent is None else self._route_display(dict(fallback_agent.inference_binding or {})),
            "queue_if_unavailable": bool(fallback_policy.get("queue_if_unavailable", False)),
        }

    def _route_display(self, route: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(route or {})
        provider = str(payload.get("provider") or "").strip()
        cli_tool = str(payload.get("cli_tool") or "").strip()
        model = str(payload.get("model") or "").strip() or None
        label = provider or "unknown"
        if cli_tool:
            label = f"{provider}:{cli_tool}" if provider else cli_tool
        return {
            "provider": provider or None,
            "cli_tool": cli_tool or None,
            "model": model,
            "label": label,
        }

    def _local_runtime_manager(self) -> LocalRuntimeManager:
        process_controller = ManagedProcessController(
            state_path=self.settings.paths.data_dir / "local_runtime.json",
            log_path=self.settings.paths.data_dir / "local_runtime.log",
        )
        manager = LocalRuntimeManager(
            backends={"llama_cpp": LlamaCppBackend()},
            process_controller=process_controller,
        )
        manager.discover_models(search_paths=self.settings.local_runtime.model_search_paths)
        if self.settings.local_runtime.llama_cpp_base_url:
            manager.select_runtime(
                backend_id="llama_cpp",
                mode="external",
                endpoint=self.settings.local_runtime.llama_cpp_base_url,
            )
        return manager

    def _configured_local_runtime_health(self, manager: LocalRuntimeManager) -> RuntimeHealthSnapshot | None:
        backend = manager.backend("llama_cpp")
        if backend is None:
            return None
        health = backend.healthcheck(
            config={
                "host": self.settings.local_runtime.llama_cpp_host,
                "port": self.settings.local_runtime.llama_cpp_port,
            }
        )
        return RuntimeHealthSnapshot(
            backend_id=backend.backend_id,
            ok=health.ok,
            status=health.status,
            endpoint=health.endpoint,
            detail=health.detail,
            metadata=dict(health.metadata or {}),
        )

    def _local_runtime_snapshot(self) -> dict[str, Any]:
        manager = self._local_runtime_manager()
        thermal_state = probe_thermal_state(preference=self.settings.local_runtime.thermal_preference)
        thermal_controller = ThermalController(state_path=self.settings.paths.data_dir / "thermal_state.json")
        thermal_decision = thermal_controller.evaluate(thermal_state)
        recommendation = manager.recommend(thermal_preference=self.settings.local_runtime.thermal_preference)
        managed = manager.managed_status()
        return {
            "thermal_preference": self.settings.local_runtime.thermal_preference,
            "thermal_state": {
                "preference": thermal_state.preference,
                "telemetry_available": thermal_state.telemetry_available,
                "thermal_pressure": thermal_state.thermal_pressure,
                "fans_allowed": thermal_state.fans_allowed,
                "detail": thermal_state.detail,
            },
            "thermal_decision": thermal_decision.__dict__,
            "recommendation": {
                "model": None if recommendation.model is None else recommendation.model.model_dump(mode="json"),
                "profile_id": recommendation.profile_id,
                "reason": recommendation.reason,
            },
            "selection": None if manager.current_selection() is None else manager.current_selection().model_dump(mode="json"),
            "managed_process": None if managed is None else self._managed_process_summary(managed),
            "models": [model.model_dump(mode="json") for model in manager.model_registry().list_models()[:12]],
        }

    def _desktop_backend_snapshot(self) -> dict[str, Any]:
        session_path = self.settings.paths.data_dir / "desktop-session.json"
        payload: dict[str, Any] = {}
        if session_path.exists():
            try:
                loaded = json.loads(session_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except Exception:
                payload = {}
        return {
            "session_path": str(session_path),
            "session_present": session_path.exists(),
            "backend_url": payload.get("backend_url"),
            "ui_port": payload.get("ui_port"),
            "started_by_desktop_shell": bool(payload.get("started_by_desktop_shell", False)),
            "backend_pid": payload.get("backend_pid"),
            "frontend_deliberately_closed": bool(payload.get("frontend_deliberately_closed", False)),
            "backend_deliberately_stopped": bool(payload.get("backend_deliberately_stopped", False)),
            "last_action": payload.get("last_action"),
            "started_at_unix_ms": payload.get("started_at_unix_ms"),
        }

    def _tasks(self, db: AstrataDatabase) -> list[TaskRecord]:
        tasks = [TaskRecord(**payload) for payload in db.iter_records("tasks")]
        return sorted(tasks, key=lambda item: item.updated_at, reverse=True)

    def _attempts(self, db: AstrataDatabase) -> list[AttemptRecord]:
        attempts = [AttemptRecord(**payload) for payload in db.iter_records("attempts")]
        return sorted(attempts, key=lambda item: item.started_at, reverse=True)

    def _artifacts(self, db: AstrataDatabase) -> list[ArtifactRecord]:
        artifacts = [ArtifactRecord(**payload) for payload in db.iter_records("artifacts")]
        return sorted(artifacts, key=lambda item: item.updated_at, reverse=True)

    def _verifications(self, db: AstrataDatabase) -> list[VerificationRecord]:
        verifications = [VerificationRecord(**payload) for payload in db.iter_records("verifications")]
        return sorted(verifications, key=lambda item: item.created_at, reverse=True)

    def _communications(self, db: AstrataDatabase) -> list[CommunicationRecord]:
        communications = [CommunicationRecord(**payload) for payload in db.iter_records("communications")]
        return sorted(communications, key=lambda item: item.created_at, reverse=True)

    def _quota_policy(self, db: AstrataDatabase, registry: ProviderRegistry) -> QuotaPolicy:
        limits = default_source_limits()
        limits["codex"] = self.settings.runtime_limits.codex_direct_requests_per_hour
        limits["cli:codex-cli"] = self.settings.runtime_limits.codex_cli_requests_per_hour
        limits["cli:kilocode"] = self.settings.runtime_limits.kilocode_requests_per_hour
        limits["cli:gemini-cli"] = self.settings.runtime_limits.gemini_requests_per_hour
        limits["cli:claude-code"] = self.settings.runtime_limits.claude_requests_per_hour
        limits["openai"] = self.settings.runtime_limits.openai_requests_per_hour
        limits["google"] = self.settings.runtime_limits.google_requests_per_hour
        limits["anthropic"] = self.settings.runtime_limits.anthropic_requests_per_hour
        limits["custom"] = self.settings.runtime_limits.custom_requests_per_hour
        return QuotaPolicy(db=db, limits_per_source=limits, registry=registry)

    def _route_cost_rank(self, route: dict[str, Any]) -> int:
        provider = str(route.get("provider") or "").strip().lower()
        cli_tool = str(route.get("cli_tool") or "").strip().lower()
        model = str(route.get("model") or "").strip().lower()
        if provider == "cli":
            if cli_tool == "kilocode":
                return 1
            if cli_tool == "gemini-cli":
                if "flash" in model:
                    return 2
                if "pro" in model:
                    return 4
                return 3
            if cli_tool == "claude-code":
                return 6
            if cli_tool == "codex-cli":
                return 7
        if provider == "google":
            return 5
        if provider == "openai":
            return 8
        if provider == "anthropic":
            return 7
        return 9

    def _inference_source_route(self, source: dict[str, Any]) -> dict[str, Any]:
        route = {
            "provider": source.get("provider"),
            "cli_tool": source.get("cli_tool"),
        }
        model = source.get("default_model")
        if model:
            route["model"] = model
        return route

    def _quota_snapshots(self, db: AstrataDatabase, registry: ProviderRegistry) -> list[dict[str, Any]]:
        quota_policy = self._quota_policy(db, registry)
        snapshots: list[dict[str, Any]] = []
        for source in registry.list_available_inference_sources():
            route = self._inference_source_route(source)
            snapshots.append(
                build_quota_snapshot(
                    route=route,
                    decision=quota_policy.assess(route),
                    cost_rank=self._route_cost_rank(route),
                )
            )
        return snapshots

    def _task_summary(self, task: TaskRecord) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "title": task.title,
            "description": task.description,
            "status": task.status,
            "priority": task.priority,
            "urgency": task.urgency,
            "risk": task.risk,
            "updated_at": task.updated_at,
            "provenance": task.provenance,
            "completion_policy": task.completion_policy,
            "target_lane": dict(task.provenance or {}).get("target_lane"),
            "source_conversation_id": dict(task.provenance or {}).get("source_conversation_id"),
        }

    def _attempt_summary(self, attempt: AttemptRecord) -> dict[str, Any]:
        return {
            "attempt_id": attempt.attempt_id,
            "task_id": attempt.task_id,
            "actor": attempt.actor,
            "outcome": attempt.outcome,
            "result_summary": attempt.result_summary,
            "failure_kind": attempt.failure_kind,
            "degraded_reason": attempt.degraded_reason,
            "verification_status": attempt.verification_status,
            "started_at": attempt.started_at,
            "resource_usage": attempt.resource_usage,
        }

    def _message_summary(self, record: CommunicationRecord) -> dict[str, Any]:
        return {
            "communication_id": record.communication_id,
            "kind": record.kind,
            "sender": record.sender,
            "recipient": record.recipient,
            "intent": record.intent,
            "status": record.status,
            "message": record.payload.get("message"),
            "created_at": record.created_at,
            "related_task_ids": record.related_task_ids,
            "channel": record.channel,
            "conversation_id": record.conversation_id,
        }

    def _artifact_summary(self, artifact: ArtifactRecord) -> dict[str, Any]:
        return {
            "artifact_id": artifact.artifact_id,
            "artifact_type": artifact.artifact_type,
            "title": artifact.title,
            "status": artifact.status,
            "lifecycle_state": artifact.lifecycle_state,
            "content_summary": artifact.content_summary,
            "updated_at": artifact.updated_at,
        }

    def _managed_process_summary(self, status: Any) -> dict[str, Any]:
        return {
            "running": status.running,
            "pid": status.pid,
            "endpoint": status.endpoint,
            "command": list(status.command),
            "log_path": status.log_path,
            "started_at": status.started_at,
            "detail": status.detail,
        }

    def _lane_count(self, lane: PrincipalMessageLane, recipient: str) -> int:
        return len(lane.list_messages(recipient=recipient))

    def _messages_for_task(self, db: AstrataDatabase, task_id: str) -> list[CommunicationRecord]:
        task = next((task for task in self._tasks(db) if task.task_id == task_id), None)
        source_communication_id = None if task is None else str(task.provenance.get("source_communication_id") or "")
        messages = []
        for message in self._communications(db):
            if task_id in message.related_task_ids:
                messages.append(message)
                continue
            if source_communication_id and message.communication_id == source_communication_id:
                messages.append(message)
                continue
            if source_communication_id and source_communication_id in str(message.payload):
                messages.append(message)
        return messages

    def _task_relationships(self, db: AstrataDatabase, task: TaskRecord) -> dict[str, Any]:
        tasks = self._tasks(db)
        provenance = dict(task.provenance or {})
        parent_task_id = str(provenance.get("parent_task_id") or "").strip()
        source_communication_id = str(provenance.get("source_communication_id") or "").strip()
        parent = next((item for item in tasks if item.task_id == parent_task_id), None) if parent_task_id else None
        children = [item for item in tasks if str(dict(item.provenance or {}).get("parent_task_id") or "").strip() == task.task_id]
        siblings = []
        if parent_task_id:
            siblings = [
                item for item in tasks
                if item.task_id != task.task_id
                and str(dict(item.provenance or {}).get("parent_task_id") or "").strip() == parent_task_id
            ]
        same_source = []
        if source_communication_id:
            same_source = [
                item for item in tasks
                if item.task_id != task.task_id
                and str(dict(item.provenance or {}).get("source_communication_id") or "").strip() == source_communication_id
            ]
        return {
            "parent": None if parent is None else self._task_summary(parent),
            "children": [self._task_summary(item) for item in children[:12]],
            "siblings": [self._task_summary(item) for item in siblings[:12]],
            "same_source": [self._task_summary(item) for item in same_source[:12]],
        }

    def _task_blockers(self, db: AstrataDatabase, task: TaskRecord) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        relationships = self._task_relationships(db, task)
        pending_children = [
            child for child in relationships["children"]
            if child["status"] in {"pending", "working", "blocked"}
        ]
        if pending_children:
            blockers.append(
                {
                    "kind": "active_children",
                    "summary": f"{len(pending_children)} child task(s) still in flight.",
                    "tasks": pending_children,
                }
            )
        if task.status == "blocked":
            blockers.append(
                {
                    "kind": "task_blocked",
                    "summary": "This task is marked blocked and likely needs intervention or retry.",
                }
            )
        attempts = [attempt for attempt in self._attempts(db) if attempt.task_id == task.task_id]
        if not attempts and task.status == "pending":
            blockers.append(
                {
                    "kind": "unstarted",
                    "summary": "No attempt has started yet.",
                }
            )
        return blockers

    def _artifact_relates_to_task(self, artifact: ArtifactRecord, task_id: str) -> bool:
        provenance = artifact.provenance or {}
        if provenance.get("task_id") == task_id:
            return True
        if provenance.get("source_task_id") == task_id:
            return True
        related = provenance.get("related_task_ids") or []
        if task_id in related:
            return True
        content = artifact.content_summary or ""
        return task_id in content

    def _history_snapshot(
        self,
        *,
        tasks: list[TaskRecord],
        attempts: list[AttemptRecord],
        artifacts: list[ArtifactRecord],
        verifications: list[VerificationRecord],
        communications: list[CommunicationRecord],
        inference_telemetry: dict[str, Any],
    ) -> dict[str, Any]:
        recent_events = self._history_events(
            tasks=tasks,
            attempts=attempts,
            artifacts=artifacts,
            verifications=verifications,
            communications=communications,
        )
        return {
            "window_hours": int(inference_telemetry.get("window_hours") or 24),
            "overview": {
                "tasks_total": len(tasks),
                "attempts_total": len(attempts),
                "artifacts_total": len(artifacts),
                "verifications_total": len(verifications),
                "communications_total": len(communications),
                "blocked_tasks": sum(1 for task in tasks if task.status == "blocked"),
                "failed_attempts": sum(1 for attempt in attempts if attempt.outcome == "failed"),
                "pending_tasks": sum(1 for task in tasks if task.status == "pending"),
                "prime_attempts": int(inference_telemetry.get("prime_spend_attempts") or 0),
                "avoidable_prime_attempts": int(inference_telemetry.get("avoidable_prime_attempts") or 0),
                "unjustified_prime_attempts": int(inference_telemetry.get("unjustified_prime_attempts") or 0),
            },
            "runtime": self._history_runtime_status(tasks=tasks, attempts=attempts, artifacts=artifacts),
            "bottlenecks": self._history_bottlenecks(tasks=tasks, attempts=attempts, inference_telemetry=inference_telemetry),
            "snapshot_reports": [self._artifact_summary(artifact) for artifact in artifacts[:10] if self._is_history_worthy_artifact(artifact)],
            "recent_events": recent_events[:24],
            "git": self._git_snapshot(),
        }

    def _history_events(
        self,
        *,
        tasks: list[TaskRecord],
        attempts: list[AttemptRecord],
        artifacts: list[ArtifactRecord],
        verifications: list[VerificationRecord],
        communications: list[CommunicationRecord],
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for task in tasks[:12]:
            events.append(
                {
                    "event_kind": "task",
                    "event_id": task.task_id,
                    "title": task.title,
                    "summary": task.description,
                    "status": task.status,
                    "timestamp": task.updated_at,
                }
            )
        for attempt in attempts[:12]:
            events.append(
                {
                    "event_kind": "attempt",
                    "event_id": attempt.attempt_id,
                    "title": attempt.actor,
                    "summary": attempt.result_summary or attempt.failure_kind or "Execution attempt recorded.",
                    "status": attempt.outcome,
                    "timestamp": attempt.ended_at or attempt.started_at,
                }
            )
        for artifact in artifacts[:12]:
            events.append(
                {
                    "event_kind": "artifact",
                    "event_id": artifact.artifact_id,
                    "title": artifact.title,
                    "summary": artifact.content_summary or artifact.description or artifact.artifact_type,
                    "status": artifact.status,
                    "timestamp": artifact.updated_at,
                }
            )
        for verification in verifications[:8]:
            events.append(
                {
                    "event_kind": "verification",
                    "event_id": verification.verification_id,
                    "title": f"{verification.target_kind}:{verification.target_id}",
                    "summary": f"{verification.verifier} verification recorded.",
                    "status": verification.result,
                    "timestamp": verification.created_at,
                }
            )
        for message in communications[:8]:
            body = str(message.payload.get("message") or "").strip()
            events.append(
                {
                    "event_kind": "communication",
                    "event_id": message.communication_id,
                    "title": f"{message.sender} -> {message.recipient}",
                    "summary": body or f"{message.intent or message.kind} message",
                    "status": message.status,
                    "timestamp": message.created_at,
                }
            )
        events.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return events

    def _history_bottlenecks(
        self,
        *,
        tasks: list[TaskRecord],
        attempts: list[AttemptRecord],
        inference_telemetry: dict[str, Any],
    ) -> list[dict[str, Any]]:
        bottlenecks: list[dict[str, Any]] = []
        blocked_tasks = sum(1 for task in tasks if task.status == "blocked")
        if blocked_tasks:
            bottlenecks.append(
                {
                    "title": "Blocked tasks",
                    "summary": f"{blocked_tasks} task(s) are currently blocked and may need intervention.",
                    "severity": "moderate" if blocked_tasks < 3 else "high",
                }
            )
        failed_attempts = sum(1 for attempt in attempts if attempt.outcome == "failed")
        if failed_attempts:
            bottlenecks.append(
                {
                    "title": "Failed attempts",
                    "summary": f"{failed_attempts} recent attempt(s) failed and may be consuming throughput without progress.",
                    "severity": "moderate" if failed_attempts < 3 else "high",
                }
            )
        quota_pressure = list(inference_telemetry.get("quota_pressure") or [])
        constrained = [item for item in quota_pressure if not bool(item.get("allowed", True))]
        if constrained:
            sources = ", ".join(str(item.get("source") or item.get("route", {}).get("provider") or "unknown") for item in constrained[:3])
            bottlenecks.append(
                {
                    "title": "Quota pressure",
                    "summary": f"One or more routes are currently throttled or exhausted: {sources}.",
                    "severity": "high",
                }
            )
        avoidable_prime_attempts = int(inference_telemetry.get("avoidable_prime_attempts") or 0)
        if avoidable_prime_attempts:
            bottlenecks.append(
                {
                    "title": "Avoidable Prime load",
                    "summary": f"{avoidable_prime_attempts} recent Prime invocation(s) look avoidable and should move to cheaper capable routes.",
                    "severity": "moderate",
                }
            )
        unjustified_prime_attempts = int(inference_telemetry.get("unjustified_prime_attempts") or 0)
        if unjustified_prime_attempts:
            bottlenecks.append(
                {
                    "title": "Prime policy drift",
                    "summary": f"{unjustified_prime_attempts} recent Prime invocation(s) lacked a recorded admission basis.",
                    "severity": "high",
                }
            )
        return bottlenecks[:8]

    def _history_runtime_status(
        self,
        *,
        tasks: list[TaskRecord],
        attempts: list[AttemptRecord],
        artifacts: list[ArtifactRecord],
    ) -> dict[str, Any]:
        heartbeat = next((artifact for artifact in artifacts if artifact.artifact_type == "loop0_daemon_heartbeat"), None)
        heartbeat_payload: dict[str, Any] = {}
        if heartbeat is not None:
            try:
                heartbeat_payload = json.loads(heartbeat.content_summary or "{}")
            except Exception:
                heartbeat_payload = {}
        last_success = next(
            (
                artifact
                for artifact in artifacts
                if artifact.artifact_type == "loop0_daemon_heartbeat"
                and str(dict(artifact.provenance or {}).get("status") or "").strip() == "ok"
            ),
            None,
        )
        last_failure = next(
            (
                artifact
                for artifact in artifacts
                if artifact.artifact_type == "loop0_daemon_heartbeat"
                and str(dict(artifact.provenance or {}).get("status") or "").strip() == "failed"
            ),
            None,
        )
        stale = False
        interval_seconds = int(heartbeat_payload.get("interval_seconds") or 0)
        if heartbeat is not None and interval_seconds > 0:
            try:
                updated_at = datetime.fromisoformat(str(heartbeat.updated_at))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                stale = (datetime.now(timezone.utc) - updated_at).total_seconds() > max(interval_seconds * 3, 180)
            except Exception:
                stale = False
        return {
            "daemon_configured": heartbeat is not None,
            "stale": stale,
            "latest_heartbeat": None if heartbeat is None else self._artifact_summary(heartbeat),
            "latest_heartbeat_payload": heartbeat_payload,
            "last_successful_heartbeat": None if last_success is None else self._artifact_summary(last_success),
            "last_failed_heartbeat": None if last_failure is None else self._artifact_summary(last_failure),
            "latest_attempt": None if not attempts else self._attempt_summary(attempts[0]),
            "latest_task": None if not tasks else self._task_summary(tasks[0]),
        }

    def _is_history_worthy_artifact(self, artifact: ArtifactRecord) -> bool:
        artifact_type = str(artifact.artifact_type or "").strip().lower()
        return any(
            token in artifact_type
            for token in ("telemetry", "review", "signal", "report", "summary", "history")
        )

    def _git_snapshot(self) -> dict[str, Any]:
        project_root = self.settings.paths.project_root
        status_proc = self._run_git(project_root, "status", "--short", "--branch")
        worktree_proc = self._run_git(project_root, "worktree", "list", "--porcelain")
        snapshot = {
            "available": bool(status_proc is not None and status_proc.returncode == 0),
            "branch": None,
            "head": None,
            "ahead": 0,
            "behind": 0,
            "dirty": False,
            "modified_count": 0,
            "modified_paths": [],
            "worktrees": [],
        }
        if status_proc is None or status_proc.returncode != 0:
            return snapshot
        status_lines = [line.rstrip() for line in status_proc.stdout.splitlines() if line.strip()]
        if status_lines:
            branch_line = status_lines[0]
            if branch_line.startswith("## "):
                snapshot.update(self._parse_git_branch_line(branch_line[3:]))
            modified_paths = []
            for line in status_lines[1:]:
                payload = line[3:] if len(line) > 3 else line
                modified_paths.append(payload.strip())
            snapshot["dirty"] = bool(modified_paths)
            snapshot["modified_count"] = len(modified_paths)
            snapshot["modified_paths"] = modified_paths[:12]
        if worktree_proc is not None:
            snapshot["worktrees"] = self._parse_worktree_list(worktree_proc.stdout)
        return snapshot

    def _run_git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", "-C", str(cwd), *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return None

    def _parse_git_branch_line(self, payload: str) -> dict[str, Any]:
        branch = payload
        ahead = 0
        behind = 0
        if "..." in payload:
            branch, tracking = payload.split("...", 1)
            if "[" in tracking and "]" in tracking:
                tracking, bracket = tracking.split("[", 1)
                details = bracket.rstrip("]")
                for item in [piece.strip() for piece in details.split(",")]:
                    if item.startswith("ahead "):
                        ahead = int(item.split(" ", 1)[1] or 0)
                    elif item.startswith("behind "):
                        behind = int(item.split(" ", 1)[1] or 0)
        return {
            "branch": branch.strip(),
            "head": None,
            "ahead": ahead,
            "behind": behind,
        }

    def _parse_worktree_list(self, payload: str) -> list[dict[str, Any]]:
        worktrees: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for raw_line in payload.splitlines():
            line = raw_line.strip()
            if not line:
                if current:
                    worktrees.append(current)
                    current = None
                continue
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line.split(" ", 1)[1], "branch": None, "head": None}
                continue
            if current is None:
                continue
            if line.startswith("HEAD "):
                current["head"] = line.split(" ", 1)[1]
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1]
            elif line == "bare":
                current["bare"] = True
            elif line == "detached":
                current["detached"] = True
        if current:
            worktrees.append(current)
        return worktrees[:12]
