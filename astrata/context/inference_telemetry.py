"""Inference activity summaries for resource-awareness and self-observation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from astrata.routing.prime_policy import infer_task_policy, prime_burden_summary


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _route_label(route: dict[str, Any]) -> str:
    provider = str(route.get("provider") or "").strip().lower()
    cli_tool = str(route.get("cli_tool") or "").strip().lower()
    model = str(route.get("model") or "").strip()
    if provider == "cli" and cli_tool:
        return f"cli:{cli_tool}:{model or 'default'}"
    return f"{provider or 'unknown'}:{model or 'default'}"


def build_quota_snapshot(
    *,
    route: dict[str, Any],
    decision: Any,
    cost_rank: int | None = None,
) -> dict[str, Any]:
    active_window = dict(getattr(decision, "active_window", None) or {})
    remaining = int(active_window.get("requests_remaining") or 0)
    limit = int(active_window.get("requests_limit") or 0)
    headroom_ratio = (float(remaining) / float(limit)) if limit > 0 else (1.0 if decision.allowed else 0.0)
    snapshot = {
        "route": dict(route),
        "source": _route_label(route),
        "allowed": bool(decision.allowed),
        "reason": str(decision.reason),
        "usage_last_hour": int(decision.usage_last_hour),
        "limit_per_hour": decision.limit_per_hour,
        "next_allowed_at": decision.next_allowed_at,
        "active_window": active_window,
        "headroom_ratio": round(headroom_ratio, 4),
    }
    if cost_rank is not None:
        snapshot["cost_rank"] = int(cost_rank)
    return snapshot


def summarize_inference_activity(
    *,
    attempts: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    quota_snapshots: list[dict[str, Any]] | None = None,
    window_hours: int = 24,
) -> dict[str, Any]:
    cutoff = _now() - timedelta(hours=max(1, window_hours))
    generation_modes: Counter[str] = Counter()
    spent_by_source: Counter[str] = Counter()
    spent_by_model: Counter[str] = Counter()
    spent_by_task_class: Counter[str] = Counter()
    delegation_by_source: Counter[str] = Counter()
    prime_spend_by_task_class: Counter[str] = Counter()
    considered_attempts = 0
    spent_attempts = 0
    prime_spend_attempts = 0
    avoidable_prime_attempts = 0
    unjustified_prime_attempts = 0
    prime_review_attempts = 0
    prime_consensus_misses = 0
    consensus_candidate_attempts = 0
    avoidable_prime_examples: list[dict[str, Any]] = []
    unjustified_prime_examples: list[dict[str, Any]] = []
    task_by_id = {str(task.get("task_id") or "").strip(): dict(task) for task in tasks if str(task.get("task_id") or "").strip()}

    for attempt in attempts:
        timestamp = _parse_time(
            str(attempt.get("ended_at") or "").strip() or str(attempt.get("started_at") or "").strip()
        )
        if timestamp is None or timestamp < cutoff:
            continue
        usage = dict(attempt.get("resource_usage") or {})
        implementation = dict(usage.get("implementation") or {})
        generation_mode = str(implementation.get("generation_mode") or "").strip().lower()
        if not generation_mode:
            continue
        considered_attempts += 1
        generation_modes[generation_mode] += 1
        task_payload = task_by_id.get(str(attempt.get("task_id") or "").strip(), {})
        task_policy = infer_task_policy(task_payload)
        task_class = str(task_policy.get("task_class") or "general")
        route = dict(
            implementation.get("resolved_route")
            or implementation.get("requested_route")
            or usage.get("route")
            or {}
        )
        route_label = _route_label(route)
        model = str(route.get("model") or "").strip() or "default"
        if generation_mode in {"provider", "delegated_worker_result"}:
            spent_attempts += 1
            spent_by_source[route_label] += 1
            spent_by_model[model] += 1
            spent_by_task_class[task_class] += 1
        elif generation_mode == "delegated_worker":
            delegation_by_source[route_label] += 1
        burden = prime_burden_summary(attempt=attempt, task_payload=task_payload)
        if burden["consensus_eligible"]:
            consensus_candidate_attempts += 1
        if burden["prime_used"]:
            prime_spend_attempts += 1
            prime_spend_by_task_class[task_class] += 1
            if burden["review_like"]:
                prime_review_attempts += 1
        if burden["avoidable_prime"]:
            avoidable_prime_attempts += 1
            if burden["consensus_eligible"]:
                prime_consensus_misses += 1
            if len(avoidable_prime_examples) < 5:
                avoidable_prime_examples.append(
                    {
                        "task_id": burden["task_id"],
                        "task_title": burden["task_title"],
                        "task_class": burden["task_class"],
                        "route": burden["route"],
                        "consensus_eligible": burden["consensus_eligible"],
                        "batchable": burden["batchable"],
                    }
                )
        if burden["unjustified_prime"]:
            unjustified_prime_attempts += 1
            if len(unjustified_prime_examples) < 5:
                unjustified_prime_examples.append(
                    {
                        "task_id": burden["task_id"],
                        "task_title": burden["task_title"],
                        "task_class": burden["task_class"],
                        "route": burden["route"],
                        "prime_admission_basis": burden["prime_admission_basis"],
                    }
                )

    worker_statuses: Counter[str] = Counter()
    worker_routes: Counter[str] = Counter()
    batchable_pending_tasks = 0
    for task in tasks:
        provenance = dict(task.get("provenance") or {})
        if provenance.get("source") != "worker_delegation":
            continue
        worker_statuses[str(task.get("status") or "unknown")] += 1
        worker_routes[_route_label(dict(provenance.get("route") or {}))] += 1
    for task in tasks:
        if str(task.get("status") or "").strip() != "pending":
            continue
        task_policy = infer_task_policy(task)
        if task_policy["batchable"]:
            batchable_pending_tasks += 1

    quota_snapshots = [dict(snapshot) for snapshot in list(quota_snapshots or [])]
    constrained = sorted(
        quota_snapshots,
        key=lambda item: (
            bool(item.get("allowed")),
            float(item.get("headroom_ratio") or 0.0),
            int(item.get("cost_rank") or 99),
        ),
    )[:6]

    return {
        "generated_at": _now().isoformat(),
        "window_hours": window_hours,
        "considered_attempts": considered_attempts,
        "spent_attempts": spent_attempts,
        "generation_modes": dict(generation_modes),
        "spent_by_source": dict(spent_by_source),
        "spent_by_model": dict(spent_by_model),
        "spent_by_task_class": dict(spent_by_task_class),
        "prime_spend_attempts": prime_spend_attempts,
        "avoidable_prime_attempts": avoidable_prime_attempts,
        "unjustified_prime_attempts": unjustified_prime_attempts,
        "prime_review_attempts": prime_review_attempts,
        "prime_consensus_misses": prime_consensus_misses,
        "consensus_candidate_attempts": consensus_candidate_attempts,
        "prime_spend_by_task_class": dict(prime_spend_by_task_class),
        "avoidable_prime_examples": avoidable_prime_examples,
        "unjustified_prime_examples": unjustified_prime_examples,
        "batchable_pending_tasks": batchable_pending_tasks,
        "delegation_by_source": dict(delegation_by_source),
        "worker_statuses": dict(worker_statuses),
        "worker_routes": dict(worker_routes),
        "quota_snapshot_count": len(quota_snapshots),
        "quota_pressure": constrained,
    }
