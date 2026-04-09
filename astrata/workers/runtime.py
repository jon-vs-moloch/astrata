"""Durable worker delegation backed by concrete provider lanes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from astrata.comms.lanes import OperatorMessageLane
from astrata.config.settings import Settings
from astrata.providers.base import CompletionRequest, Message
from astrata.providers.registry import ProviderRegistry, build_default_registry
from astrata.records.communications import CommunicationRecord
from astrata.storage.db import AstrataDatabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class WorkerDelegationResult:
    status: str
    worker_id: str
    communication_id: str
    result_message: dict[str, Any] | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "worker_id": self.worker_id,
            "communication_id": self.communication_id,
            "result_message": self.result_message,
            "detail": self.detail,
        }


class WorkerRuntime:
    """Execute delegated work on specific provider-backed worker lanes."""

    def __init__(
        self,
        *,
        settings: Settings,
        db: AstrataDatabase,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.registry = registry or build_default_registry()
        self.operator_lane = OperatorMessageLane(db=db)

    def process_pending(self, *, worker_id: str, limit: int = 5) -> list[dict[str, Any]]:
        pending = [
            message
            for message in self.operator_lane.list_messages(
                recipient=worker_id,
                include_acknowledged=False,
            )
            if message.status in {"queued", "delivered"}
            and message.intent == "worker_delegation_request"
        ][: max(1, limit)]
        return [self.handle_message(message).as_dict() for message in pending]

    def handle_message(self, message: CommunicationRecord) -> WorkerDelegationResult:
        worker_id = str(message.recipient or "").strip().lower()
        payload = dict(message.payload or {})
        route = dict(payload.get("route") or {})
        delegation_kind = str(payload.get("delegation_kind") or "message_task").strip().lower()
        if delegation_kind != "message_task":
            return self._emit_result(
                message,
                worker_id=worker_id,
                detail="unsupported_delegation_kind",
                payload={
                    "status": "unsupported",
                    "reason": f"Unsupported delegation kind `{delegation_kind or 'unknown'}`.",
                    "task_id": payload.get("task_id"),
                    "route": route,
                },
            )
        provider = self.registry.get_provider(str(route.get("provider") or "").strip() or None)
        if provider is None:
            return self._emit_result(
                message,
                worker_id=worker_id,
                detail="missing_provider",
                payload={
                    "status": "blocked",
                    "reason": "Delegated worker could not resolve the requested provider.",
                    "task_id": payload.get("task_id"),
                    "route": route,
                },
            )
        try:
            response = provider.complete(
                CompletionRequest(
                    model=route.get("model"),
                    messages=self._delegated_message_task_prompt(payload),
                    metadata={
                        **({"cli_tool": route.get("cli_tool")} if route.get("cli_tool") else {}),
                        "delegation_kind": delegation_kind,
                        "worker_id": worker_id,
                        "task_class": "delegated_message_task",
                    },
                )
            )
        except Exception as exc:
            return self._emit_result(
                message,
                worker_id=worker_id,
                detail="provider_execution_failed",
                payload={
                    "status": "failed",
                    "reason": str(exc),
                    "task_id": payload.get("task_id"),
                    "route": route,
                },
            )
        return self._emit_result(
            message,
            worker_id=worker_id,
            detail=str(route.get("reason") or "delegated_route"),
            payload={
                "status": "applied",
                "task_id": payload.get("task_id"),
                "route": {
                    **route,
                    "provider": response.provider,
                    "model": response.model,
                },
                "raw_content": response.content,
                "delegation_kind": delegation_kind,
                "delegated_by": str(message.sender or ""),
                "source_communication_id": message.communication_id,
            },
        )

    def _emit_result(
        self,
        message: CommunicationRecord,
        *,
        worker_id: str,
        detail: str,
        payload: dict[str, Any],
    ) -> WorkerDelegationResult:
        result = self.operator_lane.send(
            sender=worker_id,
            recipient="astrata",
            conversation_id=str(message.conversation_id or self.operator_lane.default_conversation_id(worker_id)),
            kind="result",
            intent="worker_delegation_result",
            payload={
                **dict(payload),
                "worker_id": worker_id,
                "detail": detail,
                "generated_at": _now_iso(),
            },
            related_task_ids=list(message.related_task_ids or []),
            related_attempt_ids=list(message.related_attempt_ids or []),
        )
        self.operator_lane.resolve(message.communication_id)
        return WorkerDelegationResult(
            status="ok",
            worker_id=worker_id,
            communication_id=message.communication_id,
            result_message=result.model_dump(mode="json"),
            detail=detail,
        )

    def _delegated_message_task_prompt(self, payload: dict[str, Any]) -> list[Message]:
        task_payload = dict(payload.get("task_payload") or {})
        candidate = {
            "title": payload.get("title"),
            "description": payload.get("description"),
            "task_id": payload.get("task_id"),
            "completion_policy": task_payload.get("completion_policy"),
            "provenance": task_payload.get("provenance"),
        }
        return [
            Message(
                role="system",
                content=(
                    "You are a delegated Astrata worker. "
                    "Return strict JSON with top-level keys `operator_response`, `followup_tasks`, and `artifact`. "
                    "`operator_response` must be a concise message for the operator. "
                    "`followup_tasks` should be an array of at most 4 concrete governed tasks only when genuinely helpful. "
                    "When a task needs decomposition, prefer multiple oneshottable leaf tasks with optional `task_id_hint`, "
                    "`depends_on`, `parallelizable`, and `route_preferences` fields rather than one oversized task. "
                    "`artifact` should be null or a compact object with `title`, `summary`, optional `confidence`, and optional `findings`."
                ),
            ),
            Message(role="user", content=str(payload.get("message") or "")),
            Message(role="user", content=f"Delegated task context:\n{candidate}"),
        ]


def worker_id_for_route(route: dict[str, Any]) -> str:
    cli_tool = str(route.get("cli_tool") or "").strip().lower()
    provider = str(route.get("provider") or "").strip().lower()
    model = str(route.get("model") or "").strip().lower()
    suffix = ""
    if model:
        normalized_model = "".join(char if char.isalnum() else "-" for char in model).strip("-")
        if normalized_model:
            suffix = f".{normalized_model[:48]}"
    if cli_tool:
        return f"worker.{cli_tool}{suffix}"
    return f"worker.{provider or 'default'}{suffix}"
