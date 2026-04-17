# Hosted MCP Relay

## Purpose

The hosted relay is the remote front door for Astrata.

It is not a second cloud runtime. It is:

- an authenticated edge surface
- a queue and rendezvous point
- a connector-safe projection surface
- a bridge into a user's local Astrata instance

## Current Code State

The repo currently contains:

- hosted relay profile and local-link records
- connector-safe tool catalog and advertisement logic
- account/device-link authorization checks
- OAuth-bound relay token resolution
- a Cloudflare worker scaffold for the hosted relay

Relevant code:

- [relay service](/Users/jon/Projects/Astrata/astrata/mcp/relay.py)
- [MCP server](/Users/jon/Projects/Astrata/astrata/mcp/server.py)
- [web auth surface](/Users/jon/Projects/Astrata/astrata/webpresence/server.py)
- [worker scaffold](/Users/jon/Projects/Astrata/deploy/cloudflare/mcp-relay/worker.js)

## Public Surfaces

Near-term connector shapes:

- MCP-style endpoint:
  `https://<relay-host>/mcp`
- Custom GPT Actions schema:
  `https://<relay-host>/gpt/openapi.json`
- Privacy policy:
  `https://<relay-host>/privacy`

Use GPT Actions for near-term tester distribution.
Keep MCP as the development and interop bridge.

## Stable GPT Actions Shape

The stable action surface should stay intentionally small:

- `help`
- `about`
- `submit_feedback`
- `list_tools`
- `tool_search`
- `use_tool`

That keeps the public schema stable while letting Astrata change the live tool catalog behind `tool_search`.

## Security Rule

The relay must route by authenticated user context, not by a shared global profile.

Safe route:

`remote token -> user account -> relay profile -> owned device link -> permitted tools`

Unsafe route:

- `RELAY_DEFAULT_PROFILE_ID`
- shared bearer token
- pairing code alone

## Current OAuth Shape

Astrata Web now exposes the hosted OAuth metadata and token routes:

- `/.well-known/oauth-authorization-server`
- `/.well-known/oauth-protected-resource`
- `/oauth/register`
- `/oauth/authorize`
- `/oauth/token`
- `/oauth/introspect`
- `/oauth/revoke`

Those endpoints are backed by the current account registry, so bearer access is now resolved against the owned profile and active device link rather than a worker-local token store.

The next-worker adapter path is now straightforward:

- set `ASTRATA_CONTROL_PLANE_URL` in the relay worker
- let the worker keep the public OAuth URLs on the relay host
- have `/oauth/register`, `/oauth/authorize`, `/oauth/token`, and bearer introspection delegate to Astrata Web
- have `/relay/mcp`, `/relay/local/heartbeat`, `/relay/local/ack`, `/relay/local/result`, `/relay/result/{request_id}`, and `/relay/session/{session_id}` proxy to Astrata Web too

## Current Hosting Posture

Reasonable first posture:

- Cloudflare Workers for HTTPS edge
- one dev/staging relay
- OAuth-shaped auth for the real user path
- bearer-token fallbacks only for private-dev smoke tests

Workers KV or in-memory relay state is acceptable for development.
It is not enough for durable user-facing queueing.

## Queueing And Storage

The repo now carries a durable per-profile relay queue shape in Astrata Web:

- `pending_requests`
- `acked_requests`
- `results`
- `sessions`

That means the current authoritative queue path is:

- remote enqueue through `/relay/mcp`
- local delivery through `/relay/local/heartbeat`
- acknowledgment through `/relay/local/ack`
- result write through `/relay/local/result`
- lookup through `/relay/result/{request_id}` and `/relay/session/{session_id}`

This is still a JSON-backed v0 scaffold rather than a final broker, but it gives us durable per-profile coordination without creating a second source of truth in the worker.

Cloudflare-native candidates:

- Durable Objects for per-profile queue coordination
- D1 for account/profile/device metadata

The repo's current worker scaffold is useful as a front door, but not yet the full production relay backend.

## Relay Postures

Supported relay control postures in the codebase:

- `true_remote_prime`
- `peer`
- `local_prime_delegate`
- `local_prime_customer`

For v0, the practical default is `local_prime_delegate`:

- local Prime remains authoritative
- remote connectors can inspect safe state and submit bounded work
- disclosure remains connector-safe

## What The Relay Must Know

Minimum facts required to deliver remote work safely:

- which profile is being addressed
- which user owns that profile
- which local device link is active for that profile
- which tools that profile is allowed to use
- whether remote host bash has been specially acknowledged

## Remote Host Bash

`run_command` is intentionally special.

It should only be advertised after an explicit acknowledgement has been recorded for that profile. The code already supports this posture in the local account/relay scaffold.

## Immediate v0 Steps

1. Keep the current relay/account tests green.
2. Point the worker adapter at Astrata Web for OAuth client/code/token decisions.
3. Point the worker adapter at Astrata Web for authoritative queue/session/result state.
4. Keep connector-safe `search` / `fetch` / task-status projections narrow.
5. Add revoke/default-device controls.

## Deliberately Not Done Yet

Still missing for true distribution:

- fuller hosted production login UX
- durable queue backend
- full revoke/session-management UI
- stronger metering and quota controls
- final distribution screenshots and operator walkthroughs
