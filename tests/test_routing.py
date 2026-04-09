from astrata.providers.base import Provider
from astrata.providers.registry import ProviderRegistry
from astrata.routing.policy import RouteChooser


class _FakeProvider(Provider):
    def __init__(self, name: str, model: str, configured: bool = True):
        self._name = name
        self._model = model
        self._configured = configured

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return self._configured

    def default_model(self) -> str | None:
        return self._model

    def complete(self, request):  # pragma: no cover - not used in this test
        raise NotImplementedError


def test_route_chooser_prefers_local_when_requested():
    registry = ProviderRegistry(
        {
            "openai": _FakeProvider("openai", "gpt-x"),
            "ollama": _FakeProvider("ollama", "llama-local"),
        }
    )
    route = RouteChooser(registry).choose(priority=1, urgency=1, risk="low", prefer_local=True)
    assert route.provider == "ollama"


def test_route_chooser_prefers_stronger_provider_for_high_risk():
    registry = ProviderRegistry(
        {
            "openai": _FakeProvider("openai", "gpt-x"),
            "ollama": _FakeProvider("ollama", "llama-local"),
        }
    )
    route = RouteChooser(registry).choose(priority=1, urgency=1, risk="high", prefer_local=False)
    assert route.provider == "openai"
