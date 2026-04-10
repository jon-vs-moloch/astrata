"""Minimal Loop 0 runner."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrata.audit import (
    ObservationSignal,
    review_audit_review,
    review_consensus_judgment,
    select_audit_followup_policy,
    select_signal_followup_policy,
    signals_from_inference_telemetry,
    signals_from_review,
)
from astrata.audit.posture import VerificationPostureStore
from astrata.config.settings import Settings
from astrata.comms.intake import normalize_derived_task_proposal
from astrata.comms.lanes import HandoffLane, PrincipalMessageLane
from astrata.context import build_quota_snapshot, summarize_inference_activity
from astrata.controllers.base import ControllerEnvelope
from astrata.controllers.coordinator import CoordinatorController
from astrata.controllers.local_executor import LocalExecutorController
from astrata.governance.authority import delegated_task_approval
from astrata.governance.documents import GovernanceBundle, load_governance_bundle
from astrata.eval.observations import EvalObservation, EvalObservationStore
from astrata.governance.policy import (
    GovernanceDriftMonitor,
    governance_change_is_authorized,
    protected_governance_paths,
)
from astrata.memory import build_memory_augmented_request, default_memory_store_path
from astrata.loop0.planner import Loop0Planner, PlannerSnapshot
from astrata.loop0.resolution import determine_task_resolution
from astrata.procedures.execution import BoundedFileGenerationProcedure, ProcedureExecutionRequest
from astrata.procedures.health import RouteHealthStore
from astrata.procedures.models import ProcedureRecord, ProcedureStructure, ProcedureTaskNode
from astrata.procedures.registry import (
    ResolvedProcedure,
    build_default_procedure_registry,
    infer_actor_capability,
)
from astrata.providers.base import CompletionRequest, Message
from astrata.providers.registry import ProviderRegistry, build_default_registry
from astrata.records.handoffs import HandoffRecord
from astrata.records.models import ArtifactRecord, AttemptRecord, TaskRecord, VerificationRecord
from astrata.routing.advisor import RoutePerformanceAdvisor
from astrata.routing.prime_policy import infer_task_policy
from astrata.routing.policy import RouteChooser
from astrata.scheduling.prioritizer import WorkPrioritizer
from astrata.scheduling.quota import QuotaPolicy, default_source_limits
from astrata.scheduling.work_pool import ScheduledWorkItem
from astrata.storage.db import AstrataDatabase
from astrata.verification.basic import (
    VerificationResult,
    inspect_expected_paths,
    inspect_weak_expected_paths,
    verify_expected_paths,
    verify_gap_candidate,
    verify_strengthening_candidate,
    verify_weak_candidate,
)
from astrata.verification.review import review_verification
from astrata.workers.runtime import WorkerRuntime, worker_id_for_route


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Loop0TaskCandidate:
    key: str
    title: str
    description: str
    expected_paths: tuple[str, ...]
    strategy: str = "normal"
    priority: int = 5
    urgency: int = 3
    risk: str = "low"
    source_task_id: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class Loop0CandidateAssessment:
    candidate: Loop0TaskCandidate
    inspection: dict[str, Any]
    verification: VerificationResult


SEED_LOOP0_CANDIDATES: tuple[Loop0TaskCandidate, ...] = (
    Loop0TaskCandidate(
        key="verification-review",
        title="Create audit review module",
        description="Add the missing audit review module to strengthen the verification/audit loop.",
        expected_paths=("astrata/audit/review.py", "astrata/audit/__init__.py"),
    ),
    Loop0TaskCandidate(
        key="variants-models",
        title="Create variant models module",
        description="Add the missing variants model module for bounded variant tracking.",
        expected_paths=("astrata/variants/models.py", "astrata/variants/__init__.py"),
    ),
    Loop0TaskCandidate(
        key="procedures-registry",
        title="Create procedure registry module",
        description="Add the missing procedure registry module for reusable structure capture.",
        expected_paths=("astrata/procedures/registry.py", "astrata/procedures/__init__.py"),
    ),
    Loop0TaskCandidate(
        key="context-telemetry",
        title="Create context telemetry module",
        description="Add the missing context telemetry module for token-pressure awareness.",
        expected_paths=("astrata/context/telemetry.py", "astrata/context/__init__.py"),
    ),
    Loop0TaskCandidate(
        key="controller-base",
        title="Create controller base module",
        description="Add the missing controller base module for minimal federated control.",
        expected_paths=("astrata/controllers/base.py", "astrata/controllers/__init__.py"),
    ),
)

EXECUTABLE_MESSAGE_TASK_SOURCES = {
    "message_intake",
    "message_task_followup",
    "artifact_finding",
    "alignment_maintenance",
    "audit_followup",
    "observation_signal",
    "startup_diagnostic",
}


class Loop0Runner:
    def __init__(
        self,
        *,
        settings: Settings,
        db: AstrataDatabase,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.registry = registry or build_default_registry()
        self.procedure_registry = build_default_procedure_registry()
        self.router = RouteChooser(self.registry)
        limits = default_source_limits()
        limits["codex"] = settings.runtime_limits.codex_direct_requests_per_hour
        limits["cli:codex-cli"] = settings.runtime_limits.codex_cli_requests_per_hour
        limits["cli:kilocode"] = settings.runtime_limits.kilocode_requests_per_hour
        limits["cli:gemini-cli"] = settings.runtime_limits.gemini_requests_per_hour
        limits["cli:claude-code"] = settings.runtime_limits.claude_requests_per_hour
        limits["openai"] = settings.runtime_limits.openai_requests_per_hour
        limits["google"] = settings.runtime_limits.google_requests_per_hour
        limits["anthropic"] = settings.runtime_limits.anthropic_requests_per_hour
        limits["custom"] = settings.runtime_limits.custom_requests_per_hour
        health_store = RouteHealthStore(settings.paths.data_dir / "route_health.json")
        self.health_store = health_store
        self.route_observations = EvalObservationStore(state_path=settings.paths.data_dir / "eval_observations.json")
        self.verification_posture = VerificationPostureStore(settings.paths.data_dir / "verification_posture.json")
        quota_policy = QuotaPolicy(db=db, limits_per_source=limits, registry=self.registry)
        self.quota_policy = quota_policy
        self.procedures = BoundedFileGenerationProcedure(
            registry=self.registry,
            router=self.router,
            health_store=health_store,
            quota_policy=quota_policy,
        )
        self.coordinator = CoordinatorController(
            router=self.router,
            quota_policy=quota_policy,
            route_advisor=RoutePerformanceAdvisor.from_data_dir(settings.paths.data_dir),
        )
        self.local_executor = LocalExecutorController(
            registry=self.registry,
            health_store=health_store,
        )
        self.handoff_lane = HandoffLane()
        self.principal_lane = PrincipalMessageLane(db=self.db)
        self.planner = Loop0Planner()
        self.prioritizer = WorkPrioritizer()
        self.governance: GovernanceBundle = load_governance_bundle(settings.paths.project_root)
        self.governance_drift_monitor = GovernanceDriftMonitor(
            settings.paths.data_dir / "governance_drift_state.json"
        )
        self.worker_runtime = WorkerRuntime(settings=settings, db=db, registry=self.registry)

    def next_candidate(self) -> Loop0TaskCandidate | None:
        assessment = self.next_candidate_assessment()
        return None if assessment is None else assessment.candidate

    def next_candidate_assessment(self) -> Loop0CandidateAssessment | None:
        self._reconcile_pending_tasks()
        work_items: list[ScheduledWorkItem] = []
        for candidate in self._pending_message_task_candidates():
            scheduling_metadata = self._scheduling_metadata_for_task_payload(dict(candidate.metadata or {}))
            assessment = self._message_task_assessment(candidate)
            work_items.append(
                ScheduledWorkItem.from_assessment(
                    assessment,
                    source_kind="message_task",
                    created_at=str(dict(candidate.metadata or {}).get("created_at") or ""),
                    metadata={"strategy": candidate.strategy, **scheduling_metadata},
                )
            )
        for candidate in self._retry_task_candidates():
            scheduling_metadata = self._scheduling_metadata_for_task_payload(dict(candidate.metadata or {}))
            assessment = Loop0CandidateAssessment(
                candidate=candidate,
                inspection={"task_record": dict(candidate.metadata or {})},
                verification=VerificationResult(
                    result="pass",
                    confidence=0.7,
                    summary="Retry candidate is eligible for another bounded attempt.",
                    evidence={"task_record": dict(candidate.metadata or {})},
                ),
            )
            work_items.append(
                ScheduledWorkItem.from_assessment(
                    assessment,
                    source_kind="retry_task",
                    created_at=str(dict(candidate.metadata or {}).get("updated_at") or ""),
                    metadata={"strategy": candidate.strategy, **scheduling_metadata},
                )
            )
        for candidate in self._artifact_finding_candidates():
            scheduling_metadata = self._scheduling_metadata_for_task_payload(dict(candidate.metadata or {}))
            scheduling_metadata["artifact_confidence"] = dict(candidate.metadata or {}).get("artifact_confidence", 0.0)
            assessment = Loop0CandidateAssessment(
                candidate=candidate,
                inspection={"artifact_finding": dict(candidate.metadata or {})},
                verification=VerificationResult(
                    result="pass",
                    confidence=0.75,
                    summary="Artifact finding is concrete enough to compete for execution.",
                    evidence={"artifact_finding": dict(candidate.metadata or {})},
                ),
            )
            work_items.append(
                ScheduledWorkItem.from_assessment(
                    assessment,
                    source_kind="artifact_finding",
                    created_at=str(dict(candidate.metadata or {}).get("created_at") or ""),
                    metadata={"strategy": candidate.strategy, **scheduling_metadata},
                )
            )
        for candidate in self._alignment_maintenance_candidates():
            scheduling_metadata = self._scheduling_metadata_for_task_payload(dict(candidate.metadata or {}))
            assessment = self._message_task_assessment(candidate)
            work_items.append(
                ScheduledWorkItem.from_assessment(
                    assessment,
                    source_kind="alignment_maintenance",
                    created_at=str(dict(candidate.metadata or {}).get("created_at") or ""),
                    metadata={"strategy": candidate.strategy, **scheduling_metadata},
                )
            )
        for candidate in self._candidate_pool():
            source_kind = self._source_kind_for_candidate(candidate)
            scheduling_metadata = self._scheduling_metadata_for_candidate(candidate)
            if candidate.strategy == "strengthen":
                inspection = inspect_weak_expected_paths(
                    self.settings.paths.project_root, list(candidate.expected_paths)
                )
                result = verify_weak_candidate(
                    self.settings.paths.project_root, list(candidate.expected_paths)
                )
                if result.result == "pass":
                    work_items.append(
                        ScheduledWorkItem.from_assessment(
                            Loop0CandidateAssessment(
                                candidate=candidate,
                                inspection=inspection,
                                verification=result,
                            ),
                            source_kind=source_kind,
                            metadata={"strategy": candidate.strategy, **scheduling_metadata},
                        )
                    )
                continue
            result = verify_expected_paths(self.settings.paths.project_root, list(candidate.expected_paths))
            if result.result == "fail":
                inspection = inspect_expected_paths(
                    self.settings.paths.project_root, list(candidate.expected_paths)
                )
                work_items.append(
                    ScheduledWorkItem.from_assessment(
                        Loop0CandidateAssessment(
                            candidate=candidate,
                            inspection=inspection,
                            verification=result,
                        ),
                        source_kind=source_kind,
                        metadata={"strategy": candidate.strategy, **scheduling_metadata},
                    )
                )
        selection = self.prioritizer.select(work_items)
        if selection is None:
            return None
        return Loop0CandidateAssessment(
            candidate=selection.item.candidate,
            inspection=selection.item.inspection,
            verification=selection.item.verification,
        )

    def _message_task_assessment(self, candidate: Loop0TaskCandidate) -> Loop0CandidateAssessment:
        return Loop0CandidateAssessment(
            candidate=candidate,
            inspection={"task_record": dict(candidate.metadata or {})},
            verification=VerificationResult(
                result="pass",
                confidence=0.8,
                summary="Inbound task is pending and eligible for unified scheduling.",
                evidence={"task_record": dict(candidate.metadata or {})},
            ),
        )

    def _supervise_worker_tasks(self) -> list[TaskRecord]:
        reconciled: list[TaskRecord] = []
        tasks_by_id = self._tasks_by_id()
        for task_payload in self.db.list_records("tasks"):
            provenance = dict(task_payload.get("provenance") or {})
            if provenance.get("source") != "worker_delegation":
                continue
            if task_payload.get("status") != "working":
                continue
            parent_task_id = str(task_payload.get("parent_task_id") or provenance.get("parent_task_id") or "").strip()
            if not parent_task_id:
                continue
            pending_request = self._pending_worker_request_for_task(str(task_payload.get("task_id") or ""))
            health = self._worker_health_snapshot(pending_request=pending_request)
            updated_worker = TaskRecord(
                **{
                    **dict(task_payload),
                    "updated_at": _now_iso(),
                    "provenance": {
                        **provenance,
                        "worker_health": health,
                    },
                }
            )
            self.db.upsert_task(updated_worker)
            reconciled.append(updated_worker)
            if health["status"] != "stalled":
                continue
            parent_task_payload = dict(tasks_by_id.get(parent_task_id) or {})
            if not parent_task_payload:
                continue
            reconciled.extend(
                self._repair_stalled_worker_task(
                    worker_task_payload=updated_worker.model_dump(mode="json"),
                    parent_task_payload=parent_task_payload,
                    pending_request=pending_request,
                )
            )
        return reconciled

    def _pending_worker_request_for_task(self, worker_task_id: str) -> dict[str, Any] | None:
        normalized_task_id = str(worker_task_id or "").strip()
        if not normalized_task_id:
            return None
        matches = [
            payload
            for payload in self.db.list_records("communications")
            if payload.get("intent") == "worker_delegation_request"
            and payload.get("status") not in {"acknowledged", "resolved"}
            and str(dict(payload.get("payload") or {}).get("worker_task_id") or "").strip() == normalized_task_id
        ]
        if not matches:
            return None
        return sorted(
            matches,
            key=lambda payload: str(payload.get("created_at") or payload.get("delivered_at") or ""),
            reverse=True,
        )[0]

    def _payload_age_hours(self, payload: dict[str, Any]) -> float:
        timestamp = (
            str(payload.get("updated_at") or "").strip()
            or str(payload.get("delivered_at") or "").strip()
            or str(payload.get("created_at") or "").strip()
        )
        if not timestamp:
            return 0.0
        try:
            parsed = datetime.fromisoformat(timestamp)
        except Exception:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0)

    def _worker_health_snapshot(self, *, pending_request: dict[str, Any] | None) -> dict[str, Any]:
        if pending_request is None:
            return {
                "status": "stalled",
                "reason": "missing_request",
                "observed_at": _now_iso(),
                "request_age_hours": None,
            }
        age_hours = self._payload_age_hours(pending_request)
        if age_hours >= 0.25:
            return {
                "status": "stalled",
                "reason": "request_timeout",
                "observed_at": _now_iso(),
                "request_age_hours": round(age_hours, 3),
            }
        return {
            "status": "healthy",
            "reason": "awaiting_worker",
            "observed_at": _now_iso(),
            "request_age_hours": round(age_hours, 3),
        }

    def _supervision_route_candidates(
        self,
        *,
        current_route: dict[str, Any],
        parent_task_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        route_preferences = dict(dict(parent_task_payload.get("completion_policy") or {}).get("route_preferences") or {})
        preferred_model = str(route_preferences.get("preferred_model") or "").strip() or None
        cli_order = list(route_preferences.get("preferred_cli_tools") or [])
        if not cli_order:
            cli_order = ["kilocode", "gemini-cli", "claude-code"]
        for cli_tool in ["kilocode", "gemini-cli", "claude-code"]:
            if cli_tool not in cli_order:
                cli_order.append(cli_tool)
        current_cli_tool = str(current_route.get("cli_tool") or "").strip()
        current_model = str(current_route.get("model") or "").strip() or None
        candidates: list[dict[str, Any]] = []
        for cli_tool in cli_order:
            model_options = [None]
            if cli_tool == "gemini-cli":
                model_options = []
                for model in [preferred_model, "gemini-2.5-flash", "gemini-2.5-pro"]:
                    if model not in model_options:
                        model_options.append(model)
            elif cli_tool == current_cli_tool:
                continue
            for model in model_options:
                if cli_tool == current_cli_tool and (model or None) == current_model:
                    continue
                candidates.append(
                    {
                        "provider": "cli",
                        "cli_tool": cli_tool,
                        "model": model,
                        "reason": "worker_supervision_reassign",
                    }
                )
        return candidates

    def _route_cost_rank(self, route: dict[str, Any]) -> int:
        cli_tool = str(route.get("cli_tool") or "").strip().lower()
        model = str(route.get("model") or "").strip().lower()
        provider = str(route.get("provider") or "").strip().lower()
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

    def _quota_snapshot_for_route(self, route: dict[str, Any]) -> dict[str, Any]:
        decision = self.quota_policy.assess(route)
        return build_quota_snapshot(
            route=route,
            decision=decision,
            cost_rank=self._route_cost_rank(route),
        )

    def _select_supervision_route(
        self,
        *,
        current_route: dict[str, Any],
        parent_task_payload: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        candidates = self._supervision_route_candidates(
            current_route=current_route,
            parent_task_payload=parent_task_payload,
        )
        scored: list[dict[str, Any]] = []
        for route in candidates:
            quota = self._quota_snapshot_for_route(route)
            scored.append(
                {
                    "route": route,
                    "quota": quota,
                    "cost_rank": self._route_cost_rank(route),
                }
            )
        allowed = [item for item in scored if item["quota"]["allowed"]]
        allowed.sort(
            key=lambda item: (
                int(item["cost_rank"]),
                -float(item["quota"]["headroom_ratio"]),
                int(item["quota"]["usage_last_hour"]),
            )
        )
        if allowed:
            return dict(allowed[0]["route"]), scored
        scored.sort(
            key=lambda item: (
                item["quota"]["next_allowed_at"] or "",
                int(item["cost_rank"]),
            )
        )
        return None, scored

    def _repair_stalled_worker_task(
        self,
        *,
        worker_task_payload: dict[str, Any],
        parent_task_payload: dict[str, Any],
        pending_request: dict[str, Any] | None,
    ) -> list[TaskRecord]:
        worker_task_id = str(worker_task_payload.get("task_id") or "").strip()
        parent_task_id = str(parent_task_payload.get("task_id") or "").strip()
        worker_provenance = dict(worker_task_payload.get("provenance") or {})
        current_route = dict(worker_provenance.get("route") or {})
        retry_index = int(dict(worker_provenance.get("supervision") or {}).get("retry_index") or 0)
        selected_route, scored_candidates = self._select_supervision_route(
            current_route=current_route,
            parent_task_payload=parent_task_payload,
        )
        action = "reassign" if selected_route is not None and retry_index < 2 else "block"
        artifact = ArtifactRecord(
            artifact_type="worker_supervision",
            title=f"Worker supervision: {parent_task_payload.get('title') or parent_task_id}",
            description="Supervisory decision for a delegated worker that stopped making bounded progress.",
            content_summary=json.dumps(
                {
                    "parent_task_id": parent_task_id,
                    "worker_task_id": worker_task_id,
                    "current_route": current_route,
                    "pending_request_id": None if pending_request is None else pending_request.get("communication_id"),
                    "health": dict(worker_provenance.get("worker_health") or {}),
                    "retry_index": retry_index,
                    "action": action,
                    "selected_route": selected_route,
                    "route_candidates": scored_candidates,
                },
                indent=2,
            ),
            provenance={
                "task_id": parent_task_id,
                "worker_task_id": worker_task_id,
                "source": "worker_supervision",
            },
            status="degraded" if action == "reassign" else "broken",
        )
        self.db.upsert_artifact(artifact)
        if pending_request is not None and str(pending_request.get("communication_id") or "").strip():
            self.principal_lane.resolve(str(pending_request.get("communication_id") or "").strip())
        failed_worker = TaskRecord(
            **{
                **dict(worker_task_payload),
                "status": "failed",
                "updated_at": _now_iso(),
                "provenance": {
                    **worker_provenance,
                    "supervision": {
                        **dict(worker_provenance.get("supervision") or {}),
                        "retry_index": retry_index,
                        "action": action,
                        "selected_route": selected_route,
                        "artifact_id": artifact.artifact_id,
                        "resolved_at": _now_iso(),
                    },
                },
            }
        )
        self.db.upsert_task(failed_worker)
        parent_update = {
            **dict(parent_task_payload),
            "updated_at": _now_iso(),
            "active_child_ids": [
                child_id
                for child_id in list(parent_task_payload.get("active_child_ids") or [])
                if str(child_id).strip() and str(child_id).strip() != worker_task_id
            ],
            "provenance": {
                **dict(parent_task_payload.get("provenance") or {}),
                "worker_supervision": {
                    "action": action,
                    "worker_task_id": worker_task_id,
                    "selected_route": selected_route,
                    "artifact_id": artifact.artifact_id,
                    "updated_at": _now_iso(),
                },
            },
        }
        results: list[TaskRecord] = [failed_worker]
        if action != "reassign" or selected_route is None:
            blocked_parent = TaskRecord(
                **{
                    **parent_update,
                    "status": "blocked",
                }
            )
            self.db.upsert_task(blocked_parent)
            results.insert(0, blocked_parent)
            return results
        candidate = Loop0TaskCandidate(
            key=f"retry:{parent_task_id}:worker-supervision",
            title=str(parent_task_payload.get("title") or parent_task_id),
            description=str(parent_task_payload.get("description") or ""),
            expected_paths=(),
            strategy="message_task",
            priority=int(parent_task_payload.get("priority") or 0),
            urgency=int(parent_task_payload.get("urgency") or 0),
            risk=str(parent_task_payload.get("risk") or "low"),
            source_task_id=parent_task_id,
            metadata={
                **parent_update,
                "status": "working",
            },
        )
        self._delegate_message_task(
            candidate,
            task_payload={
                **parent_update,
                "status": "working",
            },
            task_id=parent_task_id,
            route=selected_route,
            baseline_inspection={
                "task_record": parent_task_payload,
                "worker_supervision": {
                    "stalled_worker_task_id": worker_task_id,
                    "selected_route": selected_route,
                },
            },
        )
        refreshed_parent = next(
            (
                TaskRecord(**payload)
                for payload in self.db.list_records("tasks")
                if str(payload.get("task_id") or "").strip() == parent_task_id
            ),
            None,
        )
        if refreshed_parent is not None:
            results.insert(0, refreshed_parent)
        return results

    def _reconcile_pending_tasks(self) -> list[TaskRecord]:
        reconciled: list[TaskRecord] = []
        reconciled.extend(self._reconcile_worker_results())
        reconciled.extend(self._supervise_worker_tasks())
        for task_payload in self.db.list_records("tasks"):
            if task_payload.get("status") not in {"pending", "working"}:
                continue
            update = self._reconciled_task_payload(task_payload)
            if update is None:
                continue
            task = TaskRecord(**update)
            self.db.upsert_task(task)
            reconciled.append(task)
        return reconciled

    def _reconciled_task_payload(self, task_payload: dict[str, Any]) -> dict[str, Any] | None:
        payload = dict(task_payload)
        provenance = dict(payload.get("provenance") or {})
        if payload.get("status") == "working":
            return None
        closure_reason: str | None = None
        new_status: str | None = None
        if self._is_low_signal_message_task(payload):
            new_status = "superseded"
            closure_reason = "low_signal_pending_work"
        elif self._is_duplicate_pending_task(payload):
            new_status = "superseded"
            closure_reason = "duplicate_pending_work"
        elif self._task_goal_already_realized(payload):
            new_status = "satisfied"
            closure_reason = "filesystem_state_now_matches_task_goal"
        elif self._task_likely_satisfied(payload):
            source = str(provenance.get("source") or "").strip()
            if source in EXECUTABLE_MESSAGE_TASK_SOURCES:
                new_status = "satisfied"
                closure_reason = "later_completed_work_answered_this_task"
            else:
                new_status = "superseded"
                closure_reason = "later_completed_work_superseded_this_task"
        if not new_status:
            return None
        payload["status"] = new_status
        payload["updated_at"] = _now_iso()
        payload["provenance"] = {
            **provenance,
            "closure": {
                "status": new_status,
                "reason": closure_reason,
                "resolved_at": payload["updated_at"],
            },
        }
        return payload

    def _reconcile_worker_results(self) -> list[TaskRecord]:
        reconciled: list[TaskRecord] = []
        for message in self.principal_lane.list_messages(recipient="astrata", include_acknowledged=False):
            if message.intent != "worker_delegation_result":
                continue
            payload = dict(message.payload or {})
            task_id = str(payload.get("task_id") or "").strip()
            worker_task_id = str(payload.get("worker_task_id") or "").strip()
            if not task_id:
                self.principal_lane.acknowledge(message.communication_id)
                continue
            consensus_task_payload = None
            if worker_task_id:
                consensus_task_payload = next(
                    (
                        item
                        for item in self.db.list_records("tasks")
                        if str(item.get("task_id") or "").strip() == worker_task_id
                        and str(dict(item.get("provenance") or {}).get("role") or "").strip() == "consensus_review"
                    ),
                    None,
                )
            if consensus_task_payload is not None:
                consensus_updates = self._reconcile_consensus_worker_result(
                    worker_task_payload=consensus_task_payload,
                    message_payload=payload,
                )
                for update in consensus_updates:
                    self.db.upsert_task(update)
                    reconciled.append(update)
                self._record_worker_completion_attempt(task=TaskRecord(**consensus_task_payload), message_payload=payload)
                self.principal_lane.acknowledge(message.communication_id)
                continue
            task_payload = next(
                (item for item in self.db.list_records("tasks") if str(item.get("task_id") or "").strip() == task_id),
                None,
            )
            if not task_payload:
                self.principal_lane.acknowledge(message.communication_id)
                continue
            task = self._task_from_worker_result(task_payload, message_payload=payload)
            self.db.upsert_task(task)
            self._sync_batched_peer_tasks(task)
            self._record_worker_completion_attempt(task=task, message_payload=payload)
            self.principal_lane.acknowledge(message.communication_id)
            reconciled.append(task)
        return reconciled

    def _task_from_worker_result(
        self,
        task_payload: dict[str, Any],
        *,
        message_payload: dict[str, Any],
    ) -> TaskRecord:
        payload = dict(task_payload)
        raw_content = str(message_payload.get("raw_content") or "")
        principal_response = self._extract_principal_response(raw_content)
        followup_tasks = self._extract_followup_tasks(raw_content, payload)
        derived_artifact = self._extract_message_artifact(
            raw_content,
            Loop0TaskCandidate(
                key=f"task:{payload.get('task_id')}",
                title=str(payload.get("title") or ""),
                description=str(payload.get("description") or ""),
                expected_paths=(),
                strategy="message_task",
                metadata=payload,
            ),
            payload,
        )
        lane_sender, conversation_id = self._message_task_lane_context(payload)
        notice = self.principal_lane.send(
            sender=lane_sender,
            recipient="principal",
            conversation_id=conversation_id,
            kind="notice",
            intent="message_task_response",
            payload={
                "task_id": payload.get("task_id"),
                "title": payload.get("title"),
                "description": payload.get("description"),
                "completion_policy": dict(payload.get("completion_policy") or {}),
                "assistant_output": principal_response,
                "provider": message_payload.get("route", {}).get("provider"),
                "model": message_payload.get("route", {}).get("model"),
                "route": message_payload.get("route") or {},
                "followup_tasks": followup_tasks,
                "derived_artifact": derived_artifact,
            },
            priority=int(payload.get("priority") or 0),
            urgency=int(payload.get("urgency") or 0),
            related_task_ids=[str(payload.get("task_id") or "")],
        )
        if isinstance(derived_artifact, dict) and derived_artifact.get("artifact_type"):
            self.db.upsert_artifact(
                ArtifactRecord(
                    artifact_type=str(derived_artifact.get("artifact_type") or "message_analysis"),
                    title=str(derived_artifact.get("title") or payload.get("title") or payload.get("task_id")),
                    description=str(derived_artifact.get("description") or ""),
                    content_summary=json.dumps(derived_artifact, indent=2),
                    provenance={
                        "task_id": str(payload.get("task_id") or ""),
                        "source_communication_id": str(message_payload.get("source_communication_id") or ""),
                    },
                    status=str(derived_artifact.get("status") or "good"),
                )
            )
        if followup_tasks or derived_artifact:
            parent_task = TaskRecord(**payload)
            self._materialize_followup_tasks(
                candidate=Loop0TaskCandidate(
                    key=f"task:{payload.get('task_id')}",
                    title=str(payload.get("title") or ""),
                    description=str(payload.get("description") or ""),
                    expected_paths=(),
                    strategy="message_task",
                    metadata=payload,
                ),
                parent_task=parent_task,
                implementation={
                    "followup_tasks": followup_tasks,
                    "derived_artifact": derived_artifact,
                    "assistant_output": principal_response,
                },
            )
        if str(message_payload.get("status") or "") == "applied":
            payload["status"] = "complete"
            resolution_payload = None
        else:
            resolution = determine_task_resolution(
                task_payload=payload,
                message_payload=message_payload,
                attempts=[
                    attempt
                    for attempt in self.db.list_records("attempts")
                    if str(attempt.get("task_id") or "").strip() == str(payload.get("task_id") or "").strip()
                ],
            )
            payload["status"] = resolution.next_status
            resolution_payload = resolution.model_dump(mode="json")
            if resolution.followup_specs:
                parent_task = TaskRecord(**payload)
                self._materialize_followup_tasks(
                    candidate=Loop0TaskCandidate(
                        key=f"task:{payload.get('task_id')}",
                        title=str(payload.get("title") or ""),
                        description=str(payload.get("description") or ""),
                        expected_paths=(),
                        strategy="message_task",
                        metadata=payload,
                    ),
                    parent_task=parent_task,
                    implementation={
                        "followup_tasks": resolution.followup_specs,
                        "derived_artifact": {
                            "artifact_type": "task_resolution",
                            "title": f"Resolution for {payload.get('title') or payload.get('task_id')}",
                            "description": str(payload.get("description") or ""),
                        "summary": resolution.reason,
                            "confidence": resolution.confidence,
                            "findings": [resolution.kind],
                            "status": "degraded",
                        },
                        "assistant_output": principal_response,
                    },
                )
            self.db.upsert_artifact(
                ArtifactRecord(
                    artifact_type="task_resolution",
                    title=f"Task resolution: {payload.get('title') or payload.get('task_id')}",
                    description="Deterministic resolution selected after delegated worker failure.",
                    content_summary=json.dumps(resolution_payload, indent=2),
                    provenance={
                        "task_id": str(payload.get("task_id") or ""),
                        "source": "worker_resolution_policy",
                    },
                    status="degraded",
                )
            )
        payload["updated_at"] = _now_iso()
        provenance = dict(payload.get("provenance") or {})
        worker_task_id = str(message_payload.get("worker_task_id") or provenance.get("worker_task_id") or "").strip()
        if worker_task_id:
            worker_task_payload = next(
                (
                    item
                    for item in self.db.list_records("tasks")
                    if str(item.get("task_id") or "").strip() == worker_task_id
                ),
                None,
            )
            if worker_task_payload:
                worker_task = TaskRecord(
                    **{
                        **dict(worker_task_payload),
                        "status": "complete" if payload["status"] == "complete" else "failed",
                        "updated_at": payload["updated_at"],
                        "provenance": {
                            **dict(worker_task_payload.get("provenance") or {}),
                            "worker_result": {
                                "result_message_id": message_payload.get("source_communication_id"),
                                "status": message_payload.get("status"),
                            },
                        },
                    }
                )
                self.db.upsert_task(worker_task)
        payload["active_child_ids"] = [
            child_id
            for child_id in list(payload.get("active_child_ids") or [])
            if str(child_id).strip() and str(child_id).strip() != worker_task_id
        ]
        provenance["worker_result"] = {
            "worker_id": message_payload.get("worker_id"),
            "route": message_payload.get("route"),
            "result_message_id": message_payload.get("source_communication_id"),
            "emitted_communication_id": notice.communication_id,
        }
        if resolution_payload is not None:
            provenance["resolution"] = resolution_payload
        payload["provenance"] = provenance
        return TaskRecord(**payload)

    def _reconcile_consensus_worker_result(
        self,
        *,
        worker_task_payload: dict[str, Any],
        message_payload: dict[str, Any],
    ) -> list[TaskRecord]:
        worker_payload = dict(worker_task_payload)
        worker_payload["status"] = "complete" if str(message_payload.get("status") or "") == "applied" else "failed"
        worker_payload["updated_at"] = _now_iso()
        worker_payload["provenance"] = {
            **dict(worker_payload.get("provenance") or {}),
            "worker_result": {
                "route": message_payload.get("route"),
                "status": message_payload.get("status"),
                "detail": message_payload.get("detail"),
                "received_at": _now_iso(),
            },
        }
        worker_task = TaskRecord(**worker_payload)
        parent_task_id = str(worker_task.parent_task_id or dict(worker_payload.get("provenance") or {}).get("parent_task_id") or "").strip()
        parent_payload = next(
            (item for item in self.db.list_records("tasks") if str(item.get("task_id") or "").strip() == parent_task_id),
            None,
        )
        if not parent_payload:
            return [worker_task]
        parent_update = dict(parent_payload)
        parent_provenance = dict(parent_update.get("provenance") or {})
        consensus = dict(parent_provenance.get("consensus_review") or {})
        existing_results = list(consensus.get("results") or [])
        raw_content = str(message_payload.get("raw_content") or "")
        result_payload = {
            "worker_task_id": str(worker_payload.get("task_id") or ""),
            "worker_id": message_payload.get("worker_id"),
            "status": message_payload.get("status"),
            "route": message_payload.get("route"),
            "principal_response": self._extract_principal_response(raw_content),
            "followup_tasks": self._extract_followup_tasks(raw_content, parent_update),
            "derived_artifact": self._extract_message_artifact(
                raw_content,
                Loop0TaskCandidate(
                    key=f"task:{parent_update.get('task_id')}",
                    title=str(parent_update.get("title") or ""),
                    description=str(parent_update.get("description") or ""),
                    expected_paths=(),
                    strategy="message_task",
                    metadata=parent_update,
                ),
                parent_update,
            ),
            "raw_content": raw_content,
        }
        existing_results = [
            item for item in existing_results if str(item.get("worker_task_id") or "").strip() != result_payload["worker_task_id"]
        ]
        existing_results.append(result_payload)
        consensus["results"] = existing_results
        consensus["status"] = "collecting"
        parent_update["provenance"] = {
            **parent_provenance,
            "consensus_review": consensus,
        }
        parent_update["updated_at"] = _now_iso()
        successful = [item for item in existing_results if str(item.get("status") or "") == "applied"]
        required = max(2, int(consensus.get("required_reviews") or 2))
        if len(successful) >= required:
            canonical = self._normalize_consensus_text(str(successful[0].get("principal_response") or ""))
            if all(self._normalize_consensus_text(str(item.get("principal_response") or "")) == canonical for item in successful[:required]):
                approved = self._task_from_worker_result(parent_update, message_payload={
                    **message_payload,
                    "raw_content": str(successful[0].get("raw_content") or ""),
                    "route": successful[0].get("route") or message_payload.get("route") or {},
                    "status": "applied",
                })
                approved_payload = approved.model_dump(mode="json")
                approved_payload["provenance"] = {
                    **dict(approved_payload.get("provenance") or {}),
                    "consensus_review": {
                        **consensus,
                        "status": "approved",
                        "approved_at": _now_iso(),
                    },
                }
                approved_task = TaskRecord(**approved_payload)
                consensus_review = review_consensus_judgment(
                    task_id=approved_task.task_id,
                    consensus=dict(approved_task.provenance.get("consensus_review") or {}),
                )
                self._persist_audit_review(
                    review=consensus_review,
                    artifact_type="consensus_review_audit",
                    title=f"Consensus review audit: {approved_task.title}",
                    description="Second-pass audit of whether the consensus judgment matches preserved worker evidence.",
                    provenance={"task_id": approved_task.task_id},
                )
                self._sync_batched_peer_tasks(approved_task)
                return [approved_task, worker_task]
            disagreement_artifact = ArtifactRecord(
                artifact_type="consensus_review_disagreement",
                title=f"Consensus disagreement: {parent_update.get('title') or parent_task_id}",
                description="Cheap review lanes disagreed on a bounded task outcome and require escalation.",
                content_summary=json.dumps({"results": successful[:required]}, indent=2),
                provenance={"task_id": parent_task_id, "source": "consensus_review"},
                status="degraded",
            )
            self.db.upsert_artifact(disagreement_artifact)
            parent_update["status"] = "blocked"
            parent_update["provenance"] = {
                **dict(parent_update.get("provenance") or {}),
                "consensus_review": {
                    **consensus,
                    "status": "disagreement",
                    "artifact_id": disagreement_artifact.artifact_id,
                },
            }
            blocked_task = TaskRecord(**parent_update)
            consensus_review = review_consensus_judgment(
                task_id=blocked_task.task_id,
                consensus=dict(blocked_task.provenance.get("consensus_review") or {}),
            )
            self._persist_audit_review(
                review=consensus_review,
                artifact_type="consensus_review_audit",
                title=f"Consensus review audit: {blocked_task.title}",
                description="Second-pass audit of whether the disagreement judgment matches preserved worker evidence.",
                provenance={"task_id": blocked_task.task_id},
            )
            self._sync_batched_peer_tasks(blocked_task)
            return [blocked_task, worker_task]
        active_child_ids = [
            child_id
            for child_id in list(parent_update.get("active_child_ids") or [])
            if str(child_id).strip() and str(child_id).strip() != str(worker_payload.get("task_id") or "")
        ]
        pending_consensus = TaskRecord(
            **{
                **parent_update,
                "status": "working",
                "active_child_ids": active_child_ids,
            }
        )
        self._sync_batched_peer_tasks(pending_consensus)
        return [pending_consensus, worker_task]

    def _normalize_consensus_text(self, value: str) -> str:
        return " ".join(str(value or "").strip().lower().split())

    def _sync_batched_peer_tasks(self, parent_task: TaskRecord) -> None:
        batched_task_ids = [
            str(task_id).strip()
            for task_id in list(dict(parent_task.provenance or {}).get("batching", {}).get("batched_task_ids") or [])
            if str(task_id).strip()
        ]
        if not batched_task_ids:
            return
        for task_payload in self.db.list_records("tasks"):
            task_id = str(task_payload.get("task_id") or "").strip()
            if task_id not in batched_task_ids or task_id == parent_task.task_id:
                continue
            updated = TaskRecord(
                **{
                    **dict(task_payload),
                    "status": parent_task.status,
                    "parent_task_id": parent_task.task_id,
                    "updated_at": _now_iso(),
                    "provenance": {
                        **dict(task_payload.get("provenance") or {}),
                        "batched_under_task_id": parent_task.task_id,
                    },
                }
            )
            self.db.upsert_task(updated)

    def _persist_audit_review(
        self,
        *,
        review: Any,
        artifact_type: str,
        title: str,
        description: str,
        provenance: dict[str, Any],
    ) -> tuple[ArtifactRecord, ArtifactRecord]:
        review_artifact = ArtifactRecord(
            artifact_type=artifact_type,
            title=title,
            description=description,
            content_summary=review.model_dump_json(indent=2),
            provenance=provenance,
            status="good" if not review.findings else "degraded",
        )
        self.db.upsert_artifact(review_artifact)
        self._record_audit_route_observations(
            review=review,
            artifact_type=artifact_type,
            provenance=provenance,
        )
        posture = self.verification_posture.record_review(
            subject_kind=str(review.subject_kind or "unknown"),
            findings_count=len(list(review.findings or [])),
            status=str(review.status or "open"),
        )
        meta_review = review_audit_review(review=review)
        meta_artifact = ArtifactRecord(
            artifact_type=f"{artifact_type}_meta_review",
            title=f"{title} meta-review",
            description="Audit of whether the review itself is internally coherent.",
            content_summary=meta_review.model_dump_json(indent=2),
            provenance={
                **provenance,
                "review_id": review.review_id,
                "subject_kind": review.subject_kind,
                "subject_id": review.subject_id,
            },
            status="good" if not meta_review.findings else "degraded",
        )
        self.db.upsert_artifact(meta_artifact)
        meta_posture = self.verification_posture.record_review(
            subject_kind="audit_review",
            findings_count=len(list(meta_review.findings or [])),
            status=str(meta_review.status or "open"),
        )
        posture_artifact = ArtifactRecord(
            artifact_type="verification_posture",
            title=f"Verification posture: {review.subject_kind}",
            description="Current annealed verification/audit posture derived from recent review cleanliness.",
            content_summary=json.dumps(
                {
                    "subject_posture": posture,
                    "meta_review_posture": meta_posture,
                    "source_review_id": review.review_id,
                    "source_subject_kind": review.subject_kind,
                    "source_subject_id": review.subject_id,
                },
                indent=2,
            ),
            provenance={
                **provenance,
                "review_id": review.review_id,
                "subject_kind": review.subject_kind,
                "subject_id": review.subject_id,
            },
            status="good" if posture.get("level") == "relaxed" else ("degraded" if posture.get("level") == "strict" else "good"),
        )
        self.db.upsert_artifact(posture_artifact)
        policy = select_audit_followup_policy(review=review, sample_rate=int(posture.get("sample_rate") or 1))
        followup_tasks = self._materialize_review_followup_tasks(
            review=review,
            policy=policy,
            parent_provenance={
                "source": "audit_followup",
                "review_id": review.review_id,
                "subject_kind": review.subject_kind,
                "subject_id": review.subject_id,
                "artifact_type": artifact_type,
                "verification_posture": posture,
                **provenance,
            },
        )
        if followup_tasks:
            followup_artifact = ArtifactRecord(
                artifact_type=f"{artifact_type}_followups",
                title=f"{title} follow-up routing",
                description="Audit follow-up tasks derived from review findings or sampling policy.",
                content_summary=json.dumps(
                    {
                        "policy": policy,
                        "tasks": [task.model_dump(mode="json") for task in followup_tasks],
                    },
                    indent=2,
                ),
                provenance={
                    **provenance,
                    "review_id": review.review_id,
                    "subject_kind": review.subject_kind,
                    "subject_id": review.subject_id,
                },
                status="degraded" if policy.get("mode") == "targeted" else "good",
            )
            self.db.upsert_artifact(followup_artifact)
        return review_artifact, meta_artifact

    def _persist_observation_signal(
        self,
        *,
        signal: Any,
        artifact_type: str,
        title: str,
        description: str,
        provenance: dict[str, Any],
    ) -> ArtifactRecord:
        signal_artifact = ArtifactRecord(
            artifact_type=artifact_type,
            title=title,
            description=description,
            content_summary=signal.model_dump_json(indent=2),
            provenance={
                **provenance,
                "signal_id": signal.signal_id,
                "signal_kind": signal.signal_kind,
                "subject_kind": signal.subject_kind,
                "subject_id": signal.subject_id,
            },
            status="degraded" if signal.signal_kind in {"surprise", "problem", "drift"} else "good",
        )
        self.db.upsert_artifact(signal_artifact)
        policy = select_signal_followup_policy(signal=signal)
        followup_tasks = self._materialize_review_followup_tasks(
            review=signal,
            policy=policy,
            parent_provenance={
                "source": "observation_signal",
                "signal_id": signal.signal_id,
                "signal_kind": signal.signal_kind,
                "subject_kind": signal.subject_kind,
                "subject_id": signal.subject_id,
                "artifact_type": artifact_type,
                **provenance,
            },
        )
        if followup_tasks:
            followup_artifact = ArtifactRecord(
                artifact_type=f"{artifact_type}_followups",
                title=f"{title} follow-up routing",
                description="Follow-up tasks derived from a durable internal observation signal.",
                content_summary=json.dumps(
                    {
                        "policy": policy,
                        "tasks": [task.model_dump(mode="json") for task in followup_tasks],
                    },
                    indent=2,
                ),
                provenance={
                    **provenance,
                    "signal_id": signal.signal_id,
                    "subject_kind": signal.subject_kind,
                    "subject_id": signal.subject_id,
                },
                status="degraded",
            )
            self.db.upsert_artifact(followup_artifact)
        return signal_artifact

    def _persist_signals(
        self,
        *,
        signals: list[Any],
        artifact_type: str,
        title_prefix: str,
        description: str,
        provenance: dict[str, Any],
    ) -> list[ArtifactRecord]:
        artifacts: list[ArtifactRecord] = []
        for signal in signals:
            artifacts.append(
                self._persist_observation_signal(
                    signal=signal,
                    artifact_type=artifact_type,
                    title=f"{title_prefix}: {signal.subject_id}",
                    description=description,
                    provenance=provenance,
                )
            )
        return artifacts

    def _record_audit_route_observations(
        self,
        *,
        review: Any,
        artifact_type: str,
        provenance: dict[str, Any],
    ) -> None:
        route_records = self._routes_for_audit_subject(review=review, provenance=provenance)
        if not route_records:
            return
        passed = not bool(review.findings)
        score = 0.92 if passed else 0.2
        confidence = 0.82 if passed else 0.9
        for route_record in route_records:
            route = dict(route_record.get("route") or {})
            if not route:
                continue
            variant_id = self._variant_id_for_route(route)
            task_class = str(route_record.get("task_class") or "review").strip().lower() or "review"
            self.route_observations.record(
                EvalObservation(
                    subject_kind="execution_route",
                    subject_id=variant_id,
                    variant_id=variant_id,
                    task_class=task_class,
                    score=score,
                    passed=passed,
                    confidence=confidence,
                    evidence=[
                        f"audit_artifact:{artifact_type}",
                        f"subject_kind:{review.subject_kind}",
                        f"subject_id:{review.subject_id}",
                        f"review_status:{review.status}",
                        f"findings:{len(review.findings)}",
                    ],
                    metadata={
                        "route": route,
                        "artifact_type": artifact_type,
                        "subject_kind": review.subject_kind,
                        "subject_id": review.subject_id,
                    },
                )
            )
            if passed:
                self.health_store.record_success(route)
            else:
                self.health_store.record_failure(
                    route,
                    failure_kind=f"audit:{artifact_type}",
                    error="; ".join(finding.summary for finding in review.findings[:3]) or "audit review found route issues",
                )

    def _routes_for_audit_subject(
        self,
        *,
        review: Any,
        provenance: dict[str, Any],
    ) -> list[dict[str, Any]]:
        subject_kind = str(review.subject_kind or "").strip().lower()
        if subject_kind == "verification":
            attempt_id = str(provenance.get("attempt_id") or "").strip()
            if not attempt_id:
                return []
            attempt_payload = next(
                (
                    item
                    for item in self.db.list_records("attempts")
                    if str(item.get("attempt_id") or "").strip() == attempt_id
                ),
                None,
            )
            if not attempt_payload:
                return []
            route = self._route_from_attempt_payload(attempt_payload)
            if not route:
                return []
            task_payload = next(
                (
                    item
                    for item in self.db.list_records("tasks")
                    if str(item.get("task_id") or "").strip() == str(attempt_payload.get("task_id") or "").strip()
                ),
                None,
            )
            return [{"route": route, "task_class": self._task_class_from_payload(task_payload or {})}]
        if subject_kind == "consensus_judgment":
            task_id = str(provenance.get("task_id") or review.subject_id or "").strip()
            if not task_id:
                return []
            task_payload = next(
                (
                    item
                    for item in self.db.list_records("tasks")
                    if str(item.get("task_id") or "").strip() == task_id
                ),
                None,
            )
            if not task_payload:
                return []
            consensus = dict(dict(task_payload.get("provenance") or {}).get("consensus_review") or {})
            task_class = self._task_class_from_payload(task_payload)
            routes: list[dict[str, Any]] = []
            seen: set[str] = set()
            for result in list(consensus.get("results") or []):
                route = dict(result.get("route") or {})
                variant_id = self._variant_id_for_route(route) if route else ""
                if not route or variant_id in seen:
                    continue
                seen.add(variant_id)
                routes.append({"route": route, "task_class": task_class})
            return routes
        return []

    def _route_from_attempt_payload(self, attempt_payload: dict[str, Any]) -> dict[str, Any]:
        usage = dict(attempt_payload.get("resource_usage") or {})
        implementation = dict(usage.get("implementation") or {})
        for key in ("resolved_route", "requested_route", "route"):
            route = dict(implementation.get(key) or usage.get(key) or {})
            if route:
                return route
        provenance_route = dict(dict(attempt_payload.get("provenance") or {}).get("route") or {})
        return provenance_route

    def _variant_id_for_route(self, route: dict[str, Any]) -> str:
        provider = str(route.get("provider") or "").strip().lower()
        cli_tool = str(route.get("cli_tool") or "").strip().lower()
        model = str(route.get("model") or "").strip().lower()
        if cli_tool:
            return f"cli:{cli_tool}:{model}" if model else f"cli:{cli_tool}"
        if provider == "local":
            return f"local:{model}" if model else "local:managed"
        return f"{provider}:{model}" if model else provider

    def _task_class_from_payload(self, task_payload: dict[str, Any]) -> str:
        provenance = dict(task_payload.get("provenance") or {})
        return str(task_payload.get("task_class") or provenance.get("task_class") or "review").strip().lower() or "review"

    def _materialize_review_followup_tasks(
        self,
        *,
        review: Any,
        policy: dict[str, Any],
        parent_provenance: dict[str, Any],
    ) -> list[TaskRecord]:
        specs = list(policy.get("followup_specs") or [])
        if not specs:
            return []
        materialized: list[TaskRecord] = []
        for spec in specs[:4]:
            if not isinstance(spec, dict):
                continue
            proposal = normalize_derived_task_proposal(
                title=str(spec.get("title") or "").strip(),
                description=str(spec.get("description") or "").strip(),
                parent_provenance={
                    **dict(parent_provenance),
                    "audit_followup": {
                        "mode": str(policy.get("mode") or "none"),
                        "reason": str(policy.get("reason") or ""),
                    },
                },
                suggested_completion_type=str(spec.get("completion_type") or "").strip() or None,
                priority=int(spec.get("priority") or 4),
                urgency=int(spec.get("urgency") or 1),
                risk=str(spec.get("risk") or "low"),
                success_criteria=dict(spec.get("success_criteria") or {"audit_followup_completed": True}),
                route_preferences=dict(spec.get("route_preferences") or {}),
                delta_kind=str(spec.get("delta_kind") or "").strip() or None,
                delta_summary=str(spec.get("delta_summary") or "").strip() or None,
                task_id_hint=str(spec.get("task_id_hint") or "").strip() or None,
                depends_on=list(spec.get("depends_on") or []),
                parallelizable=bool(spec.get("parallelizable")),
            )
            task = TaskRecord(
                title=proposal.title,
                description=proposal.description,
                priority=proposal.priority,
                urgency=proposal.urgency,
                risk=proposal.risk,
                dependencies=[],
                provenance=proposal.provenance,
                success_criteria=proposal.success_criteria,
                completion_policy={
                    **proposal.completion_policy,
                    "route_preferences": proposal.route_preferences,
                },
            )
            self.db.upsert_task(task)
            materialized.append(task)
        return materialized

    def _record_worker_completion_attempt(self, *, task: TaskRecord, message_payload: dict[str, Any]) -> None:
        raw_content = str(message_payload.get("raw_content") or "")
        status = str(message_payload.get("status") or "").strip().lower()
        attempt = AttemptRecord(
            task_id=task.task_id,
            actor=str(message_payload.get("worker_id") or "worker"),
            provenance={
                "source": "worker_runtime",
                "worker_id": message_payload.get("worker_id"),
                "route": message_payload.get("route"),
            },
            attempt_reason="worker delegation result reconciled by loop0",
            outcome="succeeded" if status == "applied" else ("blocked" if status == "blocked" else "failed"),
            result_summary=self._extract_principal_response(raw_content) or "Worker result reconciled.",
            failure_kind=None if status == "applied" else str(message_payload.get("detail") or "").strip() or status,
            degraded_reason=None if status == "applied" else str(message_payload.get("reason") or "").strip() or status,
            verification_status="passed" if status == "applied" else "uncertain",
            resource_usage={"route": message_payload.get("route") or {}, "implementation": {"generation_mode": "delegated_worker_result"}},
            started_at=_now_iso(),
            ended_at=_now_iso(),
        )
        self.db.upsert_attempt(attempt)

    def _assessment_priority(self, assessment: Loop0CandidateAssessment) -> tuple[int, int, int]:
        candidate = assessment.candidate
        source_bias = 1 if candidate.strategy == "message_task" else 0
        created_at = ""
        if candidate.metadata:
            created_at = str(candidate.metadata.get("created_at") or "")
        freshness = created_at or ""
        return (candidate.priority, candidate.urgency, source_bias, freshness)

    def _source_kind_for_candidate(self, candidate: Loop0TaskCandidate) -> str:
        if candidate.strategy == "message_task":
            return "message_task"
        if candidate.strategy in {"alternate_provider", "fallback_only"}:
            return "planner_remediation"
        return "planner_candidate"

    def _scheduling_metadata_for_candidate(self, candidate: Loop0TaskCandidate) -> dict[str, Any]:
        metadata = dict(candidate.metadata or {})
        preferences = self._route_preferences_for_candidate(candidate)
        scheduling: dict[str, Any] = {
            "preferred_cli_tools": list(preferences.get("preferred_cli_tools") or []),
            "preferred_providers": list(preferences.get("preferred_providers") or []),
            "expected_paths": list(candidate.expected_paths),
            "mentions_repo_file": bool(candidate.expected_paths),
        }
        if candidate.strategy in {"alternate_provider", "fallback_only"}:
            route_health_status = "broken" if candidate.strategy == "fallback_only" else "degraded"
            scheduling["route_health_status"] = route_health_status
        if metadata:
            scheduling.update(self._scheduling_metadata_for_task_payload(metadata))
        return scheduling

    def _scheduling_metadata_for_task_payload(self, task_payload: dict[str, Any]) -> dict[str, Any]:
        completion_policy = dict(task_payload.get("completion_policy") or {})
        route_preferences = dict(completion_policy.get("route_preferences") or {})
        provenance = dict(task_payload.get("provenance") or {})
        policy = infer_task_policy(task_payload)
        title = str(task_payload.get("title") or "")
        description = str(task_payload.get("description") or "")
        task_id = str(task_payload.get("task_id") or "")
        scheduling: dict[str, Any] = {
            "preferred_cli_tools": list(route_preferences.get("preferred_cli_tools") or []),
            "preferred_providers": list(route_preferences.get("preferred_providers") or []),
            "preferred_model": str(route_preferences.get("preferred_model") or ""),
            "retry_count": int(provenance.get("retry_count") or 0),
            "completion_type": str(completion_policy.get("type") or ""),
            "mentions_repo_file": self._text_mentions_repo_file(title, description),
            "historical_file_write": self._task_has_historical_file_write(task_id),
            "commentary_only_history": self._task_has_commentary_only_history(task_id),
            "task_age_hours": self._task_age_hours(task_payload),
            "is_followup": provenance.get("source") == "message_task_followup",
            "likely_satisfied": self._task_likely_satisfied(task_payload),
            "closure_pressure": self._task_closure_pressure(task_payload),
            "batchable": bool(policy.get("batchable")),
            "consensus_eligible": bool(policy.get("consensus_eligible")),
            "task_class": str(policy.get("task_class") or "general"),
            "batch_size": len(list(task_payload.get("batched_task_ids") or [])) or 1,
        }
        return scheduling

    def _text_mentions_repo_file(self, *parts: str) -> bool:
        combined = " ".join(part for part in parts if part).lower()
        if ".py" in combined or ".md" in combined or ".toml" in combined or ".json" in combined:
            return True
        return any(token in combined for token in ("file", "module", "path", "repo", "intake.py"))

    def _task_has_historical_file_write(self, task_id: str) -> bool:
        if not task_id:
            return False
        for attempt_payload in self.db.list_records("attempts"):
            if str(attempt_payload.get("task_id") or "").strip() != task_id:
                continue
            implementation = dict(dict(attempt_payload.get("resource_usage") or {}).get("implementation") or {})
            if list(implementation.get("written_paths") or []):
                return True
        return False

    def _task_has_commentary_only_history(self, task_id: str) -> bool:
        if not task_id:
            return False
        saw_attempt = False
        for attempt_payload in self.db.list_records("attempts"):
            if str(attempt_payload.get("task_id") or "").strip() != task_id:
                continue
            saw_attempt = True
            implementation = dict(dict(attempt_payload.get("resource_usage") or {}).get("implementation") or {})
            if list(implementation.get("written_paths") or []):
                return False
            if str(implementation.get("generation_mode") or "").strip() == "provider":
                continue
        return saw_attempt

    def _task_age_hours(self, task_payload: dict[str, Any]) -> float:
        timestamp = (
            str(task_payload.get("updated_at") or "").strip()
            or str(task_payload.get("created_at") or "").strip()
        )
        if not timestamp:
            return 0.0
        try:
            parsed = datetime.fromisoformat(timestamp)
        except Exception:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0)

    def _task_likely_satisfied(self, task_payload: dict[str, Any]) -> bool:
        provenance = dict(task_payload.get("provenance") or {})
        source = str(provenance.get("source") or "").strip()
        source_comm = str(provenance.get("source_communication_id") or "").strip()
        parent_task_id = str(provenance.get("parent_task_id") or "").strip()
        title = str(task_payload.get("title") or "").strip().lower()
        description = str(task_payload.get("description") or "").strip().lower()
        for other in self.db.list_records("tasks"):
            if other.get("status") != "complete":
                continue
            if str(other.get("task_id") or "") == str(task_payload.get("task_id") or ""):
                continue
            other_prov = dict(other.get("provenance") or {})
            other_source = str(other_prov.get("source") or "").strip()
            if (
                source == "message_intake"
                and source_comm
                and other_source == "message_intake"
                and str(other_prov.get("source_communication_id") or "").strip() == source_comm
            ):
                return True
            if parent_task_id and str(other_prov.get("parent_task_id") or "").strip() == parent_task_id:
                if source == "message_task_followup":
                    other_title = str(other.get("title") or "").strip().lower()
                    other_description = str(other.get("description") or "").strip().lower()
                    if title and title == other_title:
                        return True
                    if description and description == other_description:
                        return True
                    continue
                return True
            if title and title == str(other.get("title") or "").strip().lower():
                return True
            if description and description == str(other.get("description") or "").strip().lower():
                return True
        return False

    def _task_closure_pressure(self, task_payload: dict[str, Any]) -> int:
        provenance = dict(task_payload.get("provenance") or {})
        source = str(provenance.get("source") or "").strip()
        pressure = 0
        if source in EXECUTABLE_MESSAGE_TASK_SOURCES:
            pressure += 1
        age_hours = self._task_age_hours(task_payload)
        if age_hours >= 12:
            pressure += 2
        elif age_hours >= 4:
            pressure += 1
        return pressure

    def _task_goal_already_realized(self, task_payload: dict[str, Any]) -> bool:
        success_criteria = dict(task_payload.get("success_criteria") or {})
        expected_paths = [str(path).strip() for path in list(success_criteria.get("expected_paths") or []) if str(path).strip()]
        if not expected_paths:
            implementation = dict(dict(task_payload.get("provenance") or {}).get("implementation") or {})
            expected_paths = [
                str(path).strip()
                for path in list(implementation.get("written_paths") or [])
                if str(path).strip()
            ]
        if not expected_paths:
            return False
        inspection = inspect_expected_paths(self.settings.paths.project_root, expected_paths)
        return not inspection.get("missing")

    def _duplicate_group_key(self, task_payload: dict[str, Any]) -> tuple[str, str, str, str, str] | None:
        provenance = dict(task_payload.get("provenance") or {})
        source = str(provenance.get("source") or "").strip().lower()
        title = str(task_payload.get("title") or "").strip().lower()
        description = str(task_payload.get("description") or "").strip().lower()
        completion_type = str(dict(task_payload.get("completion_policy") or {}).get("type") or "").strip().lower()
        if not title and not description:
            return None
        source_comm = str(provenance.get("source_communication_id") or "").strip().lower()
        if source in {"message_intake", "message_task_followup"} and source_comm:
            return (source, source_comm, title, description, completion_type)
        if source == "loop0_runner":
            return (source, "", title, "", completion_type)
        return None

    def _is_duplicate_pending_task(self, task_payload: dict[str, Any]) -> bool:
        task_id = str(task_payload.get("task_id") or "").strip()
        group_key = self._duplicate_group_key(task_payload)
        if not task_id or group_key is None:
            return False
        peers: list[dict[str, Any]] = []
        for other in self.db.list_records("tasks"):
            if other.get("status") != "pending":
                continue
            if self._duplicate_group_key(other) != group_key:
                continue
            peers.append(other)
        if len(peers) <= 1:
            return False
        canonical = min(
            peers,
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("updated_at") or ""),
                str(item.get("task_id") or ""),
            ),
        )
        return str(canonical.get("task_id") or "").strip() != task_id

    def _pending_message_task_candidates(self) -> list[Loop0TaskCandidate]:
        tasks_by_id = self._tasks_by_id()
        raw_candidates: list[Loop0TaskCandidate] = []
        for task_payload in self.db.list_records("tasks"):
            provenance = dict(task_payload.get("provenance") or {})
            if task_payload.get("status") != "pending":
                continue
            if provenance.get("source") not in EXECUTABLE_MESSAGE_TASK_SOURCES:
                continue
            if not self._task_is_ready_for_selection(task_payload, tasks_by_id=tasks_by_id):
                continue
            if self._is_low_signal_message_task(task_payload):
                continue
            if self._is_duplicate_pending_task(task_payload):
                continue
            task_id = str(task_payload.get("task_id") or "").strip()
            if not task_id:
                continue
            raw_candidates.append(
                Loop0TaskCandidate(
                    key=f"task:{task_id}",
                    title=str(task_payload.get("title") or "Process inbound task"),
                    description=str(task_payload.get("description") or ""),
                    expected_paths=(),
                    strategy="message_task",
                    priority=int(task_payload.get("priority") or 0),
                    urgency=int(task_payload.get("urgency") or 0),
                    risk=str(task_payload.get("risk") or "low"),
                    source_task_id=task_id,
                    metadata=task_payload,
                )
            )
        return self._collapse_batchable_message_candidates(raw_candidates)

    def _collapse_batchable_message_candidates(
        self,
        candidates: list[Loop0TaskCandidate],
    ) -> list[Loop0TaskCandidate]:
        grouped: dict[tuple[str, str, str], list[Loop0TaskCandidate]] = {}
        passthrough: list[Loop0TaskCandidate] = []
        for candidate in candidates:
            metadata = dict(candidate.metadata or {})
            policy = infer_task_policy(metadata)
            if not policy.get("batchable"):
                passthrough.append(candidate)
                continue
            provenance = dict(metadata.get("provenance") or {})
            lane_sender, _conversation_id = self._message_task_lane_context(metadata)
            batch_key = (
                lane_sender,
                str(policy.get("task_class") or "general"),
                str(dict(metadata.get("completion_policy") or {}).get("type") or "").strip().lower(),
            )
            grouped.setdefault(batch_key, []).append(candidate)
        collapsed: list[Loop0TaskCandidate] = list(passthrough)
        for batch in grouped.values():
            ordered = sorted(
                batch,
                key=lambda item: (
                    str(dict(item.metadata or {}).get("created_at") or ""),
                    str(item.source_task_id or ""),
                ),
            )
            if len(ordered) < 2:
                collapsed.extend(ordered)
                continue
            carrier = ordered[0]
            batched_payloads = [dict(item.metadata or {}) for item in ordered[:4]]
            batched_task_ids = [
                str(payload.get("task_id") or "").strip()
                for payload in batched_payloads
                if str(payload.get("task_id") or "").strip()
            ]
            if len(batched_task_ids) < 2:
                collapsed.extend(ordered)
                continue
            carrier_payload = {
                **dict(carrier.metadata or {}),
                "provenance": {
                    **dict(dict(carrier.metadata or {}).get("provenance") or {}),
                    "batching": {
                        "batched_task_ids": batched_task_ids,
                    },
                },
                "batched_task_payloads": batched_payloads,
            }
            collapsed.append(
                replace(
                    carrier,
                    title=f"Batch: {carrier.title}",
                    description=(
                        f"Handle {len(batched_task_ids)} low-priority related tasks as one bounded batch. "
                        + " | ".join(
                            str(payload.get("description") or "").strip()
                            for payload in batched_payloads[:3]
                            if str(payload.get("description") or "").strip()
                        )
                    )[:400],
                    metadata=carrier_payload,
                )
            )
        return collapsed

    def _retry_task_candidates(self) -> list[Loop0TaskCandidate]:
        tasks_by_id = self._tasks_by_id()
        if not tasks_by_id:
            return []
        latest_attempt_by_task: dict[str, dict[str, Any]] = {}
        for attempt_payload in self.db.list_records("attempts"):
            task_id = str(attempt_payload.get("task_id") or "").strip()
            if not task_id:
                continue
            existing = latest_attempt_by_task.get(task_id)
            if existing is None or str(attempt_payload.get("ended_at") or "") > str(existing.get("ended_at") or ""):
                latest_attempt_by_task[task_id] = attempt_payload
        candidates: list[Loop0TaskCandidate] = []
        for task_id, attempt_payload in latest_attempt_by_task.items():
            task_payload = dict(tasks_by_id.get(task_id) or {})
            if not task_payload:
                continue
            if task_payload.get("status") not in {"blocked", "failed"}:
                continue
            if not self._task_is_ready_for_selection(task_payload, tasks_by_id=tasks_by_id):
                continue
            outcome = str(attempt_payload.get("outcome") or "").strip().lower()
            if outcome not in {"blocked", "failed"}:
                continue
            synthetic_task = dict(task_payload)
            synthetic_task["status"] = "pending"
            synthetic_task["updated_at"] = str(attempt_payload.get("ended_at") or synthetic_task.get("updated_at") or "")
            retry_count = 1 + sum(
                1
                for payload in self.db.list_records("attempts")
                if str(payload.get("task_id") or "").strip() == task_id
            )
            synthetic_task["provenance"] = {
                **dict(task_payload.get("provenance") or {}),
                "retry_of_attempt_id": attempt_payload.get("attempt_id"),
                "retry_reason": attempt_payload.get("degraded_reason") or attempt_payload.get("failure_kind"),
                "retry_count": retry_count,
            }
            candidates.append(
                Loop0TaskCandidate(
                    key=f"retry:{task_id}",
                    title=f"Retry: {synthetic_task.get('title') or task_id}",
                    description=str(synthetic_task.get("description") or ""),
                    expected_paths=(),
                    strategy="message_task",
                    priority=min(10, int(synthetic_task.get("priority") or 0) + 1),
                    urgency=int(synthetic_task.get("urgency") or 0),
                    risk=str(synthetic_task.get("risk") or "low"),
                    source_task_id=task_id,
                    metadata=synthetic_task,
                )
            )
        return candidates

    def _artifact_finding_candidates(self) -> list[Loop0TaskCandidate]:
        tasks_by_id = self._tasks_by_id()
        pending_followups = {
            (
                str(dict(task_payload.get("provenance") or {}).get("parent_task_id") or ""),
                str(task_payload.get("description") or "").strip().lower(),
            )
            for task_payload in self.db.list_records("tasks")
            if task_payload.get("status") == "pending"
        }
        candidates: list[Loop0TaskCandidate] = []
        for artifact_payload in self.db.list_records("artifacts"):
            artifact_type = str(artifact_payload.get("artifact_type") or "").strip()
            if artifact_type not in {"spec_review", "review_report", "message_analysis"}:
                continue
            parsed = _try_parse_json(str(artifact_payload.get("content_summary") or "")) or {}
            confidence = float(parsed.get("confidence") or 0.0)
            findings = list(parsed.get("findings") or [])
            if confidence < 0.75 or not findings:
                continue
            parent_task_id = str(dict(artifact_payload.get("provenance") or {}).get("task_id") or "")
            for finding in findings[:2]:
                description = (
                    str(finding.get("description") or "").strip()
                    if isinstance(finding, dict)
                    else str(finding).strip()
                )
                if not description:
                    continue
                if (parent_task_id, description.lower()) in pending_followups:
                    continue
                title = (
                    str(finding.get("title") or "").strip()
                    if isinstance(finding, dict)
                    else self._title_from_finding(description, Loop0TaskCandidate(
                        key=f"artifact:{artifact_payload.get('artifact_id')}",
                        title=str(artifact_payload.get("title") or "Artifact finding"),
                        description=description,
                        expected_paths=(),
                    ))
                )
                synthetic_task = {
                    "task_id": f"artifact-finding-{artifact_payload.get('artifact_id')}",
                    "parent_task_id": parent_task_id or None,
                    "title": title or f"Artifact finding: {artifact_payload.get('title') or 'review'}",
                    "description": description,
                    "priority": 5,
                    "urgency": 3,
                    "risk": "low",
                    "status": "pending",
                    "provenance": {
                        "source": "artifact_finding",
                        "source_artifact_id": artifact_payload.get("artifact_id"),
                        "parent_task_id": parent_task_id,
                    },
                    "success_criteria": {"artifact_finding_addressed": True},
                    "completion_policy": {"type": "respond_or_execute"},
                    "created_at": str(artifact_payload.get("created_at") or ""),
                    "updated_at": str(artifact_payload.get("updated_at") or ""),
                    "artifact_confidence": confidence,
                }
                if not self._task_is_ready_for_selection(synthetic_task, tasks_by_id=tasks_by_id):
                    continue
                candidates.append(
                    Loop0TaskCandidate(
                        key=f"artifact:{artifact_payload.get('artifact_id')}:{len(candidates)}",
                        title=str(synthetic_task["title"]),
                        description=description,
                        expected_paths=(),
                        strategy="message_task",
                        priority=int(synthetic_task["priority"]),
                        urgency=int(synthetic_task["urgency"]),
                        risk=str(synthetic_task["risk"]),
                        source_task_id=str(synthetic_task["task_id"]),
                        metadata=synthetic_task,
                    )
                )
        return candidates

    def _alignment_maintenance_candidates(self) -> list[Loop0TaskCandidate]:
        tasks_by_id = self._tasks_by_id()
        pending_subjects = self._pending_alignment_subject_keys(tasks_by_id=tasks_by_id)
        candidates: list[Loop0TaskCandidate] = []
        for artifact_payload in self.db.list_records("artifacts"):
            candidate = self._alignment_candidate_from_artifact(
                artifact_payload,
                tasks_by_id=tasks_by_id,
                pending_subjects=pending_subjects,
            )
            if candidate is None:
                continue
            candidates.append(candidate)
            metadata = dict(candidate.metadata or {})
            subject_key = self._alignment_subject_key_from_payload(metadata)
            if subject_key is not None:
                pending_subjects.add(subject_key)
        return candidates

    def _alignment_candidate_from_artifact(
        self,
        artifact_payload: dict[str, Any],
        *,
        tasks_by_id: dict[str, dict[str, Any]],
        pending_subjects: set[tuple[str, ...]],
    ) -> Loop0TaskCandidate | None:
        artifact_type = str(artifact_payload.get("artifact_type") or "").strip()
        if artifact_type in {"loop0_review_signal", "loop0_inference_signal"}:
            return self._alignment_candidate_from_signal_artifact(
                artifact_payload,
                tasks_by_id=tasks_by_id,
                pending_subjects=pending_subjects,
            )
        if artifact_type == "governance_drift_alert":
            return self._alignment_candidate_from_governance_drift_artifact(
                artifact_payload,
                tasks_by_id=tasks_by_id,
                pending_subjects=pending_subjects,
            )
        return None

    def _alignment_candidate_from_signal_artifact(
        self,
        artifact_payload: dict[str, Any],
        *,
        tasks_by_id: dict[str, dict[str, Any]],
        pending_subjects: set[tuple[str, ...]],
    ) -> Loop0TaskCandidate | None:
        parsed = _try_parse_json(str(artifact_payload.get("content_summary") or "")) or {}
        if not parsed:
            return None
        signal = ObservationSignal.model_validate(parsed)
        if signal.status != "open":
            return None
        subject_key = ("subject", str(signal.subject_kind), str(signal.subject_id))
        if subject_key in pending_subjects:
            return None
        policy = select_signal_followup_policy(signal=signal)
        specs = [spec for spec in list(policy.get("followup_specs") or []) if isinstance(spec, dict)]
        if not specs:
            return None
        spec = specs[0]
        task_payload = {
            "task_id": f"alignment-maintenance-{artifact_payload.get('artifact_id')}",
            "title": str(spec.get("title") or f"Alignment maintenance: {signal.subject_id}"),
            "description": str(spec.get("description") or signal.summary),
            "priority": int(spec.get("priority") or 6),
            "urgency": int(spec.get("urgency") or 3),
            "risk": str(spec.get("risk") or "moderate"),
            "status": "pending",
            "provenance": {
                "source": "alignment_maintenance",
                "maintenance_kind": "signal_followup",
                "source_artifact_id": artifact_payload.get("artifact_id"),
                "signal_id": signal.signal_id,
                "signal_kind": signal.signal_kind,
                "subject_kind": signal.subject_kind,
                "subject_id": signal.subject_id,
            },
            "success_criteria": dict(spec.get("success_criteria") or {"signal_addressed": True}),
            "completion_policy": {
                "type": str(spec.get("completion_type") or "review_or_audit"),
                "route_preferences": dict(spec.get("route_preferences") or {}),
            },
            "created_at": str(artifact_payload.get("created_at") or ""),
            "updated_at": str(artifact_payload.get("updated_at") or ""),
        }
        if not self._task_is_ready_for_selection(task_payload, tasks_by_id=tasks_by_id):
            return None
        return Loop0TaskCandidate(
            key=f"alignment:{artifact_payload.get('artifact_id')}",
            title=str(task_payload["title"]),
            description=str(task_payload["description"]),
            expected_paths=(),
            strategy="message_task",
            priority=int(task_payload["priority"]),
            urgency=int(task_payload["urgency"]),
            risk=str(task_payload["risk"]),
            source_task_id=str(task_payload["task_id"]),
            metadata=task_payload,
        )

    def _alignment_candidate_from_governance_drift_artifact(
        self,
        artifact_payload: dict[str, Any],
        *,
        tasks_by_id: dict[str, dict[str, Any]],
        pending_subjects: set[tuple[str, ...]],
    ) -> Loop0TaskCandidate | None:
        subject_key = ("governance_drift", "protected_governance")
        if subject_key in pending_subjects:
            return None
        parsed = _try_parse_json(str(artifact_payload.get("content_summary") or "")) or {}
        drifted_paths = [
            str(path).strip()
            for path in list(parsed.get("newly_reported_paths") or parsed.get("drifted_paths") or [])
            if str(path).strip()
        ]
        if not drifted_paths:
            return None
        task_payload = {
            "task_id": f"alignment-maintenance-{artifact_payload.get('artifact_id')}",
            "title": "Correct detected system drift: protected_governance",
            "description": (
                "Investigate protected governance drift and either repair it or explicitly ratify it. "
                f"Impacted paths: {', '.join(drifted_paths[:5])}"
            ),
            "priority": 9,
            "urgency": 9,
            "risk": "high",
            "status": "pending",
            "provenance": {
                "source": "alignment_maintenance",
                "maintenance_kind": "governance_drift",
                "source_artifact_id": artifact_payload.get("artifact_id"),
                "subject_kind": "governance_drift",
                "subject_id": "protected_governance",
                "drifted_paths": drifted_paths,
            },
            "success_criteria": {"governance_drift_addressed": True},
            "completion_policy": {
                "type": "review_or_audit",
                "route_preferences": {"preferred_cli_tools": ["kilocode", "gemini-cli"]},
            },
            "created_at": str(artifact_payload.get("created_at") or ""),
            "updated_at": str(artifact_payload.get("updated_at") or ""),
        }
        if not self._task_is_ready_for_selection(task_payload, tasks_by_id=tasks_by_id):
            return None
        return Loop0TaskCandidate(
            key=f"alignment:{artifact_payload.get('artifact_id')}",
            title=str(task_payload["title"]),
            description=str(task_payload["description"]),
            expected_paths=(),
            strategy="message_task",
            priority=int(task_payload["priority"]),
            urgency=int(task_payload["urgency"]),
            risk=str(task_payload["risk"]),
            source_task_id=str(task_payload["task_id"]),
            metadata=task_payload,
        )

    def _pending_alignment_subject_keys(
        self,
        *,
        tasks_by_id: dict[str, dict[str, Any]],
    ) -> set[tuple[str, ...]]:
        subjects: set[tuple[str, ...]] = set()
        for task_payload in tasks_by_id.values():
            if str(task_payload.get("status") or "").strip().lower() not in {"pending", "working", "blocked"}:
                continue
            subject_key = self._alignment_subject_key_from_payload(task_payload)
            if subject_key is not None:
                subjects.add(subject_key)
        return subjects

    def _alignment_subject_key_from_payload(self, task_payload: dict[str, Any]) -> tuple[str, ...] | None:
        provenance = dict(task_payload.get("provenance") or {})
        source = str(provenance.get("source") or "").strip().lower()
        subject_kind = str(provenance.get("subject_kind") or "").strip()
        subject_id = str(provenance.get("subject_id") or "").strip()
        if source in {"observation_signal", "alignment_maintenance"} and subject_kind and subject_id:
            if subject_kind == "governance_drift":
                return ("governance_drift", subject_id)
            return ("subject", subject_kind, subject_id)
        if source == "audit_followup" and subject_kind and subject_id:
            return ("subject", subject_kind, subject_id)
        return None

    def _tasks_by_id(self) -> dict[str, dict[str, Any]]:
        return {
            str(task_payload.get("task_id") or "").strip(): task_payload
            for task_payload in self.db.list_records("tasks")
            if str(task_payload.get("task_id") or "").strip()
        }

    def _task_is_ready_for_selection(
        self,
        task_payload: dict[str, Any],
        *,
        tasks_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> bool:
        tasks = tasks_by_id or self._tasks_by_id()
        if self._task_has_unresolved_dependencies(task_payload, tasks_by_id=tasks):
            return False
        if self._task_has_active_children(task_payload, tasks_by_id=tasks):
            return False
        return True

    def _task_has_unresolved_dependencies(
        self,
        task_payload: dict[str, Any],
        *,
        tasks_by_id: dict[str, dict[str, Any]],
    ) -> bool:
        for dependency_id in list(task_payload.get("dependencies") or []):
            dependency = tasks_by_id.get(str(dependency_id).strip())
            if not dependency:
                continue
            if str(dependency.get("status") or "").strip().lower() not in {"complete", "satisfied"}:
                return True
        return False

    def _task_has_active_children(
        self,
        task_payload: dict[str, Any],
        *,
        tasks_by_id: dict[str, dict[str, Any]],
    ) -> bool:
        for child_id in list(task_payload.get("active_child_ids") or []):
            child = tasks_by_id.get(str(child_id).strip())
            if not child:
                continue
            if str(child.get("status") or "").strip().lower() in {"pending", "working", "blocked"}:
                return True
        return False

    def _is_low_signal_message_task(self, task_payload: dict[str, Any]) -> bool:
        title = str(task_payload.get("title") or "").strip().lower()
        description = str(task_payload.get("description") or "").strip().lower()
        parts = [part for part in (title, description) if part]
        if not parts:
            return True
        low_signal_phrases = {
            "hello",
            "hello from principal",
            "hello from operator",
            "hi",
            "hey",
            "yo",
        }
        normalized_parts = {part.strip() for part in parts}
        if normalized_parts and normalized_parts.issubset(low_signal_phrases):
            return True
        combined = " ".join(parts).strip()
        if combined in low_signal_phrases:
            return True
        return max(len(part.split()) for part in parts) <= 2

    def _candidate_pool(self) -> list[Loop0TaskCandidate]:
        candidates: list[Loop0TaskCandidate] = list(SEED_LOOP0_CANDIDATES)
        route_health_store = self.procedures._health_store
        route_health = dict(route_health_store._load().get("routes") or {})
        recent_attempts = self.db.list_records("attempts")
        available_providers = self.registry.configured_provider_names()
        for snapshot in self.planner.derive_remediation_candidates(
            project_root=self.settings.paths.project_root,
            attempts=recent_attempts,
            route_health=route_health,
            available_providers=available_providers,
        ):
            remediation_candidate = self._candidate_from_snapshot(snapshot)
            if any(existing.key == remediation_candidate.key and existing.strategy == remediation_candidate.strategy for existing in candidates):
                continue
            candidates.insert(0, remediation_candidate)
        phase0_doc = self.governance.planning_docs.get("phase_0_plan")
        if not phase0_doc:
            return candidates
        for snapshot in self.planner.derive_missing_candidates(
            self.settings.paths.project_root,
            phase0_doc.content,
        ):
            dynamic_candidate = self._candidate_from_snapshot(snapshot)
            if any(existing.key == dynamic_candidate.key for existing in candidates):
                continue
            candidates.append(dynamic_candidate)
        for snapshot in self.planner.derive_weak_candidates(
            self.settings.paths.project_root,
            phase0_doc.content,
        ):
            dynamic_candidate = self._candidate_from_snapshot(snapshot)
            if any(existing.key == dynamic_candidate.key for existing in candidates):
                continue
            candidates.append(dynamic_candidate)
        return candidates

    def _candidate_from_snapshot(self, snapshot: PlannerSnapshot) -> Loop0TaskCandidate:
        primary_path = snapshot.expected_paths[-1]
        return Loop0TaskCandidate(
            key=snapshot.candidate_key,
            title=self._title_for_path(primary_path),
            description=snapshot.reason or f"Add the missing module `{primary_path}` from the Phase 0 plan.",
            expected_paths=tuple(snapshot.expected_paths),
            strategy=snapshot.strategy,
            metadata=dict(snapshot.metadata),
        )

    def _title_for_path(self, rel_path: str) -> str:
        stem = Path(rel_path).stem.replace("_", " ")
        package = Path(rel_path).parent.name.replace("_", " ")
        return f"Create {package} {stem} module"

    def recommend_next_step(self, assessment: Loop0CandidateAssessment) -> dict[str, Any]:
        candidate = assessment.candidate
        coordination = self.coordinate_candidate(candidate)
        route = coordination["route"]
        if coordination["decision"]["status"] != "accepted":
            return {
                "selection_mode": "coordinator",
                "route": route,
                "recommended_key": candidate.key,
                "recommended_title": candidate.title,
                "reasoning": coordination["decision"]["reason"],
                "coordination": coordination,
            }
        provider = self.registry.get_provider(route.get("provider"))
        heuristic = {
            "selection_mode": "heuristic",
            "route": route,
            "recommended_key": candidate.key,
            "recommended_title": candidate.title,
            "reasoning": (
                "Selected pending inbound task from the unified work queue."
                if candidate.strategy == "message_task"
                else "First missing expected path in Phase 0 checklist."
            ),
            "coordination": coordination,
        }
        if candidate.strategy == "message_task":
            return heuristic
        if not provider:
            return heuristic
        try:
            request = build_memory_augmented_request(
                model=route.get("model"),
                messages=[
                    Message(
                        role="system",
                        content=(
                            "You are helping Loop 0 choose the next bounded implementation slice. "
                            "Return strict JSON with keys: reasoning, recommended_key, recommended_title."
                        ),
                    ),
                    Message(
                        role="user",
                        content=json.dumps(
                            {
                                "candidate": candidate.__dict__,
                                "inspection": assessment.inspection,
                                "available_docs": list(self.governance.planning_docs.keys()),
                            },
                            indent=2,
                        ),
                    ),
                ],
                metadata={"cli_tool": route.get("cli_tool")},
                memory_store_path=default_memory_store_path(data_dir=self.settings.paths.data_dir),
                memory_query=" ".join([candidate.title, candidate.description]),
                accessor="local",
                destination="remote",
            )
            response = provider.complete(request)
            parsed = _try_parse_json(response.content)
            if isinstance(parsed, dict):
                parsed["selection_mode"] = "provider"
                parsed["route"] = route
                parsed["coordination"] = coordination
                return parsed
        except Exception as exc:
            heuristic["provider_error"] = str(exc)
        return heuristic

    def coordinate_candidate(self, candidate: Loop0TaskCandidate) -> dict[str, Any]:
        envelope = ControllerEnvelope(
            controller_id="prime",
            task_id=candidate.key,
            priority=candidate.priority,
            urgency=candidate.urgency,
            risk=candidate.risk,
            metadata={
                "candidate_key": candidate.key,
                "strategy": candidate.strategy,
                "task_class": self._task_class_for_candidate(candidate),
                **self._route_preferences_for_candidate(candidate),
            },
        )
        source_decision, route = self.coordinator.coordinate(envelope)
        handoff: HandoffRecord | None = None
        final_decision = source_decision
        if source_decision.status == "accepted":
            handoff = self.handoff_lane.open_handoff(
                source_controller="prime",
                target_controller="local_executor",
                task_id=candidate.key,
                envelope=envelope.model_dump(mode="json"),
                route=route.__dict__,
                source_decision=source_decision.model_dump(mode="json"),
                metadata={
                    "candidate_key": candidate.key,
                    "strategy": candidate.strategy,
                },
            )
            downstream_decision = self.local_executor.evaluate_handoff(handoff)
            handoff = self.handoff_lane.respond(
                handoff,
                status=downstream_decision.status,
                reason=downstream_decision.reason,
                target_decision=downstream_decision.model_dump(mode="json"),
            )
            final_decision = downstream_decision
        return {
            "controller": "prime",
            "decision": final_decision.model_dump(mode="json"),
            "source_decision": source_decision.model_dump(mode="json"),
            "target_decision": (
                handoff.target_decision
                if handoff is not None and handoff.target_decision
                else {}
            ),
            "handoff": handoff.model_dump(mode="json") if handoff is not None else None,
            "route": route.__dict__,
        }

    def _route_preferences_for_candidate(self, candidate: Loop0TaskCandidate) -> dict[str, Any]:
        if candidate.strategy != "message_task":
            return {}
        completion_policy = dict(dict(candidate.metadata or {}).get("completion_policy") or {})
        route_preferences = dict(completion_policy.get("route_preferences") or {})
        preferred_cli_tools = list(route_preferences.get("preferred_cli_tools") or [])
        if not preferred_cli_tools:
            preferred_cli_tools = ["kilocode", "gemini-cli", "claude-code"]
        preferences = {
            "preferred_cli_tools": preferred_cli_tools,
            "preferred_providers": list(route_preferences.get("preferred_providers") or []),
            "avoided_providers": list(route_preferences.get("avoided_providers") or []),
            "avoided_cli_tools": list(route_preferences.get("avoided_cli_tools") or []),
            "preferred_model": str(route_preferences.get("preferred_model") or "").strip() or None,
        }
        if candidate.risk not in {"high", "critical"}:
            preferences["avoided_providers"] = [
                *preferences["avoided_providers"],
                "codex",
            ]
        return preferences

    def _protected_governance_block(
        self,
        *,
        candidate: Loop0TaskCandidate,
        protected_paths: list[str],
        route: dict[str, Any],
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason": "Protected governance surfaces require direct principal-governed provenance before Astrata may edit them.",
            "written_paths": [],
            "generation_mode": "none",
            "requested_route": route,
            "resolved_route": {},
            "preflight": {"ok": False, "reason": "protected_governance_surface"},
            "failure_kind": "protected_governance_surface",
            "degraded_reason": "policy:protected_governance_surface",
            "provider_error": None,
            "attempt_count": 0,
            "baseline_inspection": {
                "candidate_key": candidate.key,
                "protected_paths": protected_paths,
                "provenance": dict(provenance or {}),
            },
        }

    def _ensure_governance_write_allowed(
        self,
        *,
        candidate: Loop0TaskCandidate,
        candidate_paths: list[str],
        provenance: dict[str, Any] | None,
        route: dict[str, Any],
    ) -> dict[str, Any] | None:
        protected_paths = protected_governance_paths(candidate_paths)
        if not protected_paths:
            return None
        if governance_change_is_authorized(provenance):
            return None
        return self._protected_governance_block(
            candidate=candidate,
            protected_paths=protected_paths,
            route=route,
            provenance=provenance,
        )

    def _task_class_for_candidate(self, candidate: Loop0TaskCandidate) -> str:
        metadata = dict(candidate.metadata or {})
        provenance = dict(metadata.get("provenance") or {})
        explicit_task_class = str(metadata.get("task_class") or provenance.get("task_class") or "").strip().lower()
        if explicit_task_class:
            return explicit_task_class
        domains = [str(item).strip().lower() for item in list(metadata.get("domains") or []) if str(item).strip()]
        request_kind = str(metadata.get("derived_request_kind") or metadata.get("request_kind") or "").strip().lower()
        if "implementation" in domains or "tasking" in domains:
            return "coding"
        if candidate.strategy in {"strengthen", "retry"}:
            return "coding"
        if request_kind == "execution":
            return "coding"
        if request_kind in {"review", "spec_hardening"}:
            return "review"
        return "general"

    def run_once(self) -> dict[str, Any]:
        self._record_governance_drift_if_any()
        assessment = self.next_candidate_assessment()
        if assessment is None:
            self.principal_lane.send(
                sender="astrata.loop0",
                kind="notice",
                intent="loop0_status",
                payload={"status": "complete", "message": "No missing or weak Loop 0 candidate paths found."},
            )
            return {"status": "complete", "message": "No missing Loop 0 candidate paths found."}
        return self._execute_assessment(assessment)

    def _record_governance_drift_if_any(self) -> dict[str, Any] | None:
        drift = self.governance_drift_monitor.scan(self.settings.paths.project_root)
        if drift.get("status") != "drifted":
            return drift
        newly_reported_paths = list(drift.get("newly_reported_paths") or [])
        if not newly_reported_paths:
            return drift
        artifact = ArtifactRecord(
            artifact_type="governance_drift_alert",
            title="Protected governance drift detected",
            description="Protected governance surfaces changed without approved principal provenance.",
            content_summary=json.dumps(drift, indent=2),
            provenance={"source": "governance_drift_monitor"},
            status="broken",
        )
        self.db.upsert_artifact(artifact)
        self.principal_lane.send(
            sender="astrata.loop0",
            kind="notice",
            intent="governance_drift_alert",
            payload={
                "status": "drifted",
                "drifted_paths": drift.get("drifted_paths") or [],
                "newly_reported_paths": newly_reported_paths,
                "artifact_id": artifact.artifact_id,
                "message": "Protected governance surfaces changed without approved principal provenance.",
            },
            priority=9,
            urgency=9,
        )
        return drift

    def _approve_governance_baseline_if_authorized(
        self,
        *,
        candidate: Loop0TaskCandidate,
        implementation: dict[str, Any],
    ) -> None:
        if implementation.get("status") != "applied":
            return
        if not protected_governance_paths(list(candidate.expected_paths)):
            return
        provenance = dict(candidate.metadata or {}).get("provenance") or {}
        if not governance_change_is_authorized(dict(provenance)):
            return
        self.governance_drift_monitor.approve_current(self.settings.paths.project_root)

    def _execute_assessment(self, assessment: Loop0CandidateAssessment) -> dict[str, Any]:
        candidate = assessment.candidate
        recommendation = self.recommend_next_step(assessment)
        coordination = recommendation.get("coordination") or self.coordinate_candidate(candidate)
        implementation = self._apply_candidate(candidate, coordination=coordination)
        self._approve_governance_baseline_if_authorized(
            candidate=candidate,
            implementation=implementation,
        )
        verification_preview = self._verification_result(candidate, implementation)
        task = self._task_record_for_candidate(
            candidate=candidate,
            assessment=assessment,
            implementation=implementation,
            verification_preview=verification_preview,
            recommendation=recommendation,
            coordination=coordination,
        )
        self.db.upsert_task(task)
        self._sync_batched_peer_tasks(task)
        followup_tasks = self._materialize_followup_tasks(
            candidate=candidate,
            parent_task=task,
            implementation=implementation,
        )

        route = recommendation.get("route") or {}
        verification = self._verify_candidate(candidate, task.task_id, implementation)
        started_at = _now_iso()
        outcome = (
            "succeeded"
            if implementation["status"] == "applied"
            else (
                "running"
                if implementation["status"] == "delegated"
                else ("blocked" if implementation.get("degraded_reason") else "failed")
            )
        )
        attempt = AttemptRecord(
            task_id=task.task_id,
            actor=f"loop0:{route.get('provider') or 'heuristic'}",
            provenance={
                "source": "loop0_runner",
                "candidate_key": candidate.key,
                "inspection": assessment.inspection,
                "implementation": implementation,
                "coordination": coordination,
            },
            attempt_reason=str(recommendation.get("reasoning") or "loop0 next-step recommendation"),
            outcome=outcome,
            result_summary=(
                f"Applied candidate {candidate.key}"
                if implementation["status"] == "applied"
                else (
                    f"Delegated candidate {candidate.key} to a durable worker"
                    if implementation["status"] == "delegated"
                    else f"Could not apply candidate {candidate.key}"
                )
            ),
            failure_kind=implementation.get("failure_kind"),
            degraded_reason=implementation.get("degraded_reason") or implementation.get("provider_error"),
            verification_status=_verification_status_for(verification.result),
            resource_usage={"route": route, "implementation": implementation},
            followup_actions=[
                {
                    "type": "proposed_implementation_target",
                    "candidate_key": candidate.key,
                    "expected_paths": list(candidate.expected_paths),
                },
                {
                    "type": "implementation_attempt",
                    "status": implementation["status"],
                    "written_paths": implementation.get("written_paths", []),
                },
                {
                    "type": "coordination_decision",
                    "decision": coordination.get("decision"),
                },
                *[
                    {
                        "type": "created_followup_task",
                        "task_id": followup.task_id,
                        "title": followup.title,
                    }
                    for followup in followup_tasks
                ],
            ],
            started_at=started_at,
            ended_at=None if outcome == "running" else _now_iso(),
        )
        self.db.upsert_attempt(attempt)

        artifact = ArtifactRecord(
            artifact_type="loop0_recommendation",
            title=f"Loop 0 recommendation: {candidate.title}",
            description=candidate.description,
            content_summary=json.dumps(recommendation, indent=2),
            provenance={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
        )
        self.db.upsert_artifact(artifact)

        coordination_report = ArtifactRecord(
            artifact_type="loop0_coordination_report",
            title=f"Loop 0 coordination report: {candidate.title}",
            description="Coordinator decision for whether Prime should spend inference on this step.",
            content_summary=json.dumps(coordination, indent=2),
            provenance={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
            status="good" if coordination.get("decision", {}).get("status") == "accepted" else "degraded",
        )
        self.db.upsert_artifact(coordination_report)

        handoff = coordination.get("handoff")
        handoff_report = None
        if handoff:
            handoff_report = ArtifactRecord(
                artifact_type="loop0_handoff_report",
                title=f"Loop 0 handoff report: {candidate.title}",
                description="Downstream handoff state between Prime and the local executor.",
                content_summary=json.dumps(handoff, indent=2),
                provenance={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
                status="good" if handoff.get("status") == "accepted" else "degraded",
            )
            self.db.upsert_artifact(handoff_report)

        gap_report = ArtifactRecord(
            artifact_type="loop0_gap_report",
            title=f"Loop 0 gap report: {candidate.title}",
            description="Current repository evidence for the recommended Loop 0 slice.",
            content_summary=json.dumps(assessment.inspection, indent=2),
            provenance={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
        )
        self.db.upsert_artifact(gap_report)

        implementation_report = ArtifactRecord(
            artifact_type="loop0_implementation_report",
            title=f"Loop 0 implementation report: {candidate.title}",
            description="Bounded execution result for the selected Loop 0 slice.",
            content_summary=json.dumps(implementation, indent=2),
            provenance={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
            status="good" if implementation["status"] == "applied" else "degraded",
        )
        self.db.upsert_artifact(implementation_report)

        message_artifact = None
        artifact_payload = implementation.get("derived_artifact")
        if isinstance(artifact_payload, dict) and artifact_payload.get("artifact_type"):
            message_artifact = ArtifactRecord(
                artifact_type=str(artifact_payload.get("artifact_type") or "message_analysis"),
                title=str(artifact_payload.get("title") or candidate.title),
                description=str(artifact_payload.get("description") or ""),
                content_summary=json.dumps(artifact_payload, indent=2),
                provenance={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
                status=str(artifact_payload.get("status") or "good"),
            )
            self.db.upsert_artifact(message_artifact)

        followup_report = None
        if followup_tasks:
            followup_report = ArtifactRecord(
                artifact_type="loop0_followup_task_bundle",
                title=f"Loop 0 follow-up tasks: {candidate.title}",
                description="Derived governed work emitted by a completed message task.",
                content_summary=json.dumps(
                    [followup.model_dump(mode="json") for followup in followup_tasks],
                    indent=2,
                ),
                provenance={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
            )
            self.db.upsert_artifact(followup_report)

        verification_review = review_verification(
            project_root=self.settings.paths.project_root,
            candidate_key=candidate.key,
            expected_paths=list(candidate.expected_paths),
            implementation=implementation,
            verification=verification,
            attempt=attempt.model_dump(mode="json"),
            task_payload=task.model_dump(mode="json"),
        )
        review_artifact, verification_meta_review_artifact = self._persist_audit_review(
            review=verification_review,
            artifact_type="loop0_verification_review",
            title=f"Loop 0 verification review: {candidate.title}",
            description="Second-pass audit of whether verification matched observed repository state.",
            provenance={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
        )
        self._persist_signals(
            signals=signals_from_review(verification_review),
            artifact_type="loop0_review_signal",
            title_prefix="Loop 0 review signal",
            description="Durable internal observation signal derived from review findings.",
            provenance={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
        )

        inference_telemetry = summarize_inference_activity(
            attempts=self.db.list_records("attempts"),
            tasks=self.db.list_records("tasks"),
            quota_snapshots=[self._quota_snapshot_for_route(route)] if route else [],
        )
        telemetry_artifact = ArtifactRecord(
            artifact_type="loop0_inference_telemetry",
            title=f"Loop 0 inference telemetry: {candidate.title}",
            description="Resource-awareness summary for recent inference activity and quota pressure.",
            content_summary=json.dumps(inference_telemetry, indent=2),
            provenance={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
            status="good",
        )
        self.db.upsert_artifact(telemetry_artifact)
        self._persist_signals(
            signals=signals_from_inference_telemetry(inference_telemetry),
            artifact_type="loop0_inference_signal",
            title_prefix="Loop 0 inference signal",
            description="Durable internal observation signal derived from inference telemetry.",
            provenance={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
        )

        self.db.upsert_verification(verification)

        principal_message = self.principal_lane.send(
            sender="astrata.loop0",
            kind="status",
            intent="loop0_result",
            payload={
                "candidate_key": candidate.key,
                "candidate_title": candidate.title,
                "strategy": candidate.strategy,
                "route": route,
                "coordination_status": coordination.get("decision", {}).get("status"),
                "implementation_status": implementation.get("status"),
                "verification_result": verification.result,
                "summary": attempt.result_summary,
            },
            priority=candidate.priority,
            urgency=candidate.urgency,
            related_task_ids=[task.task_id],
            related_attempt_ids=[attempt.attempt_id],
        )

        return {
            "status": "ok",
            "task": task.model_dump(mode="json"),
            "attempt": attempt.model_dump(mode="json"),
            "artifact": artifact.model_dump(mode="json"),
            "coordination_report": coordination_report.model_dump(mode="json"),
            "handoff_report": None if handoff_report is None else handoff_report.model_dump(mode="json"),
            "gap_report": gap_report.model_dump(mode="json"),
            "implementation_report": implementation_report.model_dump(mode="json"),
            "message_artifact": None if message_artifact is None else message_artifact.model_dump(mode="json"),
            "followup_report": None if followup_report is None else followup_report.model_dump(mode="json"),
            "followup_tasks": [followup.model_dump(mode="json") for followup in followup_tasks],
            "verification_review": review_artifact.model_dump(mode="json"),
            "verification_review_meta": verification_meta_review_artifact.model_dump(mode="json"),
            "inference_telemetry": telemetry_artifact.model_dump(mode="json"),
            "verification": verification.model_dump(mode="json"),
            "principal_message": principal_message.model_dump(mode="json"),
            # TODO: Remove the legacy `operator_message` mirror once downstream callers
            # have migrated to `principal_message`.
            "operator_message": principal_message.model_dump(mode="json"),
        }

    def run_steps(self, max_steps: int) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for _ in range(max_steps):
            child_results = self._dispatch_additional_ready_child_tasks(max_tasks=3)
            if child_results:
                results.extend(child_results)
                self._process_all_pending_worker_turns(limit_per_worker=5)
                self._reconcile_pending_tasks()
                continue
            result = self.run_once()
            results.append(result)
            self._process_all_pending_worker_turns(limit_per_worker=5)
            self._reconcile_pending_tasks()
            if result.get("status") == "complete":
                break
            implementation = result.get("implementation_report", {})
            content = implementation.get("content_summary")
            if isinstance(content, str) and '"status": "unsupported"' in content:
                break
        final_status = results[-1]["status"] if results else "complete"
        return {"status": final_status, "steps": results}

    def _dispatch_additional_ready_child_tasks(self, *, max_tasks: int = 3) -> list[dict[str, Any]]:
        dispatched: list[dict[str, Any]] = []
        seen_task_ids: set[str] = set()
        for _ in range(max(0, max_tasks)):
            candidates = [
                candidate
                for candidate in self._pending_message_task_candidates()
                if (
                    str(dict(candidate.metadata or {}).get("parent_task_id") or "").strip()
                    or str(dict(dict(candidate.metadata or {}).get("provenance") or {}).get("parent_task_id") or "").strip()
                )
                and candidate.source_task_id not in seen_task_ids
            ]
            if not candidates:
                break
            work_items = [
                ScheduledWorkItem.from_assessment(
                    self._message_task_assessment(candidate),
                    source_kind="message_task",
                    created_at=str(dict(candidate.metadata or {}).get("created_at") or ""),
                    metadata={
                        "strategy": candidate.strategy,
                        **self._scheduling_metadata_for_task_payload(dict(candidate.metadata or {})),
                    },
                )
                for candidate in candidates
            ]
            selection = self.prioritizer.select(work_items)
            if selection is None:
                break
            assessment = Loop0CandidateAssessment(
                candidate=selection.item.candidate,
                inspection=selection.item.inspection,
                verification=selection.item.verification,
            )
            seen_task_ids.add(str(assessment.candidate.source_task_id or ""))
            dispatched.append(self._execute_assessment(assessment))
        return dispatched

    def _process_all_pending_worker_turns(self, *, limit_per_worker: int = 5) -> list[dict[str, Any]]:
        pending_by_worker: dict[str, int] = {}
        for payload in self.db.list_records("communications"):
            if payload.get("channel") not in {self.principal_lane.channel, "operator"}:
                continue
            if payload.get("intent") != "worker_delegation_request":
                continue
            if payload.get("status") in {"acknowledged", "resolved"}:
                continue
            worker_id = str(payload.get("recipient") or "").strip()
            if not worker_id:
                continue
            pending_by_worker[worker_id] = pending_by_worker.get(worker_id, 0) + 1
        processed: list[dict[str, Any]] = []
        for worker_id in sorted(pending_by_worker):
            processed.extend(
                self.worker_runtime.process_pending(
                    worker_id=worker_id,
                    limit=min(limit_per_worker, pending_by_worker[worker_id]),
                )
            )
        return processed

    def _task_record_for_candidate(
        self,
        *,
        candidate: Loop0TaskCandidate,
        assessment: Loop0CandidateAssessment,
        implementation: dict[str, Any],
        verification_preview: VerificationResult,
        recommendation: dict[str, Any],
        coordination: dict[str, Any],
    ) -> TaskRecord:
        status = (
            "complete"
            if implementation["status"] == "applied" and verification_preview.result == "pass"
            else ("working" if implementation["status"] == "delegated" else "pending")
        )
        if candidate.strategy == "message_task" and candidate.metadata:
            payload = dict(candidate.metadata)
            task_id = str(payload.get("task_id") or candidate.source_task_id or "").strip()
            existing_payload = next(
                (
                    item
                    for item in self.db.list_records("tasks")
                    if str(item.get("task_id") or "").strip() == task_id
                ),
                None,
            )
            if existing_payload:
                payload = {
                    **dict(existing_payload),
                    **payload,
                    "provenance": {
                        **dict(existing_payload.get("provenance") or {}),
                        **dict(payload.get("provenance") or {}),
                    },
                }
            payload["status"] = status
            provenance = dict(payload.get("provenance") or {})
            provenance["dispatch"] = {
                "source": "loop0_runner",
                "candidate_key": candidate.key,
                "inspection": assessment.inspection,
                "implementation": implementation,
                "recommendation": recommendation,
                "coordination": coordination,
            }
            payload["provenance"] = provenance
            payload["updated_at"] = _now_iso()
            return TaskRecord(**payload)
        return TaskRecord(
            title=candidate.title,
            description=candidate.description,
            priority=candidate.priority,
            urgency=candidate.urgency,
            risk=candidate.risk,
            status=status,
            provenance={
                "source": "loop0_runner",
                "candidate_key": candidate.key,
                "inspection": assessment.inspection,
                "implementation": implementation,
                "recommendation": recommendation,
                "coordination": coordination,
            },
            success_criteria={"expected_paths": list(candidate.expected_paths)},
            completion_policy={"type": "apply_bounded_implementation", "strategy": candidate.strategy},
        )

    def _verify_candidate(
        self,
        candidate: Loop0TaskCandidate,
        task_id: str,
        implementation: dict[str, Any],
    ) -> VerificationRecord:
        result = self._verification_result(candidate, implementation)
        return VerificationRecord(
            target_kind="task",
            target_id=task_id,
            verifier="loop0_basic_verifier",
            result=result.result,
            confidence=result.confidence,
            evidence={
                "summary": result.summary,
                "details": result.evidence,
                "implementation": implementation,
                "candidate_key": candidate.key,
            },
        )

    def _verification_result(
        self,
        candidate: Loop0TaskCandidate,
        implementation: dict[str, Any],
    ) -> VerificationResult:
        status = str(implementation.get("status") or "").strip().lower()
        if candidate.strategy == "message_task":
            if (
                status == "applied"
                and implementation.get("emitted_communication_id")
                and implementation.get("generation_mode") in {"provider", "delegated_worker"}
            ):
                return VerificationResult(
                    result="pass",
                    confidence=0.9,
                    summary="Inbound task was selected from the unified queue and executed through a routed assistant or worker lane.",
                    evidence={"implementation": implementation},
                )
            if status == "applied" and implementation.get("emitted_communication_id"):
                return VerificationResult(
                    result="uncertain",
                    confidence=0.6,
                    summary="Inbound task emitted an principal message, but the assistant lane did not complete the work path cleanly.",
                    evidence={"implementation": implementation},
                )
            if status == "delegated" and implementation.get("delegated_via_worker"):
                return VerificationResult(
                    result="uncertain",
                    confidence=0.7,
                    summary="Inbound task was delegated to a durable worker lane and is awaiting reconciliation.",
                    evidence={"implementation": implementation},
                )
            return VerificationResult(
                result="fail",
                confidence=0.75,
                summary="Inbound task was selected but did not successfully produce a routed response.",
                evidence={"implementation": implementation},
            )
        if status == "applied":
            if candidate.strategy == "strengthen":
                return verify_strengthening_candidate(
                    self.settings.paths.project_root,
                    list(candidate.expected_paths),
                    baseline_inspection=implementation.get("baseline_inspection") or {},
                    written_paths=list(implementation.get("written_paths") or []),
                )
            return verify_expected_paths(self.settings.paths.project_root, list(candidate.expected_paths))
        if candidate.strategy == "strengthen":
            gap_result = verify_weak_candidate(self.settings.paths.project_root, list(candidate.expected_paths))
            return VerificationResult(
                result="fail",
                confidence=min(float(gap_result.confidence), 0.8),
                summary="Strengthening attempt did not improve the weak implementation slice.",
                evidence={
                    "weak_candidate": gap_result.evidence,
                    "implementation_status": status or "unknown",
                    "implementation_reason": implementation.get("reason"),
                },
            )
        gap_result = verify_gap_candidate(self.settings.paths.project_root, list(candidate.expected_paths))
        return VerificationResult(
            result="fail",
            confidence=min(float(gap_result.confidence), 0.8),
            summary="Implementation did not satisfy the expected outputs.",
            evidence={
                "gap": gap_result.evidence,
                "implementation_status": status or "unknown",
                "implementation_reason": implementation.get("reason"),
            },
        )

    def _apply_candidate(self, candidate: Loop0TaskCandidate, *, coordination: dict[str, Any]) -> dict[str, Any]:
        decision = dict(coordination.get("decision") or {})
        route = dict(coordination.get("route") or {})
        if decision.get("status") != "accepted":
            return {
                "status": "blocked",
                "reason": str(decision.get("reason") or "Coordinator did not approve execution."),
                "written_paths": [],
                "generation_mode": "none",
                "requested_route": route,
                "resolved_route": {},
                "preflight": {"ok": False, "reason": "coordinator_deferred"},
                "failure_kind": None,
                "degraded_reason": f"coordinator:{decision.get('status')}",
                "provider_error": None,
                "attempt_count": 0,
                "baseline_inspection": coordination.get("baseline_inspection") or {},
            }
        if candidate.strategy == "message_task":
            return self._apply_message_task(candidate, route=route, coordination=coordination)
        governance_block = self._ensure_governance_write_allowed(
            candidate=candidate,
            candidate_paths=list(candidate.expected_paths),
            provenance=dict(candidate.metadata or {}).get("provenance"),
            route=route,
        )
        if governance_block is not None:
            return governance_block
        baseline_inspection = (
            inspect_weak_expected_paths(self.settings.paths.project_root, list(candidate.expected_paths))
            if candidate.strategy == "strengthen"
            else inspect_expected_paths(self.settings.paths.project_root, list(candidate.expected_paths))
        )
        request = self._procedure_request_for_candidate(
            procedure_id="loop0-bounded-file-generation",
            candidate=candidate,
            route=route,
            inspection=inspect_expected_paths(
                self.settings.paths.project_root,
                list(candidate.expected_paths),
            ),
            expected_paths=list(candidate.expected_paths),
        )
        result = self.procedures.execute(
            project_root=self.settings.paths.project_root,
            request=request,
            fallback_builder=lambda procedure_request: self._candidate_implementations().get(
                candidate.key, {}
            ),
            force_fallback_only=bool(request.procedure_metadata.get("force_fallback_only")),
        )
        payload = result.model_dump(mode="json")
        payload["baseline_inspection"] = baseline_inspection
        return payload

    def _apply_message_task(
        self,
        candidate: Loop0TaskCandidate,
        *,
        route: dict[str, Any],
        coordination: dict[str, Any],
    ) -> dict[str, Any]:
        task_payload = dict(candidate.metadata or {})
        completion_policy = dict(task_payload.get("completion_policy") or {})
        coordination_actions = [
            *list(dict(coordination.get("source_decision") or {}).get("followup_actions") or []),
            *list(dict(coordination.get("decision") or {}).get("followup_actions") or []),
        ]
        task_id = str(task_payload.get("task_id") or candidate.source_task_id or candidate.key)
        lane_sender, conversation_id = self._message_task_lane_context(task_payload)
        provider = self.registry.get_provider(str(route.get("provider") or "").strip() or None)
        baseline_inspection = {"task_record": task_payload}
        if self._is_execution_track_message_task(task_payload):
            execution_result = self._apply_message_execution_task(
                candidate,
                task_payload=task_payload,
                task_id=task_id,
                route=route,
                baseline_inspection=baseline_inspection,
            )
            if execution_result is not None:
                return execution_result
        if self._should_delegate_message_task(candidate, route=route, task_payload=task_payload):
            if self._consensus_review_requested(coordination_actions):
                delegated_consensus = self._delegate_consensus_message_task(
                    candidate,
                    task_payload=task_payload,
                    task_id=task_id,
                    route=route,
                    baseline_inspection=baseline_inspection,
                    required_reviews=self._required_consensus_reviews(coordination_actions),
                )
                if delegated_consensus is not None:
                    return delegated_consensus
            delegated = self._delegate_message_task(
                candidate,
                task_payload=task_payload,
                task_id=task_id,
                route=route,
                baseline_inspection=baseline_inspection,
            )
            if delegated is not None:
                return delegated
        if provider is None:
            return self._fallback_message_dispatch(
                candidate,
                task_id=task_id,
                completion_policy=completion_policy,
                route=route,
                baseline_inspection=baseline_inspection,
                provider_error="No configured provider matched the selected route.",
                failure_kind="provider_missing",
                degraded_reason="provider:missing",
            )

        try:
            response = provider.complete(
                build_memory_augmented_request(
                    model=route.get("model"),
                    messages=self._message_task_prompt(candidate, task_payload),
                    metadata={
                        "cli_tool": route.get("cli_tool"),
                    },
                    memory_store_path=default_memory_store_path(data_dir=self.settings.paths.data_dir),
                    memory_query=" ".join(
                        [
                            candidate.title,
                            candidate.description,
                            str(task_payload.get("message") or ""),
                        ]
                    ),
                    accessor="local",
                    destination="remote",
                )
            )
        except Exception as exc:
            return self._fallback_message_dispatch(
                candidate,
                task_id=task_id,
                completion_policy=completion_policy,
                route=route,
                baseline_inspection=baseline_inspection,
                provider_error=str(exc),
                failure_kind="provider_execution_failed",
                degraded_reason="provider:execution_failed",
            )

        principal_response = self._extract_principal_response(response.content)
        followup_tasks = self._extract_followup_tasks(response.content, task_payload)
        derived_artifact = self._extract_message_artifact(response.content, candidate, task_payload)
        notice = self.principal_lane.send(
            sender=lane_sender,
            recipient="principal",
            conversation_id=conversation_id,
            kind="question" if completion_policy.get("type") == "request_clarification" else "notice",
            intent=(
                "clarification_request"
                if completion_policy.get("type") == "request_clarification"
                else "message_task_response"
            ),
            payload={
                "task_id": task_id,
                "title": candidate.title,
                "description": candidate.description,
                "completion_policy": completion_policy,
                "assistant_output": principal_response,
                "provider": response.provider,
                "model": response.model,
                "route": route,
                "followup_tasks": followup_tasks,
                "derived_artifact": derived_artifact,
            },
            priority=candidate.priority,
            urgency=candidate.urgency,
            related_task_ids=[task_id],
        )
        return {
            "status": "applied",
            "reason": "Unified queue selected an inbound task and executed it through a routed assistant lane.",
            "written_paths": [],
            "generation_mode": "provider",
            "requested_route": route,
            "resolved_route": {
                **route,
                "provider": response.provider,
                "model": response.model,
            },
            "preflight": {"ok": True, "reason": None},
            "failure_kind": None,
            "degraded_reason": None,
            "provider_error": None,
            "attempt_count": 1,
            "baseline_inspection": baseline_inspection,
            "assistant_output": principal_response,
            "followup_tasks": followup_tasks,
            "derived_artifact": derived_artifact,
            "emitted_communication_id": notice.communication_id,
        }

    def _should_delegate_message_task(
        self,
        candidate: Loop0TaskCandidate,
        *,
        route: dict[str, Any],
        task_payload: dict[str, Any],
    ) -> bool:
        if candidate.strategy != "message_task":
            return False
        if self._is_execution_track_message_task(task_payload):
            return False
        completion_policy = dict(task_payload.get("completion_policy") or {})
        prefer_cheap_lanes = bool(completion_policy.get("prefer_cheap_lanes"))
        provider = str(route.get("provider") or "").strip().lower()
        cli_tool = str(route.get("cli_tool") or "").strip().lower()
        if cli_tool in {"kilocode", "gemini-cli", "claude-code"}:
            return True
        if prefer_cheap_lanes and provider in {"cli", "google", "custom", "anthropic"}:
            return True
        return False

    def _consensus_review_requested(self, actions: list[dict[str, Any]]) -> bool:
        return any(str(action.get("type") or "").strip() == "consensus_approval_eligible" for action in actions)

    def _required_consensus_reviews(self, actions: list[dict[str, Any]]) -> int:
        for action in actions:
            if str(action.get("type") or "").strip() != "consensus_approval_eligible":
                continue
            try:
                return max(2, int(action.get("required_reviews") or 2))
            except Exception:
                return 2
        return 2

    def _consensus_worker_routes(
        self,
        *,
        primary_route: dict[str, Any],
        task_payload: dict[str, Any],
        required_reviews: int,
    ) -> list[dict[str, Any]]:
        route_preferences = dict(dict(task_payload.get("completion_policy") or {}).get("route_preferences") or {})
        preferred_cli_tools = [
            str(item).strip().lower()
            for item in list(route_preferences.get("preferred_cli_tools") or [])
            if str(item).strip()
        ]
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        def _add(route: dict[str, Any]) -> None:
            key = (
                str(route.get("provider") or "").strip().lower(),
                str(route.get("cli_tool") or "").strip().lower(),
                str(route.get("model") or "").strip().lower(),
            )
            if not key[0] or key in seen:
                return
            seen.add(key)
            candidates.append(route)

        _add(dict(primary_route))
        for cli_tool in [*preferred_cli_tools, "kilocode", "gemini-cli", "claude-code"]:
            if cli_tool == str(primary_route.get("cli_tool") or "").strip().lower():
                continue
            if cli_tool == "gemini-cli":
                _add(
                    {
                        "provider": "cli",
                        "cli_tool": cli_tool,
                        "model": str(route_preferences.get("preferred_model") or "").strip() or "gemini-2.5-flash",
                        "reason": "consensus_review_peer",
                    }
                )
                continue
            _add(
                {
                    "provider": "cli",
                    "cli_tool": cli_tool,
                    "model": None,
                    "reason": "consensus_review_peer",
                }
            )
        return candidates[: max(2, required_reviews)]

    def _delegate_consensus_message_task(
        self,
        candidate: Loop0TaskCandidate,
        *,
        task_payload: dict[str, Any],
        task_id: str,
        route: dict[str, Any],
        baseline_inspection: dict[str, Any],
        required_reviews: int,
    ) -> dict[str, Any] | None:
        routes = self._consensus_worker_routes(
            primary_route=route,
            task_payload=task_payload,
            required_reviews=required_reviews,
        )
        if len(routes) < 2:
            return None
        approval = delegated_task_approval(task_payload=task_payload, delegated_by="prime")
        worker_task_ids: list[str] = []
        worker_ids: list[str] = []
        request_ids: list[str] = []
        for index, worker_route in enumerate(routes, start=1):
            worker_id = worker_id_for_route(worker_route)
            worker_ids.append(worker_id)
            worker_task = TaskRecord(
                parent_task_id=task_id,
                title=f"Consensus Review {index}: {candidate.title}",
                description=f"Execute bounded consensus review on {worker_id}.",
                priority=candidate.priority,
                urgency=candidate.urgency,
                risk=str(task_payload.get("risk") or candidate.risk or "low"),
                status="working",
                provenance={
                    "source": "worker_delegation",
                    "role": "consensus_review",
                    "parent_task_id": task_id,
                    "worker_id": worker_id,
                    "task_class": "delegated_message_task",
                    "route": worker_route,
                    "approval": approval,
                },
                permissions={"approval": approval},
                success_criteria={"worker_result_reconciled": True},
                completion_policy={
                    "type": "worker_execution",
                    "route_preferences": dict(task_payload.get("completion_policy", {}).get("route_preferences") or {}),
                    "approval": approval,
                },
            )
            self.db.upsert_task(worker_task)
            worker_task_ids.append(worker_task.task_id)
            request = self.principal_lane.send(
                sender="prime",
                recipient=worker_id,
                conversation_id=self.principal_lane.default_conversation_id(worker_id),
                kind="delegation",
                intent="worker_delegation_request",
                payload={
                    "delegation_kind": "message_task",
                    "task_id": task_id,
                    "title": candidate.title,
                    "description": candidate.description,
                    "message": candidate.description,
                    "task_payload": {
                        **task_payload,
                        "consensus_review": {
                            "required_reviews": required_reviews,
                            "worker_routes": routes,
                        },
                    },
                    "route": worker_route,
                    "worker_task_id": worker_task.task_id,
                    "approval": approval,
                },
                priority=candidate.priority,
                urgency=candidate.urgency,
                related_task_ids=[task_id],
            )
            request_ids.append(request.communication_id)
        if candidate.metadata:
            updated_parent = TaskRecord(
                **{
                    **dict(task_payload),
                    "status": "working",
                    "updated_at": _now_iso(),
                    "active_child_ids": list(dict.fromkeys([*list(task_payload.get("active_child_ids") or []), *worker_task_ids])),
                    "provenance": {
                        **dict(task_payload.get("provenance") or {}),
                        "consensus_review": {
                            "required_reviews": required_reviews,
                            "worker_task_ids": worker_task_ids,
                            "worker_ids": worker_ids,
                            "request_ids": request_ids,
                            "status": "pending",
                            "results": [],
                        },
                    },
                }
            )
            self.db.upsert_task(updated_parent)
        return {
            "status": "delegated",
            "reason": "Unified queue delegated the inbound task to multiple cheap worker lanes for bounded consensus review.",
            "written_paths": [],
            "generation_mode": "delegated_worker",
            "requested_route": route,
            "resolved_route": {},
            "preflight": {"ok": True, "reason": "consensus_workers_enqueued"},
            "failure_kind": None,
            "degraded_reason": None,
            "provider_error": None,
            "attempt_count": 0,
            "baseline_inspection": baseline_inspection,
            "assistant_output": "",
            "followup_tasks": [],
            "derived_artifact": None,
            "delegated_via_worker": ",".join(worker_ids),
            "worker_task_id": worker_task_ids[0],
            "delegation_request_id": request_ids[0],
            "delegation_result_id": None,
            "emitted_communication_id": None,
            "consensus_review": {
                "required_reviews": required_reviews,
                "worker_task_ids": worker_task_ids,
                "worker_ids": worker_ids,
            },
        }

    def _delegate_message_task(
        self,
        candidate: Loop0TaskCandidate,
        *,
        task_payload: dict[str, Any],
        task_id: str,
        route: dict[str, Any],
        baseline_inspection: dict[str, Any],
    ) -> dict[str, Any] | None:
        worker_id = worker_id_for_route(route)
        approval = delegated_task_approval(task_payload=task_payload, delegated_by="prime")
        worker_task = TaskRecord(
            parent_task_id=task_id,
            title=f"Worker Task: {candidate.title}",
            description=f"Execute delegated worker task on {worker_id}.",
            priority=candidate.priority,
            urgency=candidate.urgency,
            risk=str(task_payload.get("risk") or candidate.risk or "low"),
            status="working",
            provenance={
                "source": "worker_delegation",
                "parent_task_id": task_id,
                "worker_id": worker_id,
                "task_class": "delegated_message_task",
                "route": route,
                "approval": approval,
            },
            permissions={"approval": approval},
            success_criteria={"worker_result_reconciled": True},
            completion_policy={
                "type": "worker_execution",
                "route_preferences": dict(task_payload.get("completion_policy", {}).get("route_preferences") or {}),
                "approval": approval,
            },
        )
        self.db.upsert_task(worker_task)
        request = self.principal_lane.send(
            sender="prime",
            recipient=worker_id,
            conversation_id=self.principal_lane.default_conversation_id(worker_id),
            kind="delegation",
            intent="worker_delegation_request",
            payload={
                "delegation_kind": "message_task",
                "task_id": task_id,
                "title": candidate.title,
                "description": candidate.description,
                "message": candidate.description,
                "task_payload": task_payload,
                "route": route,
                "worker_task_id": worker_task.task_id,
                "approval": approval,
            },
            priority=candidate.priority,
            urgency=candidate.urgency,
            related_task_ids=[task_id],
        )
        if candidate.metadata:
            updated_parent = TaskRecord(
                **{
                    **dict(task_payload),
                    "status": "working",
                    "updated_at": _now_iso(),
                    "active_child_ids": list(
                        dict.fromkeys([*list(task_payload.get("active_child_ids") or []), worker_task.task_id])
                    ),
                    "provenance": {
                        **dict(task_payload.get("provenance") or {}),
                        "worker_task_id": worker_task.task_id,
                    },
                }
            )
            self.db.upsert_task(updated_parent)
        return {
            "status": "delegated",
            "reason": "Unified queue delegated the inbound task to a provider-backed worker lane for later reconciliation.",
            "written_paths": [],
            "generation_mode": "delegated_worker",
            "requested_route": route,
            "resolved_route": {},
            "preflight": {"ok": True, "reason": "worker_enqueued"},
            "failure_kind": None,
            "degraded_reason": None,
            "provider_error": None,
            "attempt_count": 0,
            "baseline_inspection": baseline_inspection,
            "assistant_output": "",
            "followup_tasks": [],
            "derived_artifact": None,
            "delegated_via_worker": worker_id,
            "worker_task_id": worker_task.task_id,
            "delegation_request_id": request.communication_id,
            "delegation_result_id": None,
            "emitted_communication_id": None,
        }

    def _apply_message_execution_task(
        self,
        candidate: Loop0TaskCandidate,
        *,
        task_payload: dict[str, Any],
        task_id: str,
        route: dict[str, Any],
        baseline_inspection: dict[str, Any],
    ) -> dict[str, Any] | None:
        expected_paths = self._infer_expected_paths_for_message_task(candidate)
        if not expected_paths:
            return None
        governance_block = self._ensure_governance_write_allowed(
            candidate=candidate,
            candidate_paths=expected_paths,
            provenance=dict(task_payload.get("provenance") or {}),
            route=route,
        )
        if governance_block is not None:
            governance_block["baseline_inspection"] = {
                **dict(governance_block.get("baseline_inspection") or {}),
                "task_record": task_payload,
                "inferred_expected_paths": expected_paths,
            }
            return governance_block
        request = self._procedure_request_for_candidate(
            procedure_id="message-task-bounded-file-generation",
            candidate=candidate,
            route=route,
            inspection={"task_record": task_payload, "inferred_expected_paths": expected_paths},
            expected_paths=expected_paths,
        )
        result = self.procedures.execute(
            project_root=self.settings.paths.project_root,
            request=request,
            fallback_builder=None,
            force_fallback_only=bool(request.procedure_metadata.get("force_fallback_only")),
        )
        payload = result.model_dump(mode="json")
        payload["baseline_inspection"] = baseline_inspection
        if result.status != "applied":
            return None
        notice = self.principal_lane.send(
            sender=self._message_task_lane_context(task_payload)[0],
            recipient="principal",
            conversation_id=self._message_task_lane_context(task_payload)[1],
            kind="notice",
            intent="message_task_execution_result",
            payload={
                "task_id": task_id,
                "title": candidate.title,
                "description": candidate.description,
                "written_paths": result.written_paths,
                "route": payload.get("resolved_route") or route,
            },
            priority=candidate.priority,
            urgency=candidate.urgency,
            related_task_ids=[task_id],
        )
        payload["assistant_output"] = (
            "Applied bounded file changes for the selected message task."
        )
        payload["followup_tasks"] = []
        payload["emitted_communication_id"] = notice.communication_id
        payload["execution_track"] = "bounded_file_generation"
        return payload

    def _fallback_message_dispatch(
        self,
        candidate: Loop0TaskCandidate,
        *,
        task_id: str,
        completion_policy: dict[str, Any],
        route: dict[str, Any],
        baseline_inspection: dict[str, Any],
        provider_error: str,
        failure_kind: str,
        degraded_reason: str,
    ) -> dict[str, Any]:
        lane_sender, conversation_id = self._message_task_lane_context(candidate.metadata or {})
        if completion_policy.get("type") == "request_clarification":
            notice = self.principal_lane.send(
                sender=lane_sender,
                recipient="principal",
                conversation_id=conversation_id,
                kind="question",
                intent="clarification_request",
                payload={
                    "task_id": task_id,
                    "message": f"Please clarify this inbound request before Astrata acts on it: {candidate.description}",
                    "provider_error": provider_error,
                },
                priority=candidate.priority,
                urgency=candidate.urgency,
                related_task_ids=[task_id],
            )
        else:
            notice = self.principal_lane.send(
                sender=lane_sender,
                recipient="principal",
                conversation_id=conversation_id,
                kind="notice",
                intent="inbound_task_selected",
                payload={
                    "task_id": task_id,
                    "title": candidate.title,
                    "description": candidate.description,
                    "completion_policy": completion_policy,
                    "message": "Astrata selected this inbound task, but the assistant lane degraded and fell back to a bounded principal notice.",
                    "provider_error": provider_error,
                },
                priority=candidate.priority,
                urgency=candidate.urgency,
                related_task_ids=[task_id],
            )
        return {
            "status": "applied",
            "reason": "Unified queue selected an inbound task, but assistant execution degraded to a bounded principal-lane fallback.",
            "written_paths": [],
            "generation_mode": "message_dispatch_fallback",
            "requested_route": route,
            "resolved_route": {},
            "preflight": {"ok": True, "reason": None},
            "failure_kind": failure_kind,
            "degraded_reason": degraded_reason,
            "provider_error": provider_error,
            "attempt_count": 1,
            "baseline_inspection": baseline_inspection,
            "followup_tasks": [],
            "derived_artifact": None,
            "emitted_communication_id": notice.communication_id,
        }

    def _message_task_lane_context(self, task_payload: dict[str, Any]) -> tuple[str, str]:
        provenance = dict(task_payload.get("provenance") or {})
        lane = str(provenance.get("target_lane") or "").strip().lower()
        if lane not in {"prime", "local"}:
            lane = "astrata.assistant"
        conversation_id = str(provenance.get("source_conversation_id") or "").strip()
        if not conversation_id:
            conversation_id = self.principal_lane.default_conversation_id(lane if lane in {"prime", "local"} else "system")
        return lane, conversation_id

    def _message_task_prompt(
        self,
        candidate: Loop0TaskCandidate,
        task_payload: dict[str, Any],
    ) -> list[Message]:
        completion_policy = dict(task_payload.get("completion_policy") or {})
        provenance = dict(task_payload.get("provenance") or {})
        success_criteria = dict(task_payload.get("success_criteria") or {})
        batched_task_payloads = list(task_payload.get("batched_task_payloads") or [])
        system_prompt = (
            "You are Astrata handling an inbound principal-derived task. "
            "Respond concisely and usefully. Return strict JSON with keys "
            "`principal_response`, `followup_tasks`, and `artifact`. "
            "`principal_response` should be the bounded next response or question for the principal. "
            "`followup_tasks` should be a short list of at most 4 governed tasks only when genuinely helpful. "
            "Each follow-up task should include: title, description, priority, urgency, risk, completion_type, "
            "and, when clear, delta_kind (`input_vs_spec` or `spec_vs_reality`) plus delta_summary. "
            "When the work is too broad for one leaf task, decompose it into multiple oneshottable leaf tasks. "
            "For decompositions, you may also include `task_id_hint`, `depends_on`, `parallelizable`, and `route_preferences`. "
            "`artifact` should summarize the durable knowledge produced by this step, with keys: "
            "title, summary, confidence, findings. "
            "If no follow-up work is needed, return an empty list. "
            "Do not mention internal quotas or routing unless directly relevant."
        )
        if batched_task_payloads:
            system_prompt += (
                " This task carries a small batch of related low-priority requests. "
                "Address them together in one bounded response when practical."
            )
        return [
            Message(role="system", content=system_prompt),
            Message(
                role="user",
                content=json.dumps(
                    {
                        "task": {
                            "title": candidate.title,
                            "description": candidate.description,
                            "priority": candidate.priority,
                            "urgency": candidate.urgency,
                            "risk": candidate.risk,
                        },
                        "completion_policy": completion_policy,
                        "success_criteria": success_criteria,
                        "provenance": provenance,
                        "batched_tasks": [
                            {
                                "task_id": str(item.get("task_id") or ""),
                                "title": str(item.get("title") or ""),
                                "description": str(item.get("description") or ""),
                            }
                            for item in batched_task_payloads[:4]
                        ],
                    },
                    indent=2,
                ),
            ),
        ]

    def _extract_followup_tasks(
        self,
        response_text: str,
        task_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        parsed = _try_parse_json(response_text) or {}
        raw_tasks = parsed.get("followup_tasks")
        if not isinstance(raw_tasks, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in raw_tasks[:4]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            description = str(item.get("description") or "").strip()
            if not title or not description:
                continue
            normalized.append(
                {
                    "title": title[:120],
                    "description": description,
                    "priority": int(item.get("priority") or 4),
                    "urgency": int(item.get("urgency") or 2),
                    "risk": str(item.get("risk") or "low"),
                    "completion_type": str(item.get("completion_type") or "respond_or_execute"),
                    "success_criteria": dict(item.get("success_criteria") or {"message_addressed": True}),
                    "delta_kind": str(item.get("delta_kind") or "").strip() or None,
                    "delta_summary": str(item.get("delta_summary") or "").strip() or None,
                    "route_preferences": {
                        **dict(dict(task_payload.get("completion_policy") or {}).get("route_preferences") or {}),
                        **dict(item.get("route_preferences") or {}),
                    },
                    "task_id_hint": str(item.get("task_id_hint") or "").strip() or None,
                    "depends_on": [
                        str(dependency).strip()
                        for dependency in list(item.get("depends_on") or [])
                        if str(dependency).strip()
                    ],
                    "parallelizable": bool(item.get("parallelizable")),
                }
            )
        return normalized

    def _extract_principal_response(self, response_text: str) -> str:
        parsed = _try_parse_json(response_text) or {}
        principal_response = str(
            parsed.get("principal_response") or parsed.get("operator_response") or parsed.get("response") or response_text
        ).strip()
        return principal_response or response_text.strip()

    def _extract_message_artifact(
        self,
        response_text: str,
        candidate: Loop0TaskCandidate,
        task_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        parsed = _try_parse_json(response_text) or {}
        artifact = parsed.get("artifact")
        completion_type = str(dict(task_payload.get("completion_policy") or {}).get("type") or "").strip()
        principal_response = self._extract_principal_response(response_text)
        if not isinstance(artifact, dict) and completion_type not in {"review_or_rewrite_spec", "review_or_audit"}:
            return None
        artifact = dict(artifact or {})
        findings = artifact.get("findings")
        if not isinstance(findings, list):
            findings = []
        confidence = artifact.get("confidence")
        try:
            normalized_confidence = float(confidence)
        except Exception:
            normalized_confidence = 0.7 if completion_type in {"review_or_rewrite_spec", "review_or_audit"} else 0.5
        status = "good" if normalized_confidence >= 0.75 else "degraded"
        return {
            "artifact_type": self._artifact_type_for_completion_type(completion_type),
            "title": str(artifact.get("title") or f"{candidate.title} artifact"),
            "description": candidate.description,
            "summary": str(artifact.get("summary") or principal_response),
            "confidence": normalized_confidence,
            "findings": findings[:3],
            "status": status,
        }

    def _materialize_followup_tasks(
        self,
        *,
        candidate: Loop0TaskCandidate,
        parent_task: TaskRecord,
        implementation: dict[str, Any],
    ) -> list[TaskRecord]:
        if candidate.strategy != "message_task":
            return []
        specs = list(implementation.get("followup_tasks") or [])
        if not specs:
            specs = self._promote_followups_from_artifact(
                candidate=candidate,
                implementation=implementation,
            )
        if not specs:
            return []
        prepared: list[tuple[dict[str, Any], Any]] = []
        hint_to_task_id: dict[str, str] = {}
        materialized: list[TaskRecord] = []
        for spec in specs[:4]:
            if not isinstance(spec, dict):
                continue
            proposal = normalize_derived_task_proposal(
                title=str(spec.get("title") or "").strip(),
                description=str(spec.get("description") or "").strip(),
                parent_provenance={
                    "source": "message_task_followup",
                    "parent_task_id": parent_task.task_id,
                    "source_message_task_key": candidate.key,
                    "source_communication_id": dict(parent_task.provenance).get("source_communication_id"),
                },
                suggested_completion_type=str(spec.get("completion_type") or "").strip() or None,
                priority=int(spec.get("priority") or 4),
                urgency=int(spec.get("urgency") or 2),
                risk=str(spec.get("risk") or "low"),
                success_criteria=dict(spec.get("success_criteria") or {"message_addressed": True}),
                route_preferences=dict(spec.get("route_preferences") or {}),
                delta_kind=str(spec.get("delta_kind") or "").strip() or None,
                delta_summary=str(spec.get("delta_summary") or "").strip() or None,
                task_id_hint=str(spec.get("task_id_hint") or "").strip() or None,
                depends_on=list(spec.get("depends_on") or []),
                parallelizable=bool(spec.get("parallelizable")),
            )
            prepared.append((spec, proposal))
        for spec, proposal in prepared:
            task = TaskRecord(
                title=proposal.title,
                description=proposal.description,
                priority=proposal.priority,
                urgency=proposal.urgency,
                risk=proposal.risk,
                dependencies=[],
                provenance=proposal.provenance,
                success_criteria=proposal.success_criteria,
                completion_policy={
                    **proposal.completion_policy,
                    "route_preferences": proposal.route_preferences,
                },
            )
            self.db.upsert_task(task)
            materialized.append(task)
            if proposal.task_id_hint:
                hint_to_task_id[proposal.task_id_hint] = task.task_id
            hint_to_task_id[proposal.title] = task.task_id
        for task, (spec, proposal) in zip(materialized, prepared, strict=False):
            dependencies = [
                hint_to_task_id[dependency]
                for dependency in proposal.depends_on
                if dependency in hint_to_task_id and hint_to_task_id[dependency] != task.task_id
            ]
            if dependencies or proposal.parallelizable or proposal.task_id_hint:
                task.dependencies = dependencies
                task.provenance = {
                    **dict(task.provenance),
                    "decomposition": {
                        "task_id_hint": proposal.task_id_hint,
                        "depends_on": list(proposal.depends_on),
                        "parallelizable": proposal.parallelizable,
                    },
                }
                self.db.upsert_task(task)
        self._record_followup_decomposition(
            candidate=candidate,
            parent_task=parent_task,
            materialized=materialized,
            specs=[spec for spec, _ in prepared],
        )
        return materialized

    def _record_followup_decomposition(
        self,
        *,
        candidate: Loop0TaskCandidate,
        parent_task: TaskRecord,
        materialized: list[TaskRecord],
        specs: list[dict[str, Any]],
    ) -> None:
        if len(materialized) < 2:
            return
        dependency_edges = [
            {"task_id": task.task_id, "depends_on": list(task.dependencies)}
            for task in materialized
            if task.dependencies
        ]
        if not dependency_edges and not any(bool(spec.get("parallelizable")) for spec in specs):
            return
        decomposition_artifact = ArtifactRecord(
            artifact_type="task_decomposition",
            title=f"Task decomposition: {candidate.title}",
            description="Dependency-aware follow-up DAG derived from a completed message task.",
            content_summary=json.dumps(
                {
                    "parent_task_id": parent_task.task_id,
                    "candidate_key": candidate.key,
                    "tasks": [task.model_dump(mode="json") for task in materialized],
                    "dependency_edges": dependency_edges,
                    "parallelizable_task_count": sum(1 for spec in specs if bool(spec.get("parallelizable"))),
                },
                indent=2,
            ),
            provenance={"task_id": parent_task.task_id, "source": "message_task_followup"},
        )
        self.db.upsert_artifact(decomposition_artifact)
        procedure = ProcedureRecord(
            procedure_id=f"draft-followup-{parent_task.task_id}",
            title=f"Draft Follow-up Procedure: {candidate.title}",
            description="Draft procedure candidate captured from a successful follow-up task decomposition.",
            provenance={
                "source": "message_task_followup",
                "parent_task_id": parent_task.task_id,
                "candidate_key": candidate.key,
            },
            lifecycle_state="draft",
            install_state="proposed",
            structure=ProcedureStructure(
                entry_node_id=materialized[0].task_id,
                nodes=[
                    ProcedureTaskNode(
                        node_id=task.task_id,
                        task_title=task.title,
                        description=task.description,
                        kind="leaf",
                        next_nodes=[
                            downstream.task_id
                            for downstream in materialized
                            if task.task_id in set(downstream.dependencies)
                        ],
                        metadata={
                            "priority": task.priority,
                            "urgency": task.urgency,
                            "risk": task.risk,
                            "completion_policy": dict(task.completion_policy),
                            "parallelizable": bool(
                                dict(task.provenance or {}).get("decomposition", {}).get("parallelizable")
                            ),
                        },
                    )
                    for task in materialized
                ],
                metadata={
                    "captured_from": "message_task_followup",
                    "parent_task_id": parent_task.task_id,
                    "dependency_edge_count": len(dependency_edges),
                },
            ),
            success_contract={
                "deliverables": ["bounded_subtask_dag", "draft_procedure_candidate"],
            },
            notes=["Captured from delegated follow-up decomposition for later evaluation."],
        )
        procedure_artifact = ArtifactRecord(
            artifact_type="draft_procedure",
            title=procedure.title,
            description=procedure.description,
            content_summary=procedure.model_dump_json(indent=2),
            provenance={
                "task_id": parent_task.task_id,
                "source": "message_task_followup",
                "decomposition_artifact_id": decomposition_artifact.artifact_id,
            },
        )
        self.db.upsert_artifact(procedure_artifact)

    def _promote_followups_from_artifact(
        self,
        *,
        candidate: Loop0TaskCandidate,
        implementation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        artifact = implementation.get("derived_artifact")
        if not isinstance(artifact, dict):
            return []
        confidence = float(artifact.get("confidence") or 0.0)
        findings = list(artifact.get("findings") or [])
        if confidence < 0.75 or not findings:
            return []
        promoted: list[dict[str, Any]] = []
        for finding in findings[:2]:
            if isinstance(finding, dict):
                title = str(finding.get("title") or "").strip()
                description = str(finding.get("description") or "").strip()
            else:
                title = ""
                description = str(finding).strip()
            if not description:
                continue
            promoted.append(
                {
                    "title": title or self._title_from_finding(description, candidate),
                    "description": description,
                    "priority": max(candidate.priority, 4),
                    "urgency": candidate.urgency,
                    "risk": candidate.risk,
                    "completion_type": self._completion_type_for_promoted_artifact(candidate),
                    "success_criteria": {"artifact_finding_addressed": True},
                    "route_preferences": {},
                }
            )
        return promoted

    def _artifact_type_for_completion_type(self, completion_type: str) -> str:
        if completion_type == "review_or_rewrite_spec":
            return "spec_review"
        if completion_type == "review_or_audit":
            return "review_report"
        if completion_type == "request_clarification":
            return "clarification_note"
        return "message_analysis"

    def _completion_type_for_promoted_artifact(self, candidate: Loop0TaskCandidate) -> str:
        completion_type = str(dict(candidate.metadata or {}).get("completion_policy", {}).get("type") or "").strip()
        if completion_type == "review_or_rewrite_spec":
            return "respond_or_execute"
        if completion_type == "review_or_audit":
            return "review_or_audit"
        return "respond_or_execute"

    def _title_from_finding(self, description: str, candidate: Loop0TaskCandidate) -> str:
        lowered = description.lower()
        if "spec" in lowered:
            return "Implement spec finding"
        if any(token in lowered for token in ("fix", "update", "implement", "wire", "validation", "error handling")):
            return f"Implement follow-up for {candidate.title}"
        return f"Follow up on {candidate.title}"

    def _is_execution_track_message_task(self, task_payload: dict[str, Any]) -> bool:
        completion_type = str(dict(task_payload.get("completion_policy") or {}).get("type") or "").strip()
        if completion_type not in {"respond_or_execute", "review_or_execute"}:
            return False
        combined = " ".join(
            [
                str(task_payload.get("title") or ""),
                str(task_payload.get("description") or ""),
            ]
        ).lower()
        return any(
            token in combined
            for token in ("implement", "update", "edit", "modify", "fix", "wire", "create", "build", "patch", "strengthen")
        )

    def _infer_expected_paths_for_message_task(self, candidate: Loop0TaskCandidate) -> list[str]:
        combined = " ".join([candidate.title, candidate.description])
        filenames = {
            match.group(0)
            for match in re.finditer(r"\b[\w.-]+\.(?:py|md|toml|json)\b", combined)
        }
        if not filenames:
            return []
        inferred: list[str] = []
        for filename in sorted(filenames):
            matches = [
                path
                for path in self.settings.paths.project_root.rglob(filename)
                if path.is_file() and ".astrata" not in path.parts
            ]
            if not matches:
                continue
            matches.sort(
                key=lambda path: (
                    0 if "astrata" in path.parts else 1,
                    len(path.parts),
                    str(path),
                )
            )
            rel_path = matches[0].relative_to(self.settings.paths.project_root).as_posix()
            if rel_path not in inferred:
                inferred.append(rel_path)
        return inferred[:3]

    def _preferred_provider_for_strategy(self, candidate: Loop0TaskCandidate) -> str | None:
        if not candidate.description:
            return None
        if candidate.strategy != "alternate_provider":
            return None
        marker = "alternate provider `"
        if marker not in candidate.description:
            return None
        suffix = candidate.description.split(marker, 1)[1]
        return suffix.split("`", 1)[0].strip() or None

    def _preferred_cli_tool_for_strategy(self, candidate: Loop0TaskCandidate) -> str | None:
        if candidate.strategy != "alternate_provider":
            return None
        description = candidate.description
        marker = "alternate provider `"
        if marker not in description:
            return None
        provider = description.split(marker, 1)[1].split("`", 1)[0].strip().lower()
        if provider == "cli":
            return "codex-cli"
        return None

    def _avoided_providers_for_strategy(self, candidate: Loop0TaskCandidate) -> list[str]:
        if candidate.strategy not in {"alternate_provider", "fallback_only"}:
            return []
        recent_attempts = self.db.list_records("attempts")
        for attempt in reversed(recent_attempts):
            provenance = dict(attempt.get("provenance") or {})
            if str(provenance.get("candidate_key") or "").strip() != candidate.key:
                continue
            implementation = dict(dict(attempt.get("resource_usage") or {}).get("implementation") or {})
            requested_route = dict(implementation.get("requested_route") or {})
            provider = str(requested_route.get("provider") or "").strip()
            return [provider] if provider else []
        return []

    def _avoided_cli_tools_for_strategy(self, candidate: Loop0TaskCandidate) -> list[str]:
        if candidate.strategy not in {"alternate_provider", "fallback_only"}:
            return []
        recent_attempts = self.db.list_records("attempts")
        for attempt in reversed(recent_attempts):
            provenance = dict(attempt.get("provenance") or {})
            if str(provenance.get("candidate_key") or "").strip() != candidate.key:
                continue
            implementation = dict(dict(attempt.get("resource_usage") or {}).get("implementation") or {})
            requested_route = dict(implementation.get("requested_route") or {})
            cli_tool = str(requested_route.get("cli_tool") or "").strip()
            return [cli_tool] if cli_tool else []
        return []

    def _procedure_request_for_candidate(
        self,
        *,
        procedure_id: str,
        candidate: Loop0TaskCandidate,
        route: dict[str, Any],
        inspection: dict[str, Any],
        expected_paths: list[str],
    ) -> ProcedureExecutionRequest:
        resolved = self._resolve_procedure_for_candidate(
            procedure_id=procedure_id,
            candidate=candidate,
            route=route,
        )
        preferred_provider = str(route.get("provider") or "").strip() or None
        preferred_cli_tool = str(route.get("cli_tool") or "").strip() or None
        if not preferred_provider:
            for provider in resolved.variant.preferred_providers:
                if self.registry.get_provider(provider):
                    preferred_provider = provider
                    break
        if not preferred_cli_tool:
            available_cli_tools = set(self.registry.configured_cli_tools())
            for cli_tool in resolved.variant.preferred_cli_tools:
                if cli_tool in available_cli_tools:
                    preferred_cli_tool = cli_tool
                    break
        avoided_providers = list(dict.fromkeys([
            *resolved.variant.avoided_providers,
            *self._avoided_providers_for_strategy(candidate),
        ]))
        avoided_cli_tools = list(dict.fromkeys([
            *resolved.variant.avoided_cli_tools,
            *self._avoided_cli_tools_for_strategy(candidate),
        ]))
        return ProcedureExecutionRequest(
            procedure_id=procedure_id,
            procedure_variant_id=resolved.variant_id,
            title=candidate.title,
            description=candidate.description,
            expected_paths=expected_paths,
            available_docs=list(self.governance.planning_docs.keys()),
            inspection=inspection,
            actor_capability=resolved.actor_capability,
            execution_mode=resolved.variant.execution_mode,
            risk=candidate.risk,
            priority=candidate.priority,
            urgency=candidate.urgency,
            preferred_provider=preferred_provider,
            avoided_providers=avoided_providers,
            preferred_cli_tool=preferred_cli_tool,
            avoided_cli_tools=avoided_cli_tools,
            procedure_metadata={
                "procedure_title": resolved.procedure.title,
                "variant_title": resolved.variant.title,
                "variant_description": resolved.variant.description,
                "requested_variant_id": resolved.requested_variant_id,
                "fallback_from_variant_id": resolved.fallback_from_variant_id,
                "execution_mode": resolved.variant.execution_mode,
                "shortcut_allowed": bool(resolved.variant.metadata.get("shortcut_allowed")),
                "capture_shortcut_candidate": bool(resolved.variant.metadata.get("capture_shortcut_candidate")),
                "force_fallback_only": resolved.variant.force_fallback_only,
                "use_git_worktree": True,
                "task_id": candidate.key,
            },
        )

    def _resolve_procedure_for_candidate(
        self,
        *,
        procedure_id: str,
        candidate: Loop0TaskCandidate,
        route: dict[str, Any],
    ) -> ResolvedProcedure:
        requested_variant_id = self._requested_variant_for_candidate(
            procedure_id=procedure_id,
            candidate=candidate,
        )
        actor_capability = infer_actor_capability(
            provider=str(route.get("provider") or "").strip() or None,
            cli_tool=str(route.get("cli_tool") or "").strip() or None,
        )
        return self.procedure_registry.resolve(
            procedure_id,
            actor_capability=actor_capability,
            requested_variant_id=requested_variant_id,
        )

    def _requested_variant_for_candidate(
        self,
        *,
        procedure_id: str,
        candidate: Loop0TaskCandidate,
    ) -> str | None:
        if candidate.strategy == "fallback_only":
            return "fallback_patch" if procedure_id == "loop0-bounded-file-generation" else None
        if procedure_id == "message-task-bounded-file-generation":
            return "direct_execution"
        return "direct_patch"

    def _candidate_implementations(self) -> dict[str, dict[str, str]]:
        return {
            "verification-review": {
                "astrata/audit/__init__.py": _AUDIT_INIT_TEMPLATE,
                "astrata/audit/review.py": _AUDIT_REVIEW_TEMPLATE,
            },
            "variants-models": {
                "astrata/variants/__init__.py": _VARIANTS_INIT_TEMPLATE,
                "astrata/variants/models.py": _VARIANTS_MODELS_TEMPLATE,
            },
            "procedures-registry": {
                "astrata/procedures/__init__.py": _PROCEDURES_INIT_TEMPLATE,
                "astrata/procedures/registry.py": _PROCEDURES_REGISTRY_TEMPLATE,
            },
            "astrata-procedures-models": {
                "astrata/procedures/models.py": _PROCEDURES_MODELS_TEMPLATE,
            },
            "astrata-procedures-runtime": {
                "astrata/procedures/runtime.py": _PROCEDURES_RUNTIME_TEMPLATE,
            },
            "context-telemetry": {
                "astrata/context/__init__.py": _CONTEXT_INIT_TEMPLATE,
                "astrata/context/telemetry.py": _CONTEXT_TELEMETRY_TEMPLATE,
            },
            "astrata-context-budget": {
                "astrata/context/budget.py": _CONTEXT_BUDGET_TEMPLATE,
            },
            "astrata-context-shaping": {
                "astrata/context/shaping.py": _CONTEXT_SHAPING_TEMPLATE,
            },
            "controller-base": {
                "astrata/controllers/__init__.py": _CONTROLLERS_INIT_TEMPLATE,
                "astrata/controllers/base.py": _CONTROLLERS_BASE_TEMPLATE,
            },
            "loop0-planner": {
                "astrata/loop0/planner.py": _LOOP0_PLANNER_TEMPLATE,
            },
            "astrata-governance-constitution": {
            },
            "astrata-governance-project-specs": {
            },
            "astrata-governance-authority": {
                "astrata/governance/authority.py": _GOVERNANCE_AUTHORITY_TEMPLATE,
            },
            "astrata-records-tasks": {
                "astrata/records/tasks.py": _RECORDS_TASKS_TEMPLATE,
            },
            "astrata-records-attempts": {
                "astrata/records/attempts.py": _RECORDS_ATTEMPTS_TEMPLATE,
            },
            "astrata-records-artifacts": {
                "astrata/records/artifacts.py": _RECORDS_ARTIFACTS_TEMPLATE,
            },
            "astrata-records-communications": {
                "astrata/records/communications.py": _RECORDS_COMMUNICATIONS_TEMPLATE,
            },
            "astrata-records-handoffs": {
                "astrata/records/handoffs.py": _RECORDS_HANDOFFS_TEMPLATE,
            },
            "astrata-records-verifications": {
                "astrata/records/verifications.py": _RECORDS_VERIFICATIONS_TEMPLATE,
            },
            "astrata-records-audits": {
                "astrata/records/audits.py": _RECORDS_AUDITS_TEMPLATE,
            },
            "astrata-storage-models": {
                "astrata/storage/models.py": _STORAGE_MODELS_TEMPLATE,
            },
            "astrata-routing-router": {
                "astrata/routing/router.py": _ROUTING_ROUTER_TEMPLATE,
            },
            "astrata-execution-runner": {
                "astrata/execution/__init__.py": _EXECUTION_INIT_TEMPLATE,
                "astrata/execution/runner.py": _EXECUTION_RUNNER_TEMPLATE,
            },
            "astrata-execution-executor": {
                "astrata/execution/__init__.py": _EXECUTION_INIT_TEMPLATE,
                "astrata/execution/executor.py": _EXECUTION_EXECUTOR_TEMPLATE,
            },
            "astrata-execution-tools": {
                "astrata/execution/__init__.py": _EXECUTION_INIT_TEMPLATE,
                "astrata/execution/tools.py": _EXECUTION_TOOLS_TEMPLATE,
            },
            "astrata-verification-verifier": {
                "astrata/verification/verifier.py": _VERIFICATION_VERIFIER_TEMPLATE,
            },
            "astrata-audit-diagnostics": {
                "astrata/audit/diagnostics.py": _AUDIT_DIAGNOSTICS_TEMPLATE,
            },
            "astrata-variants-trials": {
                "astrata/variants/trials.py": _VARIANTS_TRIALS_TEMPLATE,
            },
            "astrata-variants-promotion": {
                "astrata/variants/promotion.py": _VARIANTS_PROMOTION_TEMPLATE,
            },
        }


def _try_parse_json(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def _verification_status_for(result: str) -> str:
    if result == "pass":
        return "passed"
    if result == "fail":
        return "failed"
    return "uncertain"


_AUDIT_INIT_TEMPLATE = '''"""Audit helpers for verification and disagreement review."""

from astrata.audit.review import AuditReview, ReviewFinding, open_review

__all__ = ["AuditReview", "ReviewFinding", "open_review"]
'''


_AUDIT_REVIEW_TEMPLATE = '''"""Minimal audit review records for early disagreement handling."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewFinding(BaseModel):
    finding_id: str = Field(default_factory=lambda: str(uuid4()))
    severity: Literal["low", "moderate", "high", "critical"] = "moderate"
    summary: str
    evidence: dict[str, object] = Field(default_factory=dict)
    proposed_actions: list[dict[str, object]] = Field(default_factory=list)


