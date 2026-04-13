"""Outbound MCP client helpers for Astrata."""

from __future__ import annotations

import json
import urllib.request
from typing import Any
from uuid import uuid4

from astrata.mcp.models import MCPBridgeBinding
from astrata.mcp.service import MCPBridgeService


class MCPClientAdapter:
    """Thin outbound adapter that turns Astrata handoffs into MCP-style requests."""

    def __init__(self, *, bridge_service: MCPBridgeService) -> None:
        self._bridge_service = bridge_service

    def build_tool_call(
        self,
        *,
        bridge_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        binding = self._bridge_service.get_binding(bridge_id)
        if binding is None:
            raise KeyError(f"Unknown MCP bridge binding: {bridge_id}")
        return {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": dict(arguments or {}),
                "_meta": {
                    "bridge_id": bridge_id,
                    "agent_id": binding.agent_id,
                    "direction": binding.direction,
                    "transport": binding.transport,
                },
            },
        }

    def call_tool_http(
        self,
        *,
        bridge_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        binding = self._require_http_binding(bridge_id)
        payload = self.build_tool_call(bridge_id=bridge_id, tool_name=tool_name, arguments=arguments)
        request = urllib.request.Request(
            binding.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw)

    def _require_http_binding(self, bridge_id: str) -> MCPBridgeBinding:
        binding = self._bridge_service.get_binding(bridge_id)
        if binding is None:
            raise KeyError(f"Unknown MCP bridge binding: {bridge_id}")
        if binding.transport != "streamable_http":
            raise ValueError(f"MCP bridge `{bridge_id}` is not configured for streamable HTTP.")
        if not binding.endpoint:
            raise ValueError(f"MCP bridge `{bridge_id}` does not declare an endpoint.")
        return binding
