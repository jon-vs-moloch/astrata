"""Helpers for attaching connector-safe memory context to provider requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from astrata.memory.store import MemoryStore
from astrata.providers.base import CompletionRequest, Message


def default_memory_store_path(*, project_root: Path | None = None, data_dir: Path | None = None) -> Path:
    if data_dir is not None:
        return data_dir / "memory.db"
    root = project_root or Path.cwd()
    return root / ".astrata" / "memory.db"


def load_projected_memory_context(
    *,
    memory_store_path: Path | None,
    memory_query: str,
    accessor: str = "local",
    destination: str = "local",
    memory_limit: int = 5,
) -> list[str]:
    if memory_store_path is None or not memory_store_path.exists():
        return []
    store = MemoryStore(memory_store_path)
    return store.export_context(
        memory_query,
        accessor=accessor,
        destination=destination,
        limit=memory_limit,
    )


def build_memory_augmented_request(
    *,
    messages: list[Message],
    model: str | None = None,
    temperature: float | None = None,
    metadata: dict[str, Any] | None = None,
    memory_store_path: Path | None = None,
    memory_query: str = "",
    accessor: str = "local",
    destination: str = "local",
    memory_limit: int = 5,
) -> CompletionRequest:
    base_metadata = dict(metadata or {})
    snippets = load_projected_memory_context(
        memory_store_path=memory_store_path,
        memory_query=memory_query,
        accessor=accessor,
        destination=destination,
        memory_limit=memory_limit,
    )
    effective_messages = list(messages)
    if snippets:
        effective_messages.insert(
            1 if effective_messages and effective_messages[0].role == "system" else 0,
            Message(
                role="system",
                content=(
                    "Relevant memory context below is already projected for your access tier. "
                    "Treat it as potentially incomplete by design and do not infer hidden details.\n\n"
                    + "\n".join(f"- {snippet}" for snippet in snippets)
                ),
            ),
        )
    base_metadata["memory_context"] = snippets
    if memory_query:
        base_metadata["memory_context_query"] = memory_query
    return CompletionRequest(
        messages=effective_messages,
        model=model,
        temperature=temperature,
        metadata=base_metadata,
    )

