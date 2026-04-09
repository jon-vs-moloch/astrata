import json
from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.config.secrets import SecretStore
from astrata.providers.google_ai_studio import GoogleAiStudioProvider


def test_secret_store_round_trips_provider_secret():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "provider_secrets.json"
        store = SecretStore(path=path)
        store.set_provider_secret("google", "api_key", "demo-key")
        assert store.get_provider_secret("google", "api_key") == "demo-key"


def test_google_provider_describe_uses_cached_catalog():
    with TemporaryDirectory() as tmp:
        catalog_path = Path(tmp) / "google_models.json"
        quota_path = Path(tmp) / "google_quota_state.json"
        catalog_path.write_text(
            json.dumps(
                {
                    "last_synced_at": "2026-04-08T00:00:00+00:00",
                    "models": [{"model_id": "gemini-2.5-flash"}],
                }
            ),
            encoding="utf-8",
        )
        provider = GoogleAiStudioProvider(
            api_key="demo-key",
            catalog_path=catalog_path,
            quota_state_path=quota_path,
        )
        payload = provider.describe()
        assert payload["is_configured"] is True
        assert payload["cached_model_count"] == 1
        assert payload["last_model_sync_at"] == "2026-04-08T00:00:00+00:00"


def test_google_provider_returns_observed_cooldown_window_for_active_model():
    with TemporaryDirectory() as tmp:
        catalog_path = Path(tmp) / "google_models.json"
        quota_path = Path(tmp) / "google_quota_state.json"
        quota_path.write_text(
            json.dumps(
                {
                    "cooldowns": [
                        {
                            "model": "gemini-2.5-flash",
                            "reset_time": "2099-01-01T00:00:00+00:00",
                            "source": "google_http_429",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        provider = GoogleAiStudioProvider(
            api_key="demo-key",
            default_model="gemini-2.5-flash",
            catalog_path=catalog_path,
            quota_state_path=quota_path,
        )
        windows = provider.get_quota_windows({"provider": "google", "model": "gemini-2.5-flash"})
        assert windows is not None
        assert len(windows) == 1
        assert windows[0]["requests_remaining"] == 0
        assert windows[0]["source"] == "google_http_429"
