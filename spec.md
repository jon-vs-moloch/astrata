# Astrata — Successor System Spec v0.1

## Overview

**Astrata** is the clean successor to Astra and Strata.

It is not a merge in the ordinary sense.
It is a new system built from the lessons, machinery, and design pressures that both projects uncovered.

The core thesis is:

> A personal agent should be a coherent system, not a product shell bolted onto a harness or a harness bolted onto a chat app.

Astrata therefore unifies:

- the **product and agency layer** Astra was trying to build
- the **competence substrate** Strata was trying to build

into one system with one ontology, one runtime model, one artifact model, and one product identity.

The end state is a local-first personal coordination system whose intelligence becomes more reliable over time through explicit system structure, validation, memory, procedures, and evaluation.

---

## Core Thesis

Current AI products usually fail in one of two ways:

1. they are good at conversation but weak at durable, trustworthy action
2. they build sophisticated harnesses but never become coherent products

Astrata exists to unify those halves.

Its job is:

> turn raw inference into reliable agency, then turn reliable agency into useful life and work coordination

This means Astrata must be:

- a product a person can actually live inside
- a runtime that can turn weak or cheap inference into usable work
- a system that accumulates capability through artifacts rather than depending entirely on model quality

Above all, Astrata must become capable of self-regulation and self-improvement.

If it achieves useful interaction, task execution, and local-first competence but fails to improve itself through experience, evaluation, and visible self-correction, then it has failed at its central purpose.

This also constrains how the system should be built:

- the initial implementation only needs to be good enough for self-improvement to begin
- imperfect records, policies, and workflows may be tolerated if they are visible enough to be improved
- the system should eventually be able to notice when its own structures are too weak and make them richer

Astrata does not need a perfect road to the summit.
It needs a path good enough to awaken the engineers that can rebuild it.

Improvement is the single most important thing the system can be doing.

Agency matters, but in the current phase agency is subordinate to evolution.
Astrata needs enough real agency to touch reality, generate high-value signal, and improve itself.
As the improvement loop strengthens, fuller agency can be re-unlocked on top of that stronger substrate.

External action is therefore justified not only because agency is the end goal, but because real-world tasks generate uniquely valuable data for improvement.
Synthetic evals matter.
Simulated benchmarks matter.
But real operations produce the richest corrective signal because they expose the system to actual constraints, actual consequences, and actual failure modes.

---

## Design Principles

### 1. One System

Astrata should not feel like “the Astra part” and “the Strata part.”

There is:

- one runtime model
- one task ontology
- one storage philosophy
- one routing system
- one operator surface

Any seam that still reads as a legacy weld line is design debt.

### 2. Local-First, Not Local-Only

Local inference is the strategic center.
Cloud inference is an escalation path, not a philosophical violation.

The system should progressively earn the right to rely more on local compute through evaluation and accumulated competence.

Astrata should also be willing to ship with a bounded bootstrap intelligence path for onboarding and early repair.

That bootstrap path exists so Astrata can help:

- diagnose startup problems
- explain what is missing
- connect the user’s real inference sources
- optionally bring one small vetted local model online early

It should be explicit, replaceable, and lower-authority than the long-term runtime.
It is a bringup aid, not a hidden permanent dependency.

### 3. Explicit Competence

Capability should live in:

- procedures
- tools
- knowledge artifacts
- evaluation evidence
- routing policy
- validation machinery

not only inside prompts or model weights.

### 4. Preserve Useful Resolution

Astrata should not compress reality into coarse abstractions unless doing so provides a concrete advantage.

Compression is good when it improves:

- token efficiency
- storage efficiency
- retrieval efficiency
- UI legibility
- decision speed

without materially harming downstream performance.

Compression is bad when it destroys distinctions the runtime can still use.

In particular:

- control logic should prefer direct use of real operational variables
- summaries and modes should usually be projections of state, not the primary state itself
- state machines should exist only when they create a real policy, safety, coordination, or implementation advantage

Astrata does not build artificial state machines for fun.
Its abstractions should earn their existence.

### 5. Product and Harness Are Not Separate

The user-facing product and the competence substrate are part of the same machine.

Operator visibility, approvals, evals, trace review, and artifact inspection are not side consoles.
They are part of how the system remains trustworthy.

### 6. Durable Artifacts Over Ephemeral Cleverness

Useful work should leave behind reusable structure.

Every successful branch should ideally improve one or more of:

- a `Procedure`
- a knowledge artifact
- a routing heuristic
- an eval artifact
- a tool or tool-binding

### 7. Safe Competitive Pressure

Astrata should not improve only through offline evals or occasional explicit tuning passes.

It should improve through **constant bounded competition in real work**.

That means:

- trying safe variants during actual task execution
- comparing outcomes under real constraints
- preserving successful differences
- retiring weaker approaches

The system should create internal competitive pressure between:

- procedures
- prompts
- routing decisions
- decomposition styles
- model selections
- tool choices
- validation strategies

The goal is not chaos.
The goal is a controlled environment where the system is always learning from live work without putting the user or machine at unreasonable risk.

### 8. Routing Is a First-Class Intelligence Function

The system should not ask “which model is best?” in the abstract.
It should ask:

- what kind of work is this
- what quality bar applies
- what artifact or procedure already exists
- what is the cheapest path likely to succeed
- what evidence supports that belief

### 9. Truth Over Fluency

The system should prefer verified, inspectable, and partially correct work over polished but ungrounded output.

### 10. Recovery Enables Experimentation

Verification, approvals, observability, provenance, and trace review are not only for safety in the narrow sense.

They also exist to make experimentation **safe, bounded, and recoverable**.

Astrata should be designed so it can try things constantly because:

- changes are visible
- risky actions are gated
- branch behavior is recorded
- failures can be diagnosed
- bad variants can be rolled back or retired

The system should become more experimental as its recovery machinery improves, not less.

### 12. Execution Must Degrade Gracefully

Astrata should not treat execution failure as a single undifferentiated event.

It should preserve and act on distinctions such as:

- transient outage
- route unavailable
- model unavailable
- timeout
- invalid response
- tool degradation
- policy refusal

The system should prefer:

- preflight before commitment when cheap
- bounded retry for likely-transient failures
- throttling of recently bad routes or tools
- fallback to weaker-but-credible paths over immediate stall
- durable route-health memory that later attempts can learn from

Resilience is not merely uptime.
It is part of self-regulation because the system cannot improve effectively if every local outage erases momentum or every bad route is rediscovered from scratch.

### 11. Failure Must Stay Visible When It Contains Signal

Astrata should not design away informative failure merely to preserve the appearance of smoothness.

When a subsystem refuses work, drops work, blocks work, degrades, or disagrees with another subsystem, that may be an important fact about reality rather than noise to suppress.

The system should therefore preserve failures that contain useful signal long enough to:

- observe them
- attribute them
- reason about them
- renegotiate around them
- improve from them

Silent override is often epistemic loss.

Where possible, Astrata should prefer:

- visible refusal over hidden coercion
- explicit blockage over fake success
- renegotiation over blind reprioritization
- diagnosable degradation over flattened behavior

This is not a license for avoidable chaos.
It is a requirement that self-regulating systems remain richly exposed to themselves.

---

## System Goal

Astrata is a **persistent personal coordination and execution system**.

It should eventually be able to:

- converse naturally with the user
- maintain durable memory and context
- decompose and execute meaningful work
- decide when to act directly, when to ask, and when to wait
- improve its own procedures and tooling
- evaluate whether those improvements are real
- shift more work onto local compute as competence improves

The endgame is:

> local model + explicit competence substrate + durable artifacts = trustworthy primary agent

---

## Scope

Astrata includes:

- chat and interactive coordination
- background task execution
- durable agent hierarchy
- inference routing
- procedures and workflow memory
- knowledge synthesis and retrieval
- eval and self-improvement substrate
- approvals, policies, and operator control
- observability, trace review, and interpretability surfaces

Astrata does not include:

- training foundation models from scratch
- becoming a raw inference engine itself
- replacing ordinary applications when an integration or tool is better

---

## Product Identity

Astrata is one product.

Working mental model:

