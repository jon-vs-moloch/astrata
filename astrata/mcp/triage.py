"""Fast triage policy for remote hosted MCP requests."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TriageLane = Literal["instant", "fast_review", "slow_review", "attention", "blocked"]
TriageUrgency = Literal["low", "normal", "high", "urgent"]


class RemoteRequestTriageDecision(BaseModel):
    """A cheap routing decision made before remote work enters the local queue."""

    lane: TriageLane
    urgency: TriageUrgency = "normal"
    requires_attention: bool = False
    action: Literal["project_result", "forward", "queue", "request_attention", "reject"]
    reason: str
    sla_seconds: int = 300
    audit_tags: tuple[str, ...] = Field(default_factory=tuple)

    def metadata(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class RemoteRequestTriagePolicy:
    """Classifies hosted relay requests into fast and deliberate handling lanes."""

    INSTANT_TOOLS = frozenset({"list_capabilities", "get_task_status", "search", "fetch"})
    FAST_REVIEW_TOOLS = frozenset({"message_prime", "search_files", "read_file", "propose_patch"})
    SLOW_REVIEW_TOOLS = frozenset({"submit_task", "delegate_subtasks", "handoff_to_controller", "request_browser_action"})
    ATTENTION_TOOLS = frozenset({"request_elevation", "apply_patch", "run_tests", "run_command"})

    def classify(self, remote_request: dict[str, Any]) -> RemoteRequestTriageDecision:
        request = dict(remote_request or {})
        tool_name = str(request.get("tool_name") or "").strip()
        arguments = dict(request.get("arguments") or {})
        requested_urgency = self._normalize_urgency(arguments.get("urgency") or request.get("urgency"))

        if not tool_name:
            return RemoteRequestTriageDecision(
                lane="blocked",
                urgency="high",
                requires_attention=True,
                action="reject",
                reason="missing_tool_name",
                sla_seconds=30,
                audit_tags=("remote_triage", "invalid_request"),
            )
        if tool_name in self.INSTANT_TOOLS:
            return RemoteRequestTriageDecision(
                lane="instant",
                urgency=requested_urgency,
                action="project_result",
                reason="connector_safe_projection",
                sla_seconds=5,
                audit_tags=("remote_triage", "projection"),
            )
        if tool_name in self.ATTENTION_TOOLS or self._looks_attention_seeking(arguments):
            return RemoteRequestTriageDecision(
                lane="attention",
                urgency="urgent" if tool_name in {"apply_patch", "run_command", "request_elevation"} else requested_urgency,
                requires_attention=True,
                action="request_attention",
                reason=f"{tool_name}_requires_local_attention",
                sla_seconds=30,
                audit_tags=("remote_triage", "attention", tool_name),
            )
        if tool_name in self.FAST_REVIEW_TOOLS:
            return RemoteRequestTriageDecision(
                lane="fast_review",
                urgency=requested_urgency,
                action="forward",
                reason=f"{tool_name}_is_latency_sensitive",
                sla_seconds=60 if tool_name == "propose_patch" else 120,
                audit_tags=("remote_triage", "fast_review", tool_name),
            )
        if tool_name in self.SLOW_REVIEW_TOOLS:
            return RemoteRequestTriageDecision(
                lane="slow_review",
                urgency=requested_urgency,
                action="queue",
                reason=f"{tool_name}_needs_deliberate_review",
                sla_seconds=600,
                audit_tags=("remote_triage", "slow_review", tool_name),
            )
        return RemoteRequestTriageDecision(
            lane="blocked",
            urgency="high",
            requires_attention=True,
            action="reject",
            reason=f"unknown_remote_tool:{tool_name}",
            sla_seconds=30,
            audit_tags=("remote_triage", "unknown_tool", tool_name),
        )

    def _normalize_urgency(self, value: Any) -> TriageUrgency:
        normalized = str(value or "normal").strip().lower()
        if normalized in {"low", "normal", "high", "urgent"}:
            return normalized  # type: ignore[return-value]
        return "normal"

    def _looks_attention_seeking(self, arguments: dict[str, Any]) -> bool:
        if bool(arguments.get("requires_attention") or arguments.get("user_attention_required")):
            return True
        urgency = str(arguments.get("urgency") or "").strip().lower()
        return urgency in {"urgent", "asap", "now"}
