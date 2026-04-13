"""Connector-safe hosted MCP projections over local Astrata state."""

from __future__ import annotations

from typing import Any

from astrata.accounts import AccountControlPlaneRegistry
from astrata.config.settings import Settings
from astrata.memory import MemoryStore
from astrata.records.models import TaskRecord
from astrata.storage.db import AstrataDatabase


class ConnectorProjectionService:
    """Builds connector-safe views for hosted relay callers."""

    def __init__(self, *, settings: Settings, db: AstrataDatabase | None = None, memory_store: MemoryStore | None = None) -> None:
        self.settings = settings
        self.db = db or AstrataDatabase(settings.paths.data_dir / "astrata.db")
        self.db.initialize()
        self.memory = memory_store or MemoryStore(settings.paths.data_dir / "memory.db")
        self.memory.initialize()

    @classmethod
    def from_settings(cls, settings: Settings) -> "ConnectorProjectionService":
        return cls(settings=settings)

    def list_capabilities(self, *, advertisement: dict[str, Any] | None = None) -> dict[str, Any]:
        advert = dict(advertisement or {})
        account_registry = AccountControlPlaneRegistry.from_settings(self.settings)
        account_email = str(advert.get("account_email") or "").strip().lower() or None
        return {
            "system": {
                "name": "Astrata",
                "kind": "local-first AI operating system and agent constellation",
                "summary": (
                    "Astrata is the user's local-first AI system. It coordinates durable agents, local tools, "
                    "memory, governed task handoffs, and connector-safe projections so remote agents can help "
                    "without bypassing local security policy."
                ),
                "interaction_model": (
                    "Remote connectors should submit requests through the exposed tools, poll get_session or "
                    "get_result for progress, and treat Prime/local controllers as governed Astrata roles rather "
                    "than assuming direct ownership of the machine."
                ),
                "security_posture": (
                    "Connector responses are disclosure-tiered. Sensitive local memory should not be requested "
                    "or exposed unless the relay profile explicitly allows it."
                ),
                "access_boundary": (
                    "Astrata keeps download/install and local-first onboarding public, while hosted bridge activation "
                    "and remote queue usage remain invite-gated until the cloud access layer is ready for billing."
                ),
            },
            "capabilities": list(advert.get("allowed_tools") or []),
            "control_posture": advert.get("control_posture"),
            "local_prime_behavior": advert.get("local_prime_behavior"),
            "max_disclosure_tier": advert.get("max_disclosure_tier"),
            "queue_depth": advert.get("queue_depth", 0),
            "access_policy": account_registry.access_policy(),
            "hosted_bridge_eligibility": account_registry.hosted_bridge_eligibility(email=account_email),
        }

    def get_task_status(self, *, task_id: str) -> dict[str, Any]:
        payload = self.db.get_record("tasks", "task_id", task_id)
        if not payload:
            return {"found": False, "task_id": task_id}
        task = TaskRecord(**payload)
        return {
            "found": True,
            "task_id": task.task_id,
            "title": task.title,
            "status": task.status,
            "priority": task.priority,
            "urgency": task.urgency,
            "summary_public": self._task_public_summary(task),
            "summary_sensitive": self._task_sensitive_summary(task),
            "parent_task_id": task.parent_task_id,
            "updated_at": task.updated_at,
        }

    def search(self, *, query: str, limit: int = 5) -> dict[str, Any]:
        task_hits = []
        normalized = str(query or "").strip().lower()
        if normalized:
            for payload in self.db.iter_records("tasks"):
                task = TaskRecord(**payload)
                haystack = " ".join([task.title, task.description]).lower()
                if normalized in haystack:
                    task_hits.append(
                        {
                            "kind": "task",
                            "id": task.task_id,
                            "title": task.title,
                            "summary": self._task_public_summary(task),
                            "status": task.status,
                        }
                    )
                if len(task_hits) >= limit:
                    break
        memory_hits = [
            {
                "kind": "memory_page",
                "id": hit.page_id,
                "slug": hit.slug,
                "title": hit.title,
                "summary": hit.summary,
                "disclosure_tier": hit.disclosure_tier,
            }
            for hit in self.memory.retrieve_views(
                query,
                accessor="remote",
                destination="remote",
                limit=limit,
            )
        ]
        return {
            "query": query,
            "task_hits": task_hits[:limit],
            "memory_hits": memory_hits[:limit],
        }

    def fetch(self, *, identifier: str) -> dict[str, Any]:
        task = self.db.get_record("tasks", "task_id", identifier)
        if task:
            return {"kind": "task", **self.get_task_status(task_id=identifier)}
        page = self.memory.get_page(identifier) or self.memory.get_page_by_slug(identifier)
        if page is not None:
            view = self.memory.project_view(page_id=page.page_id, accessor="remote", destination="remote")
            if not view.visible or view.existence_hidden:
                return {"kind": "memory_page", "found": False, "identifier": identifier}
            return {
                "kind": "memory_page",
                "found": True,
                "page_id": view.page_id,
                "slug": view.slug,
                "title": view.title,
                "summary": view.summary,
                "disclosure_tier": view.disclosure_tier,
                "body_visible": view.body_visible,
            }
        return {"found": False, "identifier": identifier}

    def handle_tool(self, *, tool_name: str, arguments: dict[str, Any] | None = None, advertisement: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        if tool_name == "list_capabilities":
            return self.list_capabilities(advertisement=advertisement)
        if tool_name == "get_task_status":
            return self.get_task_status(task_id=str(args.get("task_id") or ""))
        if tool_name == "search":
            return self.search(query=str(args.get("query") or ""), limit=int(args.get("limit") or 5))
        if tool_name == "fetch":
            return self.fetch(identifier=str(args.get("identifier") or args.get("slug") or args.get("task_id") or ""))
        raise ValueError(f"Unsupported connector projection tool: {tool_name}")

    def _task_public_summary(self, task: TaskRecord) -> str:
        return f"{task.title} is currently {task.status}."

    def _task_sensitive_summary(self, task: TaskRecord) -> str:
        return task.description