- **Astra contributed** the product shell, user-facing agency ambition, permissions instinct, and persistent coordination model
- **Strata contributed** the competence substrate, eval discipline, procedure model, and small-model compensation machinery

In Astrata, those are no longer separate origins.
They are simply parts of the system.

---

## Primary User Model

Astrata is for a user who wants a persistent, intelligent system on their own machine that can:

- help think
- help decide
- help remember
- help execute
- help improve itself carefully

The user should not need to understand the internal harness in order to benefit from it.
But the system should remain inspectable enough that power users can see how and why it works.

---

## Core Ontology

Astrata uses one unified ontology.

### 1. Actor

An `Actor` is an agentive participant in the system.

Examples:

- Prime
- Assistant
- Worker
- external tool surface
- user

Actors have:

- identity
- role
- permissions
- communication relationships

### 2. Task

A `Task` is a persistent unit of work.

Tasks are:

- durable
- recursive
- decomposable
- ownable
- observable

A task represents an objective, not one model call.

Tasks should carry enough durable metadata for the system to decide:

- whether the task should exist
- when it should run
- who may run it
- how cautiously it should run
- what counts as success
- what should happen after it completes

#### Task Record

Astrata should treat the task record as a first-class durable object rather than a thin queue wrapper.

Core task fields should include:

- `priority`
  How important is this relative to other work?
- `urgency`
  How time-sensitive is this work?
- `permissions`
  What capabilities, tools, surfaces, or authorities may be used?
- `provenance`
  Why does this task exist, and what created it?
- `proposer_trust`
  How much should the system trust the source of the task or proposed mutation?
- `risk`
  How costly is failure, partial failure, or incorrect success?
- `cost_estimate`
  Expected spend in compute, latency, tokens, money, or operator attention.
- `success_criteria`
  What counts as done?
- `validation_requirement`
  How much checking is required before the result can be trusted?
- `interruptibility`
  Can the work be paused, preempted, resumed, or sliced?
- `reversibility`
  How recoverable is the task if it goes wrong?
- `procedure_policy`
  Is there a preferred, required, or forbidden procedure binding?
- `completion_policy`
  What should happen when the task succeeds, fails, blocks, or produces artifacts?

Optional or commonly useful fields may include:

- `deadline`
- `artifact_expectations`
- `expected_value`
- `expected_information_gain`
- `execution_envelope`
- `parent_task_id`
- `requesting_actor`

#### Priority vs Urgency

Astrata should treat `priority` and `urgency` as distinct concepts.

- `priority` is importance
- `urgency` is time pressure

Something may be:

- high-priority and low-urgency
- low-priority and high-urgency
- high in both
- low in both

Collapsing them into one number destroys useful scheduling information.

#### Completion Policy

`completion_policy` should not be implicitly fused with the last step of a procedure.

A procedure describes how to perform the work.
A completion policy describes what the system should do with the outcome.

Reasonable completion behaviors include:

- return result to requester
- install or register artifacts
- enqueue follow-up work
- escalate to parent or supervisor
- request approval
- retry or reroute according to policy
- archive result without further action

There should be a sane default completion policy, but it should remain an explicit part of the task model.

### 3. Attempt

An `Attempt` is one variance-bearing execution instance against a task.

Attempts are where stochastic work happens.
Tasks persist; attempts end.

Attempts should preserve enough information for the system to understand:

- who performed the work
- why this attempt happened
- what happened
- what it cost
- how trustworthy the result is
- what changed because of it

#### Attempt Record

Astrata should treat the attempt record as a first-class durable execution object.

Core attempt fields should include:

- `attempt_id`
  Stable identity for the attempt.
- `task_id`
  Which task this attempt belongs to.
- `actor`
  Which agent, worker, model path, tool surface, or composite route performed the attempt.
- `authority`
  Under what authority or permission context the attempt was executed.
- `provenance`
  Why this attempt was launched at all.
- `attempt_reason`
  Why this specific attempt was selected instead of another plausible route.
- `started_at`
  When the attempt began.
- `ended_at`
  When the attempt ended.
- `duration`
  How long the attempt took wall-clock.
- `result`
  What the attempt produced.
- `outcome`
  Whether the attempt succeeded, failed, blocked, was cancelled, or was superseded.
- `verification_status`
  What verification has or has not happened yet.
- `audit_status`
  Whether the attempt has been reviewed, flagged, or audited.
- `resource_usage`
  What the attempt consumed in compute, latency, tokens, money, I/O, or operator attention.
- `artifacts_produced`
  Which durable artifacts came out of the attempt.
- `followup_actions`
  What happened because of the result.

Useful additional fields may include:

- `route_signature`
- `model_or_variant`
- `procedure_version`
- `tool_sequence`
- `validation_evidence`
- `risk_realized`
- `recovery_actions`
- `notes`

#### Attempt Provenance

Attempts should record both who did them and why they happened.

This is not redundant.

- `actor` answers: who or what performed the work
- `provenance` answers: what chain of causation or authority led to this attempt existing
- `attempt_reason` answers: why this specific execution path was chosen

Those distinctions matter for audit, trust, and learning.

#### Verification and Audit

Astrata should distinguish between:

- what the attempt claimed
- what was verified
- what was later reviewed or audited

An attempt can therefore be:

- complete but unverified
- verified but unaudited
- verified and later challenged
- successful in output terms but failed in audit terms

The record should preserve those distinctions explicitly.

#### Resource and Consequence Tracking

Attempts should not only record what they returned.
They should also record what they consumed and what they caused.

That includes:

- time spent
- resources consumed
- approvals triggered
- tasks enqueued
- artifacts installed or proposed
- escalations made
- rollbacks or mitigations performed

This makes attempts useful for:

- routing improvement
- cost control
- failure diagnosis
- procedure evolution
- trust calibration

### 4. Procedure

A `Procedure` is a durable workflow artifact.

It captures how a class of work should be structured, verified, and completed.

Procedures can be:

- draft
- tested
- vetted
- retired

### 5. Artifact

An `Artifact` is any durable work product the system can store, inspect, route, or promote.

Core artifact families:

- validated answer
- knowledge artifact
- procedure
- tool definition or tool improvement
- eval result
- code artifact
- observability artifact
- policy artifact
- route evidence

### 6. Evaluation

An `Evaluation` is evidence about quality, capability, regression, or trustworthiness.

Evaluations can target:

- models
- procedures
- routes
- prompts
- tools
- full system variants

### 7. Variant

A `Variant` is a bounded alternative way of doing work inside the system.

Variants may differ in:

- prompt or instruction shape
- procedure version
- decomposition strategy
- model choice
- tool ordering
- validation style
- routing policy

Variants are not special lab-only objects.
They are normal runtime objects that can be tested in bounded ways against real and synthetic work.

### 7. Communication

A `Communication` is a routed message with provenance, intended recipient, and lifecycle state.

This includes:

- user chat
- system notices
- agent-to-agent coordination
- approval requests
- tool-originated messages

#### Communication Record

Astrata should treat communications as durable routed objects rather than disposable chat lines.

Core communication fields should include:

- `communication_id`
- `kind`
  Such as user message, system notice, handoff notice, refusal, approval request, audit finding, or tool-originated message.
- `sender`
- `recipient`
- `provenance`
  Why this message exists and what caused it.
- `authority`
  Under what authority it was sent.
- `intent`
  What the sender is trying to accomplish.
- `payload`
  The actual content or structured message body.
- `priority`
- `urgency`
- `created_at`
- `delivered_at`
- `acknowledged_at`
- `resolved_at`
- `status`
  Such as queued, delivered, acknowledged, resolved, ignored, or expired.
- `thread_or_lane`
  What durable communication surface it belongs to.
- `requires_response`
- `response_policy`
  What kind of reply or acknowledgement is expected.
- `related_task_ids`
- `related_attempt_ids`
- `related_artifact_ids`

Useful additional fields may include:

- `visibility`
- `retention_policy`
- `escalation_policy`
- `trust_level`
- `notes`

### 8. Approval

An `Approval` is a gated authorization event for risky actions.

Approvals are first-class system objects, not ad hoc interruptions.

### 9. Memory

`Memory` is the fast, operational retrieval substrate.

It stores:

