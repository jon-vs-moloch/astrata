"""Inference activity summaries for resource-awareness and self-observation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any


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
    delegation_by_source: Counter[str] = Counter()
    considered_attempts = 0
    spent_attempts = 0

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
        elif generation_mode == "delegated_worker":
            delegation_by_source[route_label] += 1

    worker_statuses: Counter[str] = Counter()
    worker_routes: Counter[str] = Counter()
    for task in tasks:
        provenance = dict(task.get("provenance") or {})
        if provenance.get("source") != "worker_delegation":
            continue
        worker_statuses[str(task.get("status") or "unknown")] += 1
        worker_routes[_route_label(dict(provenance.get("route") or {}))] += 1

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
        "delegation_by_source": dict(delegation_by_source),
        "worker_statuses": dict(worker_statuses),
        "worker_routes": dict(worker_routes),
        "quota_snapshot_count": len(quota_snapshots),
        "quota_pressure": constrained,
    }
