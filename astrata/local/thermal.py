"""Thermal control helpers for local runtime decisions."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Literal

from astrata.local.recommendation import ThermalState


ThermalSeverity = Literal["nominal", "fair", "severe", "critical", "unknown"]
ThermalAction = Literal["allow", "cooldown", "deny"]


@dataclass(frozen=True)
class ThermalDecision:
    sample: ThermalSeverity
    latched: ThermalSeverity
    action: ThermalAction
    should_start_new_local_work: bool
    should_throttle_background: bool
    reason: str


class ThermalController:
    def __init__(self, *, state_path: Path) -> None:
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def evaluate(self, thermal: ThermalState) -> ThermalDecision:
        sample = _normalize_pressure(thermal.thermal_pressure)
        previous = self._load_state()
        previous_latched = _normalize_pressure(str(previous.get("latched") or "unknown"))

        if sample in {"severe", "critical"}:
            latched = sample
            action: ThermalAction = "deny"
            reason = "Thermal pressure is above the quiet-safe boundary."
        elif sample == "fair":
            latched = "fair"
            action = "cooldown"
            reason = "Thermal pressure is near the nominal/fair boundary."
        elif sample == "nominal":
            if previous_latched in {"fair", "severe", "critical"}:
                latched = "fair"
                action = "cooldown"
                reason = "Nominal sample observed, but hysteresis is holding a cooldown latch."
            else:
                latched = "nominal"
                action = "allow"
                reason = "Thermal pressure is nominal."
        else:
            latched = previous_latched if previous_latched != "unknown" else "unknown"
            action = "cooldown" if latched in {"fair", "severe", "critical"} else "allow"
            reason = "Thermal telemetry is sparse; keeping the previous latch if present."

        decision = ThermalDecision(
            sample=sample,
            latched=latched,
            action=action,
            should_start_new_local_work=action == "allow",
            should_throttle_background=action != "allow",
            reason=reason,
        )
        self._store_state(decision)
        return decision

    def clear_latch(self) -> None:
        self.state_path.unlink(missing_ok=True)

    def _load_state(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _store_state(self, decision: ThermalDecision) -> None:
        payload = {
            "sample": decision.sample,
            "latched": decision.latched,
            "action": decision.action,
            "should_start_new_local_work": decision.should_start_new_local_work,
            "should_throttle_background": decision.should_throttle_background,
            "reason": decision.reason,
        }
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _normalize_pressure(value: str) -> ThermalSeverity:
    lowered = (value or "").strip().lower()
    if lowered in {"nominal", "fair", "severe", "critical"}:
        return lowered
    return "unknown"
