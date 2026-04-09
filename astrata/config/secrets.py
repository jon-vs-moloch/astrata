"""Project-local provider secret storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SecretStore:
    def __init__(self, *, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get_provider_secret(self, provider: str, key: str) -> str | None:
        payload = self._load()
        provider_payload = payload.get("providers", {}).get(provider, {})
        value = provider_payload.get(key)
        text = str(value or "").strip()
        return text or None

    def set_provider_secret(self, provider: str, key: str, value: str) -> None:
        payload = self._load()
        providers = payload.setdefault("providers", {})
        provider_payload = providers.setdefault(provider, {})
        provider_payload[key] = value
        self._store(payload)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"providers": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"providers": {}}
        if not isinstance(payload, dict):
            return {"providers": {}}
        payload.setdefault("providers", {})
        return payload

    def _store(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
