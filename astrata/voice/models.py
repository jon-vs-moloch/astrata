"""Voice IO capability models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VoiceBackendRecord(BaseModel):
    backend_id: str
    modality: Literal["input", "output", "duplex"]
    locality: Literal["local", "cloud"]
    available: bool = False
    implemented: bool = True
    display_name: str
    summary: str = ""
    reason: str = ""
    install_hint: str | None = None


class VoiceStatus(BaseModel):
    output_backends: list[VoiceBackendRecord] = Field(default_factory=list)
    input_backends: list[VoiceBackendRecord] = Field(default_factory=list)
    recommended_output_backend: str | None = None
    recommended_input_backend: str | None = None
    recommended_output_models: list[dict[str, str]] = Field(default_factory=list)
    recommended_input_models: list[dict[str, str]] = Field(default_factory=list)
    preload_defaults: list[dict[str, str]] = Field(default_factory=list)
    roadmap_notes: list[str] = Field(default_factory=list)
