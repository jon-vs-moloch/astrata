"""Governance-surface protection and authorization helpers."""

from __future__ import annotations

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
