"""Provider registry for broad inference access in Phase 0."""

from __future__ import annotations

from typing import Any

from astrata.config.secrets import SecretStore
from astrata.config.settings import load_settings
from astrata.providers.base import Provider
from astrata.providers.cli import CliProvider
from astrata.providers.codex_direct import CodexDirectProvider
from astrata.providers.google_ai_studio import GoogleAiStudioProvider
from astrata.providers.http_openai_compatible import OpenAICompatibleProvider
from astrata.providers.model_catalog import ModelCatalogRecord, catalog_record
from astrata.providers.sources import (
    CLI_SOURCE_PROVIDERS,
    source_display_name,
    source_id,
    source_underlying_provider,
)


class ProviderRegistry:
    def __init__(self, providers: dict[str, Provider] | None = None) -> None:
        self._providers: dict[str, Provider] = providers or {}

    def register(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    def get_provider(self, name: str | None = None) -> Provider | None:
        if name:
            provider = self._providers.get(name)
            return provider if provider and provider.is_configured() else None
        for candidate in ("cli", "openai", "openrouter", "kilo-gateway", "google", "anthropic", "pollinations", "ollama", "custom"):
            provider = self._providers.get(candidate)
            if provider and provider.is_configured():
                return provider
        return None

    def list_available_providers(self) -> list[dict[str, Any]]:
        return [provider.describe() for provider in self._providers.values()]

    def list_available_inference_sources(self) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for provider_name, provider in self._providers.items():
            if provider_name != "cli":
                sid = source_id(provider_name)
                if sid and sid not in seen:
                    seen.add(sid)
                    sources.append(
                        {
                            "id": sid,
                            "kind": "provider",
                            "provider": provider_name,
                            "cli_tool": None,
                            "underlying_provider": source_underlying_provider(provider_name),
                            "display_name": source_display_name(provider_name),
                            "is_configured": provider.is_configured(),
                            "default_model": provider.default_model(),
                        }
                    )
                continue
            for cli_tool in CLI_SOURCE_PROVIDERS:
                sid = source_id("cli", cli_tool)
                if sid in seen:
                    continue
                seen.add(sid)
                sources.append(
                    {
                        "id": sid,
                        "kind": "cli",
                        "provider": "cli",
                        "cli_tool": cli_tool,
                        "underlying_provider": source_underlying_provider("cli", cli_tool),
                        "display_name": source_display_name("cli", cli_tool),
                        "is_configured": provider.is_configured(),
                        "default_model": provider.default_model(),
                    }
                )
        return sources

    def list_model_catalog(self) -> list[dict[str, Any]]:
        records: list[ModelCatalogRecord] = []
        seen: set[str] = set()
        for provider in self._providers.values():
            for record in provider.list_model_catalog():
                if record.catalog_id in seen:
                    continue
                seen.add(record.catalog_id)
                records.append(record)
        for record in _static_source_catalog():
            if record.catalog_id in seen:
                continue
            seen.add(record.catalog_id)
            records.append(record)
        return [record.model_dump(mode="json") for record in sorted(records, key=lambda item: (item.provider_id, item.display_name))]

    def configured_provider_names(self) -> list[str]:
        ordered: list[str] = []
        preferred = (
            "codex",
            "cli",
            "openai",
            "openrouter",
            "kilo-gateway",
            "google",
            "anthropic",
            "pollinations",
            "ollama",
            "custom",
        )
        for candidate in preferred:
            provider = self._providers.get(candidate)
            if provider and provider.is_configured():
                ordered.append(candidate)
        for name, provider in self._providers.items():
            if name in ordered:
                continue
            if provider.is_configured():
                ordered.append(name)
        return ordered

    def configured_cli_tools(self) -> list[str]:
        provider = self._providers.get("cli")
        if provider is None or not hasattr(provider, "available_tools"):
            return []
        return list(provider.available_tools())


def build_default_registry() -> ProviderRegistry:
    settings = load_settings()
    secrets = SecretStore(path=settings.paths.provider_secrets_path)
    registry = ProviderRegistry()
    registry.register(CodexDirectProvider(name="codex"))
    registry.register(CliProvider(name="cli"))
    registry.register(
        OpenAICompatibleProvider(
            name="openai",
            endpoint_env="ASTRATA_OPENAI_ENDPOINT",
            api_key_env="ASTRATA_OPENAI_API_KEY",
            model_env="ASTRATA_OPENAI_MODEL",
            default_endpoint="https://api.openai.com/v1/chat/completions",
        )
    )
    registry.register(
        OpenAICompatibleProvider(
            name="openrouter",
            endpoint_env="ASTRATA_OPENROUTER_ENDPOINT",
            api_key_env="ASTRATA_OPENROUTER_API_KEY",
            model_env="ASTRATA_OPENROUTER_MODEL",
            default_endpoint="https://openrouter.ai/api/v1/chat/completions",
            models_endpoint="https://openrouter.ai/api/v1/models",
            capabilities=["chat", "vision"],
            input_modalities=["text", "image", "video", "file"],
            output_modalities=["text", "audio"],
        )
    )
    registry.register(
        OpenAICompatibleProvider(
            name="kilo-gateway",
            endpoint_env="ASTRATA_KILO_GATEWAY_ENDPOINT",
            api_key_env="ASTRATA_KILO_API_KEY",
            model_env="ASTRATA_KILO_GATEWAY_MODEL",
            default_endpoint="https://api.kilo.ai/api/gateway/chat/completions",
            models_endpoint="https://api.kilo.ai/api/gateway/models",
            capabilities=["chat", "tool_use"],
            input_modalities=["text", "image"],
            output_modalities=["text"],
        )
    )
    registry.register(
        GoogleAiStudioProvider(
            name="google",
            api_key=secrets.get_provider_secret("google", "api_key"),
            default_model=secrets.get_provider_secret("google", "default_model"),
            catalog_path=settings.paths.data_dir / "google_models.json",
            quota_state_path=settings.paths.data_dir / "google_quota_state.json",
        )
    )
    registry.register(
        OpenAICompatibleProvider(
            name="anthropic",
            endpoint_env="ASTRATA_ANTHROPIC_ENDPOINT",
            api_key_env="ASTRATA_ANTHROPIC_API_KEY",
            model_env="ASTRATA_ANTHROPIC_MODEL",
            default_endpoint=None,
        )
    )
    registry.register(
        OpenAICompatibleProvider(
            name="ollama",
            endpoint_env="ASTRATA_OLLAMA_ENDPOINT",
            api_key_env=None,
            model_env="ASTRATA_OLLAMA_MODEL",
            default_endpoint="http://127.0.0.1:11434/v1/chat/completions",
        )
    )
    registry.register(
        OpenAICompatibleProvider(
            name="pollinations",
            endpoint_env="ASTRATA_POLLINATIONS_TEXT_ENDPOINT",
            api_key_env=None,
            model_env="ASTRATA_POLLINATIONS_MODEL",
            default_endpoint="https://text.pollinations.ai/openai",
            models_endpoint="https://text.pollinations.ai/models",
            capabilities=["chat", "text", "image", "video", "audio"],
            input_modalities=["text", "image"],
            output_modalities=["text", "image", "video", "audio"],
        )
    )
    registry.register(
        OpenAICompatibleProvider(
            name="custom",
            endpoint_env="ASTRATA_CUSTOM_ENDPOINT",
            api_key_env="ASTRATA_CUSTOM_API_KEY",
            model_env="ASTRATA_CUSTOM_MODEL",
            default_endpoint=None,
        )
    )
    return registry


def _static_source_catalog() -> list[ModelCatalogRecord]:
    return [
        catalog_record(
            provider_id="vibeduel",
            model_id="arena",
            display_name="VibeDuel Arena",
            capabilities=["chat"],  # type: ignore[list-item]
            status="research_required",
            source="vibeduel_site",
            notes=(
                "VibeDuel appears to be CLI/arena-first with a voting-for-credits mechanic. "
                "Astrata should only enable this source once a non-voting or explicitly user-approved mode is verified."
            ),
        )
    ]
