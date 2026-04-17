from pathlib import Path

from astrata.accounts import AccountControlPlaneRegistry
from astrata.mcp import (
    HostedMCPRelayLink,
    HostedMCPRelayProfile,
    HostedMCPRelayService,
    MCPBridgeBinding,
    MCPBridgeService,
)
from astrata.mcp.server import MCPJSONRPCRequest, handle_relay_jsonrpc_message


def test_account_registry_pairs_device_to_invited_user_profile(tmp_path: Path):
    registry = AccountControlPlaneRegistry(state_path=tmp_path / "accounts.json")
    invite = registry.issue_invite_code(label="friendly tester")
    redeemed = registry.redeem_invite_code(
        email="tester@example.com",
        display_name="Tester",
        invite_code=invite["invite"]["code"],
    )

    paired = registry.pair_device_for_user(
        email="tester@example.com",
        label="Tester Mac",
        relay_endpoint="https://relay.example/mcp",
    )

    assert redeemed["status"] == "ok"
    assert paired["status"] == "ok"
    assert paired["device"]["user_id"] == redeemed["user"]["user_id"]
    assert paired["profile"]["user_id"] == redeemed["user"]["user_id"]
    assert paired["device_link"]["profile_id"] == paired["profile"]["profile_id"]
    assert paired["link_token"]
    assert registry.verify_device_link(
        profile_id=paired["profile"]["profile_id"],
        device_id=paired["device"]["device_id"],
        link_token=paired["link_token"],
    )["authorized"] is True


def test_hosted_relay_delivers_only_through_owned_device_link(tmp_path: Path):
    registry = AccountControlPlaneRegistry(state_path=tmp_path / "accounts.json")
    invite = registry.issue_invite_code(label="friendly tester")
    registry.redeem_invite_code(
        email="tester@example.com",
        display_name="Tester",
        invite_code=invite["invite"]["code"],
    )
    paired = registry.pair_device_for_user(email="tester@example.com", label="Tester Mac")
    profile_id = paired["profile"]["profile_id"]
    device_id = paired["device"]["device_id"]
    link_token = paired["link_token"]

    bridge_service = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    bridge_service.register_binding(
        MCPBridgeBinding(
            bridge_id="bridge-owned",
            direction="inbound",
            transport="hosted_relay",
            agent_id="tester-desktop",
        )
    )
    relay_service = HostedMCPRelayService(
        state_path=tmp_path / "mcp_relay.json",
        bridge_service=bridge_service,
        account_registry=registry,
    )
    relay_service.register_profile(
        HostedMCPRelayProfile(
            profile_id=profile_id,
            user_id=paired["user"]["user_id"],
            default_device_id=device_id,
            label="ChatGPT Connector",
            exposure="chatgpt",
            auth_token="topsecret",
        )
    )
    relay_service.register_local_link(
        HostedMCPRelayLink(
            profile_id=profile_id,
            bridge_id="bridge-owned",
            device_id=device_id,
            link_token=link_token,
            status="online",
        )
    )

    delivered = relay_service.queue_tool_call(
        profile_id=profile_id,
        tool_name="submit_task",
        arguments={"task": "Do the thing"},
        meta={"connector": "chatgpt"},
    )

    assert delivered["delivery"] == "delivered"
    assert delivered["handoff"]["bridge_id"] == "bridge-owned"


def test_hosted_relay_rejects_mismatched_device_link(tmp_path: Path):
    registry = AccountControlPlaneRegistry(state_path=tmp_path / "accounts.json")
    first_invite = registry.issue_invite_code(label="first")
    second_invite = registry.issue_invite_code(label="second")
    registry.redeem_invite_code(email="first@example.com", invite_code=first_invite["invite"]["code"])
    registry.redeem_invite_code(email="second@example.com", invite_code=second_invite["invite"]["code"])
    first = registry.pair_device_for_user(email="first@example.com", label="First Mac")
    second = registry.pair_device_for_user(email="second@example.com", label="Second Mac")

    bridge_service = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    relay_service = HostedMCPRelayService(
        state_path=tmp_path / "mcp_relay.json",
        bridge_service=bridge_service,
        account_registry=registry,
    )
    relay_service.register_profile(
        HostedMCPRelayProfile(
            profile_id=first["profile"]["profile_id"],
            user_id=first["user"]["user_id"],
            label="First Connector",
            auth_token="topsecret",
        )
    )

    try:
        relay_service.register_local_link(
            HostedMCPRelayLink(
                profile_id=first["profile"]["profile_id"],
                bridge_id="bridge-wrong",
                device_id=second["device"]["device_id"],
                link_token=second["link_token"],
                status="online",
            )
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("Expected profile/device ownership mismatch to be rejected.")


def test_oauth_access_token_routes_relay_request_to_bound_profile(tmp_path: Path):
    registry = AccountControlPlaneRegistry(state_path=tmp_path / "accounts.json")
    invite = registry.issue_invite_code(label="oauth")
    registry.redeem_invite_code(
        email="tester@example.com",
        display_name="Tester",
        invite_code=invite["invite"]["code"],
    )
    paired = registry.pair_device_for_user(email="tester@example.com", label="Tester Mac")
    client = registry.register_oauth_client(
        label="ChatGPT",
        redirect_uris=("https://chat.openai.com/aip/oauth/callback",),
    )
    code = registry.issue_oauth_authorization_code(
        client_id=client["client"]["client_id"],
        email="tester@example.com",
        redirect_uri="https://chat.openai.com/aip/oauth/callback",
    )
    token = registry.exchange_oauth_authorization_code(
        client_id=client["client"]["client_id"],
        code=code["authorization_code"]["code"],
        redirect_uri="https://chat.openai.com/aip/oauth/callback",
    )

    bridge_service = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    bridge_service.register_binding(
        MCPBridgeBinding(
            bridge_id="bridge-oauth",
            direction="inbound",
            transport="hosted_relay",
            agent_id="tester-desktop",
        )
    )
    relay_service = HostedMCPRelayService(
        state_path=tmp_path / "mcp_relay.json",
        bridge_service=bridge_service,
        account_registry=registry,
    )
    relay_service.register_profile(
        HostedMCPRelayProfile(
            profile_id=paired["profile"]["profile_id"],
            user_id=paired["user"]["user_id"],
            default_device_id=paired["device"]["device_id"],
            label="ChatGPT Connector",
            exposure="chatgpt",
            auth_token="dev-token",
        )
    )
    relay_service.register_local_link(
        HostedMCPRelayLink(
            profile_id=paired["profile"]["profile_id"],
            bridge_id="bridge-oauth",
            device_id=paired["device"]["device_id"],
            link_token=paired["link_token"],
            status="online",
        )
    )

    payload = handle_relay_jsonrpc_message(
        relay_service=relay_service,
        payload=MCPJSONRPCRequest(
            jsonrpc="2.0",
            id="oauth-call",
            method="tools/call",
            params={
                "name": "submit_task",
                "arguments": {"task": "OAuth routed work"},
            },
        ),
        authorization=f"Bearer {token['access_token']}",
    )

    assert token["status"] == "ok"
    assert payload["result"]["delivery"] == "delivered"
    assert payload["result"]["handoff"]["bridge_id"] == "bridge-oauth"
    assert payload["result"]["handoff"]["metadata"]["auth"]["mode"] == "oauth_access_token"
    assert payload["result"]["handoff"]["metadata"]["auth"]["profile_id"] == paired["profile"]["profile_id"]
