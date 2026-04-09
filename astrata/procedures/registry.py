"""Minimal procedure registry for Loop 0 reuse."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProcedureTemplate(BaseModel):
    procedure_id: str
    title: str
    description: str = ""
    expected_outputs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcedureRegistry:
    def __init__(self) -> None:
        self._templates: dict[str, ProcedureTemplate] = {}

    def register(self, template: ProcedureTemplate) -> None:
        self._templates[template.procedure_id] = template

    def get(self, procedure_id: str) -> ProcedureTemplate | None:
        return self._templates.get(procedure_id)

    def list_ids(self) -> list[str]:
        return sorted(self._templates)
