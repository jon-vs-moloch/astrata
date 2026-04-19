# Astrata — Runtime Architecture

## Purpose

This document describes the runtime architecture of Astrata.

It is not primarily an implementation inventory.
It is the architectural shape required to support Astrata’s central purpose:

> recursive self-improvement through real work, durable signal, bounded experimentation, and retained gains

The architecture should therefore be judged by one main criterion:

- does it help the system observe itself, regulate itself, improve itself, and keep the gains?

---

## Architectural Thesis

Astrata should be built as one coherent runtime composed of bounded subsystems that exchange durable records and typed artifacts.

It should not be:

- a chat app with a hidden harness bolted on
- an eval harness with a product shell bolted on
- a single monolithic controller that flattens away local signal

It should be:

- one constitutional system
- one durable work substrate
- one federated control model
- one artifact economy
- one routing and execution fabric
- one improvement loop

It should also be modular in adoption.

Users should be able to:

- run Astrata as the full integrated system
- use only the local-runtime substrate
- use only selected higher-order modules such as memory or constellation/comms
- swap major modules for external alternatives where the interfaces allow it

So the architecture must aim for:

- coherent interoperability when modules are combined
- graceful partial adoption when only one subsystem is wanted
- replaceable boundaries where external systems can be plugged in

---

## Top-Level Runtime Shape

At the highest level, Astrata consists of:

1. constitutional governance
2. durable records and storage
3. federated control
4. routing and execution
5. verification and audit
6. artifact management
7. procedure management
8. memory and knowledge
9. improvement and promotion
10. operator and user surfaces
11. context management
12. communication routing
13. proactivity and scheduling

These are not thirteen separate products.
They are thirteen architectural responsibilities inside one runtime.

---

## 1. Constitutional Governance Layer

This layer answers:

- what authority applies
- what standing instructions govern behavior
- what ambiguity remains
- when the user must be consulted

### Owns

- constitution loading and interpretation
- project spec loading and interpretation
- authority chain resolution
- ambiguity detection
- conservative interpretation policy
- user-clarification escalation

### Reads

- constitution artifacts
- project spec artifacts
- task and handoff provenance

### Writes

- policy artifacts
- clarification tasks
- communication records
- audit-relevant interpretation traces

### Why it exists

Self-improvement without a governing layer becomes unguided mutation.

---

## 2. Durable Record Layer

This layer is the system’s persistent memory of what happened.

### Core records

- task records
- attempt records
- artifact records
- controller records
- communication records
- handoff records
- verification records
- audit records

### Owns

- durable persistence
- identity linking
- record history and lineage
- append-friendly writes
- retrieval for runtime and analysis

### Why it exists

If failure, disagreement, promotion, and verification are not durable, self-improvement cannot compound.

---

## 3. Federated Control Layer

This layer governs local domains without flattening away local truth.

### Owns

- controller domains
- queue ownership
- handoff acceptance/refusal
- local scheduling
- renegotiation
- escalation
- local health signaling

### Required properties

- more than one controller
- explicit handoff semantics
- explicit refusal semantics
- explicit local degradation visibility

### Why it exists

Astrata’s self-regulation depends on preserving local friction as signal rather than bulldozing it with one central scheduler.

---

## 4. Routing and Execution Fabric

This layer chooses how work gets done and then causes it to happen.

### Owns

- provider selection
- route choice
- local vs cloud inference choice
- direct inference vs procedure-guided execution
- worker launches
- tool execution
- bounded external actions

### Subsystems

#### 4a. Provider Fabric

Astrata should preserve a very broad provider surface early on.

It should support:

- local providers
- cloud providers
- CLI-mediated providers
- specialized model surfaces
- weak cheap models
- strong expensive models

The architecture should assume:

- all available inference may be useful during bootstrap
- provider diversity is an asset
- routing should learn from observed outcomes

The provider fabric should also tolerate partial product adoption.
Users may want:

- only a conventional endpoint
- only a local runtime manager
- only Astrata's broad routing and quota logic
- the full self-improving stack

So Astrata should not assume that every runtime surface is only meaningful when attached to the rest of Astrata.
The local runtime should remain a valid standalone surface.

