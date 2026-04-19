"""Provider/model catalog records for routing and user-facing model selection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


ModelCapability = Literal["chat", "text", "image", "video", "audio", "embedding", "tool_use", "vision"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelCatalogRecord(BaseModel):
    catalog_id: str
    provider_id: str
    model_id: str
    display_name: str
    capabilities: list[ModelCapability] = Field(default_factory=lambda: ["chat"])
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    output_modalities: list[str] = Field(default_factory=lambda: ["text"])
    context_length: int | None = None
    max_output_tokens: int | None = None
    pricing: dict[str, Any] = Field(default_factory=dict)
    quota: dict[str, Any] = Field(default_factory=dict)
    supported_parameters: list[str] = Field(default_factory=list)
    performance: dict[str, Any] = Field(default_factory=dict)
    task_fit: dict[str, Any] = Field(default_factory=dict)
    status: Literal["available", "configured", "unconfigured", "experimental", "research_required"] = "available"
    source: str = "static"
    notes: str = ""
    updated_at: str = Field(default_factory=_now_iso)


def catalog_id(provider_id: str, model_id: str) -> str:
    return f"{provider_id}:{model_id}"


def catalog_record(
    *,
    provider_id: str,
    model_id: str,
    display_name: str | None = None,
    capabilities: list[ModelCapability] | None = None,
    input_modalities: list[str] | None = None,
    output_modalities: list[str] | None = None,
    context_length: int | None = None,
    max_output_tokens: int | None = None,
    pricing: dict[str, Any] | None = None,
    quota: dict[str, Any] | None = None,
    supported_parameters: list[str] | None = None,
    performance: dict[str, Any] | None = None,
    task_fit: dict[str, Any] | None = None,
    status: str = "available",
    source: str = "static",
    notes: str = "",
) -> ModelCatalogRecord:
    return ModelCatalogRecord(
        catalog_id=catalog_id(provider_id, model_id),
        provider_id=provider_id,
        model_id=model_id,
        display_name=display_name or model_id,
        capabilities=list(capabilities or ["chat"]),
        input_modalities=list(input_modalities or ["text"]),
        output_modalities=list(output_modalities or ["text"]),
        context_length=context_length,
        max_output_tokens=max_output_tokens,
        pricing=dict(pricing or {}),
        quota=dict(quota or {}),
        supported_parameters=list(supported_parameters or []),
        performance=dict(performance or {}),
        task_fit=dict(task_fit or {}),
        status=status,  # type: ignore[arg-type]
        source=source,
        notes=notes,
    )
