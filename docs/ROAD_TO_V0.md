# Road To v0

## v0 Definition

Astrata v0 is the first version that an early tester can install, connect, and leave running with confidence.

It does not need to be the full operating-system vision yet. It does need to prove the loop:

- install the app
- connect inference
- bring up local memory and runtime state
- communicate with Astrata locally and remotely
- watch it make bounded progress
- recover cleanly when something goes wrong

The v0 bar is not "feature complete." The bar is "alive, legible, useful, and hard to accidentally wedge."

## Product Pillars

- `Alive computer`: Astrata should feel like a living operating environment, not a chatbot window.
- `Local-first security`: sensitive local state must not leave the machine without deliberate policy and disclosure controls.
- `Always reachable`: desktop, local, and remote connector entrypoints should route to someone, even when Prime or the internet is unavailable.
- `Self-stabilizing`: the system should notice stopped loops, stale processes, queue buildup, quota pressure, and thermal pressure.
- `Composable`: local inference, cloud inference, memory, browser, MCP, voice, and future constellation pieces should remain swappable.

## Current v0 Milestones

1. Stabilize the always-on runtime.
   Ensure the desktop/backend lifecycle, local runtime, relay watcher, and Loop0 daemon can start, stop, recover, and report health without manual rescue.

2. Make remote Prime usable.
   Keep the ChatGPT relay connected, ship the Custom GPT Actions adapter as the near-term distribution path, keep MCP as a development bridge, move from the dev auth bridge to production OAuth, and make the connector expose enough context that ChatGPT understands Astrata's role and boundaries.

3. Close the L0 self-improvement loop.
   Keep Loop0 running on safe bounded work, make it use durable tasks and worker queues cleanly, and prevent it from duplicating or stalling on stale runtime state.

4. Bring memory online as a first-class substrate.
   Maintain a tiered-disclosure encyclopedia with provenance, history, relational links, connector-safe summaries, and vector-style retrieval.

5. Polish the operator product.
   The UI should show runtime health, queue state, pending work, connector status, memory status, local model health, and clear recovery controls.

6. Ship installation and updates.
   Provide an installer, dependency bootstrap, first-run onboarding, local model discovery/download, voice defaults, and an automatic update path.
   The current scaffold assumes Cloudflare Pages for the public download/update site, R2 for release artifacts, Workers for invite/entitlement gates and signed download URLs, and D1 for release/account metadata.
   Release cadence should be explicit: `edge` for every successful build, `nightly` for promoted daily builds, `tester` for curated prereleases, and `stable` for manual public releases. The UI now exposes these in the Settings panel.

7. Prepare public infrastructure.
   Stand up the real web presence and backend needed for OAuth, subscriptions, user/account records, model/provider catalogs, downloads, and future constellation coordination.

See `docs/WEB_AUTH_CONTROL_PLANE.md` for the current auth/control-plane decision.

## Immediate Priorities

- Use `astrata supervisor-reconcile` as the default bring-up path for the UI backend, Loop0 daemon, MCP relay watcher, and local runtime lane.
- Keep `mcp-relay-watch` running while the ChatGPT connector is in use, either under the supervisor or as an explicitly adopted process.
- Repair stale local-runtime process state so live endpoints are adopted cleanly and dead process records do not block restarts.
- Add an operator dashboard panel that distinguishes hosted relay queue depth from local task backlog and active worker count.
- Make `list_capabilities` and related connector projections explain Astrata's role, control posture, and disclosure boundary.
- Keep the Custom GPT Actions schema stable around `help`, `about`, `submit_feedback`, `list_tools`, `tool_search`, and `use_tool`, with one-time/rare guidance such as `onboarding` discovered through `tool_search` and called through `use_tool`.
- Document and implement a production OAuth migration plan for both Custom GPT Actions and MCP-style bridges.
- Build Astrata Web into the account, OAuth, device-pairing, and per-user relay-routing control plane.
- Keep public download and local-first onboarding open, but gate hosted bridge access behind account eligibility and invite controls until metering/billing is live.

## Access Policy For Friendly Testers

- Downloading Astrata should be public.
- Local install, desktop bring-up, and local-only onboarding should not require an invite code.
- Invite codes should gate hosted account creation or hosted bridge activation, because that is where Astrata starts allocating metered cloud resources such as relay queues, OAuth sessions, and remote control-plane state.
- Local model downloads or other third-party-hosted assets do not by themselves require an Astrata invite code, though the UI should disclose when a download is leaving Astrata's own infrastructure and may have its own provider terms or rate limits.
- Before monetization exists, the first billing boundary should live at the cloud access layer rather than the desktop download layer.
- The product should therefore support a friendly-tester posture of: anyone can download and install; only invited accounts can activate hosted bridge features.

## Health Signals v0 Must Show

- Desktop backend status and whether it was deliberately stopped.
- Supervisor status for UI backend, Loop0 daemon, hosted relay watcher, and local runtime adoption.
- Hosted relay last heartbeat, pending requests, consumed requests, and result-post status.
- Local task counts by status: pending, working, blocked, complete, failed, satisfied, superseded.
- Active Loop0 daemon status and last heartbeat artifact.
- Local inference endpoint health, owning process, selected model, and stale-process/adopted-endpoint state.
- Quota, thermal, and memory-pressure guardrails.
- Connector-safe disclosure tier for each remote profile.

## Known Risks

- The current OAuth implementation is a development bridge that uses the relay token as a single-user consent password.
- The Custom GPT Actions adapter currently uses the same hosted relay and should remain private development infrastructure until auth, privacy, and account controls are product-grade.
- Cloudflare KV is eventually consistent and is not a hard concurrent queue lock.
- Free-tier KV write quota is too small for high-frequency heartbeat writes; the relay now coalesces heartbeats, but durable queueing should move to Durable Objects or a real backend.
- Loop0 can stop while child runtime processes keep running, leaving stale process state and port conflicts.
- The first supervisor pass can adopt live processes, but adopted process ownership is still intentionally conservative: `supervisor-stop` will not stop adopted processes unless explicitly told to.
- The hosted relay queue depth can be `0` while local Astrata still has pending work, because these are different queues.
- The current UI/backend lifecycle still needs tighter desktop-shell integration so deliberate close/stop intent is captured in the supervisor state.

## Graduation Criteria

v0 is ready for a friendly early tester when:

- first-run setup can complete without hand-editing config
- local runtime and remote connector health are visible and recoverable
- ChatGPT or another remote Prime can send a request and receive status without manual relay draining
- Loop0 can run continuously in a conservative mode without runaway memory, quota, or duplicate-task behavior
- sensitive local state has enforceable disclosure boundaries
- the desktop app can be closed, resumed, and updated without orphaning or duplicating backends
