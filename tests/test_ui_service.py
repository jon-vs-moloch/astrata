import os
from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.config.settings import AstrataPaths, LocalRuntimeSettings, RuntimeLimits, Settings
from astrata.records.models import AttemptRecord, ArtifactRecord, TaskRecord, VerificationRecord
from astrata.storage.db import AstrataDatabase
from astrata.ui.service import AstrataUIService, MessageDraft


def _settings(root: Path) -> Settings:
    data_dir = root / ".astrata"
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        paths=AstrataPaths(
            project_root=root,
            data_dir=data_dir,
            docs_dir=root,
            provider_secrets_path=data_dir / "provider_secrets.json",
        ),
        runtime_limits=RuntimeLimits(),
        local_runtime=LocalRuntimeSettings(
            model_search_paths=(),
            model_install_dir=data_dir / "models",
        ),
    )


def test_ui_service_snapshot_and_message_flow():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        db.upsert_task(
            TaskRecord(
                title="Test task",
                description="Make sure the UI sees durable state.",
                status="pending",
            )
        )
        service = AstrataUIService(settings=settings)
        service.send_message(MessageDraft(message="Hello from UI", recipient="astrata"))
        snapshot = service.snapshot()
        assert snapshot["product"]["name"] == "Astrata"
        assert snapshot["startup"]["preflight"]["phase"] == "pre_inference"
        assert snapshot["startup"]["runtime"]["phase"] == "post_boot"
        assert "voice" in snapshot
        assert snapshot["queue"]["counts"]["pending"] == 1
        assert snapshot["inference"]["window_hours"] == 24
        assert "quota_pressure" in snapshot["inference"]
        assert snapshot["communications"]["astrata_inbox"][0]["message"] == "Hello from UI"
        assert snapshot["communications"]["prime_conversation"] == []
        assert snapshot["account_auth"]["access_policy"]["public_access"]["download"] is True
        assert snapshot["account_auth"]["hosted_bridge_eligibility"]["status"] == "invite_required"


def test_ui_service_snapshot_reports_desktop_backend_session():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        session_path = settings.paths.data_dir / "desktop-session.json"
        session_path.write_text(
            '{"ui_port":8891,"backend_url":"http://127.0.0.1:8891/","started_by_desktop_shell":true,"backend_pid":321,"frontend_deliberately_closed":false,"backend_deliberately_stopped":true,"last_action":"stop_backend","started_at_unix_ms":123}',
            encoding="utf-8",
        )

        snapshot = AstrataUIService(settings=settings).snapshot()

        assert snapshot["desktop_backend"]["session_present"] is True
        assert snapshot["desktop_backend"]["backend_url"] == "http://127.0.0.1:8891/"
        assert snapshot["desktop_backend"]["backend_deliberately_stopped"] is True
        assert snapshot["desktop_backend"]["backend_running"] is False


def test_ui_service_ensure_local_runtime_reports_existing_healthy_lane(monkeypatch):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        service = AstrataUIService(settings=settings)

        class _Managed:
            running = True
            pid = 123
            endpoint = "http://127.0.0.1:8080/health"
            command = ["llama-server"]
            log_path = str(root / "runtime.log")
            started_at = 1.0
            detail = None

        class _Health:
            ok = True

            def model_dump(self, mode="json"):
                return {"ok": True, "status": "healthy"}

        class _Recommendation:
            model = None
            profile_id = "quiet"
            reason = "ok"

        class _Manager:
            def recommend(self, thermal_preference="quiet"):
                return _Recommendation()

            def managed_status(self):
                return _Managed()

            def health(self, config=None):
                return _Health()

        monkeypatch.setattr(service, "_local_runtime_manager", lambda: _Manager())
        monkeypatch.setattr("astrata.ui.service.probe_thermal_state", lambda preference="quiet": type("Thermal", (), {
            "preference": preference,
            "thermal_pressure": "nominal",
            "detail": None,
        })())

        class _Decision:
            sample = "nominal"
            latched = "nominal"
            action = "allow"
            should_start_new_local_work = True
            should_throttle_background = False
            reason = "ok"

        monkeypatch.setattr("astrata.ui.service.ThermalController.evaluate", lambda self, thermal: _Decision())

        result = service.ensure_local_runtime()

        assert result["status"] == "already_running"
        assert result["health"]["ok"] is True