class AuditReview(BaseModel):
    review_id: str = Field(default_factory=lambda: str(uuid4()))
    subject_kind: str
    subject_id: str
    status: Literal["open", "resolved"] = "open"
    summary: str = ""
    findings: list[ReviewFinding] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


def open_review(
    *,
    subject_kind: str,
    subject_id: str,
    summary: str,
    findings: list[ReviewFinding] | None = None,
) -> AuditReview:
    return AuditReview(
        subject_kind=subject_kind,
        subject_id=subject_id,
        summary=summary,
        findings=findings or [],
    )
'''


_VARIANTS_INIT_TEMPLATE = '''"""Variant records for bounded experimentation."""

from astrata.variants.models import VariantRecord

__all__ = ["VariantRecord"]
'''


_VARIANTS_MODELS_TEMPLATE = '''"""Minimal variant model for bounded experimentation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VariantRecord(BaseModel):
    variant_id: str = Field(default_factory=lambda: str(uuid4()))
    subject_kind: str
    subject_id: str
    strategy: str
    status: Literal["candidate", "active", "retired"] = "candidate"
    notes: str = ""
    created_at: str = Field(default_factory=_now_iso)
'''


_PROCEDURES_INIT_TEMPLATE = '''"""Procedure helpers for reusable execution structure."""

from astrata.procedures.models import ProcedureRecord, ProcedureStructure, ProcedureTaskNode
from astrata.procedures.registry import ProcedureRegistry, ProcedureTemplate

__all__ = [
    "ProcedureRecord",
    "ProcedureStructure",
    "ProcedureTaskNode",
    "ProcedureRegistry",
    "ProcedureTemplate",
]
'''


_PROCEDURES_REGISTRY_TEMPLATE = '''"""Minimal procedure registry for Loop 0 reuse."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProcedureTemplate(BaseModel):
    procedure_id: str
    title: str
    description: str = ""
    expected_outputs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcedureRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, ProcedureTemplate] = {}

    def register(self, template: ProcedureTemplate) -> None:
        self._templates[template.procedure_id] = template

    def get(self, procedure_id: str) -> ProcedureTemplate | None:
        return self._templates.get(procedure_id)

    def list_ids(self) -> list[str]:
        return sorted(self._templates)
