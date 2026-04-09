"""Scoped route health for bounded procedure execution."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _route_key(route: dict[str, Any]) -> str:
    provider = str(route.get("provider") or "unknown").strip().lower()
    model = str(route.get("model") or "").strip().lower()
    cli_tool = str(route.get("cli_tool") or "").strip().lower()
    return "|".join([provider, model, cli_tool])


class RouteHealthStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def assess(self, route: dict[str, Any]) -> dict[str, Any]:
        data = self._load()
        key = _route_key(route)
        record = dict((data.get("routes") or {}).get(key) or {})
        failures = int(record.get("recent_failures") or 0)
        status = "healthy"
        if failures >= 3:
            status = "broken"
        elif failures >= 2:
            status = "degraded"
        return {
            "route_key": key,
            "status": status,
            "recent_failures": failures,
            "last_failure_kind": record.get("last_failure_kind"),
            "last_error": record.get("last_error"),
        }

    def record_success(self, route: dict[str, Any]) -> None:
        data = self._load()
        routes = dict(data.get("routes") or {})
        key = _route_key(route)
        routes[key] = {
            "recent_failures": 0,
            "last_failure_kind": None,
            "last_error": None,
            "updated_at": _now_iso(),
        }
        data["routes"] = routes
        self._save(data)

    def record_failure(self, route: dict[str, Any], *, failure_kind: str, error: str) -> None:
        data = self._load()
        routes = dict(data.get("routes") or {})
        key = _route_key(route)
        current = dict(routes.get(key) or {})
        routes[key] = {
            "recent_failures": int(current.get("recent_failures") or 0) + 1,
            "last_failure_kind": failure_kind,
            "last_error": error,
            "updated_at": _now_iso(),
        }
        data["routes"] = routes
        self._save(data)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"routes": {}}
        try:
            payload = json.loads(self.path.read_text())
        except Exception:
            return {"routes": {}}
        if not isinstance(payload, dict):
            return {"routes": {}}
        payload.setdefault("routes", {})
        return payload

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=True))