def test_ui_service_ensure_local_runtime_adopts_existing_endpoint(monkeypatch):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        service = AstrataUIService(settings=settings)

        class _Health:
            ok = True
            status = "healthy"
            endpoint = "http://127.0.0.1:8080/health"
            detail = "http_status=200"
            metadata = {"backend_id": "llama_cpp"}

            def model_dump(self, mode="json"):
                return {
                    "backend_id": "llama_cpp",
                    "ok": True,
                    "status": "healthy",
                    "endpoint": self.endpoint,
                    "detail": self.detail,
                    "metadata": self.metadata,
                }

        class _Recommendation:
            class _Model:
                model_id = "model-1"

            model = _Model()
            profile_id = "quiet"
            reason = "ok"

        class _Backend:
            backend_id = "llama_cpp"

            def healthcheck(self, config=None):
                return _Health()

        class _Manager:
            def __init__(self):
                self.selected = None

            def recommend(self, thermal_preference="quiet"):
                return _Recommendation()

            def managed_status(self):
                return None

            def health(self, config=None):
                return None

            def backend(self, backend_id):
                assert backend_id == "llama_cpp"
                return _Backend()

            def select_runtime(self, **kwargs):
                self.selected = kwargs

        manager = _Manager()

        monkeypatch.setattr(service, "_local_runtime_manager", lambda: manager)
        monkeypatch.setattr("astrata.ui.service.probe_thermal_state", lambda preference="quiet": type("Thermal", (), {
            "preference": preference,
            "thermal_pressure": "nominal",
            "detail": None,
        })())

        class _Decision:
            sample = "nominal"
            latched = "nominal"
            action = "allow"
            should_start_new_local_work = True
            should_throttle_background = False
            reason = "ok"

        monkeypatch.setattr("astrata.ui.service.ThermalController.evaluate", lambda self, thermal: _Decision())

        result = service.ensure_local_runtime()

        assert result["status"] == "already_running"
        assert result["adopted_existing_endpoint"] is True
        assert manager.selected is not None
        assert manager.selected["mode"] == "external"
        assert manager.selected["metadata"]["adopted_existing_endpoint"] is True


def test_ui_service_redeem_invite_code_enables_hosted_bridge():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        service = AstrataUIService(settings=settings)

        invite = service._account_registry().issue_invite_code(label="test")
        result = service.redeem_invite_code(
            email="tester@example.com",
            display_name="Tester",
            invite_code=invite["invite"]["code"],
        )

        assert result["status"] == "ok"
        assert result["hosted_bridge_eligibility"]["status"] == "eligible"


def test_ui_service_pairs_desktop_device_for_invited_user():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        service = AstrataUIService(settings=settings)

        invite = service._account_registry().issue_invite_code(label="test")
        service.redeem_invite_code(
            email="tester@example.com",
            display_name="Tester",
            invite_code=invite["invite"]["code"],
        )
        result = service.pair_desktop_device(
            email="tester@example.com",
            label="Tester Mac",
            relay_endpoint="https://relay.example/mcp",
        )
        snapshot = service.snapshot()

        assert result["status"] == "ok"
        assert result["device"]["label"] == "Tester Mac"
        assert result["device_link"]["relay_endpoint"] == "https://relay.example/mcp"
        assert snapshot["account_auth"]["counts"]["devices"] == 1
        assert snapshot["account_auth"]["counts"]["active_device_links"] == 1
        assert snapshot["account_auth"]["status"] == "linked"
        assert snapshot["relay"]["selected_profile"]["profile_id"] == result["profile"]["profile_id"]


