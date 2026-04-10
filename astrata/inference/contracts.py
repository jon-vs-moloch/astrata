"""Core inference-layer contracts shared across runtimes and strategies."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


EndpointType = Literal["chat_completions", "agent_session", "task_run", "tool_augmented"]
MemoryPolicy = Literal[
    "literal_transcript",
    "managed_session_state",
    "artifact_backed",
    "branch_checkpointed",
]
InferenceStrategyId = Literal[
    "single_pass",
    "cyclone",
]
ContinuityMode = Literal["stateless", "threaded", "managed"]


class BackendCapabilitySet(BaseModel):
    backend_id: str
    multi_model_residency: bool = False
    native_prefix_cache: bool = False
    native_checkpoint_restore: bool = False
    native_branch_fork: bool = False
    edit_tail_invalidation: bool = False
    streaming: bool = False
    ephemeral_sessions: bool = False
    managed_processes: bool = False
    notes: list[str] = Field(default_factory=list)


class EndpointProfile(BaseModel):
    endpoint_type: EndpointType
    memory_policy: MemoryPolicy
    default_strategy: InferenceStrategyId
    continuity: ContinuityMode
    description: str
    quality_priority: float = 0.5
    latency_priority: float = 0.5
    hides_strategy_details: bool = True


class InferenceExecutionPlan(BaseModel):
    endpoint: EndpointProfile
    strategy: InferenceStrategyId
    memory_policy: MemoryPolicy
    backend_requirements: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
