"""Deterministic failure-to-resolution policy for Loop 0 work."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field


ResolutionKind = Literal["retry", "decompose", "repair_process", "blocked", "clarify"]

_UUIDISH_PATTERN = re.compile(r"\b[0-9a-f]{8,}(?:-[0-9a-f]{4,}){0,4}\b", re.IGNORECASE)
_INTEGER_PATTERN = re.compile(r"\b\d+\b")


class TaskResolution(BaseModel):
    kind: ResolutionKind
    reason: str
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    next_status: Literal["pending", "blocked", "failed"] = "failed"
    followup_specs: list[dict[str, Any]] = Field(default_factory=list)
    repeated_failure_count: int = 0
    failure_fingerprint: str = ""


def determine_task_resolution(
    *,
    task_payload: dict[str, Any],
    message_payload: dict[str, Any],
    attempts: list[dict[str, Any]],
) -> TaskResolution:
    title = str(task_payload.get("title") or "").strip()
    description = str(task_payload.get("description") or "").strip()
    status = str(message_payload.get("status") or "").strip().lower()
    reason = str(message_payload.get("reason") or "").strip()
    detail = str(message_payload.get("detail") or "").strip()
    failure_text = reason or detail or str(message_payload.get("raw_content") or "").strip()
    fingerprint = _normalize_failure_reason(failure_text)
    repeated = _count_consecutive_matching_failures(attempts, fingerprint)

    if _looks_like_clarification_task(title=title, description=description):
        return TaskResolution(
            kind="clarify",
            reason="The task reads like it needs principal clarification rather than more autonomous retries.",
            confidence=0.85,
            next_status="blocked",
            followup_specs=[
                {
                    "title": f"Clarify: {title or 'blocked task'}",
                    "description": (
                        f"Ask the principal to clarify or decide the blocked work: {description or title}."
                    ),
                    "priority": int(task_payload.get("priority") or 4),
                    "urgency": max(1, int(task_payload.get("urgency") or 0)),
                    "risk": str(task_payload.get("risk") or "low"),
                    "completion_type": "request_clarification",
                    "success_criteria": {"clarify": True},
                }
            ],
            repeated_failure_count=repeated,
            failure_fingerprint=fingerprint,
        )

    if repeated >= 2:
        return TaskResolution(
            kind="repair_process",
            reason="The same failure repeated enough times that the owning process or routing policy should be repaired.",
            confidence=0.9,
            next_status="blocked",
            followup_specs=[
                {
                    "title": f"Repair process for {title or 'delegated work'}",
                    "description": (
                        "Investigate why this delegated task keeps failing with the same signature and repair the "
                        "owning route, worker procedure, or execution policy."
                    ),
                    "priority": max(5, int(task_payload.get("priority") or 0)),
                    "urgency": max(2, int(task_payload.get("urgency") or 0)),
                    "risk": "moderate",
                    "completion_type": "review_or_audit",
                    "success_criteria": {"process_repaired": True},
                }
            ],
            repeated_failure_count=repeated,
            failure_fingerprint=fingerprint,
        )

    if _looks_like_multistage_task(title=title, description=description):
        return TaskResolution(
            kind="decompose",
            reason="This task appears multistage and should be broken into dependency-aware leaf work.",
            confidence=0.85,
            next_status="blocked",
            followup_specs=[
                {
                    "title": f"Decompose: {title or 'blocked task'}",
                    "description": (
                        "Break this task into oneshottable leaf tasks with explicit dependency edges before retrying "
                        f"execution: {description or title}."
                    ),
                    "priority": max(4, int(task_payload.get("priority") or 0)),
                    "urgency": int(task_payload.get("urgency") or 0),
                    "risk": str(task_payload.get("risk") or "low"),
                    "completion_type": "respond_or_execute",
                    "success_criteria": {"task_decomposed": True},
                }
            ],
            repeated_failure_count=repeated,
            failure_fingerprint=fingerprint,
        )

    if status in {"failed", "blocked"} and _looks_retryable(failure_text):
        return TaskResolution(
            kind="retry",
            reason="The failure looks transient or route-related, so a later bounded retry is reasonable.",
            confidence=0.7,
            next_status="failed",
            repeated_failure_count=repeated,
            failure_fingerprint=fingerprint,
        )

    return TaskResolution(
        kind="blocked",
        reason="The task should stay blocked until a later review or explicit intervention changes conditions.",
        confidence=0.7,
        next_status="blocked",
        repeated_failure_count=repeated,
        failure_fingerprint=fingerprint,
    )


def _looks_like_clarification_task(*, title: str, description: str) -> bool:
    text = " ".join(part.strip().lower() for part in (title, description) if part.strip())
    if not text:
        return False
    hints = [
        "clarif",
        "confirm",
        "choose",
        "preference",
        "needs your input",
        "what should i",
        "principal",
    ]
    return any(hint in text for hint in hints)


def _looks_like_multistage_task(*, title: str, description: str) -> bool:
    text = " ".join(part.strip().lower() for part in (title, description) if part.strip())
    if not text:
        return False
    stage_pairs = [
        ("inspect", "patch"),
        ("analyze", "patch"),
        ("research", "implement"),
        ("review", "implement"),
        ("inspect", "validate"),
        ("patch", "validate"),
        ("implement", "validate"),
        ("find", "fix"),
    ]
    if any(all(word in text for word in pair) for pair in stage_pairs):
        return True
    return any(separator in text for separator in (" then ", " and then ", " followed by ", " before "))


def _looks_retryable(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    retryable_markers = [
        "timeout",
        "timed out",
        "connection",
        "rate limit",
        "temporarily",
        "provider_execution_failed",
        "missing_provider",
    ]
    return any(marker in normalized for marker in retryable_markers)


def _normalize_failure_reason(text: str) -> str:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return ""
    normalized = _UUIDISH_PATTERN.sub("<id>", normalized)
    normalized = _INTEGER_PATTERN.sub("<n>", normalized)
    return " ".join(normalized.split())


def _count_consecutive_matching_failures(attempts: list[dict[str, Any]], fingerprint: str) -> int:
    if not fingerprint:
        return 0
    relevant = sorted(
        [payload for payload in attempts if str(payload.get("outcome") or "").strip().lower() == "failed"],
        key=lambda payload: str(payload.get("ended_at") or payload.get("started_at") or ""),
        reverse=True,
    )
    total = 0
    for payload in relevant:
        attempt_text = _normalize_failure_reason(
            str(payload.get("degraded_reason") or payload.get("result_summary") or payload.get("failure_kind") or "")
        )
        if attempt_text != fingerprint:
            break
        total += 1
    return total


__all__ = ["TaskResolution", "determine_task_resolution"]
