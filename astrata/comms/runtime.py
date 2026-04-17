"""Stateful lane runtime for Prime and Local conversations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from astrata.agents import DurableAgentRegistry
from astrata.comms.intake import MessageIntake, RequestSpec, materialize_inbound_message
from astrata.comms.lanes import PrincipalMessageLane
from astrata.config.settings import Settings
from astrata.controllers.base import ControllerEnvelope
from astrata.controllers.coordinator import CoordinatorController
from astrata.local.strata_endpoint import StrataEndpointService
from astrata.memory import build_memory_augmented_request, default_memory_store_path
from astrata.providers.base import Message
from astrata.providers.registry import ProviderRegistry, build_default_registry
from astrata.records.communications import CommunicationRecord
from astrata.routing.advisor import RoutePerformanceAdvisor
from astrata.routing.policy import RouteChooser
from astrata.scheduling.quota import QuotaPolicy, default_source_limits
from astrata.storage.db import AstrataDatabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

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
        self.agent_registry = DurableAgentRegistry.from_settings(settings)
        self.agent_registry.ensure_bootstrap_agents()
        self.intake = MessageIntake(project_root=settings.paths.project_root)
        self.principal_lane = PrincipalMessageLane(db=db)
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
            for message in self.principal_lane.list_messages(
                recipient=normalized_lane,
                include_acknowledged=False,
            )
            if message.sender in {"principal", "operator"} and message.status in {"queued", "delivered"}
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
        conversation_id = str(message.conversation_id or self.principal_lane.default_conversation_id("prime"))
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
            return self._handle_prime_unavailable(
                message,
                coordination=coordination,
                unavailable_reason=decision.reason,
                security_level=self._message_security_level(message),
            )
        provider = self.registry.get_provider(route.provider)
        if provider is None:
            return self._handle_prime_unavailable(
                message,
                coordination=coordination,
                unavailable_reason="missing_provider",
                security_level=self._message_security_level(message),
            )
        request = build_memory_augmented_request(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are Prime, the principal-facing conversational interface for Astrata. "
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
            memory_store_path=default_memory_store_path(data_dir=self.settings.paths.data_dir),
            memory_query=str(message.payload.get("message") or ""),
            accessor="local",
            destination="remote",
        )
        try:
            try:
                setattr(provider, "last_request", request)
            except Exception:
                pass
            response = provider.complete(request)
        except Exception as exc:
            return self._handle_prime_unavailable(
                message,
                coordination=coordination,
                unavailable_reason=str(exc),
                security_level=self._message_security_level(message),
            )
        return self._emit_reply(
            message,
            lane="prime",
            content=response.content.strip() or "I’m here.",
            action="direct_reply",
            detail=route.reason,
            coordination=coordination,
        )

    def _handle_prime_unavailable(
        self,
        message: CommunicationRecord,
        *,
        coordination: dict[str, Any] | None,
        unavailable_reason: str,
        security_level: str,
    ) -> LaneTurnResult:
        fallback = self.agent_registry.choose_fallback(
            unavailable_agent_id="prime",
            security_level=security_level,
        )
        if fallback is not None and fallback.agent_id == "reception":
            return self._attempt_agent_failover(
                message,
                coordination=coordination,
                unavailable_reason=unavailable_reason,
                security_level=security_level,
                agent_id="reception",
            )
        if fallback is not None and fallback.agent_id == "local":
            return self._attempt_local_failover(
                message,
                coordination=coordination,
                unavailable_reason=unavailable_reason,
                security_level=security_level,
            )
        detail = f"prime_unavailable:{unavailable_reason}"
        content = (
            "Prime is unavailable right now, and Astrata is operating in bounded continuity mode. "
            "I can preserve your message for Prime and explain current degradation, but I cannot safely "
            "take broader action from this lane at the moment."
        )
        return self._emit_reply(
            message,
            lane="fallback",
            content=content,
            action="degraded_reply",
            detail=detail,
            coordination=coordination,
        )

    def _attempt_agent_failover(
        self,
        message: CommunicationRecord,
        *,
        coordination: dict[str, Any] | None,
        unavailable_reason: str,
        security_level: str,
        agent_id: str,
    ) -> LaneTurnResult:
        agent = self.agent_registry.get(agent_id)
        if agent is None or agent.status not in {"active", "degraded"}:
            return self._attempt_local_failover(
                message,
                coordination=coordination,
                unavailable_reason=unavailable_reason,
                security_level=security_level,
            )
        binding = dict(agent.inference_binding or {})
        provider_name = str(binding.get("provider") or "").strip()
        provider = self.registry.get_provider(provider_name)
        if provider is None or not provider.is_configured():
            return self._attempt_local_failover(
                message,
                coordination=coordination,
                unavailable_reason=unavailable_reason,
                security_level=security_level,
            )
        conversation_id = str(message.conversation_id or self.principal_lane.default_conversation_id(agent_id))
        request = build_memory_augmented_request(
            messages=[
                Message(
                    role="system",
                    content=(
                        f"You are {agent.title}, Astrata's personal assistant and second point of contact when Prime is unavailable. "
                        "A handoff has occurred because Prime did not pick up the message. "
                        "Respond from your own frame and within your own permissions. "
                        "Do not assume Prime's responsibilities, identity, authority, or commitments. "
                        "Be honest about degraded conditions. Offer to queue or begin bounded work within your permissions."
                    ),
                ),
                *self._conversation_history(conversation_id=conversation_id, lane=agent_id),
                Message(
                    role="user",
                    content=(
                        f"Handoff notice: Prime was the intended recipient, but Prime is unavailable ({unavailable_reason}). "
                        f"Security level: {security_level}. "
                        "Respond as yourself, not as Prime. "
                        f"Please respond to the principal's message:\n\n{str(message.payload.get('message') or '')}"
                    ),
                ),
            ],
            model=None if provider_name == "cli" else str(binding.get("model") or ""),
            metadata={
                **({"cli_tool": str(binding.get("cli_tool") or "")} if binding.get("cli_tool") else {}),
                "conversation_id": conversation_id,
                "task_class": "general",
                "fallback_agent_id": agent_id,
            },
            memory_store_path=default_memory_store_path(data_dir=self.settings.paths.data_dir),
            memory_query=str(message.payload.get("message") or ""),
            accessor="local",
            destination="remote" if agent.permissions_profile.get("network") else "local",
        )
        try:
            response = provider.complete(request)
        except Exception:
            return self._attempt_local_failover(
                message,
                coordination=coordination,
                unavailable_reason=unavailable_reason,
                security_level=security_level,
            )
        return self._emit_reply(
            message,
            lane=agent_id,
            content=response.content.strip() or f"{agent.title} is here while Prime is unavailable.",
            action="failover_reply",
            detail=f"prime_unavailable:{unavailable_reason}",
            coordination=coordination,
            extra_payload={
                "handoff_occurred": True,
                "intended_recipient": "prime",
                "responding_agent": agent_id,
                "response_frame": "fallback_continuity",
            },
        )

    def _attempt_local_failover(
        self,
        message: CommunicationRecord,
        *,
        coordination: dict[str, Any] | None,
        unavailable_reason: str,
        security_level: str,
    ) -> LaneTurnResult:
        conversation_id = str(message.conversation_id or self.principal_lane.default_conversation_id("prime"))
        local_prompt = (
            "Prime is currently unavailable. You are Local acting as a continuity fallback for the principal. "
            "A handoff has occurred because Prime did not pick up the message. Respond from Local's own frame and permissions. "
            "Do not assume Prime's authority, role, or responsibilities. "
            "Be honest about degraded capability. If the message implies network work, unavailable remote knowledge, "
            "or higher-trust coordination, say so explicitly. If security level is sensitive or higher, keep the "
            "response local-only and avoid suggesting any cloud handoff."
        )
        try:
            reply = self.local_endpoint.chat(
                content=(
                    f"Handoff notice: Prime was the intended recipient, but Prime is unavailable ({unavailable_reason}). "
                    f"Security level: {security_level}. "
                    "Respond as Local, not as Prime. "
                    f"Please respond to the principal's message:\n\n{str(message.payload.get('message') or '')}"
                ),
                thread_id=conversation_id,
                response_budget="normal",
                system_prompt=local_prompt,
            )
        except Exception:
            return self._emit_reply(
                message,
                lane="fallback",
                content=(
                    "Prime is unavailable, and Local is also not ready. "
                    "I’ve kept your message in the durable queue so the system can pick it up when a capable agent returns."
                ),
                action="degraded_reply",
                detail=f"prime_unavailable:{unavailable_reason}",
                coordination=coordination,
            )
        return self._emit_reply(
            message,
            lane="local",
            content=reply.content.strip() or "Local is here, but capability is degraded while Prime is unavailable.",
            action="failover_reply",
            detail=f"prime_unavailable:{unavailable_reason}",
            coordination=coordination,
            extra_payload={
                "handoff_occurred": True,
                "intended_recipient": "prime",
                "responding_agent": "local",
                "response_frame": "fallback_continuity",
            },
        )

    def _handle_local_reply(self, message: CommunicationRecord) -> LaneTurnResult:
        conversation_id = str(message.conversation_id or self.principal_lane.default_conversation_id("local"))
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
        extra_payload: dict[str, Any] | None = None,
    ) -> LaneTurnResult:
        conversation_id = str(message.conversation_id or self.principal_lane.default_conversation_id(lane))
        reply = self.principal_lane.send(
            sender=lane,
            recipient="principal",
            conversation_id=conversation_id,
            kind="response",
            intent="lane_runtime_reply",
            payload={
                "message": content,
                "action": action,
                "detail": detail,
                "source_communication_id": message.communication_id,
                "generated_at": _now_iso(),
                **dict(extra_payload or {}),
            },
            related_task_ids=list(message.related_task_ids or []),
            related_attempt_ids=list(message.related_attempt_ids or []),
        )
        self.principal_lane.resolve(message.communication_id)
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
        for record in sorted(
            self.db.iter_records("communications"),
            key=lambda item: str(item.get("created_at") or ""),
        ):
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
        pending = sum(1 for task in self.db.iter_records("tasks") if task.get("status") == "pending")
        complete = sum(1 for task in self.db.iter_records("tasks") if task.get("status") == "complete")
        running = sum(
            1 for attempt in self.db.iter_records("attempts") if attempt.get("outcome") in {"running", "started"}
        )
        return (
            f"Astrata currently has {pending} pending task"
            f"{'' if pending == 1 else 's'}, {complete} completed, and {running} active attempt"
            f"{'' if running == 1 else 's'}."
        )

    def _message_security_level(self, message: CommunicationRecord) -> str:
        payload = dict(message.payload or {})
        return str(payload.get("security_level") or payload.get("confidentiality") or "normal").strip().lower()

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
