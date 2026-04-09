from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.comms.lanes import HandoffLane
from astrata.controllers.base import ControllerEnvelope
from astrata.controllers.local_executor import LocalExecutorController
from astrata.procedures.health import RouteHealthStore
from astrata.providers.base import CompletionRequest, CompletionResponse, Provider
from astrata.providers.registry import ProviderRegistry


class _ConfiguredCodexProvider(Provider):
    @property
    def name(self) -> str:
        return "codex"

    def is_configured(self) -> bool:
        return True

    def default_model(self) -> str | None:
        return "gpt-5.4"

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(provider="codex", model="gpt-5.4", content="OK")


def test_local_executor_blocks_broken_route():
    with TemporaryDirectory() as tmp:
        health = RouteHealthStore(Path(tmp) / "route_health.json")
        route = {"provider": "codex", "model": "gpt-5.4", "cli_tool": None}
        for _ in range(3):
            health.record_failure(route, failure_kind="timeout", error="timed out")
        executor = LocalExecutorController(
            registry=ProviderRegistry({"codex": _ConfiguredCodexProvider()}),
            health_store=health,
        )
        lane = HandoffLane()
        handoff = lane.open_handoff(
            source_controller="prime",
            target_controller="local_executor",
            task_id="task-1",
            envelope=ControllerEnvelope(
                controller_id="prime",
                task_id="task-1",
                priority=5,
                urgency=3,
                risk="moderate",
            ).model_dump(mode="json"),
            route=route,
            source_decision={"status": "accepted", "reason": "ready"},
        )
        decision = executor.evaluate_handoff(handoff)
        assert decision.status == "blocked"
        assert "broken" in decision.reason.lower()