'''


_PROCEDURES_MODELS_TEMPLATE = '''"""Durable procedure records for reusable execution graphs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ProcedureTaskNode(BaseModel):
    node_id: str
    task_title: str
    description: str = ""
    kind: Literal["leaf", "coordination", "decomposition", "validation"] = "leaf"
    next_nodes: list[str] = Field(default_factory=list)
    branch_condition: str | None = None
    retry_limit: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcedureStructure(BaseModel):
    entry_node_id: str
    nodes: list[ProcedureTaskNode] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def node_map(self) -> dict[str, ProcedureTaskNode]:
        return {node.node_id: node for node in self.nodes}


class ProcedureRecord(BaseModel):
    procedure_id: str
    title: str
    description: str = ""
    status: Literal["good", "degraded", "broken"] = "good"
    lifecycle_state: Literal["draft", "tested", "vetted", "retired"] = "draft"
    install_state: Literal["proposed", "shadow", "active", "disabled", "superseded"] = "proposed"
    provenance: dict[str, Any] = Field(default_factory=dict)
    applicability: dict[str, Any] = Field(default_factory=dict)
    permissions_profile: dict[str, Any] = Field(default_factory=dict)
    entry_conditions: dict[str, Any] = Field(default_factory=dict)
    success_contract: dict[str, Any] = Field(default_factory=dict)
    failure_contract: dict[str, Any] = Field(default_factory=dict)
    artifact_contract: dict[str, Any] = Field(default_factory=dict)
    structure: ProcedureStructure
    notes: list[str] = Field(default_factory=list)
'''


_PROCEDURES_RUNTIME_TEMPLATE = '''"""Minimal runtime helpers for executing procedure structure."""

from __future__ import annotations

from astrata.procedures.models import ProcedureRecord, ProcedureTaskNode


class ProcedureRuntime:
    def __init__(self, procedure: ProcedureRecord) -> None:
        self.procedure = procedure
        self._node_map = procedure.structure.node_map()

    def entry_node(self) -> ProcedureTaskNode:
        return self._node_map[self.procedure.structure.entry_node_id]

    def get_node(self, node_id: str) -> ProcedureTaskNode | None:
        return self._node_map.get(node_id)

    def next_nodes(self, node_id: str) -> list[ProcedureTaskNode]:
        node = self.get_node(node_id)
        if node is None:
            return []
        resolved: list[ProcedureTaskNode] = []
        for next_id in node.next_nodes:
            next_node = self.get_node(next_id)
            if next_node is not None:
                resolved.append(next_node)
        return resolved

    def is_leaf(self, node_id: str) -> bool:
        node = self.get_node(node_id)
        if node is None:
            return False
        return node.kind == "leaf" and not node.next_nodes
'''


_CONTEXT_INIT_TEMPLATE = '''"""Context pressure helpers for early routing decisions."""

from astrata.context.telemetry import ContextTelemetry

__all__ = ["ContextTelemetry"]
'''


_CONTEXT_TELEMETRY_TEMPLATE = '''"""Minimal context telemetry for early token-pressure tracking."""

from __future__ import annotations

from pydantic import BaseModel


class ContextTelemetry(BaseModel):
    window_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def pressure(self) -> float:
        if self.window_tokens <= 0:
            return 0.0
        return min(1.0, (self.prompt_tokens + self.completion_tokens) / self.window_tokens)
'''


_CONTEXT_BUDGET_TEMPLATE = '''"""Minimal context budget helpers."""

from __future__ import annotations

from pydantic import BaseModel


class ContextBudget(BaseModel):
    max_window_tokens: int
    reserved_response_tokens: int = 0

    def available_prompt_tokens(self) -> int:
        remaining = self.max_window_tokens - self.reserved_response_tokens
        return max(0, remaining)
'''


_CONTEXT_SHAPING_TEMPLATE = '''"""Minimal context shaping helpers."""

from __future__ import annotations

from astrata.context.budget import ContextBudget
from astrata.context.telemetry import ContextTelemetry


def should_compact_context(*, telemetry: ContextTelemetry, budget: ContextBudget, threshold: float = 0.85) -> bool:
    if budget.max_window_tokens <= 0:
        return False
    return telemetry.pressure >= threshold
'''


_CONTROLLERS_INIT_TEMPLATE = '''"""Controller interfaces for federated control."""

from astrata.controllers.base import ControllerDecision, ControllerEnvelope

__all__ = ["ControllerDecision", "ControllerEnvelope"]
'''


_CONTROLLERS_BASE_TEMPLATE = '''"""Minimal controller records for federated handoff decisions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ControllerEnvelope(BaseModel):
    controller_id: str
    task_id: str
    priority: int = 0
    urgency: int = 0
    risk: str = "moderate"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControllerDecision(BaseModel):
    status: Literal["accepted", "deferred", "blocked", "refused"] = "accepted"
    reason: str = ""
    followup_actions: list[dict[str, Any]] = Field(default_factory=list)
