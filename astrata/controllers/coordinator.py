"""Quota-aware coordinator for minimal federated control."""

from __future__ import annotations

from typing import Any

from astrata.controllers.base import ControllerDecision, ControllerEnvelope
from astrata.routing.advisor import RoutePerformanceAdvisor
from astrata.routing.policy import ExecutionRoute, RouteChooser
from astrata.scheduling.quota import QuotaPolicy


class CoordinatorController:
    def __init__(
        self,
        *,
        router: RouteChooser,
        quota_policy: QuotaPolicy,
        route_advisor: RoutePerformanceAdvisor | None = None,
    ) -> None:
        self._router = router
        self._quota_policy = quota_policy
        self._route_advisor = route_advisor

    def coordinate(self, envelope: ControllerEnvelope) -> tuple[ControllerDecision, ExecutionRoute]:
        preferred_providers = tuple(
            str(item).strip().lower()
            for item in list(envelope.metadata.get("preferred_providers") or [])
            if str(item).strip()
        )
        avoided_providers = tuple(
            str(item).strip().lower()
            for item in list(envelope.metadata.get("avoided_providers") or [])
            if str(item).strip()
        )
        preferred_cli_tools = tuple(
            str(item).strip().lower()
            for item in list(envelope.metadata.get("preferred_cli_tools") or [])
            if str(item).strip()
        )
        preferred_model = str(envelope.metadata.get("preferred_model") or "").strip() or None
        avoided_cli_tools = tuple(
            str(item).strip().lower()
            for item in list(envelope.metadata.get("avoided_cli_tools") or [])
            if str(item).strip()
        )
        task_class = str(envelope.metadata.get("task_class") or "general").strip() or "general"
        if self._route_advisor is not None and not preferred_providers and not preferred_cli_tools:
            advice = self._route_advisor.advise(task_class=task_class)
            if envelope.risk in {"high", "critical"} or advice.preferred_providers != ("codex",):
                preferred_providers = advice.preferred_providers
            preferred_cli_tools = advice.preferred_cli_tools
        require_prime_route = bool(envelope.metadata.get("require_prime_route"))
        if (
            not require_prime_route
            and envelope.risk not in {"high", "critical"}
            and task_class in {"coding", "general", "review"}
            and "codex" not in preferred_providers
        ):
            avoided_providers = tuple(dict.fromkeys([*avoided_providers, "codex"]))
            avoided_cli_tools = tuple(dict.fromkeys([*avoided_cli_tools, "codex-cli"]))
        try:
            route = self._router.choose(
                priority=envelope.priority,
                urgency=envelope.urgency,
                risk=envelope.risk,
                task_class=task_class,
                prefer_local=False,
                preferred_model=preferred_model,
                preferred_providers=preferred_providers,
                avoided_providers=avoided_providers,
                preferred_cli_tools=preferred_cli_tools,
                avoided_cli_tools=avoided_cli_tools,
            )
        except RuntimeError:
            route = self._router.choose(
                priority=envelope.priority,
                urgency=envelope.urgency,
                risk=envelope.risk,
                task_class=task_class,
                prefer_local=False,
                preferred_model=preferred_model,
            )
        decision = self._decision_for_route(envelope, route)
        return decision, route

    def _decision_for_route(
        self,
        envelope: ControllerEnvelope,
        route: ExecutionRoute,
    ) -> ControllerDecision:
        quota = self._quota_policy.assess(route.__dict__)
        if quota.allowed:
            return ControllerDecision(
                status="accepted",
                reason=f"Route {route.provider} is available within quota policy.",
                followup_actions=[
                    {
                        "type": "route_selected",
                        "route": route.__dict__,
                    }
                ],
            )
        source = quota.active_window.get("source") if quota.active_window else None
        if route.provider == "codex":
            return ControllerDecision(
                status="deferred",
                reason=(
                    f"Prime route {route.provider} is pacing against the most constraining quota window"
                    + (f" ({source})" if source else "")
                    + "."
                ),
                followup_actions=[
                    {
                        "type": "retry_after",
                        "next_allowed_at": quota.next_allowed_at,
                    },
                    {
                        "type": "quota_window",
                        "window": quota.active_window or {},
                    },
                    {
                        "type": "preserve_agent_identity",
                        "route": route.__dict__,
                        "task_id": envelope.task_id,
                    },
                ],
            )
        return ControllerDecision(
            status="deferred",
            reason=f"Route {route.provider} is temporarily pacing due to quota policy.",
            followup_actions=[
                {
                    "type": "retry_after",
                    "next_allowed_at": quota.next_allowed_at,
                }
            ],
        )
