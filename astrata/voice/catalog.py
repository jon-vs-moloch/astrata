"""Curated local voice asset recommendations for Astrata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceAssetRecord:
    asset_id: str
    repo_id: str
    kind: str
    role: str
    display_name: str
    allow_patterns: tuple[str, ...] = ()
    notes: str = ""


VOICE_ASSETS: tuple[VoiceAssetRecord, ...] = (
    VoiceAssetRecord(
        asset_id="kokoro-82m",
        repo_id="onnx-community/Kokoro-82M-v1.0-ONNX",
        kind="tts",
        role="preload_light_default",
        display_name="Kokoro-82M",
        allow_patterns=(
            "onnx/model_q8f16.onnx",
            "voices/af_bella.bin",
            "voices/am_michael.bin",
        ),
        notes="Lightweight default local TTS preload set using the quantized ONNX build plus one female and one male voice.",
    ),
    VoiceAssetRecord(
        asset_id="whisper-tiny",
        repo_id="Systran/faster-whisper-tiny",
        kind="stt",
        role="preload_light_default",
        display_name="Whisper Tiny",
        allow_patterns=(),
        notes="Default lightweight local STT preload set using the compact faster-whisper tiny model.",
    ),
    VoiceAssetRecord(
        asset_id="omnivoice",
        repo_id="k2-fsa/OmniVoice",
        kind="tts",
        role="optional_upgrade",
        display_name="OmniVoice",
        allow_patterns=(),
        notes="Advanced multilingual TTS upgrade path.",
    ),
    VoiceAssetRecord(
        asset_id="moonshine",
        repo_id="UsefulSensors/moonshine",
        kind="stt",
        role="optional_upgrade",
        display_name="Moonshine",
        allow_patterns=(),
        notes="Experimental heavier STT upgrade path; not a default preload because of its observed footprint.",
    ),
)


def get_voice_asset(asset_id: str) -> VoiceAssetRecord | None:
    normalized = str(asset_id or "").strip().lower()
    for asset in VOICE_ASSETS:
        if asset.asset_id == normalized:
            return asset
    return None


def list_voice_assets() -> list[VoiceAssetRecord]:
    return list(VOICE_ASSETS)
