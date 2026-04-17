# Astrata Manual

## Purpose

This manual is the plain-language operator map for the current repo.

It is meant to answer:

- what Astrata is right now
- which surfaces are real
- how to inspect its state
- what we should trust versus treat as scaffold

For the v0 sequence, see [ROAD_TO_V0.md](/Users/jon/Projects/Astrata/docs/ROAD_TO_V0.md).

## System Shape

Astrata is a local-first operating environment for AI-mediated work.

The current repo has six important layers:

- procedures and bounded execution paths
- cloud and CLI provider routing
- local runtime management
- account and hosted-relay scaffolding
- memory and connector-safe projection logic
- UI and Loop 0 observability surfaces

## What Is Real Today

Working or restored enough to build on:

- local UI snapshot and message flow
- managed local runtime start/stop/status
- Loop 0 runner and observability path
- Google AI Studio model sync/list/default support
- KiloCode model registry sync/list support
- account, invite, pairing, and OAuth-shaped local control plane
- hosted relay profile/device-link authorization scaffolding
- richer memory model with disclosure-aware projection

## What Is Still Scaffold

Not yet a final product surface:

- hosted production login UX
- durable hosted queueing
- full remote connection management UI
- signed/notarized release distribution
- mature always-on desktop supervision UX

## Core CLI Surfaces

High-signal commands that exist today:

```bash
astrata doctor
astrata loop0-next
astrata loop0-run --steps 1
astrata local-runtime-start --model-id <model> --profile quiet
astrata local-runtime-stop
astrata local-runtime-status
astrata google-models-sync
astrata google-models-list
astrata google-set-default-model <model>
astrata kilocode-models-sync
astrata kilocode-models-list
astrata account-status
astrata account-issue-invite --label "friendly tester"
astrata account-redeem-invite --email you@example.com --invite-code <code>
astrata account-pair-device --email you@example.com --relay-endpoint https://<relay-host>/mcp
astrata account-register-oauth-client --label ChatGPT --redirect-uri <callback-url>
astrata account-issue-oauth-code --client-id <client-id> --email you@example.com --redirect-uri <callback-url>
astrata account-exchange-oauth-code --client-id <client-id> --code <code> --redirect-uri <callback-url>
astrata routines-list
astrata routine-run refresh-inference-registries
```

## Procedure Shape

Important procedures currently defined in code:

- `system-onboarding`
- `loop0-bounded-file-generation`
- `message-task-bounded-file-generation`
- `task-decomposition`
- `publish-to-internet`
- `ensure-local-lane`

These live in [registry.py](/Users/jon/Projects/Astrata/astrata/procedures/registry.py).

Rule of thumb:
if a behavior repeats, it should become a procedure or a routine.

## Operator Routine

When moving the system forward:

1. Run `astrata doctor`
2. Check `astrata loop0-next`
3. Verify the local runtime is healthy
4. Sync inference registries if provider/model surfaces changed
5. Make one bounded improvement
6. Re-run tests before trusting the result

## Memory And Disclosure

Astrata's memory layer is no longer just a transcript cache.
It is moving toward a permissioned encyclopedia with:

- revisions
- links
- embeddings
- disclosure tiers
- projected views for remote consumers

Remote-facing code should consume projected snippets, not raw records.

## Relay And Remote Operation

Current remote-operation rule:

`token -> account -> profile -> owned device link -> permitted tools`

That rule is already reflected in the local account and hosted-relay scaffolding. The missing piece is the hosted production web auth and queue layer around it.

## Practical Priorities

When in doubt, prefer work that:

- removes operator rescue
- strengthens local runtime reliability
- preserves disclosure boundaries
- turns repeated maintenance into procedure or routine form
- keeps tests green while salvaging older work

## Current Bottlenecks

Most likely near-term bottlenecks:

- durable hosted relay queueing
- hosted authorize/token endpoints
- connection revocation/default-device controls
- supervised desktop/runtime lifecycle
- release/distribution hardening
