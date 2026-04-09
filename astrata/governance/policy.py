"""Governance-surface protection and authorization helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROTECTED_GOVERNANCE_PATHS = frozenset(
    {
        "spec.md",
        "project-spec.md",
        "build-path.md",
        "bootstrap-plan.md",
        "runtime-architecture.md",
        "phase-0-implementation-plan.md",
        "mvp-loop.md",
        "astrata/governance/constitution.py",
        "astrata/governance/project_specs.py",
    }
)


def normalize_repo_path(path: str) -> str:
    return str(path or "").strip().replace("\\", "/").lstrip("./")


def is_protected_governance_path(path: str) -> bool:
    normalized = normalize_repo_path(path)
    return normalized in PROTECTED_GOVERNANCE_PATHS


def protected_governance_paths(paths: list[str] | tuple[str, ...]) -> list[str]:
    return [normalized for path in paths if (normalized := normalize_repo_path(path)) in PROTECTED_GOVERNANCE_PATHS]


def governance_change_is_authorized(provenance: dict[str, Any] | None) -> bool:
    payload = dict(provenance or {})
    authority_chain = [str(item).strip().lower() for item in list(payload.get("authority_chain") or []) if str(item).strip()]
    if "principal" not in authority_chain:
        return False
    return bool(payload.get("governance_update_authorized"))


def governance_surface_fingerprint(project_root: Path, *, paths: list[str] | None = None) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for raw_path in paths or sorted(PROTECTED_GOVERNANCE_PATHS):
        normalized = normalize_repo_path(raw_path)
        if not normalized:
            continue
        path = project_root / normalized
        if not path.exists():
            fingerprints[normalized] = ""
            continue
        fingerprints[normalized] = hashlib.sha256(path.read_bytes()).hexdigest()
    return fingerprints


class GovernanceDriftMonitor:
    """Tracks approved governance-surface fingerprints and flags unauthorized drift."""

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def scan(self, project_root: Path) -> dict[str, Any]:
        state = self._load_state()
        current = governance_surface_fingerprint(project_root)
        approved = dict(state.get("approved") or {})
        if not approved:
            self._write_state({"approved": current, "last_reported": {}})
            return {
                "status": "initialized",
                "approved": current,
                "current": current,
                "drifted_paths": [],
                "newly_reported_paths": [],
            }
        drifted_paths = sorted(path for path, fingerprint in current.items() if approved.get(path, "") != fingerprint)
        last_reported = dict(state.get("last_reported") or {})
        newly_reported_paths = sorted(path for path in drifted_paths if last_reported.get(path, "") != current.get(path, ""))
        retained_reports = {path: last_reported[path] for path in drifted_paths if path in last_reported}
        retained_reports.update({path: current.get(path, "") for path in newly_reported_paths})
        self._write_state(
            {
                "approved": approved,
                "last_reported": retained_reports,
            }
        )
        return {
            "status": "drifted" if drifted_paths else "clean",
            "approved": approved,
            "current": current,
            "drifted_paths": drifted_paths,
            "newly_reported_paths": newly_reported_paths,
        }

    def approve_current(self, project_root: Path) -> dict[str, str]:
        current = governance_surface_fingerprint(project_root)
        self._write_state({"approved": current, "last_reported": {}})
        return current

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            payload = json.loads(self.state_path.read_text())
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_state(self, payload: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
