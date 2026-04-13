"""Controller bridge for external Prime or partner-agent integration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from astrata.controllers.base import ControllerDecision
from astrata.records.handoffs import HandoffRecord


class ExternalAgentBinding(BaseModel):
    """Declarative description of an external agent bridge."""

    agent_id: str
    transport: str = "api"
    role: Literal["prime", "assistant", "worker", "peer"] = "peer"
    can_be_prime: bool = False
    can_receive_subtasks: bool = True
    online: bool = True
    accepts_sensitive_payloads: bool = False
    max_parallel_tasks: int = Field(default=1, ge=1)
    capabilities: tuple[str, ...] = ()
    notes: str = ""


class ExternalAgentController:
    """Governs handoffs that cross from Astrata into an external agent runtime."""

    def __init__(self, *, binding: ExternalAgentBinding) -> None:
        self._binding = binding

    def evaluate_handoff(self, handoff: HandoffRecord) -> ControllerDecision:
        envelope = dict(handoff.envelope or {})
        metadata = dict(handoff.metadata or {})
        requested_mode = str(handoff.delegation_mode or metadata.get("delegation_mode") or "direct").strip().lower()
        requires_prime = bool(envelope.get("require_prime_route"))
        sensitivity = str(
            envelope.get("security_level")
            or metadata.get("security_level")
            or envelope.get("sensitivity")
            or "normal"
        ).strip().lower()
        if not self._binding.online:
            return ControllerDecision(
                status="deferred",
                reason=f"External agent `{self._binding.agent_id}` is currently offline.",
                followup_actions=[
                    {"type": "wait_for_external_agent", "agent_id": self._binding.agent_id},
                    {"type": "preserve_agent_identity", "task_id": handoff.task_id, "agent_id": self._binding.agent_id},
                ],
            )
        if requires_prime and not self._binding.can_be_prime:
            return ControllerDecision(
                status="refused",
                reason=f"External agent `{self._binding.agent_id}` is not approved to serve as Prime.",
                followup_actions=[
                    {"type": "require_internal_prime", "task_id": handoff.task_id},
                ],
            )
        if requested_mode in {"direct", "cowork"} and not self._binding.can_receive_subtasks:
            return ControllerDecision(
                status="refused",
                reason=f"External agent `{self._binding.agent_id}` cannot accept direct delegated subtasks.",
                followup_actions=[
                    {"type": "reroute_handoff", "task_id": handoff.task_id, "reason": "external_subtasks_unsupported"},
                ],
            )
        if sensitivity in {"sensitive", "secret", "enclave"} and not self._binding.accepts_sensitive_payloads:
            return ControllerDecision(
                status="blocked",
                reason=(
                    f"External agent `{self._binding.agent_id}` is outside the permitted disclosure boundary for "
                    f"`{sensitivity}` work."
                ),
                followup_actions=[
                    {"type": "redact_or_localize", "task_id": handoff.task_id, "security_level": sensitivity},
                ],
            )
        return ControllerDecision(
            status="accepted",
            reason=f"External agent `{self._binding.agent_id}` is eligible for {requested_mode} collaboration.",
            followup_actions=[
                {
                    "type": "external_agent_handoff_approved",
                    "agent_id": self._binding.agent_id,
                    "transport": self._binding.transport,
                    "role": self._binding.role,
                    "delegation_mode": requested_mode,
                },
                {
                    "type": "record_execution_boundary",
                    "execution_boundary": "external",
                    "bridge_id": handoff.bridge_id or self._binding.agent_id,
                },
            ],
        )
