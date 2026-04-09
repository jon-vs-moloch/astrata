"""Minimal Loop 0 runner."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrata.config.settings import Settings
from astrata.comms.intake import normalize_derived_task_proposal
from astrata.comms.lanes import HandoffLane, OperatorMessageLane
from astrata.controllers.base import ControllerEnvelope
from astrata.controllers.coordinator import CoordinatorController
from astrata.controllers.local_executor import LocalExecutorController
from astrata.governance.documents import GovernanceBundle, load_governance_bundle
from astrata.loop0.planner import Loop0Planner, PlannerSnapshot
from astrata.procedures.execution import BoundedFileGenerationProcedure, ProcedureExecutionRequest
from astrata.procedures.health import RouteHealthStore
from astrata.providers.base import CompletionRequest, Message
from astrata.providers.registry import ProviderRegistry, build_default_registry
from astrata.records.handoffs import HandoffRecord
from astrata.records.models import ArtifactRecord, AttemptRecord, TaskRecord, VerificationRecord
from astrata.routing.advisor import RoutePerformanceAdvisor
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
        self.router = RouteChooser(self.registry)
        limits = default_source_limits()
        limits["codex"] = settings.runtime_limits.codex_requests_per_hour
        limits["cli:codex-cli"] = settings.runtime_limits.codex_requests_per_hour
        limits["cli:kilocode"] = settings.runtime_limits.kilocode_requests_per_hour
        limits["cli:gemini-cli"] = settings.runtime_limits.gemini_requests_per_hour
        limits["cli:claude-code"] = settings.runtime_limits.claude_requests_per_hour
        limits["openai"] = settings.runtime_limits.openai_requests_per_hour
        limits["google"] = settings.runtime_limits.google_requests_per_hour
        limits["anthropic"] = settings.runtime_limits.anthropic_requests_per_hour
        limits["custom"] = settings.runtime_limits.custom_requests_per_hour
        health_store = RouteHealthStore(settings.paths.data_dir / "route_health.json")
        quota_policy = QuotaPolicy(db=db, limits_per_source=limits, registry=self.registry)
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
        self.operator_lane = OperatorMessageLane(db=self.db)
        self.planner = Loop0Planner()
        self.prioritizer = WorkPrioritizer()
        self.governance: GovernanceBundle = load_governance_bundle(settings.paths.project_root)

    def next_candidate(self) -> Loop0TaskCandidate | None:
        assessment = self.next_candidate_assessment()
        return None if assessment is None else assessment.candidate

    def next_candidate_assessment(self) -> Loop0CandidateAssessment | None:
        self._reconcile_pending_tasks()
        work_items: list[ScheduledWorkItem] = []
        for candidate in self._pending_message_task_candidates():
            scheduling_metadata = self._scheduling_metadata_for_task_payload(dict(candidate.metadata or {}))
            assessment = Loop0CandidateAssessment(
                candidate=candidate,
                inspection={"task_record": dict(candidate.metadata or {})},
                verification=VerificationResult(
                    result="pass",
                    confidence=0.8,
                    summary="Inbound task is pending and eligible for unified scheduling.",
                    evidence={"task_record": dict(candidate.metadata or {})},
                ),
            )
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

    def _reconcile_pending_tasks(self) -> list[TaskRecord]:
        reconciled: list[TaskRecord] = []
        for task_payload in self.db.list_records("tasks"):
            if task_payload.get("status") != "pending":
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
        title = str(task_payload.get("title") or "")
        description = str(task_payload.get("description") or "")
        task_id = str(task_payload.get("task_id") or "")
        scheduling: dict[str, Any] = {
            "preferred_cli_tools": list(route_preferences.get("preferred_cli_tools") or []),
            "preferred_providers": list(route_preferences.get("preferred_providers") or []),
            "retry_count": int(provenance.get("retry_count") or 0),
            "completion_type": str(completion_policy.get("type") or ""),
            "mentions_repo_file": self._text_mentions_repo_file(title, description),
            "historical_file_write": self._task_has_historical_file_write(task_id),
            "commentary_only_history": self._task_has_commentary_only_history(task_id),
            "task_age_hours": self._task_age_hours(task_payload),
            "is_followup": provenance.get("source") == "message_task_followup",
            "likely_satisfied": self._task_likely_satisfied(task_payload),
            "closure_pressure": self._task_closure_pressure(task_payload),
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
        candidates: list[Loop0TaskCandidate] = []
        for task_payload in self.db.list_records("tasks"):
            provenance = dict(task_payload.get("provenance") or {})
            if task_payload.get("status") != "pending":
                continue
            if provenance.get("source") not in EXECUTABLE_MESSAGE_TASK_SOURCES:
                continue
            if self._is_low_signal_message_task(task_payload):
                continue
            if self._is_duplicate_pending_task(task_payload):
                continue
            task_id = str(task_payload.get("task_id") or "").strip()
            if not task_id:
                continue
            candidates.append(
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
        return candidates

    def _retry_task_candidates(self) -> list[Loop0TaskCandidate]:
        tasks_by_id = {
            str(task_payload.get("task_id") or ""): task_payload
            for task_payload in self.db.list_records("tasks")
        }
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

    def _is_low_signal_message_task(self, task_payload: dict[str, Any]) -> bool:
        title = str(task_payload.get("title") or "").strip().lower()
        description = str(task_payload.get("description") or "").strip().lower()
        parts = [part for part in (title, description) if part]
        if not parts:
            return True
        low_signal_phrases = {
            "hello",
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
            request = CompletionRequest(
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
        }
        if candidate.risk not in {"high", "critical"}:
            preferences["avoided_providers"] = [
                *preferences["avoided_providers"],
                "codex",
            ]
        return preferences

    def _task_class_for_candidate(self, candidate: Loop0TaskCandidate) -> str:
        metadata = dict(candidate.metadata or {})
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
        assessment = self.next_candidate_assessment()
        if assessment is None:
            self.operator_lane.send(
                sender="astrata.loop0",
                kind="notice",
                intent="loop0_status",
                payload={"status": "complete", "message": "No missing or weak Loop 0 candidate paths found."},
            )
            return {"status": "complete", "message": "No missing Loop 0 candidate paths found."}
        candidate = assessment.candidate

        recommendation = self.recommend_next_step(assessment)
        coordination = recommendation.get("coordination") or self.coordinate_candidate(candidate)
        implementation = self._apply_candidate(candidate, coordination=coordination)
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
            else ("blocked" if implementation.get("degraded_reason") else "failed")
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
                else f"Could not apply candidate {candidate.key}"
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
            ended_at=_now_iso(),
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
        )
        review_artifact = ArtifactRecord(
            artifact_type="loop0_verification_review",
            title=f"Loop 0 verification review: {candidate.title}",
            description="Second-pass audit of whether verification matched observed repository state.",
            content_summary=verification_review.model_dump_json(indent=2),
            provenance={"task_id": task.task_id, "attempt_id": attempt.attempt_id},
            status="good" if not verification_review.findings else "degraded",
        )
        self.db.upsert_artifact(review_artifact)

        self.db.upsert_verification(verification)

        operator_message = self.operator_lane.send(
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
            "verification": verification.model_dump(mode="json"),
            "operator_message": operator_message.model_dump(mode="json"),
        }

    def run_steps(self, max_steps: int) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for _ in range(max_steps):
            result = self.run_once()
            results.append(result)
            if result.get("status") == "complete":
                break
            implementation = result.get("implementation_report", {})
            content = implementation.get("content_summary")
            if isinstance(content, str) and '"status": "unsupported"' in content:
                break
        final_status = results[-1]["status"] if results else "complete"
        return {"status": final_status, "steps": results}

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
            else "pending"
        )
        if candidate.strategy == "message_task" and candidate.metadata:
            payload = dict(candidate.metadata)
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
                and implementation.get("generation_mode") == "provider"
            ):
                return VerificationResult(
                    result="pass",
                    confidence=0.9,
                    summary="Inbound task was selected from the unified queue and executed through a routed assistant lane.",
                    evidence={"implementation": implementation},
                )
            if status == "applied" and implementation.get("emitted_communication_id"):
                return VerificationResult(
                    result="uncertain",
                    confidence=0.6,
                    summary="Inbound task emitted an operator message, but the assistant lane did not complete the work path cleanly.",
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
            return self._apply_message_task(candidate, route=route)
        request = ProcedureExecutionRequest(
            procedure_id="loop0-bounded-file-generation",
            title=candidate.title,
            description=candidate.description,
            expected_paths=list(candidate.expected_paths),
            available_docs=list(self.governance.planning_docs.keys()),
            inspection=inspect_expected_paths(
                self.settings.paths.project_root,
                list(candidate.expected_paths),
            ),
            risk=candidate.risk,
            priority=candidate.priority,
            urgency=candidate.urgency,
            preferred_provider=self._preferred_provider_for_strategy(candidate),
            avoided_providers=self._avoided_providers_for_strategy(candidate),
            preferred_cli_tool=self._preferred_cli_tool_for_strategy(candidate),
            avoided_cli_tools=self._avoided_cli_tools_for_strategy(candidate),
        )
        if route:
            request = request.model_copy(
                update={
                    "preferred_provider": str(route.get("provider") or request.preferred_provider or "").strip() or request.preferred_provider,
                    "preferred_cli_tool": str(route.get("cli_tool") or request.preferred_cli_tool or "").strip() or request.preferred_cli_tool,
                }
            )
        baseline_inspection = (
            inspect_weak_expected_paths(self.settings.paths.project_root, list(candidate.expected_paths))
            if candidate.strategy == "strengthen"
            else inspect_expected_paths(self.settings.paths.project_root, list(candidate.expected_paths))
        )
        result = self.procedures.execute(
            project_root=self.settings.paths.project_root,
            request=request,
            fallback_builder=lambda procedure_request: self._candidate_implementations().get(
                candidate.key, {}
            ),
            force_fallback_only=(candidate.strategy == "fallback_only"),
        )
        payload = result.model_dump(mode="json")
        payload["baseline_inspection"] = baseline_inspection
        return payload

    def _apply_message_task(
        self,
        candidate: Loop0TaskCandidate,
        *,
        route: dict[str, Any],
    ) -> dict[str, Any]:
        task_payload = dict(candidate.metadata or {})
        completion_policy = dict(task_payload.get("completion_policy") or {})
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
                CompletionRequest(
                    model=route.get("model"),
                    messages=self._message_task_prompt(candidate, task_payload),
                    metadata={
                        "cli_tool": route.get("cli_tool"),
                    },
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

        operator_response = self._extract_operator_response(response.content)
        followup_tasks = self._extract_followup_tasks(response.content, task_payload)
        derived_artifact = self._extract_message_artifact(response.content, candidate, task_payload)
        notice = self.operator_lane.send(
            sender=lane_sender,
            recipient="operator",
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
                "assistant_output": operator_response,
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
            "assistant_output": operator_response,
            "followup_tasks": followup_tasks,
            "derived_artifact": derived_artifact,
            "emitted_communication_id": notice.communication_id,
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
        request = ProcedureExecutionRequest(
            procedure_id="message-task-bounded-file-generation",
            title=candidate.title,
            description=candidate.description,
            expected_paths=expected_paths,
            available_docs=list(self.governance.planning_docs.keys()),
            inspection={"task_record": task_payload, "inferred_expected_paths": expected_paths},
            risk=candidate.risk,
            priority=candidate.priority,
            urgency=candidate.urgency,
            preferred_provider=str(route.get("provider") or "").strip() or None,
            preferred_cli_tool=str(route.get("cli_tool") or "").strip() or None,
        )
        result = self.procedures.execute(
            project_root=self.settings.paths.project_root,
            request=request,
            fallback_builder=None,
            force_fallback_only=False,
        )
        payload = result.model_dump(mode="json")
        payload["baseline_inspection"] = baseline_inspection
        if result.status != "applied":
            return None
        notice = self.operator_lane.send(
            sender=self._message_task_lane_context(task_payload)[0],
            recipient="operator",
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
            notice = self.operator_lane.send(
                sender=lane_sender,
                recipient="operator",
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
            notice = self.operator_lane.send(
                sender=lane_sender,
                recipient="operator",
                conversation_id=conversation_id,
                kind="notice",
                intent="inbound_task_selected",
                payload={
                    "task_id": task_id,
                    "title": candidate.title,
                    "description": candidate.description,
                    "completion_policy": completion_policy,
                    "message": "Astrata selected this inbound task, but the assistant lane degraded and fell back to a bounded operator notice.",
                    "provider_error": provider_error,
                },
                priority=candidate.priority,
                urgency=candidate.urgency,
                related_task_ids=[task_id],
            )
        return {
            "status": "applied",
            "reason": "Unified queue selected an inbound task, but assistant execution degraded to a bounded operator-lane fallback.",
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
            conversation_id = self.operator_lane.default_conversation_id(lane if lane in {"prime", "local"} else "system")
        return lane, conversation_id

    def _message_task_prompt(
        self,
        candidate: Loop0TaskCandidate,
        task_payload: dict[str, Any],
    ) -> list[Message]:
        completion_policy = dict(task_payload.get("completion_policy") or {})
        provenance = dict(task_payload.get("provenance") or {})
        success_criteria = dict(task_payload.get("success_criteria") or {})
        system_prompt = (
            "You are Astrata handling an inbound operator-derived task. "
            "Respond concisely and usefully. Return strict JSON with keys "
            "`operator_response`, `followup_tasks`, and `artifact`. "
            "`operator_response` should be the bounded next response or question for the operator. "
            "`followup_tasks` should be a short list of at most 2 governed tasks only when genuinely helpful. "
            "Each follow-up task should include: title, description, priority, urgency, risk, completion_type, "
            "and, when clear, delta_kind (`input_vs_spec` or `spec_vs_reality`) plus delta_summary. "
            "`artifact` should summarize the durable knowledge produced by this step, with keys: "
            "title, summary, confidence, findings. "
            "If no follow-up work is needed, return an empty list. "
            "Do not mention internal quotas or routing unless directly relevant."
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
        for item in raw_tasks[:2]:
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
                    "route_preferences": dict(
                        dict(task_payload.get("completion_policy") or {}).get("route_preferences") or {}
                    ),
                }
            )
        return normalized

    def _extract_operator_response(self, response_text: str) -> str:
        parsed = _try_parse_json(response_text) or {}
        operator_response = str(
            parsed.get("operator_response") or parsed.get("response") or response_text
        ).strip()
        return operator_response or response_text.strip()

    def _extract_message_artifact(
        self,
        response_text: str,
        candidate: Loop0TaskCandidate,
        task_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        parsed = _try_parse_json(response_text) or {}
        artifact = parsed.get("artifact")
        completion_type = str(dict(task_payload.get("completion_policy") or {}).get("type") or "").strip()
        operator_response = self._extract_operator_response(response_text)
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
            "summary": str(artifact.get("summary") or operator_response),
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
        materialized: list[TaskRecord] = []
        for spec in specs[:2]:
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
            )
            task = TaskRecord(
                title=proposal.title,
                description=proposal.description,
                priority=proposal.priority,
                urgency=proposal.urgency,
                risk=proposal.risk,
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
                "astrata/governance/constitution.py": _GOVERNANCE_CONSTITUTION_TEMPLATE,
            },
            "astrata-governance-project-specs": {
                "astrata/governance/project_specs.py": _GOVERNANCE_PROJECT_SPECS_TEMPLATE,
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
