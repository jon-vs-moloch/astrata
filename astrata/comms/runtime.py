"""Stateful lane runtime for Prime and Local conversations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from astrata.comms.intake import MessageIntake, RequestSpec, materialize_inbound_message
from astrata.comms.lanes import OperatorMessageLane
from astrata.config.settings import Settings
from astrata.controllers.base import ControllerEnvelope
from astrata.controllers.coordinator import CoordinatorController
from astrata.local.strata_endpoint import StrataEndpointService
from astrata.loop0.runner import _now_iso
from astrata.providers.base import CompletionRequest, Message
from astrata.providers.registry import ProviderRegistry, build_default_registry
from astrata.records.communications import CommunicationRecord
from astrata.routing.advisor import RoutePerformanceAdvisor
from astrata.routing.policy import RouteChooser
from astrata.scheduling.quota import QuotaPolicy, default_source_limits
from astrata.storage.db import AstrataDatabase


@dataclass(frozen=True)
class LaneTurnResult:
    status: str
    lane: str
    communication_id: str
    conversation_id: str
    action: str
    reply: dict[str, Any] | None = None
    materialized: dict[str, Any] | None = None
    coordination: dict[str, Any] | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "lane": self.lane,
            "communication_id": self.communication_id,
            "conversation_id": self.conversation_id,
            "action": self.action,
            "reply": self.reply,
            "materialized": self.materialized,
            "coordination": self.coordination,
            "detail": self.detail,
        }


class LaneRuntime:
    """Own conversational turns for user-facing lanes."""

    def __init__(
        self,
        *,
        settings: Settings,
        db: AstrataDatabase,
        registry: ProviderRegistry | None = None,
        local_endpoint: StrataEndpointService | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.registry = registry or build_default_registry()
        self.intake = MessageIntake(project_root=settings.paths.project_root)
        self.operator_lane = OperatorMessageLane(db=db)
        self.router = RouteChooser(self.registry)
        limits = default_source_limits()
        limits["codex"] = settings.runtime_limits.codex_requests_per_hour
        limits["cli:codex-cli"] = settings.runtime_limits.codex_requests_per_hour
        limits["cli:kilocode"] = settings.runtime_limits.kilocode_requests_per_hour
        limits["cli:gemini-cli"] = settings.runtime_limits.gemini_requests_per_hour
        limits["cli:claude-code"] = settings.runtime_limits.claude_requests_per_hour
        limits["openai"] = settings.runtime_limits.openai_requests_per_hour
        limits["google"] = settings.runtime_limits.google_requests_per_hour
        limits["anthropic"] = settings.runtime_limits.anthropic_requests_per_hour
        limits["custom"] = settings.runtime_limits.custom_requests_per_hour
        self.coordinator = CoordinatorController(
            router=self.router,
            quota_policy=QuotaPolicy(db=db, limits_per_source=limits, registry=self.registry),
            route_advisor=RoutePerformanceAdvisor.from_data_dir(settings.paths.data_dir),
        )
        self.local_endpoint = local_endpoint or StrataEndpointService.from_settings(settings)

    def process_pending_turns(self, *, lane: str, limit: int = 5) -> list[dict[str, Any]]:
        normalized_lane = str(lane or "").strip().lower()
        if normalized_lane not in {"prime", "local"}:
            return []
        pending = [
            message
            for message in self.operator_lane.list_messages(
                recipient=normalized_lane,
                include_acknowledged=False,
            )
            if message.sender == "operator" and message.status in {"queued", "delivered"}
        ][: max(1, limit)]
        return [self.handle_message(message).as_dict() for message in pending]

    def handle_message(self, message: CommunicationRecord) -> LaneTurnResult:
        lane = str(message.recipient or "").strip().lower()
        request_spec = self.intake.build_request_spec(message)
        if self._can_answer_from_state(message, request_spec):
            reply_text = self._stateful_reply(message, request_spec)
            return self._emit_reply(message, lane=lane, content=reply_text, action="direct_reply", detail="stateful")
        if self._should_materialize_work(message, request_spec):
            materialized = materialize_inbound_message(
                db=self.db,
                intake=self.intake,
                message=message,
            )
            task_count = len(list(materialized.get("tasks") or []))
            reply_text = (
                f"I turned that into {task_count} governed work item"
                f"{'' if task_count == 1 else 's'} and attached it to this conversation."
            )
            result = self._emit_reply(
                message,
                lane=lane,
                content=reply_text,
                action="materialize_work",
                detail=f"tasks:{task_count}",
            )
            return LaneTurnResult(
                **{
                    **result.as_dict(),
                    "materialized": materialized,
                }
            )
        if lane == "local":
            return self._handle_local_reply(message)
        return self._handle_prime_reply(message)

    def _handle_prime_reply(self, message: CommunicationRecord) -> LaneTurnResult:
        conversation_id = str(message.conversation_id or self.operator_lane.default_conversation_id("prime"))
        envelope = ControllerEnvelope(
            controller_id="lane-runtime:prime",
            task_id=f"lane-turn:{message.communication_id}",
            priority=max(2, int(message.priority or 0)),
            urgency=max(2, int(message.urgency or 0)),
            risk="moderate",
            metadata={
                "require_prime_route": True,
                "task_class": "general",
            },
        )
        decision, route = self.coordinator.coordinate(envelope)
        coordination = {
            "decision": decision.model_dump(mode="json"),
            "route": route.__dict__,
        }
        if decision.status != "accepted":
            detail = f"Prime is pacing right now: {decision.reason}"
            return self._emit_reply(
                message,
                lane="prime",
                content=detail,
                action="deferred",
                detail=decision.reason,
                coordination=coordination,
            )
        provider = self.registry.get_provider(route.provider)
        if provider is None:
            return self._emit_reply(
                message,
                lane="prime",
                content="Prime is available as a lane, but its inference route is not configured yet.",
                action="degraded_reply",
                detail="missing_provider",
                coordination=coordination,
            )
        request = CompletionRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are Prime, the user-facing conversational interface for Astrata. "
                        "Reply naturally and concisely. Preserve continuity with the ongoing conversation. "
                        "If the user is asking for execution or system changes, do not fabricate completion."
                    ),
                ),
                *self._conversation_history(conversation_id=conversation_id, lane="prime"),
                Message(role="user", content=str(message.payload.get('message') or "")),
            ],
            model=route.model,
            metadata={
                **({"cli_tool": route.cli_tool} if route.cli_tool else {}),
                "conversation_id": conversation_id,
                "task_class": "general",
            },
        )
        response = provider.complete(request)
        return self._emit_reply(
            message,
            lane="prime",
            content=response.content.strip() or "I’m here.",
            action="direct_reply",
            detail=route.reason,
            coordination=coordination,
        )

    def _handle_local_reply(self, message: CommunicationRecord) -> LaneTurnResult:
        conversation_id = str(message.conversation_id or self.operator_lane.default_conversation_id("local"))
        try:
            reply = self.local_endpoint.chat(
                content=str(message.payload.get("message") or ""),
                thread_id=conversation_id,
                response_budget="normal",
            )
        except Exception as exc:
            return self._emit_reply(
                message,
                lane="local",
                content=(
                    "Local is a real lane, but the local runtime is not ready right now. "
                    f"Error: {exc}"
                ),
                action="degraded_reply",
                detail=str(exc),
            )
        return self._emit_reply(
            message,
            lane="local",
            content=reply.content.strip() or "Local is ready.",
            action="direct_reply",
            detail=f"mode:{reply.mode}",
        )

    def _emit_reply(
        self,
        message: CommunicationRecord,
        *,
        lane: str,
        content: str,
        action: str,
        detail: str,
        coordination: dict[str, Any] | None = None,
    ) -> LaneTurnResult:
        conversation_id = str(message.conversation_id or self.operator_lane.default_conversation_id(lane))
        reply = self.operator_lane.send(
            sender=lane,
            recipient="operator",
            conversation_id=conversation_id,
            kind="response",
            intent="lane_runtime_reply",
            payload={
                "message": content,
                "action": action,
                "detail": detail,
                "source_communication_id": message.communication_id,
                "generated_at": _now_iso(),
            },
            related_task_ids=list(message.related_task_ids or []),
            related_attempt_ids=list(message.related_attempt_ids or []),
        )
        self.operator_lane.resolve(message.communication_id)
        return LaneTurnResult(
            status="ok",
            lane=lane,
            communication_id=message.communication_id,
            conversation_id=conversation_id,
            action=action,
            reply=reply.model_dump(mode="json"),
            coordination=coordination,
            detail=detail,
        )

    def _conversation_history(self, *, conversation_id: str, lane: str) -> list[Message]:
        history: list[Message] = []
        for record in sorted(self.db.list_records("communications"), key=lambda item: str(item.get("created_at") or "")):
            if str(record.get("conversation_id") or "") != conversation_id:
                continue
            sender = str(record.get("sender") or "").strip().lower()
            payload = dict(record.get("payload") or {})
            content = str(payload.get("message") or "").strip()
            if not content:
                continue
            role = "assistant" if sender == lane else "user"
            history.append(Message(role=role, content=content))
        return history[-8:]

    def _can_answer_from_state(self, message: CommunicationRecord, request_spec: RequestSpec) -> bool:
        raw = str(message.payload.get("message") or "").strip().lower()
        if raw in {"hi", "hello", "hey", "good morning", "good afternoon", "good evening"}:
            return True
        return any(
            token in raw
            for token in (
                "status",
                "what are you doing",
                "what's going on",
                "how are things",
                "how many tasks",
                "queue",
            )
        ) and request_spec.request_kind == "execution"

    def _stateful_reply(self, message: CommunicationRecord, request_spec: RequestSpec) -> str:
        raw = str(message.payload.get("message") or "").strip().lower()
        if raw in {"hi", "hello", "hey", "good morning", "good afternoon", "good evening"}:
            lane = str(message.recipient or "prime").strip().lower()
            label = "Prime" if lane == "prime" else "Local"
            return f"{label} is here. I can reply directly or turn work into governed tasks when it needs the machine."
        tasks = self.db.list_records("tasks")
        attempts = self.db.list_records("attempts")
        pending = sum(1 for task in tasks if task.get("status") == "pending")
        complete = sum(1 for task in tasks if task.get("status") == "complete")
        running = sum(1 for attempt in attempts if attempt.get("outcome") in {"running", "started"})
        return (
            f"Astrata currently has {pending} pending task"
            f"{'' if pending == 1 else 's'}, {complete} completed, and {running} active attempt"
            f"{'' if running == 1 else 's'}."
        )

    def _should_materialize_work(self, message: CommunicationRecord, request_spec: RequestSpec) -> bool:
        raw = str(message.payload.get("message") or "").strip().lower()
        if request_spec.needs_clarification:
            return False
        if request_spec.request_kind in {"review", "spec_hardening"}:
            return True
        if any(
            token in raw
            for token in (
                "implement",
                "build",
                "wire",
                "rewrite",
                "review",
                "audit",
                "fix",
                "strengthen",
                "change ",
                ".py",
                ".md",
                ".toml",
                ".json",
                "module",
                "file",
                "task",
                "queue",
            )
        ):
            return True
        return False
