"""Normalized work-pool records for queue-native scheduling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScheduledWorkItem:
    candidate: Any
    inspection: dict[str, Any]
    verification: Any
    source_kind: str
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_assessment(
        cls,
        assessment: Any,
        *,
        source_kind: str,
        created_at: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "ScheduledWorkItem":
        return cls(
            candidate=assessment.candidate,
            inspection=assessment.inspection,
            verification=assessment.verification,
            source_kind=source_kind,
            created_at=created_at,
            metadata=dict(metadata or {}),
        )
