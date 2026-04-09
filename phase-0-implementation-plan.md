# Astrata — Phase 0 Implementation Plan

## Purpose

This document translates the Astrata spec, build path, bootstrap plan, and salvage map into the first concrete implementation slice.

Phase 0 is not “build Astrata.”
Phase 0 is:

> create a new repo and wire the smallest real substrate that can wake up Loop 1

That means:

- start a fresh codebase
- seed it with the highest-leverage salvage
- stub whatever is not yet needed
- avoid preserving predecessor architecture out of sentiment

---

## Phase 0 Outcome

Phase 0 succeeds when a fresh Astrata repo can:

1. load constitution and one project spec
2. create and persist tasks, attempts, artifacts, and verification records
3. route one real task across all available inference
4. execute one bounded real external action
5. verify the result
6. record underperformance or failure
7. propose one nearby variant
8. compare the variant to baseline
9. promote or retain the better behavior

If Phase 0 can do that, the loop is awake enough to justify deeper buildout.

---

## Repo Shape

Recommended new repo skeleton:

```text
astrata/
├── pyproject.toml
├── README.md
├── docs/
│   ├── constitution.md
│   ├── project-spec.md
│   └── architecture/
│       ├── spec.md
│       ├── build-path.md
│       ├── bootstrap-plan.md
│       ├── runtime-architecture.md
│       └── salvage-map.md
├── astrata/
│   ├── __init__.py
│   ├── config/
│   ├── governance/
│   ├── records/
│   ├── storage/
│   ├── providers/
│   ├── routing/
│   ├── execution/
│   ├── verification/
│   ├── audit/
│   ├── variants/
│   ├── procedures/
│   ├── context/
│   ├── comms/
│   ├── controllers/
│   ├── artifacts/
│   ├── memory/
│   ├── knowledge/
│   ├── proactivity/
│   └── ui/
└── tests/
```

This is not the final repo shape.
It is a clean enough starting body.

---

## Phase 0 Priorities

The work should be done in this order:

1. governance loading
2. durable records and storage
3. provider fabric and routing
4. one execution path
5. verification
6. variants and promotion
7. procedures
8. minimal context management
9. minimal federated control

Communication routing, proactivity, and richer UI can remain stubs until the loop is awake.

---

## Exact Seed Set

## 1. Governance

### Port or adapt first

- [/Users/jon/Projects/strata/strata/specs/bootstrap.py](/Users/jon/Projects/strata/strata/specs/bootstrap.py)

### Build around it

- `astrata/governance/constitution.py`
- `astrata/governance/project_specs.py`
- `astrata/governance/authority.py`

### Goal

Load constitution and project spec as durable governing artifacts and expose them to runtime decisions.

---

## 2. Durable Records and Storage

### Port or adapt first

- [/Users/jon/Projects/strata/strata/storage/models.py](/Users/jon/Projects/strata/strata/storage/models.py)
- [/Users/jon/Projects/strata/strata/storage/repositories/tasks.py](/Users/jon/Projects/strata/strata/storage/repositories/tasks.py)
- [/Users/jon/Projects/strata/strata/storage/repositories/attempts.py](/Users/jon/Projects/strata/strata/storage/repositories/attempts.py)

### Mine concepts from

- [/Users/jon/Projects/astra/astra/api/app.py](/Users/jon/Projects/astra/astra/api/app.py)
- [/Users/jon/Projects/astra/astra/runtime/change_ledger.py](/Users/jon/Projects/astra/astra/runtime/change_ledger.py)
- [/Users/jon/Projects/astra/astra/comms.py](/Users/jon/Projects/astra/astra/comms.py)

### Build around it

- `astrata/records/tasks.py`
- `astrata/records/attempts.py`
- `astrata/records/artifacts.py`
- `astrata/records/communications.py`
- `astrata/records/handoffs.py`
- `astrata/records/verifications.py`
- `astrata/records/audits.py`
- `astrata/storage/models.py`
- `astrata/storage/repositories/`

### Goal

Get durable linked records online first.
Do not chase final schema elegance yet.

---

## 3. Provider Fabric

### Port early with light cleanup

