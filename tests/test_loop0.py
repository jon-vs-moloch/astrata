from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.records.communications import CommunicationRecord
from astrata.config.settings import load_settings
from astrata.providers.base import CompletionRequest, CompletionResponse, Provider
from astrata.providers.registry import ProviderRegistry
from astrata.loop0.runner import Loop0Runner, Loop0TaskCandidate
from astrata.storage.db import AstrataDatabase


def test_loop0_runner_finds_a_missing_candidate():
    settings = load_settings(Path("/Users/jon/Projects/Astrata"))
    db = AstrataDatabase(settings.paths.data_dir / "test-loop0.db")
    db.initialize()
    runner = Loop0Runner(settings=settings, db=db)
    candidate = runner.next_candidate()
    if candidate is None:
        result = runner.run_once()
        assert result["status"] == "complete"
    else:
        assert candidate.key


def test_loop0_runner_records_one_cycle():
    settings = load_settings(Path("/Users/jon/Projects/Astrata"))
    db = AstrataDatabase(settings.paths.data_dir / "test-loop0-cycle.db")
    db.initialize()
    runner = Loop0Runner(settings=settings, db=db)
    result = runner.run_once()
    if result["status"] == "complete":
        assert result["message"]
        return
    assert result["status"] == "ok"
    assert db.list_records("tasks")
    assert db.list_records("attempts")
    assert db.list_records("artifacts")
    assert db.list_records("verifications")
    artifact_types = {record["artifact_type"] for record in db.list_records("artifacts")}
    assert "loop0_recommendation" in artifact_types
    assert "loop0_gap_report" in artifact_types
    assert "loop0_implementation_report" in artifact_types
    assert "loop0_verification_review" in artifact_types
    assert "loop0_inference_telemetry" in artifact_types
    assert result["verification"]["result"] in {"pass", "fail", "uncertain"}


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

    def get_quota_windows(self, route=None):
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
        return ["kilocode", "gemini-cli"]

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(
            provider="cli",
            model=str(request.metadata.get("cli_tool") or "kilocode"),
            content=self._content_for_request(request),
            raw={"cli_tool": request.metadata.get("cli_tool")},
        )

    def _content_for_request(self, request: CompletionRequest) -> str:
        rendered = "\n".join(message.content or "" for message in request.messages)
        if "'files'" in rendered or '"files"' in rendered:
            return '{"files":{"astrata/comms/intake.py":"# strengthened\\n"}}'
        return (
            '{"operator_response":"Delegated response from cheap lane.","followup_tasks":'
            '[{"title":"Spec follow-up","description":"Harden the spec section the assistant flagged.",'
            '"priority":4,"urgency":2,"risk":"low","completion_type":"review_or_rewrite_spec"}],'
            '"artifact":{"title":"Spec review artifact","summary":"Spec needs one improvement.",'
            '"confidence":0.85,"findings":["Harden the spec section the assistant flagged."]}}'
        )


class _ArtifactOnlyCliProvider(_CheapCliProvider):
    def _content_for_request(self, request: CompletionRequest) -> str:
        rendered = "\n".join(message.content or "" for message in request.messages)
        if "'files'" in rendered or '"files"' in rendered:
            return '{"files":{"astrata/comms/intake.py":"# strengthened\\n"}}'
        return (
            '{"operator_response":"Spec review complete.","followup_tasks":[],"artifact":'
            '{"title":"Spec review artifact","summary":"Found one implementable spec issue.",'
            '"confidence":0.91,"findings":["Add bounded validation to the intake path."]}}'
        )


class _DecompositionCliProvider(_CheapCliProvider):
    def _content_for_request(self, request: CompletionRequest) -> str:
        rendered = "\n".join(message.content or "" for message in request.messages)
        if "'files'" in rendered or '"files"' in rendered:
            return '{"files":{"astrata/comms/intake.py":"# strengthened\\n"}}'
        return (
            '{"operator_response":"This should be split into bounded worker tasks.","followup_tasks":['
            '{"task_id_hint":"inspect","title":"Inspect runtime posture","description":"Inspect the current runtime posture and record the exact mismatch.","priority":5,"urgency":3,"risk":"low","completion_type":"review_or_audit","parallelizable":true},'
            '{"task_id_hint":"persist","title":"Persist runtime posture","description":"Apply the bounded runtime posture fix once the mismatch is confirmed.","priority":5,"urgency":3,"risk":"low","completion_type":"respond_or_execute","depends_on":["inspect"],"route_preferences":{"preferred_cli_tools":["gemini-cli"],"preferred_model":"gemini-2.5-flash"}}'
            '],"artifact":{"title":"Runtime posture decomposition","summary":"Split into inspect and persist leaf tasks.","confidence":0.9,"findings":[]}}'
        )