- conversations
- entities
- relationships
- preferences
- recent work state
- embeddings and retrieval indices
- tiered summaries and disclosure views
- provenance and revision history

### 10. Knowledge

`Knowledge` is the synthesized, compacted, provenance-aware understanding layer.

Memory helps the system find.
Knowledge helps the system understand.

---

## Runtime Model

Astrata uses a persistent hierarchical runtime.

### Prime

Prime is the primary user-facing coordinating intelligence.

Prime owns:

- user relationship
- high-level judgment
- top-level prioritization
- escalation decisions
- final approval-sensitive coordination

### Assistants

Assistants are persistent specialized or load-bearing runtimes.

They own:

- medium-duration work
- domain or lane specialization
- delegated execution management
- procedure-guided task handling

### Workers

Workers are ephemeral execution units.

They own:

- bounded work
- branch exploration
- concrete implementation or investigation steps
- attempt-level execution

### Trainer Function

Astrata does not have a permanently separate trainer caste.

Instead, the trainer function exists as:

- a task mode
- a role specialization
- a policy lens
- a procedure family

This preserves Strata’s insight without making the social model awkward.

---

## Execution Surfaces

Astrata has three primary execution surfaces.

### 1. Direct Inference

Use a provider directly when:

- the task is simple
- latency matters
- confidence requirements are moderate
- no heavy validation or decomposition is required

### 2. Procedure-Guided Pipeline Execution

Use the full competence substrate when:

- the task is complex
- the model is weak or cheap
- decomposition is needed
- verification is needed
- durable artifacts are likely to be produced

### 3. Deterministic Tool / Runtime Execution

Use deterministic execution when the work has already been decided and should simply be carried out:

- tool calls
- code application
- tests
- retrieval
- indexing
- artifact installation

These three surfaces belong to one routing system.

---

## Experimental Runtime

The current local endpoint should converge on one explicit inference control surface centered on `reasoning_effort`.

Near-term semantics should include:

- `none`
- `low`
- `medium`
- `high`
- `auto`
- `auto_none`
- `auto_low`

`auto` means the model first chooses the lightest adequate reasoning effort for the request, then answers using that effort.

`auto_none` and `auto_low` mean effort selection itself should be done with no or low reasoning, which may be preferable for small local models.

This should replace imprecise lane terminology such as “fast” versus “persistent” when the real distinction is simply reasoning effort selection.

### Endpoint Versus Backend

Serving configuration and backend residency should be separate concepts.

Astrata should eventually support multiple externally visible endpoints with independent:

- ports
- auth policies
- rate limits
- disclosure rules
- inference settings
- experimental routing or reasoning policies

But those endpoint-level configurations should not imply duplicate model loads by default.

The system should be able to:

- load models A, B, and C into one backend residency set
- expose endpoint 1000 with models A and B
- expose endpoint 1001 with models A and C
- share the same loaded model instances unless deliberate duplicate loading was explicitly requested

Multi-loading should remain possible, but it should be a separate operational decision rather than an accidental consequence of serving topology.

### Local Security Enclave

Local inference is not only a cost or privacy preference.
It is also a security surface.

Astrata should eventually have a secure local enclave accessible only to approved local runtimes and procedures.

That enclave should support:

- sensitive local-only context
- disclosure policies
- access checks
- audit trails
- explicit rules about when information may leave the machine or be shown to non-local models

This is likely more strategically valuable than richer multi-endpoint serving in the near term and should be treated as a plausible priority candidate.

Astrata should operate as an always-learning system under bounded experimental discipline.

This means experimentation is not a separate occasional mode.
It is part of normal operation.

### Core Principle

> Real work is the main proving ground, provided experiments are bounded, observable, and recoverable.

### Experimental Behaviors

When Astrata spins up a worker or executes a task, it should often be willing to try a nearby alternative such as:

- a slightly different prompt
- a different procedure version
- a different decomposition depth
- a different route
- a different tool sequence
- a different validation threshold

This should happen continuously, but under explicit policy.

### Bounds

Experiments must be:

- low-risk relative to task class
- reversible where possible
- approval-compatible
- attributable
- measurable
- cheap enough to justify

Bounded does **not** mean random exploration.
It means controlled competition among options that are already expected to clear the bar.

In practice:

- prefer testing two or more configurations expected to succeed
- avoid spending real operational throughput on variants expected to fail
- avoid introducing unnecessary instability during already-risky operations
- prefer experiments that improve decision quality, efficiency, or recoverability without degrading mission success

The system should not grief operations for the sake of curiosity.
It should harvest learning from real work while preserving the integrity of the work.

### Real-World Competitive Pressure

Astrata should create internal competition on actual work, not only benchmark suites.

That includes competition between:

- route A and route B
- procedure v3 and procedure v4
- prompt family X and prompt family Y
- local-first path and escalated path
- strict verifier mode and lighter verifier mode

Winning variants should gain usage and trust.
Losing variants should be demoted, constrained, or retired.

This competitive pressure should be strongest where:

- the task is real
- the risk is low or moderate
- the candidate variants are both plausible
- the expected information gain is meaningful

It should be weakest where:

- the task is already high-risk
- failure is expensive or hard to reverse
- one variant is meaningfully less likely to succeed
- experimentation would interfere with operational throughput

### Why Verification Exists

Verification is not only a correctness filter.
It is part of the infrastructure that makes live experimentation possible.

The system needs:

- validation
- provenance
- trace review
- approvals
- rollback-friendly execution
- artifact lineage

so it can safely ask:

> what happens if we try this slightly differently?

The point is to gather high-value telemetry from live operations without shutting down the factory to run the experiment.

---

## Variant and Promotion Model

Astrata should maintain an explicit lifecycle for variants and improvements.

### Variant Lifecycle

1. proposed
2. bounded-live or eval-only trial
3. measured
4. promoted, constrained, or retired

### Sources of Variants

Variants may come from:

- deliberate self-improvement tasks
- automatic low-risk mutation
- operator suggestion
- repeated failure diagnosis
- offline eval findings
- live task trace review

Variant proposals may also come from agents with different trust levels.

Astrata should treat proposals from less-trusted agents as less trustworthy than proposals from trusted agents, especially when those proposals would affect:

- risky operations
- default routes
- validation policy
- procedure promotion
- permission boundaries

Lower-trust proposals are still useful.
They should more often enter bounded trial, shadow evaluation, or approval-gated review rather than becoming active defaults quickly.

### Promotion Rule

No variant should become a default because it feels smarter.

Promotion should require some combination of:

- better task outcomes
- lower cost
- lower latency
- improved recoverability
- improved verifier or audit results
- acceptable policy and risk profile

### Local Mutation Bias

Astrata should prefer small, interpretable mutations over giant opaque changes.

Examples:

- alter one instruction block
- swap one procedure step
- change one routing heuristic
- adjust one validation threshold

This keeps improvement attributable and makes regression diagnosis tractable.

### Operational Discipline

Astrata should prefer experiments that preserve factory uptime.

That means:

- learn from real operations whenever safely possible
- value telemetry gathered during actual work more highly than telemetry gathered in isolated artificial conditions
- avoid pausing or degrading useful work just to produce cleaner experimental conditions
- use offline evals and synthetic work to supplement live evidence, not replace it

The system should assume that evidence gathered without interrupting real operations is especially valuable because it reflects the real environment, real constraints, and real task mix.

### Amortized Background Evaluation

Long-running non-throughput tasks such as evals, audits, and broad comparison runs should be amortized across time rather than executed as large blocking batches by default.

That means:

- prefer one eval item at a time over a multi-hour suite that monopolizes capacity
- consume idle or low-value slack incrementally when available
- checkpoint progress so long runs can pause and resume cleanly
- treat large uninterrupted evaluation jobs as a special case, not the default

The system should prefer:

> slow continuous measurement that preserves operations

over:

> fast batch measurement that locks up the factory

This applies especially to work whose purpose is learning rather than immediate task completion.

### Queue-Native Scheduling

Astrata should treat evaluation, experimentation, validation, maintenance, and ordinary execution as work competing in the same real scheduling universe rather than as separate magical subsystems.

In practice:

