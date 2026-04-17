"""Small public web presence server for Astrata registries and metadata."""

from __future__ import annotations

import argparse
from typing import Any
from urllib.parse import parse_qs, urlencode
from html import escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
import uvicorn

from astrata.accounts import AccountControlPlaneRegistry
from astrata.mcp import HostedMCPRelayService
from astrata.mcp.server import MCPJSONRPCRequest
from astrata.webpresence.service import WebPresenceService


class OAuthClientRegistrationRequest(BaseModel):
    label: str = Field(min_length=1)
    redirect_uris: list[str] = Field(default_factory=list)
    client_kind: str = "chatgpt_connector"
    metadata: dict = Field(default_factory=dict)


class OAuthAuthorizeRequest(BaseModel):
    client_id: str = Field(min_length=1)
    email: str | None = None
    user_id: str | None = None
    profile_id: str | None = None
    device_id: str | None = None
    redirect_uri: str = ""
    scope: list[str] | str = Field(default_factory=lambda: ["relay:use"])
    ttl_seconds: int = 600
    code_challenge: str = ""
    code_challenge_method: str = ""
    state: str = ""


class OAuthTokenRequest(BaseModel):
    client_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    redirect_uri: str = ""
    ttl_seconds: int = 3600
    code_verifier: str = ""


class OAuthIntrospectRequest(BaseModel):
    access_token: str = Field(min_length=1)


class RelayLocalHeartbeatRequest(BaseModel):
    profile_id: str
    device_id: str | None = None
    link_token: str | None = None
    advertised_capabilities: dict[str, object] = Field(default_factory=dict)


class RelayLocalAckRequest(BaseModel):
    profile_id: str
    request_ids: list[str] = Field(default_factory=list)
    device_id: str | None = None
    link_token: str | None = None


class RelayLocalResultRequest(BaseModel):
    profile_id: str
    request_id: str
    result: dict[str, object] = Field(default_factory=dict)
    session_id: str | None = None
    device_id: str | None = None
    link_token: str | None = None


class RelaySessionMessageRequest(BaseModel):
    profile_id: str
    session_id: str
    request_id: str = ""
    kind: str = "message"
    content: dict[str, object] = Field(default_factory=dict)


async def _request_payload(request: Request) -> dict:
    content_type = str(request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        payload = await request.json()
        return payload if isinstance(payload, dict) else {}
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    payload: dict[str, object] = {}
    for key, values in parsed.items():
        if not values:
            continue
        payload[key] = values if len(values) > 1 else values[0]
    return payload


def _registry(service: WebPresenceService) -> AccountControlPlaneRegistry:
    return AccountControlPlaneRegistry.from_settings(service.settings)


def _relay(service: WebPresenceService) -> HostedMCPRelayService:
    return HostedMCPRelayService.from_settings(service.settings)


def _oauth_authorization_server_metadata(request: Request) -> dict:
    base = str(request.base_url).rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "introspection_endpoint": f"{base}/oauth/introspect",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["relay:use"],
    }


def _oauth_protected_resource_metadata(request: Request) -> dict:
    base = str(request.base_url).rstrip("/")
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["relay:use"],
    }


def _raise_for_status(result: dict, *, status_map: dict[str, int]) -> None:
    status = str(result.get("status") or "")
    code = status_map.get(status)
    if code is not None:
        raise HTTPException(status_code=code, detail=result)


def _friendly_authorize_error(status: str) -> str:
    return {
        "unknown_oauth_client": "This connector is not registered with Astrata Web yet.",
        "redirect_uri_not_allowed": "This connector callback URL is not registered for the selected client.",
        "unsupported_code_challenge_method": "This sign-in lane requires PKCE S256.",
        "unknown_user": "That email is not enrolled for hosted Astrata access yet. Redeem an invite first, or finish account setup locally.",
        "hosted_bridge_not_enabled": "This account is not eligible for hosted bridge use yet.",
        "profile_not_owned": "That profile does not belong to this account.",
        "no_active_owned_device_link": "This account does not have an active paired Astrata desktop yet.",
        "invalid_invite": "That invite code is not valid anymore.",
    }.get(str(status or ""), "Astrata could not complete connector authorization.")


