"""Durable procedure records for reusable execution graphs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ProcedureTaskNode(BaseModel):
    node_id: str
    task_title: str
    description: str = ""
    kind: Literal["leaf", "coordination", "decomposition", "validation"] = "leaf"
    next_nodes: list[str] = Field(default_factory=list)
    branch_condition: str | None = None
    retry_limit: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcedureStructure(BaseModel):
    entry_node_id: str
    nodes: list[ProcedureTaskNode] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def node_map(self) -> dict[str, ProcedureTaskNode]:
        return {node.node_id: node for node in self.nodes}


class ProcedureRecord(BaseModel):
    procedure_id: str
    title: str
    description: str = ""
    status: Literal["good", "degraded", "broken"] = "good"
    lifecycle_state: Literal["draft", "tested", "vetted", "retired"] = "draft"
    install_state: Literal["proposed", "shadow", "active", "disabled", "superseded"] = "proposed"
    provenance: dict[str, Any] = Field(default_factory=dict)
    applicability: dict[str, Any] = Field(default_factory=dict)
    permissions_profile: dict[str, Any] = Field(default_factory=dict)
    entry_conditions: dict[str, Any] = Field(default_factory=dict)
    success_contract: dict[str, Any] = Field(default_factory=dict)
    failure_contract: dict[str, Any] = Field(default_factory=dict)
    artifact_contract: dict[str, Any] = Field(default_factory=dict)
    structure: ProcedureStructure
    notes: list[str] = Field(default_factory=list)
