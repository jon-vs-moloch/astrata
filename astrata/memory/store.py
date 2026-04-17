"""SQLite-backed encyclopedia-style memory store."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import re
from typing import Any, Literal

from astrata.memory.models import (
    MemoryEmbeddingRecord,
    MemoryLinkRecord,
    MemoryAccessDecision,
    MemoryPageRecord,
    MemoryProjectedHit,
    MemoryPageView,
    MemoryRevisionRecord,
    MemorySearchHit,
)
from astrata.memory.policy import assess_memory_access, project_memory_page_view


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_pages (
                    page_id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_revisions (
                    revision_id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_links (
                    link_id TEXT PRIMARY KEY,
                    source_page_id TEXT NOT NULL,
                    target_page_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    embedding_id TEXT PRIMARY KEY,
                    subject_kind TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def upsert_page(self, page: MemoryPageRecord) -> MemoryPageRecord:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_pages (page_id, slug, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(page_id) DO UPDATE SET
                    slug = excluded.slug,
                    payload_json = excluded.payload_json
                """,
                (page.page_id, page.slug, json.dumps(page.model_dump(mode="json"))),
            )
        return page

    def append_revision(self, revision: MemoryRevisionRecord) -> MemoryRevisionRecord:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_revisions (revision_id, page_id, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(revision_id) DO UPDATE SET
                    page_id = excluded.page_id,
                    payload_json = excluded.payload_json
                """,
                (revision.revision_id, revision.page_id, json.dumps(revision.model_dump(mode="json"))),
            )
        return revision

    def upsert_link(self, link: MemoryLinkRecord) -> MemoryLinkRecord:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_links (link_id, source_page_id, target_page_id, relation, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(link_id) DO UPDATE SET
                    source_page_id = excluded.source_page_id,
                    target_page_id = excluded.target_page_id,
                    relation = excluded.relation,
                    payload_json = excluded.payload_json
                """,
                (
                    link.link_id,
                    link.source_page_id,
                    link.target_page_id,
                    link.relation,
                    json.dumps(link.model_dump(mode="json")),
                ),
            )
        return link

    def upsert_embedding(self, embedding: MemoryEmbeddingRecord) -> MemoryEmbeddingRecord:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_embeddings (embedding_id, subject_kind, subject_id, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(embedding_id) DO UPDATE SET
                    subject_kind = excluded.subject_kind,
                    subject_id = excluded.subject_id,
                    payload_json = excluded.payload_json
                """,
                (
                    embedding.embedding_id,
                    embedding.subject_kind,
                    embedding.subject_id,
                    json.dumps(embedding.model_dump(mode="json")),
                ),
            )
        return embedding

    def create_or_update_page(
        self,
        *,
        slug: str,
        title: str,
        body: str,
        summary: str = "",
        summary_public: str = "",
        summary_sensitive: str = "",
        summary_enclave: str = "",
        author: str = "system",
        entity_kind: str = "concept",
        tags: list[str] | None = None,
        read_scopes: list[str] | None = None,
        write_scopes: list[str] | None = None,
        visibility: str = "local",
        confidentiality: str = "normal",
        encryption_status: str = "planned",
        status: str = "active",
        change_summary: str = "",
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[MemoryPageRecord, MemoryRevisionRecord]:
        existing = self.get_page_by_slug(slug)
        page = (
            MemoryPageRecord(
                slug=slug,
                title=title,
                body=body,
                summary=summary,
                summary_public=summary_public,
                summary_sensitive=summary_sensitive,
                summary_enclave=summary_enclave,
                entity_kind=entity_kind,
                tags=list(tags or []),
                read_scopes=list(read_scopes or ["local"]),
                write_scopes=list(write_scopes or ["prime", "local"]),
                visibility=self._normalize_visibility(visibility),
                confidentiality=self._normalize_confidentiality(confidentiality),
                encryption_status=self._normalize_encryption_status(encryption_status),
                status=self._normalize_status(status),
                provenance=dict(provenance or {}),
                metadata=dict(metadata or {}),
            )
            if existing is None
            else existing.model_copy(
                update={
                    "title": title,
                    "body": body,
                    "summary": summary,
                    "summary_public": summary_public or existing.summary_public,
                    "summary_sensitive": summary_sensitive or existing.summary_sensitive,
                    "summary_enclave": summary_enclave or existing.summary_enclave,
                    "entity_kind": entity_kind,
                    "tags": list(tags or existing.tags),
                    "read_scopes": list(read_scopes or existing.read_scopes),
                    "write_scopes": list(write_scopes or existing.write_scopes),
                    "visibility": self._normalize_visibility(visibility),
                    "confidentiality": self._normalize_confidentiality(confidentiality),
                    "encryption_status": self._normalize_encryption_status(encryption_status),
                    "status": self._normalize_status(status),
                    "provenance": dict(provenance or existing.provenance),
                    "metadata": dict(metadata or existing.metadata),
                }
            )
        )
        revision = MemoryRevisionRecord(
            page_id=page.page_id,
            parent_revision_id=page.current_revision_id,
            author=author,
            title=title,
            body=body,
            summary=summary,
            change_summary=change_summary,
            provenance=dict(provenance or {}),
        )
        page = page.model_copy(update={"current_revision_id": revision.revision_id})
        self.upsert_page(page)
        self.append_revision(revision)
        return page, revision

    def get_page(self, page_id: str) -> MemoryPageRecord | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM memory_pages WHERE page_id = ?",
                (page_id,),
            ).fetchone()
        return None if row is None else MemoryPageRecord(**json.loads(row["payload_json"]))

    def get_page_by_slug(self, slug: str) -> MemoryPageRecord | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM memory_pages WHERE slug = ?",
                (slug,),
            ).fetchone()
        return None if row is None else MemoryPageRecord(**json.loads(row["payload_json"]))

    def list_pages(self) -> list[MemoryPageRecord]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM memory_pages").fetchall()
        pages = [MemoryPageRecord(**json.loads(row["payload_json"])) for row in rows]
        return sorted(pages, key=lambda item: (item.title.lower(), item.slug))

    def list_revisions(self, page_id: str) -> list[MemoryRevisionRecord]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM memory_revisions WHERE page_id = ?",
                (page_id,),
            ).fetchall()
        revisions = [MemoryRevisionRecord(**json.loads(row["payload_json"])) for row in rows]
        return sorted(revisions, key=lambda item: item.created_at)

    def list_links(
        self,
        page_id: str,
        *,
        direction: Literal["outbound", "inbound", "both"] = "both",
    ) -> list[MemoryLinkRecord]:
        self.initialize()
        clauses: list[str] = []
        params: list[str] = []
        if direction in {"outbound", "both"}:
            clauses.append("source_page_id = ?")
            params.append(page_id)
        if direction in {"inbound", "both"}:
            clauses.append("target_page_id = ?")
            params.append(page_id)
        query = "SELECT payload_json FROM memory_links"
        if clauses:
            query += " WHERE " + " OR ".join(clauses)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        links = [MemoryLinkRecord(**json.loads(row["payload_json"])) for row in rows]
        return sorted(links, key=lambda item: (item.relation, item.created_at))

    def search_pages(self, query: str, *, limit: int = 10) -> list[MemorySearchHit]:
        normalized = str(query or "").strip().lower()
        if not normalized:
            return []
        hits: list[MemorySearchHit] = []
        for page in self.list_pages():
            score = 0.0
            reasons: list[str] = []
            title = page.title.lower()
            slug = page.slug.lower()
            summary = page.summary.lower()
            body = page.body.lower()
            tags = [tag.lower() for tag in page.tags]
            if normalized == slug:
                score += 12.0
                reasons.append("slug_exact")
            if normalized in title:
                score += 8.0
                reasons.append("title")
            if normalized in slug:
                score += 6.0
                reasons.append("slug")
            if normalized in summary:
                score += 4.0
                reasons.append("summary")
            if normalized in body:
                score += 2.0
                reasons.append("body")
            if any(normalized in tag for tag in tags):
                score += 3.0
                reasons.append("tag")
            if score <= 0:
                continue
            hits.append(
                MemorySearchHit(
                    page_id=page.page_id,
                    slug=page.slug,
                    title=page.title,
                    summary=page.summary,
                    score=score,
                    match_reasons=reasons,
                )
            )
        return sorted(hits, key=lambda item: (-item.score, item.title.lower()))[: max(1, limit)]

    def retrieve_views(
        self,
        query: str,
        *,
        accessor: str,
        destination: str = "local",
        limit: int = 10,
    ) -> list[MemoryProjectedHit]:
        projected: list[MemoryProjectedHit] = []
        for hit in self.search_pages(query, limit=limit * 3):
            view = self.project_view(
                page_id=hit.page_id,
                accessor=accessor,
                destination=destination,
            )
            if not view.visible or view.existence_hidden:
                continue
            projected.append(
                MemoryProjectedHit(
                    page_id=view.page_id,
                    slug=view.slug,
                    title=view.title,
                    summary=view.summary,
                    disclosure_tier=view.disclosure_tier,
                    score=hit.score,
                    match_reasons=list(hit.match_reasons),
                    body_visible=view.body_visible,
                    access_decision=view.access_decision,
                )
            )
            if len(projected) >= max(1, limit):
                break
        return projected

    def export_context(
        self,
        query: str,
        *,
        accessor: str,
        destination: str = "local",
        limit: int = 5,
    ) -> list[str]:
        snippets: list[str] = []
        hits = self.retrieve_views(
            query,
            accessor=accessor,
            destination=destination,
            limit=limit,
        )
        if not hits:
            fallback_terms = [
                token
                for token in re.findall(r"[A-Za-z0-9_-]+", str(query or "").lower())
                if len(token) >= 4
            ]
            seen_page_ids: set[str | None] = set()
            for term in fallback_terms:
                for hit in self.retrieve_views(
                    term,
                    accessor=accessor,
                    destination=destination,
                    limit=limit,
                ):
                    if hit.page_id in seen_page_ids:
                        continue
                    seen_page_ids.add(hit.page_id)
                    hits.append(hit)
                    if len(hits) >= max(1, limit):
                        break
                if len(hits) >= max(1, limit):
                    break
        for hit in hits:
            title = hit.title or hit.slug or "Untitled"
            summary = (hit.summary or "").strip()
            tier = hit.disclosure_tier
            if summary:
                snippets.append(f"[{tier}] {title}: {summary}")
            else:
                snippets.append(f"[{tier}] {title}")
        return snippets

    def list_embeddings(self, *, subject_kind: str | None = None, subject_id: str | None = None) -> list[MemoryEmbeddingRecord]:
        self.initialize()
        clauses: list[str] = []
        params: list[str] = []
        if subject_kind:
            clauses.append("subject_kind = ?")
            params.append(subject_kind)
        if subject_id:
            clauses.append("subject_id = ?")
            params.append(subject_id)
        query = "SELECT payload_json FROM memory_embeddings"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [MemoryEmbeddingRecord(**json.loads(row["payload_json"])) for row in rows]

    def assess_access(
        self,
        *,
        page_id: str | None = None,
        slug: str | None = None,
        accessor: str,
        action: str = "read",
        destination: str = "local",
    ) -> MemoryAccessDecision:
        page = self.get_page(page_id) if page_id else self.get_page_by_slug(str(slug or ""))
        if page is None:
            return MemoryAccessDecision(
                allowed=False,
                reason="Memory page was not found.",
            )
        return assess_memory_access(
            page=page,
            accessor=accessor,
            action=action,
            destination=destination,
        )

    def project_view(
        self,
        *,
        page_id: str | None = None,
        slug: str | None = None,
        accessor: str,
        action: str = "read",
        destination: str = "local",
    ) -> MemoryPageView:
        page = self.get_page(page_id) if page_id else self.get_page_by_slug(str(slug or ""))
        if page is None:
            return MemoryPageView(
                visible=False,
                disclosure_tier="none",
                existence_hidden=True,
                access_decision=MemoryAccessDecision(
                    allowed=False,
                    reason="Memory page was not found.",
                ),
            )
        return project_memory_page_view(
            page=page,
            accessor=accessor,
            action=action,
            destination=destination,
        )

    def _normalize_visibility(self, value: str) -> str:
        return value if value in {"local", "restricted", "shared", "enclave"} else "local"

    def _normalize_confidentiality(self, value: str) -> str:
        return value if value in {"normal", "sensitive", "enclave"} else "normal"

    def _normalize_encryption_status(self, value: str) -> str:
        return value if value in {"none", "planned", "encrypted"} else "planned"

    def _normalize_status(self, value: str) -> str:
        return value if value in {"draft", "active", "deprecated", "broken"} else "draft"
