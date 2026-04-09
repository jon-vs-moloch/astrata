from pathlib import Path

from astrata.providers.base import CompletionResponse, Provider
from astrata.procedures.execution import BoundedFileGenerationProcedure, ProcedureExecutionRequest
from astrata.procedures.health import RouteHealthStore
from astrata.providers.registry import ProviderRegistry
from astrata.routing.policy import RouteChooser


class _FakeProvider(Provider):
    def __init__(self, name: str, model: str = "test-model") -> None:
        self._name = name
        self._model = model

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return True

    def default_model(self) -> str | None:
        return self._model

    def complete(self, request):
        return CompletionResponse(
            provider=self._name,
            model=self._model,
            content='{"files":{"astrata/generated.py":"VALUE = \\"ok\\"\\n"}}',
        )


def test_bounded_file_generation_procedure_uses_fallback_builder(tmp_path: Path):
    procedure = BoundedFileGenerationProcedure(
        registry=ProviderRegistry({}),
        router=RouteChooser(ProviderRegistry({})),
        health_store=RouteHealthStore(tmp_path / "route-health.json"),
    )
    request = ProcedureExecutionRequest(
        procedure_id="test",
        title="Create test file",
        description="Write one bounded file",
        expected_paths=["astrata/generated.py"],
    )
    result = procedure.execute(
        project_root=tmp_path,
        request=request,
        fallback_builder=lambda _: {"astrata/generated.py": 'VALUE = "ok"\n'},
    )
    assert result.status == "applied"
    assert (tmp_path / "astrata/generated.py").exists()
    assert result.generation_mode == "fallback"
    assert result.requested_route == {}


def test_bounded_file_generation_procedure_can_force_fallback_only(tmp_path: Path):
    procedure = BoundedFileGenerationProcedure(
        registry=ProviderRegistry({}),
        router=RouteChooser(ProviderRegistry({})),
        health_store=RouteHealthStore(tmp_path / "route-health.json"),
    )
    request = ProcedureExecutionRequest(
        procedure_id="test",
        title="Create test file",
        description="Write one bounded file",
        expected_paths=["astrata/generated.py"],
    )
    result = procedure.execute(
        project_root=tmp_path,
        request=request,
        fallback_builder=lambda _: {"astrata/generated.py": 'VALUE = "ok"\n'},
        force_fallback_only=True,
    )
    assert result.status == "applied"
    assert result.degraded_reason == "planner_selected_fallback_only"


def test_route_health_store_degrades_after_repeated_failure(tmp_path: Path):
    store = RouteHealthStore(tmp_path / "route-health.json")
    route = {"provider": "ollama", "model": "local-model", "cli_tool": None}
    store.record_failure(route, failure_kind="connection", error="connection refused")
    store.record_failure(route, failure_kind="connection", error="connection refused")
    assessment = store.assess(route)
    assert assessment["status"] == "degraded"


def test_bounded_file_generation_procedure_honors_preferred_provider(tmp_path: Path):
    registry = ProviderRegistry(
        {
            "openai": _FakeProvider("openai", "gpt-test"),
            "google": _FakeProvider("google", "gemini-test"),
        }
    )
    procedure = BoundedFileGenerationProcedure(
        registry=registry,
        router=RouteChooser(registry),
        health_store=RouteHealthStore(tmp_path / "route-health.json"),
    )
    request = ProcedureExecutionRequest(
        procedure_id="test",
        title="Create test file",
        description="Write one bounded file",
        expected_paths=["astrata/generated.py"],
        preferred_provider="google",
        avoided_providers=["openai"],
    )
    result = procedure.execute(project_root=tmp_path, request=request)
    assert result.status == "applied"
    assert result.generation_mode == "provider"
    assert result.requested_route["provider"] == "google"
