from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.eval.observations import EvalObservationStore
from astrata.eval.provider_routes import ProviderRouteArena
from astrata.eval.ratings import RatingStore
from astrata.providers.base import CompletionResponse
from astrata.providers.registry import ProviderRegistry


def test_provider_route_arena_records_observations_and_matchup():
    class FakeProvider:
        def __init__(self, name, content):
            self._name = name
            self._content = content

        @property
        def name(self):
            return self._name

        def is_configured(self):
            return True

        def default_model(self):
            return None

        def complete(self, request):
            return CompletionResponse(provider=self._name, model=request.model, content=self._content)

    class FakeJudge(FakeProvider):
        def complete(self, request):
            return CompletionResponse(
                provider=self._name,
                model=request.model,
                content='{"left_score": 1.0, "rationale": "Left is more useful."}',
            )

    with TemporaryDirectory() as tmp:
        registry = ProviderRegistry(
            providers={
                "left": FakeProvider("left", "left output"),
                "right": FakeProvider("right", "right output"),
                "judge": FakeJudge("judge", ""),
            }
        )
        observations = EvalObservationStore(state_path=Path(tmp) / "obs.json")
        ratings = RatingStore(state_path=Path(tmp) / "ratings.json")
        arena = ProviderRouteArena(registry=registry, observations=observations, ratings=ratings)
        result = arena.run_pair_eval(
            task_class="coding",
            prompt="Write a helper.",
            left_route={"provider": "left"},
            right_route={"provider": "right"},
            judge=registry.get_provider("judge"),
        )
        assert result.left_score == 1.0
        assert result.left_startup_seconds == 0.0
        assert result.left_total_wall_seconds >= result.left_duration_seconds
        listed = observations.list(subject_kind="execution_route", task_class="coding")
        assert len(listed) == 2
        summary = arena.summarize(task_class="coding")
        assert summary.domain.rating_domain == "execution_route:coding"
        assert ratings.get_domain_leader(domain="execution_route:coding", min_matches=1) == "left"


def test_provider_route_arena_can_compare_local_model_against_provider():
    class FakeProvider:
        def __init__(self, name, content):
            self._name = name
            self._content = content

        @property
        def name(self):
            return self._name

        def is_configured(self):
            return True

        def default_model(self):
            return None

        def complete(self, request):
            return CompletionResponse(provider=self._name, model=request.model, content=self._content)

    class FakeJudge(FakeProvider):
        def complete(self, request):
            return CompletionResponse(
                provider=self._name,
                model=request.model,
                content='{"left_score": 0.5, "rationale": "Comparable outputs."}',
            )

    class FakeLocalRuntime:
        def __init__(self):
            self._selection = None

        def model_registry(self):
            class Registry:
                def get(self, model_key):
                    return None

                def adopt(self, model_key):
                    class Model:
                        model_id = model_key
                    return Model()
            return Registry()

        def current_selection(self):
            return self._selection

        def health(self, config=None):
            return None

        def start_managed(self, **kwargs):
            class Selection:
                endpoint = "http://127.0.0.1:9999/health"
                model_id = kwargs["model_id"]
                backend_id = "llama_cpp"
            self._selection = Selection()
            return None

    class FakeLocalClient:
        def complete(self, *, base_url, request):
            return "local output"

    with TemporaryDirectory() as tmp:
        registry = ProviderRegistry(
            providers={
                "codex": FakeProvider("codex", "cloud output"),
                "judge": FakeJudge("judge", ""),
            }
        )
        observations = EvalObservationStore(state_path=Path(tmp) / "obs.json")
        ratings = RatingStore(state_path=Path(tmp) / "ratings.json")
        arena = ProviderRouteArena(
            registry=registry,
            observations=observations,
            ratings=ratings,
            local_runtime=FakeLocalRuntime(),
            local_client=FakeLocalClient(),
        )
        result = arena.run_pair_eval(
            task_class="coding",
            prompt="Write a helper.",
            left_route={"provider": "local-model", "model": "/tmp/qwen.gguf"},
            right_route={"provider": "codex"},
            judge=registry.get_provider("judge"),
        )
        assert result.left_variant_id == "local:/tmp/qwen.gguf"
        assert result.left_startup_seconds >= 0.0
        assert result.left_total_wall_seconds >= result.left_duration_seconds
        listed = observations.list(subject_kind="execution_route", task_class="coding")
        assert len(listed) == 2


