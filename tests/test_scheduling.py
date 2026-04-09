from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from astrata.config.settings import load_settings
from astrata.loop0.runner import Loop0Runner
from astrata.providers.base import CompletionRequest, CompletionResponse, Provider
from astrata.providers.registry import ProviderRegistry
from astrata.records.models import ArtifactRecord, AttemptRecord, TaskRecord
from astrata.scheduling.prioritizer import WorkPrioritizer
from astrata.scheduling.work_pool import ScheduledWorkItem
from astrata.storage.db import AstrataDatabase


class _DeferredCodexProvider(Provider):
    @property
    def name(self) -> str:
        return "codex"

    def is_configured(self) -> bool:
        return True

    def default_model(self) -> str | None:
        return "gpt-5.4"

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(provider="codex", model="gpt-5.4", content="OK")

    def get_quota_windows(self):
        return [
            {
                "requests_remaining": 1,
                "requests_limit": 10000,
                "reset_time": "2099-01-08T00:00:00+00:00",
                "window_duration_seconds": 604800,
                "source": "weekly",
            }
        ]


class _CheapCliProvider(Provider):
    @property
    def name(self) -> str:
        return "cli"

    def is_configured(self) -> bool:
        return True

    def default_model(self) -> str | None:
        return None

    def available_tools(self) -> list[str]:
        return ["kilocode"]

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(
            provider="cli",
            model=str(request.metadata.get("cli_tool") or "kilocode"),
            content='{"operator_response":"OK","followup_tasks":[],"artifact":{"title":"note","summary":"ok","confidence":0.8,"findings":[]}}',
            raw={},
        )


def test_prioritizer_prefers_pending_message_task_over_planner_candidate():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        db.upsert_task(
            TaskRecord(
                task_id="message-task-priority",
                title="Execute operator request",
                description="Process the inbound operator request through the unified queue.",
                priority=8,
                urgency=4,
                provenance={"source": "message_intake", "source_communication_id": "msg-priority"},
                permissions={},
                risk="low",
                status="pending",
                success_criteria={"message_addressed": True},
                completion_policy={"type": "respond_or_execute"},
                created_at="2026-04-08T00:00:00+00:00",
                updated_at="2026-04-08T00:00:00+00:00",
            )
        )
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        assessment = runner.next_candidate_assessment()
        assert assessment is not None
        assert assessment.candidate.strategy == "message_task"
        assert assessment.candidate.key == "task:message-task-priority"


def test_prioritizer_surfaces_retry_candidate_for_failed_task():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        db.upsert_task(
            TaskRecord(
                task_id="retry-task",
                title="Retry me",
                description="Retry the failed bounded task.",
                priority=4,
                urgency=2,
                provenance={"source": "message_intake", "source_communication_id": "msg-retry"},
                permissions={},
                risk="low",
                status="blocked",
                success_criteria={"message_addressed": True},
                completion_policy={"type": "respond_or_execute"},
                created_at="2026-04-08T00:00:00+00:00",
                updated_at="2026-04-08T00:00:00+00:00",
            )
        )
        db.upsert_attempt(
            AttemptRecord(
                task_id="retry-task",
                actor="loop0:cli",
                outcome="blocked",
                result_summary="Route degraded.",
                degraded_reason="route_health:broken",
                resource_usage={"route": {"provider": "cli", "cli_tool": "kilocode"}},
                started_at="2026-04-08T01:00:00+00:00",
                ended_at="2026-04-08T01:01:00+00:00",
            )
        )
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        candidates = runner._retry_task_candidates()
        assert candidates
        assert candidates[0].key == "retry:retry-task"


def test_prioritizer_surfaces_artifact_finding_candidate():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        db.upsert_artifact(
            ArtifactRecord(
                artifact_type="spec_review",
                title="Spec review artifact",
                content_summary='{"confidence": 0.9, "findings": ["Add bounded validation to the intake path."]}',
                provenance={"task_id": "parent-task"},
                created_at="2026-04-08T02:00:00+00:00",
                updated_at="2026-04-08T02:00:00+00:00",
            )
        )
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        candidates = runner._artifact_finding_candidates()
        assert candidates
        assert candidates[0].source_task_id.startswith("artifact-finding-")


