"""Local MCP bridge registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from astrata.controllers.external_agent import ExternalAgentBinding
from astrata.mcp.models import MCPBridgeBinding, MCPBridgeEvent
from astrata.records.handoffs import HandoffRecord


class MCPBridgeService:
    def __init__(self, *, state_path: Path) -> None:
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def register_binding(self, binding: MCPBridgeBinding) -> MCPBridgeBinding:
        payload = self._load()
        bindings = dict(payload.get("bindings") or {})
        bindings[binding.bridge_id] = binding.model_dump(mode="json")
        payload["bindings"] = bindings
        self._save(payload)
        return binding

    def get_binding(self, bridge_id: str) -> MCPBridgeBinding | None:
        raw = dict(self._load().get("bindings") or {}).get(bridge_id)
        return MCPBridgeBinding(**raw) if isinstance(raw, dict) else None

    def list_bindings(self, *, direction: str | None = None) -> list[MCPBridgeBinding]:
        bindings = [
            MCPBridgeBinding(**raw)
            for raw in dict(self._load().get("bindings") or {}).values()
            if isinstance(raw, dict)
        ]
        if direction:
            bindings = [binding for binding in bindings if binding.direction == direction]
        return sorted(bindings, key=lambda item: item.created_at)

    def list_events(self, *, bridge_id: str | None = None) -> list[MCPBridgeEvent]:
        events = [
            MCPBridgeEvent(**raw)
            for raw in list(self._load().get("events") or [])
            if isinstance(raw, dict)
        ]
        if bridge_id:
            events = [event for event in events if event.bridge_id == bridge_id]
        return sorted(events, key=lambda item: item.created_at)

    def open_inbound_handoff(
        self,
        *,
        bridge_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        task_id: str | None = None,
        target_controller: str = "prime",
        delegation_mode: str = "direct",
        metadata: dict[str, Any] | None = None,
    ) -> HandoffRecord:
        binding = self.get_binding(bridge_id)
        if binding is None:
            raise KeyError(f"Unknown MCP bridge `{bridge_id}`.")
        args = dict(arguments or {})
        handoff = HandoffRecord(
            source_controller=f"external:{binding.agent_id}",
            target_controller=target_controller,
            task_id=task_id or str(args.get("task_id") or args.get("task") or "remote-request"),
            execution_boundary="external",
            bridge_id=bridge_id,
            delegation_mode=delegation_mode,  # type: ignore[arg-type]
            metadata={"tool_name": tool_name, "arguments": args, **dict(metadata or {})},
        )
        self._record_event(
            MCPBridgeEvent(
                bridge_id=bridge_id,
                event_type="tool_call_received",
                payload={"tool_name": tool_name, "arguments": args, "handoff_id": handoff.handoff_id},
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
        delegation_mode: str = "direct",
    ) -> HandoffRecord:
        binding = self.get_binding(bridge_id)
        if binding is None:
            raise KeyError(f"Unknown MCP bridge `{bridge_id}`.")
        handoff = HandoffRecord(
            source_controller=source_controller,
            target_controller=f"external:{binding.agent_id}",
            task_id=task_id,
            execution_boundary="external",
            bridge_id=bridge_id,
            delegation_mode=delegation_mode,  # type: ignore[arg-type]
            route={"provider": "mcp", "bridge_id": bridge_id, "transport": binding.transport},
            metadata={"tool_name": tool_name, "arguments": dict(arguments or {})},
        )
        self._record_event(
            MCPBridgeEvent(
                bridge_id=bridge_id,
                event_type="tool_call_requested",
                payload={"tool_name": tool_name, "arguments": dict(arguments or {}), "handoff_id": handoff.handoff_id},
            )
        )
        return handoff

    def external_agent_binding(self, bridge_id: str) -> ExternalAgentBinding:
        binding = self.get_binding(bridge_id)
        if binding is None:
            raise KeyError(f"Unknown MCP bridge `{bridge_id}`.")
        return ExternalAgentBinding(
            agent_id=binding.agent_id,
            transport=binding.transport,
            role=binding.role,  # type: ignore[arg-type]
            can_be_prime=binding.can_be_prime,
            can_receive_subtasks=binding.can_receive_subtasks,
            capabilities=tuple(binding.allowed_tools),
        )

    def _record_event(self, event: MCPBridgeEvent) -> None:
        payload = self._load()
        events = list(payload.get("events") or [])
        events.append(event.model_dump(mode="json"))
        payload["events"] = events[-500:]
        self._save(payload)

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"bindings": {}, "events": []}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"bindings": {}, "events": []}
        if not isinstance(payload, dict):
            return {"bindings": {}, "events": []}
        payload.setdefault("bindings", {})
        payload.setdefault("events", [])
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

