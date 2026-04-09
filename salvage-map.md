# Astrata — Salvage Map

## Purpose

This document identifies what is worth salvaging from Astra and Strata for Astrata’s current architecture.

It is not a migration plan for preserving old codebases.
It is a leverage map:

- what should be reused directly
- what should be reused conceptually
- what should be rewritten cleanly
- what should be left behind

The governing rule is:

> salvage machinery that helps close the self-improvement loop; rewrite glue that only reflects legacy shape

---

## Decision Categories

Each subsystem or file family falls into one of four categories:

### 1. `Port Early`

The code is already close to Astrata’s desired architecture and directly helps close Loop 1.

### 2. `Mine Concepts, Then Rewrite`

The underlying ideas are strong, but the code is too entangled with predecessor architecture to transplant directly.

### 3. `Reference Only`

Useful as design pressure, examples, or fallback implementation hints, but not worth carrying forward directly.

### 4. `Leave Behind`

Legacy glue, obsolete product surfaces, or architecture that conflicts with Astrata’s direction.

---

## High-Level Summary

### Salvage strongest from Astra

- provider and routing breadth
- approvals and security instincts
- durable communication lanes
- GenUI concepts and component surfaces
- daemon / lifecycle / proactivity ideas
- identity and future network direction

### Salvage strongest from Strata

- task / attempt ontology
- verification and audit direction
- eval and promotion substrate
- procedures
- context-pressure management
- communication routing philosophy
- experimental / variant machinery

### Most likely to rewrite from scratch

- top-level app assembly
- API route shapes
- DB schemas as currently implemented
- UI structure as currently implemented
- monolithic runtime entrypoints

---

## Architecture-by-Architecture Salvage

## 1. Constitutional Governance

### Port early

- [/Users/jon/Projects/strata/strata/specs/bootstrap.py](/Users/jon/Projects/strata/strata/specs/bootstrap.py)

Reason:

- already close to the constitutional / project-spec bootstrap Astrata wants
- durable spec handling is directly relevant to the authority chain

### Mine concepts, then rewrite

- [/Users/jon/Projects/astra/astra/config.py](/Users/jon/Projects/astra/astra/config.py)
- [/Users/jon/Projects/strata/strata/runtime_config.py](/Users/jon/Projects/strata/strata/runtime_config.py)

Reason:

- both encode useful configuration ideas
- neither is yet shaped like Astrata’s constitutional governance layer

---

## 2. Durable Record Layer

### Port early

- [/Users/jon/Projects/strata/strata/storage/models.py](/Users/jon/Projects/strata/strata/storage/models.py)
- [/Users/jon/Projects/strata/strata/storage/repositories/tasks.py](/Users/jon/Projects/strata/strata/storage/repositories/tasks.py)
- [/Users/jon/Projects/strata/strata/storage/repositories/attempts.py](/Users/jon/Projects/strata/strata/storage/repositories/attempts.py)

Reason:

- Strata’s task/attempt durability is materially closer to Astrata’s ontology than Astra’s current task tables

### Mine concepts, then rewrite

- [/Users/jon/Projects/astra/astra/api/app.py](/Users/jon/Projects/astra/astra/api/app.py)
- [/Users/jon/Projects/astra/astra/api/routes/tasks.py](/Users/jon/Projects/astra/astra/api/routes/tasks.py)
- [/Users/jon/Projects/astra/astra/runtime/change_ledger.py](/Users/jon/Projects/astra/astra/runtime/change_ledger.py)

Reason:

- Astra has useful product-facing record surfaces and change tracking ideas
- current schema shape is too Astra-specific and too thin for Astrata’s record model

### Leave behind

- direct schema preservation from either predecessor as a hard constraint

Reason:

- Astrata’s ontology is now different enough that schema fidelity would be a burden

---

## 3. Federated Control

### Port early

- none directly

Reason:

- both predecessors contain ingredients, but neither has the actual federated-control architecture Astrata now wants

### Mine concepts, then rewrite

- [/Users/jon/Projects/astra/astra/comms.py](/Users/jon/Projects/astra/astra/comms.py)
- [/Users/jon/Projects/astra/astra/agent/scheduler.py](/Users/jon/Projects/astra/astra/agent/scheduler.py)
- [/Users/jon/Projects/strata/strata/core/lanes.py](/Users/jon/Projects/strata/strata/core/lanes.py)
- [/Users/jon/Projects/strata/strata/orchestrator/worker/resolution_policy.py](/Users/jon/Projects/strata/strata/orchestrator/worker/resolution_policy.py)
- [/Users/jon/Projects/strata/strata/orchestrator/worker/attempt_runner.py](/Users/jon/Projects/strata/strata/orchestrator/worker/attempt_runner.py)

Reason:

