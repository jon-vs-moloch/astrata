from pathlib import Path

from fastapi.testclient import TestClient

from astrata.accounts import AccountControlPlaneRegistry
from astrata.config.settings import AstrataPaths, LocalRuntimeSettings, RuntimeLimits, Settings
from astrata.mcp import HostedMCPRelayLink, HostedMCPRelayProfile, HostedMCPRelayService
from astrata.webpresence.server import create_app
from astrata.webpresence.service import WebPresenceService


def _settings(root: Path) -> Settings:
    data_dir = root / ".astrata"
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        paths=AstrataPaths(
            project_root=root,
            data_dir=data_dir,
            docs_dir=root,
            provider_secrets_path=data_dir / "provider_secrets.json",
        ),
        runtime_limits=RuntimeLimits(),
        local_runtime=LocalRuntimeSettings(
            model_search_paths=(),
            model_install_dir=data_dir / "models",
        ),
    )


def test_relay_queue_isolated_per_profile(tmp_path: Path):
    settings = _settings(tmp_path)
    registry = AccountControlPlaneRegistry.from_settings(settings)
    invite_a = registry.issue_invite_code(label="A")
    invite_b = registry.issue_invite_code(label="B")
    registry.redeem_invite_code(email="a@example.com", invite_code=invite_a["invite"]["code"])
    registry.redeem_invite_code(email="b@example.com", invite_code=invite_b["invite"]["code"])
    paired_a = registry.pair_device_for_user(email="a@example.com", label="A Mac")
    paired_b = registry.pair_device_for_user(email="b@example.com", label="B Mac")

    relay = HostedMCPRelayService.from_settings(settings)
    relay.register_profile(
        HostedMCPRelayProfile(
            profile_id=paired_a["profile"]["profile_id"],
            user_id=paired_a["user"]["user_id"],
            default_device_id=paired_a["device"]["device_id"],
            label="A",
        )
    )
    relay.register_profile(
        HostedMCPRelayProfile(
            profile_id=paired_b["profile"]["profile_id"],
            user_id=paired_b["user"]["user_id"],
            default_device_id=paired_b["device"]["device_id"],
            label="B",
        )
    )
    relay.register_local_link(
        HostedMCPRelayLink(
            profile_id=paired_a["profile"]["profile_id"],
            bridge_id="bridge-a",
            device_id=paired_a["device"]["device_id"],
            link_token=paired_a["link_token"],
            status="online",
        )
    )
    relay.register_local_link(
        HostedMCPRelayLink(
            profile_id=paired_b["profile"]["profile_id"],
            bridge_id="bridge-b",
            device_id=paired_b["device"]["device_id"],
            link_token=paired_b["link_token"],
            status="online",
        )
    )

    request = relay.enqueue_remote_request(
        profile_id=paired_a["profile"]["profile_id"],
        tool_name="submit_task",
        arguments={"task": "Only for A"},
    )
    heartbeat_b = relay.local_heartbeat(
        profile_id=paired_b["profile"]["profile_id"],
        device_id=paired_b["device"]["device_id"],
        link_token=paired_b["link_token"],
    )
    heartbeat_a = relay.local_heartbeat(
        profile_id=paired_a["profile"]["profile_id"],
        device_id=paired_a["device"]["device_id"],
        link_token=paired_a["link_token"],
    )

    assert heartbeat_b["pending_requests"] == []
    assert heartbeat_a["pending_requests"][0]["request_id"] == request["request_id"]


def test_webpresence_relay_queue_flow(tmp_path: Path):
    settings = _settings(tmp_path)
    service = WebPresenceService(settings=settings)
    client = TestClient(create_app(service=service))
    registry = AccountControlPlaneRegistry.from_settings(settings)
    invite = registry.issue_invite_code(label="queue")
    registry.redeem_invite_code(
        email="tester@example.com",
        display_name="Tester",
        invite_code=invite["invite"]["code"],
    )
    paired = registry.pair_device_for_user(email="tester@example.com", label="Tester Mac")
    oauth_client = registry.register_oauth_client(
        label="ChatGPT Connector",
        redirect_uris=["https://chat.openai.com/aip/g-abc123/oauth/callback"],
    )
    code = registry.issue_oauth_authorization_code(
        client_id=oauth_client["client"]["client_id"],
        email="tester@example.com",
        redirect_uri="https://chat.openai.com/aip/g-abc123/oauth/callback",
    )
    token = registry.exchange_oauth_authorization_code(
        client_id=oauth_client["client"]["client_id"],
        code=code["authorization_code"]["code"],
        redirect_uri="https://chat.openai.com/aip/g-abc123/oauth/callback",
    )

    relay = HostedMCPRelayService.from_settings(settings)
    relay.register_profile(
        HostedMCPRelayProfile(
            profile_id=paired["profile"]["profile_id"],
            user_id=paired["user"]["user_id"],
            default_device_id=paired["device"]["device_id"],
            label="ChatGPT Connector",
            exposure="chatgpt",
            auth_token="dev-token",
        )
    )
    relay.register_local_link(
        HostedMCPRelayLink(
            profile_id=paired["profile"]["profile_id"],
            bridge_id="bridge-queue",
            device_id=paired["device"]["device_id"],
            link_token=paired["link_token"],
            status="online",
        )
    )

    enqueue = client.post(
        "/relay/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "queue-1",
            "method": "tools/call",
            "params": {"name": "submit_task", "arguments": {"task": "Queued work"}},
        },
        headers={"authorization": f"Bearer {token['access_token']}"},
    )
    assert enqueue.status_code == 200
    request_id = enqueue.json()["result"]["request"]["request_id"]
    session_id = enqueue.json()["result"]["request"]["session_id"]

    heartbeat = client.post(
        "/relay/local/heartbeat",
        json={
            "profile_id": paired["profile"]["profile_id"],
            "device_id": paired["device"]["device_id"],
            "link_token": paired["link_token"],
        },
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["pending_requests"][0]["request_id"] == request_id

    ack = client.post(
        "/relay/local/ack",
        json={
            "profile_id": paired["profile"]["profile_id"],
            "device_id": paired["device"]["device_id"],
            "link_token": paired["link_token"],
            "request_ids": [request_id],
        },
    )
    assert ack.status_code == 200
    assert ack.json()["remaining_queue_depth"] == 0

    result = client.post(
        "/relay/local/result",
        json={
            "profile_id": paired["profile"]["profile_id"],
            "device_id": paired["device"]["device_id"],
            "link_token": paired["link_token"],
            "request_id": request_id,
            "session_id": session_id,
            "result": {"status": "ok", "message": "Done"},
        },
    )
    assert result.status_code == 200

    fetched = client.get(f"/relay/result/{request_id}", params={"profile_id": paired["profile"]["profile_id"]})
    assert fetched.status_code == 200
    assert fetched.json()["result"]["message"] == "Done"

    session = client.get(
        f"/relay/session/{session_id}",
        params={"profile_id": paired["profile"]["profile_id"], "actor": "remote"},
    )
    assert session.status_code == 200
    kinds = [message["kind"] for message in session.json()["session"]["messages"]]
    assert kinds == ["tool_call", "tool_result"]
