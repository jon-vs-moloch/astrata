"""Outbound MCP JSON-RPC helpers."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from astrata.mcp.service import MCPBridgeService


class MCPClientAdapter:
    def __init__(self, *, bridge_service: MCPBridgeService) -> None:
        self.bridge_service = bridge_service

    def build_tool_call(self, *, bridge_id: str, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        binding = self.bridge_service.get_binding(bridge_id)
        if binding is None:
            raise KeyError(f"Unknown MCP bridge `{bridge_id}`.")
        return {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": dict(arguments or {}),
                "_meta": {"bridge_id": bridge_id, "transport": binding.transport},
            },
        }