- Astra has the better durable lane and runtime-pair instincts
- Strata has the better refusal / resolution / local constraint instincts
- Astrata needs a new synthesis, not a direct transplant

---

## 4. Routing and Provider Fabric

### Port early

- [/Users/jon/Projects/astra/astra/execution_routing.py](/Users/jon/Projects/astra/astra/execution_routing.py)
- [/Users/jon/Projects/astra/astra/inference_sources.py](/Users/jon/Projects/astra/astra/inference_sources.py)
- [/Users/jon/Projects/astra/astra/providers/registry.py](/Users/jon/Projects/astra/astra/providers/registry.py)
- [/Users/jon/Projects/astra/astra/providers/base.py](/Users/jon/Projects/astra/astra/providers/base.py)
- [/Users/jon/Projects/astra/astra/providers/cli_provider.py](/Users/jon/Projects/astra/astra/providers/cli_provider.py)
- [/Users/jon/Projects/astra/astra/providers/openai_provider.py](/Users/jon/Projects/astra/astra/providers/openai_provider.py)
- [/Users/jon/Projects/astra/astra/providers/anthropic_provider.py](/Users/jon/Projects/astra/astra/providers/anthropic_provider.py)
- [/Users/jon/Projects/astra/astra/providers/google_provider.py](/Users/jon/Projects/astra/astra/providers/google_provider.py)
- [/Users/jon/Projects/astra/astra/providers/ollama_provider.py](/Users/jon/Projects/astra/astra/providers/ollama_provider.py)
- [/Users/jon/Projects/astra/astra/providers/custom_provider.py](/Users/jon/Projects/astra/astra/providers/custom_provider.py)

Reason:

- Astra’s “all available inference” posture is already real in code
- this is directly on the critical path for Loop 1

### Mine concepts, then rewrite

- [/Users/jon/Projects/strata/strata/models/registry.py](/Users/jon/Projects/strata/strata/models/registry.py)
- [/Users/jon/Projects/strata/strata/models/providers.py](/Users/jon/Projects/strata/strata/models/providers.py)
- [/Users/jon/Projects/strata/strata/models/adapter.py](/Users/jon/Projects/strata/strata/models/adapter.py)
- [/Users/jon/Projects/strata/strata/orchestrator/worker/routing_policy.py](/Users/jon/Projects/strata/strata/orchestrator/worker/routing_policy.py)

Reason:

- Strata’s pool, execution-context, and policy ideas are valuable
- current code is too Strata-runtime-specific to lift directly as Astrata’s router

### Reference only

- older route heuristics embedded in Astra and Strata task loops

Reason:

- good examples of what worked locally
- not yet the architecture we want

---

## 5. Real Execution and External Action

### Port early

- [/Users/jon/Projects/astra/astra/agent/executor.py](/Users/jon/Projects/astra/astra/agent/executor.py)
- [/Users/jon/Projects/astra/astra/agent/tools.py](/Users/jon/Projects/astra/astra/agent/tools.py)

Reason:

- Astrata needs real external action early
- Astra’s execution surfaces are already closer to that than Strata’s API-centric orchestration shell

### Mine concepts, then rewrite

- [/Users/jon/Projects/strata/strata/api/chat_tool_executor.py](/Users/jon/Projects/strata/strata/api/chat_tool_executor.py)
- [/Users/jon/Projects/strata/strata/api/chat_tools.py](/Users/jon/Projects/strata/strata/api/chat_tools.py)
- [/Users/jon/Projects/strata/strata/orchestrator/implementation.py](/Users/jon/Projects/strata/strata/orchestrator/implementation.py)
- [/Users/jon/Projects/strata/strata/orchestrator/research.py](/Users/jon/Projects/strata/strata/orchestrator/research.py)

Reason:

- useful decomposition and execution patterns
- code is tightly shaped around Strata’s existing API/worker assumptions

---

## 6. Verification and Audit

### Port early

- [/Users/jon/Projects/strata/strata/experimental/verifier.py](/Users/jon/Projects/strata/strata/experimental/verifier.py)
- [/Users/jon/Projects/strata/strata/experimental/trace_review.py](/Users/jon/Projects/strata/strata/experimental/trace_review.py)
- [/Users/jon/Projects/strata/strata/experimental/diagnostics.py](/Users/jon/Projects/strata/strata/experimental/diagnostics.py)

Reason:

- this is core Strata value
- directly helps wake the loop up

### Mine concepts, then rewrite

- [/Users/jon/Projects/strata/strata/orchestrator/tool_health.py](/Users/jon/Projects/strata/strata/orchestrator/tool_health.py)
- [/Users/jon/Projects/strata/strata/orchestrator/capability_incidents.py](/Users/jon/Projects/strata/strata/orchestrator/capability_incidents.py)

Reason:

- good machinery-health ideas
- should likely become more general in Astrata

---

## 7. Eval, Variants, and Promotion

