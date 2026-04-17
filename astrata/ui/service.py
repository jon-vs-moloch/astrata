"""Aggregation and action helpers for Astrata's first local UI shell."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import socket
import subprocess
from urllib.parse import urlencode
from typing import Any

from astrata.accounts import AccountControlPlaneRegistry
from astrata.comms.intake import process_inbound_messages
from astrata.comms.lanes import PrincipalMessageLane
from astrata.comms.runtime import LaneRuntime
from astrata.config.settings import Settings, load_settings
from astrata.context import build_quota_snapshot, summarize_inference_activity
from astrata.governance.documents import load_governance_bundle
from astrata.local.backends.llama_cpp import LlamaCppBackend
from astrata.local.hardware import probe_thermal_state
from astrata.local.runtime.manager import LocalRuntimeManager
from astrata.local.runtime.processes import ManagedProcessController
from astrata.local.thermal import ThermalController
from astrata.loop0.runner import Loop0Runner
from astrata.mcp.models import HostedMCPRelayLink, HostedMCPRelayProfile
from astrata.mcp.relay import HostedMCPRelayService
from astrata.providers.registry import ProviderRegistry, build_default_registry
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
            "startup": {
                "preflight": startup_preflight,
                "runtime": startup_runtime,
            },
            "agents": {
                "prime": {"agent_id": "prime", "title": "Prime", "display_route": {"label": "codex"}},
                "local": {"agent_id": "local", "title": "Local", "display_route": {"label": "local"}},
            },
            "desktop_backend": self._desktop_backend_snapshot(),
            "relay": self._relay_snapshot(),
            "voice": VoiceService(settings=self.settings).status(),
            "account_auth": self._account_auth_snapshot(),
            "local_runtime": self._local_runtime_snapshot(),
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
        if not thermal_decision.should_start_new_local_work:
            return {"status": "deferred_for_thermal", "thermal_decision": thermal_decision.__dict__}

        managed = manager.managed_status()
        health = manager.health()
        if managed is not None and getattr(managed, "running", False) and health is not None and getattr(health, "ok", False):
            return {
                "status": "already_running",
                "health": health.model_dump(mode="json") if hasattr(health, "model_dump") else {"ok": True},
                "managed_process": self._managed_process_summary(managed),
            }

        existing_health = None
        backend = manager.backend("llama_cpp") if hasattr(manager, "backend") else None
        if backend is not None and hasattr(backend, "healthcheck"):
            try:
                existing_health = backend.healthcheck(
                    config={"host": self.settings.local_runtime.llama_cpp_host, "port": self.settings.local_runtime.llama_cpp_port}
                )
            except Exception:
                existing_health = None
        if existing_health is not None and getattr(existing_health, "ok", False):
            endpoint = getattr(existing_health, "endpoint", None) or f"http://{self.settings.local_runtime.llama_cpp_host}:{self.settings.local_runtime.llama_cpp_port}/health"
            selected_model_id = model_id or getattr(getattr(recommendation, "model", None), "model_id", None)
            manager.select_runtime(
                backend_id="llama_cpp",
                model_id=selected_model_id,
                mode="external",
                profile_id=profile_id or recommendation.profile_id,
                endpoint=endpoint,
                metadata={"adopted_existing_endpoint": True, "source": "ui.ensure_local_runtime"},
            )
            return {
                "status": "already_running",
                "adopted_existing_endpoint": True,
                "health": existing_health.model_dump(mode="json") if hasattr(existing_health, "model_dump") else {"ok": True},
            }
        return self.start_local_runtime(model_id=model_id, profile_id=profile_id)

    def stop_local_runtime(self) -> dict[str, Any]:
        manager = self._local_runtime_manager()
        status = manager.stop_managed()
        return {
            "status": "stopped",
            "managed_process": self._managed_process_summary(status),
        }

    def redeem_invite_code(self, *, email: str, display_name: str = "", invite_code: str) -> dict[str, Any]:
        return self._account_registry().redeem_invite_code(
            email=email,
            display_name=display_name,
            invite_code=invite_code,
        )

    def pair_desktop_device(
        self,
        *,
        email: str,
        label: str = "Astrata Desktop",
        relay_endpoint: str = "",
    ) -> dict[str, Any]:
        result = self._account_registry().pair_device_for_user(
            email=email,
            label=label,
            device_kind="desktop",
            relay_endpoint=relay_endpoint,
        )
        if result.get("status") == "ok":
            profile = dict(result.get("profile") or {})
            device = dict(result.get("device") or {})
            try:
                relay = self._relay_service()
                relay.register_profile(
                    HostedMCPRelayProfile(
                        profile_id=str(profile.get("profile_id") or ""),
                        user_id=str(profile.get("user_id") or ""),
                        default_device_id=str(device.get("device_id") or ""),
                        label=str(profile.get("label") or "ChatGPT Connector"),
                        exposure="chatgpt",
                        auth_token="",
                    )
                )
                relay.register_local_link(
                    HostedMCPRelayLink(
                        profile_id=str(profile.get("profile_id") or ""),
                        bridge_id=f"desktop-{str(device.get('device_id') or '')[:8]}",
                        device_id=str(device.get("device_id") or ""),
                        link_token=str(result.get("link_token") or ""),
                        status="online",
                    )
                )
            except Exception as exc:
                result["relay_registration_warning"] = str(exc)
        return result

    def connector_oauth_setup(
        self,
        *,
        callback_url: str,
        label: str = "ChatGPT Connector",
        email: str = "",
        relay_endpoint: str = "",
    ) -> dict[str, Any]:
        normalized_callback = str(callback_url or "").strip()
        if not normalized_callback:
            return {"status": "missing_callback_url"}
        registry = self._account_registry()
        client = registry.register_oauth_client(
            label=label or "ChatGPT Connector",
            redirect_uris=[normalized_callback],
            client_kind="chatgpt_connector",
            metadata={"registered_via": "astrata_ui"},
        )
        base = self._connector_base_url(relay_endpoint) or self._connector_base_url(
            str(dict(self._account_auth_snapshot().get("device_link") or {}).get("relay_endpoint") or "")
        )
        authorize_url = ""
        if base:
            query = {
                "client_id": client["client"]["client_id"],
                "redirect_uri": normalized_callback,
                "response_type": "code",
                "scope": "relay:use",
            }
            if email:
                query["email"] = str(email).strip().lower()
            authorize_url = f"{base}/oauth/authorize?{urlencode(query)}"
        return {
            "status": "ok",
            "client": client["client"],
            "authorize_url": authorize_url,
            "callback_url": normalized_callback,
            "connector_urls": self._relay_connector_urls(base),
        }

    def get_preferences(self) -> dict[str, Any]:
        prefs = self._load_preferences()
        return {
            "update_channel": str(prefs.get("update_channel") or _DEFAULT_UPDATE_CHANNEL),
        }

    def set_preferences(self, data: dict[str, Any]) -> dict[str, Any]:
        prefs = self._load_preferences()
        if "update_channel" in data:
            requested = str(data.get("update_channel") or "").strip().lower()
            if requested in _UPDATE_CHANNELS:
                prefs["update_channel"] = requested
        self._save_preferences(prefs)
        return self.get_preferences()

    def _db(self) -> AstrataDatabase:
        db = AstrataDatabase(self.settings.paths.data_dir / "astrata.db")
        db.initialize()
        return db

    def _account_registry(self) -> AccountControlPlaneRegistry:
        return AccountControlPlaneRegistry.from_settings(self.settings)

    def _relay_service(self) -> HostedMCPRelayService:
        return HostedMCPRelayService.from_settings(self.settings)

    def _preferences_path(self) -> Path:
        return self.settings.paths.data_dir / "ui-preferences.json"

    def _load_preferences(self) -> dict[str, Any]:
        path = self._preferences_path()
        if not path.exists():
            return {"update_channel": _DEFAULT_UPDATE_CHANNEL}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"update_channel": _DEFAULT_UPDATE_CHANNEL}
        if not isinstance(payload, dict):
            return {"update_channel": _DEFAULT_UPDATE_CHANNEL}
        payload.setdefault("update_channel", _DEFAULT_UPDATE_CHANNEL)
        return payload

    def _save_preferences(self, prefs: dict[str, Any]) -> None:
        self._preferences_path().write_text(json.dumps(prefs, indent=2), encoding="utf-8")

    def _update_channel_snapshot(self) -> dict[str, Any]:
        prefs = self.get_preferences()
        current = str(prefs.get("update_channel") or _DEFAULT_UPDATE_CHANNEL)
        channels = [
            {"channel_id": channel_id, **meta, "selected": channel_id == current}
            for channel_id, meta in _UPDATE_CHANNELS.items()
        ]
        return {"selected": current, "channels": channels}

    def _desktop_backend_snapshot(self) -> dict[str, Any]:
        session_path = self.settings.paths.data_dir / "desktop-session.json"
        if not session_path.exists():
            return {"session_present": False, "backend_running": False}
        try:
            payload = json.loads(session_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"session_present": True, "status": "unreadable", "detail": str(exc)}
        if not isinstance(payload, dict):
            return {"session_present": True, "status": "invalid"}
        backend_url = str(payload.get("backend_url") or "").strip()
        return {
            "session_present": True,
            "backend_running": bool(backend_url) and not bool(payload.get("backend_deliberately_stopped")),
            **payload,
        }

    def _relay_snapshot(self) -> dict[str, Any]:
        relay = self._relay_service()
        summary = relay.telemetry_summary()
        selected = self._preferred_relay_profile(relay)
        connector_urls = {}
        queue_state: dict[str, Any] = {
            "pending": [],
            "acked": [],
            "results": [],
            "sessions": [],
            "counts": {"pending": 0, "acked": 0, "results": 0, "sessions": 0},
        }
        if selected is not None:
            link = self._account_registry().active_device_link_for_profile(selected.profile_id)
            connector_urls = self._relay_connector_urls("" if link is None else str(link.relay_endpoint or "").strip())
            queue_state = self._relay_queue_snapshot(relay, selected.profile_id)
        return {
            **summary,
            "selected_profile": None if selected is None else selected.model_dump(mode="json"),
            "connector_urls": connector_urls,
            "queue_state": queue_state,
        }

    def _account_auth_snapshot(self, *, profile_id: str | None = None) -> dict[str, Any]:
        registry = self._account_registry()
        selected_profile = registry.get_profile(profile_id) if profile_id else (registry.list_profiles()[0] if registry.list_profiles() else None)
        selected_profile_payload = None if selected_profile is None else selected_profile.model_dump(mode="json")
        relay_endpoint = ""
        if selected_profile is not None:
            link = registry.active_device_link_for_profile(selected_profile.profile_id)
            relay_endpoint = "" if link is None else str(link.relay_endpoint or "").strip()
        snapshot = registry.desktop_status(
            profile_id=None if selected_profile is None else selected_profile.profile_id,
            relay_endpoint=relay_endpoint,
        )
        return {
            **registry.summary(),
            **snapshot,
            "access_policy": registry.access_policy(),
            "hosted_bridge_eligibility": registry.hosted_bridge_eligibility(
                email=str(dict(snapshot.get("user") or {}).get("email") or "")
            ),
            "device_label_suggestion": self._default_device_label(),
            "selected_relay_profile": selected_profile_payload,
            "oauth": self._oauth_snapshot(),
            "connector_urls": self._relay_connector_urls(relay_endpoint),
        }

    def _preferred_relay_profile(self, relay: HostedMCPRelayService | None = None):
        service = relay or self._relay_service()
        profiles = service.list_profiles()
        if not profiles:
            return None
        chatgpt_profiles = [profile for profile in profiles if str(profile.exposure or "").strip().lower() == "chatgpt"]
        return (chatgpt_profiles or profiles)[0]

    def _relay_connector_urls(self, relay_endpoint: str) -> dict[str, str]:
        base = self._connector_base_url(relay_endpoint)
        if not base:
            return {}
        return {
            "relay": f"{base}/mcp",
            "openapi": f"{base}/gpt/openapi.json",
            "privacy": f"{base}/privacy",
            "oauth_authorization_server": f"{base}/.well-known/oauth-authorization-server",
            "oauth_protected_resource": f"{base}/.well-known/oauth-protected-resource",
            "oauth_authorize": f"{base}/oauth/authorize",
        }

    def _connector_base_url(self, relay_endpoint: str) -> str:
        base = str(relay_endpoint or "").strip().rstrip("/")
        if base.endswith("/mcp"):
            base = base.removesuffix("/mcp").rstrip("/")
        return base

    def _oauth_snapshot(self) -> dict[str, Any]:
        registry = self._account_registry()
        clients = registry.list_oauth_clients()
        tokens = registry.list_oauth_access_tokens()
        active_tokens = [token for token in tokens if token.get("status") == "active"]
        return {
            "clients": clients[:8],
            "tokens": [
                {
                    "client_id": token.get("client_id"),
                    "profile_id": token.get("profile_id"),
                    "device_id": token.get("device_id"),
                    "scope": token.get("scope"),
                    "status": token.get("status"),
                    "created_at": token.get("created_at"),
                    "expires_at": token.get("expires_at"),
                }
                for token in active_tokens[:8]
            ],
            "counts": {
                "clients": len(clients),
                "active_tokens": len(active_tokens),
                "total_tokens": len(tokens),
            },
        }

    def _relay_queue_snapshot(self, relay: HostedMCPRelayService, profile_id: str) -> dict[str, Any]:
        pending = relay.pending_requests(profile_id=profile_id)
        acked = relay.acked_requests(profile_id=profile_id)
        results = relay.results(profile_id=profile_id)
        sessions_payload = dict(relay._load().get("sessions") or {})  # noqa: SLF001
        sessions = list(dict(sessions_payload.get(profile_id) or {}).values())
        return {
            "pending": pending[-8:],
            "acked": acked[-8:],
            "results": results[-8:],
            "sessions": sessions[-8:],
            "counts": {
                "pending": len(pending),
                "acked": len(acked),
                "results": len(results),
                "sessions": len(sessions),
            },
        }

    def _default_device_label(self) -> str:
        host = str(socket.gethostname() or "").strip()
        return host or "Astrata Desktop"

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

    def _tasks(self, db: AstrataDatabase) -> list[TaskRecord]:
        tasks = [TaskRecord(**payload) for payload in db.list_records("tasks")]
        return sorted(tasks, key=lambda item: item.updated_at, reverse=True)

    def _attempts(self, db: AstrataDatabase) -> list[AttemptRecord]:
        attempts = [AttemptRecord(**payload) for payload in db.list_records("attempts")]
        return sorted(attempts, key=lambda item: item.started_at, reverse=True)

    def _artifacts(self, db: AstrataDatabase) -> list[ArtifactRecord]:
        artifacts = [ArtifactRecord(**payload) for payload in db.list_records("artifacts")]
        return sorted(artifacts, key=lambda item: item.updated_at, reverse=True)

    def _verifications(self, db: AstrataDatabase) -> list[VerificationRecord]:
        verifications = [VerificationRecord(**payload) for payload in db.list_records("verifications")]
        return sorted(verifications, key=lambda item: item.created_at, reverse=True)

    def _communications(self, db: AstrataDatabase) -> list[CommunicationRecord]:
        communications = [CommunicationRecord(**payload) for payload in db.list_records("communications")]
        return sorted(communications, key=lambda item: item.created_at, reverse=True)

    def _quota_policy(self, db: AstrataDatabase, registry: ProviderRegistry) -> QuotaPolicy:
        limits = default_source_limits()
        limits["codex"] = self.settings.runtime_limits.codex_requests_per_hour
        limits["cli:codex-cli"] = self.settings.runtime_limits.codex_requests_per_hour
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
            "running": getattr(status, "running", False),
            "pid": getattr(status, "pid", None),
            "endpoint": getattr(status, "endpoint", None),
            "command": list(getattr(status, "command", []) or []),
            "log_path": getattr(status, "log_path", None),
            "started_at": getattr(status, "started_at", None),
            "detail": getattr(status, "detail", None),
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
            "snapshot_reports": [
                self._artifact_summary(artifact)
                for artifact in artifacts[:10]
                if self._is_history_worthy_artifact(artifact)
            ],
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
            sources = ", ".join(
                str(item.get("source") or item.get("route", {}).get("provider") or "unknown")
                for item in constrained[:3]
            )
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
        return any(token in artifact_type for token in ("telemetry", "review", "signal", "report", "summary", "history"))

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
            return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)
        except Exception:
            return None

    def _parse_git_branch_line(self, payload: str) -> dict[str, Any]:
        branch = payload
        ahead = 0
        behind = 0
        if "..." in payload:
            branch, tracking = payload.split("...", 1)
            if "[" in tracking and "]" in tracking:
                _tracking, bracket = tracking.split("[", 1)
                details = bracket.rstrip("]")
                for item in [piece.strip() for piece in details.split(",")]:
                    if item.startswith("ahead "):
                        ahead = int(item.split(" ", 1)[1] or 0)
                    elif item.startswith("behind "):
                        behind = int(item.split(" ", 1)[1] or 0)
        return {"branch": branch.strip(), "head": None, "ahead": ahead, "behind": behind}

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
