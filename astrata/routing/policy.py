"""Minimal continuous-variable route chooser for Phase 0."""

from __future__ import annotations

from dataclasses import dataclass

from astrata.providers.registry import ProviderRegistry


@dataclass(frozen=True)
class ExecutionRoute:
    provider: str
    model: str | None
    cli_tool: str | None
    reason: str


class RouteChooser:
    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def choose(
        self,
        *,
        priority: int,
        urgency: int,
        risk: str,
        prefer_local: bool = False,
        preferred_providers: tuple[str, ...] = (),
        avoided_providers: tuple[str, ...] = (),
        preferred_cli_tools: tuple[str, ...] = (),
        avoided_cli_tools: tuple[str, ...] = (),
    ) -> ExecutionRoute:
        avoided = {provider.strip().lower() for provider in avoided_providers if provider}
        preferred = tuple(provider.strip().lower() for provider in preferred_providers if provider)
        avoided_tools = {tool.strip().lower() for tool in avoided_cli_tools if tool}
        preferred_tools = tuple(tool.strip().lower() for tool in preferred_cli_tools if tool)

        def _pick_cli(candidates: tuple[str, ...], *, reason: str) -> ExecutionRoute | None:
            if "cli" in avoided:
                return None
            available = set(self._registry.configured_cli_tools())
            for tool in candidates:
                if tool in avoided_tools or tool not in available:
                    continue
                provider = self._registry.get_provider("cli")
                if provider:
                    return ExecutionRoute(
                        provider="cli",
                        model=None,
                        cli_tool=tool,
                        reason=reason,
                    )
            return None

        def _pick(candidates: tuple[str, ...]) -> ExecutionRoute | None:
            for candidate in candidates:
                if candidate in avoided:
                    continue
                provider = self._registry.get_provider(candidate)
                if provider:
                    return ExecutionRoute(
                        provider=provider.name,
                        model=provider.default_model(),
                        cli_tool=None,
                        reason="policy_selected_provider",
                    )
            return None

        if preferred_tools:
            route = _pick_cli(preferred_tools, reason="preferred_cli_tool")
            if route:
                return route
        if preferred:
            route = _pick(preferred)
            if route:
                return ExecutionRoute(
                    provider=route.provider,
                    model=route.model,
                    cli_tool=route.cli_tool,
                    reason="preferred_provider",
                )
        if prefer_local:
            route = _pick(("ollama", "custom", "cli"))
            if route:
                return ExecutionRoute(
                    provider=route.provider,
                    model=route.model,
                    cli_tool=route.cli_tool,
                    reason="prefer_local",
                )
        route = _pick(("codex",))
        if route:
            return ExecutionRoute(
                provider=route.provider,
                model=route.model,
                cli_tool=route.cli_tool,
                reason="prime_prefers_codex_direct",
            )
        route = _pick_cli(("codex-cli",), reason="prime_prefers_codex")
        if route:
            return route
        if risk in {"high", "critical"}:
            route = _pick(("openai", "google", "anthropic", "cli"))
            if route:
                return ExecutionRoute(
                    provider=route.provider,
                    model=route.model,
                    cli_tool=route.cli_tool,
                    reason="high_risk_prefers_stronger_inference",
                )
            route = _pick_cli(("gemini-cli", "claude-code", "kilocode"), reason="high_risk_cli_backup")
            if route:
                return route
        if urgency > priority:
            route = _pick_cli(("codex-cli", "kilocode", "gemini-cli", "claude-code"), reason="urgency_prefers_cli")
            if route:
                return route
            route = _pick(("cli",))
            if route:
                return ExecutionRoute(
                    provider=route.provider,
                    model=route.model,
                    cli_tool=route.cli_tool,
                    reason="urgency_prefers_fastest_available",
                )
            provider = self._registry.get_provider()
            if provider and provider.name.lower() not in avoided:
                return ExecutionRoute(
                    provider=provider.name,
                    model=provider.default_model(),
                    cli_tool=None,
                    reason="urgency_prefers_fastest_available",
                )
        route = _pick_cli(("kilocode", "gemini-cli", "claude-code"), reason="assistant_cli_backup")
        if route:
            return route
        route = _pick(tuple(self._registry.configured_provider_names()))
        if route:
            return ExecutionRoute(
                provider=route.provider,
                model=route.model,
                cli_tool=route.cli_tool,
                reason="default_available_provider",
            )
        provider = self._registry.get_provider()
        if not provider or provider.name.lower() in avoided:
            raise RuntimeError("No configured inference provider is available")
        return ExecutionRoute(
            provider=provider.name,
            model=provider.default_model(),
            cli_tool=None,
            reason="default_available_provider",
        )
