from pathlib import Path

from astrata.config.settings import AstrataPaths, LocalRuntimeSettings, RuntimeLimits, Settings
from astrata.onboarding import OnboardingService
from astrata.procedures.registry import build_default_procedure_registry


def _settings(root: Path) -> Settings:
    data_dir = root / ".astrata"
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        paths=AstrataPaths(
            project_root=root,
            data_dir=data_dir,
            docs_dir=root,
            provider_secrets_path=data_dir / "provider_secrets.json",
        ),
        runtime_limits=RuntimeLimits(),
        local_runtime=LocalRuntimeSettings(
            model_search_paths=(),
            model_install_dir=data_dir / "models",
        ),
    )


def test_onboarding_service_bootstraps_default_plan(tmp_path: Path):
    service = OnboardingService(state_path=tmp_path / "onboarding_state.json")

    status = service.status()

    assert status["plan"]["status"] == "active"
    assert status["next_step"]["step_id"] == "configure-inference"
    assert status["total_steps"] >= 6


def test_onboarding_service_updates_step_status(tmp_path: Path):
    service = OnboardingService(state_path=tmp_path / "onboarding_state.json")
    service.ensure_plan()

    plan = service.update_step("configure-inference", status="complete", note="Connected a provider.")

    first = next(step for step in plan.steps if step.step_id == "configure-inference")
    assert first.status == "complete"
    assert "Connected a provider." in first.notes


def test_default_procedure_registry_includes_system_onboarding():
    registry = build_default_procedure_registry()

    procedure = registry.get("system-onboarding")

    assert procedure is not None
    assert procedure.default_variant_id == "guided_onboarding"
    assert "inference_ready" in procedure.expected_outputs


def test_default_procedure_registry_includes_publish_and_local_lane_tools():
    registry = build_default_procedure_registry()

    publish = registry.get("publish-to-internet")
    local_lane = registry.get("ensure-local-lane")

    assert publish is not None
    assert publish.default_variant_id == "static_or_api_publish"
    assert local_lane is not None
    assert local_lane.default_variant_id == "managed_local_recovery"


def test_onboarding_status_reports_inference_probe(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    service = OnboardingService.from_settings(settings)

    monkeypatch.setattr("astrata.onboarding.service.probe_hardware_profile", lambda: type("Hardware", (), {
        "platform": "darwin",
        "arch": "arm64",
        "cpu_count": 8,
        "total_memory_bytes": 32 * 1024**3,
        "apple_metal_likely": True,
    })())
    monkeypatch.setattr("astrata.onboarding.service.probe_thermal_state", lambda preference="quiet": type("Thermal", (), {
        "preference": preference,
        "telemetry_available": True,
        "thermal_pressure": "nominal",
    })())
    monkeypatch.setattr("astrata.onboarding.service.CliProvider.available_tools", lambda self: ["codex-cli", "kilocode"])

    status = service.status()

    probe = status["inference_probe"]
    assert probe["ready_now"] is True
    assert probe["codex_backdoor_available"] is True
    assert probe["preferred_bootstrap_route"] == "codex-cli"
    assert "gemini-cli" in [entry["tool"] for entry in probe["installable_cli_tools"]]
    assert probe["local_model_offer"]["catalog_id"] == "qwen3.5-0.8b-q4_k_m"


def test_recommended_settings_bundle_explains_actions(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    service = OnboardingService.from_settings(settings)

    monkeypatch.setattr("astrata.onboarding.service.probe_hardware_profile", lambda: type("Hardware", (), {
        "platform": "darwin",
        "arch": "arm64",
        "cpu_count": 8,
        "total_memory_bytes": 16 * 1024**3,
        "apple_metal_likely": True,
    })())
    monkeypatch.setattr("astrata.onboarding.service.probe_thermal_state", lambda preference="quiet": type("Thermal", (), {
        "preference": preference,
        "telemetry_available": True,
        "thermal_pressure": "nominal",
    })())
    monkeypatch.setattr("astrata.onboarding.service.CliProvider.available_tools", lambda self: ["codex-cli"])
    monkeypatch.setattr("astrata.onboarding.service.VoiceService.status", lambda self: {
        "preload_defaults": [
            {"model_id": "kokoro-82m", "kind": "tts", "policy": "preload_light_default", "reason": "Light TTS default."},
            {"model_id": "whisper-tiny", "kind": "stt", "policy": "preload_light_default", "reason": "Light STT default."},
            {"model_id": "omnivoice", "kind": "tts", "policy": "optional_upgrade", "reason": "Heavier upgrade."},
        ]
    })

    bundle = service.recommended_settings_bundle()

    assert bundle["label"] == "Use Recommended Settings"
    actions = {action["action_id"]: action for action in bundle["actions"]}
    assert "use-existing-inference-route" in actions
    assert "install-kilocode" in actions
    assert "install-gemini-cli" in actions
    assert "install-recommended-local-model" in actions
    assert "preload-kokoro-82m" in actions
    assert "preload-whisper-tiny" in actions
    assert actions["preload-kokoro-82m"]["command"] == ["astrata", "voice-install-asset", "kokoro-82m"]
    assert "why" not in actions["install-kilocode"]
    assert "reason" in actions["install-kilocode"]
    assert "user_value" in actions["install-kilocode"]
