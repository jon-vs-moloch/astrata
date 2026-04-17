from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3

from astrata.records.communications import CommunicationRecord
from astrata.records.models import ArtifactRecord, AttemptRecord, TaskRecord, VerificationRecord
from astrata.storage.archive import (
    HotRetentionPolicy,
    RuntimeHygieneManager,
    RuntimeHygienePolicy,
    RuntimeStateArchiver,
    compact_oversized_runtime_records,
)
from astrata.storage.db import AstrataDatabase
from astrata.storage.hygiene import reconcile_running_attempts


def test_database_initializes_and_accepts_records(tmp_path: Path | None = None):
    def _run(base: Path) -> None:
        db = AstrataDatabase(base / "astrata.db")
        db.initialize()

        task = TaskRecord(title="Bootstrap", description="Wake Loop 0")
        attempt = AttemptRecord(task_id=task.task_id, actor="test-runner")
        artifact = ArtifactRecord(artifact_type="note", title="Bootstrap artifact")
        verification = VerificationRecord(target_kind="task", target_id=task.task_id, verifier="pytest")
        communication = CommunicationRecord(channel="operator", sender="principal", recipient="astrata")

        db.upsert_task(task)
        db.upsert_attempt(attempt)
        db.upsert_artifact(artifact)
        db.upsert_verification(verification)
        db.upsert_communication(communication)

        assert db.path.exists()
        assert db.list_records("communications")

    if tmp_path is not None:
        _run(tmp_path)
        return
    with TemporaryDirectory() as tmp:
        _run(Path(tmp))


