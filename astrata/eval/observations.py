"""Generic durable eval observations across mutation surfaces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class EvalObservation(BaseModel):
    subject_kind: str
    subject_id: str
    variant_id: str
    task_class: str = "general"
    score: float
    passed: bool = False
    confidence: float = 0.0
    startup_seconds: float | None = None
    execution_seconds: float | None = None
    total_wall_seconds: float | None = None
    output_units: int | None = None
    throughput_units_per_second: float | None = None
    thermal_pressure: str | None = None
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObservationSignal(BaseModel):
    signal_id: str
    signal_kind: str = "observation"
    subject_kind: str
    subject_id: str
    summary: str
    status: str = "open"
    severity: str = "medium"
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def select_signal_followup_policy(*, signal: ObservationSignal) -> dict[str, Any]:
    if signal.status != "open":
        return {
            "mode": "none",
            "reason": "Closed observation signals do not need follow-up work.",
            "followup_specs": [],
        }
    severity = str(signal.severity or "medium").strip().lower()
    priority = 7 if severity in {"high", "critical"} else 5
    urgency = 4 if severity in {"high", "critical"} else 2
    return {
        "mode": "targeted",
        "reason": "Open observation signals should cash out into bounded alignment maintenance.",
        "followup_specs": [
            {
                "title": f"Resolve {signal.signal_kind}: {signal.subject_id}",
                "description": signal.summary,
                "priority": priority,
                "urgency": urgency,
                "risk": "moderate" if severity in {"high", "critical"} else "low",
                "completion_type": "review_or_audit",
                "success_criteria": {"observation_signal_resolved": True},
                "task_id_hint": f"signal-followup-{signal.subject_kind}-{signal.subject_id}",
                "route_preferences": {"preferred_cli_tools": ["kilocode", "gemini-cli"]},
            }
        ],
    }


class EvalObservationStore:
    def __init__(self, *, state_path: Path) -> None:
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._payload = self._load()

    def record(self, observation: EvalObservation) -> EvalObservation:
        entries = self._payload.setdefault("observations", [])
        entries.append(observation.model_dump(mode="json"))
        if len(entries) > 1000:
            del entries[:-1000]
        self._store()
        return observation

    def list(
        self,
        *,
        subject_kind: str | None = None,
        task_class: str | None = None,
    ) -> list[dict[str, Any]]:
        items = [item for item in self._payload.get("observations", []) if isinstance(item, dict)]
        if subject_kind is not None:
            items = [item for item in items if str(item.get("subject_kind") or "") == subject_kind]
        if task_class is not None:
            items = [item for item in items if str(item.get("task_class") or "general") == task_class]
        return items

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"observations": []}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"observations": []}
        if not isinstance(payload, dict):
            return {"observations": []}
        payload.setdefault("observations", [])
        return payload

    def _store(self) -> None:
        self.state_path.write_text(json.dumps(self._payload, indent=2), encoding="utf-8")
