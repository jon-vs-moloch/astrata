from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.local.backends.llama_cpp import LlamaCppBackend, LlamaCppLaunchConfig
from astrata.local.catalog import StarterCatalog
from astrata.local.hardware import probe_thermal_state
from astrata.local.models.discovery import discover_local_models, effective_search_paths
from astrata.local.operations import OperationProgress, OperationTracker
from astrata.local.models.registry import LocalModelRegistry
from astrata.local.profiles import RuntimeProfileRegistry
from astrata.local.recommendation import HardwareProfile, ThermalState, recommend_runtime_selection
from astrata.local.telemetry import LocalModelTelemetryStore
from astrata.eval.local_models import summarize_local_model_evals
from astrata.eval.ratings import RatingStore
from astrata.eval.local_model_arena import LocalModelArena
from astrata.eval.substrate import build_eval_domain
from astrata.local.thermal import ThermalController
from astrata.local.runtime.manager import LocalRuntimeManager
from astrata.local.runtime.processes import ManagedProcessController


def test_local_model_registry_adopts_path_once():
    registry = LocalModelRegistry()
    first = registry.adopt("/tmp/models/test.gguf")
    second = registry.adopt("/tmp/models/test.gguf")
    assert first.model_id == second.model_id
    assert first.display_name == "test"


def test_llama_cpp_backend_builds_launch_spec():
    backend = LlamaCppBackend()
    spec = backend.build_launch_spec(
        config=LlamaCppLaunchConfig(
            binary_path="/usr/local/bin/llama-server",
            host="127.0.0.1",
            port=8081,
            model_path="/models/demo.gguf",
            extra_args=("--ctx-size", "8192"),
        )
    )
    assert spec.command[:5] == [
        "/usr/local/bin/llama-server",
        "--host",
        "127.0.0.1",
        "--port",
        "8081",
    ]
    assert "-m" in spec.command
    assert spec.endpoint == "http://127.0.0.1:8081/health"


def test_local_runtime_manager_tracks_selection_and_missing_backend_health():
    manager = LocalRuntimeManager()
    selection = manager.select_runtime(
        backend_id="llama_cpp",
        model_id="demo-model",
        mode="managed",
        endpoint="http://127.0.0.1:8080/health",
    )
    assert selection.backend_id == "llama_cpp"
    health = manager.health()
    assert health is not None
    assert health.ok is False
    assert health.status == "missing_backend"


def test_local_runtime_manager_uses_registered_backend_health():
    backend = LlamaCppBackend()
    manager = LocalRuntimeManager(backends={"llama_cpp": backend})
    manager.select_runtime(backend_id="llama_cpp")
    health = manager.health(config={"host": "127.0.0.1", "port": 65500})
    assert health is not None
    assert health.backend_id == "llama_cpp"
    assert health.status in {"healthy", "degraded", "unreachable"}


def test_local_runtime_manager_exposes_backend_capabilities():
    backend = LlamaCppBackend()
    manager = LocalRuntimeManager(backends={"llama_cpp": backend})
    capabilities = manager.backend_capabilities("llama_cpp")
    assert capabilities.backend_id == "llama_cpp"
    assert capabilities.multi_model_residency is True
    assert capabilities.managed_processes is True


def test_discover_local_models_finds_gguf_files():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        models_dir = root / "models"
        models_dir.mkdir()
        model_path = models_dir / "gemma-demo.gguf"
        model_path.write_bytes(b"demo")
        registry = LocalModelRegistry()
        discovered = discover_local_models(
            registry,
            search_paths=(str(models_dir),),
            include_default_search_paths=False,
        )
        assert str(model_path) in discovered
        models = registry.list_models()
        assert len(models) == 1
        assert models[0].family == "gemma"
        assert models[0].size_bytes == 4


def test_recommend_runtime_selection_prefers_fitting_model():
    registry = LocalModelRegistry()
    model = registry.adopt("/tmp/qwen-demo.gguf", display_name="qwen-demo")
    recommendation = recommend_runtime_selection(
        hardware=HardwareProfile(
            platform="darwin",
            arch="arm64",
            cpu_count=8,
            total_memory_bytes=32 * 1024**3,
            apple_metal_likely=True,
        ),
        models=[model],
        thermal=ThermalState(preference="balanced", telemetry_available=False, fans_allowed=True),
    )
    assert recommendation.model is not None
    assert recommendation.model.model_id == model.model_id
    assert recommendation.profile_id in {"balanced", "quality", "turbo"}


