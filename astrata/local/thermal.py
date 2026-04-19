"""Thermal control helpers for local runtime decisions."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
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
    def __init__(
        self,
        *,
        state_path: Path,
        cooldown_ttl_seconds: int = 300,
        history_limit: int = 500,
    ) -> None:
        self.state_path = state_path
        self.history_path = state_path.with_name("thermal_history.json")
        self.cooldown_ttl_seconds = max(0, int(cooldown_ttl_seconds))
        self.history_limit = max(24, int(history_limit))
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def evaluate(
        self, thermal: ThermalState, *, bypass_hysteresis: bool = False
    ) -> ThermalDecision:
        sample = _normalize_pressure(thermal.thermal_pressure)
        previous = self._load_state()
        previous_latched = _normalize_pressure(str(previous.get("latched") or "unknown"))
        previous_updated_at = float(previous.get("updated_at") or 0.0)
        cooldown_expired = (
            previous_updated_at <= 0
            or self.cooldown_ttl_seconds <= 0
            or (time.time() - previous_updated_at) >= self.cooldown_ttl_seconds
        )

        if sample in {"severe", "critical"}:
            latched = sample
            action: ThermalAction = "deny"
            reason = "Thermal pressure is above the quiet-safe boundary."
        elif sample == "fair":
            latched = "fair"
            action = "cooldown"
            reason = "Thermal pressure is near the nominal/fair boundary."
        elif sample == "nominal":
            if not bypass_hysteresis and previous_latched in {"fair", "severe", "critical"}:
                if cooldown_expired:
                    latched = "nominal"
                    action = "allow"
                    reason = (
                        "Thermal pressure is nominal and the previous cooldown latch has expired."
                    )
                else:
                    latched = "fair"
                    action = "cooldown"
                    reason = "Nominal sample observed, but hysteresis is holding a cooldown latch."
            else:
                latched = "nominal"
                action = "allow"
                reason = "Thermal pressure is nominal."
        else:
            if previous_latched != "unknown" and not cooldown_expired:
                latched = previous_latched
            else:
                latched = "unknown"
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

    def history_summary(self, *, window: int = 100) -> dict[str, object]:
        history = self._load_history()
        recent = history[-max(1, int(window)) :]

        def counts_for(key: str) -> dict[str, int]:
            counts: dict[str, int] = {}
            for item in recent:
                value = str(item.get(key) or "unknown")
                counts[value] = counts.get(value, 0) + 1
            return counts

        sample_counts = counts_for("sample")
        action_counts = counts_for("action")
        sample_count = len(recent)
        nominal_count = sample_counts.get("nominal", 0)
        fair_or_worse = sum(
            sample_counts.get(label, 0) for label in ("fair", "severe", "critical")
        )
        return {
            "sample_count": sample_count,
            "last_sample_at": recent[-1].get("sampled_at") if recent else None,
            "samples": sample_counts,
            "actions": action_counts,
            "latched": counts_for("latched"),
            "nominal_ratio": nominal_count / sample_count if sample_count else None,
            "fair_or_worse_ratio": fair_or_worse / sample_count if sample_count else None,
            "recent": recent[-24:],
        }

    def _load_state(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _store_state(self, decision: ThermalDecision) -> None:
        now = time.time()
        payload = {
            "sample": decision.sample,
            "latched": decision.latched,
            "action": decision.action,
            "should_start_new_local_work": decision.should_start_new_local_work,
            "should_throttle_background": decision.should_throttle_background,
            "reason": decision.reason,
            "updated_at": now,
        }
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._append_history(decision, sampled_at=now)

    def _load_history(self) -> list[dict[str, object]]:
        if not self.history_path.exists():
            return []
        try:
            payload = json.loads(self.history_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return payload if isinstance(payload, list) else []

    def _append_history(self, decision: ThermalDecision, *, sampled_at: float) -> None:
        history = self._load_history()
        history.append(
            {
                "sampled_at": sampled_at,
                "sample": decision.sample,
                "latched": decision.latched,
                "action": decision.action,
                "should_start_new_local_work": decision.should_start_new_local_work,
            }
        )
        self.history_path.write_text(
            json.dumps(history[-self.history_limit :], separators=(",", ":")),
            encoding="utf-8",
        )


def _normalize_pressure(value: str) -> ThermalSeverity:
    lowered = (value or "").strip().lower()
    if lowered in {"nominal", "fair", "severe", "critical"}:
        return lowered
    return "unknown"
