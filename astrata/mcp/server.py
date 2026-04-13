"""Inbound HTTP MCP adapter for Astrata."""

from __future__ import annotations

import argparse
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
import uvicorn

from astrata.config.settings import load_settings
from astrata.mcp.models import HostedMCPRelayLink, HostedMCPRelayProfile, MCPBridgeBinding
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
    client_host: str = "unknown",
    user_agent: str = "",
) -> dict[str, Any]:
    if payload.method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": payload.id,
            "result": {
                "tools": [
                    {
                        "name": "submit_task",
                        "description": "Submit governed work into Astrata.",
                        "inputSchema": {"type": "object"},
                    },
                    {
                        "name": "delegate_subtasks",
                        "description": "Request Astrata to decompose and fan out bounded work.",
                        "inputSchema": {"type": "object"},
                    },
                    {
                        "name": "get_task_status",
                        "description": "Inspect status for a durable Astrata task.",
                        "inputSchema": {"type": "object"},
                    },
                ]
            },
        }
    if payload.method != "tools/call":
        raise HTTPException(status_code=400, detail={"error": f"Unsupported MCP method `{payload.method}`."})
    params = dict(payload.params or {})
    tool_name = str(params.get("name") or "").strip()
    arguments = dict(params.get("arguments") or {})
    meta = dict(params.get("_meta") or {})
    bridge_id = str(arguments.get("bridge_id") or meta.get("bridge_id") or "").strip()
    task_id = str(arguments.get("task_id") or "").strip() or f"mcp:{tool_name or 'request'}"
    if not bridge_id:
        raise HTTPException(status_code=400, detail={"error": "bridge_id is required in arguments or _meta."})
    handoff = bridge_service.open_inbound_handoff(
        bridge_id=bridge_id,
        tool_name=tool_name or "unknown_tool",
        arguments=arguments,
        task_id=task_id,
        target_controller=str(arguments.get("target_controller") or "prime"),
        delegation_mode=str(arguments.get("delegation_mode") or "direct"),
        envelope={
            "require_prime_route": bool(arguments.get("require_prime_route")),
            "security_level": str(arguments.get("security_level") or "normal"),
            "requested_by": client_host,
        },
        metadata={
            "remote_address": client_host,
            "user_agent": user_agent,
            "security_level": str(arguments.get("security_level") or "normal"),
        },
    )
    return {
        "jsonrpc": "2.0",
        "id": payload.id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": f"Accepted {tool_name or 'request'} into Astrata handoff {handoff.handoff_id}.",
                }
            ],
            "handoff": handoff.model_dump(mode="json"),
        },
    }


def handle_relay_jsonrpc_message(
    *,
    relay_service: HostedMCPRelayService,
    payload: MCPJSONRPCRequest,
    client_host: str = "unknown",
    user_agent: str = "",
    authorization: str = "",
) -> dict[str, Any]:
    params = dict(payload.params or {})
    meta = dict(params.get("_meta") or {})
    profile_id = str(params.get("profile_id") or meta.get("profile_id") or "").strip()
    if not profile_id:
        raise HTTPException(status_code=400, detail={"error": "profile_id is required in params or _meta."})
    try:
        relay_service.authorize_profile(profile_id=profile_id, authorization=authorization)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail={"error": str(exc)}) from exc
    if payload.method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": payload.id,
            "result": {
                "tools": relay_service.connector_tool_catalog(profile_id),
            },
        }
    if payload.method != "tools/call":
        raise HTTPException(status_code=400, detail={"error": f"Unsupported hosted relay MCP method `{payload.method}`."})
    tool_name = str(params.get("name") or "").strip()
    arguments = dict(params.get("arguments") or {})
    task_id = str(arguments.get("task_id") or "").strip() or f"relay:{profile_id}:{tool_name or 'request'}"
    result = relay_service.submit_connector_request(
        profile_id=profile_id,
        tool_name=tool_name or "unknown_tool",
        arguments=arguments,
        source_connector=str(meta.get("connector") or user_agent or client_host),
        bridge_id=str(meta.get("bridge_id") or arguments.get("bridge_id") or "").strip(),
        task_id=task_id,
        target_controller=str(arguments.get("target_controller") or "prime"),
    )
    delivery = result.get("delivery") or "queued"
    text = (
        f"Forwarded {tool_name or 'request'} into Astrata handoff {result['handoff']['handoff_id']}."
        if delivery == "forwarded" and result.get("handoff")
        else f"Queued {tool_name or 'request'} until Astrata's local relay link is available."
    )
    return {
        "jsonrpc": "2.0",
        "id": payload.id,
        "result": {
            "content": [{"type": "text", "text": text}],
            "delivery": delivery,
            "request": result.get("request"),
            "handoff": result.get("handoff"),
        },
    }


def create_app(*, bridge_service: MCPBridgeService | None = None) -> FastAPI:
    service = bridge_service or MCPBridgeService.from_settings(load_settings())
    relay_service = HostedMCPRelayService.from_settings(load_settings(), bridge_service=service)
    app = FastAPI(title="Astrata MCP Bridge", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "service": "astrata-mcp",
            "bindings": len(service.list_bindings()),
        }

    @app.get("/bridges")
    def bridges(direction: str | None = None) -> dict[str, Any]:
        return {
            "bindings": [binding.model_dump(mode="json") for binding in service.list_bindings(direction=direction)],
        }

    @app.post("/bridges/register")
    def register_bridge(binding: MCPBridgeBinding) -> dict[str, Any]:
        registered = service.register_binding(binding)
        return {"status": "registered", "binding": registered.model_dump(mode="json")}

    @app.get("/relay/status")
    def relay_status(profile_id: str | None = None) -> dict[str, Any]:
        return relay_service.telemetry_summary(profile_id=profile_id)

    @app.post("/relay/profiles/register")
    def register_relay_profile(profile: HostedMCPRelayProfile) -> dict[str, Any]:
        registered = relay_service.register_profile(profile)
        return {"status": "registered", "profile": registered.model_dump(mode="json")}

    @app.post("/relay/links/register")
    def register_relay_link(link: HostedMCPRelayLink) -> dict[str, Any]:
        registered = relay_service.register_local_link(link)
        return {"status": "registered", "link": registered.model_dump(mode="json")}

    @app.post("/relay/local/heartbeat")
    async def relay_local_heartbeat(request: Request) -> dict[str, Any]:
        payload = await request.json()
        return {
            "status": "accepted",
            "received": payload,
        }

    @app.post("/mcp")
    async def handle_mcp(payload: MCPJSONRPCRequest, request: Request) -> dict[str, Any]:
        return handle_jsonrpc_message(
            bridge_service=service,
            payload=payload,
            client_host=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent", ""),
        )

    @app.post("/relay/mcp")
    async def handle_relay_mcp(payload: MCPJSONRPCRequest, request: Request) -> dict[str, Any]:
        return handle_relay_jsonrpc_message(
            relay_service=relay_service,
            payload=payload,
            client_host=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent", ""),
            authorization=request.headers.get("authorization", ""),
        )

    return app


def main() -> int:
    parser = argparse.ArgumentParser(prog="astrata-mcp")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8892)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