def test_ui_service_connector_oauth_setup_returns_operator_url():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        service = AstrataUIService(settings=settings)

        invite = service._account_registry().issue_invite_code(label="test")
        service.redeem_invite_code(
            email="tester@example.com",
            display_name="Tester",
            invite_code=invite["invite"]["code"],
        )
        service.pair_desktop_device(
            email="tester@example.com",
            label="Tester Mac",
            relay_endpoint="https://relay.example/mcp",
        )

        setup = service.connector_oauth_setup(
            email="tester@example.com",
            callback_url="https://chat.openai.com/aip/g-abc123/oauth/callback",
        )
        snapshot = service.snapshot()

        assert setup["status"] == "ok"
        assert setup["authorize_url"].startswith("https://relay.example/oauth/authorize?")
        assert "client_id=" in setup["authorize_url"]
        assert snapshot["account_auth"]["oauth"]["counts"]["clients"] == 1
        assert snapshot["account_auth"]["connector_urls"]["relay"] == "https://relay.example/mcp"


def test_ui_service_preferences_and_history_snapshot():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        db.upsert_task(
            TaskRecord(
                task_id="blocked-task",
                title="Blocked work",
                description="Needs intervention.",
                status="blocked",
            )
        )
        db.upsert_attempt(
            AttemptRecord(
                attempt_id="failed-attempt",
                task_id="blocked-task",
                actor="loop0",
                outcome="failed",
                failure_kind="runtime_error",
                started_at="2026-04-09T00:00:00+00:00",
                ended_at="2026-04-09T00:01:00+00:00",
            )
        )
        db.upsert_artifact(
            ArtifactRecord(
                artifact_id="history-1",
                artifact_type="history_report",
                title="History report",
                content_summary="Overnight summary.",
            )
        )

        service = AstrataUIService(settings=settings)
        updated = service.set_preferences({"update_channel": "nightly"})
        snapshot = service.snapshot()

        assert updated["update_channel"] == "nightly"
        assert snapshot["update_channel"]["selected"] == "nightly"
        assert snapshot["history"]["overview"]["blocked_tasks"] == 1
        assert snapshot["history"]["overview"]["failed_attempts"] == 1
        assert snapshot["history"]["snapshot_reports"][0]["artifact_id"] == "history-1"
        assert "git" in snapshot["history"]


def test_ui_service_preferences_include_local_runtime_policy():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        service = AstrataUIService(settings=settings)

        updated = service.set_preferences(
            {
                "local_runtime_policy": {
                    "auto_load_enabled": True,
                    "keep_user_loaded_model": False,
                    "eligible_model_ids": ["model-a"],
                    "max_ram_gb": 8,
                }
            }
        )

        assert updated["local_runtime_policy"]["auto_load_enabled"] is True
        assert updated["local_runtime_policy"]["keep_user_loaded_model"] is False
        assert updated["local_runtime_policy"]["eligible_model_ids"] == ["model-a"]
        assert updated["local_runtime_policy"]["max_ram_gb"] == 8.0


def test_ui_service_snapshot_reports_loaded_local_model_from_managed_metadata():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        data_dir = root / ".astrata"
        models_dir = data_dir / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        model_path = models_dir / "demo.gguf"
        model_path.write_bytes(b"gguf")
        settings = Settings(
            paths=AstrataPaths(
                project_root=root,
                data_dir=data_dir,
                docs_dir=root,
                provider_secrets_path=data_dir / "provider_secrets.json",
            ),
            runtime_limits=RuntimeLimits(),
            local_runtime=LocalRuntimeSettings(
                model_search_paths=(str(models_dir),),
                model_install_dir=models_dir,
            ),
        )
        (data_dir / "local_runtime.json").write_text(
            '{"pid": %s, "endpoint": "http://127.0.0.1:8080/health", "command": ["llama-server"], "log_path": "%s", "started_at": 1.0, "metadata": {"backend_id": "llama_cpp", "model_path": "%s", "profile_id": "quiet", "load_origin": "user"}}'
            % (os.getpid(), data_dir / "local_runtime.log", model_path),
            encoding="utf-8",
        )

        snapshot = AstrataUIService(settings=settings).snapshot()

        assert snapshot["local_runtime"]["loaded_model"]["path"] == str(model_path)
        assert snapshot["local_runtime"]["selection"]["profile_id"] == "quiet"
        assert snapshot["local_runtime"]["managed_process"]["metadata"]["load_origin"] == "user"


