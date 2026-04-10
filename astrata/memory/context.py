"""Helpers for attaching disclosure-safe memory context to completion requests."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from astrata.memory.store import MemoryStore
from astrata.providers.base import CompletionRequest, Message


def default_memory_store_path(*, project_root: Path | None = None, data_dir: Path | None = None) -> Path:
    if data_dir is not None:
        return data_dir / "memory.db"
    root = (project_root or Path.cwd()).resolve()
    return root / ".astrata" / "memory.db"


def build_memory_augmented_request(
    *,
    messages: list[Message],
    model: str | None = None,
    temperature: float | None = None,
    metadata: dict[str, Any] | None = None,
    memory_store_path: Path | None = None,
    memory_query: str | None = None,
    accessor: str = "local",
    destination: str = "local",
    memory_limit: int = 3,
) -> CompletionRequest:
    base_metadata = dict(metadata or {})
    snippets = load_projected_memory_context(
        memory_store_path=memory_store_path,
        memory_query=memory_query,
        accessor=accessor,
        destination=destination,
        limit=memory_limit,
    )
    if snippets:
        base_metadata["memory_context"] = list(snippets)
        base_metadata["memory_context_query"] = str(memory_query or "").strip()
        memory_message = Message(
            role="system",
            content=(
                "Relevant memory context below is already projected for your access tier. "
                "Treat it as potentially incomplete by design and do not infer hidden details.\n\n"
                + "\n".join(f"- {snippet}" for snippet in snippets)
            ),
        )
        effective_messages = [messages[0], memory_message, *messages[1:]] if messages and messages[0].role == "system" else [memory_message, *messages]
    else:
        effective_messages = list(messages)
    return CompletionRequest(
        messages=effective_messages,
        model=model,
        temperature=temperature,
        metadata=base_metadata,
    )


def load_projected_memory_context(
    *,
    memory_store_path: Path | None,
    memory_query: str | None,
    accessor: str,
    destination: str,
    limit: int = 3,
) -> list[str]:
    path = memory_store_path
    if path is None or not path.exists():
        return []
    query = str(memory_query or "").strip()
    if not query:
        return []
    store = MemoryStore(path)
    try:
        snippets = store.export_context(
            query,
            accessor=accessor,
            destination=destination,
            limit=max(1, limit),
        )
        if snippets:
            return snippets
        fallback_terms = [
            token
            for token in re.findall(r"[a-zA-Z0-9_-]+", query.lower())
            if len(token) >= 4
        ]
        deduped: list[str] = []
        seen: set[str] = set()
        for term in fallback_terms:
            for snippet in store.export_context(
                term,
                accessor=accessor,
                destination=destination,
                limit=max(1, limit),
            ):
                if snippet in seen:
                    continue
                seen.add(snippet)
                deduped.append(snippet)
                if len(deduped) >= max(1, limit):
                    return deduped
        return deduped
    except Exception:
        return []
