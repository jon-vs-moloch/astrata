from astrata.local.catalog import StarterCatalog
from astrata.config.settings import load_settings


def test_starter_catalog_exposes_installable_tiny_qwen_entries():
    catalog = StarterCatalog()
    tiny = catalog.get_model("qwen3.5-0.8b-q4_k_m")
    assert tiny is not None
    assert tiny.download_url is not None
    assert "tiny" in tiny.tags
    installable_ids = {model.catalog_id for model in catalog.list_installable_models()}
    assert "qwen3.5-0.8b-q4_k_m" in installable_ids
    assert "qwen3-0.6b-q8_0" in installable_ids


def test_settings_include_managed_install_dir_in_search_paths():
    settings = load_settings()
    assert str(settings.local_runtime.model_install_dir) in settings.local_runtime.model_search_paths
