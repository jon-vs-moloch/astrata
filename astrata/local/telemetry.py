"""Durable local-model benchmark and observation telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LocalModelObservation:
    model_path: str
    task_class: str
    score: float
    success: bool
    startup_seconds: float | None = None
    generation_seconds: float | None = None
    total_wall_seconds: float | None = None
    output_units: int | None = None
    throughput_units_per_second: float | None = None
    thermal_pressure: str | None = None
    source: str = "observed"
    note: str | None = None
    recorded_at: str = _now_iso()


@dataclass(frozen=True)
class LocalModelTelemetrySummary:
    model_path: str
    observed_success_rate: float | None = None
    observed_average_score: float | None = None
    observed_sample_count: int = 0
    observed_average_startup_seconds: float | None = None
    observed_average_generation_seconds: float | None = None
    observed_average_total_wall_seconds: float | None = None
    observed_average_throughput_units_per_second: float | None = None
    benchmark_score: float | None = None
    benchmark_source: str | None = None


class LocalModelTelemetryStore:
    def __init__(self, *, state_path: Path) -> None:
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._payload = self._load()

    def record_observation(
        self,
        *,
        model_path: str,
        task_class: str,
        score: float,
        success: bool,
        startup_seconds: float | None = None,
        generation_seconds: float | None = None,
        total_wall_seconds: float | None = None,
        output_units: int | None = None,
        throughput_units_per_second: float | None = None,
        thermal_pressure: str | None = None,
        source: str = "observed",
        note: str | None = None,
    ) -> LocalModelObservation:
        observation = LocalModelObservation(
            model_path=str(Path(model_path).expanduser()),
            task_class=task_class,
            score=float(score),
            success=bool(success),
            startup_seconds=None if startup_seconds is None else float(startup_seconds),
            generation_seconds=None if generation_seconds is None else float(generation_seconds),
            total_wall_seconds=None if total_wall_seconds is None else float(total_wall_seconds),
            output_units=None if output_units is None else int(output_units),
            throughput_units_per_second=None if throughput_units_per_second is None else float(throughput_units_per_second),
            thermal_pressure=str(thermal_pressure) if thermal_pressure else None,
            source=source,
            note=note,
            recorded_at=_now_iso(),
        )
        observations = self._payload.setdefault("observations", [])
        observations.append(observation.__dict__)
        self._store()
        return observation

    def set_benchmark(
        self,
        *,
        model_path: str,
        score: float,
        source: str,
    ) -> None:
        benchmarks = self._payload.setdefault("benchmarks", {})
        benchmarks[str(Path(model_path).expanduser())] = {
            "score": float(score),
            "source": source,
            "updated_at": _now_iso(),
        }
        self._store()

    def summarize(self, model_path: str) -> LocalModelTelemetrySummary:
        normalized = str(Path(model_path).expanduser())
        raw_observations = [
            item for item in self._payload.get("observations", [])
            if isinstance(item, dict) and item.get("model_path") == normalized
        ]
        observations = [item for item in raw_observations if isinstance(item.get("score"), (int, float))]
        sample_count = len(observations)
        success_rate = None
        average_score = None
        average_startup_seconds = None
        average_generation_seconds = None
        average_total_wall_seconds = None
        average_throughput = None
        if observations:
            success_rate = sum(1 for item in observations if item.get("success")) / sample_count
            average_score = mean(float(item["score"]) for item in observations)
            startup_values = [float(item["startup_seconds"]) for item in observations if isinstance(item.get("startup_seconds"), (int, float))]
            generation_values = [float(item["generation_seconds"]) for item in observations if isinstance(item.get("generation_seconds"), (int, float))]
            total_values = [float(item["total_wall_seconds"]) for item in observations if isinstance(item.get("total_wall_seconds"), (int, float))]
            throughput_values = [float(item["throughput_units_per_second"]) for item in observations if isinstance(item.get("throughput_units_per_second"), (int, float))]
            if startup_values:
                average_startup_seconds = mean(startup_values)
            if generation_values:
                average_generation_seconds = mean(generation_values)
            if total_values:
                average_total_wall_seconds = mean(total_values)
            if throughput_values:
                average_throughput = mean(throughput_values)
        benchmark = self._payload.get("benchmarks", {}).get(normalized, {})
        benchmark_score = benchmark.get("score") if isinstance(benchmark, dict) else None
        benchmark_source = benchmark.get("source") if isinstance(benchmark, dict) else None
        return LocalModelTelemetrySummary(
            model_path=normalized,
            observed_success_rate=success_rate,
            observed_average_score=average_score,
            observed_sample_count=sample_count,
            observed_average_startup_seconds=average_startup_seconds,
            observed_average_generation_seconds=average_generation_seconds,
            observed_average_total_wall_seconds=average_total_wall_seconds,
            observed_average_throughput_units_per_second=average_throughput,
            benchmark_score=float(benchmark_score) if isinstance(benchmark_score, (int, float)) else None,
            benchmark_source=str(benchmark_source) if benchmark_source else None,
        )

    def all_observations(self) -> list[dict[str, object]]:
        return list(self._payload.get("observations", []))

    def _load(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {"observations": [], "benchmarks": {}}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"observations": [], "benchmarks": {}}
        if not isinstance(payload, dict):
            return {"observations": [], "benchmarks": {}}
        payload.setdefault("observations", [])
        payload.setdefault("benchmarks", {})
        return payload

    def _store(self) -> None:
        self.state_path.write_text(json.dumps(self._payload, indent=2), encoding="utf-8")
