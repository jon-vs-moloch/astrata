# ChatGPT Connector Walkthrough

This is the operator walkthrough for connecting ChatGPT to Astrata through the current hosted relay.

The current relay is a development bridge. It is good enough to prove the remote-Prime loop, but it is not the final production security posture.

There are two ChatGPT-facing paths:

- Custom GPT Actions: the near-term distribution path for testers. This uses OpenAI's OpenAPI-schema action surface.
- MCP connector: the development bridge for direct agent/app interop. Keep this around, but do not treat it as the immediate public distribution path.

## What You Need

- ChatGPT account access with developer mode available.
- The Astrata hosted relay URL:
  - `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/mcp`
- The Astrata GPT Actions schema URL:
  - `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/gpt/openapi.json`
- The Astrata public no-auth GPT Actions schema URL:
  - `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/gpt/public/openapi.json`
- The Astrata account OAuth GPT Actions schema URL:
  - `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/gpt/account/openapi.json`
- The Astrata privacy policy URL:
  - `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/privacy`
- The current development relay token.
- Local Astrata available to heartbeat the relay when you want queued work to be consumed.

## Important Security Note

The current relay supports a development OAuth flow for ChatGPT plus the older bearer-token and query-token fallback for local smoke tests.

The production shape should be a real OAuth 2.1 provider. ChatGPT's Apps SDK auth docs describe ChatGPT as an OAuth client using dynamic client registration and PKCE; it does not support custom API keys as the long-term connector auth model.

The development OAuth bridge implements the ChatGPT-facing pieces directly in the Cloudflare Worker:

- protected resource metadata
- authorization server metadata
- dynamic client registration
- authorization-code + PKCE token exchange
- bearer-token verification for `/mcp`

This bridge is now quick-and-dirty in a more useful way: the GPT can expose a public no-auth action for onboarding and funnel work, then use a separate OAuth action when the user asks for account, profile, device, or local-node behavior. The older relay-token fallback still exists for private single-profile development.

Until production OAuth is implemented, treat this connector as private development infrastructure:

- do not publish the shared-token fallback publicly
- do not share the relay token
- rotate the token after any accidental disclosure
- treat pairing codes as short-lived secrets
- keep the exposed tool surface narrow
- keep sensitive-memory disclosure rules on hard rails

## Custom GPT Actions Path

This is the preferred near-term path for distribution to friendly testers.

Important:
Do not share a GPT that is authenticated with Jonathan's development relay token. That GPT routes to Jonathan's local Astrata profile. Public distribution should start from the no-auth action and only escalate to OAuth for account or local-node work. See `docs/WEB_AUTH_CONTROL_PLANE.md`.

The relay exposes two deliberately stable OpenAPI surfaces, but GPT Builder currently treats action domains as unique. That means the same GPT cannot add both `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/gpt/public/openapi.json` and `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/gpt/account/openapi.json` as separate action sets at the same time.

For the distributable GPT, use the public no-auth action first. Add the account action later only after one of these is true:

- the account action is served from a separate hostname
- the public schema grows a tool-level sign-in bridge
- the product moves to an Apps SDK/MCP shape that supports the desired account escalation cleanly

Current v0 bridge choice: the public schema includes a tool-level sign-in bridge, so the distributable GPT should use only the public no-auth action.

### Public Action

Use this for the default distributable Astrata GPT action.

- Auth: `None`
- Schema: `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/gpt/public/openapi.json`
- Purpose: orientation, onboarding, capability ladder, feedback, sign-in guidance, local-node install guidance.
- Access rule: this surface may help a user understand Astrata, download it, install it, and complete local-first setup without an invite code. It must not activate hosted bridge access until the user passes the account/invite gate.
- Stable actions:
  - `public_about`
  - `public_tool_search`
  - `public_sign_in`
  - `public_complete_sign_in`
  - `public_use_tool`
- Tool-level sign-in flow:
  1. Prefer `public_sign_in`.
  2. Fallback: call `public_use_tool` with `tool="sign_in"`.
  3. Store the returned `request_id`.
  4. Ask the user to open the returned `sign_in_url`.
  5. The user signs in or creates an account. If tester eligibility is still invite-gated, this is where the invite code belongs.
  6. When the user says they are done, prefer `public_complete_sign_in` with the original `request_id`.
  7. Fallback: call `public_use_tool` with `tool="complete_sign_in"` and the original `request_id`.
  8. If that fails, use the fallback `ASTRATA-LOGIN-...` completion code from the sign-in page.
  9. Use the returned `session_token` only in follow-up `account_status`, `account_tool_search`, or `account_use_tool` calls in that conversation.

The account tool catalog is security-scoped. `account_tool_search` should be treated as the source of truth for what is available in the current session:

- signed in only: account and device/status/setup tools
- paired local node: read/search/propose workspace-level local tools
- special host-bash acknowledgement: generic `run_command` / host-bash control
- elevated session: patch/test tools

Do not assume a tool is available just because the GPT knows the name. If `account_tool_search` does not return it, the bridge should block it.

`run_command` should remain withheld until the operator has explicitly acknowledged the risk for that relay profile. The acknowledgement is intentionally blunt: any logged-in GPT session for that profile may execute arbitrary host shell commands on connected computers for that profile.

### Account Action

Use this for signed-in Astrata work only when it can live on a different action hostname, or when testing in a separate GPT/action configuration.

- Auth: `OAuth`
- Schema: `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/gpt/account/openapi.json`
- Purpose: account-bound relay/profile/device tools and local-node routing.
- OAuth authorization URL: `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/oauth/authorize`
- OAuth token URL: `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/oauth/token`
- Scope: `astrata:read astrata:write`
- Token exchange method: default POST request
- Stable actions:
  - `help`
  - `about`
  - `submit_feedback`
  - `list_tools`
  - `tool_search`
  - `use_tool`

The account action exposes:

- `help`
- `about`
- `submit_feedback`
- `list_tools`
- `tool_search`
- `use_tool`

The important maintenance choice is that `list_tools`, `tool_search`, and `use_tool` hide the live Astrata tool catalog behind a stable schema. That lets Astrata add, remove, permission, or rename internal tools without forcing a Custom GPT schema update every time.

`list_tools` returns the basic shortlist the GPT should consider first. `tool_search` is the full permission-aware discovery surface. `onboarding` is a live tool discovered through `tool_search` and called with `use_tool(tool="onboarding", args={})` because it should usually run once per user/context. `submit_feedback` is a top-level Action so the GPT keeps it in mind as a first-class product-quality loop.

### Create The Public Custom GPT Action

1. Open ChatGPT.
2. Open GPT Builder.
3. Create or edit the Astrata GPT.
4. Open `Configure`.
5. Open `Actions`.
6. Choose `Create new action`.
7. Set authentication to `None`.
8. Import the schema from this URL:
   `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/gpt/public/openapi.json`
9. Set the privacy policy URL:
   `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/privacy`
10. Confirm that the detected actions are `public_about`, `public_tool_search`, and `public_use_tool`.

### Create The Account Custom GPT Action

1. Use a separate test GPT or a separate action hostname. Do not try to add this next to the public action on the same `astrata-mcp-relay.jonathan-c-meriwether.workers.dev` domain.
2. Choose `Create new action`.
3. Set authentication to `OAuth`.
4. Enter the current registered OAuth client id.
5. Set client secret to `public` if ChatGPT requires a value.
6. Set the authorization URL:
   `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/oauth/authorize`
7. Set the token URL:
   `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/oauth/token`
8. Set scope:
   `astrata:read astrata:write`
9. Use the default POST token exchange method.
10. Import the schema from this URL:
   `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/gpt/account/openapi.json`
11. Set the privacy policy URL:
   `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/privacy`
12. Confirm that the detected actions are `help`, `about`, `submit_feedback`, `list_tools`, `tool_search`, and `use_tool`.

Suggested GPT instructions:

```text
You are Astrata's remote operator bridge.

Start in the signed-out public posture. Use public_about, public_tool_search, and public_use_tool for orientation, onboarding, capability explanation, install guidance, and feedback. Do not require sign-in just to explain Astrata or help someone decide whether it fits.

Escalate with the tool-level sign-in flow when the user asks for personalized account, profile, device, Prime, relay, or local-node work that depends on Astrata-hosted infrastructure. Call public_use_tool(tool="sign_in", args={}), store the returned request_id, and send the user to the returned sign_in_url. When the user says they finished signing in, call public_use_tool(tool="complete_sign_in", request_id="..."). If request_id completion fails, ask for the fallback ASTRATA-LOGIN completion code from the page and call public_use_tool(tool="complete_sign_in", code="..."). Use the returned session_token only for account_status, account_tool_search, and account_use_tool calls in that conversation. Explain that public onboarding and download/install help should remain available without sign-in, while hosted bridge access is the gated boundary.

After sign-in, always call account_tool_search before attempting account_use_tool. Treat its returned tools and security scopes as authoritative for the current account/session. File tools should appear only when the user has a paired local node. Patch, test, and command tools should appear only after an elevated session such as 2FA approval.

On first load, or when your local instructions may be stale, call use_tool(tool="onboarding", args={}) and use its guidance as the current operating manual for this bridge. Call about() when you need product/security orientation. Call list_tools() for the basic current tool shortlist, and call tool_search(query) when the user asks for a capability that is not in the shortlist. Do not invent Astrata tools. Use use_tool(tool, args) to call live Astrata tools.

For long-running work, expect use_tool to return an acknowledgement with request_id and session_id. Preserve those ids and poll with use_tool(tool="get_session", args={"session_id":"..."}) or use_tool(tool="get_result", args={"request_id":"..."}). Use submit_feedback(message, category, severity, context) when the user reports confusing behavior, bugs, missing capabilities, or product friction, or when you notice actionable bridge feedback.

Do not infodump onboarding content. If the user seems new, give a one- or two-sentence overview and offer help; otherwise proceed directly with their request. Respect Astrata's disclosure boundary: do not ask for local-only, enclave-only, secret, or PII data unless the user explicitly authorizes an appropriate secure disclosure flow. Treat this current bridge as development-only until Astrata Web has real account auth and per-user relay routing.
```

