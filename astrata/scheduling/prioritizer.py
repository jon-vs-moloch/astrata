"""Continuous-signal prioritizer for normalized work items."""

from __future__ import annotations

from dataclasses import dataclass

from astrata.scheduling.work_pool import ScheduledWorkItem


@dataclass(frozen=True)
class PrioritizedSelection:
    item: ScheduledWorkItem
    score: tuple[int, int, int, int, int, int, int, int, str]


class WorkPrioritizer:
    """Choose the next work item without flattening all signals into modes."""

    def select(self, items: list[ScheduledWorkItem]) -> PrioritizedSelection | None:
        if not items:
            return None
        scored = [PrioritizedSelection(item=item, score=self.score(item)) for item in items]
        return max(scored, key=lambda selection: selection.score)

    def score(self, item: ScheduledWorkItem) -> tuple[int, int, int, int, int, int, int, int, str]:
        candidate = item.candidate
        source_bias = self._source_bias(item)
        closure_pressure = self._closure_pressure(item)
        staleness = self._staleness_bias(item)
        cheap_lane_bias = self._cheap_lane_bias(item)
        system_change_likelihood = self._system_change_likelihood(item)
        signal_confidence = self._signal_confidence(item)
        retry_health = self._retry_health(item)
        remediation_bias = self._remediation_bias(item)
        freshness = item.created_at or ""
        return (
            candidate.priority,
            candidate.urgency,
            closure_pressure,
            staleness,
            system_change_likelihood,
            cheap_lane_bias,
            signal_confidence,
            retry_health,
            source_bias + remediation_bias,
            freshness,
        )

    def _source_bias(self, item: ScheduledWorkItem) -> int:
        if item.source_kind == "message_task":
            return 3
        if item.source_kind == "retry_task":
            return 2
        if item.source_kind == "artifact_finding":
            return 2
        if item.source_kind == "planner_remediation":
            return 1
        return 0

    def _closure_pressure(self, item: ScheduledWorkItem) -> int:
        metadata = dict(item.metadata or {})
        if metadata.get("likely_satisfied"):
            return -3
        pressure = int(metadata.get("closure_pressure") or 0)
        if metadata.get("is_followup"):
            pressure += 1
        return pressure

    def _staleness_bias(self, item: ScheduledWorkItem) -> int:
        metadata = dict(item.metadata or {})
        age_hours = float(metadata.get("task_age_hours") or 0.0)
        if age_hours >= 12:
            return 3
        if age_hours >= 4:
            return 2
        if age_hours >= 1:
            return 1
        return 0

    def _cheap_lane_bias(self, item: ScheduledWorkItem) -> int:
        metadata = dict(item.metadata or {})
        preferred_cli_tools = list(metadata.get("preferred_cli_tools") or [])
        if any(tool in {"kilocode", "gemini-cli", "claude-code"} for tool in preferred_cli_tools):
            return 2
        preferred_providers = list(metadata.get("preferred_providers") or [])
        if any(provider in {"cli", "custom", "google", "anthropic"} for provider in preferred_providers):
            return 1
        if "codex" in preferred_providers:
            return -1
        return 0

    def _system_change_likelihood(self, item: ScheduledWorkItem) -> int:
        metadata = dict(item.metadata or {})
        score = 0
        if list(metadata.get("expected_paths") or []):
            score += 3
        if metadata.get("mentions_repo_file"):
            score += 2
        completion_type = str(metadata.get("completion_type") or "").strip().lower()
        if completion_type in {"respond_or_execute", "review_or_execute"}:
            score += 2
        if completion_type == "request_clarification":
            score -= 2
        if metadata.get("historical_file_write"):
            score += 1
        if metadata.get("commentary_only_history"):
            score -= 1
        return score

    def _signal_confidence(self, item: ScheduledWorkItem) -> int:
        metadata = dict(item.metadata or {})
        confidence = metadata.get("artifact_confidence")
        try:
            return int(float(confidence) * 100)
        except Exception:
            return 0

    def _retry_health(self, item: ScheduledWorkItem) -> int:
        metadata = dict(item.metadata or {})
        retry_count = int(metadata.get("retry_count") or 0)
        if retry_count <= 0:
            return 0
        return max(0, 10 - retry_count)

    def _remediation_bias(self, item: ScheduledWorkItem) -> int:
        metadata = dict(item.metadata or {})
        strategy = str(metadata.get("strategy") or "").strip().lower()
        if strategy == "alternate_provider":
            return 2
        if strategy == "fallback_only":
            return 1
        route_health = str(metadata.get("route_health_status") or "").strip().lower()
        if route_health == "healthy":
            return 1
        if route_health == "broken":
            return -1
        return 0
