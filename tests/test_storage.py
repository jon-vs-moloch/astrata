from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3

from astrata.records.communications import CommunicationRecord
from astrata.records.models import ArtifactRecord, AttemptRecord, TaskRecord, VerificationRecord
from astrata.storage.db import AstrataDatabase


def test_database_initializes_and_accepts_records(tmp_path: Path | None = None):
    def _run(base: Path) -> None:
        db = AstrataDatabase(base / "astrata.db")
        db.initialize()

        task = TaskRecord(title="Bootstrap", description="Wake Loop 0")
        attempt = AttemptRecord(task_id=task.task_id, actor="test-runner")
        artifact = ArtifactRecord(artifact_type="note", title="Bootstrap artifact")
        verification = VerificationRecord(target_kind="task", target_id=task.task_id, verifier="pytest")
        communication = CommunicationRecord(channel="operator", sender="operator", recipient="astrata")

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
