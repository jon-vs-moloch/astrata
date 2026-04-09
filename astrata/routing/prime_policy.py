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
    return {
        "task_class": normalized_task_class,
        "risk": normalized_risk,
        "completion_type": completion_type or None,
        "consensus_eligible": consensus_eligible,
        "batchable": batchable,
        "cheap_first": cheap_first,
        "sensitive_bounded": sensitive_bounded,
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
    task_id = str(attempt.get("task_id") or "").strip()
    task_title = str(dict(task_payload or {}).get("title") or task_id).strip() or task_id
    task_class = str(policy.get("task_class") or "general")
    prime_used = route_uses_prime(route)
    review_like = task_class in {"review", "verification", "audit"}
    avoidable = bool(prime_used and (policy["consensus_eligible"] or policy["cheap_first"]))
    return {
        "task_id": task_id,
        "task_title": task_title,
        "task_class": task_class,
        "route": route,
        "prime_used": prime_used,
        "avoidable_prime": avoidable,
        "consensus_eligible": bool(policy["consensus_eligible"]),
        "batchable": bool(policy["batchable"]),
        "review_like": review_like,
    }
