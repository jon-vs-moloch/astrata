# Astrata — Build Path

## Purpose

This document defines the implementation path for Astrata.

It is not a roadmap for polishing a product.
It is a plan for crossing the threshold where recursive self-improvement becomes real.

The governing rule is:

> Build only what is necessary for the self-improvement loop to start, strengthen, and eventually sustain itself on modest resources.

Everything else is secondary.

---

## Central Objective

Astrata is being built to become a recursively self-improving agentic harness.

The path to that outcome has three loops to close:

### Loop 0

Astrata can autonomously build Astrata far enough to close Loop 1.

This means it can:

- read the governing docs and implementation plans
- inspect current implementation state
- identify the next highest-leverage missing slice
- implement that slice using available inference and tools
- verify whether the slice actually works
- retain the improvement and continue

Loop 0 is the ignition threshold.
It is not yet full recursive self-improvement in the wild, but it is the point where the system can meaningfully participate in completing its own bootstrap.

### Loop 1

Astrata can autonomously improve itself using **all available resources**.

This includes:

- all available inference providers
- local and cloud models
- high-capability models when warranted
- human clarification when feasible
- external tools
- background compute
- rich verification and audit

This is the first threshold because it is the easiest place to make self-improvement real.

### Loop 2

Astrata can autonomously improve itself using **few resources**.

This means:

- lower compute cost
- less operator attention
- less dependence on expensive inference
- more use of local models
- more reuse of procedures, artifacts, and validated structure

Loop 0 should be used to accelerate the construction of Loop 1.
Loop 1 should focus aggressively on changes that help close Loop 2.

That is the strategy:

> first wake it up enough to help build itself
> then teach the system to improve itself at all
> then teach it to do so cheaply, continuously, and locally

---

## Why The Provider Layer Matters

Astrata should assume that all available inference is in play.

This is not accidental convenience.
It is a strategic commitment.

The system should be willing to use:

- strong cloud models
- local models
- CLI-based model surfaces
- specialized providers
- weak but cheap models
- strong but expensive models

as long as doing so helps the self-improvement loop.

This matters especially for Loop 0.

If Astrata can already autonomously advance its own implementation, Astra’s broad provider layer becomes immediate bootstrap leverage:

- stronger models can help finish the self-improvement substrate
- multiple providers can accelerate diagnosis and implementation work
- expensive inference can be converted into durable architecture gains early
- broad inference access can help build the machinery that later reduces dependence on broad inference access

The rich provider layer exists because early self-improvement should not be artificially constrained to the weakest available intelligence if stronger intelligence can help build the machinery that later reduces dependency on it.

Astrata should also eventually ship with a bounded bootstrap inference lane.

This lane should help with:

- first-run onboarding
- connecting the user’s real inference providers
- early startup diagnosis and repair
- getting enough intelligence online that Astrata can participate in configuring itself

It should be:

- clearly labeled as bootstrap-first rather than Prime
- low-authority and tightly scoped
- easy to disable or replace
- visible in diagnostics, onboarding, and operator surfaces

Its purpose is not to become a hidden permanent dependency.
Its purpose is to help Astrata bring the real inference stack online faster.

In short:

- expensive capability is acceptable if it buys enduring improvement
- provider diversity is a bootstrap asset
- the long-term goal is not “always use the strongest model”
- the long-term goal is “use whatever helps the system earn independence from needing it”

---

## Build Doctrine

Astrata should be built under the following doctrines.

### 1. Self-Improvement First

The most important thing the system can be doing is improving.

Every major implementation choice should be evaluated against the question:

- does this help the system observe itself, diagnose itself, modify itself, validate itself, and keep the gains?

### 2. Agency Is Instrumental During Bootstrap

Agency is an end goal, but during bootstrap it is subordinate to evolution.

Astrata needs enough agency to:

- touch real surfaces
- perform real work
- generate real failures
- produce high-value training and evaluation signal

That is why external action matters.

### 3. Real Work Beats Artificial Isolation

Real-world tasks generate better improvement signal than synthetic benchmarks alone.

Evals matter, but real operations expose:

- real constraints
- real stakes
- real ambiguity
- real failure modes
- real recoverability requirements

### 4. Good Enough To Start

Initial implementation does not need to be perfect.

It needs to be:

- rich enough to preserve signal
- controllable enough to avoid unacceptable damage
- inspectable enough to improve

If a record shape, controller policy, or route taxonomy later proves too weak, Astrata should be able to make it richer.

### 5. Reuse Machinery, Rewrite Glue

Anything already proven in Astra or Strata that directly serves the self-improvement loop should be reused when practical.

Anything that mostly reflects historical wiring, accidental API shape, or legacy shell decisions may be rewritten freely.

---

## Minimum Viable Self-Improvement Loop

Astrata crosses the first threshold when it can do all of the following autonomously:

1. perform real work
2. record what happened durably
3. detect that something worked poorly, failed, or underperformed
4. propose a plausible improvement
5. trial that improvement in a bounded way
6. verify or audit the result
7. promote the improvement if it helps
8. retain the improved structure as a durable artifact

If any of those links is missing, recursive self-improvement is not yet truly online.

### Minimum Viable Bootstrap Loop

Before that, Astrata should cross a weaker but still crucial threshold:

1. understand the current plan
2. inspect what already exists
3. identify the next missing implementation slice
4. implement that slice
5. verify that the system is now more complete than before
6. repeat

