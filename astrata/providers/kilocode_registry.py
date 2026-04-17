"""Observed Kilo Code model registry helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PREFERRED_KILOCODE_MODELS = (
    "kilo/x-ai/grok-code-fast-1:optimized:free",
    "kilo/kilo-auto/free",
    "kilo/arcee-ai/trinity-large-thinking:free",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KiloCodeModelRegistry:
    def __init__(self, *, state_path: Path) -> None:
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def sync(self) -> dict[str, Any]:
        models = list_kilocode_models_from_cli()
        payload = {
            "status": "synced",
            "last_synced_at": _now_iso(),
            "models": models,
            "recommended_default_model": recommended_kilocode_model(models),
        }
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def cached(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"status": "missing", "models": [], "recommended_default_model": default_kilocode_model()}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"status": "unreadable", "models": [], "recommended_default_model": default_kilocode_model()}
        if not isinstance(payload, dict):
            return {"status": "invalid", "models": [], "recommended_default_model": default_kilocode_model()}
        payload.setdefault("models", [])
        payload.setdefault("recommended_default_model", recommended_kilocode_model(payload["models"]))
        return payload


def list_kilocode_models_from_cli() -> list[str]:
    exec_path = shutil.which("kilo")
    if not exec_path:
        raise RuntimeError("Kilo Code CLI is not installed or not on PATH.")
    proc = subprocess.run(
        [exec_path, "models"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "kilo models failed")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def recommended_kilocode_model(models: list[str] | tuple[str, ...]) -> str:
    configured = str(os.environ.get("ASTRATA_KILOCODE_MODEL") or "").strip()
    if configured:
        return configured
    available = set(models)
    for candidate in PREFERRED_KILOCODE_MODELS:
        if candidate in available:
            return candidate
    return next(iter(models), default_kilocode_model())


def default_kilocode_model() -> str:
    return str(os.environ.get("ASTRATA_KILOCODE_MODEL") or PREFERRED_KILOCODE_MODELS[0])

