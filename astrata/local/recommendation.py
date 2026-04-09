"""Local runtime recommendation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from astrata.local.catalog import StarterCatalog
from astrata.local.models.registry import LocalModelRecord


ThermalPreference = Literal["quiet", "balanced", "performance"]


@dataclass(frozen=True)
class HardwareProfile:
    platform: str
    arch: str
    cpu_count: int
    total_memory_bytes: int
    apple_metal_likely: bool


@dataclass(frozen=True)
class ThermalState:
    preference: ThermalPreference = "quiet"
    telemetry_available: bool = False
    thermal_pressure: str = "unknown"
    fans_allowed: bool = False
    detail: str | None = None


@dataclass(frozen=True)
class RuntimeRecommendation:
    model: LocalModelRecord | None
    profile_id: str
    reason: str


def recommend_runtime_selection(
    *,
    hardware: HardwareProfile,
    models: list[LocalModelRecord],
    thermal: ThermalState | None = None,
) -> RuntimeRecommendation:
    thermal = thermal or ThermalState()
    if not models:
        return RuntimeRecommendation(
            model=None,
            profile_id="quiet" if thermal.preference == "quiet" else "balanced",
            reason="No local models were discovered yet.",
        )

    ranked = sorted(models, key=lambda model: _score_model(model, hardware, thermal), reverse=True)
    model = ranked[0]
    total_gb = hardware.total_memory_bytes / (1024**3)
    model_bytes = _safe_model_size_bytes(model)

    profile_id = "balanced"
    if thermal.preference == "quiet":
        profile_id = "quiet"
    elif total_gb < 16 or model_bytes > hardware.total_memory_bytes * 0.45:
        profile_id = "turbo"
    elif total_gb >= 32 and model_bytes < hardware.total_memory_bytes * 0.2:
        profile_id = "quality"

    model_gb = max(1, round(model_bytes / (1024**3)))
    thermal_note = ""
    if thermal.preference == "quiet":
        thermal_note = " Quiet preference is forcing a low-noise runtime profile."
    elif thermal.preference == "performance":
        thermal_note = " Performance preference allows hotter runtime behavior."
    reason = (
        f"Recommended {model.display_name} with the {profile_id} profile "
        f"for a {round(total_gb)} GB machine and an approximately {model_gb} GB model footprint."
        f"{thermal_note}"
    )
    return RuntimeRecommendation(model=model, profile_id=profile_id, reason=reason)


def _score_model(model: LocalModelRecord, hardware: HardwareProfile, thermal: ThermalState) -> float:
    if getattr(model, "role", "model") != "model":
        return -1000.0

    catalog = StarterCatalog()
    total_gb = hardware.total_memory_bytes / (1024**3)
    model_gb = _safe_model_size_bytes(model) / (1024**3)
    fit = total_gb - model_gb
    score = max(-40.0, min(30.0, fit * 2.0))
    score += catalog.family_prior(model.family)
    if "lmstudio" in model.path.lower() or "lm studio" in model.path.lower():
        score += 6.0
    if "instruct" in model.tags:
        score += 8.0
    if "coding" in model.tags:
        score += 4.0
    if model.benchmark_score is not None:
        score += max(-20.0, min(25.0, float(model.benchmark_score)))
    if model.observed_average_score is not None and model.observed_sample_count > 0:
        utility_weight = min(30.0, float(model.observed_sample_count) * 3.0)
        score += float(model.observed_average_score) * utility_weight
    if model.observed_success_rate is not None and model.observed_sample_count > 0:
        evidence_weight = min(20.0, float(model.observed_sample_count))
        score += (float(model.observed_success_rate) - 0.5) * evidence_weight
    if model_gb > total_gb * 0.75:
        score -= 50.0
    if thermal.preference == "quiet":
        score -= model_gb * 3.0
        if hardware.apple_metal_likely:
            score -= 4.0
    elif thermal.preference == "performance":
        score += model_gb * 0.5
    return score


def _safe_model_size_bytes(model: LocalModelRecord) -> int:
    try:
        return max(1, int(model.size_bytes))
    except Exception:
        return 1
