"""Load the core governing documents for Loop 0."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class GovernanceDocument(BaseModel):
    path: str
    exists: bool
    content: str = ""


class GovernanceBundle(BaseModel):
    constitution: GovernanceDocument
    project_spec: GovernanceDocument | None = None
    planning_docs: dict[str, GovernanceDocument] = Field(default_factory=dict)


def _read_doc(path: Path) -> GovernanceDocument:
    if not path.exists():
        return GovernanceDocument(path=str(path), exists=False, content="")
    return GovernanceDocument(path=str(path), exists=True, content=path.read_text())


def load_governance_bundle(project_root: Path) -> GovernanceBundle:
    docs = {
        "spec": _read_doc(project_root / "spec.md"),
        "build_path": _read_doc(project_root / "build-path.md"),
        "bootstrap_plan": _read_doc(project_root / "bootstrap-plan.md"),
        "runtime_architecture": _read_doc(project_root / "runtime-architecture.md"),
        "phase_0_plan": _read_doc(project_root / "phase-0-implementation-plan.md"),
        "mvp_loop": _read_doc(project_root / "mvp-loop.md"),
    }
    return GovernanceBundle(
        constitution=_read_doc(project_root / "spec.md"),
        project_spec=None,
        planning_docs=docs,
    )

