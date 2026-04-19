"""Small registry for chat thread metadata.

Communication records remain the source of message truth. This registry gives each
conversation a compact, inspectable meaning before an agent loads the messages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from astrata.chats.models import ChatThreadRecord, _now_iso


class ChatThreadRegistry:
    def __init__(self, *, state_path: Path) -> None:
        self._state_path = state_path
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls, settings) -> "ChatThreadRegistry":
        return cls(state_path=settings.paths.data_dir / "chat_threads.json")

    def ensure_agent_main_thread(self, *, agent_id: str, title: str | None = None) -> ChatThreadRecord:
        thread_id = f"agent:{_safe_id(agent_id)}:principal:main"
        existing = self.get(thread_id)
        if existing is not None:
            return existing
        return self.upsert(
            ChatThreadRecord(
                thread_id=thread_id,
                conversation_id=f"lane:{_safe_id(agent_id)}:default",
                title=title or f"{agent_id.title()} main chat",
                chat_kind="agent",
                agent_mode="persistent",
                agent_id=agent_id,
                memory_policy={"read_agent_memory": True, "update_agent_memory": True},
                metadata={"main_lane": True},
            )
        )

    def create_thread(
        self,
        *,
        chat_kind: str,
        title: str = "",
        agent_id: str | None = None,
        agent_mode: str | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
        endpoint_runtime_key: str | None = None,
        memory_policy: dict[str, Any] | None = None,
        permissions_profile: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ChatThreadRecord:
        normalized_kind = "model" if chat_kind == "model" else "agent"
        normalized_mode = None if normalized_kind == "model" else _agent_mode(agent_mode)
        thread_id = _thread_id(
            chat_kind=normalized_kind,
            agent_id=agent_id,
            agent_mode=normalized_mode,
            model_id=model_id,
        )
        conversation_id = _conversation_id(
            chat_kind=normalized_kind,
            thread_id=thread_id,
            agent_id=agent_id,
            agent_mode=normalized_mode,
            model_id=model_id,
        )
        default_memory = {
            "read_agent_memory": normalized_kind == "agent",
            "update_agent_memory": normalized_kind == "agent" and normalized_mode == "persistent",
            "convertible_to_permanent": normalized_mode == "ephemeral",
        }
        return self.upsert(
            ChatThreadRecord(
                thread_id=thread_id,
                conversation_id=conversation_id,
                title=title or _default_title(normalized_kind, agent_id, normalized_mode, model_id),
                chat_kind=normalized_kind,
                agent_mode=normalized_mode,
                agent_id=agent_id,
                provider_id=provider_id,
                model_id=model_id,
                endpoint_runtime_key=endpoint_runtime_key,
                memory_policy={**default_memory, **dict(memory_policy or {})},
                permissions_profile=dict(permissions_profile or {}),
                metadata=dict(metadata or {}),
            )
        )

    def get(self, thread_id: str) -> ChatThreadRecord | None:
        raw = dict(self._load().get("threads", {}).get(thread_id) or {})
        if not raw:
            return None
        return ChatThreadRecord(**raw)

    def get_by_conversation_id(self, conversation_id: str) -> ChatThreadRecord | None:
        normalized = str(conversation_id or "").strip()
        for thread in self.list_threads(include_deleted=True):
            if thread.conversation_id == normalized:
                return thread
        return None

    def list_threads(self, *, include_deleted: bool = False) -> list[ChatThreadRecord]:
        payload = self._load()
        threads = [ChatThreadRecord(**item) for item in dict(payload.get("threads") or {}).values()]
        if not include_deleted:
            threads = [thread for thread in threads if thread.status != "deleted"]
        return sorted(threads, key=lambda thread: thread.updated_at, reverse=True)

    def upsert(self, thread: ChatThreadRecord) -> ChatThreadRecord:
        payload = self._load()
        threads = dict(payload.get("threads") or {})
        threads[thread.thread_id] = thread.model_dump(mode="json")
        payload["threads"] = threads
        self._save(payload)
        return thread

    def touch(self, thread_id: str) -> ChatThreadRecord | None:
        thread = self.get(thread_id)
        if thread is None:
            return None
        return self.upsert(thread.model_copy(update={"updated_at": _now_iso()}))

    def archive(self, thread_id: str) -> ChatThreadRecord | None:
        thread = self.get(thread_id)
        if thread is None:
            return None
        now = _now_iso()
        return self.upsert(thread.model_copy(update={"status": "archived", "archived_at": now, "updated_at": now}))

    def restore(self, thread_id: str) -> ChatThreadRecord | None:
        thread = self.get(thread_id)
        if thread is None:
            return None
        return self.upsert(thread.model_copy(update={"status": "active", "updated_at": _now_iso()}))

    def delete(self, thread_id: str) -> ChatThreadRecord | None:
        thread = self.get(thread_id)
        if thread is None:
            return None
        now = _now_iso()
        return self.upsert(thread.model_copy(update={"status": "deleted", "deleted_at": now, "updated_at": now}))

    def convert_ephemeral_to_persistent(self, thread_id: str) -> ChatThreadRecord | None:
        thread = self.get(thread_id)
        if thread is None or thread.chat_kind != "agent":
            return thread
        memory_policy = dict(thread.memory_policy or {})
        memory_policy["update_agent_memory"] = True
        memory_policy["converted_from_ephemeral"] = thread.agent_mode == "ephemeral"
        return self.upsert(
            thread.model_copy(
                update={
                    "agent_mode": "persistent",
                    "memory_policy": memory_policy,
                    "updated_at": _now_iso(),
                }
            )
        )

    def _load(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"threads": {}}
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"threads": {}}
        return payload if isinstance(payload, dict) else {"threads": {}}

    def _save(self, payload: dict[str, Any]) -> None:
        self._state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _agent_mode(value: str | None) -> str:
    candidate = str(value or "persistent").strip().lower()
    if candidate in {"ephemeral", "temporary"}:
        return candidate
    return "persistent"


def _safe_id(value: str | None) -> str:
    raw = str(value or "chat").strip().lower()
    cleaned = "".join(char if char.isalnum() else "-" for char in raw).strip("-")
    return cleaned or "chat"


def _thread_id(*, chat_kind: str, agent_id: str | None, agent_mode: str | None, model_id: str | None) -> str:
    unique = uuid4().hex[:12]
    if chat_kind == "model":
        return f"model:{_safe_id(model_id)}:{unique}"
    return f"agent:{_safe_id(agent_id)}:{_safe_id(agent_mode)}:{unique}"


def _conversation_id(
    *,
    chat_kind: str,
    thread_id: str,
    agent_id: str | None,
    agent_mode: str | None,
    model_id: str | None,
) -> str:
    if chat_kind == "model":
        return f"model:{_safe_id(model_id)}:{thread_id.rsplit(':', 1)[-1]}"
    if agent_mode == "persistent":
        return f"lane:{_safe_id(agent_id)}:{thread_id.rsplit(':', 1)[-1]}"
    return thread_id


def _default_title(chat_kind: str, agent_id: str | None, agent_mode: str | None, model_id: str | None) -> str:
    if chat_kind == "model":
        return f"Model chat: {model_id or 'selected model'}"
    if agent_mode == "temporary":
        return "Temporary agent chat"
    if agent_mode == "ephemeral":
        return f"Ephemeral chat with {(agent_id or 'agent').title()}"
    return f"Thread with {(agent_id or 'agent').title()}"
