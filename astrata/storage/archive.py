"""Archive and rebuild hot runtime state for Astrata's SQLite record store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrata.storage.db import AstrataDatabase


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except Exception:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


@dataclass(frozen=True)
class HotRetentionPolicy:
    keep_terminal_tasks: int = 96
    keep_terminal_attempts: int = 64
    keep_artifacts: int = 0
    keep_resolved_communications: int = 0
    keep_verifications: int = 0


@dataclass(frozen=True)
class ArchiveRunSummary:
    archive_path: str
    previous_live_path: str
    hot_live_path: str
    archived_counts: dict[str, int]
    hot_counts: dict[str, int]
    summary_count: int
    previous_size_bytes: int
    current_size_bytes: int


@dataclass(frozen=True)
class RuntimeHygienePolicy:
    oversized_threshold_bytes: int = 10_000_000
    compact_check_interval_seconds: int = 900
    vacuum_min_size_bytes: int = 1_000_000_000
    vacuum_interval_seconds: int = 21_600


class RuntimeStateArchiver:
    def __init__(
        self,
        *,
        live_db: Path,
        archive_dir: Path,
        retention: HotRetentionPolicy | None = None,
    ) -> None:
        self.live_db = live_db
        self.archive_dir = archive_dir
        self.retention = retention or HotRetentionPolicy()
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def archive_and_rebuild(self) -> ArchiveRunSummary:
        if not self.live_db.exists():
            raise RuntimeError(f"Live database does not exist: {self.live_db}")
        stamp = _now().strftime("%Y%m%d_%H%M%S")
        archive_path = self.archive_dir / f"astrata_runtime_{stamp}.db"
        hot_path = self.archive_dir / f"astrata_hot_rebuild_{stamp}.db"
        self._snapshot_live_db(archive_path)
        summary = self._rebuild_hot_db(snapshot_path=archive_path, hot_path=hot_path)
        live_shm = self.live_db.with_name(self.live_db.name + "-shm")
        live_wal = self.live_db.with_name(self.live_db.name + "-wal")
        if live_shm.exists():
            live_shm.unlink()
        if live_wal.exists():
            live_wal.unlink()
        if self.live_db.exists():
            self.live_db.unlink()
        self._snapshot_db(source_path=hot_path, target_path=self.live_db)
        hot_path.unlink(missing_ok=True)
        return ArchiveRunSummary(
            archive_path=str(archive_path),
            previous_live_path=str(archive_path),
            hot_live_path=str(self.live_db),
            archived_counts=summary["archived_counts"],
            hot_counts=summary["hot_counts"],
            summary_count=summary["summary_count"],
            previous_size_bytes=archive_path.stat().st_size,
            current_size_bytes=self.live_db.stat().st_size,
        )

    def _snapshot_live_db(self, target_path: Path) -> None:
        self._snapshot_db(source_path=self.live_db, target_path=target_path)

    def _snapshot_db(self, *, source_path: Path, target_path: Path) -> None:
        source = sqlite3.connect(source_path)
        try:
            target = sqlite3.connect(target_path)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()

    def _rebuild_hot_db(self, *, snapshot_path: Path, hot_path: Path) -> dict[str, Any]:
        snapshot = AstrataDatabase(snapshot_path)
        hot = AstrataDatabase(hot_path)
        hot.initialize()
        hot.initialize_archive_summaries()

        tasks = snapshot.list_records("tasks")
        attempts = snapshot.list_records("attempts")
        hot_attempts, archived_attempts = self._partition_attempts(attempts)
        hot_communications, archived_communications, communication_rollup = self._partition_communications(snapshot_path)
        retained_task_ids = {
            str(item.get("task_id") or "")
            for item in hot_attempts
            if str(item.get("task_id") or "").strip()
        }
        for communication in hot_communications:
            for task_id in list(communication.get("related_task_ids") or []):
                if str(task_id).strip():
                    retained_task_ids.add(str(task_id))
        hot_tasks, archived_tasks = self._partition_tasks(tasks, retained_task_ids=retained_task_ids)
        hot_artifacts: list[dict[str, Any]] = []
        archived_artifacts: list[dict[str, Any]] = []
        artifact_summary_rollup: dict[str, Any] | None = None
        if self.retention.keep_artifacts > 0:
            artifacts = snapshot.list_records("artifacts")
            hot_artifacts, archived_artifacts = self._partition_artifacts(artifacts)
        else:
            archived_artifact_count = snapshot.count_records("artifacts")
            if archived_artifact_count > 0:
                artifact_summary_rollup = {
                    "summary_id": "artifact-rollup:archived",
                    "record_kind": "artifact_rollup",
                    "record_id": "archived",
                    "title": "Archived artifacts",
                    "status": "archived",
                    "summary": f"{archived_artifact_count} artifact records were archived out of hot runtime state.",
                    "recorded_at": _now().isoformat(),
                    "metadata": {"archived_count": archived_artifact_count},
                }
        hot_task_ids = {str(item.get("task_id") or "") for item in hot_tasks}

        for task in hot_tasks:
            hot._upsert("tasks", "task_id", str(task.get("task_id")), task)
        for attempt in hot_attempts:
            hot._upsert(
                "attempts",
                "attempt_id",
                str(attempt.get("attempt_id")),
                attempt,
                extra={"task_id": str(attempt.get("task_id") or "")},
            )
        for artifact in hot_artifacts:
            hot._upsert("artifacts", "artifact_id", str(artifact.get("artifact_id")), artifact)
        kept_verifications: list[dict[str, Any]] = []
        archived_verifications: list[dict[str, Any]] = []
        verification_rollup: dict[str, Any] | None = None
        if self.retention.keep_verifications > 0:
            verifications = snapshot.list_records("verifications")
            kept_verifications, archived_verifications = self._partition_verifications(
                verifications,
                hot_task_ids=hot_task_ids,
                keep_limit=self.retention.keep_verifications,
            )
        else:
            verification_count = snapshot.count_records("verifications")
            if verification_count > 0:
                verification_rollup = {
                    "summary_id": "verification-rollup:archived",
                    "record_kind": "verification_rollup",
                    "record_id": "archived",
                    "title": "Archived verifications",
                    "status": "archived",
                    "summary": f"{verification_count} verification records were archived out of hot runtime state.",
                    "recorded_at": _now().isoformat(),
                    "metadata": {"archived_count": verification_count},
                }
        for verification in kept_verifications:
            hot._upsert(
                "verifications",
                "verification_id",
                str(verification.get("verification_id")),
                verification,
                extra={
                    "target_kind": str(verification.get("target_kind") or ""),
                    "target_id": str(verification.get("target_id") or ""),
                },
            )
        for communication in hot_communications:
            hot._upsert(
                "communications",
                "communication_id",
                str(communication.get("communication_id")),
                communication,
                extra={
                    "channel": str(communication.get("channel") or ""),
                    "recipient": str(communication.get("recipient") or ""),
                    "status": str(communication.get("status") or ""),
                },
            )

        summary_records = [
            *[self._summary_record("task", item) for item in archived_tasks],
            *[self._summary_record("attempt", item) for item in archived_attempts],
            *[self._summary_record("verification", item) for item in archived_verifications],
            *[self._summary_record("communication", item) for item in archived_communications],
        ]
        if artifact_summary_rollup is not None:
            summary_records.append(artifact_summary_rollup)
        elif archived_artifacts:
            summary_records.extend(self._summary_record("artifact", item) for item in archived_artifacts)
        if verification_rollup is not None:
            summary_records.append(verification_rollup)
        if communication_rollup is not None:
            summary_records.append(communication_rollup)
        for summary in summary_records:
            hot.upsert_archive_summary(summary)
        with hot.connect() as conn:
            conn.execute("VACUUM")
        return {
            "archived_counts": {
                "tasks": len(archived_tasks),
                "attempts": len(archived_attempts),
                "artifacts": len(archived_artifacts)
                or int(dict(artifact_summary_rollup or {}).get("metadata", {}).get("archived_count", 0) or 0),
                "verifications": len(archived_verifications)
                or int(dict(verification_rollup or {}).get("metadata", {}).get("archived_count", 0) or 0),
                "communications": len(archived_communications)
                or int(dict(communication_rollup or {}).get("metadata", {}).get("archived_count", 0) or 0),
            },
            "hot_counts": {
                "tasks": len(hot_tasks),
                "attempts": len(hot_attempts),
                "artifacts": len(hot_artifacts),
                "verifications": len(kept_verifications),
                "communications": len(hot_communications),
            },
            "summary_count": len(summary_records),
        }

    def _partition_tasks(
        self,
        tasks: list[dict[str, Any]],
        *,
        retained_task_ids: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        retained_task_ids = retained_task_ids or set()
        active = [
            item
            for item in tasks
            if str(item.get("status") or "") in {"pending", "working", "blocked"}
            or str(item.get("task_id") or "") in retained_task_ids
        ]
        terminal = [item for item in tasks if item not in active]
        terminal_sorted = sorted(
            terminal,
            key=lambda item: _coerce_time(item.get("updated_at") or item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        hot_terminal = terminal_sorted[: self.retention.keep_terminal_tasks]
        archived = terminal_sorted[self.retention.keep_terminal_tasks :]
        return [*active, *hot_terminal], archived

    def _partition_attempts(self, attempts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        active = [item for item in attempts if not item.get("ended_at") or str(item.get("outcome") or "") == "running"]
        terminal = [item for item in attempts if item not in active]
        terminal_sorted = sorted(
            terminal,
            key=lambda item: _coerce_time(item.get("ended_at") or item.get("started_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        hot_terminal = terminal_sorted[: self.retention.keep_terminal_attempts]
        archived = terminal_sorted[self.retention.keep_terminal_attempts :]
        return [*active, *hot_terminal], archived

    def _partition_artifacts(self, artifacts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        sorted_items = sorted(
            artifacts,
            key=lambda item: _coerce_time(item.get("updated_at") or item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        hot = sorted_items[: self.retention.keep_artifacts]
        archived = sorted_items[self.retention.keep_artifacts :]
        return hot, archived

    def _partition_communications(self, snapshot_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
        with sqlite3.connect(snapshot_path) as conn:
            conn.row_factory = sqlite3.Row
            active_rows = conn.execute(
                """
                SELECT payload_json
                FROM communications
                WHERE json_extract(payload_json, '$.status') NOT IN ('acknowledged', 'resolved')
                """
            ).fetchall()
            active = [json.loads(row["payload_json"]) for row in active_rows]
            resolved_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM communications
                    WHERE json_extract(payload_json, '$.status') IN ('acknowledged', 'resolved')
                    """
                ).fetchone()[0]
                or 0
            )
            if self.retention.keep_resolved_communications <= 0:
                rollup = None
                if resolved_count > 0:
                    rollup = {
                        "summary_id": "communication-rollup:archived",
                        "record_kind": "communication_rollup",
                        "record_id": "archived",
                        "title": "Archived communications",
                        "status": "archived",
                        "summary": f"{resolved_count} resolved or acknowledged communication records were archived out of hot runtime state.",
                        "recorded_at": _now().isoformat(),
                        "metadata": {"archived_count": resolved_count},
                    }
                return active, [], rollup
            terminal_rows = conn.execute(
                """
                SELECT payload_json
                FROM communications
                WHERE json_extract(payload_json, '$.status') IN ('acknowledged', 'resolved')
                ORDER BY COALESCE(
                    json_extract(payload_json, '$.resolved_at'),
                    json_extract(payload_json, '$.acknowledged_at'),
                    json_extract(payload_json, '$.delivered_at'),
                    json_extract(payload_json, '$.created_at')
                ) DESC
                """
            ).fetchall()
        terminal = [json.loads(row["payload_json"]) for row in terminal_rows]
        hot_terminal = terminal[: self.retention.keep_resolved_communications]
        return [*active, *hot_terminal], terminal[self.retention.keep_resolved_communications :], None

    def _partition_verifications(
        self,
        verifications: list[dict[str, Any]],
        *,
        hot_task_ids: set[str],
        keep_limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        related = [
            item
            for item in verifications
            if str(item.get("target_kind") or "") == "task" and str(item.get("target_id") or "") in hot_task_ids
        ]
        unrelated = [item for item in verifications if item not in related]
        unrelated_sorted = sorted(
            unrelated,
            key=lambda item: _coerce_time(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        hot_unrelated = unrelated_sorted[:keep_limit]
        archived = unrelated_sorted[keep_limit:]
        hot = [*related, *hot_unrelated]
        deduped: dict[str, dict[str, Any]] = {str(item.get("verification_id")): item for item in hot}
        return list(deduped.values()), archived

    def _summary_record(self, record_kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        summary_id = f"{record_kind}:{payload.get(self._id_key(record_kind), '')}"
        if record_kind == "task":
            summary = {
                "summary_id": summary_id,
                "record_kind": record_kind,
                "record_id": str(payload.get("task_id") or ""),
                "title": str(payload.get("title") or ""),
                "status": str(payload.get("status") or ""),
                "summary": str(payload.get("description") or ""),
                "recorded_at": str(payload.get("updated_at") or payload.get("created_at") or ""),
                "metadata": {"priority": payload.get("priority"), "urgency": payload.get("urgency")},
            }
        elif record_kind == "attempt":
            route = dict(payload.get("resource_usage") or {}).get("route") or {}
            summary = {
                "summary_id": summary_id,
                "record_kind": record_kind,
                "record_id": str(payload.get("attempt_id") or ""),
                "title": str(payload.get("attempt_reason") or "Attempt"),
                "status": str(payload.get("outcome") or ""),
                "summary": str(payload.get("result_summary") or ""),
                "recorded_at": str(payload.get("ended_at") or payload.get("started_at") or ""),
                "metadata": {"task_id": payload.get("task_id"), "route": route},
            }
        elif record_kind == "artifact":
            summary = {
                "summary_id": summary_id,
                "record_kind": record_kind,
                "record_id": str(payload.get("artifact_id") or ""),
                "title": str(payload.get("title") or ""),
                "status": str(payload.get("status") or ""),
                "summary": str(payload.get("content_summary") or payload.get("description") or ""),
                "recorded_at": str(payload.get("updated_at") or payload.get("created_at") or ""),
                "metadata": {"artifact_type": payload.get("artifact_type"), "lifecycle_state": payload.get("lifecycle_state")},
            }
        elif record_kind == "verification":
            summary = {
                "summary_id": summary_id,
                "record_kind": record_kind,
                "record_id": str(payload.get("verification_id") or ""),
                "title": f"{payload.get('target_kind') or 'unknown'} verification",
                "status": str(payload.get("result") or ""),
                "summary": str(dict(payload.get("evidence") or {}).get("summary") or ""),
                "recorded_at": str(payload.get("created_at") or ""),
                "metadata": {"target_kind": payload.get("target_kind"), "target_id": payload.get("target_id")},
            }
        else:
            summary = {
                "summary_id": summary_id,
                "record_kind": record_kind,
                "record_id": str(payload.get("communication_id") or ""),
                "title": str(payload.get("intent") or payload.get("kind") or "communication"),
                "status": str(payload.get("status") or ""),
                "summary": str(dict(payload.get("payload") or {}).get("message") or ""),
                "recorded_at": str(
                    payload.get("resolved_at")
                    or payload.get("acknowledged_at")
                    or payload.get("delivered_at")
                    or payload.get("created_at")
                    or ""
                ),
                "metadata": {"sender": payload.get("sender"), "recipient": payload.get("recipient"), "channel": payload.get("channel")},
            }
        return summary

    def _id_key(self, record_kind: str) -> str:
        return {
            "task": "task_id",
            "attempt": "attempt_id",
            "artifact": "artifact_id",
            "verification": "verification_id",
            "communication": "communication_id",
        }[record_kind]


def compact_oversized_runtime_records(
    *,
    live_db: Path,
    snapshot_hint: str,
    threshold_bytes: int = 10_000_000,
) -> dict[str, Any]:
    now = _now().isoformat()
    conn = sqlite3.connect(live_db)
    changes: list[dict[str, Any]] = []

    def compact_string(value: str, field: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return (
            f"[archived oversized {field}: trimmed {len(value)} chars at {now}; "
            f"sha256={digest}; snapshot={snapshot_hint}]"
        )

    def compact_json(value: Any, field: str) -> dict[str, Any]:
        encoded = json.dumps(value, sort_keys=True)
        summary: dict[str, Any] = {
            "archived": True,
            "field": field,
            "reason": "oversized_runtime_compaction",
            "archived_at": now,
            "original_json_bytes": len(encoded),
            "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "snapshot": snapshot_hint,
        }
        if isinstance(value, dict):
            summary["original_keys"] = sorted(value.keys())[:64]
        return summary

    def rewrite_payload(table: str, id_field: str, record_id: str, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, sort_keys=True)
        conn.execute(
            f"UPDATE {table} SET payload_json = ? WHERE {id_field} = ?",
            (encoded, record_id),
        )

    for task_id, payload_json in conn.execute(
        "SELECT task_id, payload_json FROM tasks WHERE length(payload_json) > ?",
        (threshold_bytes,),
    ).fetchall():
        payload = json.loads(payload_json)
        provenance = payload.get("provenance")
        if provenance is None or len(json.dumps(provenance, sort_keys=True)) <= threshold_bytes:
            continue
        payload["provenance"] = compact_json(provenance, "task.provenance")
        rewrite_payload("tasks", "task_id", str(task_id), payload)
        changes.append({"table": "tasks", "record_id": str(task_id), "field": "provenance"})

    for attempt_id, payload_json in conn.execute(
        "SELECT attempt_id, payload_json FROM attempts WHERE length(payload_json) > ?",
        (threshold_bytes,),
    ).fetchall():
        payload = json.loads(payload_json)
        updated_fields: list[str] = []
        provenance = payload.get("provenance")
        if provenance is not None and len(json.dumps(provenance, sort_keys=True)) > threshold_bytes:
            payload["provenance"] = compact_json(provenance, "attempt.provenance")
            updated_fields.append("provenance")
        resource_usage = payload.get("resource_usage")
        if resource_usage is not None and len(json.dumps(resource_usage, sort_keys=True)) > threshold_bytes:
            payload["resource_usage"] = compact_json(resource_usage, "attempt.resource_usage")
            updated_fields.append("resource_usage")
        if not updated_fields:
            continue
        rewrite_payload("attempts", "attempt_id", str(attempt_id), payload)
        changes.append(
            {
                "table": "attempts",
                "record_id": str(attempt_id),
                "field": ",".join(updated_fields),
            }
        )

    for communication_id, payload_json in conn.execute(
        "SELECT communication_id, payload_json FROM communications WHERE length(payload_json) > ?",
        (threshold_bytes,),
    ).fetchall():
        payload = json.loads(payload_json)
        communication_payload = payload.get("payload")
        if communication_payload is None or len(json.dumps(communication_payload, sort_keys=True)) <= threshold_bytes:
            continue
        payload["payload"] = compact_json(communication_payload, "communication.payload")
        rewrite_payload("communications", "communication_id", str(communication_id), payload)
        changes.append({"table": "communications", "record_id": str(communication_id), "field": "payload"})

    for artifact_id, payload_json in conn.execute(
        "SELECT artifact_id, payload_json FROM artifacts WHERE length(payload_json) > ?",
        (threshold_bytes,),
    ).fetchall():
        payload = json.loads(payload_json)
        content_summary = payload.get("content_summary")
        if not isinstance(content_summary, str) or len(content_summary) <= threshold_bytes:
            continue
        payload["content_summary"] = compact_string(content_summary, "artifact.content_summary")
        rewrite_payload("artifacts", "artifact_id", str(artifact_id), payload)
        changes.append({"table": "artifacts", "record_id": str(artifact_id), "field": "content_summary"})

    for verification_id, payload_json in conn.execute(
        "SELECT verification_id, payload_json FROM verifications WHERE length(payload_json) > ?",
        (threshold_bytes,),
    ).fetchall():
        payload = json.loads(payload_json)
        evidence = payload.get("evidence")
        if evidence is None or len(json.dumps(evidence, sort_keys=True)) <= threshold_bytes:
            continue
        payload["evidence"] = compact_json(evidence, "verification.evidence")
        rewrite_payload("verifications", "verification_id", str(verification_id), payload)
        changes.append({"table": "verifications", "record_id": str(verification_id), "field": "evidence"})

    conn.commit()
    conn.close()
    counts: dict[str, int] = {}
    for item in changes:
        counts[item["table"]] = counts.get(item["table"], 0) + 1
    return {
        "changed_records": len(changes),
        "counts_by_table": counts,
        "changes": changes,
    }


class RuntimeHygieneManager:
    def __init__(
        self,
        *,
        live_db: Path,
        archive_dir: Path,
        state_path: Path,
        policy: RuntimeHygienePolicy | None = None,
    ) -> None:
        self.live_db = live_db
        self.archive_dir = archive_dir
        self.state_path = state_path
        self.policy = policy or RuntimeHygienePolicy()
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def maintain(self, *, force: bool = False) -> dict[str, Any]:
        state = self._load_state()
        inspection = self.inspect()
        now = _now()
        if not force and not self._should_check(state, now):
            return {
                "status": "skipped",
                "reason": "interval_not_elapsed",
                "inspection": inspection,
                "state": state,
            }

        snapshot_hint = self._latest_snapshot_hint()
        compaction = {
            "changed_records": 0,
            "counts_by_table": {},
            "changes": [],
        }
        if inspection["oversized_record_count"] > 0:
            compaction = compact_oversized_runtime_records(
                live_db=self.live_db,
                snapshot_hint=snapshot_hint,
                threshold_bytes=self.policy.oversized_threshold_bytes,
            )

        vacuumed = False
        if (
            self.live_db.exists()
            and self.live_db.stat().st_size >= self.policy.vacuum_min_size_bytes
            and compaction["changed_records"] > 0
            and self._should_vacuum(state, now)
        ):
            with sqlite3.connect(self.live_db) as conn:
                conn.execute("VACUUM")
            vacuumed = True

        final_inspection = self.inspect()
        updated_state = {
            "last_checked_at": now.isoformat(),
            "last_compaction_at": now.isoformat() if compaction["changed_records"] > 0 else state.get("last_compaction_at"),
            "last_vacuum_at": now.isoformat() if vacuumed else state.get("last_vacuum_at"),
            "last_result": {
                "changed_records": compaction["changed_records"],
                "vacuumed": vacuumed,
                "oversized_record_count": final_inspection["oversized_record_count"],
                "db_size_bytes": final_inspection["db_size_bytes"],
            },
        }
        self.state_path.write_text(json.dumps(updated_state, indent=2, sort_keys=True))
        return {
            "status": "ok",
            "inspection": final_inspection,
            "compaction": compaction,
            "vacuumed": vacuumed,
            "state": updated_state,
        }

    def inspect(self) -> dict[str, Any]:
        counts_by_table: dict[str, int] = {}
        if not self.live_db.exists():
            return {
                "db_path": str(self.live_db),
                "db_exists": False,
                "db_size_bytes": 0,
                "oversized_record_count": 0,
                "oversized_counts_by_table": counts_by_table,
            }
        conn = sqlite3.connect(self.live_db)
        try:
            for table in ("tasks", "attempts", "communications", "artifacts", "verifications"):
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE length(payload_json) > ?",
                    (self.policy.oversized_threshold_bytes,),
                ).fetchone()
                counts_by_table[table] = int((row or [0])[0] or 0)
        finally:
            conn.close()
        return {
            "db_path": str(self.live_db),
            "db_exists": True,
            "db_size_bytes": self.live_db.stat().st_size,
            "oversized_record_count": sum(counts_by_table.values()),
            "oversized_counts_by_table": counts_by_table,
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            payload = json.loads(self.state_path.read_text())
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _should_check(self, state: dict[str, Any], now: datetime) -> bool:
        last_checked = _coerce_time(state.get("last_checked_at"))
        if last_checked is None:
            return True
        elapsed = (now - last_checked).total_seconds()
        return elapsed >= self.policy.compact_check_interval_seconds

    def _should_vacuum(self, state: dict[str, Any], now: datetime) -> bool:
        last_vacuum = _coerce_time(state.get("last_vacuum_at"))
        if last_vacuum is None:
            return True
        elapsed = (now - last_vacuum).total_seconds()
        return elapsed >= self.policy.vacuum_interval_seconds

    def _latest_snapshot_hint(self) -> str:
        snapshots = sorted(self.archive_dir.glob("astrata_runtime_*.db"))
        if snapshots:
            return str(snapshots[-1])
        return str(self.live_db)
