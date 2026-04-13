from pathlib import Path

from astrata.mcp import HostedMCPRelayLink, HostedMCPRelayProfile, HostedMCPRelayService, MCPBridgeBinding, MCPBridgeService
from astrata.mcp.client import MCPClientAdapter
from astrata.mcp.server import MCPJSONRPCRequest, handle_jsonrpc_message, handle_relay_jsonrpc_message


def test_mcp_client_adapter_builds_tool_call(tmp_path: Path):
    service = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    service.register_binding(
        MCPBridgeBinding(
            bridge_id="bridge-http",
            direction="outbound",
            transport="streamable_http",
            agent_id="peer-agent",
            endpoint="http://127.0.0.1:8892/mcp",
        )
    )
    adapter = MCPClientAdapter(bridge_service=service)

    request = adapter.build_tool_call(
        bridge_id="bridge-http",
        tool_name="submit_task",
        arguments={"task_id": "task-1"},
    )

    assert request["jsonrpc"] == "2.0"
    assert request["method"] == "tools/call"
    assert request["params"]["name"] == "submit_task"
    assert request["params"]["_meta"]["bridge_id"] == "bridge-http"


def test_mcp_http_server_accepts_tool_call_and_records_handoff(tmp_path: Path):
    service = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    service.register_binding(
        MCPBridgeBinding(
            bridge_id="bridge-in",
            direction="inbound",
            transport="streamable_http",
            agent_id="external-prime",
            role="prime",
            can_be_prime=True,
        )
    )
    payload = handle_jsonrpc_message(
        bridge_service=service,
        payload=MCPJSONRPCRequest(
            jsonrpc="2.0",
            id="abc123",
            method="tools/call",
            params={
                "name": "submit_task",
                "arguments": {
                    "bridge_id": "bridge-in",
                    "task_id": "task-77",
                    "target_controller": "prime",
                    "delegation_mode": "supervisory",
                },
            },
        ),
        client_host="127.0.0.1",
        user_agent="pytest",
    )

    assert payload["id"] == "abc123"
    assert payload["result"]["handoff"]["bridge_id"] == "bridge-in"
    assert payload["result"]["handoff"]["delegation_mode"] == "supervisory"
    assert service.list_events(bridge_id="bridge-in")[0].event_type == "tool_call_received"


def test_hosted_mcp_relay_server_lists_connector_safe_tools(tmp_path: Path):
    bridge_service = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    relay_service = HostedMCPRelayService(state_path=tmp_path / "mcp_relay.json", bridge_service=bridge_service)
    relay_service.register_profile(
        HostedMCPRelayProfile(
            profile_id="chatgpt",
            label="ChatGPT Connector",
            exposure="chatgpt",
            auth_token="topsecret",
        )
    )

    payload = handle_relay_jsonrpc_message(
        relay_service=relay_service,
        payload=MCPJSONRPCRequest(
            jsonrpc="2.0",
            id="tools1",
            method="tools/list",
            params={"profile_id": "chatgpt"},
        ),
        authorization="Bearer topsecret",
    )

    assert payload["id"] == "tools1"
    assert any(tool["name"] == "search" for tool in payload["result"]["tools"])
    assert any(tool["name"] == "message_prime" for tool in payload["result"]["tools"])


def test_hosted_mcp_relay_server_queues_when_local_link_is_offline(tmp_path: Path):
    bridge_service = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    relay_service = HostedMCPRelayService(state_path=tmp_path / "mcp_relay.json", bridge_service=bridge_service)
    relay_service.register_profile(
        HostedMCPRelayProfile(
            profile_id="chatgpt",
            label="ChatGPT Connector",
            exposure="chatgpt",
            auth_token="topsecret",
        )
    )
    relay_service.register_local_link(
        HostedMCPRelayLink(
            profile_id="chatgpt",
            bridge_id="bridge-missing",
            status="offline",
        )
    )

    payload = handle_relay_jsonrpc_message(
        relay_service=relay_service,
        payload=MCPJSONRPCRequest(
            jsonrpc="2.0",
            id="relay1",
            method="tools/call",
            params={
                "profile_id": "chatgpt",
                "name": "submit_task",
                "arguments": {"task": "Do the thing"},
                "_meta": {"connector": "chatgpt"},
            },
        ),
        authorization="Bearer topsecret",
    )

    assert payload["result"]["delivery"] == "queued"
    assert payload["result"]["handoff"] is None


def test_hosted_mcp_relay_server_rejects_invalid_token(tmp_path: Path):
    bridge_service = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    relay_service = HostedMCPRelayService(state_path=tmp_path / "mcp_relay.json", bridge_service=bridge_service)
    relay_service.register_profile(
        HostedMCPRelayProfile(
            profile_id="chatgpt",
            label="ChatGPT Connector",
            exposure="chatgpt",
            auth_token="topsecret",
        )
    )

    try:
        handle_relay_jsonrpc_message(
            relay_service=relay_service,
            payload=MCPJSONRPCRequest(
                jsonrpc="2.0",
                id="tools2",
                method="tools/list",
                params={"profile_id": "chatgpt"},
            ),
            authorization="Bearer wrong",
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 401
    else:
        raise AssertionError("Expected hosted relay auth failure.")
