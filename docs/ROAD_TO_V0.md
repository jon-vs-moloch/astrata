# Road To v0

## Current v0 Definition

Astrata v0 is the first build that a friendly tester can install, operate locally, and connect to a remote operator surface without routing through a shared developer identity.

The target is not full constellation autonomy. The target is:

- local desktop/runtime comes up reliably
- Prime, Local, Reception, and worker lanes can preserve handoffs
- KiloCode and Google AI Studio can absorb cheap/generous worker traffic
- hosted bridge activation is account/invite gated
- connector projections expose only connector-safe task and memory views
- distribution artifacts and update manifests are legible

## Current Status

Working or restored:

- local UI snapshot and message flow
- local runtime ensure/adoption path
- durable agent fallback from Prime to Reception or Local
- account/auth control-plane scaffold
- owned desktop device and relay-link pairing scaffold
- MCP bridge and hosted relay scaffold
- connector-safe task and memory projections
- relay delivery checks for owned profile/device links
- OAuth-shaped relay tokens bound to account/profile/device context
- KiloCode model registry refresh command
- Google AI Studio model sync/list/default commands
- Routine scaffold for recurring Procedure work

Not v0-safe yet:

- production OAuth account flow
- per-user durable relay queue beyond development storage
- signed/notarized release artifacts
- hosted bridge metering and quota controls
- full desktop lifecycle supervision

## Immediate Sequence

1. Keep tests green after the source restoration pass.
2. Clean source control: remove local Wrangler state and decide where release ZIPs live.
3. Promote `refresh-inference-registries` into a regular Routine.
4. Move hosted relay queueing from KV-shaped development storage to a serialized queue such as Durable Objects.
5. Add hosted authorize/token HTTP endpoints around the local OAuth-shaped registry.
6. Add revoke/default-device controls for friendly testers.
7. Add screenshots and exact setup steps for ChatGPT connector distribution.

## Operating Heuristic

When in doubt, v0 work should reduce operator rescue:

- repair local lane reliability before adding new remote powers
- route cheap worker work through KiloCode or Google before spending Prime
- turn repeated maintenance into Procedures, then Routines
- preserve disclosure boundaries even when remote access is convenient