class _ParallelDecompositionCliProvider(_CheapCliProvider):
    def _content_for_request(self, request: CompletionRequest) -> str:
        rendered = "\n".join(message.content or "" for message in request.messages)
        if "'files'" in rendered or '"files"' in rendered:
            return '{"files":{"astrata/comms/intake.py":"# strengthened\\n"}}'
        if "split it into parallel bounded worker tasks" not in rendered.lower():
            return (
                '{"operator_response":"Leaf review complete.","followup_tasks":[],"artifact":'
                '{"title":"Leaf review artifact","summary":"Executed delegated leaf review.",'
                '"confidence":0.88,"findings":[]}}'
            )
        return (
            '{"operator_response":"This should fan out into parallel leaf tasks.","followup_tasks":['
            '{"task_id_hint":"inspect","title":"Inspect runtime posture","description":"Inspect the current runtime posture and record the exact mismatch.","priority":5,"urgency":3,"risk":"low","completion_type":"review_or_audit","parallelizable":true},'
            '{"task_id_hint":"benchmark","title":"Benchmark runtime posture","description":"Benchmark the current runtime posture and record the current performance envelope.","priority":5,"urgency":3,"risk":"low","completion_type":"review_or_audit","parallelizable":true,"route_preferences":{"preferred_cli_tools":["gemini-cli"],"preferred_model":"gemini-2.5-flash"}}'
            '],"artifact":{"title":"Runtime posture parallel decomposition","summary":"Split into two independent review tasks.","confidence":0.9,"findings":[]}}'
        )


class _FileCodexProvider(_DeferredCodexProvider):
    def complete(self, request: CompletionRequest) -> CompletionResponse:
        rendered = "\n".join(message.content or "" for message in request.messages)
        if "'files'" in rendered or '"files"' in rendered:
            return CompletionResponse(
                provider="codex",
                model="gpt-5.4",
                content='{"files":{"astrata/comms/intake.py":"# direct\\n"}}',
            )
        return super().complete(request)


class _FailingCliProvider(_CheapCliProvider):
    def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise RuntimeError("provider_execution_failed")


class _QuotaAwareCliProvider(_CheapCliProvider):
    def get_quota_windows(self, route=None):
        route = dict(route or {})
        cli_tool = str(route.get("cli_tool") or "").strip().lower()
        model = str(route.get("model") or "").strip().lower()
        if cli_tool == "gemini-cli" and "flash" in model:
            return [
                {
                    "requests_remaining": 0,
                    "requests_limit": 10,
                    "reset_time": "2099-01-01T00:00:00+00:00",
                    "window_duration_seconds": 3600,
                    "source": "test_flash_exhausted",
                }
            ]
        return [
            {
                "requests_remaining": 5,
                "requests_limit": 10,
                "reset_time": "2099-01-01T00:00:00+00:00",
                "window_duration_seconds": 3600,
                "source": "test_available",
            }
        ]


def test_loop0_runner_records_coordination_deferral():
    settings = load_settings(Path("/Users/jon/Projects/Astrata"))
    db = AstrataDatabase(settings.paths.data_dir / "test-loop0-coordinator.db")
    db.initialize()
    registry = ProviderRegistry({"codex": _DeferredCodexProvider()})
    runner = Loop0Runner(settings=settings, db=db, registry=registry)
    result = runner.run_once()
    if result["status"] == "complete":
        assert result["message"]
        return
    assert result["attempt"]["outcome"] in {"blocked", "failed"}
    coordination = result["coordination_report"]["content_summary"]
    assert "controller" in coordination
    artifact_types = {record["artifact_type"] for record in db.list_records("artifacts")}
    assert "loop0_coordination_report" in artifact_types


