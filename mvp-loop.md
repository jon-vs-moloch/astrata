# Astrata — MVP Loop

## Purpose

This document defines the MVP for `Loop 0`.

It does **not** define the minimum viable product for Astrata as a user-facing system.
It defines the minimum viable self-building loop:

> the smallest real system that can help build the rest of Astrata

The standard is directional, not absolute.
Astrata does not need to be fully capable at this stage.
It needs to be moving in the right direction such that the number of required human interventions decreases over time.

---

## Loop 0 Definition

`Loop 0` exists when Astrata can autonomously advance Astrata’s own implementation far enough to carry the system toward `Loop 1`.

In plain language:

- it can read the plan
- inspect the current implementation
- identify the next missing or weak slice
- make a bounded improvement
- verify whether the improvement helped
- keep the gain
- repeat

It does not need to do this perfectly.
It needs to do it usefully enough that humans intervene less and less often.

---

## Success Criterion

The real success condition for Loop 0 is:

> Astrata increasingly reduces the amount of human steering required to continue building Astrata.

That means early Loop 0 may still require:

- occasional correction
- occasional clarification
- occasional approval
- occasional rerouting after poor choices

That is acceptable.

Loop 0 is working if:

- the system is taking meaningful implementation steps on its own
- those steps are usually net-positive
- it can often recover from local mistakes
- humans are spending less effort choosing and implementing the next slice

---

## Required Machine

The MVP machine for Loop 0 consists of eight things.

### 1. Governing Docs Reader

Astrata must be able to read:

- the constitution
- the local project spec
- `spec.md`
- `build-path.md`
- `bootstrap-plan.md`
- `phase-0-implementation-plan.md`

If it cannot read the plan, it cannot help execute the plan.

### 2. Repo State Inspector

Astrata must be able to inspect:

- what files and modules exist
- what is stubbed or missing
- what recently changed
- what likely remains to be built

This can be simple at first.

### 3. Implementation Task Selector

Astrata must be able to choose one bounded next step.

Examples:

- create a missing module
- enrich a too-thin record shape
- wire one verification path
- add one promotion path
- integrate one provider

It does not need a grand strategic planner yet.
It needs a good next-step chooser.

### 4. Broad Provider Access

Astrata must be able to use all available inference surfaces early.

This includes:

- strong cloud models
- local models
- CLI-mediated models
- multiple providers when comparison helps

Loop 0 gets much easier if Astrata can use strong inference to help build the machinery that later reduces dependence on strong inference.

### 5. Real Execution Surface

Astrata must be able to:

- edit files
- run checks
- inspect command output
- re-run after changes

Without real execution, Loop 0 is only simulated.

### 6. Durable Records

Astrata must durably record enough to learn from its own build steps.

At minimum:

- task
- attempt
- artifact
- verification result

The schema can be crude.
The signal cannot evaporate.

### 7. Verification

Astrata must be able to answer:

- did the change actually work
- is the codebase more complete or stronger than before
- did we obviously regress anything

Verification can be task-specific and narrow at first.

### 8. Promotion / Retention

Astrata must be able to keep gains.

That means:

- retain the better implementation
- preserve some lineage or reason for why it was kept
- avoid endlessly rediscovering the same improvement

---

## Minimal Federated Form

Loop 0 does not require the full mature federated-control architecture.

It does require enough structure to preserve disagreement as signal.

The minimal acceptable form is:

- one upstream coordinator
- one downstream executor/controller
- explicit handoff
- explicit refusal or blockage

That is enough to prevent the first implementation from bulldozing away local signal.

---

## What Can Be Weak At First

These can be rough in the Loop 0 MVP:

- schema elegance
- controller taxonomy
- route taxonomy
- UI
- procedure formatting
- artifact presentation
- audit sophistication

These should not be weak:

- provenance
- ability to inspect current state
- ability to make real code changes
- ability to verify whether those changes helped
- ability to retain gains

---

## What Loop 0 Should Actually Do

A good Loop 0 task is:

- small enough to verify
- real enough to matter
- close enough to the current plan to be useful

Examples of early Loop 0 tasks:

1. create a missing `records/` module from the plan
2. enrich an incomplete task or attempt record shape
3. wire one provider into the new Astrata provider fabric
4. add one verifier path for implementation tasks
5. add one variant/promotion path for prompt or route changes
6. add one handoff/refusal record path between two controllers

These are ideal because they:

- directly strengthen the self-improvement substrate
- are bounded enough to verify
- produce durable structural gains

---

## Loop 0 Verification

Loop 0 should be judged by trend, not perfection.

Useful signs that it is alive:

- it picks sane next tasks more often than not
- it completes some of them without intervention
- it usually leaves the repo in a better state
- it can notice when a change failed
- it can often make a second corrective attempt
- human interventions become more supervisory and less hands-on

Useful signs that it is not yet alive:

- it cannot choose the next slice
- it cannot verify whether a slice helped
- it makes changes but cannot retain gains
- it repeatedly needs humans to decide every next step
- it cannot use available provider breadth to improve its own build process

---

## Relationship To Loop 1

Loop 0 is not the end state.

Loop 1 is stronger:

- Astrata can improve itself broadly using all available resources on real work, not just on its own implementation path

But Loop 0 is the ignition point because:

- once Astrata can help finish building Astrata
- the remaining bootstrap work stops being purely manual construction

That is why Loop 0 matters so much.

---

## Practical Motto

> It does not need to be done.
> It needs to be building itself.