def test_database_retries_transient_open_failures(monkeypatch, tmp_path: Path):
    db = AstrataDatabase(tmp_path / ".astrata" / "astrata.db")
    real_connect = sqlite3.connect
    attempts = {"count": 0}

    def flaky_connect(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", flaky_connect)
    db.initialize()

    assert attempts["count"] >= 2
    assert db.path.exists()


def test_database_supports_streaming_iteration_and_point_lookup(tmp_path: Path):
    db = AstrataDatabase(tmp_path / "astrata.db")
    db.initialize()

    task = TaskRecord(task_id="task-1", title="Bootstrap", description="Wake Loop 0")
    pending = CommunicationRecord(
        communication_id="comm-pending",
        channel="operator",
        sender="principal",
        recipient="astrata",
        status="queued",
        payload={"message": "hello"},
    )
    resolved = CommunicationRecord(
        communication_id="comm-resolved",
        channel="operator",
        sender="principal",
        recipient="astrata",
        status="resolved",
        payload={"message": "done"},
    )

    db.upsert_task(task)
    db.upsert_communication(pending)
    db.upsert_communication(resolved)

    looked_up = db.get_record("tasks", "task_id", "task-1")
    pending_messages = list(db.iter_records("communications", where={"status": "queued"}))

    assert looked_up is not None
    assert looked_up["task_id"] == "task-1"
    assert [item["communication_id"] for item in pending_messages] == ["comm-pending"]


def test_runtime_hygiene_closes_running_attempts_for_terminal_tasks(tmp_path: Path):
    db = AstrataDatabase(tmp_path / "astrata.db")
    db.initialize()
    db.upsert_task(
        TaskRecord(
            task_id="task-done",
            title="Done",
            description="Already completed.",
            status="complete",
        )
    )
    db.upsert_attempt(
        AttemptRecord(
            attempt_id="attempt-running",
            task_id="task-done",
            actor="loop0:cli",
            outcome="running",
            ended_at=None,
        )
    )

    result = reconcile_running_attempts(db)
    stored = db.get_record("attempts", "attempt_id", "attempt-running")

    assert result["closed_attempts"] == 1
    assert stored is not None
    assert stored["outcome"] == "succeeded"
    assert stored["ended_at"]
    assert stored["provenance"]["runtime_hygiene"]["reason"] == "task_completed_while_attempt_was_running"


def test_runtime_archiver_rebuilds_compact_hot_state(tmp_path: Path):
    live_db = tmp_path / "astrata.db"
    db = AstrataDatabase(live_db)
    db.initialize()

    active_task = TaskRecord(task_id="task-active", title="Active", description="Still pending.", status="pending")
    archived_task = TaskRecord(task_id="task-old", title="Old", description="Completed.", status="complete")
    db.upsert_task(active_task)
    db.upsert_task(archived_task)
    db.upsert_attempt(
        AttemptRecord(
            attempt_id="attempt-active",
            task_id=active_task.task_id,
            actor="loop0",
            outcome="running",
        )
    )
    db.upsert_attempt(
        AttemptRecord(
            attempt_id="attempt-old",
            task_id=archived_task.task_id,
            actor="loop0",
            outcome="succeeded",
            ended_at="2026-04-01T00:00:00+00:00",
            attempt_reason="old run",
            resource_usage={"route": {"provider": "cli", "cli_tool": "kilocode"}},
        )
    )
    db.upsert_artifact(
        ArtifactRecord(
            artifact_id="artifact-old",
            artifact_type="loop0_gap_report",
            title="Old artifact",
            content_summary="Cold artifact.",
        )
    )
    db.upsert_communication(
        CommunicationRecord(
            communication_id="comm-old",
            channel="operator",
            sender="astrata",
            recipient="principal",
            status="resolved",
            intent="loop0_result",
            payload={"message": "Completed long ago."},
            resolved_at="2026-04-01T00:00:00+00:00",
        )
    )
    db.upsert_verification(
        VerificationRecord(
            verification_id="verification-old",
            target_kind="task",
            target_id=archived_task.task_id,
            verifier="pytest",
            result="pass",
        )
    )

    archiver = RuntimeStateArchiver(
        live_db=live_db,
        archive_dir=tmp_path / "archive",
        retention=HotRetentionPolicy(
            keep_terminal_tasks=0,
            keep_terminal_attempts=0,
            keep_artifacts=0,
            keep_resolved_communications=0,
            keep_verifications=0,
        ),
    )
    summary = archiver.archive_and_rebuild()

    compact_db = AstrataDatabase(live_db)
    compact_db.initialize()
    tasks = {item["task_id"] for item in compact_db.list_records("tasks")}
    attempts = {item["attempt_id"] for item in compact_db.list_records("attempts")}
    summaries = compact_db.list_archive_summaries()

    assert summary.previous_size_bytes >= summary.current_size_bytes
    assert summary.archived_counts["communications"] >= 1
    assert "task-active" in tasks
    assert "attempt-active" in attempts
    assert any(item["record_kind"] == "task" and item["record_id"] == "task-old" for item in summaries)
    assert any(item["record_kind"] == "communication_rollup" for item in summaries)


def test_compact_oversized_runtime_records_trims_large_attempt_fields(tmp_path: Path):
    db_path = tmp_path / "astrata.db"
    db = AstrataDatabase(db_path)
    db.initialize()

    task = TaskRecord(task_id="task-big", title="Big", description="Large attempt payload.")
    db.upsert_task(task)
    db.upsert_attempt(
        AttemptRecord(
            attempt_id="attempt-big",
            task_id=task.task_id,
            actor="loop0",
            provenance={"huge": "x" * 5000},
            resource_usage={"huge": "y" * 5000},
            outcome="failed",
        )
    )

    summary = compact_oversized_runtime_records(
        live_db=db_path,
        snapshot_hint="archive/test.db",
        threshold_bytes=1000,
    )
    updated_attempt = db.get_record("attempts", "attempt_id", "attempt-big")

    assert summary["changed_records"] == 1
    assert summary["counts_by_table"]["attempts"] == 1
    assert updated_attempt is not None
    assert updated_attempt["provenance"]["archived"] is True
    assert updated_attempt["resource_usage"]["archived"] is True


def test_runtime_hygiene_manager_compacts_and_persists_state(tmp_path: Path):
    db_path = tmp_path / "astrata.db"
    db = AstrataDatabase(db_path)
    db.initialize()

    task = TaskRecord(task_id="task-hygiene", title="Hygiene", description="Large payload.")
    db.upsert_task(task)
    db.upsert_attempt(
        AttemptRecord(
            attempt_id="attempt-hygiene",
            task_id=task.task_id,
            actor="loop0",
            provenance={"huge": "x" * 5000},
            resource_usage={"huge": "y" * 5000},
            outcome="failed",
        )
    )

    manager = RuntimeHygieneManager(
        live_db=db_path,
        archive_dir=tmp_path / "archive",
        state_path=tmp_path / "runtime_hygiene_state.json",
        policy=RuntimeHygienePolicy(
            oversized_threshold_bytes=4000,
            compact_check_interval_seconds=0,
            vacuum_min_size_bytes=10**12,
            vacuum_interval_seconds=0,
        ),
    )
    result = manager.maintain()
    updated_attempt = db.get_record("attempts", "attempt_id", "attempt-hygiene")

    assert result["status"] == "ok"
    assert result["compaction"]["changed_records"] == 1
    assert result["inspection"]["oversized_record_count"] == 0
    assert manager.state_path.exists()
    assert updated_attempt is not None
    assert updated_attempt["provenance"]["archived"] is True