def test_loop0_runner_finds_strengthening_candidate_when_repo_is_complete():
    settings = load_settings(Path("/Users/jon/Projects/Astrata"))
    db = AstrataDatabase(settings.paths.data_dir / "test-loop0-strengthening.db")
    db.initialize()
    runner = Loop0Runner(settings=settings, db=db)
    assessment = runner.next_candidate_assessment()
    assert assessment is not None
    assert assessment.candidate.strategy in {"normal", "strengthen"}
    if assessment.candidate.strategy == "strengthen":
        assert assessment.verification.result == "pass"
        assert assessment.inspection.get("weak_paths")


def test_loop0_blocks_unproven_governance_surface_edits():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"cli": _CheapCliProvider(), "codex": _DeferredCodexProvider()}),
        )
        candidate = Loop0TaskCandidate(
            key="astrata-governance-constitution",
            title="Create governance constitution module",
            description="Attempt to rewrite a protected governance helper without principal authorization.",
            expected_paths=("astrata/governance/constitution.py",),
        )
        result = runner._apply_candidate(  # noqa: SLF001
            candidate,
            coordination={"decision": {"status": "accepted"}, "route": {"provider": "cli", "cli_tool": "kilocode"}},
        )
        assert result["status"] == "blocked"
        assert result["failure_kind"] == "protected_governance_surface"
        assert "astrata/governance/constitution.py" in result["baseline_inspection"]["protected_paths"]


def test_loop0_records_unauthorized_governance_drift():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "spec.md").write_text("# Spec\n")
        (root / "astrata/governance").mkdir(parents=True, exist_ok=True)
        target = root / "astrata/governance/constitution.py"
        target.write_text('"""Constitution loading helpers."""\n')

        settings = load_settings(root)
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        runner = Loop0Runner(settings=settings, db=db)
        runner._record_governance_drift_if_any()  # noqa: SLF001

        target.write_text('"""Constitution loading and parsing helpers."""\n')
        drift = runner._record_governance_drift_if_any()  # noqa: SLF001

        assert drift is not None
        assert drift["status"] == "drifted"
        artifact_types = {record["artifact_type"] for record in db.list_records("artifacts")}
        assert "governance_drift_alert" in artifact_types
        alerts = [
            record
            for record in db.list_records("communications")
            if record.get("intent") == "governance_drift_alert"
        ]
        assert alerts


def test_loop0_runner_unifies_pending_message_tasks():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        inbound_task = {
            "task_id": "message-task-1",
            "title": "Execute principal request",
            "description": "Process the inbound principal request through the unified queue.",
            "priority": 8,
            "urgency": 4,
            "provenance": {"source": "message_intake", "source_communication_id": "msg-1"},
            "permissions": {},
            "risk": "low",
            "status": "pending",
            "success_criteria": {"message_addressed": True},
            "completion_policy": {"type": "respond_or_execute"},
            "created_at": "2026-04-08T00:00:00+00:00",
            "updated_at": "2026-04-08T00:00:00+00:00",
        }
        from astrata.records.models import TaskRecord

        db.upsert_task(TaskRecord(**inbound_task))
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry(
                {
                    "codex": _DeferredCodexProvider(),
                    "cli": _CheapCliProvider(),
                }
            ),
        )
        assessment = runner.next_candidate_assessment()
        assert assessment is not None
        assert assessment.candidate.strategy == "message_task"
        result = runner.run_once()
        assert result["status"] == "ok"
        assert result["task"]["task_id"] == "message-task-1"
        assert result["attempt"]["outcome"] == "running"
        assert result["verification"]["result"] == "uncertain"
        telemetry = result["inference_telemetry"]
        assert telemetry["artifact_type"] == "loop0_inference_telemetry"
        assert '"delegated_worker"' in telemetry["content_summary"]
        assert result["operator_message"]["intent"] == "loop0_result"
        route = result["attempt"]["resource_usage"]["route"]
        assert route["provider"] == "cli"
        assert route["cli_tool"] == "kilocode"
        implementation = result["attempt"]["resource_usage"]["implementation"]
        assert implementation["generation_mode"] == "delegated_worker"
        assert implementation["delegated_via_worker"] == "worker.kilocode"
        assert implementation["assistant_output"] == ""
        assert implementation["worker_task_id"]
        worker_task = next(
            payload for payload in db.list_records("tasks") if payload.get("task_id") == implementation["worker_task_id"]
        )
        assert worker_task["status"] == "working"
        assert worker_task["parent_task_id"] == "message-task-1"
        communications = db.list_records("communications")
        worker_requests = [payload for payload in communications if payload.get("intent") == "worker_delegation_request"]
        assert worker_requests
        assert worker_requests[-1]["recipient"] == "worker.kilocode"
        assert worker_requests[-1]["payload"]["worker_task_id"] == implementation["worker_task_id"]
        worker_results = [payload for payload in communications if payload.get("intent") == "worker_delegation_result"]
        assert not worker_results
        runner.worker_runtime.process_pending(worker_id="worker.kilocode")
        runner._reconcile_pending_tasks()
        communications = db.list_records("communications")
        worker_results = [payload for payload in communications if payload.get("intent") == "worker_delegation_result"]
        assert worker_results
        assert worker_results[-1]["payload"]["worker_id"] == "worker.kilocode"
        delegated = [payload for payload in communications if payload.get("intent") == "message_task_response"]
        assert delegated
        assert delegated[-1]["payload"]["assistant_output"] == "Delegated response from cheap lane."
        assert delegated[-1]["payload"]["followup_tasks"]
        updated_task = next(payload for payload in db.list_records("tasks") if payload.get("task_id") == "message-task-1")
        assert updated_task["status"] == "complete"
        assert updated_task["active_child_ids"] == []
        worker_task = next(
            payload for payload in db.list_records("tasks") if payload.get("task_id") == implementation["worker_task_id"]
        )
        assert worker_task["status"] == "complete"
        attempts = [payload for payload in db.list_records("attempts") if payload.get("task_id") == "message-task-1"]
        assert {payload["outcome"] for payload in attempts} == {"running", "succeeded"}
        followup_tasks = [
            payload
            for payload in db.list_records("tasks")
            if payload.get("provenance", {}).get("source") == "message_task_followup"
        ]
        assert followup_tasks
        assert followup_tasks[-1]["title"] == "Spec follow-up"


