"""Composite strategy that tries a fast lane before escalating to a persistent lane."""

from __future__ import annotations

from astrata.inference.strategies.base import InferenceStrategy, StrategyContext, StrategyResult


class FastThenPersistentStrategy(InferenceStrategy):
    @property
    def strategy_id(self) -> str:
        return "fast_then_persistent"

    def execute(self, context: StrategyContext) -> StrategyResult:
        fast_executor = context.metadata.get("fast_executor")
        persistent_executor = context.metadata.get("persistent_executor")
        if fast_executor is None or not callable(fast_executor):
            raise RuntimeError("FastThenPersistentStrategy requires a fast_executor callback.")
        if persistent_executor is None or not callable(persistent_executor):
            raise RuntimeError("FastThenPersistentStrategy requires a persistent_executor callback.")

        fast_request = context.metadata.get("fast_request", context.request)
        persistent_request = context.metadata.get("persistent_request", context.request)
        fast_runtime_key = str(context.metadata.get("fast_runtime_key") or "fast")
        persistent_runtime_key = str(context.metadata.get("persistent_runtime_key") or "persistent")

        fast_content = str(fast_executor(fast_request, fast_runtime_key))
        if fast_content.strip() not in {"", "ESCALATE_THINKING"}:
            return StrategyResult(
                content=fast_content,
                strategy_id=self.strategy_id,
                runtime_key=fast_runtime_key,
                metadata={
                    "endpoint_type": context.endpoint_type,
                    "memory_policy": context.memory_policy,
                    "continuity": context.continuity,
                    "escalated": False,
                },
            )

        persistent_content = str(persistent_executor(persistent_request, persistent_runtime_key))
        return StrategyResult(
            content=persistent_content,
            strategy_id=self.strategy_id,
            runtime_key=persistent_runtime_key,
            metadata={
                "endpoint_type": context.endpoint_type,
                "memory_policy": context.memory_policy,
                "continuity": context.continuity,
                "escalated": True,
            },
        )