def test_prioritizer_prefers_cheap_lane_when_priority_is_equal():
    prioritizer = WorkPrioritizer()
    base_candidate = SimpleNamespace(priority=5, urgency=3)
    cheap = ScheduledWorkItem(
        candidate=base_candidate,
        inspection={},
        verification=None,
        source_kind="message_task",
        created_at="2026-04-08T00:00:00+00:00",
        metadata={"preferred_cli_tools": ["kilocode"]},
    )
    expensive = ScheduledWorkItem(
        candidate=base_candidate,
        inspection={},
        verification=None,
        source_kind="message_task",
        created_at="2026-04-08T00:00:00+00:00",
        metadata={"preferred_providers": ["codex"]},
    )
    assert prioritizer.score(cheap) > prioritizer.score(expensive)


def test_prioritizer_prefers_higher_confidence_artifact_finding_when_priority_is_equal():
    prioritizer = WorkPrioritizer()
    candidate = SimpleNamespace(priority=5, urgency=3)
    low = ScheduledWorkItem(
        candidate=candidate,
        inspection={},
        verification=None,
        source_kind="artifact_finding",
        created_at="2026-04-08T00:00:00+00:00",
        metadata={"artifact_confidence": 0.76},
    )
    high = ScheduledWorkItem(
        candidate=candidate,
        inspection={},
        verification=None,
        source_kind="artifact_finding",
        created_at="2026-04-08T00:00:00+00:00",
        metadata={"artifact_confidence": 0.91},
    )
    assert prioritizer.score(high) > prioritizer.score(low)


def test_prioritizer_prefers_more_likely_system_change_when_priority_is_equal():
    prioritizer = WorkPrioritizer()
    candidate = SimpleNamespace(priority=5, urgency=3)
    implementation_like = ScheduledWorkItem(
        candidate=candidate,
        inspection={},
        verification=None,
        source_kind="message_task",
        created_at="2026-04-08T00:00:00+00:00",
        metadata={
            "completion_type": "respond_or_execute",
            "mentions_repo_file": True,
            "historical_file_write": True,
        },
    )
    commentary_like = ScheduledWorkItem(
        candidate=candidate,
        inspection={},
        verification=None,
        source_kind="message_task",
        created_at="2026-04-08T00:00:00+00:00",
        metadata={
            "completion_type": "request_clarification",
            "mentions_repo_file": False,
            "commentary_only_history": True,
        },
    )
    assert prioritizer.score(implementation_like) > prioritizer.score(commentary_like)


def test_runner_exposes_system_change_metadata_for_file_task():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        task = TaskRecord(
            task_id="metadata-task",
            title="Execute: update intake.py",
            description="Update intake.py to strengthen validation for inbound operator messages.",
            priority=5,
            urgency=3,
            provenance={"source": "message_intake", "source_communication_id": "msg-meta"},
            permissions={},
            risk="low",
            status="pending",
            success_criteria={"message_addressed": True},
            completion_policy={"type": "respond_or_execute"},
            created_at="2026-04-08T00:00:00+00:00",
            updated_at="2026-04-08T00:00:00+00:00",
        )
        db.upsert_task(task)
        db.upsert_attempt(
            AttemptRecord(
                task_id="metadata-task",
                actor="loop0:cli",
                outcome="succeeded",
                result_summary="Wrote file.",
                resource_usage={"implementation": {"written_paths": ["astrata/comms/intake.py"]}},
                started_at="2026-04-08T01:00:00+00:00",
                ended_at="2026-04-08T01:01:00+00:00",
            )
        )
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        metadata = runner._scheduling_metadata_for_task_payload(task.model_dump(mode="json"))
        assert metadata["mentions_repo_file"] is True
        assert metadata["historical_file_write"] is True
        assert metadata["completion_type"] == "respond_or_execute"