def test_loop0_runner_unifies_pending_followup_tasks():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        from astrata.records.models import TaskRecord

        db.upsert_task(
            TaskRecord(
                task_id="message-followup-1",
                title="Implement Phase 0 Plan",
                description="Execute the phase 0 plan for initial deployment.",
                priority=8,
                urgency=4,
                provenance={
                    "source": "message_task_followup",
                    "parent_task_id": "parent-task-1",
                    "source_communication_id": "msg-followup-1",
                    "derived_request_kind": "execution",
                },
                permissions={},
                risk="low",
                status="pending",
                success_criteria={"message_addressed": True},
                completion_policy={
                    "type": "respond_or_execute",
                    "route_preferences": {"preferred_cli_tools": ["kilocode"]},
                },
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
        assert assessment.candidate.key == "task:message-followup-1"
        assert assessment.candidate.strategy == "message_task"


def test_loop0_runner_executes_startup_diagnostic_tasks():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        from astrata.records.models import TaskRecord

        db.upsert_task(
            TaskRecord(
                task_id="startup-self-diagnosis",
                title="Review startup diagnostics",
                description="Startup reflection found 1 issue(s): local_runtime_unhealthy.",
                priority=9,
                urgency=7,
                provenance={
                    "source": "startup_diagnostic",
                    "report_path": str(Path(tmp) / "startup-runtime-report.json"),
                },
                permissions={},
                risk="low",
                status="pending",
                success_criteria={"startup_issues_reviewed": True},
                completion_policy={
                    "type": "respond_or_execute",
                    "prefer_cheap_lanes": True,
                    "route_preferences": {
                        "preferred_cli_tools": ["gemini-cli", "kilocode"],
                        "preferred_model": "gemini-2.5-flash",
                    },
                },
                created_at="2026-04-09T00:00:00+00:00",
                updated_at="2026-04-09T00:00:00+00:00",
            )
        )
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry(
                {
                    "codex": _DeferredCodexProvider(),
                    "cli": _CheapCliProvider(),
                }
            ),
        )

        assessment = runner.next_candidate_assessment()

        assert assessment is not None
        assert assessment.candidate.key == "task:startup-self-diagnosis"
        assert assessment.candidate.strategy == "message_task"

        result = runner.run_once()

        assert result["status"] == "ok"
        assert result["task"]["task_id"] == "startup-self-diagnosis"
        assert result["attempt"]["outcome"] == "running"
        assert result["verification"]["result"] == "uncertain"
        route = result["attempt"]["resource_usage"]["route"]
        assert route["provider"] == "cli"
        assert route["cli_tool"] == "gemini-cli"
        assert route["model"] == "gemini-2.5-flash"
        implementation = result["attempt"]["resource_usage"]["implementation"]
        assert implementation["delegated_via_worker"] == "worker.gemini-cli.gemini-2-5-flash"
        runner.worker_runtime.process_pending(worker_id="worker.gemini-cli.gemini-2-5-flash")
        runner._reconcile_pending_tasks()
        updated_task = next(
            payload for payload in db.list_records("tasks") if payload.get("task_id") == "startup-self-diagnosis"
        )
        assert updated_task["status"] == "complete"


def test_loop0_runner_executes_file_shaped_message_task():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "astrata" / "comms").mkdir(parents=True, exist_ok=True)
        (root / "astrata" / "comms" / "intake.py").write_text("# original\n", encoding="utf-8")
        settings = load_settings(root)
        db = AstrataDatabase(Path(tmp) / ".astrata" / "astrata.db")
        db.initialize()
        from astrata.records.models import TaskRecord

        db.upsert_task(
            TaskRecord(
                task_id="message-task-file",
                title="Execute: update intake.py",
                description="Update intake.py to strengthen validation for inbound principal messages.",
                priority=8,
                urgency=4,
                provenance={"source": "message_intake", "source_communication_id": "msg-2"},
                permissions={},
                risk="low",
                status="pending",
                success_criteria={"message_addressed": True},
                completion_policy={
                    "type": "respond_or_execute",
                    "route_preferences": {"preferred_cli_tools": ["kilocode"]},
                },
                created_at="2026-04-08T00:00:00+00:00",
                updated_at="2026-04-08T00:00:00+00:00",
            )
        )
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        result = runner.run_once()
        assert result["status"] == "ok"
        implementation = result["attempt"]["resource_usage"]["implementation"]
        assert implementation["execution_track"] == "bounded_file_generation"
        assert implementation["procedure_variant_id"] == "careful_execution"
        assert "astrata/comms/intake.py" in implementation["written_paths"]
        assert (root / "astrata" / "comms" / "intake.py").read_text(encoding="utf-8") == "# strengthened\n"
        assert result["verification"]["result"] == "pass"