The provider fabric should also include a bounded bootstrap lane for first-run bringup.

This lane exists for:

- onboarding assistance
- startup self-diagnosis
- early bounded self-repair
- connecting richer provider surfaces

It should be narrower in authority than Prime and easy to replace once the user’s actual preferred inference stack is online.

#### 4a.1. Local Runtime Substrate

Astrata should absorb Lightning as its local runtime substrate.

This substrate is responsible for:

- managed local inference backends
- runtime process control
- local model discovery and activation
- hardware/runtime recommendation
- local health and thermal-aware runtime policy
- optional exposure as a conventional or persistent endpoint

This should live inside Astrata's architecture, not beside it.
But it should remain usable independently by a user who only wants a local endpoint.

The right constitutional rule is:

- full Astrata uses the local runtime as one substrate among others
- partial adopters may use the local runtime without adopting memory, Constellation, or the self-improvement layers
- other Astrata modules should depend on stable runtime contracts, not on hidden Lightning-internal assumptions

#### 4a.1. Local Runtime Substrate

Astrata should absorb Lightning as the beginning of its local-runtime substrate.

This layer should own:

- local model discovery and adoption
- starter catalog and install flows
- vetted starter-model onboarding procedures
- runtime recommendation and profile selection
- managed `llama.cpp` process control
- runtime health and operation tracking

It should remain usable in at least three modes:

- as Astrata's own local inference substrate
- as a standalone local endpoint for users who only want hardware-efficient inference
- as a conventional endpoint that other systems can target without adopting the rest of Astrata

This matters because "go down to the metal" is not an aesthetic preference.
It is one of the main routes by which Astrata can extend the usefulness of local hardware and eventually convert more real work into local capability.

During onboarding, this layer should also be able to bring one small vetted local model online early when doing so materially improves self-setup and diagnosis.

That path should be:

- optional
- explicit
- tied to known-good procedures
- evaluated and replaceable like every other Astrata mechanism

#### 4b.1. Execution Resilience

Astrata should assume that routes, tools, and providers will degrade in uneven ways.

The execution fabric should therefore include:

- preflight checks before committing important work to a route
- bounded retry for transient failures
- failure-kind classification
- scoped health memory for routes and tools
- temporary throttling of recently bad paths
- explicit degraded-mode fallback rather than silent stall

This is not just operational polish.
It is part of how the system keeps improving while under imperfect conditions.

#### 4b. Execution Router

The router should choose among:

- direct inference
- procedure-guided execution
- deterministic execution
- escalation to stronger inference
- alternate route trial

It should reason from continuous variables such as:

- priority
- urgency
- risk
- provenance and proposer trust
- local load
- recent route performance
- expected information gain
- route health and recent degraded behavior

#### 4c. External Action Surface

Astrata must be able to touch reality.

This includes:

- file operations
- shell and system actions
- network checks
- code/test actions
- environment interactions allowed by policy

This surface exists because real action generates the richest improvement signal.

### Why this layer exists

Without execution, Astrata cannot do real work.
Without broad routing, Astrata cannot exploit all available leverage during bootstrap.

---

## 5. Verification and Audit Layer

This layer determines whether the system should trust what just happened.

### Verification owns

- lightweight post-attempt checks
- output validation
- route comparison support
- artifact-level correctness checks
- verification-of-verification when the system needs to know whether its own checks can be trusted

### Audit owns

- interpretive review of failures
- controller disagreement review
- handoff failure diagnosis
- route/process diagnosis
- root-cause hypotheses

### Why this layer exists

Experimentation without verification is noise.
Verification without audit misses process failures.

---

## 6. Artifact Layer

This layer governs the durable things Astrata produces and reuses.

### Owns

- artifact registration
- artifact identity and lineage
- artifact health state
- install state
- consumption policy
- promotion and retirement

### Artifact families

- procedures
- knowledge artifacts
- eval results
- tool artifacts
- code artifacts
- policy artifacts
- observability artifacts
- validated answers

### Why this layer exists

Artifacts are how improvements survive past the moment they were discovered.

---

