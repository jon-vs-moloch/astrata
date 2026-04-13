"""Onboarding planning and state helpers."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from astrata.local.catalog import StarterCatalog
from astrata.local.hardware import probe_hardware_profile, probe_thermal_state
from astrata.local.recommendation import HardwareProfile
from astrata.providers.cli import CLI_TOOL_SPECS, CliProvider
from astrata.voice import VoiceService

from astrata.onboarding.models import OnboardingPlan, OnboardingStepRecord


class OnboardingService:
    """Maintains the durable onboarding procedure for Astrata."""

    def __init__(self, *, state_path: Path, settings=None) -> None:
        self._state_path = state_path
        self._settings = settings
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls, settings) -> "OnboardingService":
        return cls(state_path=settings.paths.data_dir / "onboarding_state.json", settings=settings)

    def ensure_plan(self) -> OnboardingPlan:
        payload = self._load()
        if payload:
            return OnboardingPlan(**payload)
        plan = self._default_plan()
        self._save(plan)
        return plan

    def status(self) -> dict:
        plan = self.ensure_plan()
        next_step = next((step for step in plan.steps if step.status in {"pending", "active", "blocked"}), None)
        payload = {
            "plan": plan.model_dump(mode="json"),
            "next_step": None if next_step is None else next_step.model_dump(mode="json"),
            "completed_steps": sum(1 for step in plan.steps if step.status == "complete"),
            "total_steps": len(plan.steps),
        }
        if self._settings is not None:
            inference_probe = self.inference_probe()
            payload["inference_probe"] = inference_probe
            payload["recommended_settings"] = self.recommended_settings_bundle(inference_probe=inference_probe)
        return payload

    def update_step(self, step_id: str, *, status: str, note: str | None = None) -> OnboardingPlan:
        plan = self.ensure_plan()
        steps: list[OnboardingStepRecord] = []
        found = False
        for step in plan.steps:
            if step.step_id != step_id:
                steps.append(step)
                continue
            found = True
            notes = list(step.notes)
            if note:
                notes.append(note)
            steps.append(step.model_copy(update={"status": status, "notes": notes}))
        if not found:
            raise KeyError(f"Unknown onboarding step `{step_id}`.")
        updated = plan.model_copy(update={"steps": steps})
        completed = all(step.status in {"complete", "skipped"} for step in steps)
        active = any(step.status in {"active", "blocked"} for step in steps)
        updated = updated.model_copy(update={"status": "complete" if completed else ("active" if active else "pending")})
        self._save(updated)
        return updated

    def _default_plan(self) -> OnboardingPlan:
        return OnboardingPlan(
            status="active",
            steps=[
                OnboardingStepRecord(
                    step_id="configure-inference",
                    title="Configure Inference",
                    description=(
                        "Connect at least one usable inference path with minimal friction. "
                        "Prefer direct sign-in and browser guidance over raw secret hunting."
                    ),
                    category="inference",
                    status="active",
                    blocking=True,
                    can_auto_advance=True,
                    metadata={
                        "goal": "usable inference from install time",
                        "aspiration": "temporary bundled inference until a durable provider is connected",
                        "preferred_sources": [
                            "codex-cli-authenticated",
                            "kilocode",
                            "gemini-cli",
                            "local-model",
                        ],
                        "auto_install_policy": "offer cli acquisition for usable lanes; recommend a local model after hardware probe; preload only lightweight local media defaults",
                    },
                ),
                OnboardingStepRecord(
                    step_id="security-policy",
                    title="Security Policy",
                    description="Establish disclosure boundaries, enclave posture, and default trust rules early.",
                    category="security",
                    blocking=True,
                ),
                OnboardingStepRecord(
                    step_id="prime-identity",
                    title="Prime Name And Personality",
                    description="Choose the Prime's true name, title posture, and personality framing.",
                    category="identity",
                    blocking=True,
                ),
                OnboardingStepRecord(
                    step_id="autonomy-sovereignty",
                    title="Autonomy And Sovereignty",
                    description="Decide what Astrata may do alone, what requires approval, and what remains outside its authority.",
                    category="autonomy",
                    blocking=True,
                ),
                OnboardingStepRecord(
                    step_id="constellation",
                    title="Constellation",
                    description="Define the initial durable-agent roster and who should exist at startup.",
                    category="constellation",
                    blocking=False,
                ),
                OnboardingStepRecord(
                    step_id="product-polish",
                    title="Install Experience Polish",
                    description="Review setup friction, auto-acquisition gaps, and first-run magic before ordinary use.",
                    category="other",
                    blocking=False,
                ),
            ],
        )

    def _load(self) -> dict:
        if not self._state_path.exists():
            return {}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, plan: OnboardingPlan) -> None:
        self._state_path.write_text(json.dumps(plan.model_dump(mode="json"), indent=2), encoding="utf-8")

    def inference_probe(self) -> dict[str, Any]:
        available_cli = CliProvider().available_tools()
        authenticated_codex = "codex-cli" in available_cli
        install_candidates = self._installable_cli_candidates(available_cli=available_cli)
        hardware = probe_hardware_profile()
        thermal = probe_thermal_state(
            preference=getattr(getattr(self._settings, "local_runtime", None), "thermal_preference", "quiet")
        )
        local_model_offer = self._recommend_installable_local_model(hardware=hardware)
        return {
            "ready_now": bool(available_cli),
            "preferred_bootstrap_route": self._preferred_bootstrap_route(available_cli),
            "available_cli_tools": available_cli,
            "codex_backdoor_available": authenticated_codex,
            "installable_cli_tools": install_candidates,
            "hardware_profile": self._hardware_payload(hardware),
            "thermal_state": {
                "preference": thermal.preference,
                "telemetry_available": thermal.telemetry_available,
                "thermal_pressure": thermal.thermal_pressure,
            },
            "local_model_offer": local_model_offer,
        }

    def recommended_settings_bundle(self, *, inference_probe: dict[str, Any] | None = None) -> dict[str, Any]:
        probe = inference_probe or self.inference_probe()
        actions: list[dict[str, Any]] = []
        preferred_route = probe.get("preferred_bootstrap_route")
        if preferred_route:
            actions.append(
                {
                    "action_id": "use-existing-inference-route",
                    "kind": "enable_existing_route",
                    "target": preferred_route,
                    "reason": self._existing_route_reason(str(preferred_route)),
                    "user_value": "Gets Astrata usable immediately without extra setup work.",
                }
            )
        for candidate in probe.get("installable_cli_tools", []):
            if not candidate.get("install_recommended"):
                continue
            actions.append(
                {
                    "action_id": f"install-{candidate['tool']}",
                    "kind": "install_cli_dependency",
                    "target": candidate["tool"],
                    "reason": candidate.get("eligibility_note"),
                    "user_value": self._cli_user_value(str(candidate["tool"])),
                }
            )
        local_model_offer = probe.get("local_model_offer")
        if isinstance(local_model_offer, dict) and local_model_offer.get("catalog_id"):
            actions.append(
                {
                    "action_id": "install-recommended-local-model",
                    "kind": "install_local_model",
                    "target": local_model_offer["catalog_id"],
                    "reason": local_model_offer.get("reason"),
                    "user_value": "Provides a private local fallback and continuity lane when cloud routes are unavailable.",
                }
            )
        voice_status = VoiceService(settings=self._settings).status() if self._settings is not None else {}
        for preload in voice_status.get("preload_defaults", []):
            policy = str(preload.get("policy") or "")
            if policy != "preload_light_default":
                continue
            actions.append(
                {
                    "action_id": f"preload-{preload['model_id']}",
                    "kind": "preload_media_default",
                    "target": preload["model_id"],
                    "reason": preload.get("reason"),
                    "user_value": self._media_preload_user_value(str(preload.get("kind") or "")),
                    "command": ["astrata", "voice-install-asset", str(preload["model_id"])],
                }
            )
        return {
            "label": "Use Recommended Settings",
            "description": (
                "Astrata should be able to configure the lowest-friction, highest-resilience setup in one click, "
                "while still explaining why each install or connection is being recommended."
            ),
            "one_click_supported": True,
            "actions": actions,
        }

    def _installable_cli_candidates(self, *, available_cli: list[str]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for tool in ("kilocode", "gemini-cli", "codex-cli"):
            if tool in available_cli:
                continue
            spec = CLI_TOOL_SPECS[tool]
            candidates.append(
                {
                    "tool": tool,
                    "exec": spec["exec"],
                    "underlying_provider": spec["underlying_provider"],
                    "install_recommended": True,
                    "eligibility_note": self._eligibility_note(tool),
                    "already_installed": bool(shutil.which(spec["exec"])),
                }
            )
        return candidates

    def _preferred_bootstrap_route(self, available_cli: list[str]) -> str | None:
        for tool in ("codex-cli", "kilocode", "gemini-cli", "claude-code"):
            if tool in available_cli:
                return tool
        return None

    def _eligibility_note(self, tool: str) -> str:
        if tool == "kilocode":
            return "Recommended universal CLI lane; Astrata should offer to install it by default."
        if tool == "codex-cli":
            return "Offer when the user has or wants an OpenAI-backed Codex path."
        if tool == "gemini-cli":
            return "Offer when the user has a Google account and wants a low-friction Gemini lane."
        return "Installable CLI-backed inference lane."

    def _existing_route_reason(self, tool: str) -> str:
        if tool == "codex-cli":
            return "Codex is already authenticated, so Astrata can come online immediately through the easiest existing route."
        if tool == "kilocode":
            return "KiloCode is already present, so Astrata can use a free low-friction CLI lane immediately."
        if tool == "gemini-cli":
            return "Gemini CLI is already available, giving Astrata a low-friction cloud lane without extra installation."
        return "A usable inference route is already available on this machine."

    def _cli_user_value(self, tool: str) -> str:
        if tool == "kilocode":
            return "Adds a cheap, broadly usable fallback lane that reduces the odds of Astrata going dark."
        if tool == "codex-cli":
            return "Lets Astrata reuse an existing Codex/OpenAI path instead of forcing separate key management."
        if tool == "gemini-cli":
            return "Adds a second cloud lane for resilience and cheap parallel work."
        return "Adds another inference route Astrata can use for continuity and routing flexibility."

    def _media_preload_user_value(self, kind: str) -> str:
        if kind == "tts":
            return "Makes Astrata immediately capable of speaking back without requiring a heavier voice stack first."
        if kind == "stt":
            return "Makes basic voice input available early, even before richer audio-capable models are configured."
        return "Preloads a lightweight local media capability that Astrata can unload later if the user does not want it."

    def _hardware_payload(self, hardware: HardwareProfile) -> dict[str, Any]:
        total_gb = round(hardware.total_memory_bytes / (1024**3), 1) if hardware.total_memory_bytes else 0.0
        return {
            "platform": hardware.platform,
            "arch": hardware.arch,
            "cpu_count": hardware.cpu_count,
            "total_memory_bytes": hardware.total_memory_bytes,
            "total_memory_gb": total_gb,
            "apple_metal_likely": hardware.apple_metal_likely,
        }

    def _recommend_installable_local_model(self, *, hardware: HardwareProfile) -> dict[str, Any] | None:
        catalog = StarterCatalog()
        installable = catalog.list_installable_models()
        if not installable:
            return None
        total_gb = hardware.total_memory_bytes / (1024**3) if hardware.total_memory_bytes else 0.0
        if total_gb >= 24:
            chosen = next((model for model in installable if model.catalog_id == "qwen3.5-0.8b-q4_k_m"), installable[0])
        else:
            chosen = next((model for model in installable if model.catalog_id == "qwen3-0.6b-q8_0"), installable[0])
        return {
            "catalog_id": chosen.catalog_id,
            "label": chosen.label,
            "variant_label": chosen.variant_label,
            "download_url": chosen.download_url,
            "reason": (
                "Best immediate local-model install candidate after hardware probe. "
                f"For a machine with about {round(total_gb, 1)} GB RAM, start with {chosen.label} {chosen.variant_label}."
            ).strip(),
        }
