# Astrata — Throughline Audit & Spec Update

**April 7, 2026**

---

## What Happened

The four Astrata planning documents were audited against the key throughlines of both predecessor projects (Astra and Strata) to check whether anything important was being silently dropped.

The audit found that Strata's throughlines were very well tracked — every core commitment was either preserved or consciously evolved. Astra's core product and agency throughlines were also solid. But several outer-ring Astra commitments and one Strata subsystem were either absent or underspecified:

- **GenUI** (Astra's server-driven UI composition) — not mentioned
- **Constellation** (Astra's network vision) — not mentioned
- **Proactivity** (Astra's autonomous scheduling and monitoring) — implied but not committed to
- **Context Management** (Strata's token budget and pressure tracking) — absorbed into routing without its own home
- **Communication Routing** (both projects had models) — Astrata's ontology had the message shape but not the routing philosophy

All five were confirmed as intentional commitments and have now been written into the spec.

---

## What Changed

### spec.md

Five new sections added before Non-Goals:

- **Generative Interface** — GenUI model carried forward from Astra. Agent composes structured layouts from a finite validated component library. Spatial tree composition. Component definitions as artifacts with standard lifecycle.
- **Proactivity** — The constitution and project specs define a desired state of reality; the system should autonomously work toward it. Monitoring, anticipation, briefings, autonomous scheduling, all under the same governance rules as reactive work.
- **Context Management** — Explicit architectural responsibility, not a side effect of routing. Token budget tracking, pressure monitoring, context shaping, artifact scanning, retrieval integration. Context pressure treated as a continuous system variable.
- **Communication Routing** — Communication as a first-class routed capability. Communication decisions (should we speak? where? what kind of act?), durable lanes, session routing, message lifecycle, append-only semantics.
- **Constellation Network** — Vision preserved from Astra, explicitly deferred during bootstrap. Design constraints to avoid painting ourselves into a corner: local identity, artifact portability, trust model compatibility, extensible comms.

### runtime-architecture.md

- Top-level shape expanded from 10 → 13 architectural responsibilities
- Three new subsystem descriptions added: Context Management (11), Communication Routing (12), Proactivity & Scheduling (13)
- Runtime dataflow expanded from 10 → 13 steps

### bootstrap-plan.md

- Context management woven into Phase 2 (Real Execution) as bootstrap-critical
- Phase 9 added: Communication Routing
- Phase 10 added: Proactivity
- Implementation order updated: "Items 1–9 create the self-improvement loop. Items 10–11 make it a product."

### build-path.md

- Salvage priorities expanded with GenUI, communication systems from both projects, proactivity/scheduler, and node identity
- Added note on combining Strata's communication routing philosophy with Astra's lane infrastructure

---

## Communication Model Note

The audit included a comparison of the two predecessor communication systems, since both had mature but different approaches:

**Strata** had the design-level routing philosophy — communication decision objects, communicative act classification, session-as-routing-substrate, message lifecycle metadata, audience-aware disclosure, append-only semantics.

**Astra** had the working infrastructure — durable SQL-backed lanes with typed participants, sender/recipient actor IDs, acknowledgment tracking, pending message queries, urgency and wake policy metadata.

The successor should combine both: Strata's decision layer on top of Astra's persistence and delivery layer.

---

## Bridge Document Status

`UNIFIED_VISION.md` and `INTEGRATION.md` (in the parent Projects directory) described an earlier integration model where Strata-as-pipeline would be merged into Astra-as-product. Astrata explicitly rejects that framing in favor of a clean-sheet design with selective reuse. Those documents remain useful as historical context but are partially superseded by the Astrata documents. The current plan of record is:

- `spec.md` — what the system is
- `runtime-architecture.md` — how it is shaped
- `build-path.md` — what governs implementation decisions
- `bootstrap-plan.md` — what to build first