def test_prioritizer_prefers_stale_pending_work_when_priority_is_equal():
    prioritizer = WorkPrioritizer()
    candidate = SimpleNamespace(priority=5, urgency=3)
    fresh = ScheduledWorkItem(
        candidate=candidate,
        inspection={},
        verification=None,
        source_kind="message_task",
        created_at="2026-04-08T00:00:00+00:00",
        metadata={"task_age_hours": 0.2, "closure_pressure": 1},
    )
    stale = ScheduledWorkItem(
        candidate=candidate,
        inspection={},
        verification=None,
        source_kind="message_task",
        created_at="2026-04-08T00:00:00+00:00",
        metadata={"task_age_hours": 14.0, "closure_pressure": 3},
    )
    assert prioritizer.score(stale) > prioritizer.score(fresh)


def test_prioritizer_penalizes_likely_satisfied_pending_work():
    prioritizer = WorkPrioritizer()
    candidate = SimpleNamespace(priority=5, urgency=3)
    live = ScheduledWorkItem(
        candidate=candidate,
        inspection={},
        verification=None,
        source_kind="message_task",
        created_at="2026-04-08T00:00:00+00:00",
        metadata={"closure_pressure": 2, "likely_satisfied": False},
    )
    satisfied = ScheduledWorkItem(
        candidate=candidate,
        inspection={},
        verification=None,
        source_kind="message_task",
        created_at="2026-04-08T00:00:00+00:00",
        metadata={"closure_pressure": 2, "likely_satisfied": True},
    )
    assert prioritizer.score(live) > prioritizer.score(satisfied)


def test_runner_marks_pending_task_likely_satisfied_when_sibling_completed():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        completed = TaskRecord(
            task_id="complete-sibling",
            title="Execute operator request",
            description="Process the inbound operator request through the unified queue.",
            priority=5,
            urgency=3,
            provenance={"source": "message_intake", "source_communication_id": "msg-shared"},
            permissions={},
            risk="low",
            status="complete",
            success_criteria={"message_addressed": True},
            completion_policy={"type": "respond_or_execute"},
            created_at="2026-04-08T00:00:00+00:00",
            updated_at="2026-04-08T01:00:00+00:00",
        )
        pending = TaskRecord(
            task_id="pending-sibling",
            title="Review communication/task translation path",
            description="Inspect whether the inbound operator request should also improve Astrata's communication-to-task intake path.",
            priority=4,
            urgency=2,
            provenance={"source": "message_intake", "source_communication_id": "msg-shared"},
            permissions={},
            risk="low",
            status="pending",
            success_criteria={"message_addressed": True},
            completion_policy={"type": "review_or_execute"},
            created_at="2026-04-08T00:00:00+00:00",
            updated_at="2026-04-08T01:00:00+00:00",
        )
        db.upsert_task(completed)
        db.upsert_task(pending)
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        metadata = runner._scheduling_metadata_for_task_payload(pending.model_dump(mode="json"))
        assert metadata["likely_satisfied"] is True


def test_runner_does_not_mark_followup_task_satisfied_only_because_parent_message_completed():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        completed = TaskRecord(
            task_id="complete-parent-message",
            title="Execute operator request",
            description="Process the inbound operator request through the unified queue.",
            priority=5,
            urgency=3,
            provenance={"source": "message_intake", "source_communication_id": "msg-parent"},
            permissions={},
            risk="low",
            status="complete",
            success_criteria={"message_addressed": True},
            completion_policy={"type": "respond_or_execute"},
            created_at="2026-04-08T00:00:00+00:00",
            updated_at="2026-04-08T01:00:00+00:00",
        )
        followup = TaskRecord(
            task_id="pending-followup-child",
            title="Implement Phase 0 Plan",
            description="Execute the phase 0 plan for initial deployment.",
            priority=5,
            urgency=3,
            provenance={
                "source": "message_task_followup",
                "source_communication_id": "msg-parent",
                "parent_task_id": "complete-parent-message",
            },
            permissions={},
            risk="low",
            status="pending",
            success_criteria={"message_addressed": True},
            completion_policy={"type": "respond_or_execute"},
            created_at="2026-04-08T01:05:00+00:00",
            updated_at="2026-04-08T01:05:00+00:00",
        )
        db.upsert_task(completed)
        db.upsert_task(followup)
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        metadata = runner._scheduling_metadata_for_task_payload(followup.model_dump(mode="json"))
        assert metadata["likely_satisfied"] is False


