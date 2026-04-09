"""Base controller module for Astrata's federated handoff system.

Provides core abstractions and utilities for implementing controllers that manage
task delegation, decision-making, and recursive work processing in a federated
architecture.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional, Protocol, Union
from uuid import uuid4

import pydantic
from pydantic import BaseModel, Field


# Type aliases
ControllerId = str
TaskId = str
RiskLevel = Literal["low", "moderate", "high", "critical"]
DecisionStatus = Literal["accepted", "deferred", "blocked", "refused"]


class ControllerEnvelope(BaseModel):
    """Envelope containing metadata for controller task delegation."""
    controller_id: ControllerId
    task_id: TaskId
    priority: int = Field(default=0, ge=0, le=10)
    urgency: int = Field(default=0, ge=0, le=10)
    risk: RiskLevel = "moderate"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    expires_at: Optional[str] = None

    @pydantic.validator("created_at", "expires_at", pre=True, always=True)
    def set_timestamps(cls, v):
        """Auto-generate timestamps if not provided."""
        if v is None:
            from datetime import datetime
            return datetime.utcnow().isoformat()
        return v


class ControllerDecision(BaseModel):
    """Decision outcome from a controller evaluation."""
    status: DecisionStatus = "accepted"
    reason: str = ""
    followup_actions: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    processing_time_ms: Optional[int] = None

    def is_successful(self) -> bool:
        """Check if the decision allows proceeding."""
        return self.status in ("accepted", "deferred")


class TaskContext(BaseModel):
    """Context information for task processing."""
    envelope: ControllerEnvelope
    input_data: Dict[str, Any] = Field(default_factory=dict)
    previous_decisions: List[ControllerDecision] = Field(default_factory=list)
    recursion_depth: int = 0
    max_recursion_depth: int = 10


class ControllerProtocol(Protocol):
    """Protocol defining the interface for controllers."""

    @property
    def controller_id(self) -> ControllerId:
        """Unique identifier for this controller."""
        ...

    async def evaluate_task(self, context: TaskContext) -> ControllerDecision:
        """Evaluate and decide on a task."""
        ...

    async def can_handle_task(self, envelope: ControllerEnvelope) -> bool:
        """Check if this controller can handle the given task."""
        ...


class BaseController(ABC):
    """Abstract base class for implementing controllers.

    Controllers handle task delegation and decision-making in the federated
    system. Subclasses must implement the core evaluation logic.
    """

    def __init__(self, controller_id: Optional[ControllerId] = None):
        self._controller_id = controller_id or f"{self.__class__.__name__}-{uuid4().hex[:8]}"

    @property
    def controller_id(self) -> ControllerId:
        """Unique identifier for this controller."""
        return self._controller_id

    @abstractmethod
    async def evaluate_task(self, context: TaskContext) -> ControllerDecision:
        """Evaluate a task and return a decision.

        Args:
            context: Task context containing envelope and input data

        Returns:
            ControllerDecision with status and reasoning
        """
        pass

    @abstractmethod
    async def can_handle_task(self, envelope: ControllerEnvelope) -> bool:
        """Determine if this controller can handle the given task.

        Args:
            envelope: Task envelope with metadata

        Returns:
            True if this controller can handle the task
        """
        pass

    async def process_recursive_task(
        self,
        context: TaskContext,
        sub_controllers: List[ControllerProtocol]
    ) -> ControllerDecision:
        """Process a task recursively using sub-controllers.

        Args:
            context: Task context
            sub_controllers: List of controllers to delegate to

        Returns:
            Aggregated decision from recursive processing
        """
        if context.recursion_depth >= context.max_recursion_depth:
            return ControllerDecision(
                status="blocked",
                reason=f"Maximum recursion depth {context.max_recursion_depth} exceeded"
            )

        # Evaluate current level
        decision = await self.evaluate_task(context)
        if not decision.is_successful():
            return decision

        # Process sub-tasks recursively
        for action in decision.followup_actions:
            if "delegate_to" in action:
                sub_controller_id = action["delegate_to"]
                sub_controller = next(
                    (c for c in sub_controllers if c.controller_id == sub_controller_id),
                    None
                )
                if sub_controller:
                    sub_context = TaskContext(
                        envelope=ControllerEnvelope(
                            controller_id=sub_controller_id,
                            task_id=f"{context.envelope.task_id}-sub-{uuid4().hex[:4]}",
                            priority=context.envelope.priority,
                            urgency=context.envelope.urgency,
                            risk=context.envelope.risk,
                            metadata={**context.envelope.metadata, **action.get("metadata", {})}
                        ),
                        input_data=action.get("input_data", {}),
                        previous_decisions=context.previous_decisions + [decision],
                        recursion_depth=context.recursion_depth + 1,
                        max_recursion_depth=context.max_recursion_depth
                    )
                    sub_decision = await sub_controller.process_recursive_task(
                        sub_context, sub_controllers
                    )
                    if not sub_decision.is_successful():
                        return ControllerDecision(
                            status="blocked",
                            reason=f"Sub-task failed: {sub_decision.reason}",
                            followup_actions=[action]
                        )

        return decision


def create_controller_envelope(
    task_id: TaskId,
    controller_id: ControllerId,
    priority: int = 0,
    urgency: int = 0,
    risk: RiskLevel = "moderate",
    metadata: Optional[Dict[str, Any]] = None
) -> ControllerEnvelope:
    """Create a new controller envelope for task delegation.

    Args:
        task_id: Unique task identifier
        controller_id: Target controller identifier
        priority: Task priority (0-10)
        urgency: Task urgency (0-10)
        risk: Risk level
        metadata: Additional metadata

    Returns:
        Configured ControllerEnvelope
    """
    return ControllerEnvelope(
        controller_id=controller_id,
        task_id=task_id,
        priority=priority,
        urgency=urgency,
        risk=risk,
        metadata=metadata or {}
    )


def aggregate_decisions(decisions: List[ControllerDecision]) -> ControllerDecision:
    """Aggregate multiple controller decisions into a single decision.

    Uses majority voting for status, combines reasons, and averages confidence.

    Args:
        decisions: List of decisions to aggregate

    Returns:
        Aggregated ControllerDecision
    """
    if not decisions:
        return ControllerDecision(status="refused", reason="No decisions provided")

    # Count status votes
    status_counts = {}
    total_confidence = 0.0
    reasons = []
    all_actions = []

    for decision in decisions:
        status_counts[decision.status] = status_counts.get(decision.status, 0) + 1
        total_confidence += decision.confidence
        if decision.reason:
            reasons.append(decision.reason)
        all_actions.extend(decision.followup_actions)

    # Determine majority status
    majority_status = max(status_counts, key=status_counts.get)
    avg_confidence = total_confidence / len(decisions)
    combined_reason = "; ".join(reasons) if reasons else "Aggregated decision"

    return ControllerDecision(
        status=majority_status,
        reason=combined_reason,
        followup_actions=all_actions,
        confidence=avg_confidence
    )


# Registry for controller discovery
_controller_registry: Dict[ControllerId, ControllerProtocol] = {}


def register_controller(controller: ControllerProtocol) -> None:
    """Register a controller in the global registry.

    Args:
        controller: Controller instance to register
    """
    _controller_registry[controller.controller_id] = controller


def get_controller(controller_id: ControllerId) -> Optional[ControllerProtocol]:
    """Retrieve a controller from the registry.

    Args:
        controller_id: Controller identifier

    Returns:
        Registered controller or None
    """
    return _controller_registry.get(controller_id)


def list_controllers() -> List[ControllerId]:
    """List all registered controller IDs.

    Returns:
        List of registered controller identifiers
    """
    return list(_controller_registry.keys())
