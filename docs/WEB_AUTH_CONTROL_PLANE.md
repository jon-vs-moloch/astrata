# Astrata Web Auth And Control Plane

## Decision

Astrata Web is the identity and routing control plane.

Cloudflare Workers can remain the edge relay for development and early v0, but the user-owned account system must live above the current single-user development bridge.

## Current Rule

The safe remote route is:

`token -> account -> relay profile -> owned device link -> permitted tools`

Anything weaker than that is development infrastructure, not distribution infrastructure.

## Roles

### Astrata Desktop

Owns:

- local memory
- local models
- local tools
- local-only disclosure boundaries
- the paired device identity

### Astrata Web

Owns:

- accounts
- hosted OAuth metadata and token surfaces
- login
- OAuth authorize/token flow
- relay profile ownership
- device ownership
- connection revocation
- queue routing
- per-profile relay queue/session/result authority

### Hosted Relay

Owns:

- public HTTPS edge
- connector entrypoints
- queue submission and delivery coordination
- profile/device authorization checks

## Current HTTP Surface

The repo now exposes the narrow hosted OAuth control-plane scaffold through Astrata Web:

- `GET /.well-known/oauth-authorization-server`
- `GET /.well-known/oauth-protected-resource`
- `POST /oauth/register`
- `POST /oauth/authorize`
- `POST /oauth/token`
- `POST /oauth/introspect`
- `POST /oauth/revoke`

These routes sit in front of the current local account registry. They are intentionally narrow:

- authorization-code flow only
- effective scope is `relay:use`
- issued tokens bind to one `user_id -> profile_id -> device_id`

That is enough to make Astrata, not the edge worker, the source of truth for connector access decisions.

## Why The Distinction Matters

The current repo can already prove the routing chain locally.
What it does not yet provide is the hosted web experience around it.
The metadata and token endpoints now exist, and the repo now includes a simple browser authorize flow for v0 testers.
The fuller end-user sign-in and consent UX is still to come.

Without Astrata Web owning identity:

- a shared GPT could route through the wrong profile
- pairing codes could be mistaken for authentication
- device selection and revocation would be too brittle

## v0-Friendly Access Policy

Public:

- download pages
- installer distribution
- local-first onboarding docs
- local model/runtime setup

Invite-gated:

- hosted account activation
- GPT OAuth sign-in
- remote queue usage
- hosted control-plane features that consume cloud resources

The guiding rule is:

`download/install is public; remote bridge activation is eligibility-gated`

## Recommended Hosted Shape

Short version:

- Workers for HTTPS edge and small adapter surfaces
- durable per-profile queueing
- relational account/profile/device metadata

Cloudflare-native candidates remain sensible:

- Workers for edge/API
- Durable Objects for serialized queue coordination
- D1 for account and routing metadata

## Immediate Build Sequence

1. Put hosted authorize/token endpoints in front of the current OAuth-shaped registry.
2. Add login and consent flow for GPT users.
3. Add default profile/device selection and revocation controls.
4. Move queueing to durable per-profile storage.
5. Remove shared default-profile assumptions from any shared-user path.

## Security Rails

- shared bearer tokens are dev-only
- pairing is device selection, not identity proof
- connector-safe projections remain mandatory
- local-only and enclave-only data must not leave the machine through the hosted bridge
- tool availability should come from profile policy and relay advertisement, not hardcoded GPT prompts

## Relationship To v0

This is not separate from the v0 plan. It is one of the gating pieces for v0.

We do not need a perfect long-term SaaS platform first.
We do need a user-owned account and routing path that prevents a tester GPT from accidentally operating on the wrong person's machine.
