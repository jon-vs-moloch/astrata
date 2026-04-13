# Astrata Cloudflare Distribution

This directory is the scaffold for Astrata's Cloudflare-based distribution path.

## Intended Topology

- `Cloudflare Pages`
  Serves the public download site, release notes, and human-facing update pages.
- `Cloudflare R2`
  Stores installers, update bundles, signatures, and large release artifacts.
- `Cloudflare Workers`
  Gates invite-only channels, issues signed artifact URLs, and serves updater manifests.
- `Cloudflare D1`
  Stores invite, entitlement, channel, and release metadata when Astrata Web owns distribution state.

## Planned Hostnames

- `download.astrata.ai`
  Public Pages site.
- `releases.astrata.ai`
  R2-backed artifact host.
- `api.astrata.ai`
  Worker-backed update and signed-download surface.

## Contract

The local Astrata web-presence API now exposes matching scaffold routes:

- `/api/downloads`
- `/api/distribution`
- `/api/updates/{channel}`

These routes define the current distribution shape before real signed artifacts exist.

## Channels

- `edge`
  Every successful build. Highest velocity, highest churn.
- `nightly`
  Latest promoted daily build.
- `tester`
  Curated prerelease lane for friendly testers before monetization.
- `stable`
  Public-ready release channel.

## Tester Release Command

The repo now includes a first-pass release command:

```bash
astrata-release --version 0.1.0-dev1
```

You can target a specific release lane:

```bash
astrata-release --channel nightly --version 0.1.0-nightly.20260412
astrata-release --channel edge --version 0.1.0-edge.20260412.1 --skip-build
astrata-release --channel stable --version 0.1.0
```

Current behavior:

1. builds the desktop app
2. zips the macOS app bundle
3. stages the ZIP into the Pages site payload
4. updates the selected channel manifest
5. uploads the ZIP to the `astrata-releases` R2 bucket
6. redeploys the distribution Worker
7. redeploys the `astrata-downloads` Pages site
8. mirrors the ZIP into both `downloads/Astrata-macos-app.zip` and `downloads/<channel>/macos/Astrata-macos-app.zip` inside the Pages payload

Useful flags:

- `--skip-build`
- `--skip-upload`
- `--skip-worker-deploy`
- `--skip-pages-deploy`
- `--no-commit-dirty`

This now supports Astrata's current four release lanes from one command instead of ad hoc manual steps.

## Channel Model

- `edge`
  Every successful build. Fastest cadence and highest churn.
- `nightly`
  Latest promoted daily build.
- `tester`
  Curated prerelease lane for friendly testers.
- `stable`
  General-availability release lane.

All non-stable channels are currently treated as invite-gated at the update layer, while download/install remain public.