### Port early

- [/Users/jon/Projects/strata/strata/eval/benchmark.py](/Users/jon/Projects/strata/strata/eval/benchmark.py)
- [/Users/jon/Projects/strata/strata/eval/structured_eval.py](/Users/jon/Projects/strata/strata/eval/structured_eval.py)
- [/Users/jon/Projects/strata/strata/eval/job_runner.py](/Users/jon/Projects/strata/strata/eval/job_runner.py)
- [/Users/jon/Projects/strata/strata/experimental/variants.py](/Users/jon/Projects/strata/strata/experimental/variants.py)
- [/Users/jon/Projects/strata/strata/experimental/experiment_runner.py](/Users/jon/Projects/strata/strata/experimental/experiment_runner.py)
- [/Users/jon/Projects/strata/strata/experimental/promotion_policy.py](/Users/jon/Projects/strata/strata/experimental/promotion_policy.py)
- [/Users/jon/Projects/strata/strata/experimental/artifact_pipeline.py](/Users/jon/Projects/strata/strata/experimental/artifact_pipeline.py)

Reason:

- this is the densest concentration of self-improvement machinery in either predecessor

### Reference only

- existing eval route/admin surfaces in [/Users/jon/Projects/strata/strata/api/eval_routes.py](/Users/jon/Projects/strata/strata/api/eval_routes.py) and [/Users/jon/Projects/strata/strata/api/experiment_admin.py](/Users/jon/Projects/strata/strata/api/experiment_admin.py)

Reason:

- useful for understanding operator needs
- not worth preserving as route shapes

---

## 8. Procedures

### Port early

- [/Users/jon/Projects/strata/strata/procedures/registry.py](/Users/jon/Projects/strata/strata/procedures/registry.py)
- [/Users/jon/Projects/strata/strata/system_capabilities.py](/Users/jon/Projects/strata/strata/system_capabilities.py)

Reason:

- procedures are one of Strata’s strongest direct contributions to Astrata

### Mine concepts, then rewrite

- task/procedure coupling patterns in [/Users/jon/Projects/strata/strata/orchestrator/worker/attempt_runner.py](/Users/jon/Projects/strata/strata/orchestrator/worker/attempt_runner.py)

Reason:

- useful evidence of how procedures interact with live work
- likely too coupled to current worker design

---

## 9. Context Management

### Port early

- [/Users/jon/Projects/strata/strata/observability/context.py](/Users/jon/Projects/strata/strata/observability/context.py)
- [/Users/jon/Projects/strata/strata/context/loaded_files.py](/Users/jon/Projects/strata/strata/context/loaded_files.py)

Reason:

- directly aligned with Astrata’s explicit context-management responsibility

### Mine concepts, then rewrite

- context shaping embedded inside Astra and Strata chat/task assembly code

Reason:

- useful examples, not the right architectural home

---

## 10. Communication Routing

### Port early

- [/Users/jon/Projects/astra/astra/comms.py](/Users/jon/Projects/astra/astra/comms.py)

Reason:

- Astra’s durable lane infrastructure is already real and useful

### Port early

- [/Users/jon/Projects/strata/strata/communication/primitives.py](/Users/jon/Projects/strata/strata/communication/primitives.py)

Reason:

- Strata’s communication decision layer is exactly the missing complement to Astra’s lanes

### Mine concepts, then rewrite

- [/Users/jon/Projects/strata/strata/messages/metadata.py](/Users/jon/Projects/strata/strata/messages/metadata.py)
- [/Users/jon/Projects/strata/strata/sessions/metadata.py](/Users/jon/Projects/strata/strata/sessions/metadata.py)

Reason:

- useful routing/session concepts
- likely to be folded into a new Astrata communication model

---

## 11. Proactivity and Scheduling

### Port early

- [/Users/jon/Projects/astra/astra/agent/scheduler.py](/Users/jon/Projects/astra/astra/agent/scheduler.py)
- [/Users/jon/Projects/astra/astra/api/routes/briefing.py](/Users/jon/Projects/astra/astra/api/routes/briefing.py)
- [/Users/jon/Projects/astra/astra/api/routes/scheduler.py](/Users/jon/Projects/astra/astra/api/routes/scheduler.py)

Reason:

- Astra already contains the product-facing proactivity instinct Astrata wants

### Mine concepts, then rewrite

- [/Users/jon/Projects/strata/strata/orchestrator/scheduler.py](/Users/jon/Projects/strata/strata/orchestrator/scheduler.py)
- [/Users/jon/Projects/strata/strata/orchestrator/worker/idle_policy.py](/Users/jon/Projects/strata/strata/orchestrator/worker/idle_policy.py)

Reason:

- useful queue-pressure and idle-work ideas
- should be re-expressed under Astrata’s continuous-variable control model

---

## 12. Memory and Knowledge

