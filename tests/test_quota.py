from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.controllers.base import ControllerEnvelope
from astrata.controllers.coordinator import CoordinatorController
from astrata.providers.base import CompletionRequest, CompletionResponse, Provider
from astrata.providers.registry import ProviderRegistry
from astrata.records.models import AttemptRecord
from astrata.routing.policy import RouteChooser
from astrata.scheduling.quota import QuotaPolicy
from astrata.storage.db import AstrataDatabase


class _QuotaProvider(Provider):
    @property
    def name(self) -> str:
        return "codex"

    def is_configured(self) -> bool:
        return True

    def default_model(self) -> str | None:
        return "gpt-5.4"

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(provider="codex", model="gpt-5.4", content="OK")

    def get_quota_windows(self, route=None):
        return [
            {
                "requests_remaining": 9000,
                "requests_limit": 10000,
                "reset_time": "2099-01-01T05:00:00+00:00",
                "window_duration_seconds": 5 * 3600,
                "source": "five_hour",
            },
            {
                "requests_remaining": 500,
                "requests_limit": 10000,
                "reset_time": "2099-01-08T00:00:00+00:00",
                "window_duration_seconds": 7 * 24 * 3600,
                "source": "weekly",
            },
        ]


def test_quota_policy_uses_most_throttling_window(tmp_path: Path | None = None):
    def _run(base: Path) -> None:
        db = AstrataDatabase(base / "quota.db")
        db.initialize()
        registry = ProviderRegistry({"codex": _QuotaProvider()})
        policy = QuotaPolicy(db=db, limits_per_source={"codex": 12}, registry=registry)
        decision = policy.assess({"provider": "codex", "model": "gpt-5.4"})
        assert decision.allowed is True
        assert decision.active_window is not None
        assert decision.active_window["source"] == "weekly"

    if tmp_path is not None:
        _run(tmp_path)
        return
    with TemporaryDirectory() as tmp:
        _run(Path(tmp))


def test_coordinator_defers_prime_when_pacing_window_active(tmp_path: Path | None = None):
    def _run(base: Path) -> None:
        db = AstrataDatabase(base / "quota.db")
        db.initialize()
        db.upsert_attempt(
            AttemptRecord(
                task_id="task-1",
                actor="loop0:codex",
                outcome="succeeded",
                verification_status="passed",
                ended_at=datetime.now(timezone.utc).isoformat(),
                resource_usage={
                    "implementation": {
                        "generation_mode": "provider",
                        "requested_route": {"provider": "codex", "model": "gpt-5.4", "cli_tool": None},
                    }
                },
            )
        )
        registry = ProviderRegistry({"codex": _QuotaProvider()})
        policy = QuotaPolicy(db=db, limits_per_source={"codex": 12}, registry=registry)
        coordinator = CoordinatorController(router=RouteChooser(registry), quota_policy=policy)
        decision, route = coordinator.coordinate(
            ControllerEnvelope(controller_id="prime", task_id="task-1", priority=5, urgency=3, risk="low")
        )
        assert route.provider == "codex"
        assert decision.status == "deferred"
        assert "most constraining quota window" in decision.reason

    if tmp_path is not None:
        _run(tmp_path)
        return
    with TemporaryDirectory() as tmp:
        _run(Path(tmp))


class _CliQuotaProvider(Provider):
    @property
    def name(self) -> str:
        return "cli"

    def is_configured(self) -> bool:
        return True

    def default_model(self) -> str | None:
        return None

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(provider="cli", model=None, content="OK")

    def available_tools(self) -> list[str]:
        return ["kilocode", "gemini-cli"]


def test_coordinator_prefers_kilocode_for_delegable_work(tmp_path: Path | None = None):
    def _run(base: Path) -> None:
        db = AstrataDatabase(base / "quota.db")
        db.initialize()
        registry = ProviderRegistry({"codex": _QuotaProvider(), "cli": _CliQuotaProvider()})
        policy = QuotaPolicy(db=db, limits_per_source={"codex": 12, "cli:kilocode": 200}, registry=registry)
        coordinator = CoordinatorController(router=RouteChooser(registry), quota_policy=policy)
        decision, route = coordinator.coordinate(
            ControllerEnvelope(
                controller_id="prime",
                task_id="task-2",
                priority=5,
                urgency=3,
                risk="low",
                metadata={
                    "preferred_cli_tools": ["kilocode", "gemini-cli"],
                    "avoided_providers": ["codex"],
                },
            )
        )
        assert decision.status == "accepted"
        assert route.provider == "cli"
        assert route.cli_tool == "kilocode"

    if tmp_path is not None:
        _run(tmp_path)
        return
    with TemporaryDirectory() as tmp:
        _run(Path(tmp))


def test_coordinator_keeps_review_work_off_codex_when_cli_is_available(tmp_path: Path | None = None):
    def _run(base: Path) -> None:
        db = AstrataDatabase(base / "quota.db")
        db.initialize()
        registry = ProviderRegistry({"codex": _QuotaProvider(), "cli": _CliQuotaProvider()})
        policy = QuotaPolicy(db=db, limits_per_source={"codex": 12, "cli:kilocode": 200}, registry=registry)
        coordinator = CoordinatorController(router=RouteChooser(registry), quota_policy=policy)
        decision, route = coordinator.coordinate(
            ControllerEnvelope(
                controller_id="prime",
                task_id="task-review-1",
                priority=4,
                urgency=2,
                risk="low",
                metadata={"task_class": "review"},
            )
        )
        assert decision.status == "accepted"
        assert route.provider == "cli"
        assert route.cli_tool == "kilocode"

    if tmp_path is not None:
        _run(tmp_path)
        return
    with TemporaryDirectory() as tmp:
        _run(Path(tmp))


def test_coordinator_marks_review_work_as_consensus_eligible(tmp_path: Path | None = None):
    def _run(base: Path) -> None:
        db = AstrataDatabase(base / "quota.db")
        db.initialize()
        registry = ProviderRegistry({"codex": _QuotaProvider(), "cli": _CliQuotaProvider()})
        policy = QuotaPolicy(db=db, limits_per_source={"codex": 12, "cli:kilocode": 200}, registry=registry)
        coordinator = CoordinatorController(router=RouteChooser(registry), quota_policy=policy)
        decision, route = coordinator.coordinate(
            ControllerEnvelope(
                controller_id="prime",
                task_id="task-review-2",
                priority=3,
                urgency=2,
                risk="low",
                metadata={
                    "task_class": "review",
                    "completion_type": "review_or_audit",
                },
            )
        )
        assert decision.status == "accepted"
        assert route.provider == "cli"
        actions = {action["type"]: action for action in decision.followup_actions}
        assert "consensus_approval_eligible" in actions
        assert "batch_if_non_urgent" in actions

    if tmp_path is not None:
        _run(tmp_path)
        return
    with TemporaryDirectory() as tmp:
        _run(Path(tmp))
