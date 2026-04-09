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
        assert snapshot["queue"]["counts"]["pending"] == 1
        assert snapshot["communications"]["astrata_inbox"][0]["message"] == "Hello from UI"
        assert snapshot["communications"]["prime_conversation"] == []


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
        assert prime_result["turn"]["action"] == "direct_reply"
        assert local_result["turn"]["action"] in {"direct_reply", "degraded_reply"}
        assert snapshot["communications"]["prime_inbox"][0]["message"] == "Hello Prime"
        assert snapshot["communications"]["local_inbox"][0]["message"] == "Hello Local"
        assert snapshot["communications"]["prime_conversation"][0]["message"] == "Hello Prime"
        assert len(snapshot["communications"]["prime_conversation"]) >= 2
