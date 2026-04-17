# Astrata Account Auth Implementation Plan

## Goal

Make the remote operator path safe for friendly-tester distribution.

The required routing rule is:

`OAuth token -> Astrata account -> relay profile -> owned device link -> permitted tools`

That means:

- a GPT user signs into their own Astrata account
- the account owns one or more relay profiles
- Astrata Desktop pairs as an owned device for that same account
- remote requests resolve to one account-owned device context
- no shared developer bearer token remains in the distribution path

## Current Code State

The repo already has the local control-plane scaffold for this:

- account, invite, relay-profile, device, and device-link records
- OAuth client, authorization-code, and access-token records
- CLI surfaces for invite issuance, redemption, device pairing, and OAuth exchange
- hosted relay checks that profile/device links belong to the same user
- relay token resolution bound to `user_id/profile_id/device_id`

Relevant code:

- [accounts models](/Users/jon/Projects/Astrata/astrata/accounts/models.py)
- [accounts service](/Users/jon/Projects/Astrata/astrata/accounts/service.py)
- [MCP server auth path](/Users/jon/Projects/Astrata/astrata/mcp/server.py)
- [hosted relay service](/Users/jon/Projects/Astrata/astrata/mcp/relay.py)

This is enough for local development and controlled tester work. It is not yet a production web auth system.

## Product Rule

Pairing is not identity.

Pairing should answer:

- which device belongs to this authenticated user
- which relay profile that device should serve

Identity should be owned by Astrata Web:

- account login
- OAuth consent
- device ownership
- connection revocation
- default-profile and default-device controls

## v0 Target User Flow

1. User opens the Astrata GPT.
2. ChatGPT starts OAuth against Astrata Web.
3. User signs into their Astrata account.
4. Astrata Web resolves the user's relay profiles and paired devices.
5. User selects a target profile/device, or Astrata Web uses the default.
6. Astrata Web issues an access token bound to that user/profile/device context.
7. The hosted relay accepts only tools that profile is allowed to use.
8. The user can later revoke the GPT connection or change their default device.

## Local CLI Surfaces We Already Have

These are the current repo-backed primitives:

```bash
astrata account-status
astrata account-issue-invite --label "friendly tester"
astrata account-redeem-invite --email you@example.com --invite-code <code>
astrata account-pair-device --email you@example.com --relay-endpoint https://<relay-host>/mcp
astrata account-register-oauth-client --label ChatGPT --redirect-uri <callback-url>
astrata account-issue-oauth-code --client-id <client-id> --email you@example.com --redirect-uri <callback-url>
astrata account-exchange-oauth-code --client-id <client-id> --code <code> --redirect-uri <callback-url>
```

These commands are local scaffolding around the registry. They are not the final hosted web UX.

## Durable Records

Minimum records for the v0 path:

- `users`
- `invites`
- `relay_profiles`
- `devices`
- `device_links`
- `oauth_clients`
- `oauth_authorization_codes`
- `oauth_access_tokens`

Desirable next records once the hosted flow exists:

- `account_sessions`
- `gpt_connections`
- `relay_requests`
- `relay_results`

## Remaining Phases

### Phase 1: Hosted Authorize/Token Endpoints

Build real HTTP authorize/token endpoints around the current registry.

Acceptance:

- ChatGPT can complete OAuth without local-only operator steps
- issued tokens resolve to account/profile/device context

### Phase 2: Device And Connection Management

Expose:

- connected devices
- connected GPT clients
- revoke/disconnect controls
- default-profile and default-device selection

Acceptance:

- a tester can recover from bad pairing without operator rescue

### Phase 3: Durable Queueing

Move relay queueing off development-shaped JSON/KV patterns and into a serialized per-profile queue.

Acceptance:

- a user's remote work is isolated by account/profile
- retries and reconnects do not duplicate or lose work casually

### Phase 4: Distribution Safety Gates

Before broader sharing:

- no shared default-profile routing
- no developer bearer token fallback in the user path
- no routing by pairing code alone

## Near-Term Build Order

1. Keep the current local control-plane code green.
2. Add hosted authorize/token endpoints around the existing registry.
3. Add revoke/default-device/default-profile operations.
4. Move hosted queueing to a durable per-profile queue.
5. Hook the Custom GPT flow to the hosted OAuth path.

## Non-Goals For v0

Not required yet:

- enterprise SSO
- full billing system
- perfect long-term account schema
- multi-tenant operations tooling beyond what testers need

The bar is simpler: safe, user-owned remote routing for friendly testers.