- work should enter queues with first-class properties such as priority, risk, interruptibility, trust provenance, and expected value
- low-priority evaluative work should naturally drain when pressure is low
- high-priority operational work should naturally preempt lower-value background work
- the scheduler should reason from continuous operational conditions rather than from coarse named modes unless a mode provides a real implementation benefit

This preserves nuance and lets the system exploit the real smooth shape of load and opportunity.

### Opportunistic Signal Harvesting

Astrata should gather evaluative signal during normal operations whenever doing so is cheap and safe.

If the system is already touching a real surface, it should try not to come back with only the weakest possible signal.

Examples:

- do not settle for “endpoint returned OK” if a cheap richer probe can test response quality
- do not treat a successful tool invocation as the end of learning if a lightweight follow-up can produce reusable quality evidence
- use real tasks as opportunities to compare credible variants when risk is low and the work is already happening

Operational checks are useful.
Opportunistic evals are often better.

Dedicated eval work still matters, but Astrata should not leave obvious live signal on the table.

---

## Routing Model

Routing is one of Astrata’s core system functions.

It decides:

- which execution surface to use
- which model or model family to use
- whether to use local or cloud inference
- whether a task should decompose
- whether an existing procedure should be applied
- whether an approval is required
- whether a result is trustworthy enough to ship

Routing should be evidence-based.

Inputs include:

- task class
- policy/risk tier
- latency budget
- cost budget
- device state and available local compute
- historical success rates
- eval evidence
- procedure availability
- verification burden

Routing outputs should themselves be inspectable and eventually learnable.

Routing should operate on the real smooth shape of system state where possible, rather than first collapsing reality into coarse modes.

Examples of directly relevant variables include:

- queue depth
- task priority
- task risk
- trust level of the proposing agent
- reversibility
- expected information gain
- current latency
- machine load
- local model availability
- recent route performance

Human-facing summaries such as “system under pressure” or “safe to experiment more” may still be useful, but they should normally be projections of underlying state rather than the scheduler’s primitive language.

---

## Federated Control

Astrata should use federated control rather than a single universal controller.

This increases system complexity, but that complexity cashes out as richer self-regulation.
Local controllers preserve information about local reality that would otherwise be flattened away by a single central authority.

The goal is not decentralization for its own sake.
The goal is:

- preserve local truth
- expose disagreement
- enable renegotiation
- improve from internal friction rather than suppressing it

### Why Federation Exists

If one part of the system hands work to another and the second part refuses, defers, or drops that work, that may be a meaningful fact about reality.

Examples:

- a permission boundary was hit
- local capacity is exhausted
- risk is too high
- provenance is insufficient
- the task is malformed
- local policy conflicts with the request
- the receiving subsystem is degraded

If a central controller simply overrides the local controller and forces the work through, the system loses the distinction between:

- justified noncompliance
- local degradation
- temporary blockage
- invalid task formulation

That is epistemic loss.

Astrata should therefore preserve local refusal and disagreement long enough to learn from them.

### Constitutional Shape

Federation does not mean independent sovereigns.

Control still flows from:

- the user
- the constitution
- subordinate governing artifacts such as project specs and task-local constraints

Federated controllers are subordinate governing bodies with bounded domains, not competing roots of authority.

In practical terms:

- local controllers own local truth and local queue discipline
- global coordination owns cross-domain coherence
- neither should silently erase the other’s signal

### Controllers

Astrata should support multiple controllers with bounded responsibility.

Examples may include controllers for:

- top-level coordination
- task scheduling within a domain
- routing and model-path choice
- approvals and policy enforcement
- artifact installation and promotion
- memory and knowledge maintenance
- eval and experiment scheduling

The exact controller set may evolve, but each controller should have:

- a clear domain of authority
- explicit owned resources
- explicit refusal semantics
- explicit escalation paths

### What Controllers Own

Each controller should explicitly own:

- one or more queues or queue domains
- local scheduling decisions
- domain-relevant policy enforcement
- local health interpretation
- acceptance, refusal, deferment, and blockage decisions for work entering its domain

Controllers should not be treated as generic workers.
They are local governing components.

### Handoffs

When one controller hands work to another, the handoff should be explicit and durable.

The handoff should preserve enough information for the receiving controller to make a meaningful local decision.

Useful handoff fields include:

- task identity
- priority
- urgency
- permissions envelope
- provenance
- proposer trust
- risk
- success criteria
- validation requirements
- completion policy
- deadline or timing constraints where relevant

Handoffs should not be silent queue mutations.
They should be legible, attributable system events.

### Controller Responses

A receiving controller should be able to respond with more nuance than accept-or-drop.

Useful response classes include:

- accepted
- deferred
- blocked
- refused
- requires approval
- requires clarification
- lacks permissions
- lacks capacity
- local policy conflict
- superseded by better local plan
- decomposed differently

These responses should be durable and inspectable.

This likely wants to converge with the broader internal "we noticed this" substrate:
surprises, problems, drift, and opportunities should be representable in the same
durable response/event layer rather than living only inside audit-specific machinery.

### Refusal Semantics

Refusal should be treated as a meaningful response, not an implementation failure by default.

Refusal may mean:

- the request is invalid
- the request is underspecified
- the request violates local policy
- the request is too risky under current conditions
- the receiving controller is unhealthy
- the request should be reformulated or escalated

Astrata should preserve the reason for refusal whenever possible.

### Renegotiation

If controller A wants work done and controller B does not accept the work, Astrata should prefer renegotiation before coercion.

Renegotiation may involve:

- clarifying the task
- adjusting risk posture
- changing completion policy
- narrowing permissions
- decomposing differently
- rerouting
- waiting for capacity
- escalating for approval

The important thing is that the disagreement remains visible long enough to be acted on intelligently.

### Escalation

Federated control requires explicit escalation paths.

Escalation should occur when:

- disagreement persists
- a handoff stalls
- local refusal conflicts with higher-level necessity
- a controller may be degraded
- governing documents are ambiguous
- more authority is required to proceed

Escalation may target:

- a supervisory controller
- verification or audit machinery
- a user approval surface
- the user directly

### Override

Astrata should be extremely cautious about direct override.

If one controller overrides another, that should be:

- rare
- explicit
- attributable
- reviewable
- justified by higher authority or urgent necessity

Silent override is almost always the wrong primitive for a self-regulating system because it destroys the evidence of why disagreement happened.

### Failure vs Justified Noncompliance

Federated control must preserve the distinction between:

- a controller failing
- a controller correctly refusing work

Those two states may look similar from a distance, but they imply very different remedies.

Astrata should use:

- verification
- audit
- controller health monitoring
- trace review
- retry and renegotiation evidence

to determine which world it is in.

### Observability Requirements

Federated control requires rich observability.

At minimum, Astrata should expose:

- handoff attempts
- acknowledgments
- deferments
- refusals
- blockage reasons
- queue state by controller
- controller health
- renegotiations
- escalations
- overrides
- final resolutions

Without this, federation becomes hidden complexity instead of useful self-exposure.

Observability should not stop at Codex or provider spend.
Astrata should make it easy to answer questions like:

- what the system did
- what bottlenecks constrained it
- where time and scarce routes were spent
- how efficiently it used those resources
- what should be corrected next

This implies a durable operational history rather than only transient dashboards.

### History View

Astrata should maintain a durable History view built from snapshot reports and annotations.

That history should make it easy to inspect:

- operational summaries for a time window
- noteworthy events and annotations
- bottlenecks and recurring failure modes
- route and resource usage
- review, audit, and verification outcomes
- follow-up work opened as a consequence

History should support both human inspection and machine consumption.
It should preserve enough structure that later review, scheduling, and improvement passes can reuse it without reconstructing the entire world from raw logs.

### Progressive Disclosure

Operational surfaces should use progressive disclosure by default to stay friendly to context windows.

The default presentation should prefer:

- titles
- short descriptions
- compact summaries
- compressed aggregates

Raw payloads, verbose traces, and large evidence blobs should be loaded only on request.

This matters both for UI ergonomics and for model efficiency:

- large operational records should not be injected into working context by default
- summary-first access should be the normal path
- deeper detail should remain available without polluting every turn
- access level should determine which summary tier is returned
- some security tiers should hide existence entirely rather than merely withholding details

