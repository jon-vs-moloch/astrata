from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.controllers.base import ControllerEnvelope
from astrata.controllers.coordinator import CoordinatorController
from astrata.eval.observations import EvalObservation, EvalObservationStore
from astrata.eval.ratings import RatingStore
from astrata.routing.advisor import RoutePerformanceAdvisor
from astrata.routing.policy import RouteChooser
from astrata.providers.registry import ProviderRegistry
from astrata.scheduling.quota import QuotaPolicy


class _FakeProvider:
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return True

    def default_model(self):
        return None

    def complete(self, request):
        raise NotImplementedError

    def get_quota_windows(self, route=None):
        return None


class _FakeCliProvider(_FakeProvider):
    def __init__(self):
        super().__init__("cli")

    def available_tools(self):
        return ["kilocode", "gemini-cli"]


class _FakeDb:
    def list_records(self, table_name: str):
        return []


def test_route_performance_advisor_prefers_rating_leader_cli_tool():
    with TemporaryDirectory() as tmp:
        data = Path(tmp)
        observations = EvalObservationStore(state_path=data / "eval_observations.json")
        ratings = RatingStore(state_path=data / "local_model_ratings.json")
        ratings.record_matchup(
            domain="execution_route:coding",
            left_variant_id="cli:kilocode",
            right_variant_id="google:gemini-2.5-flash",
            left_score=1.0,
        )
        ratings.record_matchup(
            domain="execution_route:coding",
            left_variant_id="cli:kilocode",
            right_variant_id="google:gemini-2.5-flash",
            left_score=1.0,
        )
        advisor = RoutePerformanceAdvisor(observations=observations, ratings=ratings)
        advice = advisor.advise(task_class="coding")
        assert advice.preferred_cli_tools == ("kilocode",)


def test_coordinator_uses_empirical_route_advice_when_no_explicit_preferences():
    with TemporaryDirectory() as tmp:
        data = Path(tmp)
        observations = EvalObservationStore(state_path=data / "eval_observations.json")
        ratings = RatingStore(state_path=data / "local_model_ratings.json")
        observations.record(
            EvalObservation(
                subject_kind="execution_route",
                subject_id="cli:kilocode",
                variant_id="cli:kilocode",
                task_class="coding",
                score=0.4,
                passed=True,
            )
        )
        observations.record(
            EvalObservation(
                subject_kind="execution_route",
                subject_id="cli:kilocode",
                variant_id="cli:kilocode",
                task_class="coding",
                score=0.5,
                passed=True,
            )
        )
        advisor = RoutePerformanceAdvisor(observations=observations, ratings=ratings)
        registry = ProviderRegistry(
            providers={
                "cli": _FakeCliProvider(),
                "codex": _FakeProvider("codex"),
                "google": _FakeProvider("google"),
            }
        )
        coordinator = CoordinatorController(
            router=RouteChooser(registry),
            quota_policy=QuotaPolicy(db=_FakeDb(), limits_per_source={}, registry=registry),
            route_advisor=advisor,
        )
        envelope = ControllerEnvelope(
            controller_id="prime",
            task_id="demo",
            priority=5,
            urgency=5,
            risk="low",
            metadata={"task_class": "coding"},
        )
        _decision, route = coordinator.coordinate(envelope)
        assert route.provider == "cli"
        assert route.cli_tool == "kilocode"


def test_route_performance_advisor_uses_exploration_bonus_for_sparse_candidates():
    with TemporaryDirectory() as tmp:
        data = Path(tmp)
        observations = EvalObservationStore(state_path=data / "eval_observations.json")
        ratings = RatingStore(state_path=data / "local_model_ratings.json")
        for score in (0.50, 0.49, 0.50, 0.49):
            observations.record(
                EvalObservation(
                    subject_kind="execution_route",
                    subject_id="codex:gpt-5.4",
                    variant_id="codex:gpt-5.4",
                    task_class="coding",
                    score=score,
                    passed=True,
                    confidence=0.8,
                    total_wall_seconds=1.4,
                )
            )
        observations.record(
            EvalObservation(
                subject_kind="execution_route",
                subject_id="cli:gemini-cli",
                variant_id="cli:gemini-cli",
                task_class="coding",
                score=0.48,
                passed=True,
                confidence=0.7,
                total_wall_seconds=2.0,
            )
        )
        advisor = RoutePerformanceAdvisor(observations=observations, ratings=ratings)
        advice = advisor.advise(task_class="coding")
        assert advice.preferred_cli_tools == ("gemini-cli",)
        assert advice.rationale.startswith("information_gain_explore:")


