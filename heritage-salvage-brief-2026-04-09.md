# Heritage Salvage Brief — 2026-04-09

## Purpose

This note translates the existing salvage documents and predecessor code into a concrete implementation sequence for Astrata.

It is not a preservation plan for Astra or Strata.
It is a map for what Astrata should absorb next.

Guiding rule:

> salvage machinery that strengthens Astrata's self-improvement loop; rewrite glue that only preserves legacy shape

## Current Read

The strategic documents still align cleanly:

- [salvage-map.md](/Users/jon/Projects/Astrata/salvage-map.md) is still directionally correct
- [INTEGRATION.md](/Users/jon/Projects/INTEGRATION.md) correctly says Astra's agency model should win while Strata's pipeline should survive as a work engine
- [UNIFIED_VISION.md](/Users/jon/Projects/UNIFIED_VISION.md) correctly frames the stack as:
  - Astra downstream of agency
  - Strata upstream of agency
  - Astrata as the synthesis

The code inspection suggests that Astrata is already leaning the right way:

- Astra-derived routing breadth is already substantially present
- Astra-derived durable lane instincts are already present
- procedure variants and model-scoped worker delegation are now present
- decomposition is now starting to be expressed in Astrata-native task and procedure terms

The biggest missing pieces are no longer "basic delegation exists" or "multiple providers exist."
They are:

1. richer durable work state
2. resolution policy for when work should branch, decompose, escalate, or block
3. explicit worker-task orchestration over dependency graphs
4. preservation and promotion of successful structures into trusted machinery

## Most Valuable Heritage Reads

### Astra

Most valuable now:

- [astra/comms.py](/Users/jon/Projects/astra/astra/comms.py)
- [execution_routing.py](/Users/jon/Projects/astra/astra/execution_routing.py)
- [inference_sources.py](/Users/jon/Projects/astra/astra/inference_sources.py)
- Astra scheduler / daemon instincts referenced in [salvage-map.md](/Users/jon/Projects/Astrata/salvage-map.md)

Why:

- Astra is still the best source for durable communication lanes and practical multi-provider breadth
- Astra's system shape assumes concurrent workers and real runtime orchestration
- Astrata should continue inheriting "all available inference is usable" from Astra

### Strata

Most valuable now:

- [storage/models.py](/Users/jon/Projects/strata/strata/storage/models.py)
- [storage/repositories/tasks.py](/Users/jon/Projects/strata/strata/storage/repositories/tasks.py)
- [resolution_policy.py](/Users/jon/Projects/strata/strata/orchestrator/worker/resolution_policy.py)
- [decomposition.py](/Users/jon/Projects/strata/strata/orchestrator/decomposition.py)
- [procedures/registry.py](/Users/jon/Projects/strata/strata/procedures/registry.py)
- [test_background_worker_lanes.py](/Users/jon/Projects/strata/tests/test_background_worker_lanes.py)

Why:

- Strata is much stronger on work ontology and structural failure handling
- Strata knows how to say "this should decompose" or "this should block" rather than just "this failed"
- Strata already treats procedures and decomposition as durable substrates instead of branch-local helpers

## What To Salvage Next

### 1. Strata's Richer Task State Machine

Primary references:

- [storage/models.py](/Users/jon/Projects/strata/strata/storage/models.py)
- [storage/repositories/tasks.py](/Users/jon/Projects/strata/strata/storage/repositories/tasks.py)

What to take:

- explicit parent/child task graph
- durable dependency edges
- durable active child tracking
- task type / work kind distinctions
- "pushed" or equivalent child-in-progress state
- explicit provenance normalization

What not to copy directly:

- SQLAlchemy schema shape
- Strata's exact enum vocabulary
- background-worker-specific assumptions

Astrata translation:

- keep Astrata's simpler record layer for now
- expand it to include parent-child lineage, active children, dependency readiness, and richer work kinds
- treat worker tasks as first-class durable records, not just queue messages

### 2. Strata's Resolution Policy

Primary reference:

- [resolution_policy.py](/Users/jon/Projects/strata/strata/orchestrator/worker/resolution_policy.py)

What to take:

- deterministic recognition of multistage tasks
- structural distinction between:
  - retry
  - decompose
  - internal replan
  - tooling/process repair
  - blocked / needs operator input
