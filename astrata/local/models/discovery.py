"""Local model discovery helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from astrata.local.models.registry import LocalModelRegistry


DEFAULT_SEARCH_ROOTS: tuple[str, ...] = (
    "~/Library/Application Support/LM Studio/models",
    "~/.cache/lm-studio/models",
    "~/.lmstudio/models",
    "~/.cache/lm-studio",
    "~/.ollama/models",
    "~/models",
    "~/Downloads",
)


def discover_local_models(
    registry: LocalModelRegistry,
    *,
    search_paths: tuple[str, ...],
    max_depth: int = 4,
    include_default_search_paths: bool = True,
) -> list[str]:
    discovered: list[str] = []
    for raw_path in _effective_search_paths(search_paths, include_default_search_paths=include_default_search_paths):
        root = Path(raw_path).expanduser()
        if not root.exists():
            continue
        for model_path in _walk_for_gguf(root, max_depth=max_depth):
            registry.adopt(str(model_path))
            discovered.append(str(model_path))
    return discovered


def effective_search_paths(search_paths: tuple[str, ...]) -> list[str]:
    return list(_effective_search_paths(search_paths, include_default_search_paths=True))


def _effective_search_paths(search_paths: tuple[str, ...], *, include_default_search_paths: bool) -> list[str]:
    candidates: list[str] = []
    if include_default_search_paths:
        candidates.extend(DEFAULT_SEARCH_ROOTS)
        lmstudio_home = _read_lmstudio_home_pointer()
        if lmstudio_home:
            candidates.extend(
                [
                    f"{lmstudio_home}/models",
                    f"{lmstudio_home}/hub/models",
                ]
            )
    candidates.extend(search_paths)
    seen: set[str] = set()
    normalized: list[str] = []
    for path in candidates:
        expanded = str(Path(path).expanduser())
        if expanded in seen:
            continue
        seen.add(expanded)
        normalized.append(expanded)
    return normalized


def _read_lmstudio_home_pointer() -> str | None:
    pointer = Path("~/.lmstudio-home-pointer").expanduser()
    if not pointer.exists():
        return None
    try:
        value = pointer.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not value:
        return None
    return value


def _walk_for_gguf(root: Path, *, max_depth: int) -> list[Path]:
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            entries = list(current.iterdir())
        except Exception:
            continue
        for entry in entries:
            if entry.is_dir():
                stack.append((entry, depth + 1))
                continue
            if entry.is_file() and entry.suffix.lower() == ".gguf":
                found.append(entry)
    return sorted(found)
