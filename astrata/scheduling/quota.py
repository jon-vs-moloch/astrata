"""Quota-window pacing and request-budget guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from astrata.providers.registry import ProviderRegistry
from astrata.storage.db import AstrataDatabase


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    reason: str
    usage_last_hour: int
    limit_per_hour: int | None
    next_allowed_at: str | None = None
    active_window: dict[str, Any] | None = None


class QuotaPolicy:
    def __init__(
        self,
        *,
        db: AstrataDatabase,
        limits_per_source: dict[str, int | None],
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._db = db
        self._limits_per_source = limits_per_source
        self._registry = registry

    def assess(self, route: dict[str, Any]) -> QuotaDecision:
        source_id = _route_source_id(route)
        limit = self._limits_per_source.get(source_id)
        usage = self._usage_last_hour(source_id)
        windows = self._collect_windows(route, source_id, usage, limit)
        if not windows:
            return QuotaDecision(True, "unlimited", usage, limit, None, None)
        active_window = self._most_throttling_window(windows)
        if active_window is None:
            return QuotaDecision(True, "within_known_windows", usage, limit, None, None)
        remaining = int(active_window.get("requests_remaining") or 0)
        reset_time = self._coerce_time(active_window.get("reset_time"))
        if remaining <= 0 and reset_time is not None and _now() < reset_time:
            return QuotaDecision(
                False,
                "window_exhausted",
                usage,
                limit,
                reset_time.isoformat(),
                active_window,
            )
        spacing = self._window_spacing_seconds(active_window)
        last_used_at = self._last_provider_attempt_time(source_id)
        if last_used_at is not None and spacing is not None:
            next_allowed = last_used_at + timedelta(seconds=spacing)
            if _now() < next_allowed:
                return QuotaDecision(
                    False,
                    "paced_window",
                    usage,
                    limit,
                    next_allowed.isoformat(),
                    active_window,
                )
        if usage >= limit:
            return QuotaDecision(False, "hourly_quota_reached", usage, limit, None, active_window)
        return QuotaDecision(True, "within_hourly_quota", usage, limit, None, active_window)

    def _usage_last_hour(self, source_id: str) -> int:
        cutoff = _now() - timedelta(hours=1)
        count = 0
        for attempt in self._db.list_records("attempts"):
            ended_at = str(attempt.get("ended_at") or "").strip()
            if not ended_at:
                continue
            try:
                ended = datetime.fromisoformat(ended_at)
            except Exception:
                continue
            if ended.tzinfo is None:
                ended = ended.replace(tzinfo=timezone.utc)
            if ended < cutoff:
                continue
            usage = dict(attempt.get("resource_usage") or {})
            implementation = dict(usage.get("implementation") or {})
            if str(implementation.get("generation_mode") or "").strip().lower() != "provider":
                continue
            requested_route = dict(implementation.get("requested_route") or usage.get("route") or {})
            if _route_source_id(requested_route) == source_id:
                count += 1
        return count

    def _last_provider_attempt_time(self, source_id: str) -> datetime | None:
        latest: datetime | None = None
        for attempt in self._db.list_records("attempts"):
            ended_at = str(attempt.get("ended_at") or "").strip()
            if not ended_at:
                continue
            try:
                ended = datetime.fromisoformat(ended_at)
            except Exception:
                continue
            if ended.tzinfo is None:
                ended = ended.replace(tzinfo=timezone.utc)
            usage = dict(attempt.get("resource_usage") or {})
            implementation = dict(usage.get("implementation") or {})
            if str(implementation.get("generation_mode") or "").strip().lower() != "provider":
                continue
            requested_route = dict(implementation.get("requested_route") or usage.get("route") or {})
            if _route_source_id(requested_route) != source_id:
                continue
            if latest is None or ended > latest:
                latest = ended
        return latest

    def _collect_windows(
        self,
        route: dict[str, Any],
        source_id: str,
        usage_last_hour: int,
        limit_per_hour: int | None,
    ) -> list[dict[str, Any]]:
        windows: list[dict[str, Any]] = []
        if limit_per_hour is not None and limit_per_hour > 0:
            windows.append(
                {
                    "requests_remaining": max(0, limit_per_hour - usage_last_hour),
                    "requests_limit": limit_per_hour,
                    "reset_time": (_now() + timedelta(hours=1)).isoformat(),
                    "window_duration_seconds": 3600,
                    "source": "local_hourly_cap",
                }
            )
        provider_name = str(route.get("provider") or "").strip().lower()
        if self._registry is not None and provider_name:
            provider = self._registry.get_provider(provider_name)
            if provider is not None:
                dynamic = provider.get_quota_windows(route)
                if dynamic:
                    windows.extend(dynamic)
        return windows

    def _most_throttling_window(self, windows: list[dict[str, Any]]) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_spacing: float | None = None
        for window in windows:
            spacing = self._window_spacing_seconds(window)
            if spacing is None:
                continue
            if best is None or best_spacing is None or spacing > best_spacing:
                best = window
                best_spacing = spacing
        return best

    def _window_spacing_seconds(self, window: dict[str, Any]) -> float | None:
        remaining = int(window.get("requests_remaining") or 0)
        reset_time = self._coerce_time(window.get("reset_time"))
        if reset_time is None:
            duration = int(window.get("window_duration_seconds") or 0)
            limit = int(window.get("requests_limit") or 0)
            if duration <= 0 or limit <= 0:
                return None
            return max(1.0, float(duration) / float(limit))
        seconds_remaining = max(1.0, (reset_time - _now()).total_seconds())
        if remaining <= 0:
            return seconds_remaining
        return max(1.0, seconds_remaining / float(remaining))

    def _coerce_time(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except Exception:
                return None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return None


def default_source_limits() -> dict[str, int | None]:
    return {
        "codex": 12,
        "cli:codex-cli": 12,
        "cli:kilocode": 200,
        "cli:gemini-cli": 60,
        "cli:claude-code": 30,
        "openai": 60,
        "google": 60,
        "anthropic": 40,
        "ollama": None,
        "strata-endpoint": None,
        "custom": 60,
        "unknown": 10,
    }


def _route_source_id(route: dict[str, Any]) -> str:
    provider = str(route.get("provider") or "").strip().lower()
    cli_tool = str(route.get("cli_tool") or "").strip().lower()
    if provider == "cli" and cli_tool:
        return f"cli:{cli_tool}"
    return provider or "unknown"
