"""Minimal SQLite durability for Phase 0 records."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator

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
                CREATE TABLE IF NOT EXISTS archive_summaries (
                    summary_id TEXT PRIMARY KEY,
                    record_kind TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def initialize_archive_summaries(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS archive_summaries (
                    summary_id TEXT PRIMARY KEY,
                    record_kind TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )

    def _validated_table_name(self, table: str) -> str:
        allowed = {
            "tasks",
            "attempts",
            "artifacts",
            "verifications",
            "communications",
            "archive_summaries",
        }
        if table not in allowed:
            raise ValueError(f"Unsupported table: {table}")
        return table

    def _validated_column_name(self, column: str) -> str:
        allowed = {
            "task_id",
            "attempt_id",
            "artifact_id",
            "verification_id",
            "communication_id",
            "summary_id",
            "status",
            "recipient",
            "channel",
            "record_kind",
            "record_id",
            "target_kind",
            "target_id",
        }
        if column not in allowed:
            raise ValueError(f"Unsupported column: {column}")
        return column

    def list_records(self, table: str) -> list[dict[str, Any]]:
        table_name = self._validated_table_name(table)
        query = f"SELECT payload_json FROM {table_name}"
        with self.connect() as conn:
            rows = conn.execute(query).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def iter_records(
        self,
        table: str,
        *,
        where: dict[str, Any] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        table_name = self._validated_table_name(table)
        query = f"SELECT payload_json FROM {table_name}"
        params: list[Any] = []
        if where:
            clauses: list[str] = []
            for key, value in where.items():
                column_name = self._validated_column_name(key)
                clauses.append(f"{column_name} = ?")
                params.append(value)
            query += f" WHERE {' AND '.join(clauses)}"
        if order_by:
            order_column = self._validated_column_name(order_by)
            query += f" ORDER BY {order_column} {'DESC' if descending else 'ASC'}"
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        for row in rows:
            yield json.loads(row["payload_json"])

    def get_record(self, table: str, id_column: str, record_id: str) -> dict[str, Any] | None:
        table_name = self._validated_table_name(table)
        column_name = self._validated_column_name(id_column)
        query = f"SELECT payload_json FROM {table_name} WHERE {column_name} = ? LIMIT 1"
        with self.connect() as conn:
            row = conn.execute(query, (record_id,)).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def count_records(self, table: str) -> int:
        table_name = self._validated_table_name(table)
        with self.connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
        return int(row["count"] or 0)

    def list_archive_summaries(self, *, record_kind: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT payload_json FROM archive_summaries"
        params: tuple[Any, ...] = ()
        if record_kind:
            query += " WHERE record_kind = ?"
            params = (record_kind,)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
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

    def upsert_archive_summary(self, summary: dict[str, Any]) -> None:
        self._upsert(
            "archive_summaries",
            "summary_id",
            str(summary.get("summary_id") or ""),
            summary,
            extra={
                "record_kind": str(summary.get("record_kind") or ""),
                "record_id": str(summary.get("record_id") or ""),
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
            if table == "archive_summaries":
                conn.execute(
                    """
                    INSERT INTO archive_summaries (summary_id, record_kind, record_id, payload_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(summary_id) DO UPDATE SET
                        record_kind = excluded.record_kind,
                        record_id = excluded.record_id,
                        payload_json = excluded.payload_json
                    """,
                    (
                        record_id,
                        extra["record_kind"],
                        extra["record_id"],
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
