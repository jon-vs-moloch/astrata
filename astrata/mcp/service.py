"""Bridge service for Astrata's inbound and outbound MCP posture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from astrata.comms.lanes import HandoffLane
from astrata.controllers.external_agent import ExternalAgentBinding
from astrata.mcp.models import MCPBridgeBinding, MCPBridgeEvent
from astrata.records.handoffs import HandoffRecord


class MCPBridgeService:
    """Keeps MCP bindings legible and translates them into Astrata-native handoffs."""

    def __init__(self, *, state_path: Path) -> None:
        self._state_path = state_path
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls, settings) -> "MCPBridgeService":
        return cls(state_path=settings.paths.data_dir / "mcp_bridges.json")

    def register_binding(self, binding: MCPBridgeBinding) -> MCPBridgeBinding:
        payload = self._load()
        bindings = dict(payload.get("bindings") or {})
        updated = binding.model_copy(update={"updated_at": binding.updated_at})
        bindings[updated.bridge_id] = updated.model_dump(mode="json")
        payload["bindings"] = bindings
        self._save(payload)
        return updated

    def get_binding(self, bridge_id: str) -> MCPBridgeBinding | None:
        payload = self._load()
        record = dict(payload.get("bindings", {}).get(bridge_id) or {})
        if not record:
            return None
        return MCPBridgeBinding(**record)

    def list_bindings(self, *, direction: str | None = None) -> list[MCPBridgeBinding]:
        payload = self._load()
        bindings = [MCPBridgeBinding(**record) for record in dict(payload.get("bindings") or {}).values()]
        if direction:
            normalized = str(direction).strip().lower()
            bindings = [binding for binding in bindings if binding.direction == normalized]
        return sorted(bindings, key=lambda binding: (binding.direction, binding.agent_id, binding.created_at))

    def list_events(self, *, bridge_id: str | None = None) -> list[MCPBridgeEvent]:
        payload = self._load()
        events = [MCPBridgeEvent(**record) for record in list(payload.get("events") or [])]
        if bridge_id:
            events = [event for event in events if event.bridge_id == bridge_id]
        return sorted(events, key=lambda event: event.created_at)

    def external_agent_binding(self, bridge_id: str) -> ExternalAgentBinding:
        binding = self._require_binding(bridge_id)
        return ExternalAgentBinding(
            agent_id=binding.agent_id,
            transport=f"mcp:{binding.transport}",
            role=binding.role,
            can_be_prime=binding.can_be_prime,
            can_receive_subtasks=binding.can_receive_subtasks,
            online=binding.online,
            accepts_sensitive_payloads=binding.accepts_sensitive_payloads,
            capabilities=tuple(dict.fromkeys([*binding.allowed_tools, *binding.exposed_resources])),
            notes=binding.notes,
        )

    def open_inbound_handoff(
        self,
        *,
        bridge_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        task_id: str,
        target_controller: str = "prime",
        delegation_mode: str = "direct",
        envelope: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HandoffRecord:
        binding = self._require_binding(bridge_id)
        lane = HandoffLane(lane_id=f"mcp:{bridge_id}:inbound")
        handoff = lane.open_handoff(
            source_controller=f"external:{binding.agent_id}",
            target_controller=target_controller,
            task_id=task_id,
            envelope={
                **dict(envelope or {}),
                "bridge_id": bridge_id,
                "tool_name": tool_name,
                "security_level": str((metadata or {}).get("security_level") or (envelope or {}).get("security_level") or "normal"),
            },
            route={
                "provider": "mcp",
                "bridge_id": bridge_id,
                "transport": binding.transport,
                "direction": "inbound",
                "tool_name": tool_name,
            },
            source_decision={
                "status": "submitted",
                "reason": f"Inbound MCP tool `{tool_name}` requested by `{binding.agent_id}`.",
            },
            metadata={
                **dict(metadata or {}),
                "bridge_direction": "inbound",
                "tool_name": tool_name,
                "arguments": dict(arguments or {}),
                "agent_id": binding.agent_id,
                "transport": binding.transport,
            },
        ).model_copy(
            update={
                "execution_boundary": "external",
                "bridge_id": bridge_id,
                "delegation_mode": delegation_mode,
            }
        )
        self._append_event(
            MCPBridgeEvent(
                bridge_id=bridge_id,
                direction="inbound",
                event_type="tool_call_received",
                task_id=task_id,
                tool_name=tool_name,
                payload={"arguments": dict(arguments or {}), "target_controller": target_controller},
            )
        )
        return handoff

    def open_outbound_handoff(
        self,
        *,
        bridge_id: str,
        task_id: str,
        source_controller: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        envelope: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        delegation_mode: str = "direct",
    ) -> HandoffRecord:
        binding = self._require_binding(bridge_id)
        lane = HandoffLane(lane_id=f"mcp:{bridge_id}:outbound")
        handoff = lane.open_handoff(
            source_controller=source_controller,
            target_controller=f"external:{binding.agent_id}",
            task_id=task_id,
            envelope={
                **dict(envelope or {}),
                "bridge_id": bridge_id,
                "tool_name": tool_name,
            },
            route={
                "provider": "mcp",
                "bridge_id": bridge_id,
                "transport": binding.transport,
                "direction": "outbound",
                "tool_name": tool_name,
            },
            source_decision={
                "status": "delegated",
                "reason": f"Astrata delegated tool `{tool_name}` to external MCP agent `{binding.agent_id}`.",
            },
            metadata={
                **dict(metadata or {}),
                "bridge_direction": "outbound",
                "tool_name": tool_name,
                "arguments": dict(arguments or {}),
                "agent_id": binding.agent_id,
                "transport": binding.transport,
            },
        ).model_copy(
            update={
                "execution_boundary": "external",
                "bridge_id": bridge_id,
                "delegation_mode": delegation_mode,
            }
        )
        self._append_event(
            MCPBridgeEvent(
                bridge_id=bridge_id,
                direction="outbound",
                event_type="tool_call_requested",
                task_id=task_id,
                tool_name=tool_name,
                payload={"arguments": dict(arguments or {}), "source_controller": source_controller},
            )
        )
        return handoff

    def _append_event(self, event: MCPBridgeEvent) -> None:
        payload = self._load()
        events = list(payload.get("events") or [])
        events.append(event.model_dump(mode="json"))
        payload["events"] = events[-128:]
        self._save(payload)

    def _require_binding(self, bridge_id: str) -> MCPBridgeBinding:
        binding = self.get_binding(bridge_id)
        if binding is None:
            raise KeyError(f"Unknown MCP bridge binding: {bridge_id}")
        return binding

    def _load(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"bindings": {}, "events": []}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"bindings": {}, "events": []}

    def _save(self, payload: dict[str, Any]) -> None:
        self._state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
