from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.records.communications import CommunicationRecord
from astrata.config.settings import load_settings
from astrata.providers.base import CompletionRequest, CompletionResponse, Provider
from astrata.providers.registry import ProviderRegistry
from astrata.loop0.runner import Loop0Runner
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


def test_loop0_runner_unifies_pending_message_tasks():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        inbound_task = {
            "task_id": "message-task-1",
            "title": "Execute operator request",
            "description": "Process the inbound operator request through the unified queue.",
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
                description="Update intake.py to strengthen validation for inbound operator messages.",
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
                description="Update intake.py to strengthen validation for inbound operator messages.",
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
