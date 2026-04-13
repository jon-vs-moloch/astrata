"""Observed local voice asset registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class VoiceAssetRegistry:
    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        return self._load()

    def record_install(
        self,
        *,
        asset_id: str,
        repo_id: str,
        kind: str,
        role: str,
        destination_dir: Path,
        size_bytes: int,
    ) -> dict[str, Any]:
        payload = self._load()
        assets = payload.setdefault("assets", {})
        record = dict(assets.get(asset_id) or {})
        record.update(
            {
                "asset_id": asset_id,
                "repo_id": repo_id,
                "kind": kind,
                "role": role,
                "destination_dir": str(destination_dir),
                "observed_size_bytes": int(size_bytes),
                "status": "installed",
            }
        )
        assets[asset_id] = record
        self._save(payload)
        return record

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"assets": {}}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {"assets": {}}

    def _save(self, payload: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
