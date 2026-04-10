"""Quota-aware coordinator for minimal federated control."""

from __future__ import annotations

from typing import Any

from astrata.controllers.base import ControllerDecision, ControllerEnvelope
from astrata.routing.prime_policy import classify_work_policy, route_uses_prime
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
        preferred_model = str(envelope.metadata.get("preferred_model") or "").strip() or None
        prime_quota = self._quota_policy.assess({"provider": "codex", "model": preferred_model})
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
        avoided_cli_tools = tuple(
            str(item).strip().lower()
            for item in list(envelope.metadata.get("avoided_cli_tools") or [])
            if str(item).strip()
        )
        task_class = str(envelope.metadata.get("task_class") or "general").strip() or "general"
        work_policy = classify_work_policy(
            task_class=task_class,
            risk=envelope.risk,
            metadata={
                **dict(envelope.metadata),
                "priority": envelope.priority,
                "urgency": envelope.urgency,
                "prime_budget_healthy": prime_quota.allowed,
                "prime_budget_abundant": self._prime_budget_abundant(prime_quota.active_window),
            },
        )
        if self._route_advisor is not None and not preferred_providers and not preferred_cli_tools:
            advice = self._route_advisor.advise(task_class=task_class)
            if envelope.risk in {"high", "critical"} or advice.preferred_providers != ("codex",):
                preferred_providers = advice.preferred_providers
            preferred_cli_tools = advice.preferred_cli_tools
        require_prime_route = bool(envelope.metadata.get("require_prime_route"))
        if work_policy.get("prefer_prime") and "codex" not in preferred_providers:
            preferred_providers = tuple(dict.fromkeys(["codex", *preferred_providers]))
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
        decision = self._decision_for_route(envelope, route, work_policy=work_policy)
        return decision, route

    def _decision_for_route(
        self,
        envelope: ControllerEnvelope,
        route: ExecutionRoute,
        *,
        work_policy: dict[str, Any],
    ) -> ControllerDecision:
        quota = self._quota_policy.assess(route.__dict__)
        followup_actions: list[dict[str, Any]] = []
        if work_policy.get("prime_admission_basis"):
            followup_actions.append(
                {
                    "type": "prime_admission_basis",
                    "basis": list(work_policy.get("prime_admission_basis") or []),
                    "reason": "Prime should be invoked only when it is safer, cheaper overall, or quota-backed for course correction.",
                }
            )
        if work_policy.get("consensus_eligible"):
            followup_actions.append(
                {
                    "type": "consensus_approval_eligible",
                    "required_reviews": 2,
                    "reason": "Low-risk bounded review/audit work may be settled by competent non-prime workers before Prime is consulted.",
                }
            )
        if work_policy.get("batchable"):
            followup_actions.append(
                {
                    "type": "batch_if_non_urgent",
                    "task_class": work_policy.get("task_class"),
                    "reason": "This low-risk work is eligible for batch handling instead of immediate Prime attention.",
                }
            )
        if route_uses_prime(route.__dict__) and (work_policy.get("consensus_eligible") or work_policy.get("cheap_first")):
            followup_actions.append(
                {
                    "type": "prime_usage_review",
                    "reason": "Prime was selected for work that should be reviewed for cheaper viable routing.",
                }
            )
        if quota.allowed:
            return ControllerDecision(
                status="accepted",
                reason=f"Route {route.provider} is available within quota policy.",
                followup_actions=[
                    {
                        "type": "route_selected",
                        "route": route.__dict__,
                    },
                    *followup_actions,
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
                    *followup_actions,
                ],
            )
        return ControllerDecision(
            status="deferred",
            reason=f"Route {route.provider} is temporarily pacing due to quota policy.",
            followup_actions=[
                {
                    "type": "retry_after",
                    "next_allowed_at": quota.next_allowed_at,
                },
                *followup_actions,
            ],
        )

    def _prime_budget_abundant(self, window: dict[str, Any] | None) -> bool:
        payload = dict(window or {})
        try:
            remaining = int(payload.get("requests_remaining") or 0)
            limit = int(payload.get("requests_limit") or 0)
        except Exception:
            return False
        if limit <= 0:
            return False
        return remaining >= max(3, limit // 5)