def test_loop0_runner_allows_direct_variant_for_strong_file_execution_route():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "astrata" / "comms").mkdir(parents=True, exist_ok=True)
        (root / "astrata" / "comms" / "intake.py").write_text("# original\n", encoding="utf-8")
        settings = load_settings(root)
        db = AstrataDatabase(Path(tmp) / ".astrata" / "astrata.db")
        db.initialize()
        from astrata.records.models import TaskRecord

        db.upsert_task(
            TaskRecord(
                task_id="message-task-file-direct",
                title="Execute: update intake.py directly",
                description="Update intake.py to strengthen validation for inbound principal messages.",
                priority=8,
                urgency=4,
                provenance={"source": "message_intake", "source_communication_id": "msg-4"},
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
            registry=ProviderRegistry({"codex": _FileCodexProvider()}),
        )
        result = runner.run_once()
        assert result["status"] == "ok"
        implementation = result["attempt"]["resource_usage"]["implementation"]
        assert implementation["execution_track"] == "bounded_file_generation"
        assert implementation["procedure_variant_id"] == "direct_execution"
        assert implementation["actor_capability"] == "expert"
        assert implementation["procedure_metadata"]["shortcut_allowed"] is True
        assert (root / "astrata" / "comms" / "intake.py").read_text(encoding="utf-8") == "# direct\n"


def test_loop0_runner_promotes_artifact_findings_into_followup_tasks():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        from astrata.records.models import TaskRecord

        db.upsert_task(
            TaskRecord(
                task_id="message-task-artifact",
                title="Spec: review the intake spec",
                description="Review the intake spec and identify gaps.",
                priority=6,
                urgency=3,
                provenance={"source": "message_intake", "source_communication_id": "msg-3"},
                permissions={},
                risk="low",
                status="pending",
                success_criteria={"message_addressed": True},
                completion_policy={"type": "review_or_rewrite_spec"},
                created_at="2026-04-08T00:00:00+00:00",
                updated_at="2026-04-08T00:00:00+00:00",
            )
        )
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _ArtifactOnlyCliProvider()}),
        )
        result = runner.run_once()
        assert result["status"] == "ok"
        runner.worker_runtime.process_pending(worker_id="worker.kilocode")
        runner._reconcile_pending_tasks()
        artifacts = db.list_records("artifacts")
        message_artifact = next(
            (payload for payload in artifacts if payload.get("artifact_type") == "spec_review"),
            None,
        )
        assert message_artifact is not None
        followup_tasks = [
            payload
            for payload in db.list_records("tasks")
            if payload.get("provenance", {}).get("source") == "message_task_followup"
        ]
        assert followup_tasks
        assert followup_tasks[-1]["provenance"]["source"] == "message_task_followup"
        assert followup_tasks[-1]["completion_policy"]["type"] == "respond_or_execute"