'''


_LOOP0_PLANNER_TEMPLATE = '''"""Minimal Loop 0 planner for summarizing repo state and next steps."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from astrata.verification.basic import inspect_expected_paths


class PlannerSnapshot(BaseModel):
    candidate_key: str
    missing_paths: list[str] = Field(default_factory=list)
    existing_paths: list[str] = Field(default_factory=list)


class Loop0Planner:
    def summarize_candidate(self, project_root: Path, candidate_key: str, expected_paths: list[str]) -> PlannerSnapshot:
        inspection = inspect_expected_paths(project_root, expected_paths)
        return PlannerSnapshot(
            candidate_key=candidate_key,
            missing_paths=list(inspection["missing"]),
            existing_paths=list(inspection["existing"]),
        )
'''


_GOVERNANCE_CONSTITUTION_TEMPLATE = '''"""Constitution loading helpers."""

from __future__ import annotations

from pathlib import Path


def load_constitution_text(project_root: Path) -> str:
    spec_path = project_root / "spec.md"
    return spec_path.read_text() if spec_path.exists() else ""
'''


_GOVERNANCE_PROJECT_SPECS_TEMPLATE = '''"""Project spec loading helpers."""

from __future__ import annotations

from pathlib import Path


def load_project_spec_text(project_root: Path, spec_name: str = "project-spec.md") -> str:
    spec_path = project_root / spec_name
    return spec_path.read_text() if spec_path.exists() else ""
'''


_GOVERNANCE_AUTHORITY_TEMPLATE = '''"""Authority chain helpers for constitutional control."""

from __future__ import annotations

from pydantic import BaseModel


class AuthorityChain(BaseModel):
    source: str = "user"
    delegated_via: str = "constitution"

    @property
    def summary(self) -> str:
        return f"{self.source} -> {self.delegated_via}"
'''


_RECORDS_TASKS_TEMPLATE = '''"""Task record alias helpers."""

from astrata.records.models import TaskRecord

__all__ = ["TaskRecord"]
'''


_RECORDS_ATTEMPTS_TEMPLATE = '''"""Attempt record alias helpers."""

from astrata.records.models import AttemptRecord

__all__ = ["AttemptRecord"]
'''


_RECORDS_ARTIFACTS_TEMPLATE = '''"""Artifact record alias helpers."""

from astrata.records.models import ArtifactRecord

__all__ = ["ArtifactRecord"]
'''


_RECORDS_COMMUNICATIONS_TEMPLATE = '''"""Communication record models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CommunicationRecord(BaseModel):
    channel: str
    sender: str
    recipient: str
    payload: dict[str, object] = Field(default_factory=dict)