- failure fingerprinting and repeated-failure handling

What not to copy directly:

- task-type coupling to Strata's background loop
- user-question machinery as-is

Astrata translation:

- add a controller-usable resolution policy that operates on `TaskRecord`, `AttemptRecord`, and worker outputs
- make "decompose" and "block" explicit outcomes rather than ad hoc follow-up behaviors
- use this policy as the bridge between failed work and durable self-improvement

### 3. Strata's Decomposition DAG Discipline

Primary references:

- [decomposition.py](/Users/jon/Projects/strata/strata/orchestrator/decomposition.py)
- [test_background_worker_lanes.py](/Users/jon/Projects/strata/tests/test_background_worker_lanes.py)

What to take:

- decomposition into oneshottable leaf tasks
- dependency-aware DAGs instead of fake serial or fake parallel plans
- explicit preservation of decomposition phases
- draft procedure capture from successful decompositions

What not to copy directly:

- dedicated background-worker decomposition loop
- Strata's exact task class names

Astrata translation:

- continue the work already started in [runner.py](/Users/jon/Projects/Astrata/astrata/loop0/runner.py)
- move from "message follow-up DAG" to "general task decomposition procedure"
- let stronger models bypass decomposition steps when appropriate, but still require them to emit reusable structure

### 4. Astra's Lane Runtime Model

Primary reference:

- [comms.py](/Users/jon/Projects/astra/astra/comms.py)

What to take:

- stable named lanes
- participant-aware lane identity
- durable lane metadata
- lane-local messaging as system state rather than transient I/O

What not to copy directly:

- old DB shape
- Astra-specific actor identity assumptions

Astrata translation:

- keep the current Astrata lane approach
- extend it with durable worker-task state and event streams rather than only message rows
- make lane identity and worker identity inspectable enough for small models to navigate

## Recommended Next Implementation Order

### Step 1. Add First-Class Worker Task Records

Why first:

- it turns current delegation from "message queue plus reconciliation" into true multiagent state
- it is the missing substrate for parallel work

Concrete target:

- durable `worker_task` or richer `TaskRecord` metadata capturing:
  - assigned worker identity
  - route
  - capability class
  - dependency state
  - parent task
  - current phase
  - last heartbeat / event

### Step 2. Add Dependency-Aware Scheduling

Why second:

- we already preserve dependency edges
- now the scheduler should actually honor them

Concrete target:

- tasks blocked by unresolved dependencies should not be selected
- ready siblings should be eligible for parallel worker dispatch
- "children in progress" should become a real visible task state

### Step 3. Add Resolution Policy

Why third:

- once worker tasks are durable, Astrata needs a consistent way to decide when to retry, branch, decompose, block, or escalate

Concrete target:

- explicit resolution artifact or decision object
- repeated-failure recognition
- multistage-task detection
- decomposition trigger
- tooling/process-repair trigger

### Step 4. Promote Successful Decompositions

Why fourth:

- this is where throughput turns into accumulation

Concrete target:

- successful decomposition DAGs should become draft procedures
- successful direct shortcuts from stronger models should become evaluated alternate procedure variants
- promotion should be evidence-gated, not automatic

## Immediate Design Rule

When Astrata sees a task that is too large, too multistage, or too dependent on heterogeneous capability:

1. decompose into leaf work
2. assign each leaf to the cheapest route that can plausibly handle it
3. preserve dependency edges
4. preserve the decomposition as a draft reusable procedure
5. preserve any stronger-model shortcut as an alternate candidate path, not as a replacement for the careful path

That rule is compatible with:

- Astrata's self-legibility
- model-capability gating
- small-model future viability
- real parallel throughput

## Bottom Line

The salvage docs still hold up.
The strongest next inheritance is not a giant subsystem transplant.

It is a synthesis:

- Astra for provider breadth and durable lanes
- Strata for work ontology, decomposition discipline, and structural resolution

If Astrata keeps following that line, the system should converge toward:

- Prime as supervisor
- durable workers as real subagents
- decomposition and delegation as one governed mechanism
- procedures as the memory of successful work structure
- small models handling more of the total system because the structure around them keeps improving