def test_reconcile_marks_low_signal_pending_task_superseded():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        low_signal = TaskRecord(
            task_id="hello-task",
            title="hello from operator",
            description="hello",
            priority=1,
            urgency=1,
            provenance={"source": "message_intake", "source_communication_id": "msg-hello"},
            permissions={},
            risk="low",
            status="pending",
            success_criteria={"message_addressed": True},
            completion_policy={"type": "respond_or_execute"},
            created_at="2026-04-08T00:00:00+00:00",
            updated_at="2026-04-08T00:00:00+00:00",
        )
        db.upsert_task(low_signal)
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        reconciled = runner._reconcile_pending_tasks()
        assert reconciled
        stored = [task for task in db.list_records("tasks") if task.get("task_id") == "hello-task"][0]
        assert stored["status"] == "superseded"
        assert stored["provenance"]["closure"]["reason"] == "low_signal_pending_work"


def test_reconcile_marks_live_shape_hello_from_operator_superseded():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        low_signal = TaskRecord(
            task_id="hello-task-live-shape",
            title="hello from operator",
            description="hello from operator",
            priority=5,
            urgency=3,
            provenance={"source": "message_intake", "source_communication_id": "msg-hello-live"},
            permissions={},
            risk="low",
            status="pending",
            success_criteria={"message_addressed": True},
            completion_policy={"type": "respond_or_execute"},
            created_at="2026-04-08T00:00:00+00:00",
            updated_at="2026-04-08T00:00:00+00:00",
        )
        db.upsert_task(low_signal)
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        runner._reconcile_pending_tasks()
        stored = [task for task in db.list_records("tasks") if task.get("task_id") == "hello-task-live-shape"][0]
        assert stored["status"] == "superseded"
        assert stored["provenance"]["closure"]["reason"] == "low_signal_pending_work"


def test_reconcile_marks_pending_message_task_satisfied_when_answered():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        completed = TaskRecord(
            task_id="complete-sibling-2",
            title="Execute operator request",
            description="Process the inbound operator request through the unified queue.",
            priority=5,
            urgency=3,
            provenance={"source": "message_intake", "source_communication_id": "msg-shared-2"},
            permissions={},
            risk="low",
            status="complete",
            success_criteria={"message_addressed": True},
            completion_policy={"type": "respond_or_execute"},
            created_at="2026-04-08T00:00:00+00:00",
            updated_at="2026-04-08T01:00:00+00:00",
        )
        pending = TaskRecord(
            task_id="pending-sibling-2",
            title="Review communication/task translation path",
            description="Inspect whether the inbound operator request should also improve Astrata's communication-to-task intake path.",
            priority=4,
            urgency=2,
            provenance={"source": "message_intake", "source_communication_id": "msg-shared-2"},
            permissions={},
            risk="low",
            status="pending",
            success_criteria={"message_addressed": True},
            completion_policy={"type": "review_or_execute"},
            created_at="2026-04-08T00:00:00+00:00",
            updated_at="2026-04-08T01:00:00+00:00",
        )
        db.upsert_task(completed)
        db.upsert_task(pending)
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        runner._reconcile_pending_tasks()
        stored = [task for task in db.list_records("tasks") if task.get("task_id") == "pending-sibling-2"][0]
        assert stored["status"] == "satisfied"
        assert stored["provenance"]["closure"]["reason"] == "later_completed_work_answered_this_task"