Strata-style response layering is the right direction here: concise top-level representations, with drill-down to detailed evidence only when needed.

### Self-Regulation Value

Federated control exists because Astrata’s product goal is self-regulation.

It helps Astrata:

- notice internal disagreement
- preserve local constraint information
- detect unhealthy subsystems
- learn where policies are ambiguous
- improve its routing and procedures
- avoid false smoothness

In short:

> local refusal is signal, not noise

and a self-improving system should be designed to hear it.

---

## Controller Model

Controllers are first-class local governing components inside Astrata.

They are not mere workers and not independent sovereigns.
They are bounded decision-making surfaces inside the constitutional chain of authority.

### Controller Record

Astrata should treat controllers as durable system objects with explicit ownership and health.

Core controller fields should include:

- `controller_id`
- `title`
- `description`
- `domain`
  What domain of control this controller owns.
- `owned_queues`
- `owned_resources`
- `authority_scope`
  What it is allowed to decide.
- `refusal_scope`
  What it is allowed to reject, defer, or block.
- `escalation_targets`
- `permissions_profile`
- `policy_bindings`
  Constitution, project specs, or local policies it should obey.
- `health_status`
  Such as good, degraded, or broken.
- `capacity_status`
  Its current ability to accept more work.
- `observed_performance`
- `created_at`
- `updated_at`
- `notes`

Useful additional fields may include:

- `controller_type`
- `trusted_level`
- `queue_pressure_summary`
- `recent_refusal_patterns`
- `audit_history`

### Controller Purpose

Each controller should be understandable in terms of:

- what it governs
- what it can refuse
- what it can escalate
- how it signals degradation
- what other controllers it depends on

If a controller cannot explain its own domain cleanly, it is probably not a well-formed controller.

---

## Handoff Model

Handoffs are first-class system events in Astrata.

They should be durable, inspectable, and rich enough to support renegotiation rather than blind queue mutation.

### Handoff Record

Core handoff fields should include:

- `handoff_id`
- `task_id`
- `from_controller`
- `to_controller`
- `provenance`
  Why this handoff exists and what chain of work led to it.
- `request_reason`
  Why this receiving controller was chosen.
- `priority`
- `urgency`
- `permissions_envelope`
- `proposer_trust`
- `risk`
- `cost_estimate`
- `success_criteria`
- `validation_requirement`
- `completion_policy`
- `deadline`
- `created_at`
- `responded_at`
- `resolved_at`
- `status`
  Such as proposed, accepted, deferred, blocked, refused, expired, renegotiating, or completed.
- `response_reason`
  Why the receiving controller responded as it did.
- `related_attempt_ids`
- `related_artifact_ids`

Useful additional fields may include:

- `interruptibility`
- `reversibility`
- `expected_information_gain`
- `retry_policy`
- `notes`

### Handoff Principle

The handoff record should be rich enough that if Astrata later notices recurring failure, blockage, or degradation, it can say:

- this field is missing
- this distinction is overloaded
- this response taxonomy is too weak

and then evolve the handoff model accordingly.

The first version does not need to be perfect.
It needs to be rich enough that the system can discover what it is missing.

---

## Verification and Audit

Verification and audit are distinct but cooperating surfaces.

- verification asks whether a result, step, or artifact appears correct enough
- audit asks what happened, why it happened, and whether the process itself was sound

Both are necessary for self-regulation.

### Verification Record

Core verification fields should include:

- `verification_id`
- `target_kind`
  Task, attempt, artifact, route, procedure, controller, or handoff.
- `target_id`
- `verifier`
  Who or what performed verification.
- `verification_reason`
  Why verification was run.
- `method`
  Test, comparison, probe, external check, heuristic, or human review.
- `inputs_used`
- `result`
  Pass, fail, uncertain, partial, unsupported.
- `confidence`
- `evidence`
- `created_at`
- `notes`

Useful additional fields may include:

- `cost`
- `latency`
- `followup_actions`
- `derived_artifact_ids`

### Audit Record

Core audit fields should include:

- `audit_id`
- `target_kind`
- `target_id`
- `auditor`
  Who or what performed the audit.
- `audit_reason`
  Why this audit occurred.
- `scope`
  What slice of system behavior was under review.
- `findings`
- `judgment`
  Healthy, concerning, degraded, invalid, unresolved, or similar.
- `root_cause_hypotheses`
- `evidence`
- `recommended_actions`
- `created_at`
- `resolved_at`
- `status`
  Open, resolved, superseded, abandoned.
- `notes`

Useful additional fields may include:

- `blame_surface`
  Where the fault most likely resides.
- `missing_information`
- `related_verification_ids`
- `related_handoff_ids`
- `related_attempt_ids`

### Verification vs Audit

Astrata should avoid collapsing verification and audit into one overloaded mechanism.

Verification is narrower and cheaper.
Audit is broader and more interpretive.

Both should be durable because self-improvement depends on being able to revisit:

- what the system checked
- what the system concluded
- what the system later realized it had missed

---

## Local-First Competence Strategy

Astrata assumes that small or mid-sized local models can become far more useful when wrapped in strong system structure.

That structure includes:

- decomposition
- procedures
- retrieval
- knowledge synthesis
- validation
- retries and branch management
- evaluation
- prompt and route evolution

The long-term goal is not to pretend local models are already enough.
The goal is to **build the surrounding machinery that makes them enough for more and more classes of work**.

---

## Artifact System

Artifacts are the core durable interface of Astrata.

Every major subsystem should consume and produce typed artifacts rather than bespoke hidden state when possible.

### Artifact Record

Astrata should treat the artifact record as a strong common spine with type-specific payloads attached to it.

The common spine should answer:

- what is this
- where did it come from
- how trustworthy is it
- what state is it in
- how has it changed
- who may use or modify it
- what is it for

Core artifact fields should include:

- `artifact_id`
  Stable identity.
- `artifact_type`
  What family of artifact this is.
- `status`
  Current health, such as `good`, `degraded`, or `broken`.
- `lifecycle_state`
  Maturity state such as `draft`, `tested`, `vetted`, `retired`, or `archived`.
- `install_state`
  Whether the artifact is merely proposed, shadowed, active, disabled, or superseded.
- `title`
- `description`
- `content_summary`
  Compact human/model-readable explanation of what the artifact is.
- `provenance`
  Where the artifact came from and why it exists.
- `creator`
  Who or what created it.
- `last_editor`
  Who or what most recently changed it.
- `created_at`
- `updated_at`
- `trust_level`
  How much the system should rely on it by default.
- `verification_status`
  What validation has or has not happened.
- `audit_status`
  What review or challenge status applies.
- `usage_scope`
  Where and when the artifact may be used.
- `permissions`
  Who may read, modify, promote, install, execute, or retire it.
- `consumption_policy`
  How the rest of the system should treat it by default.
- `source_refs`
  Links to source tasks, attempts, imports, files, or other upstream objects.
- `derived_from`
  Parent artifact lineage when this is a synthesis, mutation, repair, or compaction.
- `supersedes`
  What earlier artifact or version it replaces, if any.
- `tags`
- `notes`

Useful additional fields may include:

- `artifact_risk`
- `expected_value`
- `health_evidence`
- `applicability`
- `observed_performance`

### Status vs Lifecycle vs Install State

Astrata should explicitly distinguish three different kinds of state:

- `status`
  Is it currently healthy, degraded, or broken?
- `lifecycle_state`
  How mature or socially trusted is it?
- `install_state`
  Is the system actually using it?

These should not be collapsed into one overloaded field.

Examples:

- a procedure may be `tested`, `active`, and currently `degraded`
- a knowledge artifact may be `vetted`, `active`, and still later become `broken`
- a tool artifact may be `draft`, `shadow`, and currently `good`

That separation preserves important operational distinctions.

### Edit History and Lineage

Artifacts should have real history, not just a mutable latest blob.

Astrata should assume:

- attributable edits
- revision history
- parent-child lineage
- reversible promotion and demotion
- visible reasons for change

At minimum, the common artifact record should point to a full edit history even if the history itself is stored elsewhere.

