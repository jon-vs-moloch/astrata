"""Helpers for minimizing Prime burden and promoting cheap-lane consensus."""

from __future__ import annotations

from typing import Any


def route_uses_prime(route: dict[str, Any] | None) -> bool:
    payload = dict(route or {})
    provider = str(payload.get("provider") or "").strip().lower()
    cli_tool = str(payload.get("cli_tool") or "").strip().lower()
    return provider == "codex" or cli_tool == "codex-cli"


def classify_work_policy(
    *,
    task_class: str,
    risk: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(metadata or {})
    normalized_task_class = str(task_class or "general").strip().lower() or "general"
    normalized_risk = str(risk or "moderate").strip().lower() or "moderate"
    completion_type = str(payload.get("completion_type") or "").strip().lower()
    require_prime_route = bool(payload.get("require_prime_route"))
    governance_authorized = bool(payload.get("governance_update_authorized"))
    protected_governance = bool(payload.get("protected_governance"))
    direct_more_efficient = bool(
        payload.get("prime_direct_more_efficient")
        or payload.get("coordination_overhead_high")
        or payload.get("delegation_overhead_exceeds_prime")
    )
    catastrophic_offload_risk = bool(
        payload.get("catastrophic_offload_risk")
        or payload.get("catastrophic_if_offloaded")
    )
    opportunistic_course_correction = bool(
        payload.get("allow_prime_course_correction")
        or payload.get("opportunistic_prime_course_correction")
    )
    prime_budget_healthy = bool(payload.get("prime_budget_healthy"))
    prime_budget_abundant = bool(payload.get("prime_budget_abundant"))
    catastrophic_or_protected = bool(
        require_prime_route
        or governance_authorized
        or protected_governance
        or catastrophic_offload_risk
        or normalized_risk in {"high", "critical"}
    )

    sensitive_bounded = (
        normalized_task_class in {"review", "verification", "audit"}
        or completion_type in {"review_or_audit", "review_or_rewrite_spec"}
    )
    consensus_eligible = (
        not require_prime_route
        and not governance_authorized
        and not protected_governance
        and normalized_risk in {"low", "moderate"}
        and sensitive_bounded
    )
    batchable = (
        not require_prime_route
        and normalized_risk == "low"
        and normalized_task_class in {"general", "review", "maintenance"}
        and int(payload.get("priority") or 0) <= 4
        and int(payload.get("urgency") or 0) <= 3
    )
    cheap_first = (
        not require_prime_route
        and not governance_authorized
        and normalized_risk in {"low", "moderate"}
        and normalized_task_class in {"general", "coding", "review", "verification", "audit", "maintenance"}
    )
    prime_admission_basis: list[str] = []
    if direct_more_efficient:
        prime_admission_basis.append("direct_more_efficient")
    if catastrophic_or_protected:
        prime_admission_basis.append("catastrophic_or_protected")
    if opportunistic_course_correction and (prime_budget_healthy or prime_budget_abundant):
        prime_admission_basis.append("opportunistic_course_correction")
    prime_allowed = bool(prime_admission_basis)
    prefer_prime = bool(catastrophic_or_protected or direct_more_efficient or "opportunistic_course_correction" in prime_admission_basis)
    return {
        "task_class": normalized_task_class,
        "risk": normalized_risk,
        "completion_type": completion_type or None,
        "consensus_eligible": consensus_eligible,
        "batchable": batchable,
        "cheap_first": cheap_first,
        "sensitive_bounded": sensitive_bounded,
        "prime_allowed": prime_allowed,
        "prefer_prime": prefer_prime,
        "prime_admission_basis": tuple(prime_admission_basis),
        "prime_direct_more_efficient": direct_more_efficient,
        "catastrophic_or_protected": catastrophic_or_protected,
        "opportunistic_course_correction": opportunistic_course_correction,
        "prime_budget_healthy": prime_budget_healthy,
        "prime_budget_abundant": prime_budget_abundant,
    }


def infer_task_policy(task_payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(task_payload or {})
    completion_policy = dict(payload.get("completion_policy") or {})
    provenance = dict(payload.get("provenance") or {})
    metadata = {
        "completion_type": str(completion_policy.get("type") or "").strip(),
        "priority": int(payload.get("priority") or 0),
        "urgency": int(payload.get("urgency") or 0),
        "require_prime_route": bool(
            payload.get("permissions", {}).get("require_prime_route")
            if isinstance(payload.get("permissions"), dict)
            else False
        )
        or bool(completion_policy.get("route_preferences", {}).get("require_prime_route"))
        or bool(provenance.get("governance_update_authorized")),
        "governance_update_authorized": bool(provenance.get("governance_update_authorized")),
        "protected_governance": bool(provenance.get("protected_governance")),
        "prime_direct_more_efficient": bool(completion_policy.get("route_preferences", {}).get("prime_direct_more_efficient")),
        "catastrophic_offload_risk": bool(completion_policy.get("route_preferences", {}).get("catastrophic_offload_risk")),
        "allow_prime_course_correction": bool(completion_policy.get("route_preferences", {}).get("allow_prime_course_correction")),
    }
    return classify_work_policy(
        task_class=str(provenance.get("task_class") or payload.get("task_class") or "general"),
        risk=str(payload.get("risk") or "moderate"),
        metadata=metadata,
    )


def prime_burden_summary(
    *,
    attempt: dict[str, Any],
    task_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    usage = dict(attempt.get("resource_usage") or {})
    implementation = dict(usage.get("implementation") or {})
    route = dict(
        implementation.get("resolved_route")
        or implementation.get("requested_route")
        or usage.get("route")
        or {}
    )
    policy = infer_task_policy(task_payload)
    attempt_basis = _prime_admission_basis_from_attempt(attempt)
    effective_basis = attempt_basis or tuple(policy.get("prime_admission_basis") or ())
    task_id = str(attempt.get("task_id") or "").strip()
    task_title = str(dict(task_payload or {}).get("title") or task_id).strip() or task_id
    task_class = str(policy.get("task_class") or "general")
    prime_used = route_uses_prime(route)
    review_like = task_class in {"review", "verification", "audit"}
    avoidable = bool(prime_used and (policy["consensus_eligible"] or policy["cheap_first"]))
    unjustified = bool(prime_used and not effective_basis)
    return {
        "task_id": task_id,
        "task_title": task_title,
        "task_class": task_class,
        "route": route,
        "prime_used": prime_used,
        "avoidable_prime": avoidable,
        "unjustified_prime": unjustified,
        "consensus_eligible": bool(policy["consensus_eligible"]),
        "batchable": bool(policy["batchable"]),
        "review_like": review_like,
        "prime_admission_basis": list(effective_basis),
        "prime_allowed": bool(policy.get("prime_allowed") or effective_basis),
    }


def _prime_admission_basis_from_attempt(attempt: dict[str, Any]) -> tuple[str, ...]:
    for action in list(attempt.get("followup_actions") or []):
        payload = dict(action or {})
        if str(payload.get("type") or "").strip() != "prime_admission_basis":
            continue
        basis = tuple(
            str(item).strip()
            for item in list(payload.get("basis") or [])
            if str(item).strip()
        )
        if basis:
            return basis
    implementation = dict(dict(attempt.get("resource_usage") or {}).get("implementation") or {})
    basis = tuple(
        str(item).strip()
        for item in list(implementation.get("prime_admission_basis") or [])
        if str(item).strip()
    )
    return basis