### Port early

- [/Users/jon/Projects/strata/strata/knowledge/pages.py](/Users/jon/Projects/strata/strata/knowledge/pages.py)
- [/Users/jon/Projects/strata/strata/knowledge/page_payloads.py](/Users/jon/Projects/strata/strata/knowledge/page_payloads.py)
- [/Users/jon/Projects/strata/strata/knowledge/page_access.py](/Users/jon/Projects/strata/strata/knowledge/page_access.py)

Reason:

- Astrata explicitly wants synthesized durable knowledge artifacts

### Port early

- [/Users/jon/Projects/astra/astra/memory/store.py](/Users/jon/Projects/astra/astra/memory/store.py)
- [/Users/jon/Projects/astra/astra/memory/retrieval.py](/Users/jon/Projects/astra/astra/memory/retrieval.py)
- [/Users/jon/Projects/astra/astra/memory/graph.py](/Users/jon/Projects/astra/astra/memory/graph.py)

Reason:

- Astra’s retrieval-oriented memory substrate aligns with Astrata’s memory layer

### Mine concepts, then rewrite

- [/Users/jon/Projects/strata/strata/memory/semantic.py](/Users/jon/Projects/strata/strata/memory/semantic.py)

Reason:

- potentially useful, but Astrata’s memory/knowledge split is now more explicit than either predecessor

---

## 13. Generative Interface

### Port early

- [/Users/jon/Projects/astra/astra/genui/components.py](/Users/jon/Projects/astra/astra/genui/components.py)
- [/Users/jon/Projects/astra/astra/genui/registry.py](/Users/jon/Projects/astra/astra/genui/registry.py)
- [/Users/jon/Projects/astra/astra/genui/renderer.py](/Users/jon/Projects/astra/astra/genui/renderer.py)
- [/Users/jon/Projects/astra/astra/genui/composition.py](/Users/jon/Projects/astra/astra/genui/composition.py)
- [/Users/jon/Projects/astra/astra/genui/spatial.py](/Users/jon/Projects/astra/astra/genui/spatial.py)
- [/Users/jon/Projects/astra/astra/genui/hierarchy_compiler.py](/Users/jon/Projects/astra/astra/genui/hierarchy_compiler.py)
- [/Users/jon/Projects/astra/astra/genui/spatial_adapter.py](/Users/jon/Projects/astra/astra/genui/spatial_adapter.py)

Reason:

- this is almost entirely Astra-origin territory and already conceptually aligned

### Reference only

- current UI route and HTML shell in [/Users/jon/Projects/astra/astra/api/ui/index.html](/Users/jon/Projects/astra/astra/api/ui/index.html)

Reason:

- useful for UX pressure
- not a long-term architectural center

---

## 14. Identity and Constellation

### Port early

- [/Users/jon/Projects/astra/astra/identity.py](/Users/jon/Projects/astra/astra/identity.py)

Reason:

- Astrata still wants durable identity and future network compatibility

### Mine concepts, then rewrite

- [/Users/jon/Projects/astra/astra/constellation/__init__.py](/Users/jon/Projects/astra/astra/constellation/__init__.py)

Reason:

- current code is mostly a stub
- the identity and future-network constraints are worth preserving, not the current implementation

---

## 15. Top-Level App Assembly

### Leave behind

- [/Users/jon/Projects/astra/astra/api/app.py](/Users/jon/Projects/astra/astra/api/app.py) as app assembly
- [/Users/jon/Projects/strata/strata/api/main.py](/Users/jon/Projects/strata/strata/api/main.py)
- most predecessor API route shapes in both repos

Reason:

- these reflect predecessor product boundaries and lifecycle assumptions
- Astrata’s runtime responsibilities are now different enough that carrying these forward directly would create legacy drag

---

## 16. UI Shells

### Reference only

- Astra’s current local UI shell
- Strata’s dashboard and Tauri shell

Reason:

- useful product pressure and operator-surface examples
- not where Astrata should inherit architecture

### Leave behind

- preserving either predecessor frontend as the canonical final shell

Reason:

- Astrata should inherit concepts, not be forced into old presentation structure

---

## Earliest Recommended Ports

If implementation started immediately, the most leverage-rich early salvage set would be:

1. Astra provider fabric
2. Strata task/attempt storage model
3. Strata verifier / audit / variants / promotion substrate
4. Strata procedures
5. Strata context-pressure tooling
6. Astra comm lanes
7. Strata communication decision layer
8. Astra executor/tool surfaces
9. Astra scheduler/proactivity machinery
10. Astra GenUI substrate

This set is not the whole product.
It is the highest-leverage starting mine.

---

## Final Rule

When in doubt:

- salvage what strengthens the self-improvement loop
- rewrite what only preserves historical shape

The point is not to preserve Astra and Strata.
The point is to use them to build Astrata.