def test_recommend_runtime_selection_prefers_quiet_profile_when_requested():
    registry = LocalModelRegistry()
    model = registry.adopt("/tmp/gemma-demo.gguf", display_name="gemma-demo")
    recommendation = recommend_runtime_selection(
        hardware=HardwareProfile(
            platform="darwin",
            arch="arm64",
            cpu_count=8,
            total_memory_bytes=32 * 1024**3,
            apple_metal_likely=True,
        ),
        models=[model],
        thermal=ThermalState(preference="quiet", telemetry_available=False, fans_allowed=False),
    )
    assert recommendation.model is not None
    assert recommendation.profile_id == "quiet"
    assert "Quiet preference" in recommendation.reason


def test_recommend_runtime_selection_demotes_projector_artifacts():
    registry = LocalModelRegistry()
    projector = registry.adopt(
        "/tmp/mmproj-gemma.gguf",
        display_name="mmproj-gemma",
    )
    real_model = registry.adopt(
        "/tmp/gemma-4-e2b-it-q4.gguf",
        display_name="gemma-4-e2b-it-q4",
    )
    recommendation = recommend_runtime_selection(
        hardware=HardwareProfile(
            platform="darwin",
            arch="arm64",
            cpu_count=8,
            total_memory_bytes=16 * 1024**3,
            apple_metal_likely=True,
        ),
        models=[projector, real_model],
        thermal=ThermalState(preference="balanced", telemetry_available=False, fans_allowed=True),
    )
    assert recommendation.model is not None
    assert recommendation.model.model_id == real_model.model_id


def test_recommend_runtime_selection_prefers_observed_success():
    registry = LocalModelRegistry()
    gemma = registry.adopt("/tmp/gemma-4-e2b-it-q4.gguf", display_name="gemma-4-e2b-it-q4")
    qwen = registry.adopt("/tmp/qwen-3.5-4b-instruct-q4.gguf", display_name="qwen-3.5-4b-instruct-q4")
    weaker_gemma = gemma.model_copy(update={"observed_success_rate": 0.20, "observed_sample_count": 12})
    stronger_qwen = qwen.model_copy(update={"observed_success_rate": 0.90, "observed_sample_count": 12})
    recommendation = recommend_runtime_selection(
        hardware=HardwareProfile(
            platform="darwin",
            arch="arm64",
            cpu_count=8,
            total_memory_bytes=16 * 1024**3,
            apple_metal_likely=True,
        ),
        models=[weaker_gemma, stronger_qwen],
        thermal=ThermalState(preference="balanced", telemetry_available=False, fans_allowed=True),
    )
    assert recommendation.model is not None
    assert recommendation.model.model_id == stronger_qwen.model_id


def test_runtime_profile_registry_lists_quiet_profile():
    registry = RuntimeProfileRegistry()
    profiles = registry.list_profiles()
    assert any(profile.profile_id == "quiet" for profile in profiles)
    quiet = registry.get("quiet")
    assert quiet.fan_policy == "avoid"
    assert quiet.background_aggression == "low"


def test_probe_thermal_state_quiet_disallows_fans():
    thermal = probe_thermal_state(preference="quiet")
    assert thermal.preference == "quiet"
    assert thermal.fans_allowed is False


def test_local_runtime_manager_recommend_uses_quiet_preference():
    registry = LocalModelRegistry()
    registry.adopt("/tmp/gemma-demo.gguf", display_name="gemma-demo")
    manager = LocalRuntimeManager(model_registry=registry)
    recommendation = manager.recommend(thermal_preference="quiet")
    assert recommendation.profile_id == "quiet"


def test_local_runtime_manager_start_managed_applies_quiet_profile():
    registry = LocalModelRegistry()
    model = registry.adopt("/tmp/gemma-demo.gguf", display_name="gemma-demo")
    with TemporaryDirectory() as tmp:
        controller = ManagedProcessController(
            state_path=Path(tmp) / "runtime.json",
            log_path=Path(tmp) / "runtime.log",
        )
        manager = LocalRuntimeManager(
            backends={"llama_cpp": LlamaCppBackend()},
            model_registry=registry,
            process_controller=controller,
        )
        manager._wait_until_healthy = lambda **kwargs: None  # type: ignore[method-assign]
        original_start = controller.start
        captured = {}

        def fake_start(launch_spec):
            captured["launch_spec"] = launch_spec
            return controller.status()

        controller.start = fake_start  # type: ignore[method-assign]
        try:
            manager.start_managed(
                backend_id="llama_cpp",
                model_id=model.model_id,
                profile_id="quiet",
                binary_path="/usr/local/bin/llama-server",
                host="127.0.0.1",
                port=8090,
            )
        finally:
            controller.start = original_start  # type: ignore[method-assign]

        launch_spec = captured["launch_spec"]
        assert launch_spec.command[:5] == [
            "/usr/local/bin/llama-server",
            "--host",
            "127.0.0.1",
            "--port",
            "8090",
        ]
        assert "-t" in launch_spec.command
        assert "1" in launch_spec.command
        assert "-ngl" in launch_spec.command
        selection = manager.current_selection()
        assert selection is not None
        assert selection.profile_id == "quiet"
        assert selection.runtime_key == "default"


