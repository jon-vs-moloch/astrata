import base64
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from astrata.accounts import AccountControlPlaneRegistry
from astrata.config.settings import AstrataPaths, LocalRuntimeSettings, RuntimeLimits, Settings
from astrata.mcp import HostedMCPRelayLink, HostedMCPRelayProfile, HostedMCPRelayService, MCPBridgeBinding, MCPBridgeService
from astrata.mcp.server import MCPJSONRPCRequest, handle_relay_jsonrpc_message
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


def _pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def test_webpresence_oauth_metadata_routes(tmp_path: Path):
    settings = _settings(tmp_path)
    client = TestClient(create_app(service=WebPresenceService(settings=settings)))

    auth_server = client.get("/.well-known/oauth-authorization-server")
    protected = client.get("/.well-known/oauth-protected-resource")

    assert auth_server.status_code == 200
    assert auth_server.json()["grant_types_supported"] == ["authorization_code"]
    assert auth_server.json()["scopes_supported"] == ["relay:use"]
    assert auth_server.json()["token_endpoint"].endswith("/oauth/token")
    assert protected.status_code == 200
    assert protected.json()["resource"].endswith("/mcp")
    assert protected.json()["authorization_servers"] == ["http://testserver"]


def test_webpresence_authorize_page_renders_client_context(tmp_path: Path):
    settings = _settings(tmp_path)
    service = WebPresenceService(settings=settings)
    client = TestClient(create_app(service=service))
    control_plane = AccountControlPlaneRegistry.from_settings(settings)
    registered = control_plane.register_oauth_client(
        label="ChatGPT Connector",
        redirect_uris=["https://chat.openai.com/aip/g-abc123/oauth/callback"],
    )

    response = client.get(
        "/oauth/authorize",
        params={
            "client_id": registered["client"]["client_id"],
            "redirect_uri": "https://chat.openai.com/aip/g-abc123/oauth/callback",
            "scope": "relay:use",
        },
    )

    assert response.status_code == 200
    assert "Authorize Astrata" in response.text
    assert "ChatGPT Connector" in response.text
    assert "relay:use" in response.text


def test_webpresence_authorize_form_redirects_with_code(tmp_path: Path):
    settings = _settings(tmp_path)
    service = WebPresenceService(settings=settings)
    client = TestClient(create_app(service=service), follow_redirects=False)
    control_plane = AccountControlPlaneRegistry.from_settings(settings)
    invite = control_plane.issue_invite_code(label="browser-flow")
    control_plane.redeem_invite_code(
        email="tester@example.com",
        display_name="Tester",
        invite_code=invite["invite"]["code"],
    )
    control_plane.pair_device_for_user(email="tester@example.com", label="Tester Mac")
    registered = control_plane.register_oauth_client(
        label="ChatGPT Connector",
        redirect_uris=["https://chat.openai.com/aip/g-abc123/oauth/callback"],
    )

    verifier = "astrata-browser-verifier"
    response = client.post(
        "/oauth/authorize",
        data={
            "client_id": registered["client"]["client_id"],
            "email": "tester@example.com",
            "redirect_uri": "https://chat.openai.com/aip/g-abc123/oauth/callback",
            "scope": "relay:use",
            "state": "browser-state",
            "code_challenge": _pkce_s256(verifier),
            "code_challenge_method": "S256",
        },
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("https://chat.openai.com/aip/g-abc123/oauth/callback?code=")
    assert "state=browser-state" in response.headers["location"]


def test_webpresence_oauth_http_flow_issues_token_and_routes_relay_work(tmp_path: Path):
    settings = _settings(tmp_path)
    service = WebPresenceService(settings=settings)
    client = TestClient(create_app(service=service))

    control_plane = AccountControlPlaneRegistry.from_settings(settings)
    assert client.get("/api/auth-control-plane").json()["counts"]["users"] == 0
    issued_invite = control_plane.issue_invite_code(label="oauth-http")
    control_plane.redeem_invite_code(
        email="tester@example.com",
        display_name="Tester",
        invite_code=issued_invite["invite"]["code"],
    )
    paired = control_plane.pair_device_for_user(
        email="tester@example.com",
        label="Tester Mac",
        relay_endpoint="https://relay.example/mcp",
    )

    register = client.post(
        "/oauth/register",
        json={
            "label": "ChatGPT Connector",
            "redirect_uris": ["https://chat.openai.com/aip/g-abc123/oauth/callback"],
        },
    )
    assert register.status_code == 200
    client_id = register.json()["client"]["client_id"]
    verifier = "astrata-pkce-verifier"

    authorize = client.post(
        "/oauth/authorize",
        json={
            "client_id": client_id,
            "email": "tester@example.com",
            "redirect_uri": "https://chat.openai.com/aip/g-abc123-oauth-lane/oauth/callback",
            "scope": ["astrata:read", "astrata:write"],
            "code_challenge": _pkce_s256(verifier),
            "code_challenge_method": "S256",
        },
    )
    assert authorize.status_code == 200
    assert authorize.json()["authorization_code"]["scope"] == ["relay:use"]
    code = authorize.json()["authorization_code"]["code"]

    token = client.post(
        "/oauth/token",
        data={
            "client_id": client_id,
            "code": code,
            "redirect_uri": "https://chat.openai.com/aip/g-abc123/oauth/callback",
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200
    access_token = token.json()["access_token"]
    assert token.json()["scope"] == ["relay:use"]

    introspect = client.post("/oauth/introspect", json={"access_token": access_token})
    assert introspect.status_code == 200
    assert introspect.json()["authorized"] is True
    assert introspect.json()["profile_id"] == paired["profile"]["profile_id"]
    assert introspect.json()["device_id"] == paired["device"]["device_id"]

    bridge_service = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    bridge_service.register_binding(
        MCPBridgeBinding(
            bridge_id="bridge-http-oauth",
            direction="inbound",
            transport="hosted_relay",
            agent_id="tester-desktop",
        )
    )
    relay_service = HostedMCPRelayService(
        state_path=tmp_path / "mcp_relay.json",
        bridge_service=bridge_service,
        account_registry=control_plane,
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
            bridge_id="bridge-http-oauth",
            device_id=paired["device"]["device_id"],
            link_token=paired["link_token"],
            status="online",
        )
    )

    payload = handle_relay_jsonrpc_message(
        relay_service=relay_service,
        payload=MCPJSONRPCRequest(
            jsonrpc="2.0",
            id="oauth-http-call",
            method="tools/call",
            params={"name": "submit_task", "arguments": {"task": "HTTP OAuth routed work"}},
        ),
        authorization=f"Bearer {access_token}",
    )
    assert payload["result"]["delivery"] == "delivered"
    assert payload["result"]["handoff"]["bridge_id"] == "bridge-http-oauth"

    replay = client.post(
        "/oauth/token",
        json={
            "client_id": client_id,
            "code": code,
            "redirect_uri": "https://chat.openai.com/aip/g-abc123/oauth/callback",
            "code_verifier": verifier,
        },
    )
    assert replay.status_code == 400
    assert replay.json()["detail"]["status"] == "invalid_grant"

    revoke = client.post("/oauth/revoke", json={"access_token": access_token})
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "ok"

    revoked = client.post("/oauth/introspect", json={"access_token": access_token})
    assert revoked.status_code == 401
    assert revoked.json()["detail"]["status"] == "revoked"
