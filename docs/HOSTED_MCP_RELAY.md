# Hosted MCP Relay

## Purpose

This is the thin hosted bridge that lets external AI products reach Astrata when the user is away from their machine.

It is not a second cloud runtime.
It is:

- an authenticated front door
- a queue and rendezvous point
- a connector-safe projection surface
- a bridge to the user's local Astrata instance

## First Hosting Posture

Recommended first deployment posture:

- Cloudflare Workers
- default `*.workers.dev` domain
- one dev/staging relay
- Custom GPT Actions adapter for near-term tester distribution
- dev OAuth for MCP-style ChatGPT experiments, with bearer-token auth retained for local smoke tests
- narrow stable GPT Actions surface plus narrow MCP development surface

Optional but strongly recommended once the relay becomes useful:

- bind a Cloudflare KV namespace as `RELAY_STATE`

Without KV, the current Worker falls back to in-memory state, which is acceptable for development smoke testing but not durable enough for serious use.

This is enough for:

- ChatGPT Custom GPT Actions use
- ChatGPT-style remote MCP use for development
- future Gemini / Claude hosted compatibility
- mobile / away-from-desk continuity

For the ChatGPT-side connection steps, see `docs/CHATGPT_CONNECTOR_WALKTHROUGH.md`.

## Custom GPT Actions Adapter

OpenAI's near-term Custom GPT distribution path expects an OpenAPI action schema. The relay exposes that schema at:

- `https://<relay-host>/gpt/openapi.json`

The public privacy policy URL for GPT Builder is:

- `https://<relay-host>/privacy`

The stable action surface is deliberately tiny:

- `help`
- `about`
- `submit_feedback`
- `list_tools`
- `tool_search`
- `use_tool`

`list_tools` returns a basic shortlist. `tool_search` reflects the current relay profile, advertised local capabilities, and disclosure posture, including one-time/rare tools such as `onboarding`. `use_tool` accepts a tool name plus arbitrary JSON arguments and routes into the same hosted queue/session machinery as the MCP adapter. `submit_feedback` is a top-level action so remote operators keep the feedback path visible.

This lets Astrata change the live tool catalog without forcing a Custom GPT schema update every time.

## MCP Adapter Contract

The hosted relay exposes two MCP-compatible adapter entrypoints:

- `POST /mcp`
- `POST /adapters/chatgpt/mcp`

Both entrypoints use the same internal adapter path. The `chatgpt` route exists so connector-specific behavior can grow without baking Chat assumptions into the generic bridge. Future adapters should add routes such as `/adapters/gemini/mcp` or `/adapters/claude/mcp`, then translate their client-specific request shape into the same relay primitives:

- `initialize`
- `tools/list`
- `tools/call`
- local queue delivery
- `get_result`
- `get_session`

The adapter is asynchronous by default. A remote client can submit work and receive a fast confirmation instead of waiting for the local Astrata instance to be online and finished. The confirmation includes:

- `status: received`
- `request_id`
- `session_id`
- a short instruction to poll `get_result` or `get_session`

This is the current Chat-friendly contract: send the message, tell the user it was received, and check back periodically.

The public connector URL for ChatGPT should be the HTTPS `/mcp` endpoint:

- `https://<relay-host>/mcp`

The MCP adapter remains useful as a development bridge and future agent-to-agent interop surface. The relay also keeps `/adapters/chatgpt/mcp` available for connector-specific evolution, but `/mcp` is the simplest MCP URL.

Write-like tools should include an `idempotency_key` or `client_request_id` when available. ChatGPT and other clients may retry tool calls; the adapter stores queued-request receipts by stable request key so retries can replay the same acknowledgement instead of creating duplicate Astrata work.

The current receipt cache is enough for ordinary sequential retries. It is not a hard concurrent write lock, because Cloudflare KV is eventually consistent. If the relay starts handling higher-volume write traffic, move queueing and idempotency into a Durable Object or another atomic coordinator.

## Dialogue Sessions

Every adapter request can include a `session_id`. If it does not, the relay assigns a default session for the profile.

Sessions store a lightweight transcript plus read-receipt timestamps:

- `remote_last_seen_at`
- `local_last_seen_at`

Remote clients should use `get_session` to fetch the current transcript and receipts. Local Astrata marks sessions seen when it heartbeats, and result delivery appends a local message to the same session.

The current Cloudflare KV backend is eventually consistent. That means immediate reads after writes can occasionally be stale. The contract is designed for periodic polling, not strict realtime delivery.