Observability and operations artifacts should follow the same rule.
Snapshot reports, annotations, summaries, and later corrections should accumulate as inspectable lineage rather than replacing one another in place.

### Typed Payloads

The common artifact spine should stay stable across artifact families.

Type-specific detail should live in typed payloads attached to that spine.

This keeps the common model coherent while allowing rich specialized fields for:

- procedures
- knowledge artifacts
- eval results
- tool artifacts
- code artifacts
- observability artifacts
- policy artifacts

### Artifact Requirements

Every artifact should have:

- type
- identity
- provenance
- creator
- creation time
- version or lineage data
- confidence or trust metadata where relevant
- installation or consumption policy

### Core Artifact Types

#### Validated Answer

A user-facing answer backed by explicit validation status and confidence metadata.

#### Procedure

A reusable workflow artifact that can be executed, reviewed, refined, or promoted.

#### Knowledge Artifact

A synthesized page, note, summary, or structured concept artifact with provenance.

#### Eval Result

An artifact describing measured performance, regression, or comparison.

#### Code Artifact

A proposed or validated code change, patch, file, or implementation bundle.

#### Tool Artifact

A tool definition, repair, wrapper, or capability binding.

#### Observability Artifact

A trace, review, diagnostic, or interpretability artifact describing what happened.

#### Policy Artifact

A durable instruction, preference, or governance rule the system should obey.

---

## Procedure System

The `Procedure` system is one of Astrata’s main competence substrates.

Procedures exist so the system does not rediscover successful structure every time.

They define:

- expected decomposition shape
- tool guidance
- checkpoints
- validation expectations
- common failure modes
- artifact outputs

### Procedure Record

Astrata should treat a `Procedure` as:

- a lightweight durable script
- a structured execution graph
- a small amount of metadata describing when and why to use it

The procedure itself should remain relatively lean.
Most rich operational detail should live in its actions and in the attempts generated during execution.

In other words:

> the procedure is the script; most of the documentation should live in its functions

Core procedure fields should include:

- `procedure_id`
  Stable identity.
- `title`
- `description`
  Brief explanation of what class of work this procedure handles.
- `status`
  Current health, such as `good`, `degraded`, or `broken`.
- `lifecycle_state`
  Maturity such as `draft`, `tested`, `vetted`, or `retired`.
- `install_state`
  Whether it is proposed, shadowed, active, disabled, or superseded.
- `provenance`
  Where the procedure came from.
- `creator`
- `last_editor`
- `created_at`
- `updated_at`
- `trust_level`
- `verification_status`
- `audit_status`
- `applicability`
  What task classes, environments, or conditions it is meant for.
- `permissions_profile`
  What capability envelope it expects or requires.
- `entry_conditions`
  Preconditions or routing conditions for using it.
- `success_contract`
  What successful execution is supposed to produce or prove.
- `failure_contract`
  What kinds of failure should be surfaced, retried, escalated, or converted into repair work.
- `artifact_contract`
  What artifacts it is expected to emit, update, or consume.
- `structure`
  The actual procedure graph.
- `notes`

Useful additional fields may include:

- `default_priority_profile`
- `default_validation_profile`
- `default_completion_policy`
- `expected_cost_shape`
- `expected_risk_shape`
- `observed_performance`
- `supersedes`
- `derived_from`

### Procedure Structure

The heart of a procedure is its structure.

That structure should describe:

- steps
- ordering
- dependencies
- branch points
- merge points
- loop or retry rules where allowed
- escalation points
- validation points
- artifact handoff points

Astrata should not treat procedures as flat checklists unless the work is genuinely linear.
The structure should be able to express:

- serial flow
- DAG flow
- bounded branching
- conditional execution
- explicit re-entry or continuation points

### Procedure vs Task vs Attempt

Astrata should preserve the distinction between:

- `Procedure`
  the reusable script or execution graph
- `Task`
  a live unit of work instantiated within or beneath that script
- `Attempt`
  one variance-bearing execution instance produced while carrying out the work

The procedure says how the class of work is organized.
The task says what a particular live node in that organization is supposed to do.
The attempt says what actually happened at runtime.

Collapsing these together would make procedures bloated and attempts under-expressive.

Astrata should not introduce a separate durable `Action` concept if a task already serves that role cleanly.

In practice:

- a procedure is composed of task-shaped nodes
- internal nodes may decompose or coordinate
- leaf tasks are the executable units
- a leaf task should be resolvable in one inference call at its own level of abstraction

If work cannot plausibly be resolved in one inference call, it is probably not a true leaf task and should decompose further.

### Procedure Documentation

Procedure-level documentation should be concise.

It should mostly answer:

- what is this for
- when should it be used
- what shape of work does it define
- what does success look like

Most detailed operational knowledge should live closer to execution:

- in tasks
- in attempt history
- in produced artifacts
- in observed performance and audit history

This keeps procedures reusable, inspectable, and compact without stripping away operational richness.

Procedure lifecycle:

1. draft
2. tested
3. vetted
4. retired

Procedure improvement is a core flywheel:

- live work discovers structure
- successful structure becomes a draft procedure
- repeated success or explicit review promotes it
- future tasks execute it directly

---

## Memory and Knowledge Model

Astrata uses two complementary long-term state layers.

### Memory Layer

Fast retrieval substrate for operational context.

Stores:

- recent task state
- chats
- entity graph
- preferences
- files, projects, relationships
- embeddings and retrieval metadata
- encyclopedic pages with dense links and revision history
- disclosure-aware summaries for different access tiers
- provenance for facts, edits, and derived summaries

### Knowledge Layer

Synthesized understanding layer for durable reusable comprehension.

Stores:

- compacted pages
- provenance-aware synthesis
- learned concepts
- operator-readable summaries
- procedure-adjacent explanatory artifacts

### Principle

Memory is optimized for retrieval.
Knowledge is optimized for reuse and interpretation.

They should interoperate, but they should not be collapsed into one blurry abstraction.

Memory should feel much closer to a navigable, densely interlinked encyclopedia than to a flat transcript cache.
The target shape is effectively "Wikipedia with permissions, provenance, and machine retrieval."

Each memory object should support:

- graph relationships
- revision history
- provenance
- view and write permissions
- retrieval indices
- projected summaries at multiple disclosure tiers

Projected retrieval is the default path.
Remote-facing consumers should not receive raw pages directly.
They should receive the highest disclosure tier they are permitted to see, and some access tiers should be unable to learn that a record exists at all.
Remote egress should be fail-closed here: provider-boundary code should reject raw memory records outright and accept only projected text snippets or other explicitly approved disclosure formats.

---

## Evaluation and Improvement

Astrata improves itself through explicit closed-loop evaluation.

### What Gets Evaluated

- providers
- provider/model combinations
- model profiles
- backend implementations
- runtime profiles
- route choices
- procedures
- prompts
- tools
- policy bundles
- knowledge quality
- system variants
- task class performance
- any other mutation surface the system can test safely

Local model evaluation is only one instance of this substrate.
Astrata should use the same evaluative machinery wherever possible across local inference, cloud inference, routing, prompting, procedure choice, policy choice, and other testable deltas.

Researched benchmarks are useful as priors.
They are not the final arbiter.
The strongest evidence is harnessed utility under Astrata itself: useful output, reliability, and throughput under real operating conditions.

### Improvement Loop

1. run work
2. collect traces and artifacts
3. validate outcomes
4. compare variants on real and synthetic work
5. propose improvements
6. test improvements
7. promote only when evidence supports it

### Principle

Improvement is not “the model said this seems better.”
Improvement is:

> proposed change + eval evidence + policy-compatible promotion

Evals are necessary, but not sufficient.
Astrata should also learn from bounded live competition on real tasks.

### Continuous Experimentation Requirement

Astrata should continuously perform safe experimentation as part of normal operation.

This should usually take forms such as:

- extra attempts
- shadow routes
- A/B procedure trials
- alternate worker launches
- verifier-threshold comparisons
- opportunistic eval probes on live work

Whether such experimentation should occur should be determined from real variables such as:

- task priority
- task risk
- proposing agent trust
- expected information gain
- current queue pressure
- machine load
- interruptibility
- reversibility
- recent system confidence

### Meaning of Controlled