def test_loop0_runner_materializes_dependency_aware_decomposition_followups():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        from astrata.records.models import TaskRecord

        db.upsert_task(
            TaskRecord(
                task_id="message-task-decomposition",
                title="Runtime posture needs decomposition",
                description="Figure out the runtime posture repair path and split it into bounded worker steps.",
                priority=6,
                urgency=3,
                provenance={"source": "message_intake", "source_communication_id": "msg-5"},
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
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _DecompositionCliProvider()}),
        )
        result = runner.run_once()
        assert result["status"] == "ok"
        runner.worker_runtime.process_pending(worker_id="worker.kilocode")
        runner._reconcile_pending_tasks()
        followup_tasks = [
            payload
            for payload in db.list_records("tasks")
            if payload.get("provenance", {}).get("source") == "message_task_followup"
        ]
        assert len(followup_tasks) == 2
        by_title = {payload["title"]: payload for payload in followup_tasks}
        inspect_task = by_title["Inspect runtime posture"]
        persist_task = by_title["Persist runtime posture"]
        assert inspect_task["dependencies"] == []
        assert persist_task["dependencies"] == [inspect_task["task_id"]]
        assert inspect_task["provenance"]["decomposition"]["parallelizable"] is True
        assert persist_task["completion_policy"]["route_preferences"]["preferred_cli_tools"] == ["gemini-cli"]
        assert persist_task["completion_policy"]["route_preferences"]["preferred_model"] == "gemini-2.5-flash"
        artifacts = db.list_records("artifacts")
        decomposition_artifact = next(
            (payload for payload in artifacts if payload.get("artifact_type") == "task_decomposition"),
            None,
        )
        assert decomposition_artifact is not None
        draft_procedure = next(
            (payload for payload in artifacts if payload.get("artifact_type") == "draft_procedure"),
            None,
        )
        assert draft_procedure is not None


def test_pending_followup_with_unresolved_dependency_is_not_selected():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        from astrata.records.models import TaskRecord

        inspect_task = TaskRecord(
            task_id="inspect-task",
            parent_task_id="parent-task",
            title="Inspect runtime posture",
            description="Inspect current posture.",
            priority=5,
            urgency=3,
            provenance={"source": "message_task_followup"},
            risk="low",
            status="pending",
            success_criteria={"done": True},
            completion_policy={"type": "review_or_audit"},
        )
        persist_task = TaskRecord(
            task_id="persist-task",
            parent_task_id="parent-task",
            title="Persist runtime posture",
            description="Persist posture after inspection.",
            priority=7,
            urgency=3,
            provenance={"source": "message_task_followup"},
            risk="low",
            status="pending",
            dependencies=["inspect-task"],
            success_criteria={"done": True},
            completion_policy={"type": "respond_or_execute"},
        )
        db.upsert_task(inspect_task)
        db.upsert_task(persist_task)
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        candidates = runner._pending_message_task_candidates()
        candidate_ids = {candidate.source_task_id for candidate in candidates}
        assert "inspect-task" in candidate_ids
        assert "persist-task" not in candidate_ids