If Astrata can do that reliably with bounded supervision, Loop 0 is alive and the system is on glide slope toward Loop 1.

---

## First Build Slices

The implementation path should prioritize the smallest slices that establish the minimum viable self-improvement loop.

### Slice 1: Durable Work and Trace Substrate

Astrata must be able to represent and persist:

- tasks
- attempts
- artifacts
- communications
- handoffs
- verification and audit results

This is the memory of the improvement loop.
Without it, failures vanish instead of becoming training signal.

### Slice 2: Real Work Execution

Astrata must be able to perform real external work through:

- inference
- tools
- file/system actions
- networked checks where allowed
- bounded agentic task execution

This is necessary because real work generates the highest-value corrective signal.

This slice should also include basic execution resilience:

- route preflight
- transient-failure retry ceilings
- classified failure recording
- degraded-mode fallback
- route or tool health memory good enough to avoid immediately retrying known-bad paths

### Slice 3: Verification and Audit

Astrata must be able to:

- check outputs
- compare variants
- review failures
- distinguish bad result from bad process
- verify the verifier when needed

This is what makes experimentation safe enough to compound.

### Slice 4: Variant Trial and Promotion

Astrata must be able to:

- propose changes
- run bounded trials
- compare outcomes
- promote or retire variants

Without this, the system can observe failure but not improve from it.

### Slice 5: Procedure Capture

Astrata must be able to turn successful patterns into reusable procedures and artifacts.

Without this, improvements do not accumulate.

### Slice 6: Federated Control and Rich Handoffs

Astrata must preserve local disagreement, refusal, and blockage as signal.

This matters because self-improvement depends on exposing internal friction rather than flattening it away.

### Slice 7: Local-First Compression

Once the system can improve itself using all available resources, it should focus on:

- reducing reliance on expensive inference
- improving routing
- strengthening procedure reuse
- improving local model support
- shifting validation and execution toward cheaper paths

This is the beginning of Loop 2.

---

## Loop 1: Improve Using All Available Resources

The purpose of Loop 1 is to make self-improvement undeniably real.

At this stage Astrata should be willing to use:

- premium models
- multiple providers
- cloud escalation
- broad tool access where appropriate
- richer verification passes
- higher-cost audit and review

if doing so helps produce:

- better procedures
- better routing
- better task decomposition
- better artifact schemas
- better verification methods
- better local-model support

Loop 1 is the phase where the system learns how to learn.

### Success Criteria For Loop 1

Loop 1 is working when Astrata can repeatedly:

- find its own weak spots
- generate and test improvements
- keep the good changes
- do so without constant human micromanagement

---

## Loop 2: Improve Using Few Resources

The purpose of Loop 2 is to make self-improvement efficient, local-first, and sustainable.

At this stage Astrata should increasingly optimize for:

- cheaper execution
- smaller-model viability
- amortized evals
- opportunistic signal harvesting
- stronger procedure reuse
- less expensive verification where possible
- high-value local experimentation

Loop 2 does not replace Loop 1.
It is built on top of Loop 1.

Strong resources remain available, but they are used more strategically.

### Success Criteria For Loop 2

Loop 2 is working when Astrata can:

- continue improving during normal operation
- do so mostly from low-cost or local resources
- reserve expensive inference for leverage points rather than routine dependence

---

## What Counts As Progress

Astrata is making real progress when:

- failures are turning into durable corrective artifacts
- procedures are getting better
- routing is getting smarter
- local models are handling more useful work
- expensive models are being used more selectively
- the system is noticing weaknesses in its own record shapes and control structures
- the system is enriching those structures rather than remaining stuck

Progress is not primarily:

- prettier architecture
- cleaner code in the abstract
- more subsystems
- broader feature count

Those things matter only if they help the loop.

---

## Salvage Priorities

Code reuse should follow the self-improvement path, not sentiment.

Likely high-value salvage:

- Astra’s provider/routing surfaces
- Astra’s permissions and approvals instincts
- Astra’s GenUI component library and spatial composition model
- Astra’s communication lane infrastructure (durable lanes, acknowledgment, pending queries)
- Astra’s proactivity and scheduler machinery
- Astra’s node identity model (Ed25519 keypair, actor identity)
- Strata’s task/attempt ontology
- Strata’s verification, audit, and eval ideas
- Strata’s procedures
- Strata’s context-pressure and observability machinery
- Strata’s experimental and promotion substrate
- Strata’s communication routing philosophy (communication decisions, session routing, message lifecycle, communicative acts)

The successor communication system should combine Strata’s routing philosophy (should we speak? where? what kind of act?) with Astra’s lane infrastructure (durable SQL-backed lanes, typed participants, acknowledgment tracking).

Likely low-value salvage:

- legacy app assembly
- legacy API route shapes
- historical UI coupling
- old schema assumptions that do not fit the successor ontology

This evaluative posture should apply across all mutation surfaces, not only model choice.
Astrata should eventually compare providers, provider/model pairings, routes, prompts, procedures, backends, runtime profiles, and policy bundles wherever bounded testing is possible.

---

## Implementation Standard

Every implementation step should be evaluated by asking:

- does this help close Loop 1?
- does it help Loop 1 produce assets that close Loop 2?

If the answer to both is no, it is probably not on the critical path.

---

## Build Motto

> First, teach the system to improve itself with everything.
> Then, teach it to need almost nothing.