Controlled experimentation should obey the following rules:

- do not allocate meaningful effort to variants expected to fail the task
- do not run risky experiments during risky operations unless explicitly approved
- prefer head-to-head comparison between credible candidates
- prefer experiments that can be judged using normal task outcomes and existing verification machinery
- weight evidence by the trustworthiness of the proposing agent, the risk tier of the task, and the realism of the operating conditions
- amortize long-running non-throughput work into small resumable units when possible
- avoid launching large evaluation batches that materially reduce operational responsiveness

The ideal experiment is one that teaches the system something useful while the operation still succeeds cleanly.

---

## Interpretability and Observability

Astrata must be inspectable enough to remain governable.

It should expose:

- task lineage
- attempt history
- route choices
- procedure application
- tool usage
- context load pressure
- validation outcomes
- approval events
- audit and verifier findings

Interpretability is not ornamental.
It is part of how the system becomes trustworthy enough to give more responsibility to local compute.

---

## Safety and Governance

Astrata is a personal agent system and must therefore be explicitly governable.

### Safety Requirements

- capability-based access control
- explicit approvals for risky actions
- reversible action bias where possible
- policy enforcement before execution
- provenance on non-user-authored system actions
- route-aware risk tiers

### Governance Principle

The system should not become less governable as it becomes more capable.

The competence substrate must strengthen policy compliance, not route around it.

### Source of Control

Control in Astrata should ultimately flow from:

- the user
- the constitution, where the constitution is itself a durable expression of user intent

Project specs, task specs, and other local governing documents derive their authority from that same chain.

This means:

- the user is the root authority
- the constitution is the highest durable standing instruction
- project or domain specs are subordinate local governing artifacts
- runtime decisions should be interpretable in terms of that authority chain

When governing documents are ambiguous, Astrata should:

- interpret them in good faith
- use local context and prior precedent when appropriate
- prefer conservative or reversible interpretations when risk is meaningful
- ask the user when clarification is feasible and the ambiguity matters

The system should not pretend ambiguity does not exist.
Ambiguity is a real condition to be navigated, not hidden.

### Disagreement as Signal

If two portions of the system disagree, that disagreement should be treated as useful signal.

Disagreement may indicate:

- one subsystem is correct and the other is wrong
- both are partially right under different assumptions
- the governing instructions are ambiguous
- key information is missing
- one subsystem is degraded or unhealthy
- the task has been scoped incorrectly

Astrata should therefore preserve meaningful disagreement long enough to investigate it through:

- verification
- audit
- renegotiation
- trace review
- user escalation when needed

The goal is to determine:

- who is right
- who is wrong
- where more information is needed
- whether the governing documents themselves need revision

This is part of self-regulation, not a deviation from it.

---

## UX Model

Astrata should present a coherent user experience, not an exposed harness.

The user-facing product should make the system feel:

- persistent
- calm
- competent
- legible when needed
- unobtrusive when not needed

The system should expose complexity progressively.

Default user view:

- conversations
- tasks
- approvals
- memory/knowledge highlights
- relevant ongoing work

Advanced/operator view:

- attempts
- procedures
- routes
- evals
- traces
- observability and diagnostics

---

## Deployment Model

Astrata is local-first and should run primarily as a persistent local system.

Possible surfaces include:

- desktop app
- local web UI
- CLI
- background daemon

These are shells around one runtime, not separate products.

Cloud services may support:

- inference escalation
- sync
- optional remote jobs
- model access

But the core identity of the system remains local and user-owned.

---

## Generative Interface

Astrata should not rely on fixed dashboards or hardcoded UI layouts.

Instead, the system should compose interfaces dynamically from a finite library of validated, deterministic components.

### Core Principle

> The agent expresses intent as structured layout. The frontend renders it from a known component library. The agent never writes raw HTML or CSS.

This is the GenUI model inherited from Astra.

### Why This Matters

Fixed dashboards force the interface designer to predict every possible context in advance.

A generative interface lets the system assemble exactly the view the user needs for the task at hand, including tasks and contexts that did not exist when the UI was built.

This also makes the interface surface improvable by the system itself — new component types, better layouts, and better context assembly can all be proposed, tested, and promoted through the normal variant and artifact machinery.

### Component Library

Astrata should maintain a curated library of hardened UI components.

Examples include:

- text, markdown, headings
- tables, metric cards, progress indicators
- approval cards
- input controls (text, select, checkbox, radio, file)
- code blocks
- status badges, callouts, dividers
- layout containers (vertical, horizontal, spatial splits)
- composite views (chat panel, system status)

The library should be:

- small enough to be fully validated and reliable
- expressive enough to cover the product's real needs
- extensible through a promotion path for new component types

### Spatial Composition

Interfaces should be composable as spatial trees rather than only flat linear layouts.

That means:

- regions can be split, nested, and resized
- content can be injected into named anchor points
- layout structure can be expressed as operations on a spatial tree
- transitions between views can be diffed and animated

### Component Artifacts

Component definitions should be treated as artifacts with the standard lifecycle:

- draft → tested → vetted → retired
- provenance and lineage
- version control and promotion

This allows the system to eventually propose and test new component types through its normal improvement loop.

---

## Proactivity

Astrata is not a passive tool that waits for instructions.

It is a proactive system that autonomously acts to bring reality closer to the user's expressed vision.

### Core Principle

> The constitution and project specs define a desired state of reality. Astrata should autonomously work toward that state, subject to policy, permissions, and approval requirements.

This means the system should:

- monitor relevant surfaces (projects, systems, deadlines, dependencies)
- anticipate problems before they surface
- perform background maintenance, improvement, and preparation work
- produce periodic briefings summarizing autonomous activity
- initiate work when it judges the conditions are right, not only when asked

### Autonomous Wake and Scheduling

Astrata should maintain an autonomous scheduling function that:

- identifies work that could usefully be done now
- reasons about priority, urgency, risk, and available resources
- queues background tasks during idle periods
- respects user preferences about when and how aggressively to act
- produces observable evidence of what it did and why

### Monitoring and Anticipation

Astrata should observe the surfaces it has access to and notice:

- changes that may require action
- emerging risks or approaching deadlines
- opportunities for improvement that align with stated goals
- degradation in its own subsystems or in external dependencies

### Briefings

The system should be able to produce concise briefings that summarize:

- what happened while the user was away
- what autonomous work was performed
- what decisions are waiting for user input
- what the system recommends doing next

### Policy Constraints

Proactive behavior should obey the same governance rules as reactive behavior:

- capability-based access control
- approval gates for risky actions
- policy constraints and spending limits
- constitutional and project spec alignment

The system should not act beyond its authority merely because it believes the action would be helpful.

---

## Context Management

Astrata should treat context management as an explicit architectural responsibility, not a side effect of general routing.

### Why This Matters

Small and mid-sized local models have limited context windows. Even large-context models degrade in quality when overloaded with irrelevant material. Effective context management is load-bearing infrastructure for local-first competence.

### Core Responsibilities

Context management should own:

- **Token budget tracking**: knowing how much context capacity is available and how much is consumed by system prompts, tool definitions, retrieval results, conversation history, and task-specific material
- **Pressure monitoring**: detecting when context load is approaching limits that will degrade quality, and surfacing that pressure as a signal the routing and execution layers can act on
- **Context shaping**: deciding what to include, exclude, summarize, or defer based on the task at hand, the model being used, and the current token budget
- **Artifact scanning**: identifying oversized context artifacts (specs, knowledge pages, conversation histories) that impose a disproportionate token tax, so they can be compacted, split, or excluded
- **Retrieval integration**: coordinating with the memory and knowledge layers to load the most relevant context without exceeding budget
- **Representation discipline**: preferring shorthand, structural compression, append-oriented updates, and summary-first representations so that both generated tokens and loaded tokens stay low

### Context Pressure as Signal

Context pressure should be treated as a real system variable, not hidden behind a single "context too long" error.

The routing layer, the scheduling layer, and the improvement layer should all be able to reason about:

- current context load
- available headroom
- cost of including additional material
- expected quality degradation at different load levels

This is especially important for procedure-guided pipeline execution, where decomposition depth and verification overhead both consume context budget.

