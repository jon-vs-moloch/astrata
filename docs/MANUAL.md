# Astrata Manual

## Purpose

This manual exists to make Astrata legible to:

- the operator
- Prime
- durable assistants working within their disclosure tier

The goal is not exhaustive prose. The goal is to make the system easy to understand, operate, and improve.

For the current release path, see `docs/ROAD_TO_V0.md`.
For the hosted account/auth direction, see `docs/WEB_AUTH_CONTROL_PLANE.md`.
For the concrete implementation sequence, see `docs/ACCOUNT_AUTH_IMPLEMENTATION_PLAN.md`.

## System Shape

Astrata is not a chatbot.
Astrata is a local-first operating environment for AI-mediated computing.

Core layers:

- `Procedures`: reusable named ways of doing work
- `Tools`: bounded actions that procedures and agents can invoke
- `Durable agents`: Prime, reception, local, and future staff
- `Memory`: the durable encyclopedia / knowledge substrate
- `Inference`: cloud, CLI, and local lanes
- `Runtime`: local model execution, thermal control, process management
- `Bridges`: MCP and future constellation-facing boundaries
- `UI / Web presence`: operator surfaces and public metadata surfaces

## Loops

Astrata is being built toward three loops:

- `L0`: bounded self-improvement inside the repo and runtime
- `L1`: durable autonomous operation with reliable local/cloud execution lanes
- `L2`: constellation-grade multi-node coordination

Current priority is closing `L0` cleanly, then making `L1` reliable enough that the system can keep improving without constant rescue.

## Prime Operating Heuristic

When choosing the next change:

1. Prefer fixes that unblock durable operation over cosmetic improvements.
2. Prefer work that can be expressed as a Procedure or Tool over one-off behavior.
3. Prefer changes that make the system more legible to itself.
4. Prefer fixes that remove repeated operator intervention.

## Core Procedures

Important current procedures include:

- `system-onboarding`
- `message-task-bounded-file-generation`
- `task-decomposition`
- `publish-to-internet`
- `ensure-local-lane`

Rule of thumb:
If a behavior recurs, it should become a Procedure.

## Core Tools

Important current operator/runtime tools include:

- `onboarding-status`
- `onboarding-recommended-settings`
- `local-runtime-ensure`
- `local-runtime-start`
- `local-runtime-status`
- `voice-status`
- `voice-preload-defaults`
- `voice-install-asset`
- `mcp-server`
- `web-presence-server`
- `supervisor-status`
- `supervisor-reconcile`
- `supervisor-stop`

Rule of thumb:
If an action is bounded and directly invocable, it should become a Tool surface.

## Local Lane

The local lane matters because it is the minimum autonomy floor.

Current expectations:

- stale managed-process state should self-clear
- thermal cooldown should not latch forever after nominal conditions return
- the operator and Prime should have an idempotent way to bring the lane up

Primary tool:

- `astrata local-runtime-ensure`

If the local lane is unhealthy, restoring it is usually higher leverage than adding another feature.

## Memory And Disclosure

Astrata should remain legible without violating tiered disclosure.

Guidelines:

- public/operator docs may describe system structure freely
- sensitive task content should not be exposed across tiers
- manuals should explain the existence of boundaries even when a given reader cannot cross them
- artifacts and status views should say what kind of thing happened even when full details are redacted

## Registries

Astrata should prefer observed registries over static assumptions.

Current examples:

- provider registry
- local model catalog
- voice asset registry with observed install size

Rule of thumb:
When Astrata learns a real operational fact, it should record it so the mistake is not repeated.

## Operator Routine

Recommended routine when advancing the system:

1. Check `astrata doctor`
2. Check `astrata loop0-next`
3. Reconcile the always-on lane with `astrata supervisor-reconcile`
4. Pick the highest-leverage bounded change
5. Express the result as a Procedure/Tool when plausible
6. Update the manual if the system shape changed

## Always-On Supervisor

Astrata's v0 supervisor is the owner-of-last-resort for the pieces that make the system feel alive:

- desktop UI backend
- Loop0 daemon
- hosted MCP relay watcher
- local inference lane health/adoption

Primary tools:

- `astrata supervisor-status`
- `astrata supervisor-reconcile`
- `astrata supervisor-stop`

`supervisor-reconcile` should adopt matching live processes and healthy endpoints before it starts anything new. That is the guard against the failure mode where the desktop shell closes, the backend or local model process survives, and a later restart tries to bind the same port again.

## Near-Term Bottlenecks

Current likely bottlenecks:

- reliable supervisor ownership across desktop, Loop0, relay, and local runtime surfaces
- getting Loop0 to use the local lane automatically when healthy
- making registries first-class inputs to planning and routing
- strengthening the manual so Astrata can navigate its own architecture more efficiently

## Remaining Work

Current operator-visible remaining work:

- make desktop/backend lifecycle fully deliberate:
  close prompt, graceful backend stop, keep-alive on ordinary close, and explicit resume/recovery paths
- make backend recovery work not only while the desktop shell is alive, but under the supervisor shape
- make Loop0 use the durable comms/task path without stalling on avoidable runtime duplication
- promote local runtime endpoint adoption from live reconciliation into fully persistent owned process state
- define and build the hosted MCP relay for off-machine connector access
- project memory, task status, and summaries into connector-safe `search` / `fetch` surfaces
- enforce hosted disclosure tiers so remote connectors never bypass local-only or enclave-only boundaries
- define metering, quotas, and operator telemetry for hosted bridge traffic

If a remaining item is likely to recur or become a staff responsibility, it should be turned into:

- a Procedure
- a Tool
- a durable task or bridge event