## 7. Procedure Layer

This layer stores and executes reusable structure.

### Owns

- procedure records
- procedure structure graphs
- applicability logic
- procedure lifecycle
- procedure binding into tasks
- procedure refinement

### Relationship to tasks

- procedures define reusable execution structure
- tasks are live instances within that structure
- attempts record what actually happened during execution

### Why this layer exists

Procedures are one of the main mechanisms by which Loop 1 creates assets that help close Loop 2.

---

## 8. Memory and Knowledge Layer

This layer preserves both retrieval state and synthesized understanding.

### Memory owns

- operational retrieval state
- recent task and conversation context
- graph and embedding structures
- fast lookup surfaces

### Knowledge owns

- synthesized durable understanding
- compacted pages
- provenance-aware summaries
- reusable explanatory artifacts

### Why this layer exists

Memory helps the runtime find.
Knowledge helps the runtime understand.

Both are necessary for cheapening the loop.

---

## 9. Improvement Layer

This layer is the heart of Astrata.

### Owns

- variant proposal
- bounded trials
- promotion and retirement
- retained gains
- procedure capture
- route refinement
- evaluation slicing
- opportunistic signal harvesting
- general eval substrate across mutation surfaces

### Core responsibilities

- notice failure or underperformance
- create candidate improvements
- test nearby alternatives
- compare outcomes
- keep what helps
- compare local and cloud inference paths on the same kinds of work when safe and useful
- treat models, providers, routes, prompts, procedures, backends, profiles, and policy bundles as first-class eval subjects

### Why this layer exists

If this layer is weak, Astrata may be capable, but it is not alive in the intended sense.

---

## 10. User and Operator Surfaces

This layer exposes Astrata to humans.

### User surface owns

- conversation
- approvals
- task visibility
- lightweight memory/knowledge visibility
- escalation and clarification interactions

### Operator surface owns

- traces
- controller state
- queue state
- verification and audit visibility
- artifact and procedure inspection
- variant and promotion visibility

### Why this layer exists

Astrata is not meant to be opaque.
It should expose enough of itself to remain governable and improvable.

---

## 11. Context Management Layer

This layer governs how much and what kind of context reaches the model on each inference call.

### Owns

- token budget tracking per model and per task
- context pressure measurement and signaling
- context shaping decisions (include, exclude, summarize, defer)
- oversized artifact detection and compaction triggers
- retrieval budget coordination with memory and knowledge layers

### Required properties

- context pressure is a continuous system variable, not a binary error
- routing, scheduling, and improvement layers can all read context pressure
- context shaping is explicit and inspectable, not hidden inside prompt assembly
- different models and execution surfaces may have different context budgets

### Why this layer exists

Local-first competence depends on making good use of limited context windows.
Without explicit context management, the system will silently degrade on small models before anyone can diagnose why.

---

## 12. Communication Routing Layer

This layer decides whether, where, and how the system speaks.

### Owns

- communication decision logic (should the system speak?)
- session and lane routing (where should the message go?)
- communicative act classification (what kind of message is this?)
- durable lane management (agent-to-agent, tool, system, user lanes)
- message lifecycle tracking (sent, delivered, seen, read, acknowledged)
- provenance and authority attribution on all system-authored messages

### Required properties

- all non-user-authored messages go through the communication routing layer
- user-authored messages are stored directly as user actions
- communication history is append-only in meaning
- silence is a valid communication decision and should be recorded

### Why this layer exists

Without communication routing, subsystems write directly to chat history whenever they want to say something.
That destroys routing discipline, provenance, and the system's ability to make intelligent decisions about attention management.

### Inbound Message Intake

Communication routing is only half of the job.
Astrata also needs a durable intake path that turns inbound messages into governed work.

The minimum useful shape is:

1. inbound message enters a durable lane
2. intake normalizes it into a request spec
3. request spec is interpreted against constitution and project specs
4. one or more task proposals are produced
5. accepted proposals become durable tasks with provenance back to the message

This matters because a message is not yet work.
A message is evidence of intent, request, ambiguity, or pressure.
The intake layer is where Astrata decides what kind of work the message actually implies.