The Worker coalesces unchanged heartbeat advertisements and only marks sessions seen when pending work is present. This keeps the free-tier relay from spending a KV write on every liveness pulse. If KV write quota is exhausted anyway, the Worker degrades to isolate-local memory instead of throwing a 500, but queued work is not reliably durable until KV recovers or the relay graduates to Durable Objects / a real backend.

## Configuration

The Worker accepts a default relay profile with:

- `RELAY_DEFAULT_PROFILE_ID`

This should now be treated as a private-dev fallback. The better quick v0 path is:

- local Astrata mints a short-lived pairing code for a specific `profile_id`
- ChatGPT completes OAuth with that pairing code
- the resulting OAuth access token is bound to that `profile_id`

If `RELAY_DEFAULT_PROFILE_ID` is not set, shared-token adapter clients must pass `profile_id` as a query parameter. OAuth-bound ChatGPT clients should not need to.

## Required Pieces

To make the hosted relay actually useful, we need:

1. Hosted relay server
   It accepts authenticated MCP calls from remote clients.

2. Relay profile registry
   Each profile declares:
   - exposure surface (`chatgpt`, `gemini`, `claude`, `generic`)
   - control posture
   - disclosure ceiling
   - auth mode

3. Local Astrata outbound link
   The local instance must heartbeat outward so the hosted relay knows:
   - whether Astrata is online
   - which bridge id to use
   - what queue depth exists
   - what capabilities are currently safe to expose

4. Queueing
   If local Astrata is offline, hosted requests should queue instead of disappearing.

5. Connector-safe projections
   `search`, `fetch`, and `get_task_status` should return summaries shaped for the caller's allowed tier.

## Control Postures

Supported relay postures:

- `true_remote_prime`
  The remote side is Prime. Local Prime is absent or subordinate.

- `peer`
  The remote side is a partner system. It can cooperate without silently becoming Prime.

- `local_prime_delegate`
  Local Prime remains authoritative. The remote side may act as part of the system, but under Local Prime's authority.

- `local_prime_customer`
  The remote side is informational only. It can inspect safe state but not direct the system.

## Suggested Defaults

- `true_remote_prime`
  - auth required: yes
  - default disclosure ceiling: `trusted_remote`
  - default tools: `search`, `fetch`, `submit_task`, `get_task_status`, `list_capabilities`, `message_prime`, `delegate_subtasks`, `handoff_to_controller`, `request_browser_action`

- `peer`
  - auth required: yes
  - default disclosure ceiling: `connector_safe`
  - default tools: `search`, `fetch`, `submit_task`, `get_task_status`, `list_capabilities`, `message_prime`

- `local_prime_delegate`
  - auth required: yes
  - default disclosure ceiling: `connector_safe`
  - default tools: `search`, `fetch`, `submit_task`, `get_task_status`, `list_capabilities`, `message_prime`

- `local_prime_customer`
  - auth required: yes
  - default disclosure ceiling: `public`
  - default tools: `search`, `fetch`, `get_task_status`, `list_capabilities`

## Immediate Build Steps

Completed first-pass pieces:

- deployed one hosted relay endpoint on Cloudflare Workers
- registered one relay profile for ChatGPT-compatible use
- registered one local outbound link from the user's Astrata instance
- added GPT Actions adapter routes: `help`, `about`, `submit_feedback`, `list_tools`, `tool_search`, and `use_tool`
- added hosted privacy policy page for GPT Builder
- added authenticated relay and MCP-adapter `tools/list` / `tools/call` routes
- added local heartbeat plus queue drain
- added safe task and memory projection scaffolding for connector reads

## CLI Surfaces

Current local scaffolding:

- `astrata mcp-relay-status`
- `astrata mcp-register-relay-profile`
- `astrata mcp-register-relay-link`
- `astrata mcp-relay-heartbeat`
- `astrata mcp-relay-watch`

For interactive remote-Prime development, keep the watcher running:

```bash
./.venv/bin/python -m astrata.main mcp-relay-watch \
  --profile-id 4c5cb217-c30e-46c4-b1f8-31eeddb39ab3 \
  --interval-seconds 30
```

## What Is Still Missing

Still not done:

- remote auth/session rotation and revocation
- production OAuth provider instead of the quick single-user Worker-hosted dev bridge
- stronger adapter auth than dev bearer-token or query-token fallbacks
- production-grade GPT Actions auth that cleanly replaces the dev bearer-token bridge
- production-grade local outbound heartbeat supervision integrated with the app/daemon lifecycle
- Durable Object or backend-backed queueing so relay writes do not depend on KV daily write quota
- richer connector-safe task and memory fetch adapters
- realtime delivery or websocket/SSE upgrade path, if polling becomes too limiting
- ChatGPT connector screenshots
- metering and quota enforcement for hosted traffic
