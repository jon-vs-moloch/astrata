"""Runtime hygiene for durable task and attempt state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from astrata.records.models import AttemptRecord
from astrata.storage.db import AstrataDatabase


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def reconcile_running_attempts(
    db: AstrataDatabase,
    *,
    stale_after: timedelta = timedelta(hours=6),
    now: datetime | None = None,
    include_attempts: bool = False,
) -> dict[str, Any]:
    """Close impossible/stale running attempts so active-work views stay honest."""

    resolved_at = now or datetime.now(timezone.utc)
    tasks_by_id = {
        str(task.get("task_id") or ""): task
        for task in db.select_json_fields(
            "tasks",
            fields={
                "task_id": "$.task_id",
                "status": "$.status",
            },
        )
        if str(task.get("task_id") or "").strip()
    }
    updated: list[dict[str, Any]] = []
    for payload in db.iter_records("attempts"):
        if str(payload.get("outcome") or "").strip().lower() != "running":
            continue
        task_id = str(payload.get("task_id") or "").strip()
        task = tasks_by_id.get(task_id)
        task_status = str((task or {}).get("status") or "").strip().lower()
        reason = ""
        outcome = "cancelled"
        if task_status in {"complete", "satisfied"}:
            reason = "task_completed_while_attempt_was_running"
            outcome = "succeeded"
        elif task_status in {"superseded"}:
            reason = "task_superseded_while_attempt_was_running"
            outcome = "cancelled"
        elif task_status in {"failed", "blocked"}:
            reason = f"task_{task_status}_while_attempt_was_running"
            outcome = "blocked" if task_status == "blocked" else "failed"
        else:
            started_at = _parse_datetime(str(payload.get("started_at") or ""))
            if started_at is None or resolved_at - started_at < stale_after:
                continue
            reason = "stale_running_attempt_without_terminal_task"
            outcome = "cancelled"

        provenance = dict(payload.get("provenance") or {})
        resource_usage = dict(payload.get("resource_usage") or {})
        followup_actions = list(payload.get("followup_actions") or [])
        followup_actions.append(
            {
                "type": "runtime_hygiene",
                "status": "closed_stale_running_attempt",
                "reason": reason,
                "closed_at": resolved_at.isoformat(),
            }
        )
        attempt = AttemptRecord(
            **{
                **payload,
                "outcome": outcome,
                "ended_at": payload.get("ended_at") or resolved_at.isoformat(),
                "result_summary": payload.get("result_summary")
                or f"Runtime hygiene closed a running attempt: {reason}.",
                "provenance": {
                    **provenance,
                    "runtime_hygiene": {
                        "reason": reason,
                        "closed_at": resolved_at.isoformat(),
                    },
                },
                "resource_usage": {
                    **resource_usage,
                    "runtime_hygiene": {
                        "reason": reason,
                        "task_status": task_status or None,
                    },
                },
                "followup_actions": followup_actions,
            }
        )
        db.upsert_attempt(attempt)
        updated.append(attempt.model_dump(mode="json"))
    return {
        "status": "ok",
        "closed_attempts": len(updated),
        "closed_attempt_ids": [str(attempt.get("attempt_id") or "") for attempt in updated],
        **({"attempts": updated} if include_attempts else {}),
        "checked_at": resolved_at.isoformat(),
    }