'''


_RECORDS_HANDOFFS_TEMPLATE = '''"""Handoff record models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HandoffRecord(BaseModel):
    source_controller: str
    target_controller: str
    task_id: str
    metadata: dict[str, object] = Field(default_factory=dict)
'''


_RECORDS_VERIFICATIONS_TEMPLATE = '''"""Verification record alias helpers."""

from astrata.records.models import VerificationRecord

__all__ = ["VerificationRecord"]
'''


_RECORDS_AUDITS_TEMPLATE = '''"""Audit record alias helpers."""

from astrata.audit.review import AuditReview, ReviewFinding

__all__ = ["AuditReview", "ReviewFinding"]
'''


_STORAGE_MODELS_TEMPLATE = '''"""Storage-layer aliases for early durable models."""

from astrata.records.models import ArtifactRecord, AttemptRecord, TaskRecord, VerificationRecord

__all__ = ["ArtifactRecord", "AttemptRecord", "TaskRecord", "VerificationRecord"]
'''


_ROUTING_ROUTER_TEMPLATE = '''"""Routing helpers for choosing execution routes."""

from astrata.routing.policy import ExecutionRoute, RouteChooser

__all__ = ["ExecutionRoute", "RouteChooser"]
'''


_EXECUTION_INIT_TEMPLATE = '''"""Execution helpers for bounded real-world actions."""

from astrata.execution.executor import ExecutionResult
from astrata.execution.runner import ExecutionRequest, ExecutionRunner
from astrata.execution.tools import ToolInvocation

__all__ = ["ExecutionRequest", "ExecutionResult", "ExecutionRunner", "ToolInvocation"]
'''


_EXECUTION_RUNNER_TEMPLATE = '''"""Minimal execution runner for bounded tasks."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExecutionRequest(BaseModel):
    task_id: str
    command: str
    cwd: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class ExecutionRunner:
    def normalize(self, request: ExecutionRequest) -> ExecutionRequest:
        return request
