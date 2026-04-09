"""Single-pass inference strategy."""

from __future__ import annotations

from astrata.inference.strategies.base import InferenceStrategy, StrategyContext, StrategyResult


class SinglePassStrategy(InferenceStrategy):
    @property
    def strategy_id(self) -> str:
        return "single_pass"

    def execute(self, context: StrategyContext) -> StrategyResult:
        executor = context.metadata.get("executor")
        if executor is None or not callable(executor):
            raise RuntimeError("SinglePassStrategy requires an executor callback in context.metadata.")
        content = str(executor(context.request, context.runtime_key))
        return StrategyResult(
            content=content,
            strategy_id=self.strategy_id,
            runtime_key=context.runtime_key,
            metadata={
                "endpoint_type": context.endpoint_type,
                "memory_policy": context.memory_policy,
                "continuity": context.continuity,
            },
        )