def test_reconcile_supersedes_duplicate_pending_message_tasks():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        first = TaskRecord(
            task_id="dup-msg-1",
            title="Execute: strengthen intake",
            description="Strengthen the intake path.",
            priority=5,
            urgency=3,
            provenance={"source": "message_intake", "source_communication_id": "msg-dup"},
            permissions={},
            risk="low",
            status="pending",
            success_criteria={"message_addressed": True},
            completion_policy={"type": "respond_or_execute"},
            created_at="2026-04-08T00:00:00+00:00",
            updated_at="2026-04-08T00:00:00+00:00",
        )
        second = TaskRecord(
            task_id="dup-msg-2",
            title="Execute: strengthen intake",
            description="Strengthen the intake path.",
            priority=5,
            urgency=3,
            provenance={"source": "message_intake", "source_communication_id": "msg-dup"},
            permissions={},
            risk="low",
            status="pending",
            success_criteria={"message_addressed": True},
            completion_policy={"type": "respond_or_execute"},
            created_at="2026-04-08T00:05:00+00:00",
            updated_at="2026-04-08T00:05:00+00:00",
        )
        db.upsert_task(first)
        db.upsert_task(second)
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        runner._reconcile_pending_tasks()
        stored = {task.get("task_id"): task for task in db.list_records("tasks")}
        assert stored["dup-msg-1"]["status"] == "pending"
        assert stored["dup-msg-2"]["status"] == "superseded"
        assert stored["dup-msg-2"]["provenance"]["closure"]["reason"] == "duplicate_pending_work"


def test_reconcile_supersedes_duplicate_pending_loop0_runner_tasks():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        first = TaskRecord(
            task_id="dup-loop0-1",
            title="Create audit review module",
            description="Add the missing audit review module.",
            priority=5,
            urgency=3,
            provenance={"source": "loop0_runner"},
            permissions={},
            risk="low",
            status="pending",
            success_criteria={"paths_created": True},
            completion_policy={"type": "execute"},
            created_at="2026-04-08T00:00:00+00:00",
            updated_at="2026-04-08T00:00:00+00:00",
        )
        second = TaskRecord(
            task_id="dup-loop0-2",
            title="Create audit review module",
            description="Add the missing audit review module.",
            priority=5,
            urgency=3,
            provenance={"source": "loop0_runner"},
            permissions={},
            risk="low",
            status="pending",
            success_criteria={"paths_created": True},
            completion_policy={"type": "execute"},
            created_at="2026-04-08T00:02:00+00:00",
            updated_at="2026-04-08T00:02:00+00:00",
        )
        db.upsert_task(first)
        db.upsert_task(second)
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        runner._reconcile_pending_tasks()
        stored = {task.get("task_id"): task for task in db.list_records("tasks")}
        assert stored["dup-loop0-1"]["status"] == "pending"
        assert stored["dup-loop0-2"]["status"] == "superseded"
        assert stored["dup-loop0-2"]["provenance"]["closure"]["reason"] == "duplicate_pending_work"


def test_reconcile_marks_pending_loop0_task_satisfied_when_expected_paths_exist():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "astrata" / "audit").mkdir(parents=True, exist_ok=True)
        (root / "astrata" / "audit" / "__init__.py").write_text("", encoding="utf-8")
        (root / "astrata" / "audit" / "review.py").write_text("def review():\n    return 'ok'\n", encoding="utf-8")
        settings = load_settings(root)
        db = AstrataDatabase(root / ".astrata" / "astrata.db")
        db.initialize()
        pending = TaskRecord(
            task_id="pending-realized-loop0",
            title="Create audit review module",
            description="Add the missing audit review module.",
            priority=5,
            urgency=3,
            provenance={"source": "loop0_runner"},
            permissions={},
            risk="low",
            status="pending",
            success_criteria={"expected_paths": ["astrata/audit/__init__.py", "astrata/audit/review.py"]},
            completion_policy={"type": "propose_next_implementation_step"},
            created_at="2026-04-08T00:00:00+00:00",
            updated_at="2026-04-08T00:00:00+00:00",
        )
        db.upsert_task(pending)
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        runner._reconcile_pending_tasks()
        stored = [task for task in db.list_records("tasks") if task.get("task_id") == "pending-realized-loop0"][0]
        assert stored["status"] == "satisfied"
        assert stored["provenance"]["closure"]["reason"] == "filesystem_state_now_matches_task_goal"
