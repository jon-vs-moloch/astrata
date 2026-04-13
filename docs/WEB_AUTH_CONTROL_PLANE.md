# Astrata Web Auth And Control Plane

## Decision

Astrata Web is the identity and routing control plane.

Cloudflare can remain the edge relay for development and early v0, but the current Worker + KV shape is not the production account system. The next production-shaped step is:

- real user accounts
- real OAuth for Custom GPT Actions
- per-user relay profile routing
- per-device local Astrata pairing
- queue storage that is not high-frequency Workers KV writes

## Why This Matters

The current GPT Actions bridge can talk to Astrata, but it is still single-user development infrastructure. Its default relay profile points at Jonathan's local Astrata instance. That is useful for proving the loop, but unsafe for distribution.

The production rule is:

`remote token -> user account -> relay profile -> local device/link -> permitted tools`

No remote action should route by a global default profile once the GPT is shared with anyone else.

## Product Surfaces

- `Astrata Desktop`
  The local node. It owns local-only memory, local model access, local tools, and secure-enclave boundaries.

- `Astrata Web`
  The hosted control plane. It owns accounts, OAuth, profile routing, device pairing, relay queues, subscription/metering status, and public docs/downloads.

- `Custom GPT`
  The near-term distribution surface. It should call Astrata Web through the stable GPT Actions schema: `help`, `about`, `submit_feedback`, `list_tools`, `tool_search`, and `use_tool`. One-time/rare guidance such as `onboarding` should be discovered through `tool_search` and called through `use_tool`.

- `ChatGPT App/MCP`
  A development and agent-interop bridge. Keep it, but do not depend on it for immediate tester distribution.

- `Astrata Mobile`
  Future companion/remote client. It should authenticate as the same user and route through Astrata Web, not directly to another user's local profile.

## Cloudflare Fit

Cloudflare Workers remain good for:

- public HTTPS edge
- low-latency action endpoints
- privacy policy and schema hosting
- OAuth endpoint stubs
- routing remote calls to per-user relay queues

Workers KV is not good as the primary queue:

- it has low free-tier write limits
- it is eventually consistent
- it is not an atomic queue/lock
- high-frequency heartbeat writes can exhaust quota quickly

Better Cloudflare-native options:

- Durable Objects for per-user or per-profile serialized relay queues and live coordination.
- D1 for relational account/profile/device metadata.
- Workers as the front door that routes to Durable Objects and D1.

This keeps the current hosting investment useful while replacing the weak storage layer.

## Real Auth Shape

The production OAuth shape should be:

1. User creates or signs into an Astrata Web account.
2. Astrata Web creates a user id and default relay profile.
3. Astrata Desktop pairs to that account using a browser login or short pairing code.
4. Custom GPT Actions uses OAuth, not the shared relay token.
5. The OAuth access token resolves to a user id.
6. `tool_search` resolves allowed tools for that user/profile.
7. `use_tool` queues work only for that user's relay profile.
8. User can revoke the GPT, rotate tokens, or disconnect a local device.

## Tester Access Policy

For v0-friendly tester distribution, Astrata should separate public product access from metered cloud access.

- Public:
  - download pages
  - desktop installers
  - local-first onboarding and setup guidance
  - local runtime bootstrap
  - local model and voice asset setup that does not require Astrata-hosted control-plane capacity
- Invite-gated:
  - hosted Astrata account activation for testers
  - GPT bridge sign-in
  - relay profile creation
  - remote queue usage
  - any hosted control-plane feature that consumes metered Astrata infrastructure

The default rule is:

`download/install is public; remote bridge activation is eligibility-gated`

This keeps the top of funnel open while reserving the actual cloud-cost surface for invited testers until subscriptions or another billing model exist.

## Minimal Migration Plan

1. Disable default-profile routing for shared GPT builds.
   Keep the current `RELAY_DEFAULT_PROFILE_ID` only for private dev deployments.

2. Add user/account schema.
   Required records:
   - `users`
   - `oauth_clients`
   - `oauth_authorization_codes`
   - `oauth_access_tokens`
   - `relay_profiles`
   - `local_devices`
   - `device_links`
   - `relay_sessions`
   - `relay_requests`
   - `relay_results`

3. Add device pairing.
   Desktop requests a pairing code, user signs in on Astrata Web, Web binds that local device to the user's relay profile, Desktop stores a local link token.

4. Move queueing off KV.
   Route each `user_id/profile_id` to a Durable Object queue or an equivalent backend-backed queue.

5. Upgrade GPT Actions auth.
   Replace API-key bearer auth with OAuth in the GPT Builder, using Astrata Web's production authorization and token URLs.

6. Add account controls.
   User can see connected GPTs/devices, revoke sessions, rotate local-link tokens, and disable remote control.

## Security Rails

- Shared bearer tokens are development-only.
- GPT Actions must route by authenticated user, never by global profile, before broader distribution.
- Local-only and enclave-only data must not transit Astrata Web.
- Connector-safe projections must stay tiered by relay profile and user permissions.
- Tool availability must come from `tool_search`, not from a hardcoded GPT prompt.
- User onboarding state should become an account-level field once Astrata Web owns identity, starting with a simple flag or timestamp such as `gpt_onboarded_at` before growing into richer curricula.

## Open Questions

- Whether to use Cloudflare D1 for accounts immediately, or use an external managed Postgres/auth provider and keep Cloudflare as edge only.
- Whether Durable Objects are enough for all relay queues, or whether long-term constellation messaging wants a dedicated event bus.
- Whether Astrata accounts should initially use email magic link, passkeys, or an external OAuth provider.
- Whether tester billing/subscription state should live in Astrata Web v0 or wait until distribution expands.
