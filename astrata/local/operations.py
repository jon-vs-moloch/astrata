"""Tracked long-running local-runtime operations."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperationProgress(BaseModel):
    current_bytes: int | None = None
    total_bytes: int | None = None
    percent: float | None = None
    message: str | None = None


class OperationRecord(BaseModel):
    operation_id: str = Field(default_factory=lambda: f"op_{uuid4()}")
    kind: str
    status: Literal["running", "succeeded", "failed"] = "running"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    progress: OperationProgress | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class OperationTracker:
    def __init__(self, *, state_path: Path | None = None) -> None:
        self._state_path = state_path
        if self._state_path is not None:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._operations: dict[str, OperationRecord] = self._load()

    def list_operations(self) -> list[OperationRecord]:
        return sorted(self._operations.values(), key=lambda op: op.created_at, reverse=True)

    def get_operation(self, operation_id: str) -> OperationRecord | None:
        return self._operations.get(operation_id)

    def start_operation(self, kind: str, progress: OperationProgress | None = None) -> OperationRecord:
        record = OperationRecord(kind=kind, progress=progress)
        self._operations[record.operation_id] = record
        self._store()
        return record

    def update_operation(self, operation_id: str, progress: OperationProgress) -> OperationRecord:
        current = self._require(operation_id)
        updated = current.model_copy(update={"updated_at": _now_iso(), "progress": progress})
        self._operations[operation_id] = updated
        self._store()
        return updated

    def complete_operation(self, operation_id: str, result: dict[str, Any]) -> OperationRecord:
        current = self._require(operation_id)
        updated = current.model_copy(
            update={"status": "succeeded", "updated_at": _now_iso(), "result": result}
        )
        self._operations[operation_id] = updated
        self._store()
        return updated

    def fail_operation(self, operation_id: str, error: str) -> OperationRecord:
        current = self._require(operation_id)
        updated = current.model_copy(update={"status": "failed", "updated_at": _now_iso(), "error": error})
        self._operations[operation_id] = updated
        self._store()
        return updated

    def _require(self, operation_id: str) -> OperationRecord:
        current = self._operations.get(operation_id)
        if current is None:
            raise KeyError(f"Unknown operation: {operation_id}")
        return current

    def _load(self) -> dict[str, OperationRecord]:
        if self._state_path is None or not self._state_path.exists():
            return {}
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        records: dict[str, OperationRecord] = {}
        for item in payload if isinstance(payload, list) else []:
            try:
                record = OperationRecord.model_validate(item)
            except Exception:
                continue
            records[record.operation_id] = record
        return records

    def _store(self) -> None:
        if self._state_path is None:
            return
        payload = [record.model_dump(mode="json") for record in self.list_operations()]
        self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
