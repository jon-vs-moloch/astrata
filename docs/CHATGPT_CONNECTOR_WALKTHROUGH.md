# ChatGPT Connector Walkthrough

## Development Bridge

The current development bridge supports two ChatGPT-facing shapes:

- Custom GPT Actions through `https://<relay-host>/gpt/openapi.json`
- MCP-style development through `https://<relay-host>/mcp`

Use the Custom GPT Actions path for near-term tester experiments. Keep MCP as the development and future interop bridge.

## Connector URL

For MCP-compatible experiments, use:

```text
https://<relay-host>/mcp
```

For Custom GPT Actions, import:

```text
https://<relay-host>/gpt/openapi.json
```

The privacy policy URL is:

```text
https://<relay-host>/privacy
```

## Local Setup

1. Issue or redeem a tester invite so the account is eligible for hosted bridge use.
2. Pair this desktop to the eligible account.
3. Register an OAuth client for the connector callback.
4. Issue and exchange an OAuth authorization code for a relay access token.
5. Point the hosted connector at the relay host, not at a shared default profile.

The hosted auth metadata is now available at:

```text
https://<relay-host>/.well-known/oauth-authorization-server
https://<relay-host>/.well-known/oauth-protected-resource
```

And the current control-plane token routes are:

```text
https://<relay-host>/oauth/register
https://<relay-host>/oauth/authorize
https://<relay-host>/oauth/token
https://<relay-host>/oauth/introspect
https://<relay-host>/oauth/revoke
```

When the Cloudflare relay worker is acting as the public edge, configure it with:

```text
ASTRATA_CONTROL_PLANE_URL=https://<astrata-web-host>
```

That keeps the public OAuth contract on the relay host while delegating client, code, token, and bearer resolution to Astrata Web.

Astrata Web now also exposes a simple browser authorize page at:

```text
https://<astrata-web-host>/oauth/authorize
```

For the current v0 tester flow, that page is enough to confirm the account is enrolled and that a paired Astrata desktop exists before redirecting back with an authorization code.

The current authoritative queue path now also lives in Astrata Web:

```text
/relay/mcp
/relay/local/heartbeat
/relay/local/ack
/relay/local/result
/relay/result/{request_id}
/relay/session/{session_id}
```

So the worker can stay the public edge while Astrata Web owns both connector auth and per-profile queue state.

In proxy mode, the worker should forward both OAuth and relay queue traffic to Astrata Web instead of storing authoritative state locally.

Useful local surfaces:

```bash
astrata account-status
astrata account-issue-invite
astrata account-redeem-invite --email <email> --invite-code <code>
astrata account-pair-device --email <email> --relay-endpoint https://<relay-host>/mcp
astrata account-register-oauth-client --label ChatGPT --redirect-uri <callback-url>
astrata account-issue-oauth-code --client-id <client-id> --email <email> --redirect-uri <callback-url>
astrata account-exchange-oauth-code --client-id <client-id> --code <code> --redirect-uri <callback-url>
```

## Tool Shape

The stable GPT Actions surface should stay small:

- `help`
- `about`
- `submit_feedback`
- `list_tools`
- `tool_search`
- `use_tool`

`tool_search` should expose one-time and profile-specific capabilities. `use_tool` should queue the selected work into Astrata rather than requiring a schema change for every new tool.

## Distribution Safety Rule

Do not share a distributed GPT that routes through:

- `RELAY_DEFAULT_PROFILE_ID`
- a shared bearer token
- a desktop-generated secret without Astrata Web account identity

The v0-safe path is:

```text
ChatGPT OAuth token -> Astrata account -> relay profile -> owned desktop device/link -> permitted tools
```

Until that path exists, this bridge is private-dev or invite-only tester infrastructure.

The local control-plane scaffold can now mint OAuth-shaped access tokens bound to one account, relay profile, and owned desktop device. The hosted relay should prefer those tokens over shared profile bearer tokens whenever the connector supports OAuth, and the worker should treat Astrata Web as the source of truth for client, code, and token state.
