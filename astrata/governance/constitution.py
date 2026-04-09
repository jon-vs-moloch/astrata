"""Constitution loading helpers."""

from __future__ import annotations

from pathlib import Path


def load_constitution_text(project_root: Path) -> str:
    spec_path = project_root / "spec.md"
    return spec_path.read_text() if spec_path.exists() else ""