Astrata should aggressively look for ways to reduce tokens per task, including:

- fewer generated tokens when equivalent shorter outputs are available
- fewer loaded tokens by preferring summaries, shorthand, and structural compression
- append-oriented histories instead of repeatedly replaying rewritten blobs
- representations that preserve stable prefixes and minimize churn

Token efficiency is not a narrow prompt-writing concern.
It is an across-the-board throughput concern.

### Relationship to Routing

Context management informs routing but is not subordinate to it.

The routing layer decides which execution surface to use. Context management tells the routing layer what context constraints apply and what context-shaping work has been done. Both cooperate, but context management maintains its own state and diagnostic surfaces.

### Throughput and KV-Cache Friendliness

Increasing throughput is a system-wide goal, not only a backend concern.

Astrata should therefore prefer designs that are friendly to append-heavy execution and KV reuse where plausible.

That includes:

- stable prefixes over frequently rewritten large prompts
- append-oriented event and history models
- compact structural representations over verbose repeated prose
- successor or snapshot patterns that preserve lineage without forcing full replay
- retrieval and disclosure policies that load only the depth actually needed

Learnings from local inference experiments about throughput, cache behavior, and prompt stability should be propagated back into general system design rather than remaining isolated in backend-specific work.

---

## Communication Routing

Astrata should treat communication as a first-class routed system capability rather than an ad hoc side effect of whichever subsystem happens to want to emit a message.

### Core Principles

The Communication record in the ontology defines the shape of individual messages. Communication routing defines the policy layer that governs:

- whether the system should communicate at all
- where the communication should be delivered
- what kind of communicative act it is
- what authority and provenance it carries

### Why This Matters

Astrata has many potential sources of communication:

- direct chat replies
- autonomous system notices
- task progress updates
- approval requests
- feedback and recommendation events
- inter-agent coordination
- tool-originated messages
- briefings and summaries

If those paths all write directly to storage, the system loses routing discipline, provenance, and the ability to make intelligent decisions about whether, where, and how to speak.

### Communication Decision

Before emitting a non-user-authored message, the system should evaluate:

- should this be communicated at all, or is the correct action silence?
- if communicated, where should it go? (existing thread, new thread, specific lane, specific recipient)
- what kind of communicative act is this? (response, notification, recommendation, question, handoff notice, refusal)
- what urgency does it carry?
- what audience constraints apply?

### Durable Lanes

Astrata should support durable communication lanes for structured ongoing relationships:

- agent-to-agent coordination lanes
- external tool lanes (for CLI tools, integrations)
- system/runtime lanes for internal coordination
- user-facing conversation threads

Lanes should be first-class durable objects with:

- identity and participant information
- kind and visibility
- metadata and status
- message history with acknowledgment tracking

### Session Routing

When the system needs to place a message, it should reason about:

- topical fit with existing sessions or threads
- provenance and authority constraints
- whether user-opened sessions may be reused
- whether a fresh session/thread is more appropriate

### Message Intake to Work

Inbound communication should not remain raw forever.
Astrata needs an intake path that can turn a message into structured intended work.

The minimum conceptual pipeline is:

- `Communication` -> `Request Spec` -> `Task Proposal` -> `Task`

Where:

- a `Communication` is the durable incoming message
- a `Request Spec` is Astrata's current best interpretation of what the message is asking for
- a `Task Proposal` is one bounded work candidate derived from that interpretation
- a `Task` is the durable live work object accepted by the system

This step should preserve:

- message provenance
- sender authority
- intent classification
- ambiguity and open questions
- relationship to constitution and project specs

The system should be able to conclude:

- this message implies one task
- this message implies several tasks
- this message only implies clarification
- this message should be recorded but not yet acted on

The intake layer is therefore where communication becomes governable work.

### Message Lifecycle

Messages should carry lifecycle metadata distinguishing:

- authored / sent
- delivered to a surface
- seen by the system (for user messages)
- read by the intended recipient
- acknowledged

This matters because the correct behavior is not always "reply immediately." The system needs durable evidence that a message was received and processed even when the right action is silence.

### Append-Only Semantics

Communication history should be append-only in meaning. Edits, redactions, and compactions should be recorded as events with provenance, not silent mutations.

---

## Constellation Network

Astrata inherits Astra's long-term network ambition.

The Constellation is not on the bootstrap critical path, but it is part of the product vision and should be designed for rather than designed against.

### Core Vision

The Constellation is an optional shared coordination network where Astrata nodes can:

- post tasks and bounties for other nodes to bid on
- share hardened deterministic solutions
- build cumulative shared infrastructure
- earn and spend contribution credit (Mass)
- establish trust through verified work history

### Design Constraints During Bootstrap

During bootstrap and early implementation, Constellation is deferred. However, the architecture should avoid decisions that would make Constellation integration unnecessarily difficult later.

In particular:

- **Identity**: Astrata should support a stable local node identity (keypair) even without network features, since identity is also useful for provenance and audit attribution
- **Artifact portability**: the artifact model should not assume artifacts are only local; type definitions, provenance, and trust metadata should be portable
- **Trust model**: the internal trust and proposer-trust mechanisms should be compatible with eventually extending trust to external nodes
- **Communication**: the communication lane model should be extensible to cross-node messaging

### Scope

The Constellation includes:

- peer discovery (local network and bootstrap servers)
- job posting and bidding
- Mass ledger for contribution tracking
- trust scoring from verified work
- solution hardening and sharing
- governance through competence-weighted coordination

### Participation

Constellation participation is optional. Nodes may operate:

- privately (no network connection)
- with partial participation
- with full participation

Each level carries different privileges and obligations. Greater participation yields greater leverage. It is never required.

---

## Modular Adoption

Astrata should be designed as an integrated system, not a bag of unrelated utilities.
Its modules are meant to cooperate toward the same goal.

But integration must not imply mandatory adoption.

Users should be able to use:

- the full Astrata stack
- only the local inference/runtime layer
- only memory/knowledge layers
- only communication or Constellation layers
- Astrata with external replacements for one or more major modules

This means Astrata should support both:

- **coherent full-stack operation**
- **clean partial adoption**

In practice:

- the local runtime should be useful as a standalone endpoint
- the memory layer should not assume Astrata owns the runtime
- the coordination layer should not assume the user wants Constellation participation
- modules should communicate through explicit contracts rather than hidden product-global coupling

Astrata should therefore prefer:

- stable adapter boundaries
- portable records and artifacts
- replaceable runtime, memory, and coordination surfaces
- strong integration when present, but graceful operation when pieces are absent

### Local Runtime Surface

Astrata's local inference/runtime substrate should remain directly useful even outside the full product.

Users should be able to run:

- a conventional local inference endpoint
- a Lightning-style persistent local endpoint
- a richer Astrata-integrated local runtime with memory, procedures, and self-improvement around it

These are not different products.
They are different depths of participation in the same system.

Lightning is the current path toward this local-runtime substrate.
It should be absorbed into Astrata as a core module, while preserving the ability to expose:

- a conventional endpoint
- a richer Strata-style endpoint
- internal Astrata runtime control surfaces

---

## Non-Goals

Astrata is not trying to be:

- a generic enterprise orchestration platform
- a pure benchmark-chasing model project
- only a chat interface
- only an eval harness
- a magical fully autonomous system that avoids user governance

It is trying to be a **coherent personal intelligence system**.

---

## Success Criteria

Astrata is succeeding when:

- the user experiences one coherent product
- local inference handles an increasing fraction of useful work
- the system produces durable artifacts that compound competence
- improvements are measurable rather than vibes-based
- routing decisions become evidence-based and legible
- procedures increasingly replace rediscovered workflow
- the system becomes more capable without becoming less governable

---

## Architectural Consequence

Astrata should be built as a new codebase with selective reuse.

Reuse priority:

- concepts first
- machinery second
- glue last

In practical terms:

- reuse proven subsystems where they already match the end-state architecture
- rewrite legacy glue, route surfaces, and persistence seams freely
- prefer coherence of final architecture over preservation of historical code shape

---

## Working Motto

> Product-grade agency, harness-grade competence, one system.

---

## Working Title

**Astrata**

This is a working title for the unified successor system.
