# Astrata

Fresh successor codebase for the Astrata architecture.

Current focus:

- wake up Loop 0
- preserve durable signal
- enable bounded self-improving implementation work
- keep subsystems interoperable and independently usable
- put a touchable local product shell on top of the live runtime

Product direction:

- Astrata should operate as one coherent system when the full stack is used.
- Users should still be able to adopt as much or as little of the stack as they want.
- Local inference, memory, communication/constellation, and other major modules should remain composable rather than mandatory.
- Astrata should stay friendly to external replacements for any major module where the user prefers an alternative.

The planning docs in this directory are the current plan of record.

Current release path:

- `docs/ROAD_TO_V0.md`

Current local UI shell:

- `astrata-ui` starts a Fast local operator surface on `http://127.0.0.1:8891`
- it exposes queue state, recent attempts, inboxes, artifacts, and local runtime controls
- it is the first web shell around the same local Astrata runtime rather than a separate product

Desktop direction:

- Astrata now has a native desktop shell scaffold built around the same local web/runtime surface
- the CLI installer path is `astrata-install`
- the desktop wrapper and CLI bootstrap share one substrate rather than diverging into separate install stories

Near-term onboarding direction:

- Astrata should eventually ship with a bounded bootstrap inference lane so the app can help connect the user’s real inference sources during setup.
- Astrata should also support an optional vetted starter local-model path, so one small useful model can come online early when that materially improves self-setup and diagnosis.
- Both of these are bootstrap aids, not permanent hidden dependencies.
