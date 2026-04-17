"""Connector-safe task and memory projections."""

from __future__ import annotations

from typing import Any

from astrata.accounts import AccountControlPlaneRegistry
from astrata.config.settings import Settings
from astrata.memory import MemoryStore
from astrata.records.models import TaskRecord
from astrata.storage.db import AstrataDatabase


class ConnectorProjectionService:
    def __init__(self, *, settings: Settings, db: AstrataDatabase, memory_store: MemoryStore | None = None) -> None:
        self.settings = settings
        self.db = db
        self.memory_store = memory_store or MemoryStore(settings.paths.data_dir / "memory.db")
        self.accounts = AccountControlPlaneRegistry.from_settings(settings)

    def list_capabilities(self, *, advertisement: dict[str, Any] | None = None) -> dict[str, Any]:
        advertised = dict(advertisement or {})
        return {
            "system": {
                "name": "Astrata",
                "summary": "Astrata is a local-first personal coordination system with governed remote operator bridges.",
            },
            "capabilities": list(advertised.get("allowed_tools") or ["search", "fetch", "get_task_status"]),
            "control_posture": advertised.get("control_posture") or "local_prime_delegate",
            "access_policy": self.accounts.access_policy(),
            "hosted_bridge_eligibility": self.accounts.hosted_bridge_eligibility(),
        }

    def get_task_status(self, *, task_id: str) -> dict[str, Any]:
        raw = self.db.get_record("tasks", "task_id", task_id)
        if raw is None:
            return {"found": False, "task_id": task_id}
        task = TaskRecord(**raw)
        return {
            "found": True,
            "task_id": task.task_id,
            "title": task.title,
            "status": task.status,
            "summary_public": f"{task.title} is currently {task.status}.",
            "updated_at": task.updated_at,
        }

    def search(self, *, query: str, limit: int = 5) -> dict[str, Any]:
        normalized = str(query or "").strip().lower()
        task_hits = []
        for raw in self.db.list_records("tasks"):
            task = TaskRecord(**raw)
            haystack = f"{task.task_id} {task.title} {task.description}".lower()
            if normalized and normalized not in haystack:
                continue
            task_hits.append(
                {
                    "id": task.task_id,
                    "kind": "task",
                    "title": task.title,
                    "status": task.status,
                    "summary": f"{task.title} is currently {task.status}.",
                }
            )
            if len(task_hits) >= limit:
                break
        memory_hits = [hit.model_dump(mode="json") for hit in self.memory_store.search_pages(query, limit=limit)]
        return {"query": query, "task_hits": task_hits, "memory_hits": memory_hits}

    def fetch(self, *, identifier: str) -> dict[str, Any]:
        raw_task = self.db.get_record("tasks", "task_id", identifier)
        if raw_task is not None:
            task = TaskRecord(**raw_task)
            return {
                "found": True,
                "kind": "task",
                "id": task.task_id,
                "title": task.title,
                "status": task.status,
                "summary": f"{task.title} is currently {task.status}.",
            }
        page = self.memory_store.get_page(identifier) or self.memory_store.get_page_by_slug(identifier)
        if page is None:
            return {"found": False, "identifier": identifier}
        return {
            "found": True,
            "kind": "memory",
            "id": page.page_id,
            "slug": page.slug,
            "title": page.title,
            "summary": page.summary_public or page.summary or page.title,
        }

