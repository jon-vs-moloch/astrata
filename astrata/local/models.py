"""Durable local-runtime model records and recommendation types."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ModelSource = Literal["astrata", "lm-studio", "ollama", "custom", "unknown"]
ModelStatus = Literal["discovered", "adopted"]
ModelAcquisition = Literal["discovered", "adopted", "installed", "catalog-installed"]


class ModelBenchmarks(BaseModel):
    note: str | None = None


class LocalModelMetadata(BaseModel):
    notes: str | None = None
    benchmark: ModelBenchmarks | None = None
    tags: list[str] = Field(default_factory=list)


class LocalModelProvenance(BaseModel):
    acquisition: ModelAcquisition
    managed_path: bool = False
    install_source_url: str | None = None
    catalog_id: str | None = None
    catalog_family: str | None = None


class LocalModelRecord(BaseModel):
    model_id: str
    path: str
    format: str = "gguf"
    size_bytes: int
    label: str
    family: str
    source: ModelSource
    status: ModelStatus
    discovered_at: str = Field(default_factory=_now_iso)
    provenance: LocalModelProvenance
    metadata: LocalModelMetadata | None = None


class HardwareProfile(BaseModel):
    total_memory_bytes: int
    available_memory_bytes: int | None = None
    cpu_label: str | None = None
    gpu_label: str | None = None


class RuntimeRecommendation(BaseModel):
    model: LocalModelRecord | None = None
    profile_id: str = "balanced"
    reason: str
