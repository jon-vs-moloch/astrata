"""Aggregation and action helpers for Astrata's first local UI shell."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrata.comms.intake import process_inbound_messages
from astrata.comms.lanes import PrincipalMessageLane
from astrata.comms.runtime import LaneRuntime
from astrata.config.settings import Settings, load_settings
from astrata.governance.documents import load_governance_bundle
from astrata.local.backends.llama_cpp import LlamaCppBackend
from astrata.local.hardware import probe_thermal_state
from astrata.local.runtime.client import LocalRuntimeClient
from astrata.local.runtime.manager import LocalRuntimeManager
from astrata.local.runtime.processes import ManagedProcessController
from astrata.local.thermal import ThermalController
from astrata.loop0.runner import Loop0Runner
from astrata.providers.registry import build_default_registry
from astrata.records.communications import CommunicationRecord
from astrata.records.models import AttemptRecord, ArtifactRecord, TaskRecord, VerificationRecord
from astrata.routing.policy import RouteChooser
from astrata.storage.db import AstrataDatabase
from astrata.startup.diagnostics import (
    generate_python_preflight_report,
    load_preflight_report,
    load_runtime_report,
    run_startup_reflection,
)


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
            "local_runtime": self._local_runtime_snapshot(),
            "queue": {
                "counts": dict(Counter(task.status for task in tasks)),
                "recent_tasks": [self._task_summary(task) for task in tasks[:8]],
            },
            "attempts": {
                "counts": dict(Counter(attempt.outcome for attempt in attempts)),
                "recent_attempts": [self._attempt_summary(attempt) for attempt in attempts[:8]],
            },
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

    def stop_local_runtime(self) -> dict[str, Any]:
        manager = self._local_runtime_manager()
        status = manager.stop_managed()
        return {
            "status": "stopped",
            "managed_process": self._managed_process_summary(status),
        }

    def _db(self) -> AstrataDatabase:
        db = AstrataDatabase(self.settings.paths.data_dir / "astrata.db")
        db.initialize()
        return db

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