def test_loop0_worker_failure_decomposes_multistage_task():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        from astrata.records.models import TaskRecord

        db.upsert_task(
            TaskRecord(
                task_id="multistage-failure",
                title="Inspect and then summarize runtime posture",
                description="Inspect the runtime posture and then summarize the findings in a durable note.",
                priority=10,
                urgency=10,
                provenance={"source": "message_intake", "source_communication_id": "msg-fail"},
                permissions={},
                risk="low",
                status="pending",
                success_criteria={"message_addressed": True},
                completion_policy={"type": "review_or_audit"},
            )
        )
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _FailingCliProvider()}),
        )
        assessment = runner.next_candidate_assessment()
        assert assessment is not None
        assert assessment.candidate.source_task_id == "multistage-failure"
        result = runner.run_once()
        assert result["status"] == "ok"
        worker_task = next(
            payload
            for payload in db.list_records("tasks")
            if payload.get("parent_task_id") == "multistage-failure"
            and payload.get("provenance", {}).get("source") == "worker_delegation"
        )
        runner.worker_runtime.process_pending(worker_id=str(worker_task["provenance"]["worker_id"]))
        runner._reconcile_pending_tasks()
        updated = next(payload for payload in db.list_records("tasks") if payload.get("task_id") == "multistage-failure")
        assert updated["status"] == "blocked"
        assert updated["provenance"]["resolution"]["kind"] == "decompose"
        followups = [
            payload
            for payload in db.list_records("tasks")
            if payload.get("provenance", {}).get("source") == "message_task_followup"
        ]
        assert followups
        assert followups[-1]["title"].startswith("Decompose:")


def test_run_steps_dispatches_ready_parallel_children():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        from astrata.records.models import TaskRecord

        db.upsert_task(
            TaskRecord(
                task_id="parallel-parent",
                title="Runtime posture needs parallel decomposition",
                description="Figure out the runtime posture issue and split it into parallel bounded worker tasks.",
                priority=9,
                urgency=7,
                provenance={"source": "message_intake", "source_communication_id": "msg-parallel"},
                permissions={},
                risk="low",
                status="pending",
                success_criteria={"message_addressed": True},
                completion_policy={
                    "type": "review_or_audit",
                    "route_preferences": {"preferred_cli_tools": ["kilocode"]},
                },
            )
        )
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _ParallelDecompositionCliProvider()}),
        )
        assessment = runner.next_candidate_assessment()
        assert assessment is not None
        assert assessment.candidate.source_task_id == "parallel-parent"
        result = runner.run_steps(2)
        assert result["status"] == "ok"
        followups = [
            payload
            for payload in db.list_records("tasks")
            if payload.get("provenance", {}).get("source") == "message_task_followup"
        ]
        assert len(followups) == 2
        worker_children = [
            payload
            for payload in db.list_records("tasks")
            if payload.get("provenance", {}).get("source") == "worker_delegation"
            and payload.get("parent_task_id") in {task["task_id"] for task in followups}
        ]
        assert len(worker_children) == 2
        assert {payload["status"] for payload in worker_children} == {"complete"}


def test_worker_supervision_reassigns_stalled_child_to_stronger_route():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        from astrata.records.models import TaskRecord

        db.upsert_task(
            TaskRecord(
                task_id="stalled-parent",
                title="Inspect runtime posture",
                description="Inspect the current runtime posture and report the exact mismatch.",
                priority=7,
                urgency=4,
                provenance={"source": "message_intake", "source_communication_id": "msg-stalled"},
                permissions={},
                risk="low",
                status="pending",
                success_criteria={"message_addressed": True},
                completion_policy={
                    "type": "review_or_audit",
                    "route_preferences": {
                        "preferred_cli_tools": ["kilocode", "gemini-cli"],
                        "preferred_model": "gemini-2.5-flash",
                    },
                },
            )
        )
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        result = runner.run_once()
        worker_task_id = result["attempt"]["resource_usage"]["implementation"]["worker_task_id"]
        worker_request = next(
            payload
            for payload in db.list_records("communications")
            if payload.get("intent") == "worker_delegation_request"
            and str(dict(payload.get("payload") or {}).get("worker_task_id") or "") == worker_task_id
        )
        db.upsert_communication(
            CommunicationRecord(
                **{
                    **worker_request,
                    "created_at": "2026-04-08T00:00:00+00:00",
                    "delivered_at": "2026-04-08T00:00:00+00:00",
                }
            )
        )
        runner._reconcile_pending_tasks()
        parent_task = next(payload for payload in db.list_records("tasks") if payload.get("task_id") == "stalled-parent")
        assert parent_task["status"] == "working"
        assert parent_task["provenance"]["worker_supervision"]["action"] == "reassign"
        new_child_ids = list(parent_task["active_child_ids"])
        assert len(new_child_ids) == 1
        old_worker = next(payload for payload in db.list_records("tasks") if payload.get("task_id") == worker_task_id)
        assert old_worker["status"] == "failed"
        new_worker = next(payload for payload in db.list_records("tasks") if payload.get("task_id") == new_child_ids[0])
        assert new_worker["provenance"]["worker_id"] == "worker.gemini-cli.gemini-2-5-flash"
        supervision_artifacts = [
            payload for payload in db.list_records("artifacts") if payload.get("artifact_type") == "worker_supervision"
        ]
        assert supervision_artifacts