def test_local_runtime_manager_can_track_multiple_managed_runtimes():
    registry = LocalModelRegistry()
    small = registry.adopt("/tmp/qwen-small.gguf", display_name="qwen-small")
    big = registry.adopt("/tmp/qwen-big.gguf", display_name="qwen-big")
    with TemporaryDirectory() as tmp:
        controller = ManagedProcessController(
            state_path=Path(tmp) / "runtime.json",
            log_path=Path(tmp) / "runtime.log",
        )
        manager = LocalRuntimeManager(
            backends={"llama_cpp": LlamaCppBackend()},
            model_registry=registry,
            process_controller=controller,
        )
        captured = []

        def fake_wait_until_healthy(*, backend, config, timeout_seconds=60.0, poll_seconds=0.25):
            return None

        manager._wait_until_healthy = fake_wait_until_healthy  # type: ignore[method-assign]

        original_start = ManagedProcessController.start

        def fake_start(self, launch_spec):
            captured.append((self.state_path.name, self.log_path.name, list(launch_spec.command)))
            self.state_path.write_text(
                '{"pid": 123, "endpoint": "%s", "command": [], "log_path": "%s", "started_at": 1.0}'
                % (launch_spec.endpoint, self.log_path),
                encoding="utf-8",
            )
            return self.status()

        ManagedProcessController.start = fake_start  # type: ignore[method-assign]
        try:
            manager.start_managed(
                runtime_key="draft",
                backend_id="llama_cpp",
                model_id=small.model_id,
                profile_id="quiet",
                binary_path="/usr/local/bin/llama-server",
                host="127.0.0.1",
                port=8091,
                activate=False,
            )
            manager.start_managed(
                runtime_key="verify",
                backend_id="llama_cpp",
                model_id=big.model_id,
                profile_id="quiet",
                binary_path="/usr/local/bin/llama-server",
                host="127.0.0.1",
                port=8092,
            )
        finally:
            ManagedProcessController.start = original_start  # type: ignore[method-assign]

        assert [item[0] for item in captured] == ["runtime-draft.json", "runtime-verify.json"]
        selections = {selection.runtime_key: selection for selection in manager.list_selections()}
        assert set(selections) == {"draft", "verify"}
        assert manager.current_selection() is not None
        assert manager.current_selection().runtime_key == "verify"
        assert manager.current_selection("draft").model_id == small.model_id
        statuses = manager.list_managed_statuses()
        assert set(statuses) == {"draft", "verify"}
        assert statuses["draft"].endpoint == "http://127.0.0.1:8091/health"
        assert statuses["verify"].endpoint == "http://127.0.0.1:8092/health"


def test_effective_search_paths_include_lmstudio_defaults():
    paths = effective_search_paths(())
    assert any(".lmstudio/models" in path for path in paths)
    assert any("LM Studio/models" in path for path in paths)


def test_thermal_controller_holds_cooldown_latch_after_nominal_sample():
    with TemporaryDirectory() as tmp:
        controller = ThermalController(state_path=Path(tmp) / "thermal.json")
        first = controller.evaluate(ThermalState(preference="quiet", thermal_pressure="fair"))
        second = controller.evaluate(ThermalState(preference="quiet", thermal_pressure="nominal"))
        assert first.action == "cooldown"
        assert second.action == "cooldown"
        assert second.latched == "fair"


def test_starter_catalog_contains_current_families():
    catalog = StarterCatalog()
    ids = {model.catalog_id for model in catalog.list_models()}
    assert "gemma-4" in ids
    assert "qwen-3.5" in ids
    assert "lfm-2.5" in ids


def test_operation_tracker_persists_to_disk():
    with TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "ops.json"
        tracker = OperationTracker(state_path=state_path)
        op = tracker.start_operation("local_model_adopt", OperationProgress(message="starting"))
        tracker.complete_operation(op.operation_id, {"ok": True})

        reloaded = OperationTracker(state_path=state_path)
        operations = reloaded.list_operations()
        assert len(operations) == 1
        assert operations[0].status == "succeeded"
        assert operations[0].result == {"ok": True}


