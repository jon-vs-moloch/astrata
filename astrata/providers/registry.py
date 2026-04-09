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
        for candidate in ("cli", "openai", "google", "anthropic", "ollama", "custom"):
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

    def configured_provider_names(self) -> list[str]:
        ordered: list[str] = []
        preferred = ("codex", "cli", "openai", "google", "anthropic", "ollama", "custom")
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
            name="custom",
            endpoint_env="ASTRATA_CUSTOM_ENDPOINT",
            api_key_env="ASTRATA_CUSTOM_API_KEY",
            model_env="ASTRATA_CUSTOM_MODEL",
            default_endpoint=None,
        )
    )
    return registry