'''


_EXECUTION_EXECUTOR_TEMPLATE = '''"""Minimal execution result models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExecutionResult(BaseModel):
    status: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    metadata: dict[str, object] = Field(default_factory=dict)
'''


_EXECUTION_TOOLS_TEMPLATE = '''"""Minimal tool invocation records."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ToolInvocation(BaseModel):
    tool_name: str
    arguments: dict[str, object] = Field(default_factory=dict)
'''


_VERIFICATION_VERIFIER_TEMPLATE = '''"""Minimal verifier entrypoints."""

from __future__ import annotations

from pathlib import Path

from astrata.verification.basic import VerificationResult, verify_expected_paths


def verify_paths(project_root: Path, expected_paths: list[str]) -> VerificationResult:
    return verify_expected_paths(project_root, expected_paths)
'''


_AUDIT_DIAGNOSTICS_TEMPLATE = '''"""Minimal diagnostics helpers for audit findings."""

from __future__ import annotations

from astrata.audit.review import ReviewFinding


def summarize_findings(findings: list[ReviewFinding]) -> str:
    if not findings:
        return "No findings."
    return "; ".join(finding.summary for finding in findings)
'''


_VARIANTS_TRIALS_TEMPLATE = '''"""Minimal variant trial helpers."""

from __future__ import annotations

from pydantic import BaseModel


class TrialResult(BaseModel):
    variant_id: str
    score: float
'''


_VARIANTS_PROMOTION_TEMPLATE = '''"""Minimal variant promotion helpers."""

from __future__ import annotations

from astrata.variants.trials import TrialResult


def promote_best(results: list[TrialResult]) -> TrialResult | None:
    if not results:
        return None
    return max(results, key=lambda result: result.score)
'''
