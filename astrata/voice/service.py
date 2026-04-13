"""Local-first voice IO helpers and capability probing."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from typing import Any

from astrata.bootstrap import DependencyBootstrapService
from astrata.config.settings import Settings, load_settings
from astrata.providers.registry import build_default_registry
from astrata.voice.catalog import get_voice_asset, list_voice_assets
from astrata.voice.models import VoiceBackendRecord, VoiceStatus
from astrata.voice.registry import VoiceAssetRegistry


class VoiceService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()

    def status(self) -> dict[str, Any]:
        registry = build_default_registry()
        configured = set(registry.configured_provider_names())
        output_backends = [
            VoiceBackendRecord(
                backend_id="macos-say",
                modality="output",
                locality="local",
                available=bool(shutil.which("say")),
                display_name="macOS Say",
                summary="Built-in local speech output on macOS.",
                reason="Available immediately on macOS when the `say` command is present.",
                install_hint=None,
            ),
            VoiceBackendRecord(
                backend_id="espeak",
                modality="output",
                locality="local",
                available=bool(shutil.which("espeak")),
                display_name="eSpeak",
                summary="Portable local speech output on Linux-class environments.",
                reason="Useful local fallback when built-in OS speech output is unavailable.",
                install_hint="Install `espeak` to enable a lightweight local TTS lane.",
            ),
            VoiceBackendRecord(
                backend_id="openai-realtime-audio",
                modality="duplex",
                locality="cloud",
                available="openai" in configured or "codex" in configured,
                implemented=False,
                display_name="OpenAI Audio",
                summary="Cloud-backed speech input/output lane for richer voice sessions.",
                reason="Provider route is potentially available, but Astrata has not wired this backend yet.",
                install_hint=None,
            ),
            VoiceBackendRecord(
                backend_id="gemini-audio",
                modality="duplex",
                locality="cloud",
                available="google" in configured,
                implemented=False,
                display_name="Gemini Audio",
                summary="Cloud-backed audio lane for speech input/output when a Google route is configured.",
                reason="Provider route is potentially available, but Astrata has not wired this backend yet.",
                install_hint=None,
            ),
        ]
        input_backends = [
            VoiceBackendRecord(
                backend_id="whisper-cli",
                modality="input",
                locality="local",
                available=bool(shutil.which("whisper")),
                display_name="Whisper CLI",
                summary="Local speech-to-text via the Whisper command-line tool.",
                reason="Best immediate local transcription lane if the CLI is installed.",
                install_hint="Install `openai-whisper` to enable local transcription from audio files.",
            ),
            VoiceBackendRecord(
                backend_id="faster-whisper",
                modality="input",
                locality="local",
                available=importlib.util.find_spec("faster_whisper") is not None,
                display_name="faster-whisper",
                summary="Local speech-to-text through a Python package backend.",
                reason="Faster local transcription path when the package is available.",
                install_hint="Install `faster-whisper` to enable local transcription without the Whisper CLI.",
            ),
            VoiceBackendRecord(
                backend_id="openai-realtime-audio",
                modality="duplex",
                locality="cloud",
                available="openai" in configured or "codex" in configured,
                implemented=False,
                display_name="OpenAI Audio",
                summary="Cloud transcription path for speech input.",
                reason="Provider route is potentially available, but Astrata has not wired this backend yet.",
                install_hint=None,
            ),
            VoiceBackendRecord(
                backend_id="gemini-audio",
                modality="duplex",
                locality="cloud",
                available="google" in configured,
                implemented=False,
                display_name="Gemini Audio",
                summary="Cloud transcription path for speech input.",
                reason="Provider route is potentially available, but Astrata has not wired this backend yet.",
                install_hint=None,
            ),
        ]
        status = VoiceStatus(
            output_backends=output_backends,
            input_backends=input_backends,
            recommended_output_backend=self._recommended_backend_id(output_backends),
            recommended_input_backend=self._recommended_backend_id(input_backends),
            recommended_output_models=[
                {
                    "model_id": "kokoro-82m",
                    "display_name": "Kokoro-82M",
                    "role": "default_local_tts",
                    "reason": "Best default local TTS recommendation: tiny, decent quality, and a strong fit for making Astrata feel alive early.",
                },
                {
                    "model_id": "omnivoice",
                    "display_name": "OmniVoice",
                    "role": "advanced_local_tts",
                    "reason": "Recommended advanced TTS option for multilingual and higher-ceiling use cases.",
                },
            ],
            recommended_input_models=[
                {
                    "model_id": "native-chat-audio",
                    "display_name": "Native Audio-Capable Chat Model",
                    "role": "preferred_stt_path",
                    "reason": "If the active chat model already supports audio input, Astrata should prefer that direct path over a separate STT hop.",
                },
                {
                    "model_id": "whisper-tiny",
                    "display_name": "Whisper Tiny",
                    "role": "default_local_stt",
                    "reason": "Recommended default lightweight local STT fallback when the active chat model lacks native audio input.",
                },
                {
                    "model_id": "moonshine",
                    "display_name": "Moonshine",
                    "role": "optional_stt_upgrade",
                    "reason": "Optional heavier STT upgrade path if its quality/latency tradeoff proves worth the footprint.",
                },
                {
                    "model_id": "whisper-family",
                    "display_name": "Whisper Family",
                    "role": "compatibility_local_stt",
                    "reason": "Recommended compatibility fallback family because of its broad tooling and ecosystem support.",
                },
            ],
            preload_defaults=[
                {
                    "model_id": "kokoro-82m",
                    "kind": "tts",
                    "policy": "preload_light_default",
                    "reason": "Small enough to preload as a sane default voice-output capability, then unload later if the user does not want voice.",
                },
                {
                    "model_id": "whisper-tiny",
                    "kind": "stt",
                    "policy": "preload_light_default",
                    "reason": "Small enough to serve as the default local speech-input preload when the active chat model does not natively handle audio.",
                },
                {
                    "model_id": "omnivoice",
                    "kind": "tts",
                    "policy": "optional_upgrade",
                    "reason": "Heavier advanced TTS upgrade path for users who care about multilingual or higher-ceiling voice quality.",
                },
                {
                    "model_id": "moonshine",
                    "kind": "stt",
                    "policy": "optional_upgrade",
                    "reason": "Heavier STT upgrade path retained as optional rather than preloaded by default.",
                },
            ],
            roadmap_notes=[
                "Voice should be local-first when possible, with provider-backed backends as governed fallbacks.",
                "Recommended local TTS defaults are Kokoro-82M for the main path and OmniVoice for the advanced path.",
                "Preferred STT order is native audio-capable chat model first, whisper-tiny as the default local fallback, and broader Whisper-family tooling as the compatibility fallback.",
                "Astrata should preload sane light defaults, unload them if the user does not want the capability, and offer heavier upgrades only when the user cares and the hardware supports them.",
                "Image and video generation should default to chat-first hands-off workflows while preserving expert controls.",
                "On modest hardware, Astrata should be willing to unload chat inference temporarily to run heavier local media models and then restore the chat lane.",
            ],
        )
        return status.model_dump(mode="json")

    def speak(self, text: str, *, voice: str | None = None, output_path: str | None = None) -> dict[str, Any]:
        content = str(text).strip()
        if not content:
            raise ValueError("Cannot speak empty text.")
        if shutil.which("say"):
            return self._speak_with_macos_say(content, voice=voice, output_path=output_path)
        if shutil.which("espeak"):
            return self._speak_with_espeak(content, voice=voice, output_path=output_path)
        raise RuntimeError("No local speech output backend is available.")

    def transcribe(self, audio_path: str, *, model: str | None = None) -> dict[str, Any]:
        path = Path(audio_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Audio file does not exist: {path}")
        if shutil.which("whisper"):
            return self._transcribe_with_whisper_cli(path, model=model)
        raise RuntimeError("No local speech input backend is available. Install `whisper` to enable transcription.")

    def preload_defaults(self) -> dict[str, Any]:
        bootstrap = DependencyBootstrapService(
            state_path=self.settings.paths.data_dir / "dependency_bootstrap.json",
            python_executable=str(Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python")
            if (Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python").exists()
            else None,
            auto_install=True,
        )
        voice_root = self.settings.paths.data_dir / "voice"
        registry = VoiceAssetRegistry(path=self.settings.paths.data_dir / "voice_registry.json")
        installed: list[dict[str, Any]] = []
        for asset in list_voice_assets():
            if asset.role != "preload_light_default":
                continue
            destination = voice_root / asset.asset_id
            bootstrap.ensure_huggingface_snapshot(
                repo_id=asset.repo_id,
                destination_dir=destination,
                allow_patterns=list(asset.allow_patterns) or None,
            )
            size_bytes = _directory_size_bytes(destination)
            registry.record_install(
                asset_id=asset.asset_id,
                repo_id=asset.repo_id,
                kind=asset.kind,
                role=asset.role,
                destination_dir=destination,
                size_bytes=size_bytes,
            )
            installed.append(
                {
                    "asset_id": asset.asset_id,
                    "repo_id": asset.repo_id,
                    "kind": asset.kind,
                    "role": asset.role,
                    "destination_dir": str(destination),
                    "observed_size_bytes": size_bytes,
                }
            )
        return {
            "status": "ok",
            "installed": installed,
            "voice_root": str(voice_root),
            "registry_path": str(self.settings.paths.data_dir / "voice_registry.json"),
        }

    def install_asset(self, asset_id: str) -> dict[str, Any]:
        asset = get_voice_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown voice asset `{asset_id}`.")
        bootstrap = DependencyBootstrapService(
            state_path=self.settings.paths.data_dir / "dependency_bootstrap.json",
            python_executable=str(Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python")
            if (Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python").exists()
            else None,
            auto_install=True,
        )
        destination = self.settings.paths.data_dir / "voice" / asset.asset_id
        bootstrap.ensure_huggingface_snapshot(
            repo_id=asset.repo_id,
            destination_dir=destination,
            allow_patterns=list(asset.allow_patterns) or None,
        )
        size_bytes = _directory_size_bytes(destination)
        VoiceAssetRegistry(path=self.settings.paths.data_dir / "voice_registry.json").record_install(
            asset_id=asset.asset_id,
            repo_id=asset.repo_id,
            kind=asset.kind,
            role=asset.role,
            destination_dir=destination,
            size_bytes=size_bytes,
        )
        return {
            "status": "ok",
            "asset_id": asset.asset_id,
            "repo_id": asset.repo_id,
            "kind": asset.kind,
            "role": asset.role,
            "destination_dir": str(destination),
            "observed_size_bytes": size_bytes,
        }

    def _recommended_backend_id(self, backends: list[VoiceBackendRecord]) -> str | None:
        for backend in backends:
            if backend.available and backend.implemented and backend.locality == "local":
                return backend.backend_id
        for backend in backends:
            if backend.available and backend.implemented:
                return backend.backend_id
        return None

    def _speak_with_macos_say(self, text: str, *, voice: str | None, output_path: str | None) -> dict[str, Any]:
        cmd = ["say"]
        if voice:
            cmd.extend(["-v", voice])
        output = None if not output_path else str(Path(output_path).expanduser().resolve())
        if output:
            cmd.extend(["-o", output])
        cmd.append(text)
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "macOS say failed")
        return {
            "status": "ok",
            "backend": "macos-say",
            "voice": voice,
            "output_path": output,
            "text": text,
        }

    def _speak_with_espeak(self, text: str, *, voice: str | None, output_path: str | None) -> dict[str, Any]:
        cmd = ["espeak"]
        if voice:
            cmd.extend(["-v", voice])
        output = None if not output_path else str(Path(output_path).expanduser().resolve())
        if output:
            cmd.extend(["-w", output])
        cmd.append(text)
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "espeak failed")
        return {
            "status": "ok",
            "backend": "espeak",
            "voice": voice,
            "output_path": output,
            "text": text,
        }

    def _transcribe_with_whisper_cli(self, audio_path: Path, *, model: str | None) -> dict[str, Any]:
        whisper_model = str(model or "turbo").strip() or "turbo"
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            cmd = [
                "whisper",
                str(audio_path),
                "--model",
                whisper_model,
                "--task",
                "transcribe",
                "--output_format",
                "txt",
                "--output_dir",
                str(output_dir),
                "--fp16",
                "False",
            ]
            completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "whisper failed")
            transcript_path = output_dir / f"{audio_path.stem}.txt"
            transcript = transcript_path.read_text(encoding="utf-8").strip() if transcript_path.exists() else ""
            return {
                "status": "ok",
                "backend": "whisper-cli",
                "model": whisper_model,
                "audio_path": str(audio_path),
                "text": transcript,
                "raw": {"stdout": completed.stdout, "stderr": completed.stderr},
            }


def _directory_size_bytes(path: Path) -> int:
    total = 0
    for candidate in path.rglob("*"):
        if candidate.is_file():
            total += candidate.stat().st_size
    return total
