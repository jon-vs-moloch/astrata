# Handoff: Inference Lanes And Startup Diagnostics

This note captures the current state of the inference-lane refactor and the startup-diagnostic follow-up work as of 2026-04-09.

## What Landed

### 1. Shared inference abstractions

Astrata now has explicit separation between:

- endpoint type
- memory policy
- inference strategy
- backend capability

Key files:

- `/Users/jon/Projects/Astrata/astrata/inference/contracts.py`
- `/Users/jon/Projects/Astrata/astrata/inference/planner.py`
- `/Users/jon/Projects/Astrata/astrata/inference/strategies/base.py`
- `/Users/jon/Projects/Astrata/astrata/inference/strategies/single_pass.py`
- `/Users/jon/Projects/Astrata/astrata/inference/strategies/fast_then_persistent.py`

### 2. Internal lane-aware local execution

The native Strata-style local endpoint now routes through strategy objects and supports hidden fast/persistent internal lanes.

Key files:

- `/Users/jon/Projects/Astrata/astrata/local/strata_endpoint.py`
- `/Users/jon/Projects/Astrata/astrata/local/runtime/manager.py`
- `/Users/jon/Projects/Astrata/astrata/local/runtime/models.py`

Behavior:

- `fast_then_persistent` is a real executable strategy
- replies record both `strategy_id` and `runtime_key`
- the endpoint still presents one logical surface while using internal lanes

### 3. Multi-model local substrate groundwork

Astrata can now track multiple named managed runtimes under one local runtime manager. This is the substrate for hidden multi-model acceleration work like Cyclone.

Related files:

- `/Users/jon/Projects/Astrata/astrata/local/runtime/manager.py`
- `/Users/jon/Projects/Astrata/astrata/local/backends/llama_cpp.py`

### 4. Cyclone experiment harness

A first experiment harness exists for comparing `small`, `big`, and `cyclone` routes.

Key files:

- `/Users/jon/Projects/Astrata/astrata/eval/cyclone.py`
- `/Users/jon/Projects/Astrata/tests/test_cyclone.py`

### 5. Tiny local draft model support

The local model catalog/install path now supports tiny draft candidates, and Astrata discovery includes the managed install directory.

Key files:

- `/Users/jon/Projects/Astrata/astrata/local/catalog.py`
- `/Users/jon/Projects/Astrata/astrata/config/settings.py`
- `/Users/jon/Projects/Astrata/astrata/main.py`

Installed model:

- `/Users/jon/Projects/Astrata/.astrata/models/qwen3-0.6b-q8_0/Qwen3-0.6B-Q8_0.gguf`

### 6. Startup runtime health bug fixed

Startup reflection was incorrectly treating `127.0.0.1:8080` as the canonical managed local runtime endpoint when `ASTRATA_LLAMA_CPP_BASE_URL` was unset. That was wrong when the persisted managed runtime was actually on another port.

This is now fixed: startup reflection checks the persisted managed endpoint first.

Key file:

- `/Users/jon/Projects/Astrata/astrata/startup/diagnostics.py`

Related test:

- `/Users/jon/Projects/Astrata/tests/test_startup_diagnostics.py`

### 7. Startup self-diagnosis tasks are now schedulable

Previously `startup-self-diagnosis` could be created as a pending task but Loop0 would not execute it because `startup_diagnostic` was not treated as an executable message-task source.

That is now fixed.

Key file:

- `/Users/jon/Projects/Astrata/astrata/loop0/runner.py`

Related test:

- `/Users/jon/Projects/Astrata/tests/test_loop0.py`

## Important Observations

### Thermal throttling

`thermal_throttle` currently affects local-runtime start/defer decisions. It does not appear to gate Codex, CLI, Google, or other non-local routes.

Relevant file:

- `/Users/jon/Projects/Astrata/astrata/local/thermal.py`

### Port 8080 confusion

There was a stray `llama-server` on `127.0.0.1:8080` serving Gemma:

- model path:
  `/Users/jon/.lmstudio/models/lmstudio-community/gemma-4-E2B-it-GGUF/gemma-4-E2B-it-Q4_K_M.gguf`

It was not Astrata's managed runtime. Astrata's persisted managed runtime was on:

- `http://127.0.0.1:62734/health`

The stray `8080` process was killed during investigation.

### Internal local inference still uses HTTP

Astrata's internal local inference path still calls the local model through HTTP:

- `/Users/jon/Projects/Astrata/astrata/local/runtime/client.py`

That is acceptable for now, but it should not be the long-term architecture for internal-only execution.

This intent is now documented in:

- `/Users/jon/Projects/Astrata/runtime-architecture.md`

## Current Live State At Handoff

Useful checks:

- Startup reflection now reports the real managed endpoint:
  `http://127.0.0.1:62734/health`
- Current issues reported by startup reflection:
  `thermal_throttle` only
- Live Loop0 next candidate:
  `task:startup-self-diagnosis`
  strategy: `message_task`

## Recommended Next Steps

### 1. Make startup diagnosis actually repair things

Now that `startup-self-diagnosis` is executable, the next upgrade is to make it concretely actionable for runtime failures. That likely means:

- inspect the startup runtime report
- classify whether the issue is local-runtime, transport, thermal, or provider-route related
- attempt bounded repair when safe
- mark the task satisfied or complete with explicit evidence

### 2. Separate internal engine invocation from HTTP serving

The desired end state is:

- internal Astrata inference can call an engine interface directly
- HTTP remains an optional serving wrapper for external consumers and debugging

This is documented, but not implemented yet.

### 3. Continue lane work toward Cyclone

The lane substrate is ready enough to continue toward:

- model-role selection: `draft`, `verify`, `judge`
- request-scoped multi-lane sessions
- edit-aware draft state and suffix invalidation
- eventually a real `CycloneStrategy`

## Verification State

Latest passing command:

```bash
uv run --extra dev pytest -q tests/test_loop0.py tests/test_startup_diagnostics.py tests/test_storage.py tests/test_ui_service.py tests/test_inference_strategy.py tests/test_inference_planner.py tests/test_strata_endpoint.py tests/test_local_runtime.py tests/test_local_catalog.py tests/test_cyclone.py
```

Result:

- `58 passed`

## Working Guidance Captured From The Chat

- Download tools you need as long as they are safe and well-established.
- Prefer native software to dependencies, even if that means rewriting the software later.
- External tools are acceptable as bootstrapping accelerants even if they are not the terminal architecture.
