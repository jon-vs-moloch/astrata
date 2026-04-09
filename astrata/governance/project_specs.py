"""Project spec loading helpers."""

from __future__ import annotations

from pathlib import Path


def load_project_spec_text(project_root: Path, spec_name: str = "project-spec.md") -> str:
    spec_path = project_root / spec_name
    return spec_path.read_text() if spec_path.exists() else ""
