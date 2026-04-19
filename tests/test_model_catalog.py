from astrata.providers.registry import ProviderRegistry
from astrata.providers.http_openai_compatible import OpenAICompatibleProvider


def test_provider_registry_exposes_multimodal_model_catalog():
    registry = ProviderRegistry(
        {
            "openrouter": OpenAICompatibleProvider(
                name="openrouter",
                endpoint_env="MISSING_ENDPOINT",
                api_key_env="MISSING_KEY",
                model_env="MISSING_MODEL",
                default_endpoint="https://openrouter.ai/api/v1/chat/completions",
                models_endpoint="https://openrouter.ai/api/v1/models",
                capabilities=["chat", "vision"],
                input_modalities=["text", "image", "video"],
                output_modalities=["text"],
            ),
            "kilo-gateway": OpenAICompatibleProvider(
                name="kilo-gateway",
                endpoint_env="MISSING_ENDPOINT",
                api_key_env="MISSING_KEY",
                model_env="MISSING_MODEL",
                default_endpoint="https://api.kilo.ai/api/gateway/chat/completions",
                models_endpoint="https://api.kilo.ai/api/gateway/models",
            ),
            "pollinations": OpenAICompatibleProvider(
                name="pollinations",
                endpoint_env="MISSING_ENDPOINT",
                api_key_env=None,
                model_env="MISSING_MODEL",
                default_endpoint="https://text.pollinations.ai/openai",
                models_endpoint="https://text.pollinations.ai/models",
                capabilities=["chat", "image", "video"],
                output_modalities=["text", "image", "video"],
            ),
        }
    )

    catalog = registry.list_model_catalog()
    ids = {item["catalog_id"] for item in catalog}

    assert "kilo-gateway:kilo-auto/frontier" in ids
    assert "pollinations:flux" in ids
    assert "pollinations:seedance" in ids
    assert "vibeduel:arena" in ids
    assert any("video" in item["output_modalities"] for item in catalog)
