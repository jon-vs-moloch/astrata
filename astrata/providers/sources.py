"""Canonical inference-source helpers."""

from __future__ import annotations


CLI_SOURCE_PROVIDERS: dict[str, str] = {
    "codex-cli": "openai",
    "gemini-cli": "google",
    "claude-code": "anthropic",
    "kilocode": "kilo-gateway",
    "vibeduel": "vibeduel",
}


def source_underlying_provider(provider: str | None, cli_tool: str | None = None) -> str | None:
    provider_name = str(provider or "").strip().lower() or None
    cli_name = str(cli_tool or "").strip().lower() or None
    if provider_name == "cli" and cli_name:
        return CLI_SOURCE_PROVIDERS.get(cli_name) or "cli"
    if provider_name == "codex":
        return "openai"
    return provider_name


def source_id(provider: str | None, cli_tool: str | None = None) -> str | None:
    provider_name = str(provider or "").strip().lower() or None
    cli_name = str(cli_tool or "").strip().lower() or None
    if provider_name == "cli" and cli_name:
        return cli_name
    return provider_name


def source_display_name(provider: str | None, cli_tool: str | None = None) -> str:
    sid = source_id(provider, cli_tool)
    if not sid:
        return "Unknown Source"
    parts = sid.replace("-", " ").split()
    return " ".join(part.upper() if part.lower() == "cli" else part.capitalize() for part in parts)
