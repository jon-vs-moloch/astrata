# Astrata Account Auth Implementation Plan

## Goal

Reach the point where:

- each GPT user signs into their own Astrata account
- each account owns one or more relay profiles
- each Astrata Desktop client signs into that same account and registers as a user-owned device
- GPT OAuth tokens resolve to `user_id -> profile_id -> device selection`
- no distributed GPT routes through a shared bearer token or a global default profile

## Non-Goal

This plan is not trying to deliver:

- enterprise SSO
- billing-complete SaaS infrastructure
- a perfect long-term queue architecture on the first pass

The v0 target is user-safe distribution:

`GPT user auth -> Astrata account -> owned desktop device(s) -> per-user relay routing`

## Product Rule

Pairing is not identity.

Pairing should answer:

- which device belongs to this authenticated user
- which profile should remote work target

Identity should be owned by Astrata Web:

- user account
- login session
- OAuth consent
- device ownership
- revocation

## Current State

The repo already has:

- hosted relay OAuth endpoints in the Cloudflare Worker
- OAuth tokens bound to `profile_id`
- desktop-minted pairing codes
- per-profile relay routing
- desktop UI support for generating pairing codes

That is a good development bridge, but it is still not distribution-safe because the GPT user is not proving account ownership to Astrata Web.

## v0 Target User Flow

1. User opens the Astrata GPT.
2. ChatGPT starts OAuth against Astrata Web.
3. User signs into their Astrata account.
4. Astrata Web loads the user's registered relay profiles and paired desktop devices.
5. User chooses a target device/profile, or Astrata Web uses the default.
6. Astrata Web issues an OAuth token bound to `user_id`, `profile_id`, and the selected device context.
7. GPT requests route only to that user's relay queue and permitted tools.
8. User can later review and revoke the GPT connection or change the default device/profile.

## Architecture

### Surfaces

- `Astrata Desktop`
  The local trusted node. It signs into Astrata Web, registers devices, stores device credentials, and receives routed relay work.

- `Astrata Web`
  The account, OAuth, and routing control plane. It owns user identity, device ownership, relay profiles, and revocation controls.

- `Hosted Relay`
  The edge-facing bridge. It terminates GPT and MCP traffic, validates Astrata-issued OAuth tokens, and routes requests by authenticated user/profile.

### Routing Rule

Every remote request should resolve through this chain:

`access token -> user account -> relay profile -> selected device/link -> permitted tools`

No distributed GPT should route by:

- `RELAY_DEFAULT_PROFILE_ID`
- shared bearer token
- desktop-generated secret alone

## Data Model

The minimum durable records are:

- `users`
- `account_sessions`
- `relay_profiles`
- `devices`
- `device_links`
- `oauth_clients`
- `oauth_authorization_codes`
- `oauth_access_tokens`
- `gpt_connections`
- `relay_requests`
- `relay_results`

Recommended minimum fields:

### users

- `user_id`
- `email`
- `display_name`
- `status`
- `default_profile_id`
- `gpt_onboarded_at`
- `created_at`
- `updated_at`

### relay_profiles

- `profile_id`
- `user_id`
- `label`
- `control_posture`
- `disclosure_tier`
- `default_device_id`
- `created_at`
- `updated_at`

### devices

- `device_id`
- `user_id`
- `label`
- `platform`
- `status`
- `last_seen_at`
- `created_at`
- `updated_at`

### device_links

- `link_id`
- `device_id`
- `profile_id`
- `relay_endpoint`
- `link_token_hash`
- `status`
- `last_heartbeat_at`
- `created_at`
- `updated_at`

### gpt_connections

- `connection_id`
- `user_id`
- `profile_id`
- `oauth_client_id`
- `status`
- `last_used_at`
- `created_at`
- `updated_at`

## Implementation Phases

### Phase 1: Account Schema And Control Plane Surface

Goal:
Create durable account/device/profile models and expose a hosted control-plane surface that describes the system state.

Deliverables:

- account-control-plane data models in the repo
- durable state store abstraction for users, profiles, devices, and OAuth records
- Astrata Web presence endpoints for auth/control-plane status and schema
- repo docs that describe the exact migration path

Acceptance:

- the repo has a stable account/device/profile schema
- Astrata Web can report auth-control-plane readiness
- future desktop and relay work have a real module boundary to target

### Phase 2: Desktop Sign-In And Device Registration

Goal:
Let Astrata Desktop authenticate to Astrata Web and register itself as a user-owned device.

Deliverables:

- desktop sign-in UI
- browser login callback flow
- device registration endpoint
- persistent local device credential
- device status and default-profile selection in the desktop UI

Acceptance:

- a signed-in desktop appears under the correct user account
- one user can register multiple desktops

### Phase 3: User-Centric GPT OAuth

Goal:
Replace pairing-code identity with Astrata account identity during GPT OAuth.

Deliverables:

- Astrata Web-hosted login requirement in OAuth authorize flow
- device/profile selection screen after login
- OAuth code/token records bound to `user_id` and `profile_id`
- relay request routing by authenticated user/profile
- removal of shared-token requirement for distributed GPT builds

Acceptance:

- a new GPT user can connect using only their Astrata account
- the connection routes to their own desktop device(s), not Jonathan's

### Phase 4: Revocation, Defaults, And Recovery Controls

Goal:
Make the account system operable and supportable.

Deliverables:

- connected GPT list
- connected device list
- revoke/disconnect controls
- default profile/device settings
- token rotation and stale-device cleanup

Acceptance:

- a user can recover from a bad device binding without operator rescue

### Phase 5: Queue And Storage Hardening

Goal:
Move the relay from development-shaped storage to production-shaped routing durability.

Deliverables:

- queueing off the current lightweight KV-heavy pattern
- profile/device routing storage with stronger consistency
- serialized queue coordination per user/profile

Acceptance:

- relay routing is durable enough for friendly tester distribution

## Immediate Build Order

The next implementation steps should happen in this order:

1. Land durable account/device/profile models in the repo.
2. Expose control-plane schema/status through `astrata/webpresence`.
3. Add desktop sign-in and device registration primitives.
4. Update the relay OAuth authorize flow to require Astrata account login.
5. Replace manual pairing-code-first UX with user login plus device selection.
6. Add revoke/default-device controls.

## First Slice We Are Starting Now

The first repo slice starts here:

- create a durable implementation plan in `docs/`
- add account-control-plane models and registry scaffolding in code
- expose those models through Astrata Web presence endpoints

That does not finish user auth by itself, but it creates the durable schema and module boundary needed for the rest of the implementation.