def test_ui_service_start_local_runtime_can_override_resource_policy(monkeypatch):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        service = AstrataUIService(settings=settings)
        service.set_preferences({"local_runtime_policy": {"max_ram_gb": 1}})

        class _Model:
            model_id = "model-1"
            display_name = "Big model"
            size_bytes = 3 * (1024**3)

            def model_dump(self, mode="json"):
                return {
                    "model_id": self.model_id,
                    "display_name": self.display_name,
                    "size_bytes": self.size_bytes,
                }

        class _Recommendation:
            model = _Model()
            profile_id = "balanced"
            reason = "picked"

        class _Manager:
            started = None

            class _Registry:
                @staticmethod
                def get(model_id):
                    return _Model() if model_id == "model-1" else None

            def recommend(self, thermal_preference="quiet"):
                return _Recommendation()

            def model_registry(self):
                return self._Registry()

            def start_managed(self, **kwargs):
                self.started = kwargs
                return type("Status", (), {
                    "running": True,
                    "pid": 111,
                    "endpoint": "http://127.0.0.1:8080/health",
                    "command": ["llama-server"],
                    "log_path": str(root / "runtime.log"),
                    "started_at": 1.0,
                    "metadata": kwargs.get("metadata") or {},
                    "detail": None,
                })()

        manager = _Manager()
        monkeypatch.setattr(service, "_local_runtime_manager", lambda: manager)
        monkeypatch.setattr("astrata.ui.service.probe_thermal_state", lambda preference="quiet": type("Thermal", (), {
            "preference": preference,
            "thermal_pressure": "nominal",
            "detail": None,
        })())

        class _Decision:
            sample = "nominal"
            latched = "nominal"
            action = "allow"
            should_start_new_local_work = True
            should_throttle_background = False
            reason = "ok"

        monkeypatch.setattr("astrata.ui.service.ThermalController.evaluate", lambda self, thermal: _Decision())

        blocked = service.start_local_runtime(model_id="model-1", operator_initiated=True)
        allowed = service.start_local_runtime(
            model_id="model-1",
            operator_initiated=True,
            override_resource_policy=True,
        )

        assert blocked["status"] == "blocked_by_resource_policy"
        assert allowed["status"] == "started"
        assert manager.started["metadata"]["load_origin"] == "user"


def test_ui_service_task_detail_and_lane_views():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        task = TaskRecord(
            task_id="task-1",
            title="Trace me",
            description="Need a detail pane.",
            status="pending",
            provenance={"source_communication_id": "msg-1"},
        )
        db.upsert_task(task)
        db.upsert_attempt(
            AttemptRecord(
                attempt_id="attempt-1",
                task_id=task.task_id,
                actor="loop0",
                outcome="succeeded",
                result_summary="Did the thing.",
                verification_status="passed",
            )
        )
        db.upsert_artifact(
            ArtifactRecord(
                artifact_id="artifact-1",
                artifact_type="trace",
                title="Trace artifact",
                content_summary="task-1 left behind a useful trace",
            )
        )
        db.upsert_verification(
            VerificationRecord(
                verification_id="verification-1",
                target_kind="task",
                target_id=task.task_id,
                verifier="basic",
                result="pass",
                confidence=0.8,
            )
        )
        service = AstrataUIService(settings=settings)
        prime_result = service.send_message(MessageDraft(message="Hello Prime", recipient="prime"))
        local_result = service.send_message(MessageDraft(message="Hello Local", recipient="local"))
        detail = service.task_detail(task.task_id)
        snapshot = service.snapshot()
        assert detail["status"] == "ok"
        assert detail["task"]["task_id"] == task.task_id
        assert detail["attempts"][0]["attempt_id"] == "attempt-1"
        assert detail["artifacts"][0]["artifact_id"] == "artifact-1"
        assert detail["verifications"][0]["verification_id"] == "verification-1"
        assert "children" in detail["relationships"]
        assert "same_source" in detail["relationships"]
        assert prime_result["turn"]["action"] in {"direct_reply", "deferred", "failover_reply"}
        assert local_result["turn"]["action"] in {"direct_reply", "degraded_reply"}
        assert snapshot["communications"]["prime_inbox"][0]["message"] == "Hello Prime"
        assert snapshot["communications"]["local_inbox"][0]["message"] == "Hello Local"
        assert snapshot["communications"]["prime_conversation"][0]["message"] == "Hello Prime"
        assert len(snapshot["communications"]["prime_conversation"]) >= 2