- [/Users/jon/Projects/astra/astra/execution_routing.py](/Users/jon/Projects/astra/astra/execution_routing.py)
- [/Users/jon/Projects/astra/astra/inference_sources.py](/Users/jon/Projects/astra/astra/inference_sources.py)
- [/Users/jon/Projects/astra/astra/providers/base.py](/Users/jon/Projects/astra/astra/providers/base.py)
- [/Users/jon/Projects/astra/astra/providers/registry.py](/Users/jon/Projects/astra/astra/providers/registry.py)
- [/Users/jon/Projects/astra/astra/providers/cli_provider.py](/Users/jon/Projects/astra/astra/providers/cli_provider.py)
- [/Users/jon/Projects/astra/astra/providers/openai_provider.py](/Users/jon/Projects/astra/astra/providers/openai_provider.py)
- [/Users/jon/Projects/astra/astra/providers/anthropic_provider.py](/Users/jon/Projects/astra/astra/providers/anthropic_provider.py)
- [/Users/jon/Projects/astra/astra/providers/google_provider.py](/Users/jon/Projects/astra/astra/providers/google_provider.py)
- [/Users/jon/Projects/astra/astra/providers/ollama_provider.py](/Users/jon/Projects/astra/astra/providers/ollama_provider.py)
- [/Users/jon/Projects/astra/astra/providers/custom_provider.py](/Users/jon/Projects/astra/astra/providers/custom_provider.py)

### Mine concepts from

- [/Users/jon/Projects/strata/strata/models/registry.py](/Users/jon/Projects/strata/strata/models/registry.py)
- [/Users/jon/Projects/strata/strata/models/providers.py](/Users/jon/Projects/strata/strata/models/providers.py)
- [/Users/jon/Projects/strata/strata/orchestrator/worker/routing_policy.py](/Users/jon/Projects/strata/strata/orchestrator/worker/routing_policy.py)

### Build around it

- `astrata/providers/`
- `astrata/routing/router.py`
- `astrata/routing/policy.py`

### Goal

Get “all available inference” online as early as possible.
This is a strategic bootstrap asset.

### Local-runtime extension

Lightning should now be treated as the local-runtime continuation of this area, not as a parallel product.

That means the next substrate beneath `astrata/providers/` is not merely more route wrappers.
It is:

- managed local backend processes
- model discovery, registry, and activation
- runtime recommendation and profile application
- optional standalone endpoint exposure

Those capabilities should land inside Astrata under a local-runtime package, while remaining independently usable by users who only want a local endpoint.

The first extraction targets are:

- local backend adapter contracts
- managed `llama.cpp` runtime control
- local model registry and starter catalog
- runtime recommendation, selection, and health surfaces

These should land inside Astrata in a way that still permits partial adoption:

- full Astrata stack
- local-runtime-only usage
- conventional local endpoint usage
- external module replacement where interface contracts permit it

---

## 4. Real Execution

### Port early

- [/Users/jon/Projects/astra/astra/agent/executor.py](/Users/jon/Projects/astra/astra/agent/executor.py)
- [/Users/jon/Projects/astra/astra/agent/tools.py](/Users/jon/Projects/astra/astra/agent/tools.py)

### Mine concepts from

- [/Users/jon/Projects/strata/strata/api/chat_tool_executor.py](/Users/jon/Projects/strata/strata/api/chat_tool_executor.py)
- [/Users/jon/Projects/strata/strata/orchestrator/implementation.py](/Users/jon/Projects/strata/strata/orchestrator/implementation.py)
- [/Users/jon/Projects/strata/strata/orchestrator/research.py](/Users/jon/Projects/strata/strata/orchestrator/research.py)

### Build around it

- `astrata/execution/runner.py`
- `astrata/execution/tools.py`
- `astrata/execution/executor.py`

### Goal

One real task path that can touch reality and leave durable records.

---

## 5. Verification and Audit

### Port early

- [/Users/jon/Projects/strata/strata/experimental/verifier.py](/Users/jon/Projects/strata/strata/experimental/verifier.py)
- [/Users/jon/Projects/strata/strata/experimental/trace_review.py](/Users/jon/Projects/strata/strata/experimental/trace_review.py)
- [/Users/jon/Projects/strata/strata/experimental/diagnostics.py](/Users/jon/Projects/strata/strata/experimental/diagnostics.py)

### Mine concepts from

- [/Users/jon/Projects/strata/strata/orchestrator/tool_health.py](/Users/jon/Projects/strata/strata/orchestrator/tool_health.py)

### Build around it

- `astrata/verification/verifier.py`
- `astrata/audit/review.py`
- `astrata/audit/diagnostics.py`

### Goal

Make bounded experimentation trustworthy enough to start compounding.

---

## 6. Variants and Promotion

### Port early

- [/Users/jon/Projects/strata/strata/eval/benchmark.py](/Users/jon/Projects/strata/strata/eval/benchmark.py)
- [/Users/jon/Projects/strata/strata/eval/structured_eval.py](/Users/jon/Projects/strata/strata/eval/structured_eval.py)
- [/Users/jon/Projects/strata/strata/experimental/variants.py](/Users/jon/Projects/strata/strata/experimental/variants.py)
- [/Users/jon/Projects/strata/strata/experimental/promotion_policy.py](/Users/jon/Projects/strata/strata/experimental/promotion_policy.py)

### Mine concepts, then rewrite