Smoke test prompt:

```text
Call use_tool(tool="onboarding", args={}), then list_tools(), then submit_feedback() with the message "GPT actions smoke test reached the feedback path." After that, use tool_search() for "prime" and use use_tool() to send Prime a low-risk smoke message. If the request is queued, tell me the request id and session id and remind me to check back.
```

## Preflight

Check that the Worker is reachable:

```bash
curl -sS https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/health
```

Check that the MCP endpoint can initialize with the local smoke-test bearer fallback:

```bash
curl -sS \
  -H 'Authorization: Bearer <relay-token>' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"init","method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{}}}' \
  https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/mcp
```

For non-ChatGPT smoke tests, the query-token fallback still exists. Use it only as a temporary bridge:

```text
https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/mcp?token=<relay-token>
```

Check that OAuth discovery is live:

```bash
curl -sS https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/.well-known/oauth-protected-resource
curl -sS https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/.well-known/oauth-authorization-server
```

Mint a short-lived pairing code for the Astrata instance that should receive GPT work:

```bash
curl -sS \
  -H 'Authorization: Bearer <relay-token>' \
  -H 'Content-Type: application/json' \
  -d '{"profile_id":"<relay-profile-id>","label":"jon-laptop","ttl_minutes":15}' \
  https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/relay/pairing/create
```

Use that pairing code on the OAuth authorization page when ChatGPT connects. The resulting OAuth access token is bound to that relay profile, so the GPT is no longer hard-locked to one global default Astrata instance.

Check that GPT Actions assets are live:

```bash
curl -sS https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/gpt/openapi.json
curl -sS https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/privacy
```

## Development MCP Connector Path

This path is still useful for agent/app bridge development, but Custom GPT Actions are the near-term distribution path.

1. Open ChatGPT.
2. Go to `Settings`.
3. Open `Apps & Connectors`.
4. Open `Advanced settings`.
5. Enable developer mode if it is available for the account or workspace.
6. Go to `Settings` -> `Connectors`.
7. Choose `Create`.
8. Use this connector name:
   `Astrata Dev Relay`
9. Use this description:
   `Connects ChatGPT to the user's local Astrata instance through a private hosted MCP relay. Use it to message Astrata, inspect connector-safe status, and queue governed work for local delivery.`
10. Use this connector URL:
   `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/mcp`
11. Click `Create`.
12. When ChatGPT begins the OAuth connection flow, it should open Astrata's authorization page.
13. Enter the pairing code for the target Astrata instance. Use the relay token only as a private single-profile fallback.
14. Approve the connection.
15. Confirm that ChatGPT shows the Astrata tool list.

If the OAuth flow fails before the authorization page opens, confirm that ChatGPT can read:

- `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/.well-known/oauth-protected-resource`
- `https://astrata-mcp-relay.jonathan-c-meriwether.workers.dev/.well-known/oauth-authorization-server`

If you need to bypass OAuth for a local smoke test, use curl with `Authorization: Bearer <relay-token>`. Do not use the query-token URL for the ChatGPT connector unless we are deliberately testing a fallback path.

Expected tools include:

- `search`
- `fetch`
- `submit_task`
- `get_task_status`
- `list_capabilities`
- `message_prime`
- `search_files`
- `read_file`
- `propose_patch`
- `request_elevation`
- `apply_patch`
- `run_tests`
- `run_command`
- `delegate_subtasks`
- `handoff_to_controller`
- `request_browser_action`
- `get_result`
- `get_session`

## Try It In A Chat

1. Open a new ChatGPT conversation.
2. Click the `+` button near the composer.
3. Choose `More`.
4. Select the `Astrata Dev Relay` connector.
5. Ask ChatGPT to send a low-risk smoke message:
   `Use Astrata to send Prime the message "connector smoke test"; if the relay says it was queued, tell me the request id and session id.`

The expected first response is not a completed answer from local Astrata. It should be an acknowledgement like:

```json
{
  "status": "received",
  "request_id": "...",
  "session_id": "...",
  "message": "Message received. Check back periodically with get_result or get_session to see status and replies."
}
```