def test_local_model_telemetry_store_summarizes_observations():
    with TemporaryDirectory() as tmp:
        store = LocalModelTelemetryStore(state_path=Path(tmp) / "telemetry.json")
        store.set_benchmark(model_path="/tmp/demo.gguf", score=12.0, source="research")
        store.record_observation(
            model_path="/tmp/demo.gguf",
            task_class="coding",
            score=0.9,
            success=True,
        )
        store.record_observation(
            model_path="/tmp/demo.gguf",
            task_class="coding",
            score=0.4,
            success=False,
        )
        summary = store.summarize("/tmp/demo.gguf")
        assert summary.observed_sample_count == 2
        assert summary.observed_success_rate == 0.5
        assert summary.benchmark_score == 12.0
        assert summary.benchmark_source == "research"


def test_local_model_eval_uses_observed_trials_to_pick_winner():
    with TemporaryDirectory() as tmp:
        store = LocalModelTelemetryStore(state_path=Path(tmp) / "telemetry.json")
        qwen = "/tmp/qwen.gguf"
        gemma = "/tmp/gemma.gguf"
        store.record_observation(model_path=qwen, task_class="coding", score=0.92, success=True)
        store.record_observation(model_path=qwen, task_class="coding", score=0.88, success=True)
        store.record_observation(model_path=gemma, task_class="coding", score=0.70, success=True)
        store.record_observation(model_path=gemma, task_class="coding", score=0.68, success=True)
        summary = summarize_local_model_evals(telemetry=store, task_class="coding")
        assert summary.domain.subject_kind == "local_model"
        assert summary.domain.mutation_surface == "model_profile"
        assert summary.domain.environment == "local_runtime"
        assert summary.decision.winner_variant_id == qwen
        assert summary.summaries[0].variant_id == qwen


def test_eval_domain_builds_shared_rating_namespace():
    domain = build_eval_domain(
        subject_kind="provider_route",
        task_class="coding",
        mutation_surface="route_choice",
        environment="cloud",
    )
    assert domain.rating_domain == "provider_route:coding"


def test_rating_store_tracks_domain_scoped_pairwise_winner():
    with TemporaryDirectory() as tmp:
        ratings = RatingStore(state_path=Path(tmp) / "ratings.json")
        qwen = "/tmp/qwen.gguf"
        gemma = "/tmp/gemma.gguf"
        ratings.record_matchup(domain="local_model:coding", left_variant_id=qwen, right_variant_id=gemma, left_score=1.0)
        ratings.record_matchup(domain="local_model:coding", left_variant_id=qwen, right_variant_id=gemma, left_score=1.0)
        assert ratings.get_domain_leader(domain="local_model:coding", min_matches=2) == qwen


def test_local_model_arena_records_observations_and_matchup():
    class FakeLmStudio:
        def generate(self, *, model_key, prompt, system_prompt=None, ttl_seconds=300):
            from astrata.local.lmstudio import LmStudioGeneration
            content = "left output" if "left" in model_key else "right output"
            duration = 2.0 if "left" in model_key else 4.0
            return LmStudioGeneration(model_key=model_key, prompt=prompt, content=content, duration_seconds=duration)

    class FakeJudge:
        name = "fake-judge"

        def complete(self, request):
            from astrata.providers.base import CompletionResponse
            return CompletionResponse(provider="fake", model=None, content='{"left_score": 1.0, "rationale": "Left is better and faster."}')

    with TemporaryDirectory() as tmp:
        telemetry = LocalModelTelemetryStore(state_path=Path(tmp) / "telemetry.json")
        ratings = RatingStore(state_path=Path(tmp) / "ratings.json")
        arena = LocalModelArena(lmstudio=FakeLmStudio(), telemetry=telemetry, ratings=ratings)
        result = arena.run_pair_eval(
            task_class="coding",
            prompt="Solve the task.",
            left_model_key="/tmp/left.gguf",
            right_model_key="/tmp/right.gguf",
            judge=FakeJudge(),
        )
        assert result.left_score == 1.0
        left_summary = telemetry.summarize("/tmp/left.gguf")
        right_summary = telemetry.summarize("/tmp/right.gguf")
        assert left_summary.observed_sample_count == 1
        assert right_summary.observed_sample_count == 1
        assert ratings.get_domain_leader(domain="local_model:coding", min_matches=1) == "/tmp/left.gguf"


def test_managed_process_controller_reports_not_running_without_state():
    with TemporaryDirectory() as tmp:
        controller = ManagedProcessController(
            state_path=Path(tmp) / "runtime.json",
            log_path=Path(tmp) / "runtime.log",
        )
        status = controller.status()
        assert status.running is False
        assert status.detail == "not_running"
