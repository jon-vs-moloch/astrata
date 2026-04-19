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
from astrata.agents import DurableAgentRegistry
from astrata.chats import ChatThreadRegistry
from astrata.comms.intake import process_inbound_messages
from astrata.comms.lanes import PrincipalMessageLane
from astrata.comms.runtime import LaneRuntime
from astrata.config.settings import Settings, load_settings
from astrata.context import build_quota_snapshot, summarize_inference_activity
from astrata.governance.documents import load_governance_bundle
from astrata.local.backends.llama_cpp import LlamaCppBackend
from astrata.local.hardware import probe_thermal_state
from astrata.local.runtime.client import LocalRuntimeClient
from astrata.local.runtime.manager import LocalRuntimeManager
from astrata.local.runtime.processes import ManagedProcessController
from astrata.local.thermal import ThermalController
from astrata.loop0.runner import Loop0Runner
from astrata.mcp.models import HostedMCPRelayLink, HostedMCPRelayProfile
from astrata.mcp.relay import HostedMCPRelayService
from astrata.providers.base import CompletionRequest, Message
from astrata.providers.registry import ProviderRegistry, build_default_registry
from astrata.records.communications import CommunicationRecord
from astrata.records.models import AttemptRecord, ArtifactRecord, TaskRecord, VerificationRecord
from astrata.routing.policy import RouteChooser
from astrata.scheduling.quota import QuotaPolicy, default_source_limits
from astrata.storage.db import AstrataDatabase
from astrata.storage.archive import RuntimeHygieneManager
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
_DEFAULT_LOCAL_RUNTIME_POLICY: dict[str, Any] = {
    "auto_load_enabled": False,
    "keep_user_loaded_model": True,
    "allow_manual_override": True,
    "eligible_model_ids": [],
    "default_profile_id": None,
    "max_cache_gb": None,
    "max_ram_gb": None,
    "max_vram_gb": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MessageDraft:
    message: str
    recipient: str = "prime"
    conversation_id: str = ""
    intent: str = "principal_message"
    kind: str = "request"
    chat_kind: str = "agent"
    thread_id: str = ""
    start_new_thread: bool = False
    agent_mode: str = "persistent"
    provider_id: str = ""
    model_id: str = ""


class AstrataUIService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self._startup_cache: tuple[datetime, dict[str, Any]] | None = None
        self._conversation_cache: dict[str, tuple[datetime, list[Message]]] = {}

    def ensure_startup_reports(self) -> dict[str, Any]:
        if self._startup_cache is not None:
            cache_time, cache_result = self._startup_cache
            if (datetime.now(timezone.utc) - cache_time).total_seconds() < 60:
                return cache_result
        preflight = load_preflight_report(self.settings) or generate_python_preflight_report(
            self.settings
        )
        reflection = run_startup_reflection(self.settings, db=self._db())
        result = {"preflight": preflight, "runtime": reflection.report}
        self._startup_cache = (datetime.now(timezone.utc), result)
        return result

    def snapshot(self) -> dict[str, Any]:
        self._maintain_runtime_hygiene()
        db = self._db()
        registry = build_default_registry()
        chooser = RouteChooser(registry)
        bundle = load_governance_bundle(self.settings.paths.project_root)
        all_counts = db.count_multiple_records_by_json_field(
            [
                ("tasks", "$.status"),
                ("attempts", "$.outcome"),
                ("artifacts", "$.artifact_type"),
                ("verifications", "$.result"),
            ]
        )
        task_counts = all_counts.get("tasks", {})
        attempt_counts = all_counts.get("attempts", {})
        artifact_counts = all_counts.get("artifacts", {})
        verification_counts = all_counts.get("verifications", {})
        task_total = sum(task_counts.values())
        attempt_total = sum(attempt_counts.values())
        artifact_total = sum(artifact_counts.values())
        verification_total = sum(verification_counts.values())
        tasks = self._recent_task_summaries(db)
        attempts = self._recent_attempt_summaries(db)
        artifacts = self._recent_artifact_summaries(db)
        verifications = self._recent_verification_summaries(db)
        recent_communications = self._recent_communication_summaries(db)
        communication_total = db.count_records("communications")
        heartbeat = self._latest_loop0_heartbeat(db)
        principal_lane = PrincipalMessageLane(db=db)
        principal_messages = list(reversed(principal_lane.list_messages(recipient="principal")))[:8]
        astrata_messages = list(reversed(principal_lane.list_messages(recipient="astrata")))[:8]
        prime_messages = list(reversed(principal_lane.list_messages(recipient="prime")))[:8]
        local_messages = list(reversed(principal_lane.list_messages(recipient="local")))[:8]
        agents = self._agents_snapshot()
        chats = self._chat_snapshot(
            db=db, agents=agents, recent_communications=recent_communications
        )
        default_route = None
        try:
            default_route = chooser.choose(priority=0, urgency=0, risk="moderate").__dict__
        except Exception:
            default_route = None
        startup_data = self.ensure_startup_reports()
        startup_preflight = startup_data["preflight"]
        startup_runtime = startup_data["runtime"]
        inference_telemetry = summarize_inference_activity(
            attempts=attempts,
            tasks=tasks,
            quota_snapshots=self._quota_snapshots(db, registry),
        )
        history = self._history_snapshot(
            tasks=tasks,
            attempts=attempts,
            artifacts=artifacts,
            verifications=verifications,
            communications=recent_communications,
            heartbeat=heartbeat,
            totals={
                "tasks": task_total,
                "attempts": attempt_total,
                "artifacts": artifact_total,
                "verifications": verification_total,
                "communications": communication_total,
            },
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
                "project_spec_path": None
                if bundle.project_spec is None
                else bundle.project_spec.path,
                "planning_docs": {name: doc.path for name, doc in bundle.planning_docs.items()},
            },
            "providers": {
                "available": registry.list_available_providers(),
                "inference_sources": registry.list_available_inference_sources(),
                "model_catalog": registry.list_model_catalog(),
                "default_route": default_route,
            },
            "startup": {
                "preflight": startup_preflight,
                "runtime": startup_runtime,
            },
            "agents": {agent["agent_id"]: agent for agent in agents},
            "chats": chats,
            "desktop_backend": self._desktop_backend_snapshot(),
            "relay": self._relay_snapshot(),
            "voice": VoiceService(settings=self.settings).status(),
            "account_auth": self._account_auth_snapshot(),
            "local_runtime": self._local_runtime_snapshot(),
            "queue": {
                "counts": task_counts,
                "recent_tasks": [self._task_summary(task) for task in tasks[:8]],
            },
            "attempts": {
                "counts": attempt_counts,
                "recent_attempts": [self._attempt_summary(attempt) for attempt in attempts[:8]],
            },
            "inference": inference_telemetry,
            "history": history,
            "communications": {
                "principal_inbox": [
                    self._message_summary(message) for message in principal_messages
                ],
                "operator_inbox": [
                    self._message_summary(message) for message in principal_messages
                ],
                "astrata_inbox": [self._message_summary(message) for message in astrata_messages],
                "prime_inbox": [self._message_summary(message) for message in prime_messages],
                "local_inbox": [self._message_summary(message) for message in local_messages],
                "prime_conversation": [
                    self._message_summary(message)
                    for message in self.lane_conversation("prime", db=db)[-16:]
                ],
                "local_conversation": [
                    self._message_summary(message)
                    for message in self.lane_conversation("local", db=db)[-16:]
                ],
                "threads": chats["threads"],
                "chat_thread_conversations": self._chat_thread_conversations(
                    db=db, threads=chats["threads"]
                ),
                "lane_counts": {
                    "principal": self._lane_count(principal_lane, "principal"),
                    "operator": self._lane_count(principal_lane, "principal"),
                    "astrata": self._lane_count(principal_lane, "astrata"),
                    "prime": self._lane_count(principal_lane, "prime"),
                    "local": self._lane_count(principal_lane, "local"),
                },
            },
            "artifacts": {
                "counts": artifact_counts,
                "recent": [self._artifact_summary(artifact) for artifact in artifacts[:6]],
            },
            "verifications": {
                "counts": verification_counts,
                "recent": verifications[:6],
            },
            "update_channel": self._update_channel_snapshot(),
        }

    def send_message(self, draft: MessageDraft) -> dict[str, Any]:
        if draft.chat_kind == "model":
            return self.send_model_chat(draft)
        db = self._db()
        lane = PrincipalMessageLane(db=db)
        chat_registry = self._chat_registry()
        thread = self._resolve_chat_thread(draft, chat_registry=chat_registry)
        recipient = thread.agent_id or draft.recipient
        conversation_id = (
            thread.conversation_id
            if thread is not None
            else (draft.conversation_id or lane.default_conversation_id(draft.recipient))
        )
        record = lane.send(
            sender="principal",
            recipient=recipient,
            conversation_id=conversation_id,
            kind=draft.kind,
            intent=draft.intent,
            payload={
                "message": draft.message,
                "chat_kind": "agent",
                **(
                    {
                        "chat_thread_id": thread.thread_id,
                        "agent_mode": thread.agent_mode,
                        "memory_policy": thread.memory_policy,
                        "permissions_profile": thread.permissions_profile,
                    }
                    if thread is not None
                    else {}
                ),
            },
        )
        if thread is not None:
            chat_registry.touch(thread.thread_id)
        result: dict[str, Any] = {"message": record.model_dump(mode="json")}
        if recipient in {"prime", "local"}:
            runtime = LaneRuntime(settings=self.settings, db=db)
            result["turn"] = runtime.handle_message(record).as_dict()
        return result

    def send_model_chat(self, draft: MessageDraft) -> dict[str, Any]:
        db = self._db()
        lane = PrincipalMessageLane(db=db)
        chat_registry = self._chat_registry()
        thread = self._resolve_chat_thread(
            draft,
            chat_registry=chat_registry,
            default_chat_kind="model",
        )
        provider_id = str(draft.provider_id or getattr(thread, "provider_id", None) or "").strip()
        model_id = str(draft.model_id or getattr(thread, "model_id", None) or "").strip() or "local"
        conversation_id = thread.conversation_id if thread is not None else draft.conversation_id
        recipient = f"model:{provider_id}:{model_id}" if provider_id else f"model:{model_id}"
        user_record = lane.send(
            sender="principal",
            recipient=recipient,
            conversation_id=conversation_id,
            kind=draft.kind,
            intent="model_chat_message",
            payload={
                "message": draft.message,
                "chat_kind": "model",
                "chat_thread_id": None if thread is None else thread.thread_id,
                "provider_id": provider_id,
                "model_id": model_id,
            },
        )
        result: dict[str, Any] = {"message": user_record.model_dump(mode="json")}
        try:
            history = self._conversation_messages(db=db, conversation_id=conversation_id, limit=16)
            request = CompletionRequest(
                model=model_id,
                messages=[
                    Message(
                        role="system",
                        content=(
                            "You are a direct model chat surface inside Astrata. "
                            "Answer the user normally. Do not claim to be an Astrata durable agent."
                        ),
                    ),
                    *history,
                ],
                metadata={"chat_kind": "model", "conversation_id": conversation_id},
            )
            endpoint: dict[str, Any] | None = None
            if provider_id and provider_id != "local":
                provider = build_default_registry().get_provider(provider_id)
                if provider is None:
                    raise RuntimeError(f"Provider `{provider_id}` is not configured.")
                response = provider.complete(request)
                content = response.content
                endpoint = {"provider_id": provider_id, "model_id": response.model}
            else:
                endpoint = self._model_chat_endpoint(thread)
                content = LocalRuntimeClient().complete(
                    base_url=endpoint["base_url"],
                    thread_id=conversation_id,
                    request=request,
                )
            reply = lane.send(
                sender=recipient,
                recipient="principal",
                conversation_id=conversation_id,
                kind="response",
                intent="model_chat_reply",
                payload={
                    "message": content.strip() or "(empty model response)",
                    "chat_kind": "model",
                    "chat_thread_id": None if thread is None else thread.thread_id,
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "endpoint": endpoint,
                },
            )
            result["reply"] = reply.model_dump(mode="json")
            result["endpoint"] = endpoint
        except Exception as exc:
            result["error"] = str(exc)
        if thread is not None:
            chat_registry.touch(thread.thread_id)
        return result

    def create_chat_thread(self, payload: dict[str, Any]) -> dict[str, Any]:
        registry = self._chat_registry()
        thread = registry.create_thread(
            chat_kind=str(payload.get("chat_kind") or "agent"),
            title=str(payload.get("title") or ""),
            agent_id=str(payload.get("agent_id") or payload.get("recipient") or "prime"),
            agent_mode=str(payload.get("agent_mode") or "persistent"),
            model_id=str(payload.get("model_id") or ""),
            provider_id=str(payload.get("provider_id") or "") or None,
            endpoint_runtime_key=str(payload.get("endpoint_runtime_key") or "") or None,
            memory_policy=dict(payload.get("memory_policy") or {}),
            permissions_profile=dict(payload.get("permissions_profile") or {}),
            metadata=dict(payload.get("metadata") or {}),
        )
        return thread.model_dump(mode="json")

    def update_chat_thread_status(self, thread_id: str, action: str) -> dict[str, Any]:
        registry = self._chat_registry()
        if action == "archive":
            thread = registry.archive(thread_id)
        elif action == "restore":
            thread = registry.restore(thread_id)
        elif action == "delete":
            thread = registry.delete(thread_id)
        elif action == "convert":
            thread = registry.convert_ephemeral_to_persistent(thread_id)
        else:
            thread = registry.get(thread_id)
        if thread is None:
            return {"status": "not_found", "thread_id": thread_id}
        return {"status": "ok", "thread": thread.model_dump(mode="json")}

    def task_detail(self, task_id: str) -> dict[str, Any]:
        self._maintain_runtime_hygiene()
        db = self._db()
        task = next((task for task in self._tasks(db) if task.task_id == task_id), None)
        if task is None:
            return {"status": "not_found", "task_id": task_id}
        attempts = [attempt for attempt in self._attempts(db) if attempt.task_id == task_id]
        artifacts = [
            artifact
            for artifact in self._artifacts(db)
            if self._artifact_relates_to_task(artifact, task_id)
        ]
        verifications = [
            verification
            for verification in self._verifications(db)
            if verification.target_id in {task_id, *[attempt.attempt_id for attempt in attempts]}
        ]
        messages = self._messages_for_task(db, task_id)
        return {
            "status": "ok",
            "task": self._task_summary(task),
            "attempts": [self._attempt_summary(attempt) for attempt in attempts],
            "artifacts": [self._artifact_summary(artifact) for artifact in artifacts],
            "verifications": [
                verification.model_dump(mode="json") for verification in verifications
            ],
            "messages": [self._message_summary(message) for message in messages],
            "relationships": self._task_relationships(db, task),
            "blockers": self._task_blockers(db, task),
        }

    def lane_conversation(
        self, lane: str, *, db: AstrataDatabase | None = None
    ) -> list[CommunicationRecord]:
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

    def _chat_thread_conversations(
        self,
        *,
        db: AstrataDatabase,
        threads: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        active_conversations = {
            str(thread.get("conversation_id") or ""): str(thread.get("thread_id") or "")
            for thread in threads
            if thread.get("status") == "active" and thread.get("conversation_id")
        }
        if not active_conversations:
            return {}
        grouped: dict[str, list[CommunicationRecord]] = {
            thread_id: [] for thread_id in active_conversations.values()
        }
        for record in self._communications(db):
            thread_id = active_conversations.get(str(record.conversation_id or ""))
            if thread_id:
                grouped.setdefault(thread_id, []).append(record)
        return {
            thread_id: [
                self._message_summary(message)
                for message in sorted(messages, key=lambda item: item.created_at)[-32:]
            ]
            for thread_id, messages in grouped.items()
        }

    def _chat_registry(self) -> ChatThreadRegistry:
        return ChatThreadRegistry.from_settings(self.settings)

    def _agent_registry(self) -> DurableAgentRegistry:
        registry = DurableAgentRegistry.from_settings(self.settings)
        registry.ensure_bootstrap_agents()
        return registry

    def _agents_snapshot(self) -> list[dict[str, Any]]:
        agents = []
        for agent in self._agent_registry().list_agents():
            payload = agent.model_dump(mode="json")
            binding = dict(agent.inference_binding or {})
            payload["display_route"] = {
                "label": binding.get("cli_tool")
                or binding.get("provider")
                or binding.get("lane")
                or agent.role,
                "provider": binding.get("provider"),
                "model": binding.get("model"),
            }
            agents.append(payload)
        return agents

    def _chat_snapshot(
        self,
        *,
        db: AstrataDatabase,
        agents: list[dict[str, Any]],
        recent_communications: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        registry = self._chat_registry()
        for agent in agents:
            if agent.get("agent_id") in {"prime", "local", "reception"}:
                registry.ensure_agent_main_thread(
                    agent_id=str(agent["agent_id"]), title=f"{agent['title']} main chat"
                )
        messages = (
            recent_communications
            if recent_communications is not None
            else self._recent_communication_summaries(db, limit=128)
        )
        all_counts_by_conversation = self._communication_counts_by_conversation(db)
        self._prune_empty_non_main_threads(
            registry=registry,
            counts_by_conversation=all_counts_by_conversation,
        )
        counts_by_conversation = Counter(
            str(item.get("conversation_id") or "") for item in messages
        )
        latest_by_conversation: dict[str, dict[str, Any]] = {}
        for message in messages:
            conversation_id = str(message.get("conversation_id") or "")
            latest_by_conversation.setdefault(conversation_id, message)
        threads = []
        for thread in registry.list_threads():
            latest = latest_by_conversation.get(thread.conversation_id)
            payload = thread.model_dump(mode="json")
            payload["message_count_recent"] = counts_by_conversation.get(thread.conversation_id, 0)
            payload["message_count_total"] = all_counts_by_conversation.get(thread.conversation_id, 0)
            payload["latest_message"] = latest
            payload["preview"] = None if latest is None else latest.get("message")
            threads.append(payload)
        return {
            "threads": threads,
            "active_threads": [thread for thread in threads if thread.get("status") == "active"],
            "archived_threads": [
                thread for thread in threads if thread.get("status") == "archived"
            ],
            "kinds": {
                "agent": len([thread for thread in threads if thread.get("chat_kind") == "agent"]),
                "model": len([thread for thread in threads if thread.get("chat_kind") == "model"]),
            },
        }

    def _resolve_chat_thread(
        self,
        draft: MessageDraft,
        *,
        chat_registry: ChatThreadRegistry,
        default_chat_kind: str = "agent",
    ):
        if draft.thread_id:
            thread = chat_registry.get(draft.thread_id)
            if thread is not None:
                return thread
        if draft.conversation_id:
            thread = chat_registry.get_by_conversation_id(draft.conversation_id)
            if thread is not None:
                return thread
            created = chat_registry.create_thread(
                chat_kind=draft.chat_kind or default_chat_kind,
                agent_id=str(draft.recipient or "prime").strip().lower() or "prime",
                agent_mode=draft.agent_mode,
                provider_id=draft.provider_id or None,
                model_id=draft.model_id or "local",
                title=f"Thread with {str(draft.recipient or 'agent').title()}",
            )
            return chat_registry.upsert(
                created.model_copy(
                    update={
                        "conversation_id": draft.conversation_id,
                        "updated_at": _now_iso(),
                    }
                )
            )
        chat_kind = draft.chat_kind or default_chat_kind
        if chat_kind == "model" or default_chat_kind == "model":
            return chat_registry.create_thread(
                chat_kind="model",
                provider_id=draft.provider_id or None,
                model_id=draft.model_id or "local",
                title=f"Model chat: {draft.model_id or 'local'}",
            )
        agent_id = str(draft.recipient or "prime").strip().lower() or "prime"
        if draft.start_new_thread:
            return chat_registry.create_thread(
                chat_kind="agent",
                agent_id=agent_id,
                agent_mode=draft.agent_mode,
                title=f"{draft.agent_mode.title()} chat with {agent_id.title()}",
            )
        if draft.agent_mode == "persistent" and not draft.conversation_id:
            return chat_registry.ensure_agent_main_thread(
                agent_id=agent_id, title=f"{agent_id.title()} main chat"
            )
        return chat_registry.create_thread(
            chat_kind="agent",
            agent_id=agent_id,
            agent_mode=draft.agent_mode,
            title=f"{draft.agent_mode.title()} chat with {agent_id.title()}",
        )

    def _communication_counts_by_conversation(self, db: AstrataDatabase) -> Counter[str]:
        rows = db.select_json_fields(
            "communications",
            fields={"conversation_id": "$.conversation_id"},
        )
        return Counter(str(row.get("conversation_id") or "") for row in rows)

    def _prune_empty_non_main_threads(
        self,
        *,
        registry: ChatThreadRegistry,
        counts_by_conversation: Counter[str],
    ) -> None:
        for thread in registry.list_threads():
            if thread.status != "active":
                continue
            if dict(thread.metadata or {}).get("main_lane"):
                continue
            if counts_by_conversation.get(thread.conversation_id, 0) > 0:
                continue
            registry.delete(thread.thread_id)

    def _conversation_messages(
        self,
        *,
        db: AstrataDatabase,
        conversation_id: str,
        limit: int = 16,
    ) -> list[Message]:
        cache_key = f"{conversation_id}:{limit}"
        if cache_key in self._conversation_cache:
            cache_time, cached = self._conversation_cache[cache_key]
            if (datetime.now(timezone.utc) - cache_time).total_seconds() < 30:
                return cached
        records = [
            CommunicationRecord(**payload)
            for payload in db.iter_records("communications")
            if str(payload.get("conversation_id") or "") == conversation_id
        ]
        sorted_records = sorted(records, key=lambda item: item.created_at)[-limit:]
        messages = []
        for record in sorted_records:
            content = str(record.payload.get("message") or "").strip()
            if not content:
                continue
            role = "user" if str(record.sender).startswith("principal") else "assistant"
            messages.append(Message(role=role, content=content))
        self._conversation_cache[cache_key] = (datetime.now(timezone.utc), messages)
        return messages

    def _model_chat_endpoint(self, thread: Any | None) -> dict[str, Any]:
        runtime = self._local_runtime_snapshot()
        endpoints = list(runtime.get("served_endpoints") or [])
        runtime_key = str(getattr(thread, "endpoint_runtime_key", None) or "").strip()
        if runtime_key:
            endpoints = [
                endpoint for endpoint in endpoints if endpoint.get("runtime_key") == runtime_key
            ] or endpoints
        for endpoint in endpoints:
            if endpoint.get("running") and endpoint.get("base_url"):
                return endpoint
        raise RuntimeError("No running local inference endpoint is available for model chat.")

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

    def start_local_runtime(
        self,
        *,
        model_id: str | None = None,
        profile_id: str | None = None,
        override_thermal: bool = False,
        override_resource_policy: bool = False,
        operator_initiated: bool = False,
    ) -> dict[str, Any]:
        manager = self._local_runtime_manager()
        policy = self._local_runtime_policy()
        recommendation = self._recommendation_for_policy(manager, policy)
        thermal_state = probe_thermal_state(
            preference=self.settings.local_runtime.thermal_preference
        )
        thermal_controller = ThermalController(
            state_path=self.settings.paths.data_dir / "thermal_state.json"
        )
        thermal_decision = thermal_controller.evaluate(thermal_state)
        if not override_thermal and not thermal_decision.should_start_new_local_work:
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
                    "model": None
                    if recommendation.model is None
                    else recommendation.model.model_dump(mode="json"),
                    "profile_id": recommendation.profile_id,
                    "reason": recommendation.reason,
                },
            }
        if not operator_initiated and not self._is_model_eligible(model.model_id, policy):
            return {
                "status": "blocked_by_eligibility_policy",
                "model": model.model_dump(mode="json"),
                "policy": policy,
            }
        resource_policy = self._resource_policy_for_model(model, policy)
        if not override_resource_policy and resource_policy["status"] == "blocked":
            return {
                "status": "blocked_by_resource_policy",
                "model": model.model_dump(mode="json"),
                "policy": policy,
                "resource_policy": resource_policy,
            }
        selected_profile_id = (
            profile_id or str(policy.get("default_profile_id") or "") or recommendation.profile_id
        )
        status = manager.start_managed(
            backend_id="llama_cpp",
            model_id=model.model_id,
            binary_path=self.settings.local_runtime.llama_cpp_binary,
            host=self.settings.local_runtime.llama_cpp_host,
            port=self.settings.local_runtime.llama_cpp_port,
            profile_id=selected_profile_id,
            metadata={
                "load_origin": "user" if operator_initiated else "astrata",
                "operator_initiated": operator_initiated,
                "override_thermal": override_thermal,
                "override_resource_policy": override_resource_policy,
                "keep_loaded": bool(policy.get("keep_user_loaded_model", True)),
            },
        )
        return {
            "status": "started",
            "model": model.model_dump(mode="json"),
            "policy": policy,
            "resource_policy": resource_policy,
            "managed_process": None if status is None else self._managed_process_summary(status),
        }

    def ensure_local_runtime(
        self, *, model_id: str | None = None, profile_id: str | None = None
    ) -> dict[str, Any]:
        manager = self._local_runtime_manager()
        recommendation = manager.recommend(
            thermal_preference=self.settings.local_runtime.thermal_preference
        )
        thermal_state = probe_thermal_state(
            preference=self.settings.local_runtime.thermal_preference
        )
        thermal_controller = ThermalController(
            state_path=self.settings.paths.data_dir / "thermal_state.json"
        )
        thermal_decision = thermal_controller.evaluate(thermal_state)
        if not thermal_decision.should_start_new_local_work:
            return {"status": "deferred_for_thermal", "thermal_decision": thermal_decision.__dict__}

        managed = manager.managed_status()
        health = manager.health()
        if (
            managed is not None
            and getattr(managed, "running", False)
            and health is not None
            and getattr(health, "ok", False)
        ):
            return {
                "status": "already_running",
                "health": health.model_dump(mode="json")
                if hasattr(health, "model_dump")
                else {"ok": True},
                "managed_process": self._managed_process_summary(managed),
            }

        existing_health = None
        backend = manager.backend("llama_cpp") if hasattr(manager, "backend") else None
        if backend is not None and hasattr(backend, "healthcheck"):
            try:
                existing_health = backend.healthcheck(
                    config={
                        "host": self.settings.local_runtime.llama_cpp_host,
                        "port": self.settings.local_runtime.llama_cpp_port,
                    }
                )
            except Exception:
                existing_health = None
        if existing_health is not None and getattr(existing_health, "ok", False):
            endpoint = (
                getattr(existing_health, "endpoint", None)
                or f"http://{self.settings.local_runtime.llama_cpp_host}:{self.settings.local_runtime.llama_cpp_port}/health"
            )
            selected_model_id = model_id or getattr(
                getattr(recommendation, "model", None), "model_id", None
            )
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
                "health": existing_health.model_dump(mode="json")
                if hasattr(existing_health, "model_dump")
                else {"ok": True},
            }
        return self.start_local_runtime(model_id=model_id, profile_id=profile_id)

    def stop_local_runtime(self, *, runtime_key: str | None = None) -> dict[str, Any]:
        manager = self._local_runtime_manager()
        status = manager.stop_managed(runtime_key=runtime_key)
        return {
            "status": "stopped",
            "runtime_key": runtime_key or "active",
            "managed_process": self._managed_process_summary(status),
        }

    def redeem_invite_code(
        self, *, email: str, display_name: str = "", invite_code: str
    ) -> dict[str, Any]:
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
            str(
                dict(self._account_auth_snapshot().get("device_link") or {}).get("relay_endpoint")
                or ""
            )
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
            "local_runtime_policy": self._normalize_local_runtime_policy(
                prefs.get("local_runtime_policy")
            ),
        }

    def set_preferences(self, data: dict[str, Any]) -> dict[str, Any]:
        prefs = self._load_preferences()
        if "update_channel" in data:
            requested = str(data.get("update_channel") or "").strip().lower()
            if requested in _UPDATE_CHANNELS:
                prefs["update_channel"] = requested
        if "local_runtime_policy" in data:
            prefs["local_runtime_policy"] = self._normalize_local_runtime_policy(
                data.get("local_runtime_policy")
            )
        self._save_preferences(prefs)
        return self.get_preferences()

    def _local_runtime_policy(self) -> dict[str, Any]:
        prefs = self._load_preferences()
        return self._normalize_local_runtime_policy(prefs.get("local_runtime_policy"))

    def _normalize_local_runtime_policy(self, raw: Any) -> dict[str, Any]:
        payload = dict(_DEFAULT_LOCAL_RUNTIME_POLICY)
        if isinstance(raw, dict):
            payload.update(raw)
        eligible = payload.get("eligible_model_ids")
        if isinstance(eligible, list):
            payload["eligible_model_ids"] = [
                str(item).strip() for item in eligible if str(item).strip()
            ]
        else:
            payload["eligible_model_ids"] = []
        payload["auto_load_enabled"] = bool(payload.get("auto_load_enabled"))
        payload["keep_user_loaded_model"] = bool(payload.get("keep_user_loaded_model", True))
        payload["allow_manual_override"] = bool(payload.get("allow_manual_override", True))
        default_profile_id = str(payload.get("default_profile_id") or "").strip()
        payload["default_profile_id"] = default_profile_id or None
        for key in ("max_cache_gb", "max_ram_gb", "max_vram_gb"):
            payload[key] = self._coerce_optional_float(payload.get(key))
        return payload

    def _coerce_optional_float(self, value: Any) -> float | None:
        if value in {None, "", False}:
            return None
        try:
            parsed = float(value)
        except Exception:
            return None
        return parsed if parsed > 0 else None

    def _recommendation_for_policy(self, manager: LocalRuntimeManager, policy: dict[str, Any]):
        recommendation = manager.recommend(
            thermal_preference=self.settings.local_runtime.thermal_preference
        )
        if recommendation.model is None:
            return recommendation
        if self._is_model_eligible(recommendation.model.model_id, policy):
            return recommendation
        eligible_models = [
            model
            for model in manager.model_registry().list_models()
            if self._is_model_eligible(model.model_id, policy)
        ]
        if not eligible_models:
            return recommendation
        ranked = sorted(
            eligible_models,
            key=lambda model: (
                -float(model.observed_average_score or 0.0),
                -float(model.benchmark_score or 0.0),
                float(model.size_bytes or 0),
                model.display_name.lower(),
            ),
        )
        return type(recommendation)(
            model=ranked[0],
            profile_id=str(
                policy.get("default_profile_id") or recommendation.profile_id or "balanced"
            ),
            reason=f"{recommendation.reason} Auto-load eligibility narrowed the candidate set.",
        )

    def _is_model_eligible(self, model_id: str, policy: dict[str, Any]) -> bool:
        eligible = list(policy.get("eligible_model_ids") or [])
        return not eligible or model_id in eligible

    def _resource_policy_for_model(self, model: Any, policy: dict[str, Any]) -> dict[str, Any]:
        estimated_gb = round(float(getattr(model, "size_bytes", 0) or 0) / (1024**3), 2)
        limits = {
            "max_ram_gb": policy.get("max_ram_gb"),
            "max_vram_gb": policy.get("max_vram_gb"),
        }
        blockers = []
        for key, label in (("max_ram_gb", "RAM"), ("max_vram_gb", "VRAM/offload")):
            limit = limits[key]
            if limit is not None and estimated_gb > float(limit):
                blockers.append(
                    f"Estimated footprint {estimated_gb} GB exceeds configured {label} budget of {float(limit):g} GB."
                )
        return {
            "status": "blocked" if blockers else "ok",
            "estimated_model_gb": estimated_gb,
            "limits": limits,
            "reasons": blockers,
        }

    def _resolve_loaded_model(self, manager: LocalRuntimeManager):
        selection = manager.current_selection()
        if selection is None:
            return None
        model_id = str(selection.model_id or "").strip()
        if model_id:
            record = manager.model_registry().get(model_id)
            if record is not None:
                return record
        model_path = str(dict(selection.metadata or {}).get("model_path") or "").strip()
        if model_path:
            return manager.model_registry().find_by_path(model_path)
        return None

    def _directory_size_bytes(self, path: Path | None) -> int:
        if path is None or not path.exists():
            return 0
        total = 0
        try:
            for item in path.rglob("*"):
                if item.is_file():
                    total += item.stat().st_size
        except Exception:
            return 0
        return total

    def _db(self) -> AstrataDatabase:
        db = AstrataDatabase(self.settings.paths.data_dir / "astrata.db")
        db.initialize()
        return db

    def _maintain_runtime_hygiene(self) -> dict[str, Any]:
        manager = RuntimeHygieneManager(
            live_db=self.settings.paths.data_dir / "astrata.db",
            archive_dir=self.settings.paths.data_dir / "archive",
            state_path=self.settings.paths.data_dir / "runtime_hygiene_state.json",
        )
        try:
            return manager.maintain(force=False)
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    def _account_registry(self) -> AccountControlPlaneRegistry:
        return AccountControlPlaneRegistry.from_settings(self.settings)

    def _relay_service(self) -> HostedMCPRelayService:
        return HostedMCPRelayService.from_settings(self.settings)

    def _preferences_path(self) -> Path:
        return self.settings.paths.data_dir / "ui-preferences.json"

    def _load_preferences(self) -> dict[str, Any]:
        path = self._preferences_path()
        if not path.exists():
            return {
                "update_channel": _DEFAULT_UPDATE_CHANNEL,
                "local_runtime_policy": self._normalize_local_runtime_policy(None),
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "update_channel": _DEFAULT_UPDATE_CHANNEL,
                "local_runtime_policy": self._normalize_local_runtime_policy(None),
            }
        if not isinstance(payload, dict):
            return {
                "update_channel": _DEFAULT_UPDATE_CHANNEL,
                "local_runtime_policy": self._normalize_local_runtime_policy(None),
            }
        payload.setdefault("update_channel", _DEFAULT_UPDATE_CHANNEL)
        payload["local_runtime_policy"] = self._normalize_local_runtime_policy(
            payload.get("local_runtime_policy")
        )
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
            "backend_running": bool(backend_url)
            and not bool(payload.get("backend_deliberately_stopped")),
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
            connector_urls = self._relay_connector_urls(
                "" if link is None else str(link.relay_endpoint or "").strip()
            )
            queue_state = self._relay_queue_snapshot(relay, selected.profile_id)
        return {
            **summary,
            "selected_profile": None if selected is None else selected.model_dump(mode="json"),
            "connector_urls": connector_urls,
            "queue_state": queue_state,
        }

    def _account_auth_snapshot(self, *, profile_id: str | None = None) -> dict[str, Any]:
        registry = self._account_registry()
        selected_profile = (
            registry.get_profile(profile_id)
            if profile_id
            else (registry.list_profiles()[0] if registry.list_profiles() else None)
        )
        selected_profile_payload = (
            None if selected_profile is None else selected_profile.model_dump(mode="json")
        )
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
        chatgpt_profiles = [
            profile
            for profile in profiles
            if str(profile.exposure or "").strip().lower() == "chatgpt"
        ]
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

    def _relay_queue_snapshot(
        self, relay: HostedMCPRelayService, profile_id: str
    ) -> dict[str, Any]:
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
        statuses = manager.list_managed_statuses()
        active_key: str | None = None
        for runtime_key, managed in statuses.items():
            if managed is None or not getattr(managed, "running", False):
                continue
            managed_metadata = dict(getattr(managed, "metadata", {}) or {})
            model_id = str(managed_metadata.get("model_id") or "").strip() or None
            model_path = str(managed_metadata.get("model_path") or "").strip()
            if model_id is None and model_path:
                record = manager.model_registry().find_by_path(model_path)
                model_id = None if record is None else record.model_id
            manager.select_runtime(
                runtime_key=runtime_key,
                backend_id=str(managed_metadata.get("backend_id") or "llama_cpp"),
                model_id=model_id,
                mode="managed",
                profile_id=str(managed_metadata.get("profile_id") or "").strip() or None,
                endpoint=getattr(managed, "endpoint", None),
                metadata=managed_metadata,
                activate=False,
            )
            if runtime_key == "default":
                active_key = runtime_key
            elif active_key is None:
                active_key = runtime_key
        if active_key is not None:
            active_selection = manager.current_selection(active_key)
            if active_selection is not None:
                manager.select_runtime(
                    runtime_key=active_selection.runtime_key,
                    backend_id=active_selection.backend_id,
                    model_id=active_selection.model_id,
                    mode=active_selection.mode,
                    profile_id=active_selection.profile_id,
                    endpoint=active_selection.endpoint,
                    metadata=active_selection.metadata,
                    activate=True,
                )
        return manager

    def _local_runtime_snapshot(self) -> dict[str, Any]:
        manager = self._local_runtime_manager()
        policy = self._local_runtime_policy()
        thermal_state = probe_thermal_state(
            preference=self.settings.local_runtime.thermal_preference
        )
        thermal_controller = ThermalController(
            state_path=self.settings.paths.data_dir / "thermal_state.json"
        )
        thermal_decision = thermal_controller.evaluate(thermal_state)
        thermal_history = thermal_controller.history_summary()
        recommendation = self._recommendation_for_policy(manager, policy)
        managed = manager.managed_status()
        managed_statuses = manager.list_managed_statuses()
        selection = manager.current_selection()
        selections = {item.runtime_key: item for item in manager.list_selections()}
        loaded_model = self._resolve_loaded_model(manager)
        loaded_models = []
        served_endpoints = []
        for runtime_key, status in managed_statuses.items():
            selected = selections.get(runtime_key)
            status_metadata = dict(getattr(status, "metadata", {}) or {})
            model = self._resolve_model_for_runtime(manager, selected, status_metadata)
            if getattr(status, "running", False):
                loaded_models.append(
                    {
                        "runtime_key": runtime_key,
                        "backend_id": (
                            selected.backend_id
                            if selected is not None
                            else status_metadata.get("backend_id")
                        )
                        or "llama_cpp",
                        "profile_id": None if selected is None else selected.profile_id,
                        "model": None if model is None else model.model_dump(mode="json"),
                        "managed_process": self._managed_process_summary(status),
                        "load_origin": str(status_metadata.get("load_origin") or "astrata"),
                    }
                )
            endpoint = self._served_endpoint_payload(
                runtime_key=runtime_key,
                status=status,
                selection=selected,
                model=model,
            )
            if endpoint is not None:
                served_endpoints.append(endpoint)
        if self.settings.local_runtime.llama_cpp_base_url:
            base_url = self.settings.local_runtime.llama_cpp_base_url.rstrip("/")
            served_endpoints.append(
                {
                    "runtime_key": "external",
                    "backend_id": "llama_cpp",
                    "mode": "external",
                    "status": "configured",
                    "running": True,
                    "model": None,
                    "base_url": base_url,
                    "health_url": f"{base_url}/health",
                    "chat_completions_url": f"{base_url}/v1/chat/completions",
                    "legacy_completion_url": f"{base_url}/completion",
                    "config": {"source": "ASTRATA_LLAMA_CPP_BASE_URL"},
                }
            )
        install_dir = self.settings.local_runtime.model_install_dir
        install_dir_bytes = self._directory_size_bytes(install_dir)
        models = []
        for model in manager.model_registry().list_models()[:24]:
            payload = model.model_dump(mode="json")
            payload["eligible_for_auto_load"] = self._is_model_eligible(model.model_id, policy)
            payload["resource_policy"] = self._resource_policy_for_model(model, policy)
            payload["is_loaded"] = (
                loaded_model is not None and loaded_model.model_id == model.model_id
            )
            models.append(payload)
        return {
            "thermal_preference": self.settings.local_runtime.thermal_preference,
            "policy": policy,
            "thermal_state": {
                "preference": thermal_state.preference,
                "telemetry_available": thermal_state.telemetry_available,
                "thermal_pressure": thermal_state.thermal_pressure,
                "fans_allowed": thermal_state.fans_allowed,
                "detail": thermal_state.detail,
            },
            "thermal_decision": thermal_decision.__dict__,
            "thermal_history": thermal_history,
            "recommendation": {
                "model": None
                if recommendation.model is None
                else recommendation.model.model_dump(mode="json"),
                "profile_id": recommendation.profile_id,
                "reason": recommendation.reason,
            },
            "endpoint_config": {
                "backend_id": "llama_cpp",
                "binary_path": self.settings.local_runtime.llama_cpp_binary,
                "host": self.settings.local_runtime.llama_cpp_host,
                "port": self.settings.local_runtime.llama_cpp_port,
                "managed": self.settings.local_runtime.llama_cpp_managed,
                "base_url": self.settings.local_runtime.llama_cpp_base_url,
                "default_health_url": f"http://{self.settings.local_runtime.llama_cpp_host}:{self.settings.local_runtime.llama_cpp_port}/health",
                "default_chat_completions_url": f"http://{self.settings.local_runtime.llama_cpp_host}:{self.settings.local_runtime.llama_cpp_port}/v1/chat/completions",
                "default_legacy_completion_url": f"http://{self.settings.local_runtime.llama_cpp_host}:{self.settings.local_runtime.llama_cpp_port}/completion",
                "profiles": [profile.__dict__ for profile in manager.list_profiles()],
                "model_search_paths": list(self.settings.local_runtime.model_search_paths),
            },
            "selection": None if selection is None else selection.model_dump(mode="json"),
            "selections": [item.model_dump(mode="json") for item in manager.list_selections()],
            "loaded_model": None if loaded_model is None else loaded_model.model_dump(mode="json"),
            "loaded_models": loaded_models,
            "managed_process": None if managed is None else self._managed_process_summary(managed),
            "managed_processes": {
                runtime_key: self._managed_process_summary(status)
                for runtime_key, status in managed_statuses.items()
            },
            "served_endpoints": served_endpoints,
            "inventory": {
                "install_dir": None if install_dir is None else str(install_dir),
                "install_dir_bytes": install_dir_bytes,
                "install_dir_gb": round(install_dir_bytes / (1024**3), 2),
            },
            "models": models,
        }

    def _resolve_model_for_runtime(
        self,
        manager: LocalRuntimeManager,
        selection: Any | None,
        metadata: dict[str, Any] | None = None,
    ):
        payload = dict(metadata or {})
        model_id = str(
            getattr(selection, "model_id", None) or payload.get("model_id") or ""
        ).strip()
        if model_id:
            record = manager.model_registry().get(model_id)
            if record is not None:
                return record
        model_path = str(payload.get("model_path") or "").strip()
        if not model_path and selection is not None:
            model_path = str(
                dict(getattr(selection, "metadata", {}) or {}).get("model_path") or ""
            ).strip()
        if model_path:
            return manager.model_registry().find_by_path(model_path)
        return None

    def _served_endpoint_payload(
        self,
        *,
        runtime_key: str,
        status: Any,
        selection: Any | None,
        model: Any | None,
    ) -> dict[str, Any] | None:
        endpoint = str(
            getattr(status, "endpoint", None) or getattr(selection, "endpoint", None) or ""
        ).strip()
        if not endpoint:
            return None
        base_url = endpoint.removesuffix("/health").rstrip("/")
        metadata = dict(getattr(status, "metadata", {}) or {})
        return {
            "runtime_key": runtime_key,
            "backend_id": (
                getattr(selection, "backend_id", None) or metadata.get("backend_id") or "llama_cpp"
            ),
            "mode": getattr(selection, "mode", None) or "managed",
            "status": getattr(status, "detail", None)
            or ("running" if getattr(status, "running", False) else "stopped"),
            "running": bool(getattr(status, "running", False)),
            "pid": getattr(status, "pid", None),
            "model": None if model is None else model.model_dump(mode="json"),
            "base_url": base_url,
            "health_url": f"{base_url}/health",
            "chat_completions_url": f"{base_url}/v1/chat/completions",
            "legacy_completion_url": f"{base_url}/completion",
            "config": {
                "host": self.settings.local_runtime.llama_cpp_host,
                "port": self.settings.local_runtime.llama_cpp_port,
                "profile_id": getattr(selection, "profile_id", None) or metadata.get("profile_id"),
                "command": list(getattr(status, "command", []) or []),
                "log_path": getattr(status, "log_path", None),
            },
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
        verifications = [
            VerificationRecord(**payload) for payload in db.list_records("verifications")
        ]
        return sorted(verifications, key=lambda item: item.created_at, reverse=True)

    def _communications(self, db: AstrataDatabase) -> list[CommunicationRecord]:
        communications = [
            CommunicationRecord(**payload) for payload in db.list_records("communications")
        ]
        return sorted(communications, key=lambda item: item.created_at, reverse=True)

    def _counts_for_field(self, db: AstrataDatabase, table: str, json_field: str) -> dict[str, int]:
        counts = db.count_records_by_json_field(table, json_field)
        return {key or "unknown": value for key, value in counts.items()}

    def _recent_task_summaries(
        self, db: AstrataDatabase, *, limit: int = 8
    ) -> list[dict[str, Any]]:
        rows = db.select_json_fields(
            "tasks",
            fields={
                "task_id": "$.task_id",
                "title": "$.title",
                "description": "$.description",
                "status": "$.status",
                "priority": "$.priority",
                "urgency": "$.urgency",
                "risk": "$.risk",
                "provenance": "$.provenance",
                "completion_policy": "$.completion_policy",
                "updated_at": "$.updated_at",
                "target_lane": "$.provenance.target_lane",
                "source_conversation_id": "$.provenance.source_conversation_id",
                "provenance_archived": "$.provenance.archived",
            },
            order_by_json_field="$.updated_at",
            descending=True,
            limit=limit,
            include_payload_size=True,
        )
        return [self._task_summary_payload(row) for row in rows]

    def _recent_attempt_summaries(
        self, db: AstrataDatabase, *, limit: int = 8
    ) -> list[dict[str, Any]]:
        rows = db.select_json_fields(
            "attempts",
            fields={
                "attempt_id": "$.attempt_id",
                "task_id": "$.task_id",
                "actor": "$.actor",
                "outcome": "$.outcome",
                "result_summary": "$.result_summary",
                "failure_kind": "$.failure_kind",
                "degraded_reason": "$.degraded_reason",
                "verification_status": "$.verification_status",
                "started_at": "$.started_at",
                "ended_at": "$.ended_at",
                "resource_usage": "$.resource_usage",
            },
            order_by_json_field="$.started_at",
            descending=True,
            limit=limit,
        )
        return [self._attempt_summary_payload(row) for row in rows]

    def _recent_artifact_summaries(
        self, db: AstrataDatabase, *, limit: int = 6
    ) -> list[dict[str, Any]]:
        rows = db.select_json_fields(
            "artifacts",
            fields={
                "artifact_id": "$.artifact_id",
                "artifact_type": "$.artifact_type",
                "title": "$.title",
                "status": "$.status",
                "lifecycle_state": "$.lifecycle_state",
                "content_summary": "$.content_summary",
                "updated_at": "$.updated_at",
                "provenance_status": "$.provenance.status",
            },
            order_by_json_field="$.updated_at",
            descending=True,
            limit=limit,
        )
        return [self._artifact_summary_payload(row) for row in rows]

    def _recent_verification_summaries(
        self, db: AstrataDatabase, *, limit: int = 6
    ) -> list[dict[str, Any]]:
        return db.select_json_fields(
            "verifications",
            fields={
                "verification_id": "$.verification_id",
                "target_kind": "$.target_kind",
                "target_id": "$.target_id",
                "verifier": "$.verifier",
                "result": "$.result",
                "confidence": "$.confidence",
                "created_at": "$.created_at",
            },
            order_by_json_field="$.created_at",
            descending=True,
            limit=limit,
        )

    def _recent_communication_summaries(
        self, db: AstrataDatabase, *, limit: int = 8
    ) -> list[dict[str, Any]]:
        rows = db.select_json_fields(
            "communications",
            fields={
                "communication_id": "$.communication_id",
                "kind": "$.kind",
                "sender": "$.sender",
                "recipient": "$.recipient",
                "intent": "$.intent",
                "status": "$.status",
                "message": "$.payload.message",
                "created_at": "$.created_at",
                "channel": "$.channel",
                "conversation_id": "$.conversation_id",
            },
            order_by_json_field="$.created_at",
            descending=True,
            limit=limit,
        )
        return [self._message_summary_payload(row) for row in rows]

    def _latest_loop0_heartbeat(self, db: AstrataDatabase) -> dict[str, Any] | None:
        payload = db.get_record_by_json_fields(
            "artifacts",
            where_json_fields={"$.artifact_type": "loop0_daemon_heartbeat"},
            order_by_json_field="$.updated_at",
            descending=True,
        )
        if payload is None:
            return None
        return self._artifact_summary_payload(payload)

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

    def _quota_snapshots(
        self, db: AstrataDatabase, registry: ProviderRegistry
    ) -> list[dict[str, Any]]:
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
        if isinstance(task, dict):
            return self._task_summary_payload(task)
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
        if isinstance(attempt, dict):
            return self._attempt_summary_payload(attempt)
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
        if isinstance(record, dict):
            return self._message_summary_payload(record)
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
        if isinstance(artifact, dict):
            return self._artifact_summary_payload(artifact)
        return {
            "artifact_id": artifact.artifact_id,
            "artifact_type": artifact.artifact_type,
            "title": artifact.title,
            "status": artifact.status,
            "lifecycle_state": artifact.lifecycle_state,
            "content_summary": artifact.content_summary,
            "updated_at": artifact.updated_at,
        }

    def _task_summary_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        target_lane = payload.get("target_lane")
        source_conversation_id = payload.get("source_conversation_id")
        provenance = self._coerce_json_object(payload.get("provenance"))
        completion_policy = self._coerce_json_object(payload.get("completion_policy"))
        if payload.get("provenance_archived"):
            provenance["archived"] = True
        if target_lane:
            provenance["target_lane"] = target_lane
        if source_conversation_id:
            provenance["source_conversation_id"] = source_conversation_id
        return {
            "task_id": payload.get("task_id"),
            "title": payload.get("title"),
            "description": payload.get("description"),
            "status": payload.get("status"),
            "priority": int(payload.get("priority") or 0),
            "urgency": int(payload.get("urgency") or 0),
            "risk": payload.get("risk"),
            "updated_at": payload.get("updated_at"),
            "provenance": provenance,
            "completion_policy": completion_policy,
            "target_lane": target_lane,
            "source_conversation_id": source_conversation_id,
            "payload_size": payload.get("payload_size"),
        }

    def _attempt_summary_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "attempt_id": payload.get("attempt_id"),
            "task_id": payload.get("task_id"),
            "actor": payload.get("actor"),
            "outcome": payload.get("outcome"),
            "result_summary": payload.get("result_summary"),
            "failure_kind": payload.get("failure_kind"),
            "degraded_reason": payload.get("degraded_reason"),
            "verification_status": payload.get("verification_status"),
            "started_at": payload.get("started_at"),
            "ended_at": payload.get("ended_at"),
            "resource_usage": self._coerce_json_object(payload.get("resource_usage")),
        }

    def _coerce_json_object(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except Exception:
                return {}
            return dict(parsed) if isinstance(parsed, dict) else {}
        return {}

    def _message_summary_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "communication_id": payload.get("communication_id"),
            "kind": payload.get("kind"),
            "sender": payload.get("sender"),
            "recipient": payload.get("recipient"),
            "intent": payload.get("intent"),
            "status": payload.get("status"),
            "message": payload.get("message"),
            "created_at": payload.get("created_at"),
            "related_task_ids": [],
            "channel": payload.get("channel"),
            "conversation_id": payload.get("conversation_id"),
        }

    def _artifact_summary_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": payload.get("artifact_id"),
            "artifact_type": payload.get("artifact_type"),
            "title": payload.get("title"),
            "status": payload.get("status"),
            "lifecycle_state": payload.get("lifecycle_state"),
            "content_summary": payload.get("content_summary"),
            "updated_at": payload.get("updated_at"),
            "provenance_status": payload.get("provenance_status"),
        }

    def _managed_process_summary(self, status: Any) -> dict[str, Any]:
        return {
            "running": getattr(status, "running", False),
            "pid": getattr(status, "pid", None),
            "endpoint": getattr(status, "endpoint", None),
            "command": list(getattr(status, "command", []) or []),
            "log_path": getattr(status, "log_path", None),
            "started_at": getattr(status, "started_at", None),
            "metadata": dict(getattr(status, "metadata", {}) or {}),
            "detail": getattr(status, "detail", None),
        }

    def _lane_count(self, lane: PrincipalMessageLane, recipient: str) -> int:
        return len(lane.list_messages(recipient=recipient))

    def _messages_for_task(self, db: AstrataDatabase, task_id: str) -> list[CommunicationRecord]:
        task = next((task for task in self._tasks(db) if task.task_id == task_id), None)
        source_communication_id = (
            None if task is None else str(task.provenance.get("source_communication_id") or "")
        )
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
        parent = (
            next((item for item in tasks if item.task_id == parent_task_id), None)
            if parent_task_id
            else None
        )
        children = [
            item
            for item in tasks
            if str(dict(item.provenance or {}).get("parent_task_id") or "").strip() == task.task_id
        ]
        siblings = []
        if parent_task_id:
            siblings = [
                item
                for item in tasks
                if item.task_id != task.task_id
                and str(dict(item.provenance or {}).get("parent_task_id") or "").strip()
                == parent_task_id
            ]
        same_source = []
        if source_communication_id:
            same_source = [
                item
                for item in tasks
                if item.task_id != task.task_id
                and str(dict(item.provenance or {}).get("source_communication_id") or "").strip()
                == source_communication_id
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
            child
            for child in relationships["children"]
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
        tasks: list[dict[str, Any]],
        attempts: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        verifications: list[dict[str, Any]],
        communications: list[dict[str, Any]],
        heartbeat: dict[str, Any] | None,
        totals: dict[str, int],
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
                "tasks_total": int(totals.get("tasks") or 0),
                "attempts_total": int(totals.get("attempts") or 0),
                "artifacts_total": int(totals.get("artifacts") or 0),
                "verifications_total": int(totals.get("verifications") or 0),
                "communications_total": int(totals.get("communications") or 0),
                "blocked_tasks": sum(1 for task in tasks if task.get("status") == "blocked"),
                "failed_attempts": sum(
                    1 for attempt in attempts if attempt.get("outcome") == "failed"
                ),
                "pending_tasks": sum(1 for task in tasks if task.get("status") == "pending"),
                "prime_attempts": int(inference_telemetry.get("prime_spend_attempts") or 0),
                "avoidable_prime_attempts": int(
                    inference_telemetry.get("avoidable_prime_attempts") or 0
                ),
                "unjustified_prime_attempts": int(
                    inference_telemetry.get("unjustified_prime_attempts") or 0
                ),
            },
            "runtime": self._history_runtime_status(
                tasks=tasks, attempts=attempts, heartbeat=heartbeat
            ),
            "bottlenecks": self._history_bottlenecks(
                tasks=tasks, attempts=attempts, inference_telemetry=inference_telemetry
            ),
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
        tasks: list[dict[str, Any]],
        attempts: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        verifications: list[dict[str, Any]],
        communications: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for task in tasks[:12]:
            events.append(
                {
                    "event_kind": "task",
                    "event_id": task.get("task_id"),
                    "title": task.get("title"),
                    "summary": task.get("description"),
                    "status": task.get("status"),
                    "timestamp": task.get("updated_at"),
                }
            )
        for attempt in attempts[:12]:
            events.append(
                {
                    "event_kind": "attempt",
                    "event_id": attempt.get("attempt_id"),
                    "title": attempt.get("actor"),
                    "summary": attempt.get("result_summary")
                    or attempt.get("failure_kind")
                    or "Execution attempt recorded.",
                    "status": attempt.get("outcome"),
                    "timestamp": attempt.get("ended_at") or attempt.get("started_at"),
                }
            )
        for artifact in artifacts[:12]:
            events.append(
                {
                    "event_kind": "artifact",
                    "event_id": artifact.get("artifact_id"),
                    "title": artifact.get("title"),
                    "summary": artifact.get("content_summary")
                    or artifact.get("description")
                    or artifact.get("artifact_type"),
                    "status": artifact.get("status"),
                    "timestamp": artifact.get("updated_at"),
                }
            )
        for verification in verifications[:8]:
            events.append(
                {
                    "event_kind": "verification",
                    "event_id": verification.get("verification_id"),
                    "title": f"{verification.get('target_kind') or 'unknown'}:{verification.get('target_id') or 'unknown'}",
                    "summary": f"{verification.get('verifier') or 'unknown'} verification recorded.",
                    "status": verification.get("result"),
                    "timestamp": verification.get("created_at"),
                }
            )
        for message in communications[:8]:
            body = str(message.get("message") or "").strip()
            events.append(
                {
                    "event_kind": "communication",
                    "event_id": message.get("communication_id"),
                    "title": f"{message.get('sender')} -> {message.get('recipient')}",
                    "summary": body or f"{message.get('intent') or message.get('kind')} message",
                    "status": message.get("status"),
                    "timestamp": message.get("created_at"),
                }
            )
        events.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
        return events

    def _history_bottlenecks(
        self,
        *,
        tasks: list[dict[str, Any]],
        attempts: list[dict[str, Any]],
        inference_telemetry: dict[str, Any],
    ) -> list[dict[str, Any]]:
        bottlenecks: list[dict[str, Any]] = []
        blocked_tasks = sum(1 for task in tasks if task.get("status") == "blocked")
        if blocked_tasks:
            bottlenecks.append(
                {
                    "title": "Blocked tasks",
                    "summary": f"{blocked_tasks} task(s) are currently blocked and may need intervention.",
                    "severity": "moderate" if blocked_tasks < 3 else "high",
                }
            )
        failed_attempts = sum(1 for attempt in attempts if attempt.get("outcome") == "failed")
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
        tasks: list[dict[str, Any]],
        attempts: list[dict[str, Any]],
        heartbeat: dict[str, Any] | None,
    ) -> dict[str, Any]:
        heartbeat_payload: dict[str, Any] = {}
        if heartbeat is not None:
            try:
                heartbeat_payload = json.loads(str(heartbeat.get("content_summary") or "{}"))
            except Exception:
                heartbeat_payload = {}
        stale = False
        interval_seconds = int(heartbeat_payload.get("interval_seconds") or 0)
        if heartbeat is not None and interval_seconds > 0:
            try:
                updated_at = datetime.fromisoformat(str(heartbeat.get("updated_at") or ""))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                stale = (datetime.now(timezone.utc) - updated_at).total_seconds() > max(
                    interval_seconds * 3, 180
                )
            except Exception:
                stale = False
        return {
            "daemon_configured": heartbeat is not None,
            "stale": stale,
            "latest_heartbeat": heartbeat,
            "latest_heartbeat_payload": heartbeat_payload,
            "last_successful_heartbeat": heartbeat
            if heartbeat and heartbeat.get("provenance_status") == "ok"
            else None,
            "last_failed_heartbeat": heartbeat
            if heartbeat and heartbeat.get("provenance_status") == "failed"
            else None,
            "latest_attempt": None if not attempts else attempts[0],
            "latest_task": None if not tasks else tasks[0],
        }

    def _is_history_worthy_artifact(self, artifact: ArtifactRecord) -> bool:
        if isinstance(artifact, dict):
            artifact_type = str(artifact.get("artifact_type") or "").strip().lower()
        else:
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
                ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
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