def test_ui_service_snapshot_reports_inference_spend():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        db.upsert_task(
            TaskRecord(
                task_id="worker-task-1",
                title="Cheap lane worker",
                description="Track delegated worker state in telemetry.",
                status="working",
                provenance={
                    "source": "worker_delegation",
                    "route": {"provider": "cli", "cli_tool": "gemini-cli", "model": "gemini-2.5-flash"},
                },
            )
        )
        db.upsert_task(
            TaskRecord(
                task_id="review-task-1",
                title="Review route",
                description="This review should ideally stay off Prime.",
                status="complete",
                risk="low",
                provenance={"task_class": "review"},
                completion_policy={"type": "review_or_audit"},
            )
        )
        db.upsert_task(
            TaskRecord(
                task_id="pending-batch-1",
                title="Batch me later",
                description="Low-risk pending maintenance that should be batchable.",
                status="pending",
                risk="low",
                priority=2,
                urgency=2,
                provenance={"task_class": "maintenance"},
                completion_policy={"type": "respond_or_execute"},
            )
        )
        db.upsert_attempt(
            AttemptRecord(
                attempt_id="attempt-provider-1",
                task_id="worker-task-1",
                actor="worker.gemini-cli.gemini-2-5-flash",
                outcome="succeeded",
                result_summary="Delegated worker completed.",
                verification_status="passed",
                resource_usage={
                    "implementation": {
                        "generation_mode": "provider",
                        "resolved_route": {"provider": "cli", "cli_tool": "gemini-cli", "model": "gemini-2.5-flash"},
                    }
                },
                started_at="2026-04-09T00:00:00+00:00",
                ended_at="2026-04-09T00:05:00+00:00",
            )
        )
        db.upsert_attempt(
            AttemptRecord(
                attempt_id="attempt-prime-1",
                task_id="review-task-1",
                actor="loop0:codex",
                outcome="succeeded",
                result_summary="Prime handled a review task.",
                verification_status="passed",
                resource_usage={
                    "implementation": {
                        "generation_mode": "provider",
                        "resolved_route": {"provider": "codex", "model": "gpt-5.4"},
                    }
                },
                started_at="2026-04-09T00:10:00+00:00",
                ended_at="2026-04-09T00:11:00+00:00",
            )
        )
        service = AstrataUIService(settings=settings)
        snapshot = service.snapshot()
        assert snapshot["inference"]["spent_attempts"] == 2
        assert snapshot["inference"]["spent_by_model"]["gemini-2.5-flash"] == 1
        assert snapshot["inference"]["spent_by_task_class"]["review"] == 1
        assert snapshot["inference"]["worker_statuses"]["working"] == 1
        assert snapshot["inference"]["prime_spend_attempts"] == 1
        assert snapshot["inference"]["avoidable_prime_attempts"] == 1
        assert snapshot["inference"]["prime_review_attempts"] == 1
        assert snapshot["inference"]["prime_consensus_misses"] == 1
        assert snapshot["inference"]["batchable_pending_tasks"] == 1
        assert snapshot["inference"]["avoidable_prime_examples"][0]["task_id"] == "review-task-1"
        assert snapshot["inference"]["quota_snapshot_count"] >= 1