- [/Users/jon/Projects/strata/strata/eval/job_runner.py](/Users/jon/Projects/strata/strata/eval/job_runner.py)
- [/Users/jon/Projects/strata/strata/experimental/experiment_runner.py](/Users/jon/Projects/strata/strata/experimental/experiment_runner.py)
- [/Users/jon/Projects/strata/strata/experimental/artifact_pipeline.py](/Users/jon/Projects/strata/strata/experimental/artifact_pipeline.py)

### Build around it

- `astrata/variants/models.py`
- `astrata/variants/trials.py`
- `astrata/variants/promotion.py`
- `astrata/evals/`

### Goal

Get one real improvement lever online.
Prompt or route variants are probably the best first target.

---

## 7. Procedures

### Port early

- [/Users/jon/Projects/strata/strata/procedures/registry.py](/Users/jon/Projects/strata/strata/procedures/registry.py)

### Mine concepts, then rewrite

- [/Users/jon/Projects/strata/strata/system_capabilities.py](/Users/jon/Projects/strata/strata/system_capabilities.py)
- [/Users/jon/Projects/strata/strata/orchestrator/worker/attempt_runner.py](/Users/jon/Projects/strata/strata/orchestrator/worker/attempt_runner.py)

### Build around it

- `astrata/procedures/registry.py`
- `astrata/procedures/models.py`
- `astrata/procedures/runtime.py`

### Goal

Capture successful structure as a durable cheapening asset.

---

## 8. Context Management

### Port early

- [/Users/jon/Projects/strata/strata/observability/context.py](/Users/jon/Projects/strata/strata/observability/context.py)
- [/Users/jon/Projects/strata/strata/context/loaded_files.py](/Users/jon/Projects/strata/strata/context/loaded_files.py)

### Selectively reuse from

- [/Users/jon/Projects/strata/strata/knowledge/page_payloads.py](/Users/jon/Projects/strata/strata/knowledge/page_payloads.py)

### Build around it

- `astrata/context/budget.py`
- `astrata/context/shaping.py`
- `astrata/context/telemetry.py`

### Goal

Minimal context awareness must land in Phase 0.
Otherwise local-model degradation will be invisible.

---

## 9. Minimal Federated Control

### Port early

- [/Users/jon/Projects/astra/astra/comms.py](/Users/jon/Projects/astra/astra/comms.py)
- [/Users/jon/Projects/strata/strata/communication/primitives.py](/Users/jon/Projects/strata/strata/communication/primitives.py)

### Mine concepts, then rewrite

- [/Users/jon/Projects/astra/astra/agent/scheduler.py](/Users/jon/Projects/astra/astra/agent/scheduler.py)
- [/Users/jon/Projects/strata/strata/core/lanes.py](/Users/jon/Projects/strata/strata/core/lanes.py)
- [/Users/jon/Projects/strata/strata/orchestrator/worker/resolution_policy.py](/Users/jon/Projects/strata/strata/orchestrator/worker/attempt_runner.py)

### Build around it

- `astrata/controllers/base.py`
- `astrata/controllers/coordinator.py`
- `astrata/controllers/local_executor.py`
- `astrata/comms/lanes.py`
- `astrata/comms/routing.py`

### Goal

Start with exactly two controllers:

- one upstream coordinator
- one downstream local executor/controller

That is enough to preserve refusal and handoff signal without overbuilding federation.

---

## Explicit Non-Goals For Phase 0

Do not spend early effort on:

- preserving predecessor API route shapes
- preserving predecessor app assembly
- preserving predecessor DB schema exactly
- full UI shell design
- Constellation implementation
- rich proactivity
- broad controller taxonomy

Those are downstream of waking the loop up.

---

## Earliest Build Sequence

If implementation began today, the concrete file-level order would be:

1. create new Astrata repo skeleton
2. port/adapt Strata spec bootstrap into `astrata/governance/`
3. port/adapt Strata task/attempt models into `astrata/storage/` and `astrata/records/`
4. port Astra provider fabric into `astrata/providers/` and `astrata/routing/`
5. port Astra executor/tool surfaces into `astrata/execution/`
6. port Strata verifier + trace review into `astrata/verification/` and `astrata/audit/`
7. port Strata variants + promotion policy into `astrata/variants/`
8. port Strata procedures into `astrata/procedures/`
9. port Strata context telemetry into `astrata/context/`
10. port Astra comm lanes + Strata communication primitives into `astrata/comms/`
11. stand up two-controller minimal federation
12. run the first real Loop 1 task end-to-end

---

## Working Definition Of “Done”

Phase 0 is done when Astrata can:

- ingest governing artifacts
- execute a real task through one routed path
- persist task/attempt/artifact/verifier records
- detect underperformance
- try one nearby variant
- compare outcomes
- retain the better result
- preserve a local refusal or handoff disagreement instead of flattening it away

That is enough architecture to justify the next layer.