def _resolve_relay_profile(
    *,
    relay_service: HostedMCPRelayService,
    authorization: str | None,
    requested_profile_id: str = "",
) -> tuple[str, dict[str, Any]]:
    authorization_token = str(authorization or "").removeprefix("Bearer ").strip()
    auth_context: dict[str, Any] = {"mode": "development_profile_token"}
    profile_id = str(requested_profile_id or "").strip()
    if relay_service.account_registry is not None and authorization_token:
        resolved = relay_service.account_registry.resolve_oauth_access_token(authorization_token)
        if resolved.get("authorized"):
            auth_context = {"mode": "oauth_access_token", **resolved}
            if profile_id and profile_id != resolved.get("profile_id"):
                raise HTTPException(status_code=403, detail="OAuth token is not bound to the requested relay profile")
            profile_id = str(resolved["profile_id"])
    profile = relay_service.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Unknown relay profile")
    if auth_context["mode"] != "oauth_access_token" and profile.auth_token:
        if authorization_token != profile.auth_token:
            raise HTTPException(status_code=401, detail="Invalid relay token")
    return profile_id, auth_context


def _authorize_page(
    request: Request,
    *,
    service: WebPresenceService,
    error: str = "",
    email: str = "",
    invite_code: str = "",
    selected_profile_id: str = "",
    selected_device_id: str = "",
) -> HTMLResponse:
    registry = _registry(service)
    params = dict(request.query_params)
    client_id = str(params.get("client_id") or "").strip()
    redirect_uri = str(params.get("redirect_uri") or "").strip()
    state = str(params.get("state") or "").strip()
    scope = str(params.get("scope") or "relay:use").strip() or "relay:use"
    code_challenge = str(params.get("code_challenge") or "").strip()
    code_challenge_method = str(params.get("code_challenge_method") or "").strip()
    client_label = "Astrata Connector"
    if client_id:
        for client in dict(registry._load().get("oauth_clients") or {}).values():  # noqa: SLF001
            if isinstance(client, dict) and client.get("client_id") == client_id:
                client_label = str(client.get("label") or client_label)
                break

    account_line = ""
    if email:
        user = registry.user_for_email(email)
        if user is not None:
            profile = registry.default_relay_profile_for_user(user.user_id)
            link = None if profile is None else registry.active_device_link_for_profile(profile.profile_id)
            if profile is not None and link is not None:
                account_line = (
                    f"<p class='status ok'>Ready to authorize as <strong>{escape(user.display_name or user.email)}</strong> "
                    f"for profile <code>{escape(profile.label)}</code>.</p>"
                )
            elif profile is not None:
                account_line = (
                    f"<p class='status warn'>Account found for <strong>{escape(user.email)}</strong>, "
                    "but no active paired Astrata desktop is linked yet.</p>"
                )

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Authorize Astrata</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; min-height: 100vh; display: grid; place-items: center; background: #101820; color: #f8f3e8; }}
      main {{ width: min(620px, calc(100vw - 32px)); background: #172531; border: 1px solid #355063; border-radius: 20px; padding: 28px; box-shadow: 0 24px 80px rgb(0 0 0 / 35%); }}
      h1 {{ margin: 0 0 10px; font-size: 1.7rem; }}
      p {{ line-height: 1.5; color: #d9ccb8; font-size: 14px; }}
      label {{ display: grid; gap: 8px; margin: 16px 0; color: #fff7ea; }}
      input {{ font: inherit; border: 1px solid #4b6c80; border-radius: 10px; padding: 12px 14px; background: #0d151c; color: #fff7ea; }}
      button {{ font: inherit; border: 0; border-radius: 999px; padding: 12px 18px; background: #f6b04a; color: #211505; cursor: pointer; font-weight: 700; width: 100%; margin-top: 10px; }}
      .error {{ color: #ffb4a8; font-weight: 700; background: #401010; padding: 12px; border-radius: 8px; font-size: 14px; margin: 14px 0; }}
      .status {{ padding: 12px; border-radius: 8px; font-size: 14px; margin: 14px 0; }}
      .status.ok {{ background: #113322; color: #c9f6da; }}
      .status.warn {{ background: #332410; color: #ffe4b5; }}
      code {{ color: #ffd18a; }}
      .meta {{ background: #0d151c; border: 1px solid #253743; border-radius: 10px; padding: 14px; margin: 18px 0; }}
      .meta-row {{ display: grid; grid-template-columns: 140px 1fr; gap: 10px; font-size: 13px; margin: 8px 0; color: #c8d8e2; }}
      .note {{ color: #9ab3c0; font-size: 12px; margin-top: 12px; }}
    </style>
  </head>
  <body>
    <main>
      <h1>Authorize Astrata</h1>
      <p>Connect this connector to your paired Astrata desktop. Astrata will only issue a token if the selected account owns an active device link.</p>
      {f"<div class='error'>{escape(error)}</div>" if error else ""}
      {account_line}
      <div class="meta">
        <div class="meta-row"><strong>Client</strong><span>{escape(client_label)}</span></div>
        <div class="meta-row"><strong>Scope</strong><code>{escape(scope)}</code></div>
        <div class="meta-row"><strong>Callback</strong><code>{escape(redirect_uri or '(missing)')}</code></div>
      </div>
      <form method="post" action="/oauth/authorize">
        <input type="hidden" name="client_id" value="{escape(client_id)}">
        <input type="hidden" name="redirect_uri" value="{escape(redirect_uri)}">
        <input type="hidden" name="state" value="{escape(state)}">
        <input type="hidden" name="scope" value="{escape(scope)}">
        <input type="hidden" name="code_challenge" value="{escape(code_challenge)}">
        <input type="hidden" name="code_challenge_method" value="{escape(code_challenge_method)}">
        <label>
          Email address
          <input type="email" name="email" required autofocus value="{escape(email)}">
        </label>
        <label>
          Invite Code <span class="note">(Only needed the first time, if this email has not been enrolled yet.)</span>
          <input type="text" name="invite_code" autocomplete="off" value="{escape(invite_code)}">
        </label>
        <input type="hidden" name="profile_id" value="{escape(selected_profile_id)}">
        <input type="hidden" name="device_id" value="{escape(selected_device_id)}">
        <button type="submit">Authorize Connector</button>
      </form>
      <p class="note">This is the current v0 tester flow. It proves account-owned routing and paired-device authorization before we add the fuller hosted sign-in and consent experience.</p>
    </main>
  </body>
</html>"""
    return HTMLResponse(html)


def create_app(*, service: WebPresenceService | None = None) -> FastAPI:
    app = FastAPI(title="Astrata Web Presence", version="0.1.0")
    service = service or WebPresenceService()

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "service": "astrata-web-presence"}

    @app.get("/api/capabilities")
    def capabilities() -> dict:
        return service.capabilities()

    @app.get("/api/auth-control-plane")
    def auth_control_plane() -> dict:
        return service.auth_control_plane()

    @app.get("/api/auth-schema")
    def auth_schema() -> dict:
        return service.auth_schema()

    @app.get("/api/provider-registry")
    def provider_registry() -> dict:
        return service.provider_registry()

    @app.get("/api/model-registry")
    def model_registry() -> dict:
        return service.model_registry()

    @app.get("/api/voice-registry")
    def voice_registry() -> dict:
        return service.voice_registry()

    @app.get("/api/downloads")
    def downloads() -> dict:
        return service.download_manifest()

    @app.get("/api/distribution")
    def distribution() -> dict:
        return service.distribution_manifest()

    @app.get("/api/updates/{channel}")
    def updates(channel: str) -> dict:
        return service.update_manifest(channel)

    @app.get("/.well-known/oauth-authorization-server")
    def oauth_authorization_server(request: Request) -> dict:
        return _oauth_authorization_server_metadata(request)

    @app.get("/.well-known/oauth-protected-resource")
    def oauth_protected_resource(request: Request) -> dict:
        return _oauth_protected_resource_metadata(request)

    @app.get("/oauth/authorize")
    async def oauth_authorize_page(request: Request) -> HTMLResponse:
        return _authorize_page(request, service=service, email=str(request.query_params.get("email") or "").strip())

    @app.post("/oauth/register")
    async def oauth_register(request: Request) -> dict:
        payload = OAuthClientRegistrationRequest.model_validate(await _request_payload(request))
        result = _registry(service).register_oauth_client(
            label=payload.label,
            redirect_uris=payload.redirect_uris,
            client_kind=payload.client_kind,
            metadata=payload.metadata,
        )
        return result

    @app.post("/oauth/authorize", response_model=None)
    async def oauth_authorize(request: Request) -> dict | RedirectResponse | HTMLResponse:
        browser_form_post = "application/x-www-form-urlencoded" in str(request.headers.get("content-type") or "").lower()
        raw_payload = await _request_payload(request)
        payload = OAuthAuthorizeRequest.model_validate(raw_payload)
        registry = _registry(service)
        normalized_email = str(payload.email or "").strip().lower()
        normalized_invite = str(raw_payload.get("invite_code") or "").strip()
        selected_profile_id = str(payload.profile_id or raw_payload.get("profile_id") or "").strip()
        selected_device_id = str(payload.device_id or raw_payload.get("device_id") or "").strip()
        if normalized_email and registry.user_for_email(normalized_email) is None and normalized_invite:
            redeemed = registry.redeem_invite_code(
                email=normalized_email,
                invite_code=normalized_invite,
                display_name=normalized_email.split("@")[0],
            )
            if redeemed.get("status") != "ok":
                if browser_form_post:
                    return _authorize_page(
                        request,
                        service=service,
                        error=_friendly_authorize_error(str(redeemed.get("status") or "")),
                        email=normalized_email,
                        invite_code=normalized_invite,
                        selected_profile_id=selected_profile_id,
                        selected_device_id=selected_device_id,
                    )
                _raise_for_status(redeemed, status_map={"invalid_invite": 400})
        result = registry.issue_oauth_authorization_code(
            client_id=payload.client_id,
            email=normalized_email or payload.email,
            user_id=payload.user_id,
            profile_id=selected_profile_id or None,
            device_id=selected_device_id or None,
            redirect_uri=payload.redirect_uri,
            scope=payload.scope,
            ttl_seconds=payload.ttl_seconds,
            code_challenge=payload.code_challenge,
            code_challenge_method=payload.code_challenge_method,
        )
        if browser_form_post:
            if result.get("status") == "ok":
                query = {"code": str(result["authorization_code"]["code"])}
                if payload.state:
                    query["state"] = payload.state
                redirect = RedirectResponse(
                    url=f"{payload.redirect_uri}{'&' if '?' in payload.redirect_uri else '?'}{urlencode(query)}",
                    status_code=303,
                )
                return redirect
            return _authorize_page(
                request,
                service=service,
                error=_friendly_authorize_error(str(result.get("status") or "")),
                email=normalized_email,
                invite_code=normalized_invite,
                selected_profile_id=selected_profile_id,
                selected_device_id=selected_device_id,
            )
        _raise_for_status(
            result,
            status_map={
                "unknown_oauth_client": 404,
                "redirect_uri_not_allowed": 400,
                "unsupported_code_challenge_method": 400,
                "invalid_invite": 400,
                "unknown_user": 404,
                "hosted_bridge_not_enabled": 403,
                "profile_not_owned": 403,
                "no_active_owned_device_link": 409,
            },
        )
        return result

    @app.post("/oauth/token")
    async def oauth_token(request: Request) -> dict:
        payload = OAuthTokenRequest.model_validate(await _request_payload(request))
        result = _registry(service).exchange_oauth_authorization_code(
            client_id=payload.client_id,
            code=payload.code,
            redirect_uri=payload.redirect_uri,
            ttl_seconds=payload.ttl_seconds,
            code_verifier=payload.code_verifier,
        )
        _raise_for_status(
            result,
            status_map={
                "invalid_grant": 400,
                "expired_grant": 400,
                "missing_code_verifier": 400,
                "no_active_owned_device_link": 409,
            },
        )
        return result

    @app.post("/oauth/introspect")
    async def oauth_introspect(request: Request) -> dict:
        payload = OAuthIntrospectRequest.model_validate(await _request_payload(request))
        result = _registry(service).resolve_oauth_access_token(payload.access_token)
        _raise_for_status(
            result,
            status_map={
                "invalid_token": 401,
                "expired_token": 401,
                "revoked": 401,
                "no_active_owned_device_link": 409,
            },
        )
        return result

    @app.post("/oauth/revoke")
    async def oauth_revoke(request: Request) -> dict:
        payload = OAuthIntrospectRequest.model_validate(await _request_payload(request))
        result = _registry(service).revoke_oauth_access_token(payload.access_token)
        _raise_for_status(result, status_map={"not_found": 404})
        return result

    @app.post("/relay/mcp")
    async def relay_mcp(request: Request) -> dict:
        relay_service = _relay(service)
        payload = MCPJSONRPCRequest.model_validate(await request.json())
        params = dict(payload.params or {})
        requested_profile_id = str(params.get("profile_id") or params.get("_meta", {}).get("profile_id") or "").strip()
        profile_id, auth_context = _resolve_relay_profile(
            relay_service=relay_service,
            authorization=request.headers.get("authorization"),
            requested_profile_id=requested_profile_id,
        )
        if payload.method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": payload.id,
                "result": {"tools": relay_service.connector_safe_tools(profile_id)},
            }
        if payload.method != "tools/call":
            raise HTTPException(status_code=400, detail=f"Unsupported relay method: {payload.method}")
        request_record = relay_service.enqueue_remote_request(
            profile_id=profile_id,
            tool_name=str(params.get("name") or "tool"),
            arguments=dict(params.get("arguments") or {}),
            meta={**dict(params.get("_meta") or {}), "auth": auth_context},
            source_connector=str(params.get("_meta", {}).get("connector") or "remote_connector"),
            session_id=str(dict(params.get("arguments") or {}).get("session_id") or params.get("session_id") or ""),
        )
        return {
            "jsonrpc": "2.0",
            "id": payload.id,
            "result": {"delivery": "queued", "request": request_record, "handoff": None},
        }

    @app.post("/relay/local/heartbeat")
    async def relay_local_heartbeat(request: Request) -> dict:
        payload = RelayLocalHeartbeatRequest.model_validate(await request.json())
        try:
            return _relay(service).local_heartbeat(
                profile_id=payload.profile_id,
                device_id=payload.device_id,
                link_token=payload.link_token,
                advertised_capabilities=dict(payload.advertised_capabilities or {}),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/relay/local/ack")
    async def relay_local_ack(request: Request) -> dict:
        payload = RelayLocalAckRequest.model_validate(await request.json())
        try:
            return _relay(service).acknowledge_requests(
                profile_id=payload.profile_id,
                request_ids=payload.request_ids,
                device_id=payload.device_id,
                link_token=payload.link_token,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/relay/local/result")
    async def relay_local_result(request: Request) -> dict:
        payload = RelayLocalResultRequest.model_validate(await request.json())
        try:
            return _relay(service).record_result(
                profile_id=payload.profile_id,
                request_id=payload.request_id,
                result=dict(payload.result or {}),
                session_id=payload.session_id,
                device_id=payload.device_id,
                link_token=payload.link_token,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get("/relay/result/{request_id}")
    async def relay_result(request_id: str, profile_id: str = "") -> dict:
        result = _relay(service).result_for_request(request_id=request_id, profile_id=profile_id or None)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result)
        return result

    @app.post("/relay/session/message")
    async def relay_session_message(request: Request) -> dict:
        payload = RelaySessionMessageRequest.model_validate(await request.json())
        return _relay(service).append_session_message(
            profile_id=payload.profile_id,
            session_id=payload.session_id,
            request_id=payload.request_id,
            sender="remote",
            kind=payload.kind,
            content=dict(payload.content or {}),
        )

    @app.get("/relay/session/{session_id}")
    async def relay_session(session_id: str, profile_id: str, actor: str = "remote") -> dict:
        return _relay(service).session(profile_id=profile_id, session_id=session_id, actor=actor)

    return app


def main() -> int:
    parser = argparse.ArgumentParser(prog="astrata-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8893)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
