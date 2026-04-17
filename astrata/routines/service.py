"""Routine registry and lightweight runner."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrata.routines.models import RoutineRecord


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RoutineService:
    def __init__(self, *, state_path: Path) -> None:
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls, settings) -> "RoutineService":
        return cls(state_path=settings.paths.data_dir / "routines.json")

    def ensure_default_routines(self) -> list[RoutineRecord]:
        defaults = [
            RoutineRecord(
                routine_id="refresh-kilocode-model-registry",
                title="Refresh KiloCode Model Registry",
                procedure_id="refresh-inference-registries",
                cadence="weekly",
                command=["astrata", "kilocode-models-sync"],
                next_run_hint="weekly when Astrata is active",
                metadata={"registry": "kilocode", "quota_pressure_reduction": True},
            ),
            RoutineRecord(
                routine_id="sync-google-ai-studio-model-registry",
                title="Sync Google AI Studio Model Registry",
                procedure_id="refresh-inference-registries",
                cadence="weekly",
                command=["astrata", "google-models-sync"],
                next_run_hint="weekly when Google AI Studio is configured",
                metadata={"registry": "google_ai_studio", "quota_pressure_reduction": True},
            ),
        ]
        payload = self._load()
        routines = dict(payload.get("routines") or {})
        changed = False
        for routine in defaults:
            if routine.routine_id not in routines:
                routines[routine.routine_id] = routine.model_dump(mode="json")
                changed = True
        if changed:
            payload["routines"] = routines
            self._save(payload)
        return self.list_routines()

    def list_routines(self) -> list[RoutineRecord]:
        return sorted(
            [
                RoutineRecord(**raw)
                for raw in dict(self._load().get("routines") or {}).values()
                if isinstance(raw, dict)
            ],
            key=lambda item: item.routine_id,
        )

    def run(self, routine_id: str) -> dict[str, Any]:
        payload = self._load()
        routines = dict(payload.get("routines") or {})
        raw = routines.get(routine_id)
        if not isinstance(raw, dict):
            return {"status": "not_found", "routine_id": routine_id}
        routine = RoutineRecord(**raw)
        if routine.status != "active":
            return {"status": "skipped", "reason": routine.status, "routine": routine.model_dump(mode="json")}
        if not routine.command:
            return {"status": "skipped", "reason": "no_command", "routine": routine.model_dump(mode="json")}
        proc = subprocess.run(
            routine.command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        updated = routine.model_copy(update={"last_run_at": _now_iso(), "updated_at": _now_iso()})
        routines[routine_id] = updated.model_dump(mode="json")
        payload["routines"] = routines
        self._save(payload)
        return {
            "status": "succeeded" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode,
            "routine": updated.model_dump(mode="json"),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"routines": {}}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"routines": {}}
        if not isinstance(payload, dict):
            return {"routines": {}}
        payload.setdefault("routines", {})
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