def test_route_performance_advisor_keeps_established_winner_when_gap_is_decisive():
    with TemporaryDirectory() as tmp:
        data = Path(tmp)
        observations = EvalObservationStore(state_path=data / "eval_observations.json")
        ratings = RatingStore(state_path=data / "local_model_ratings.json")
        for score in (0.82, 0.84, 0.81, 0.83):
            observations.record(
                EvalObservation(
                    subject_kind="execution_route",
                    subject_id="cli:kilocode",
                    variant_id="cli:kilocode",
                    task_class="coding",
                    score=score,
                    passed=True,
                    confidence=0.8,
                    total_wall_seconds=3.0,
                )
            )
        observations.record(
            EvalObservation(
                subject_kind="execution_route",
                subject_id="cli:gemini-cli",
                variant_id="cli:gemini-cli",
                task_class="coding",
                score=0.60,
                passed=True,
                confidence=0.8,
                total_wall_seconds=2.0,
            )
        )
        advisor = RoutePerformanceAdvisor(observations=observations, ratings=ratings)
        advice = advisor.advise(task_class="coding")
        assert advice.preferred_cli_tools == ("kilocode",)
        assert advice.rationale.startswith("capable_least_constrained:")


def test_route_performance_advisor_penalizes_scarce_bottleneck_routes_when_close():
    with TemporaryDirectory() as tmp:
        data = Path(tmp)
        observations = EvalObservationStore(state_path=data / "eval_observations.json")
        ratings = RatingStore(state_path=data / "local_model_ratings.json")
        for score in (0.60, 0.61, 0.60):
            observations.record(
                EvalObservation(
                    subject_kind="execution_route",
                    subject_id="codex:gpt-5.4",
                    variant_id="codex:gpt-5.4",
                    task_class="coding",
                    score=score,
                    passed=True,
                    confidence=0.8,
                    total_wall_seconds=1.2,
                )
            )
        for score in (0.57, 0.58, 0.57):
            observations.record(
                EvalObservation(
                    subject_kind="execution_route",
                    subject_id="cli:kilocode",
                    variant_id="cli:kilocode",
                    task_class="coding",
                    score=score,
                    passed=True,
                    confidence=0.8,
                    total_wall_seconds=4.0,
                )
            )
        advisor = RoutePerformanceAdvisor(observations=observations, ratings=ratings)
        advice = advisor.advise(task_class="coding")
        assert advice.preferred_cli_tools == ("kilocode",)
        assert advice.rationale.startswith("capable_least_constrained:")


def test_route_performance_advisor_uses_best_available_when_no_route_clears_floor():
    with TemporaryDirectory() as tmp:
        data = Path(tmp)
        observations = EvalObservationStore(state_path=data / "eval_observations.json")
        ratings = RatingStore(state_path=data / "local_model_ratings.json")
        for score in (0.30, 0.32, 0.31):
            observations.record(
                EvalObservation(
                    subject_kind="execution_route",
                    subject_id="codex:gpt-5.4",
                    variant_id="codex:gpt-5.4",
                    task_class="coding",
                    score=score,
                    passed=True,
                    confidence=0.8,
                    total_wall_seconds=1.2,
                )
            )
        for score in (0.27, 0.28, 0.29):
            observations.record(
                EvalObservation(
                    subject_kind="execution_route",
                    subject_id="cli:kilocode",
                    variant_id="cli:kilocode",
                    task_class="coding",
                    score=score,
                    passed=True,
                    confidence=0.8,
                    total_wall_seconds=4.0,
                )
            )
        advisor = RoutePerformanceAdvisor(observations=observations, ratings=ratings)
        advice = advisor.advise(task_class="coding")
        assert advice.preferred_providers == ("codex",)
        assert advice.rationale.startswith("best_available_route:")
