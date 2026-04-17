"""MCP JSON-RPC handlers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from astrata.mcp.relay import HostedMCPRelayService
from astrata.mcp.service import MCPBridgeService


class MCPJSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


def handle_jsonrpc_message(
    *,
    bridge_service: MCPBridgeService,
    payload: MCPJSONRPCRequest,
    client_host: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    if payload.method == "tools/call":
        params = dict(payload.params or {})
        args = dict(params.get("arguments") or {})
        bridge_id = str(args.get("bridge_id") or params.get("_meta", {}).get("bridge_id") or "")
        if not bridge_id:
            raise HTTPException(status_code=400, detail="Missing bridge_id")
        handoff = bridge_service.open_inbound_handoff(
            bridge_id=bridge_id,
            tool_name=str(params.get("name") or args.get("tool_name") or "tool"),
            arguments=args,
            task_id=str(args.get("task_id") or args.get("task") or ""),
            target_controller=str(args.get("target_controller") or "prime"),
            delegation_mode=str(args.get("delegation_mode") or "direct"),
            metadata={"client_host": client_host, "user_agent": user_agent},
        )
        return {"jsonrpc": "2.0", "id": payload.id, "result": {"handoff": handoff.model_dump(mode="json")}}
    if payload.method == "tools/list":
        return {"jsonrpc": "2.0", "id": payload.id, "result": {"tools": []}}
    raise HTTPException(status_code=400, detail=f"Unsupported MCP method: {payload.method}")


def handle_relay_jsonrpc_message(
    *,
    relay_service: HostedMCPRelayService,
    payload: MCPJSONRPCRequest,
    authorization: str | None = None,
) -> dict[str, Any]:
    params = dict(payload.params or {})
    authorization_token = str(authorization or "").removeprefix("Bearer ").strip()
    auth_context: dict[str, Any] = {"mode": "development_profile_token"}
    if relay_service.account_registry is not None and authorization_token:
        resolved = relay_service.account_registry.resolve_oauth_access_token(authorization_token)
        if resolved.get("authorized"):
            auth_context = {"mode": "oauth_access_token", **resolved}
            requested_profile_id = str(params.get("profile_id") or "").strip()
            if requested_profile_id and requested_profile_id != resolved.get("profile_id"):
                raise HTTPException(status_code=403, detail="OAuth token is not bound to the requested relay profile")
            profile_id = str(resolved["profile_id"])
        else:
            profile_id = str(params.get("profile_id") or "default")
    else:
        profile_id = str(params.get("profile_id") or "default")
    profile = relay_service.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Unknown relay profile")
    expected = profile.auth_token
    if auth_context["mode"] != "oauth_access_token" and expected:
        if authorization_token != expected:
            raise HTTPException(status_code=401, detail="Invalid relay token")
    if payload.method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": payload.id,
            "result": {"tools": relay_service.connector_safe_tools(profile_id)},
        }
    if payload.method == "tools/call":
        result = relay_service.queue_tool_call(
            profile_id=profile_id,
            tool_name=str(params.get("name") or "tool"),
            arguments=dict(params.get("arguments") or {}),
            meta={**dict(params.get("_meta") or {}), "auth": auth_context},
        )
        return {"jsonrpc": "2.0", "id": payload.id, "result": result}
    raise HTTPException(status_code=400, detail=f"Unsupported relay method: {payload.method}")


def main() -> int:
    print("astrata-mcp exposes library handlers in this build.")
    return 0
