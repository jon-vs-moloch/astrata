"""Minimal local model registry inspired by Lightning's runtime direction."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalModelRecord(BaseModel):
    model_id: str = Field(default_factory=lambda: str(uuid4()))
    display_name: str
    path: str
    format: str = "gguf"
    family: str | None = None
    role: str = "model"
    quantization: str | None = None
    size_bytes: int = 0
    source: str = "custom"
    tags: list[str] = Field(default_factory=list)
    benchmark_score: float | None = None
    benchmark_source: str | None = None
    observed_success_rate: float | None = None
    observed_average_score: float | None = None
    observed_sample_count: int = 0
    notes: str = ""
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class LocalModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, LocalModelRecord] = {}

    def adopt(self, path: str, *, display_name: str | None = None) -> LocalModelRecord:
        expanded = Path(path).expanduser()
        normalized = str(expanded)
        existing = self.find_by_path(normalized)
        if existing is not None:
            return existing
        model = LocalModelRecord(
            model_id=_stable_model_id(normalized),
            display_name=display_name or expanded.stem,
            path=normalized,
            size_bytes=expanded.stat().st_size if expanded.exists() else 0,
            source=_infer_source(normalized),
            family=_infer_family(display_name or expanded.stem),
            role=_infer_role(display_name or expanded.stem, normalized),
            tags=_infer_tags(display_name or expanded.stem, normalized),
        )
        self._models[model.model_id] = model
        return model

    def list_models(self) -> list[LocalModelRecord]:
        return sorted(self._models.values(), key=lambda record: (record.display_name.lower(), record.model_id))

    def get(self, model_id: str) -> LocalModelRecord | None:
        return self._models.get(model_id)

    def find_by_path(self, path: str) -> LocalModelRecord | None:
        normalized = str(Path(path).expanduser())
        for record in self._models.values():
            if record.path == normalized:
                return record
        return None

    def replace(self, record: LocalModelRecord) -> LocalModelRecord:
        self._models[record.model_id] = record
        return record


def _infer_family(label: str) -> str:
    lowered = label.lower()
    if "gemma" in lowered:
        return "gemma"
    if "qwen" in lowered:
        return "qwen"
    if "llama" in lowered:
        return "llama"
    if "mistral" in lowered:
        return "mistral"
    if "phi" in lowered:
        return "phi"
    return "unknown"


def _infer_source(path: str) -> str:
    lowered = path.lower()
    if "lm studio" in lowered or "lm-studio" in lowered or ".lmstudio" in lowered:
        return "lm-studio"
    if ".ollama" in lowered:
        return "ollama"
    if "astrata" in lowered:
        return "astrata"
    return "custom"


def _infer_role(label: str, path: str) -> str:
    lowered = f"{label} {path}".lower()
    if "mmproj" in lowered or "projector" in lowered:
        return "projector"
    if "embedding" in lowered or "embed" in lowered:
        return "embedding"
    return "model"


def _infer_tags(label: str, path: str) -> list[str]:
    lowered = f"{label} {path}".lower()
    tags: list[str] = []
    if "instruct" in lowered or "-it" in lowered or ".it-" in lowered:
        tags.append("instruct")
    if "coder" in lowered or "code" in lowered:
        tags.append("coding")
    if "mmproj" in lowered or "projector" in lowered:
        tags.append("support-artifact")
    return tags


def _stable_model_id(path: str) -> str:
    normalized = str(Path(path).expanduser()).strip()
    if not normalized:
        return str(uuid4())
    return str(uuid5(NAMESPACE_URL, normalized))