The intake layer should preserve:

- source communication id
- sender and recipient
- intent and message kind
- relevant constitutional or project-spec context
- whether clarification is needed
- whether the result is one task, many tasks, or no task

---

## 13. Proactivity and Scheduling Layer

This layer makes Astrata an autonomous actor, not a passive tool.

### Owns

- autonomous task identification and scheduling
- surface monitoring and anticipation
- briefing generation
- idle-period work planning
- scheduling policy (when and how aggressively to act)

### Required properties

- proactive work obeys the same governance and approval rules as reactive work
- autonomous actions are observable, attributable, and reviewable
- the scheduling function reasons from real system variables (priority, urgency, risk, capacity, user preferences)
- briefings are produced as artifacts with normal provenance

### Why this layer exists

The constitution and project specs define a desired state of reality.
If the system only acts when spoken to, it cannot autonomously work toward that state.
Proactivity is what turns Astrata from a responsive tool into a genuine coordination partner.

---

## Runtime Dataflow

The hot path for a typical task should look roughly like:

1. task enters system with provenance, permissions, risk, priority, urgency, and completion policy
2. governing layer resolves relevant constitutional and project-level authority
3. a controller accepts or negotiates the task locally
4. context management shapes the context window for the chosen model and task
5. routing chooses an execution path
6. execution produces an attempt record and any artifacts
7. verification checks the result
8. audit is triggered when needed
9. communication routing decides what to say, where, and whether silence is correct
10. completion policy determines what happens next
11. improvement layer decides whether anything learned here should create a variant, promotion, procedure draft, or route update
12. resulting gains are retained as artifacts or policy changes
13. proactivity layer evaluates whether follow-up work, monitoring, or anticipatory tasks should be queued

This is the essential loop.

---

## Architectural Priority Order

If tradeoffs are necessary, the architecture should prioritize:

1. durable signal
2. real execution
3. verification and audit
4. retained gains
5. procedure reuse
6. local-first cheapening
7. polish and convenience

This order reflects the build doctrine, not aesthetics.

---

## The Local Inference Stack

Astrata should begin with broad access to all available inference surfaces.

But in the long run, it should move progressively downward in the local inference stack wherever doing so yields meaningful gains.

That means:

- not just choosing between providers
- not just preferring “local” as a routing label
- but improving the actual local inference substrate itself where leverage exists

Examples of going down-stack include:

- tighter control over model serving
- better batching and queueing
- better cache reuse
- stronger prefix/KV reuse
- more direct runtime management
- better scheduling around hardware constraints
- better control over transport and invocation overhead
- tighter integration with local execution hardware

For internal Astrata use, local inference should eventually be callable as an in-process engine capability rather than requiring an HTTP round-trip to a localhost service.

The HTTP surface should remain available as an optional serving wrapper for:

- external software that wants an OpenAI-compatible endpoint
- operator-visible local runtime serving
- debugging and observability workflows

But Astrata's own lane execution, strategy orchestration, and cache-aware inference paths should not be forced to depend on a bound port when no external client is involved.

Astrata should go as close to the metal as is practical **when** doing so produces real improvement in:

- throughput
- latency
- reliability
- experimental bandwidth
- cost
- local-model viability

It should not go lower merely for purity or aesthetic reasons.

The standard is always:

- do we get meaningful gains?

If yes, go lower.
If no, stay higher-level and spend effort elsewhere.

---

## Architectural Non-Goals

This architecture should not optimize for:

- conceptual tidiness at the expense of self-improvement leverage
- a single-controller illusion of smoothness
- premature minimization of provider diversity
- perfect first-pass schemas
- artificial state machines where continuous variables suffice

Those may look simpler, but they would weaken the actual product goal.

---

## Maturity Criterion

The architecture is maturing correctly when:

- more work is represented through durable records and artifacts
- local disagreement becomes easier to diagnose
- strong models are increasingly used to create durable cheapening gains
- procedures become better and more reusable
- routing becomes more evidence-based
- local inference becomes more capable and more deeply integrated
- the system begins enriching its own internal structures in response to observed failure

That is the point of the architecture.
