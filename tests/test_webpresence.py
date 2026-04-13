from pathlib import Path

from fastapi.testclient import TestClient

from astrata.config.settings import AstrataPaths, LocalRuntimeSettings, RuntimeLimits, Settings
from astrata.voice.registry import VoiceAssetRegistry
from astrata.webpresence.server import create_app
from astrata.webpresence.service import WebPresenceService


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


def test_webpresence_service_exposes_registries(tmp_path: Path):
    settings = _settings(tmp_path)
    VoiceAssetRegistry(path=settings.paths.data_dir / "voice_registry.json").record_install(
        asset_id="whisper-tiny",
        repo_id="Systran/faster-whisper-tiny",
        kind="stt",
        role="preload_light_default",
        destination_dir=settings.paths.data_dir / "voice" / "whisper-tiny",
        size_bytes=123,
    )
    service = WebPresenceService(settings=settings)

    capabilities = service.capabilities()
    model_registry = service.model_registry()
    voice_registry = service.voice_registry()

    assert capabilities["services"]["public_registry_api"] is True
    assert capabilities["services"]["publish_to_internet"] is True
    assert capabilities["services"]["account_auth_control_plane"] == "in_progress"
    assert capabilities["access_policy"]["public_access"]["download"] is True
    assert capabilities["auth_control_plane"]["current_bootstrap"]["invite_required_for_hosted_bridge"] is True
    assert model_registry["local_model_catalog"]
    assert "whisper-tiny" in voice_registry["assets"]


def test_webpresence_server_routes(tmp_path: Path):
    settings = _settings(tmp_path)
    app = create_app(service=WebPresenceService(settings=settings))
    client = TestClient(app)

    assert client.get("/api/health").json()["ok"] is True
    assert "providers" in client.get("/api/provider-registry").json()
    assert "local_model_catalog" in client.get("/api/model-registry").json()
    downloads = client.get("/api/downloads").json()
    distribution = client.get("/api/distribution").json()
    updates = client.get("/api/updates/tester").json()
    edge_updates = client.get("/api/updates/edge").json()
    assert "download_channels" in downloads
    assert downloads["download_channels"][1]["status"] == "live"
    assert downloads["hosted_bridge_activation"]["invite_required"] is True
    assert all(channel["invite_required"] is False for channel in downloads["download_channels"])
    assert distribution["product"]["distribution_status"] == "live"
    assert distribution["topology"]["site"]["provider"] == "cloudflare_pages"
    assert distribution["topology"]["artifact_storage"]["provider"] == "cloudflare_r2"
    assert [channel["channel_id"] for channel in distribution["channels"]][:2] == ["edge", "nightly"]
    assert updates["channel"] == "tester"
    assert updates["status"] == "live"
    assert updates["invite_required"] is True
    assert updates["cadence"] == "manual_promote"
    assert edge_updates["channel"] == "edge"
    assert edge_updates["status"] == "live"
    assert edge_updates["cadence"] == "every_build"
