"""Small inference planner for endpoint/memory/strategy defaults."""

from __future__ import annotations

from astrata.inference.contracts import (
    BackendCapabilitySet,
    EndpointProfile,
    InferenceExecutionPlan,
)


class InferencePlanner:
    """Describe endpoint semantics and default strategy choices."""

    def endpoint_profile(self, endpoint_type: str) -> EndpointProfile:
        normalized = str(endpoint_type or "").strip().lower()
        if normalized == "agent_session":
            return EndpointProfile(
                endpoint_type="agent_session",
                memory_policy="managed_session_state",
                default_strategy="fast_then_persistent",
                continuity="managed",
                description="Long-running agentic endpoint with managed continuity rather than literal transcript replay.",
                quality_priority=0.75,
                latency_priority=0.6,
                hides_strategy_details=True,
            )
        if normalized == "task_run":
            return EndpointProfile(
                endpoint_type="task_run",
                memory_policy="artifact_backed",
                default_strategy="single_pass",
                continuity="managed",
                description="Bounded task-oriented endpoint that can persist artifacts and checkpoints across substeps.",
                quality_priority=0.8,
                latency_priority=0.45,
                hides_strategy_details=True,
            )
        if normalized == "tool_augmented":
            return EndpointProfile(
                endpoint_type="tool_augmented",
                memory_policy="branch_checkpointed",
                default_strategy="single_pass",
                continuity="threaded",
                description="Interactive tool-calling endpoint with checkpoint-friendly branch state.",
                quality_priority=0.7,
                latency_priority=0.55,
                hides_strategy_details=True,
            )
        return EndpointProfile(
            endpoint_type="chat_completions",
            memory_policy="literal_transcript",
            default_strategy="single_pass",
            continuity="threaded",
            description="Compatibility endpoint that behaves like conventional chat completions.",
            quality_priority=0.6,
            latency_priority=0.7,
            hides_strategy_details=True,
        )

    def plan_for_endpoint(
        self,
        *,
        endpoint_type: str,
        backend: BackendCapabilitySet | None = None,
    ) -> InferenceExecutionPlan:
        endpoint = self.endpoint_profile(endpoint_type)
        strategy = endpoint.default_strategy
        requirements: list[str] = []
        notes: list[str] = []
        if strategy == "cyclone":
            requirements.extend(["multi_model_residency", "edit_tail_invalidation"])
        if strategy == "fast_then_persistent":
            requirements.append("ephemeral_sessions")
        if backend is not None:
            if strategy == "cyclone" and not backend.multi_model_residency:
                notes.append("Backend lacks native multi-model residency; Cyclone should remain disabled or emulated.")
            if endpoint.memory_policy == "branch_checkpointed" and not backend.native_checkpoint_restore:
                notes.append("Checkpointed continuity will be emulated above the backend.")
        return InferenceExecutionPlan(
            endpoint=endpoint,
            strategy=strategy,
            memory_policy=endpoint.memory_policy,
            backend_requirements=requirements,
            notes=notes,
        )
