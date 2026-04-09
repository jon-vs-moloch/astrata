"""Minimal SQLite durability for Phase 0 records."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from astrata.records.communications import CommunicationRecord
from astrata.records.models import ArtifactRecord, AttemptRecord, TaskRecord, VerificationRecord


class AstrataDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        attempts = 3
        last_error: sqlite3.OperationalError | None = None
        for index in range(attempts):
            try:
                conn = sqlite3.connect(self.path, timeout=5.0)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                return conn
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "unable to open database file" not in str(exc).lower() or index == attempts - 1:
                    detail = (
                        f"{exc}; path={self.path}; "
                        f"parent_exists={self.path.parent.exists()}; "
                        f"parent_is_dir={self.path.parent.is_dir()}"
                    )
                    raise sqlite3.OperationalError(detail) from exc
                time.sleep(0.05 * (index + 1))
        if last_error is not None:
            raise last_error
        raise sqlite3.OperationalError(f"Failed to connect to database at {self.path}")

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS verifications (
                    verification_id TEXT PRIMARY KEY,
                    target_kind TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS communications (
                    communication_id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def list_records(self, table: str) -> list[dict[str, Any]]:
        query = f"SELECT payload_json FROM {table}"
        with self.connect() as conn:
            rows = conn.execute(query).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def upsert_task(self, task: TaskRecord) -> None:
        self._upsert("tasks", "task_id", task.task_id, task.model_dump(mode="json"))

    def upsert_attempt(self, attempt: AttemptRecord) -> None:
        self._upsert(
            "attempts",
            "attempt_id",
            attempt.attempt_id,
            attempt.model_dump(mode="json"),
            extra={"task_id": attempt.task_id},
        )

    def upsert_artifact(self, artifact: ArtifactRecord) -> None:
        self._upsert("artifacts", "artifact_id", artifact.artifact_id, artifact.model_dump(mode="json"))

    def upsert_verification(self, verification: VerificationRecord) -> None:
        self._upsert(
            "verifications",
            "verification_id",
            verification.verification_id,
            verification.model_dump(mode="json"),
            extra={"target_kind": verification.target_kind, "target_id": verification.target_id},
        )

    def upsert_communication(self, communication: CommunicationRecord) -> None:
        self._upsert(
            "communications",
            "communication_id",
            communication.communication_id,
            communication.model_dump(mode="json"),
            extra={
                "channel": communication.channel,
                "recipient": communication.recipient,
                "status": communication.status,
            },
        )

    def _upsert(
        self,
        table: str,
        id_col: str,
        record_id: str,
        payload: dict[str, Any],
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            if table == "attempts":
                conn.execute(
                    """
                    INSERT INTO attempts (attempt_id, task_id, payload_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(attempt_id) DO UPDATE SET
                        task_id = excluded.task_id,
                        payload_json = excluded.payload_json
                    """,
                    (record_id, extra["task_id"], json.dumps(payload)),
                )
                return
            if table == "verifications":
                conn.execute(
                    """
                    INSERT INTO verifications (verification_id, target_kind, target_id, payload_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(verification_id) DO UPDATE SET
                        target_kind = excluded.target_kind,
                        target_id = excluded.target_id,
                        payload_json = excluded.payload_json
                    """,
                    (record_id, extra["target_kind"], extra["target_id"], json.dumps(payload)),
                )
                return
            if table == "communications":
                conn.execute(
                    """
                    INSERT INTO communications (communication_id, channel, recipient, status, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(communication_id) DO UPDATE SET
                        channel = excluded.channel,
                        recipient = excluded.recipient,
                        status = excluded.status,
                        payload_json = excluded.payload_json
                    """,
                    (
                        record_id,
                        extra["channel"],
                        extra["recipient"],
                        extra["status"],
                        json.dumps(payload),
                    ),
                )
                return
            conn.execute(
                f"""
                INSERT INTO {table} ({id_col}, payload_json)
                VALUES (?, ?)
                ON CONFLICT({id_col}) DO UPDATE SET payload_json = excluded.payload_json
                """,
                (record_id, json.dumps(payload)),
            )
