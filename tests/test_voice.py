from pathlib import Path

from astrata.config.settings import AstrataPaths, LocalRuntimeSettings, RuntimeLimits, Settings
from astrata.voice import VoiceService


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


def test_voice_status_prefers_local_backends(tmp_path: Path, monkeypatch):
    service = VoiceService(settings=_settings(tmp_path))

    monkeypatch.setattr("astrata.voice.service.shutil.which", lambda exe: {"say": "/usr/bin/say", "whisper": "/usr/bin/whisper"}.get(exe))
    monkeypatch.setattr("astrata.voice.service.importlib.util.find_spec", lambda name: None)
    monkeypatch.setattr("astrata.voice.service.build_default_registry", lambda: type("Registry", (), {"configured_provider_names": lambda self: ["codex", "google"]})())

    status = service.status()

    assert status["recommended_output_backend"] == "macos-say"
    assert status["recommended_input_backend"] == "whisper-cli"
    assert any(item["backend_id"] == "openai-realtime-audio" for item in status["output_backends"])
    assert status["recommended_output_models"][0]["model_id"] == "kokoro-82m"
    assert status["recommended_output_models"][1]["model_id"] == "omnivoice"
    assert status["recommended_input_models"][0]["model_id"] == "native-chat-audio"
    assert status["recommended_input_models"][1]["model_id"] == "whisper-tiny"
    assert status["recommended_input_models"][2]["model_id"] == "moonshine"
    assert status["recommended_input_models"][3]["model_id"] == "whisper-family"
    assert status["preload_defaults"][0]["model_id"] == "kokoro-82m"
    assert status["preload_defaults"][1]["model_id"] == "whisper-tiny"


def test_voice_speak_uses_local_say_backend(tmp_path: Path, monkeypatch):
    service = VoiceService(settings=_settings(tmp_path))
    seen: dict[str, object] = {}

    monkeypatch.setattr("astrata.voice.service.shutil.which", lambda exe: "/usr/bin/say" if exe == "say" else None)

    def _fake_run(cmd, capture_output, text, check):
        seen["cmd"] = cmd
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("astrata.voice.service.subprocess.run", _fake_run)

    result = service.speak("Hello there", voice="Samantha")

    assert result["backend"] == "macos-say"
    assert seen["cmd"] == ["say", "-v", "Samantha", "Hello there"]


def test_voice_transcribe_uses_whisper_cli(tmp_path: Path, monkeypatch):
    service = VoiceService(settings=_settings(tmp_path))
    audio_path = tmp_path / "clip.wav"
    audio_path.write_bytes(b"RIFF")

    monkeypatch.setattr("astrata.voice.service.shutil.which", lambda exe: "/usr/bin/whisper" if exe == "whisper" else None)

    def _fake_run(cmd, capture_output, text, check):
        output_dir = Path(cmd[cmd.index("--output_dir") + 1])
        stem = Path(cmd[1]).stem
        (output_dir / f"{stem}.txt").write_text("hello world", encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("astrata.voice.service.subprocess.run", _fake_run)

    result = service.transcribe(str(audio_path), model="tiny")

    assert result["backend"] == "whisper-cli"
    assert result["text"] == "hello world"


def test_voice_preload_defaults_stages_light_assets(tmp_path: Path, monkeypatch):
    service = VoiceService(settings=_settings(tmp_path))
    calls: list[dict[str, object]] = []

    class _Bootstrap:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def ensure_huggingface_snapshot(self, *, repo_id, destination_dir, allow_patterns=None):
            calls.append(
                {
                    "repo_id": repo_id,
                    "destination_dir": str(destination_dir),
                    "allow_patterns": list(allow_patterns or []),
                }
            )
            return True

    monkeypatch.setattr("astrata.voice.service.DependencyBootstrapService", _Bootstrap)

    result = service.preload_defaults()

    assert result["status"] == "ok"
    assert [entry["asset_id"] for entry in result["installed"]] == ["kokoro-82m", "whisper-tiny"]
    assert calls[0]["repo_id"] == "onnx-community/Kokoro-82M-v1.0-ONNX"
    assert calls[1]["repo_id"] == "Systran/faster-whisper-tiny"


def test_voice_install_asset_stages_named_asset(tmp_path: Path, monkeypatch):
    service = VoiceService(settings=_settings(tmp_path))
    calls: list[str] = []

    class _Bootstrap:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def ensure_huggingface_snapshot(self, *, repo_id, destination_dir, allow_patterns=None):
            calls.append(repo_id)
            return True

    monkeypatch.setattr("astrata.voice.service.DependencyBootstrapService", _Bootstrap)

    result = service.install_asset("omnivoice")

    assert result["asset_id"] == "omnivoice"
    assert calls == ["k2-fsa/OmniVoice"]


def test_voice_install_asset_records_registry(tmp_path: Path, monkeypatch):
    service = VoiceService(settings=_settings(tmp_path))

    class _Bootstrap:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def ensure_huggingface_snapshot(self, *, repo_id, destination_dir, allow_patterns=None):
            destination_dir.mkdir(parents=True, exist_ok=True)
            (destination_dir / "weights.bin").write_bytes(b"1234")
            return True

    monkeypatch.setattr("astrata.voice.service.DependencyBootstrapService", _Bootstrap)

    result = service.install_asset("whisper-tiny")

    assert result["observed_size_bytes"] == 4
    registry_payload = (tmp_path / ".astrata" / "voice_registry.json").read_text(encoding="utf-8")
    assert "whisper-tiny" in registry_payload
