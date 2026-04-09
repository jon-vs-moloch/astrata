"""Downstream controller for bounded local execution handoffs."""

from __future__ import annotations

from typing import Any

from astrata.controllers.base import ControllerDecision
from astrata.procedures.health import RouteHealthStore
from astrata.providers.registry import ProviderRegistry
from astrata.records.handoffs import HandoffRecord


class LocalExecutorController:
    """Owns the final go/no-go decision for local execution handoffs."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        health_store: RouteHealthStore,
    ) -> None:
        self._registry = registry
        self._health_store = health_store

    def evaluate_handoff(self, handoff: HandoffRecord) -> ControllerDecision:
        route = dict(handoff.route or {})
        envelope = dict(handoff.envelope or {})
        if not route:
            return ControllerDecision(
                status="refused",
                reason="Local executor received a handoff without a concrete route.",
                followup_actions=[{"type": "missing_route", "task_id": handoff.task_id}],
            )
        provider_name = str(route.get("provider") or "").strip()
        provider = self._registry.get_provider(provider_name)
        if provider is None:
            return ControllerDecision(
                status="blocked",
                reason=f"Local executor cannot use unavailable provider `{provider_name or 'unknown'}`.",
                followup_actions=[{"type": "provider_unavailable", "route": route}],
            )
        preflight = self._preflight(provider, route)
        if not preflight["ok"]:
            return ControllerDecision(
                status="blocked",
                reason=f"Local executor preflight failed: {preflight['reason']}.",
                followup_actions=[{"type": "preflight_failed", "route": route, "preflight": preflight}],
            )
        health = self._health_store.assess(route)
        risk = str(envelope.get("risk") or "moderate").strip().lower()
        if health["status"] == "broken":
            return ControllerDecision(
                status="blocked",
                reason="Local executor is refusing a route already marked broken by route health memory.",
                followup_actions=[{"type": "route_broken", "route": route, "health": health}],
            )
        if health["status"] == "degraded" and risk in {"moderate", "high", "critical"}:
            return ControllerDecision(
                status="deferred",
                reason="Local executor is deferring a degraded route for non-trivial risk work.",
                followup_actions=[{"type": "route_degraded", "route": route, "health": health}],
            )
        return ControllerDecision(
            status="accepted",
            reason="Local executor accepted the handoff and confirmed the route is viable enough to try.",
            followup_actions=[
                {"type": "route_confirmed", "route": route},
                {"type": "health_snapshot", "health": health},
            ],
        )

    def _preflight(self, provider: Any, route: dict[str, Any]) -> dict[str, Any]:
        provider_name = str(route.get("provider") or "").strip()
        if not provider.is_configured():
            return {"ok": False, "reason": "provider_not_configured"}
        if provider_name == "cli":
            cli_tool = str(route.get("cli_tool") or "").strip().lower()
            available_tools = list(getattr(provider, "available_tools", lambda: [])())
            if not cli_tool:
                return {"ok": False, "reason": "cli_tool_missing"}
            if cli_tool not in available_tools:
                return {"ok": False, "reason": "cli_tool_unavailable"}
        return {"ok": True, "reason": None}
