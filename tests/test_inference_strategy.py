from astrata.inference.strategies import SinglePassStrategy, StrategyContext
from astrata.providers.base import CompletionRequest, Message


def test_single_pass_strategy_uses_executor_callback():
    strategy = SinglePassStrategy()
    seen = {}

    def executor(request, runtime_key):
        seen["runtime_key"] = runtime_key
        seen["message_count"] = len(request.messages)
        return "hello from strategy"

    result = strategy.execute(
        StrategyContext(
            request=CompletionRequest(messages=[Message(role="user", content="hello")]),
            endpoint_type="agent_session",
            strategy_id="single_pass",
            memory_policy="managed_session_state",
            continuity="managed",
            runtime_key="draft",
            metadata={"executor": executor},
        )
    )

    assert result.content == "hello from strategy"
    assert result.strategy_id == "single_pass"
    assert seen["runtime_key"] == "draft"
    assert seen["message_count"] == 1
