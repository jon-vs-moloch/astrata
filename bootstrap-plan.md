# Astrata — Bootstrap Plan

## Purpose

This document defines the shortest credible path to waking Astrata up.

The goal is not to build the full mature architecture immediately.
The goal is to cross the threshold where Astrata can:

- do real work
- notice its own failures
- propose changes
- test those changes
- keep what works

Once that loop is alive, the system can begin rebuilding its own path.

More specifically, bootstrap should aim to awaken Loop 0:

> Astrata can autonomously complete enough of its own build path to carry itself toward Loop 1.

---

## Bootstrap Standard

The first implementation is sufficient if it can support recursive self-improvement, even if:

- some records are incomplete
- some policies are crude
- some controllers are manually simple
- some schemas later need to become richer

The system does not need a perfect first body.
It needs a body capable of learning how to strengthen itself.

---

## First Threshold

Astrata is "awake enough" when it can autonomously do the following cycle on at least some real tasks:

1. execute work
2. record what happened
3. detect underperformance or failure
4. generate a candidate improvement
5. trial that improvement in bounded fashion
6. verify or audit the result
7. retain the improvement as a durable artifact

Everything in this plan is aimed at enabling that cycle.

But there is an earlier and more practical ignition threshold:

### Loop 0 Threshold

Astrata is on glide slope when it can:

1. read the governing and implementation documents
2. inspect its current code and runtime state
3. identify the next highest-leverage unfinished bootstrap item
4. implement that item using available providers and tools
5. verify that the system is now more complete or stronger than before
6. continue to the next item with bounded supervision

Loop 0 is not the final goal.
It is the point where Astrata becomes able to help finish building Astrata.

---

## Bootstrap Strategy

The path should be:

1. build the minimum durable substrate
2. connect it to real execution
3. add bounded comparison and promotion
4. capture successful structure as reusable procedures
5. shift effort toward cheaper and more local self-improvement

This should be done with aggressive reuse of proven machinery where possible.

The practical aim is:

- hand-build enough substrate to awaken Loop 0
- let Loop 0 help complete the rest of bootstrap
- then drive into Loop 1

---

## Phase 0: Constitutional Core

Before anything else, Astrata needs a minimal governing core.

### Required outputs

- constitution loaded as a durable governing artifact
- project spec support
- user authority chain represented
- minimal ambiguity handling policy

### Why this is first

If the system is going to improve itself, it needs to know what it is trying to preserve while changing.

Without a constitutional layer, self-improvement degenerates into unguided mutation.

### Minimum acceptable implementation

- load constitution from file
- load one local project spec if present
- expose both to runtime decisions
- allow “ask user” as an explicit ambiguity resolution action

---

## Phase 1: Durable Work Substrate

Astrata needs a minimal persistent substrate for work and evidence.

### Required records

- task record
- attempt record
- artifact record
- communication record
- handoff record
- verification record
- audit record

### Required capabilities

- create tasks
- record attempts
- persist artifacts
- link attempts to artifacts
- record verification and audit outcomes

### Why this matters

Without durable records, failure evaporates.
If failure evaporates, no self-improvement loop can form.

### Minimum acceptable implementation

- simple durable storage
- append-friendly records
- linked identities between tasks, attempts, and artifacts
- enough detail for later diagnosis

The first version may be crude, but it must preserve signal.

---

## Phase 2: Real Execution Path

Astrata must be able to touch reality.

### Required capabilities

- route inference across all available providers
- execute simple external actions
- perform file and shell work under policy
- perform network checks where allowed
- create bounded workers for delegated task execution

### Why this matters

Real tasks produce higher-value signal than isolated evals.

If Astrata cannot do real work, it cannot generate the kind of corrective pressure needed for durable improvement.

This phase is also where Astra’s broad provider abstractions become strategically important.
Loop 0 becomes much more attainable if Astrata can use all available inference while building itself, rather than being restricted to the weakest available path during bootstrap.

### Minimum acceptable implementation

- one working task execution path
- provider routing over all available inference
- one or two tool surfaces that touch real state
- durable recording of outcomes
- route preflight and degraded-mode fallback
- failure-kind recording instead of flat failure only

The path may be ugly internally.
It just needs to work reliably enough to generate improvement signal.

