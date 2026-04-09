from astrata.inference.strategies import FastThenPersistentStrategy, SinglePassStrategy, StrategyContext
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


def test_fast_then_persistent_strategy_returns_fast_lane_when_adequate():
    strategy = FastThenPersistentStrategy()
    seen = []

    def fast_executor(request, runtime_key):
        seen.append((runtime_key, len(request.messages), request.metadata["execution_mode"]))
        return "quick answer"

    def persistent_executor(request, runtime_key):
        raise AssertionError("persistent lane should not run")

    result = strategy.execute(
        StrategyContext(
            request=CompletionRequest(messages=[Message(role="user", content="hello")]),
            endpoint_type="agent_session",
            strategy_id="fast_then_persistent",
            memory_policy="managed_session_state",
            continuity="managed",
            runtime_key="fast",
            metadata={
                "fast_request": CompletionRequest(
                    messages=[Message(role="user", content="hello")],
                    metadata={"execution_mode": "fast"},
                ),
                "persistent_request": CompletionRequest(
                    messages=[Message(role="user", content="hello")],
                    metadata={"execution_mode": "persistent"},
                ),
                "fast_executor": fast_executor,
                "persistent_executor": persistent_executor,
            },
        )
    )

    assert result.content == "quick answer"
    assert result.strategy_id == "fast_then_persistent"
    assert result.runtime_key == "fast"
    assert result.metadata["escalated"] is False
    assert seen == [("fast", 1, "fast")]


def test_fast_then_persistent_strategy_escalates_when_fast_lane_requests_more_thinking():
    strategy = FastThenPersistentStrategy()
    seen = []

    def fast_executor(request, runtime_key):
        seen.append((runtime_key, request.metadata["execution_mode"]))
        return "ESCALATE_THINKING"

    def persistent_executor(request, runtime_key):
        seen.append((runtime_key, request.metadata["execution_mode"]))
        return "deep answer"

    result = strategy.execute(
        StrategyContext(
            request=CompletionRequest(messages=[Message(role="user", content="hello")]),
            endpoint_type="agent_session",
            strategy_id="fast_then_persistent",
            memory_policy="managed_session_state",
            continuity="managed",
            runtime_key="fast",
            metadata={
                "fast_request": CompletionRequest(
                    messages=[Message(role="user", content="hello")],
                    metadata={"execution_mode": "fast"},
                ),
                "persistent_request": CompletionRequest(
                    messages=[Message(role="user", content="hello"), Message(role="assistant", content="draft")],
                    metadata={"execution_mode": "persistent"},
                ),
                "fast_executor": fast_executor,
                "persistent_executor": persistent_executor,
            },
        )
    )

    assert result.content == "deep answer"
    assert result.strategy_id == "fast_then_persistent"
    assert result.runtime_key == "persistent"
    assert result.metadata["escalated"] is True
    assert seen == [("fast", "fast"), ("persistent", "persistent")]
