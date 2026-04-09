"""Loop 0 planner for deriving next implementation slices from repo state and plan docs."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from astrata.verification.basic import inspect_expected_paths, inspect_weak_expected_paths


_PATH_PATTERN = re.compile(r"`(astrata/[A-Za-z0-9_./-]+\.py)`")


class PlannerSnapshot(BaseModel):
    candidate_key: str
    expected_paths: list[str] = Field(default_factory=list)
    missing_paths: list[str] = Field(default_factory=list)
    existing_paths: list[str] = Field(default_factory=list)
    source_doc: str = ""
    reason: str = ""
    strategy: str = "normal"
    metadata: dict[str, object] = Field(default_factory=dict)


class Loop0Planner:
    def planned_paths(self, plan_text: str) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for match in _PATH_PATTERN.findall(plan_text):
            if match in seen:
                continue
            seen.add(match)
            ordered.append(match)
        return ordered

    def summarize_candidate(
        self,
        project_root: Path,
        candidate_key: str,
        expected_paths: list[str],
        *,
        source_doc: str = "",
        reason: str = "",
        strategy: str = "normal",
    ) -> PlannerSnapshot:
        inspection = inspect_expected_paths(project_root, expected_paths)
        return PlannerSnapshot(
            candidate_key=candidate_key,
            expected_paths=expected_paths,
            missing_paths=list(inspection["missing"]),
            existing_paths=list(inspection["existing"]),
            source_doc=source_doc,
            reason=reason,
            strategy=strategy,
        )

    def derive_missing_candidates(self, project_root: Path, plan_text: str) -> list[PlannerSnapshot]:
        candidates: list[PlannerSnapshot] = []
        for rel_path in self.planned_paths(plan_text):
            expected_paths = self._expected_paths_for(rel_path)
            snapshot = self.summarize_candidate(
                project_root,
                self._candidate_key(rel_path),
                expected_paths,
                source_doc="phase-0-implementation-plan.md",
                reason=f"Planned path `{rel_path}` is still missing from the repo.",
            )
            if snapshot.missing_paths:
                candidates.append(snapshot)
        return candidates

    def derive_weak_candidates(self, project_root: Path, plan_text: str) -> list[PlannerSnapshot]:
        candidates: list[PlannerSnapshot] = []
        for rel_path in self.planned_paths(plan_text):
            expected_paths = [rel_path]
            inspection = inspect_weak_expected_paths(project_root, expected_paths)
            weak_paths = dict(inspection.get("weak_paths") or {})
            if not weak_paths:
                continue
            quality = dict(weak_paths.get(rel_path) or {})
            reasons = list(quality.get("weakness_reasons") or [])
            leverage = self._leverage_profile(rel_path)
            candidate_key = f"{self._candidate_key(rel_path)}-strengthen"
            reason = (
                f"Planned path `{rel_path}` exists but is still thin enough to justify strengthening. "
                + ("Signals: " + ", ".join(reasons) + "." if reasons else "")
            ).strip()
            if leverage["summary"]:
                reason = f"{reason} Leverage: {leverage['summary']}."
            candidates.append(
                PlannerSnapshot(
                    candidate_key=candidate_key,
                    expected_paths=expected_paths,
                    missing_paths=[],
                    existing_paths=[rel_path],
                    source_doc="phase-0-implementation-plan.md",
                    reason=reason,
                    strategy="strengthen",
                    metadata={"weak_paths": weak_paths, "leverage": leverage},
                )
            )
        candidates.sort(key=self._weak_candidate_sort_key)
        return candidates

    def derive_remediation_candidates(
        self,
        *,
        project_root: Path,
        attempts: list[dict[str, object]],
        route_health: dict[str, dict[str, object]],
        available_providers: list[str],
    ) -> list[PlannerSnapshot]:
        remediation: list[PlannerSnapshot] = []
        seen: set[str] = set()

        for attempt in reversed(attempts):
            outcome = str(attempt.get("outcome") or "").strip().lower()
            if outcome not in {"failed", "blocked"}:
                continue
            provenance = dict(attempt.get("provenance") or {})
            candidate_key = str(provenance.get("candidate_key") or "").strip()
            inspection = dict(provenance.get("inspection") or {})
            missing_paths = list(inspection.get("missing") or [])
            if not candidate_key or not missing_paths or candidate_key in seen:
                continue
            current_inspection = inspect_expected_paths(project_root, missing_paths)
            current_missing = list(current_inspection.get("missing") or [])
            if not current_missing:
                continue
            seen.add(candidate_key)

            resource_usage = dict(attempt.get("resource_usage") or {})
            implementation = dict(resource_usage.get("implementation") or {})
            requested_route = dict(implementation.get("requested_route") or resource_usage.get("route") or {})
            route_key = self._route_key(requested_route)
            failed_provider = str(requested_route.get("provider") or "").strip().lower()
            route_state = dict(route_health.get(route_key) or {})
            failure_count = int(route_state.get("recent_failures") or 0)
            route_status = str(route_state.get("status") or "").strip().lower()
            if not route_status:
                if failure_count >= 3:
                    route_status = "broken"
                elif failure_count >= 2:
                    route_status = "degraded"
                else:
                    route_status = "healthy"
            failure_kind = str(attempt.get("failure_kind") or implementation.get("failure_kind") or "").strip().lower()

            strategy = "normal"
            reason_parts = [
                f"Recent attempt for `{candidate_key}` did not complete successfully.",
            ]
            if failure_kind:
                reason_parts.append(f"Failure kind: {failure_kind}.")
            alternate_provider = self._choose_alternate_provider(
                failed_provider=failed_provider,
                available_providers=available_providers,
                route_health=route_health,
            )
            if route_status in {"degraded", "broken"}:
                if alternate_provider:
                    strategy = "alternate_provider"
                    reason_parts.append(
                        f"Route `{route_key}` is currently {route_status}, so the next step should try alternate provider `{alternate_provider}`."
                    )
                else:
                    strategy = "fallback_only"
                    reason_parts.append(
                        f"Route `{route_key}` is currently {route_status}, so the next step should avoid depending on it."
                    )

            remediation.append(
                PlannerSnapshot(
                    candidate_key=candidate_key,
                    expected_paths=current_missing,
                    missing_paths=current_missing,
                    existing_paths=list(current_inspection.get("existing") or []),
                    source_doc="recent_attempts",
                    reason=" ".join(reason_parts),
                    strategy=strategy,
                )
            )
        return remediation

    def _choose_alternate_provider(
        self,
        *,
        failed_provider: str,
        available_providers: list[str],
        route_health: dict[str, dict[str, object]],
    ) -> str | None:
        ordered = [provider.strip().lower() for provider in available_providers if provider]
        for provider in ordered:
            if not provider or provider == failed_provider:
                continue
            candidate_states = self._provider_route_states(provider, route_health)
            if not candidate_states:
                return provider
            statuses = {state for state in candidate_states if state}
            if "broken" in statuses:
                continue
            return provider
        return None

    def _provider_route_states(
        self,
        provider: str,
        route_health: dict[str, dict[str, object]],
    ) -> list[str]:
        states: list[str] = []
        prefix = f"{provider}|"
        for key, payload in route_health.items():
            if not key.startswith(prefix):
                continue
            record = dict(payload or {})
            failures = int(record.get("recent_failures") or 0)
            status = str(record.get("status") or "").strip().lower()
            if not status:
                if failures >= 3:
                    status = "broken"
                elif failures >= 2:
                    status = "degraded"
                else:
                    status = "healthy"
            states.append(status)
        return states

    def _expected_paths_for(self, rel_path: str) -> list[str]:
        package_dir = Path(rel_path).parent
        package_init = str(package_dir / "__init__.py")
        expected = [rel_path]
        if package_dir.parts and package_init != "__init__.py" and self._should_require_init(package_dir):
            expected.insert(0, package_init)
        return expected

    def _should_require_init(self, package_dir: Path) -> bool:
        package = str(package_dir)
        return package in {
            "astrata/execution",
            "astrata/evals",
        }

    def _candidate_key(self, rel_path: str) -> str:
        return rel_path.removesuffix(".py").replace("/", "-").replace("_", "-")

    def _weak_candidate_sort_key(self, snapshot: PlannerSnapshot) -> tuple[int, int, str]:
        rel_path = snapshot.expected_paths[0] if snapshot.expected_paths else snapshot.candidate_key
        quality = dict(dict(snapshot.metadata).get("weak_paths") or {}).get(rel_path) or {}
        score = int(dict(quality).get("weakness_score") or 0)
        leverage = dict(dict(snapshot.metadata).get("leverage") or {})
        leverage_score = int(leverage.get("score") or 0)
        return (-leverage_score, -score, rel_path)

    def _leverage_profile(self, rel_path: str) -> dict[str, object]:
        score = 0
        reasons: list[str] = []

        if rel_path.startswith("astrata/execution/"):
            score += 100
            reasons.append("execution substrate directly affects whether Astrata can change reality")
        if rel_path.startswith("astrata/verification/"):
            score += 95
            reasons.append("verification determines whether improvements can be trusted and retained")
        if rel_path.startswith("astrata/variants/"):
            score += 85
            reasons.append("variants and promotion provide the first explicit improvement lever")
        if rel_path.startswith("astrata/procedures/"):
            score += 80
            reasons.append("procedures help improvements become cheaper and more reusable")
        if rel_path.startswith("astrata/controllers/") or rel_path.startswith("astrata/comms/"):
            score += 75
            reasons.append("control and communication make recursive work legible and governable")
        if rel_path.startswith("astrata/context/"):
            score += 55
            reasons.append("context shaping supports efficiency but is downstream of core execution")

        if rel_path == "astrata/variants/trials.py":
            score += 15
            reasons.append("bounded trials are the bridge from candidate change to retained gain")
        elif rel_path == "astrata/variants/promotion.py":
            score += 10
            reasons.append("promotion keeps successful experiments from evaporating")
        elif rel_path == "astrata/execution/runner.py":
            score += 10
            reasons.append("execution runner quality determines whether planned work can actually complete")

        summary = "; ".join(reasons[:2])
        return {"score": score, "reasons": reasons, "summary": summary}

    def _route_key(self, route: dict[str, object]) -> str:
        provider = str(route.get("provider") or "unknown").strip().lower()
        model = str(route.get("model") or "").strip().lower()
        cli_tool = str(route.get("cli_tool") or "").strip().lower()
        return "|".join([provider, model, cli_tool])