That acknowledgement means ChatGPT reached the hosted relay and the request was queued.

## Let Local Astrata Consume The Request

For a one-shot drain from the Astrata repo, run:

```bash
./.venv/bin/python -m astrata.main mcp-relay-heartbeat \
  --profile-id 4c5cb217-c30e-46c4-b1f8-31eeddb39ab3 \
  --push-remote
```

For an interactive ChatGPT session, run the continuous relay watcher instead:

```bash
./.venv/bin/python -m astrata.main mcp-relay-watch \
  --profile-id 4c5cb217-c30e-46c4-b1f8-31eeddb39ab3 \
  --interval-seconds 30
```

The preferred v0 bring-up path is the supervisor, which will adopt an already-running watcher instead of starting a duplicate:

```bash
./.venv/bin/python -m astrata.main supervisor-reconcile \
  --relay-profile-id 4c5cb217-c30e-46c4-b1f8-31eeddb39ab3
```

Expected behavior:

- the heartbeat posts local capabilities to the relay
- the relay returns pending requests
- Astrata forwards the request into the local handoff queue
- Astrata acknowledges the hosted request so it leaves the relay queue
- read-only projection tools such as `list_capabilities`, `search`, `fetch`, and `get_task_status` post connector-safe results back to the hosted relay

Operational note:
The relay coalesces unchanged heartbeat writes, and the recommended watcher interval is now 30 seconds. A 5-second interval can burn through the free Cloudflare KV write quota quickly during development.

## Remote Request Triage Policy

When the local watcher receives hosted remote requests, it should triage them before they enter the normal handoff queue. The first pass is intentionally cheap and deterministic:

- `list_capabilities`, `search`, `fetch`, and `get_task_status` are `instant` projection tools and should post connector-safe results back to the relay immediately.
- `message_prime`, `search_files`, `read_file`, and `propose_patch` are `fast_review` tools. They should be forwarded quickly with triage metadata so Prime or the local reviewer can handle them before slow background work.
- `submit_task`, `delegate_subtasks`, `handoff_to_controller`, and `request_browser_action` are `slow_review` tools unless the request carries an explicit urgency flag.
- `request_elevation`, `apply_patch`, `run_tests`, and `run_command` are `attention` tools. They should produce a quick receipt for the remote caller and require local attention/elevation before sensitive work runs.
- unknown or malformed remote tools are `blocked` and should return a rejection result instead of entering the queue.

The handoff metadata includes `triage_lane`, `triage_urgency`, `triage_action`, `triage_reason`, `triage_sla_seconds`, `requires_attention`, and `triage_audit_tags`. Downstream controllers should honor those fields rather than re-inferring the request class from the tool name alone.

## Poll For Status

Back in ChatGPT, ask:

```text
Use Astrata get_session for session id <session-id> and summarize whether local Astrata has seen the request.
```

Or:

```text
Use Astrata get_result for request id <request-id>.
```

For handoff-style tools such as `message_prime`, `get_result` may initially report only that the relay request was acknowledged. The richer dialogue state should appear through `get_session` as local responses are appended.

## Refresh Metadata After Changes

When the Worker tool list or descriptions change:

1. Deploy the updated Worker.
2. Open `Settings` -> `Connectors`.
3. Open `Astrata Dev Relay`.
4. Choose `Refresh`.
5. Confirm that the tool list matches the current relay.

## Troubleshooting

- If ChatGPT cannot create the connector, confirm the URL is public HTTPS and ends in `/mcp`.
- If it reports unauthorized, confirm that the `WWW-Authenticate` header points to `/.well-known/oauth-protected-resource`.
- If the OAuth page rejects the login, confirm that the relay token matches the current Cloudflare Worker secret.
- If the OAuth token exchange fails, confirm that ChatGPT is using PKCE `S256` and that the redirect URI is the one it registered through dynamic client registration.
- If tools list successfully but work stays pending, run the local watcher or heartbeat command and check that it reports `remote_consumed` and `remote_ack`.
- If duplicate requests appear, include an `idempotency_key` or `client_request_id` in write-like tool arguments. The current KV receipt cache handles ordinary sequential retries, but hard concurrent idempotency needs a future Durable Object or other atomic coordinator.
- If reads look stale immediately after writes, wait a moment and retry. Cloudflare KV is eventually consistent.

## Sources

- OpenAI Help Center: Configuring actions in GPTs
  - `https://help.openai.com/en/articles/9442513-configuring-actions-in-gpts`
- OpenAI Apps SDK: Connect from ChatGPT
  - `https://developers.openai.com/apps-sdk/deploy/connect-chatgpt`
- OpenAI Apps SDK: Authentication
  - `https://developers.openai.com/apps-sdk/build/auth`
- OpenAI Apps SDK: Test your integration
  - `https://developers.openai.com/apps-sdk/deploy/testing`
