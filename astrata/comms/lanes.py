"""Minimal durable lanes for controller handoffs and principal messages."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from astrata.records.communications import CommunicationRecord
from astrata.records.handoffs import HandoffRecord
from astrata.storage.db import AstrataDatabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HandoffLane:
    """A tiny durable lane for controller-to-controller work transfer."""

    def __init__(self, lane_id: str = "local-execution") -> None:
        self.lane_id = lane_id

    def open_handoff(
        self,
        *,
        source_controller: str,
        target_controller: str,
        task_id: str,
        envelope: dict[str, Any],
        route: dict[str, Any],
        source_decision: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> HandoffRecord:
        payload = dict(metadata or {})
        payload.setdefault("lane_id", self.lane_id)
        return HandoffRecord(
            source_controller=source_controller,
            target_controller=target_controller,
            task_id=task_id,
            route=route,
            envelope=envelope,
            source_decision=source_decision,
            metadata=payload,
        )

    def respond(
        self,
        handoff: HandoffRecord,
        *,
        status: str,
        reason: str,
        target_decision: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> HandoffRecord:
        merged_metadata = dict(handoff.metadata)
        if metadata:
            merged_metadata.update(metadata)
        return handoff.model_copy(
            update={
                "status": status,
                "reason": reason,
                "target_decision": target_decision,
                "metadata": merged_metadata,
                "responded_at": _now_iso(),
            }
        )


class PrincipalMessageLane:
    """Durable local inbox/outbox for principal-facing Astrata messages."""

    def __init__(self, *, db: AstrataDatabase, channel: str = "principal") -> None:
        self._db = db
        self.channel = channel

    def send(
        self,
        *,
        sender: str,
        recipient: str = "principal",
        conversation_id: str = "",
        kind: str = "notice",
        intent: str = "",
        payload: dict[str, Any] | None = None,
        priority: int = 0,
        urgency: int = 0,
        related_task_ids: list[str] | None = None,
        related_attempt_ids: list[str] | None = None,
    ) -> CommunicationRecord:
        record = CommunicationRecord(
            conversation_id=conversation_id
            or self.default_conversation_id(recipient if sender in {"principal", "operator"} else sender),
            channel=self.channel,
            kind=kind,
            sender=sender,
            recipient=recipient,
            intent=intent,
            status="delivered",
            payload=dict(payload or {}),
            priority=priority,
            urgency=urgency,
            related_task_ids=list(related_task_ids or []),
            related_attempt_ids=list(related_attempt_ids or []),
            delivered_at=_now_iso(),
        )
        self._db.upsert_communication(record)
        return record

    def default_conversation_id(self, lane: str) -> str:
        normalized = str(lane or "system").strip().lower() or "system"
        return f"lane:{normalized}:default"

    def list_messages(
        self,
        *,
        recipient: str = "principal",
        include_acknowledged: bool = True,
    ) -> list[CommunicationRecord]:
        channels = {self.channel}
        if self.channel == "principal":
            channels.add("operator")
        recipients = {recipient}
        if recipient == "principal":
            recipients.add("operator")
        records = [
            CommunicationRecord(**payload)
            for payload in self._db.iter_records("communications")
            if payload.get("channel") in channels and payload.get("recipient") in recipients
        ]
        if not include_acknowledged:
            records = [record for record in records if record.status not in {"acknowledged", "resolved"}]
        return sorted(records, key=lambda record: record.created_at)

    def acknowledge(self, communication_id: str) -> CommunicationRecord | None:
        return self._update_status(
            communication_id,
            status="acknowledged",
            timestamp_field="acknowledged_at",
        )

    def resolve(self, communication_id: str) -> CommunicationRecord | None:
        return self._update_status(
            communication_id,
            status="resolved",
            timestamp_field="resolved_at",
        )

    def get_message(self, communication_id: str) -> CommunicationRecord | None:
        channels = {self.channel}
        if self.channel == "principal":
            channels.add("operator")
        payload = self._db.get_record("communications", "communication_id", communication_id)
        if payload is None or payload.get("channel") not in channels:
            return None
        return CommunicationRecord(**payload)

    def _update_status(
        self,
        communication_id: str,
        *,
        status: str,
        timestamp_field: str,
    ) -> CommunicationRecord | None:
        record = self.get_message(communication_id)
        if record is None:
            return None
        updated = record.model_copy(
            update={
                "status": status,
                timestamp_field: _now_iso(),
            }
        )
        self._db.upsert_communication(updated)
        return updated


# TODO: Remove the legacy `operator` compatibility alias and fallback channel handling
# once all callers, stored records, and UI consumers have fully migrated to `principal`.
# Backwards-compatible alias while older imports migrate.
OperatorMessageLane = PrincipalMessageLane