def test_provider_route_arena_can_compare_strata_endpoint_against_provider():
    class FakeProvider:
        def __init__(self, name, content):
            self._name = name
            self._content = content

        @property
        def name(self):
            return self._name

        def is_configured(self):
            return True

        def default_model(self):
            return None

        def complete(self, request):
            return CompletionResponse(provider=self._name, model=request.model, content=self._content)

    class FakeJudge(FakeProvider):
        def complete(self, request):
            return CompletionResponse(
                provider=self._name,
                model=request.model,
                content='{"left_score": 0.5, "rationale": "Comparable outputs."}',
            )

    class FakeLocalClient:
        def __init__(self):
            self.calls = []

        def complete(self, *, base_url, request, thread_id=None, allow_degraded_fallback=False):
            self.calls.append(
                {
                    "base_url": base_url,
                    "thread_id": thread_id,
                    "allow_degraded_fallback": allow_degraded_fallback,
                }
            )
            return "persistent output"

    class FakeStrataService:
        def __init__(self):
            self.calls = []

        def chat(self, *, content, thread_id=None, model_id=None, allow_degraded_fallback=False, system_prompt=None):
            self.calls.append(
                {
                    "content": content,
                    "thread_id": thread_id,
                    "model_id": model_id,
                    "allow_degraded_fallback": allow_degraded_fallback,
                }
            )

            class Reply:
                content = "native persistent output"

            return Reply()

    with TemporaryDirectory() as tmp:
        registry = ProviderRegistry(
            providers={
                "codex": FakeProvider("codex", "cloud output"),
                "judge": FakeJudge("judge", ""),
            }
        )
        observations = EvalObservationStore(state_path=Path(tmp) / "obs.json")
        ratings = RatingStore(state_path=Path(tmp) / "ratings.json")
        local_client = FakeLocalClient()
        strata_service = FakeStrataService()
        arena = ProviderRouteArena(
            registry=registry,
            observations=observations,
            ratings=ratings,
            local_client=local_client,
            strata_service=strata_service,
        )
        result = arena.run_pair_eval(
            task_class="coding",
            prompt="Write a helper.",
            left_route={
                "provider": "strata-endpoint",
                "thread_id": "demo-thread",
                "allow_degraded_fallback": True,
            },
            right_route={"provider": "codex"},
            judge=registry.get_provider("judge"),
        )
        assert result.left_variant_id == "strata-endpoint"
        assert strata_service.calls[0]["thread_id"] == "demo-thread"
        assert strata_service.calls[0]["allow_degraded_fallback"] is True


def test_provider_route_arena_rejects_scarce_sidequest_judge_by_default():
    class FakeProvider:
        def __init__(self, name, content):
            self._name = name
            self._content = content

        @property
        def name(self):
            return self._name

        def is_configured(self):
            return True

        def default_model(self):
            return None

        def complete(self, request):
            return CompletionResponse(provider=self._name, model=request.model, content=self._content)

    with TemporaryDirectory() as tmp:
        registry = ProviderRegistry(
            providers={
                "left": FakeProvider("left", "left output"),
                "right": FakeProvider("right", "right output"),
                "codex": FakeProvider("codex", '{"left_score": 0.5, "rationale": "Tie."}'),
            }
        )
        observations = EvalObservationStore(state_path=Path(tmp) / "obs.json")
        ratings = RatingStore(state_path=Path(tmp) / "ratings.json")
        arena = ProviderRouteArena(registry=registry, observations=observations, ratings=ratings)
        try:
            arena.run_pair_eval(
                task_class="coding",
                prompt="Write a helper.",
                left_route={"provider": "left"},
                right_route={"provider": "right"},
                judge=registry.get_provider("codex"),
            )
        except RuntimeError as exc:
            assert "Scarce judge route" in str(exc)
        else:
            raise AssertionError("Expected scarce sidequest judge to be rejected.")