def test_worker_supervision_blocks_after_retry_budget_exhausted():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        from astrata.records.models import TaskRecord

        db.upsert_task(
            TaskRecord(
                task_id="blocked-parent",
                title="Benchmark runtime posture",
                description="Benchmark the current runtime posture and report the current envelope.",
                priority=6,
                urgency=3,
                provenance={"source": "message_intake", "source_communication_id": "msg-blocked"},
                permissions={},
                risk="low",
                status="pending",
                success_criteria={"message_addressed": True},
                completion_policy={
                    "type": "review_or_audit",
                    "route_preferences": {
                        "preferred_cli_tools": ["gemini-cli"],
                        "preferred_model": "gemini-2.5-flash",
                    },
                },
            )
        )
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _CheapCliProvider()}),
        )
        result = runner.run_once()
        worker_task_id = result["attempt"]["resource_usage"]["implementation"]["worker_task_id"]
        worker_request = next(
            payload
            for payload in db.list_records("communications")
            if payload.get("intent") == "worker_delegation_request"
            and str(dict(payload.get("payload") or {}).get("worker_task_id") or "") == worker_task_id
        )
        db.upsert_communication(
            CommunicationRecord(
                **{
                    **worker_request,
                    "created_at": "2026-04-08T00:00:00+00:00",
                    "delivered_at": "2026-04-08T00:00:00+00:00",
                }
            )
        )
        worker_task = next(payload for payload in db.list_records("tasks") if payload.get("task_id") == worker_task_id)
        db.upsert_task(
            TaskRecord(
                **{
                    **worker_task,
                    "provenance": {
                        **dict(worker_task.get("provenance") or {}),
                        "supervision": {"retry_index": 2},
                    },
                }
            )
        )
        runner._reconcile_pending_tasks()
        parent_task = next(payload for payload in db.list_records("tasks") if payload.get("task_id") == "blocked-parent")
        assert parent_task["status"] == "blocked"
        assert parent_task["active_child_ids"] == []
        assert parent_task["provenance"]["worker_supervision"]["action"] == "block"


def test_worker_supervision_skips_quota_exhausted_cheap_route():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        from astrata.records.models import TaskRecord

        db.upsert_task(
            TaskRecord(
                task_id="quota-parent",
                title="Benchmark runtime posture",
                description="Benchmark the current runtime posture and report the current envelope.",
                priority=6,
                urgency=3,
                provenance={"source": "message_intake", "source_communication_id": "msg-quota"},
                permissions={},
                risk="low",
                status="pending",
                success_criteria={"message_addressed": True},
                completion_policy={
                    "type": "review_or_audit",
                    "route_preferences": {
                        "preferred_cli_tools": ["kilocode", "gemini-cli"],
                        "preferred_model": "gemini-2.5-flash",
                    },
                },
            )
        )
        runner = Loop0Runner(
            settings=settings,
            db=db,
            registry=ProviderRegistry({"codex": _DeferredCodexProvider(), "cli": _QuotaAwareCliProvider()}),
        )
        result = runner.run_once()
        worker_task_id = result["attempt"]["resource_usage"]["implementation"]["worker_task_id"]
        worker_request = next(
            payload
            for payload in db.list_records("communications")
            if payload.get("intent") == "worker_delegation_request"
            and str(dict(payload.get("payload") or {}).get("worker_task_id") or "") == worker_task_id
        )
        db.upsert_communication(
            CommunicationRecord(
                **{
                    **worker_request,
                    "created_at": "2026-04-08T00:00:00+00:00",
                    "delivered_at": "2026-04-08T00:00:00+00:00",
                }
            )
        )
        runner._reconcile_pending_tasks()
        parent_task = next(payload for payload in db.list_records("tasks") if payload.get("task_id") == "quota-parent")
        assert parent_task["status"] == "working"
        selected_route = parent_task["provenance"]["worker_supervision"]["selected_route"]
        assert selected_route["cli_tool"] == "gemini-cli"
        assert selected_route["model"] == "gemini-2.5-pro"
