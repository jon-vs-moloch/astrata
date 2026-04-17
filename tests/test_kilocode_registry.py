from pathlib import Path

from astrata.providers.kilocode_registry import KiloCodeModelRegistry, recommended_kilocode_model


def test_kilocode_registry_prefers_optimized_grok_code_model():
    models = [
        "kilo/kilo-auto/free",
        "kilo/x-ai/grok-code-fast-1:optimized:free",
    ]

    assert recommended_kilocode_model(models) == "kilo/x-ai/grok-code-fast-1:optimized:free"


def test_kilocode_registry_sync_records_recommendation(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "astrata.providers.kilocode_registry.list_kilocode_models_from_cli",
        lambda: ["kilo/kilo-auto/free", "kilo/x-ai/grok-code-fast-1:optimized:free"],
    )
    registry = KiloCodeModelRegistry(state_path=tmp_path / "kilocode_models.json")

    payload = registry.sync()

    assert payload["status"] == "synced"
    assert payload["recommended_default_model"] == "kilo/x-ai/grok-code-fast-1:optimized:free"
    assert registry.cached()["models"] == payload["models"]