Context management should also be included at this stage, at least in minimal form: tracking token budget consumption and shaping context for the model being used. Without context awareness, local-model execution will silently degrade before anyone can diagnose why.

### Bootstrap Inference During Onboarding

Astrata should ideally ship with a bounded bootstrap intelligence path so the system is not completely blind during onboarding and early repair.

This may be as simple as:

- a temporary or free-tier bootstrap API key
- a bootstrap provider lane with narrow authority
- explicit visibility that this lane is for bringup rather than permanent reliance

This lane should help Astrata:

- inspect startup state
- explain setup failures
- connect the user’s preferred cloud and local inference surfaces
- participate in its own first-run configuration

It should not silently become the long-term intelligence substrate.

### Starter Local Model Bringup

Astrata should also support an optional vetted path that brings one small starter local model online early.

This path should:

- download or adopt a small model family Astrata has a reliable procedure for
- only run when the machine and thermal policy make that reasonable
- focus on bounded useful procedures the starter model can reliably execute

The purpose is not to declare the starter model “good enough.”
The purpose is to get a modest local brain online early enough that Astrata can help diagnose, configure, and calibrate the rest of its stack.

---

## Phase 3: Verification and Audit Loop

Astrata must be able to distinguish:

- apparent success from real success
- bad outputs from bad process
- local failure from justified refusal

### Required capabilities

- lightweight verification after attempts
- richer audit after notable failure, disagreement, or degradation
- ability to attach findings to tasks, attempts, controllers, and artifacts

### Why this matters

Experimentation without verification is noise.
Experimentation without audit becomes blind thrashing.

### Minimum acceptable implementation

- cheap verifier path
- slower audit path
- durable findings
- ability to trigger follow-up tasks from findings
- at least one path that checks whether verifier conclusions match observed reality

---

## Phase 4: Variant Trial

Astrata must be able to try something slightly different on purpose.

### Required capabilities

- create a candidate variant
- bind that variant to a route, prompt, procedure, policy choice, provider, backend, model, or runtime profile
- run bounded trials on real or eval work
- compare outcomes

### Why this matters

Self-improvement begins when the system can move from:

- “that went badly”

to:

- “try this nearby alternative and see if it goes better”

### Minimum acceptable implementation

- one variant family is enough at first
- prompt variants or route variants are probably the easiest starting point
- trials may be low-volume and manually constrained initially

The important thing is not breadth.
It is having at least one real improvement lever.

Local model comparison is a good early instance.
It should not become the only shape of evaluation.
The same substrate should eventually compare cloud providers, model/provider pairings, routes, prompts, procedures, and other testable mutation surfaces.

---

## Phase 5: Promotion and Retention

Astrata must be able to keep gains.

### Required capabilities

- promote successful variants
- retire failed variants
- preserve lineage
- update default behavior
- record why the change was kept

### Why this matters

Without retention, the system can explore but not improve.

### Minimum acceptable implementation

- one promotion path
- one retirement path
- durable lineage between prior and promoted forms

This can initially be narrow and still be enough to wake the loop up.

---

## Phase 6: Procedure Capture

Astrata must begin turning repeated successful structure into reusable procedures.

### Required capabilities

- detect recurring successful task structure
- draft a procedure from it
- bind procedures back into execution
- improve procedures over time

### Why this matters

Procedures are one of the main ways improvements become cheaper and more local.

They are a bridge from Loop 1 to Loop 2.

### Minimum acceptable implementation

- manually narrow procedure domain at first
- use only a few high-value task classes
- allow drafted procedures to remain rough

The important thing is accumulation, not elegance.

---

## Phase 7: Federated Control

Astrata must preserve disagreement and refusal as signal.

### Required capabilities

- more than one controller
- explicit handoffs
- explicit refusal/defer/block semantics
- renegotiation or escalation path
- visibility into local controller state

### Why this matters

If the system flattens away local disagreement too early, it loses the very signal it needs to regulate itself.

### Minimum acceptable implementation

- two controllers are enough to start
- one upstream coordinator
- one downstream local controller
- explicit handoff record
- explicit refusal record

The first federated form may be simple, but it must preserve the distinction between:

- local failure
- justified noncompliance

---

