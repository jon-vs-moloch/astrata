# Lightning Salvage Map

Lightning is now treated as Astrata's local-runtime substrate, not as a parallel product.

## Integration rule

- absorb engine/substrate pieces into Astrata
- do not preserve Lightning as a separate product shell inside Astrata
- keep boundaries modular enough that users can still run only the local-runtime layer if they want

## Handoff-Derived Mission

Lightning's handoff doc sharpens the pickup bar.

The near-term mission is not merely "have a local runtime abstraction."
It is:

- get Astrata to the point where LM Studio is no longer required as the local endpoint
- make managed local runtime ownership a first-class product surface
- keep the API and CLI/operator story first-class, not as implementation leftovers
- support a real quiet mode on Macs whose practical meaning is "do not turn on the fans if avoidable"
- preserve explicit degraded compatibility modes instead of silently falling back into them

That means Astrata should absorb Lightning primarily as:

- runtime control plane
- model discovery and recommendation
- managed backend ownership
- thermal-aware local scheduling input
- inspectable API/CLI surface

not as a separate shell.

## First extraction order

1. adapter and runtime-control contracts
2. managed `llama.cpp` process control
3. local model registry and adoption
4. operation tracking
5. runtime recommendation and selection
6. starter catalog and install flows
7. API and UI surfaces, only where they still make sense after Astrata-native integration

## Current status against the handoff

Already landed in Astrata:

- managed `llama.cpp` backend contract and health checks
- local model discovery and recommendation
- Mac-first quiet-mode thermal preference
- sparse thermal sampling plus hysteresis/cooldown control
- operator-visible CLI status/control surfaces for the local runtime

Still missing relative to the handoff:

- explicit operation tracking for long-running local-runtime work
- starter catalog and installer flow
- degraded-compatibility semantics surfaced in request/response metadata
- thinking vs non-thinking runtime controls
- MLX backend after `llama.cpp` becomes boringly usable
- local-runtime control lane exposure through Astrata controllers/comms
- "stop needing LM Studio" as a complete day-to-day reality rather than partial substrate

## Preserve from the handoff

- Mac-first backend order:
  - `llama.cpp`
  - `MLX`
  - later `vLLM`
- local recommendation should remain local-first even if remote catalogues exist later
- helper/operator chat should eventually sit on top of the same control plane
- UI should not invent capabilities that the API/CLI cannot express
- degraded compatibility must stay explicit
- quiet mode should be a real operational constraint, not only a label

## File map

### Extract first

- [/Users/jon/Projects/lightning/src/core/adapter.ts](/Users/jon/Projects/lightning/src/core/adapter.ts)
  - backend adapter contract
- [/Users/jon/Projects/lightning/src/backends/llama_cpp_process.ts](/Users/jon/Projects/lightning/src/backends/llama_cpp_process.ts)
  - managed `llama.cpp` process lifecycle and health checks
- [/Users/jon/Projects/lightning/src/models/registry.ts](/Users/jon/Projects/lightning/src/models/registry.ts)
  - local model discovery and adoption
- [/Users/jon/Projects/lightning/src/models/operations.ts](/Users/jon/Projects/lightning/src/models/operations.ts)
  - tracked long-running control-plane work
- [/Users/jon/Projects/lightning/src/models/recommendation.ts](/Users/jon/Projects/lightning/src/models/recommendation.ts)
  - local runtime recommendation

### Extract second

- [/Users/jon/Projects/lightning/src/models/catalog.ts](/Users/jon/Projects/lightning/src/models/catalog.ts)
  - starter catalog and family defaults
- [/Users/jon/Projects/lightning/src/models/installer.ts](/Users/jon/Projects/lightning/src/models/installer.ts)
  - managed install flows
- [/Users/jon/Projects/lightning/src/models/control.ts](/Users/jon/Projects/lightning/src/models/control.ts)
  - runtime selection and restart control plane
- [/Users/jon/Projects/lightning/src/models/state.ts](/Users/jon/Projects/lightning/src/models/state.ts)
  - durable local-runtime state

### Mine concepts, then rewrite

- [/Users/jon/Projects/lightning/src/core/runtime.ts](/Users/jon/Projects/lightning/src/core/runtime.ts)
  - useful ideas, but too Lightning-shaped to port directly
- [/Users/jon/Projects/lightning/src/core/scheduler.ts](/Users/jon/Projects/lightning/src/core/scheduler.ts)
  - thermal ideas should be adapted to Astrata scheduling, not copied raw
- [/Users/jon/Projects/lightning/src/api/server.ts](/Users/jon/Projects/lightning/src/api/server.ts)
  - endpoint shape should be reconsidered after substrate extraction
- [/Users/jon/Projects/lightning/src/ui/app.ts](/Users/jon/Projects/lightning/src/ui/app.ts)
  - UI ideas only, not a source-of-truth shell

## Current Astrata landing zone

- `astrata/local/backends/`
- `astrata/local/models/`
- `astrata/local/runtime/`
- `astrata/local/recommendation.py`
- `astrata/local/hardware.py`
- `astrata/local/thermal.py`
- `astrata/local/profiles.py`
- `astrata/local/operations.py`

## Next concrete extraction

1. port starter catalog
2. port installer with operation tracking
3. add degraded-compatibility metadata and explicit runtime-mode reporting
4. expose local-runtime health, selection, and thermal posture through Astrata comms/controllers
5. add thinking vs non-thinking request controls
6. start the `MLX` pickup once `llama.cpp` ownership feels boring
