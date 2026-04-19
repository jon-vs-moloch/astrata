# Progressive Disclosure Doctrine

Astrata should be maintainable by a local agent with finite context.
That means every durable surface should reveal the smallest useful representation first and reserve raw detail for explicit drill-down.

## Representation Ladder

Prefer this ladder by default:

1. stable name or id
2. one-line summary
3. one-paragraph summary
4. one-page note
5. index, outline, or section list
6. targeted section, excerpt, or aggregate
7. raw payload, trace, document, or source code

Each layer should point cleanly to the next layer down.
Raw detail remains available, but it should not be loaded, persisted inline, or injected into context unless the current task truly needs it.

## Storage Rule

Durable records should store summaries, references, hashes, indexes, bounds, and provenance before storing full content.

Large documents, traces, conversations, tool outputs, evidence blobs, and generated artifacts should usually live as external artifacts with compact record pointers.
If a raw payload must be retained, it needs an explicit retention reason, a size budget, and a summary that lets an agent decide whether to fetch it.

## Documentation Rule

Internal documentation should be short, pointed, and composable.

Prefer discrete notes, prompts, procedure docs, manifests, and indexes over large omnibus files.
Use YAML for human-authored metadata, procedure manifests, prompts, and policy/configuration notes when possible.
Use JSON for wire formats, strict API payloads, machine-generated state, or places where downstream tooling specifically requires JSON.

## Tool And Procedure Rule

Astrata's own internals should speak Astrata's language.
Audits, verifications, startup, runtime hygiene, repair, reconciliation, and release checks are Procedures.
Executable capabilities are Tools.

Tools and Procedures should expose progressive layers:

1. name
2. info
3. short description
4. detailed description
5. inputs, outputs, side effects, invariants, and permissions
6. implementation references
7. raw code

An agent should usually understand `do_thing(x)` from the name and metadata.
It should only need the implementation when changing, debugging, or verifying how `do_thing` works.

## Code Shape Rule

Avoid large monolithic files, scripts, prompts, and controllers.

Small scripts composed of other small scripts are good when each individual script is interpretable at its own level of abstraction.
Composition is not a context leak if the called unit has a clear name, contract, and summary.

The long-term goal is not merely cleaner code.
It is a runtime that can inspect, run, verify, repair, and improve its own primitives without first loading the whole world into memory.

## Strata Precedent

The parent Strata project points in the right direction: codemaps, module metadata, symbol summaries, resource summaries, and summary-first fetch paths before raw artifacts.
Astrata should carry that idea deeper into storage, documentation, Tools, Procedures, prompts, and runtime self-management.