## Phase 8: Cheapening The Loop

Once the full loop exists using all available resources, Astrata should turn aggressively toward making it cheaper.

### Required optimization directions

- stronger routing
- better procedure reuse
- more opportunistic signal harvesting
- amortized eval slicing
- cheaper verification where safe
- better local-model decomposition
- more selective use of expensive providers

### Why this matters

This is where Loop 2 begins in earnest.

The system stops merely improving and starts learning how to improve efficiently.

---

## Phase 9: Communication Routing

Once the self-improvement loop is alive, Astrata should establish communication as a routed surface rather than ad hoc storage writes.

### Required capabilities

- communication decision logic (should the system speak?)
- session or lane routing (where does the message go?)
- durable lanes for agent-to-agent and tool coordination
- message lifecycle tracking (sent, delivered, acknowledged)
- provenance on all system-authored messages

### Why this matters

As the system becomes more autonomous and more proactive, unstructured communication becomes a liability. Routing discipline, provenance, and the distinction between silence and failure all become important.

### Minimum acceptable implementation

- one communication decision path for system-authored messages
- one durable lane for internal coordination
- user-authored messages stored directly; system messages go through routing
- basic lifecycle tracking (sent, acknowledged)
- one intake path that turns an inbound operator message into a request spec and at least one governed task proposal

---

## Phase 10: Proactivity

Astrata should begin acting autonomously to bring reality toward the user's stated goals.

### Required capabilities

- autonomous task identification from constitution and project specs
- scheduling that reasons about priority, urgency, risk, and idle capacity
- surface monitoring for changes, risks, and opportunities
- briefing generation summarizing autonomous activity

### Why this matters

Proactivity is what transforms Astrata from a responsive tool into a coordination partner. The constitution and project specs define a desired state of reality; the system should autonomously work toward it.

### Minimum acceptable implementation

- one monitoring surface (e.g., project workspace)
- one autonomous task generator for identified work
- scheduling that queues background tasks during idle periods
- one briefing artifact summarizing what happened

---

## What To Build First In Practice

If forced into a ruthless first implementation order, the likely sequence is:

1. constitution + project spec loading
2. task / attempt / artifact persistence
3. one real execution path using all available providers, with minimal context management
4. one verification path
5. one audit path
6. one variant mechanism
7. one promotion path
8. one narrow procedure capture path
9. one second controller plus handoff/refusal semantics
10. one communication routing path for system-authored messages
11. one proactive monitoring and scheduling surface

Items 1–9 create the first real self-improvement loop.
Items 10–11 make it a product.

---

## What Can Stay Janky At First

These can be crude during bootstrap as long as signal is preserved:

- schema elegance
- UI polish
- API purity
- controller taxonomy
- route taxonomy
- procedure formatting
- artifact presentation

These should not be crude:

- provenance
- task/attempt linking
- verification and audit persistence
- variant lineage
- promotion decisions
- refusal visibility

If the system cannot see why something happened, it cannot improve from it.

---

## Bootstrapping With Strong Models

Astrata should unapologetically use strong models early if they help build the self-improvement loop.

That includes using strong providers to:

- design better procedures
- review failures
- propose variants
- interpret audits
- strengthen local-model support

The bootstrap goal is not purity.
It is leverage.

Expensive inference is justified if it builds durable improvements that reduce future dependency on it.

---

## Failure Conditions

Bootstrap should be considered off-course if Astrata becomes:

- a competent assistant that does not improve
- a rich observability system with no promotion path
- a powerful execution runtime with weak verification
- a variant generator that cannot retain gains
- a local-first shell that avoids using stronger inference when it would accelerate self-improvement

Those are all partial successes and overall failures.

---

## Success Condition

Bootstrap succeeds when Astrata can, on its own:

- do real work
- learn from that work
- alter its own behavior
- verify whether the alteration helped
- preserve the gain
- increasingly focus those gains on making future improvement cheaper

That is the moment the system stops being merely constructed and starts participating in its own construction.

In practice, bootstrap is already succeeding earlier if Loop 0 is alive enough that Astrata can autonomously complete the remaining high-leverage bootstrap slices with bounded supervision.

---

## Build Motto

> Wake the loop up.
> Then let it learn how to make itself cheaper.
