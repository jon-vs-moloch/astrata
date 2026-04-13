from pathlib import Path

from astrata.accounts.service import AccountControlPlaneRegistry
from astrata.mcp.models import HostedMCPRelayProfile, MCPBridgeBinding
from astrata.mcp.relay import HostedMCPRelayService
from astrata.mcp.service import MCPBridgeService


def test_hosted_mcp_relay_surfaces_access_policy_in_advertisement_and_telemetry(tmp_path: Path):
    bridge = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    bridge.register_binding(
        MCPBridgeBinding(
            bridge_id="bridge-1",
            direction="inbound",
            transport="streamable_http",
            agent_id="remote-prime",
            role="prime",
            can_be_prime=True,
        )
    )
    relay = HostedMCPRelayService(state_path=tmp_path / "mcp_relay.json", bridge_service=bridge)
    relay.register_profile(
        HostedMCPRelayProfile(
            profile_id="profile-1",
            label="ChatGPT",
            exposure="chatgpt",
            auth_mode="none",
        )
    )

    advert = relay.local_capability_advertisement(profile_id="profile-1")
    telemetry = relay.telemetry_summary(profile_id="profile-1")
    catalog = relay.connector_tool_catalog("profile-1")

    assert advert["access_policy"]["public_access"]["download"] is True
    assert "invite-gated" in advert["access_boundary_summary"]
    assert advert["remote_host_bash"]["enabled"] is False
    assert telemetry["access_policy"]["invite_gated_access"]["gpt_bridge_sign_in"] is True
    assert any(tool["name"] == "list_capabilities" and "download/install is public" in tool["description"] for tool in catalog)


def test_hosted_mcp_relay_advertises_run_command_only_after_acknowledgement(tmp_path: Path):
    bridge = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    relay = HostedMCPRelayService(state_path=tmp_path / "mcp_relay.json", bridge_service=bridge)
    relay.register_profile(
        HostedMCPRelayProfile(
            profile_id="profile-1",
            label="ChatGPT",
            exposure="chatgpt",
            auth_mode="none",
            allowed_tools=("message_prime",),
        )
    )

    assert "run_command" not in relay.local_capability_advertisement(profile_id="profile-1")["allowed_tools"]

    registry = AccountControlPlaneRegistry(state_path=tmp_path / "account_control_plane.json")
    registry.register_desktop_device(
        email="tester@example.com",
        device_label="Test Mac",
        profile_id="profile-1",
        relay_endpoint="https://relay.example.com",
    )
    registry.set_remote_host_bash(profile_id="profile-1", enabled=True)

    advert = relay.local_capability_advertisement(profile_id="profile-1")
    catalog = relay.connector_tool_catalog("profile-1")
    assert "run_command" in advert["allowed_tools"]
    assert advert["remote_host_bash"]["enabled"] is True
    assert any(tool["name"] == "run_command" and "special acknowledgement" in tool["description"] for tool in catalog)
