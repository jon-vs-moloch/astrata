"""Durable memory-layer models for pages, revisions, links, and embeddings."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryPageRecord(BaseModel):
    page_id: str = Field(default_factory=lambda: str(uuid4()))
    slug: str
    title: str
    body: str = ""
    summary: str = ""
    summary_public: str = ""
    summary_sensitive: str = ""
    summary_enclave: str = ""
    entity_kind: str = "concept"
    tags: list[str] = Field(default_factory=list)
    read_scopes: list[str] = Field(default_factory=lambda: ["local"])
    write_scopes: list[str] = Field(default_factory=lambda: ["prime", "local"])
    visibility: Literal["local", "restricted", "shared", "enclave"] = "local"
    confidentiality: Literal["normal", "sensitive", "enclave"] = "normal"
    encryption_status: Literal["none", "planned", "encrypted"] = "planned"
    status: Literal["draft", "active", "deprecated", "broken"] = "draft"
    current_revision_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class MemoryRevisionRecord(BaseModel):
    revision_id: str = Field(default_factory=lambda: str(uuid4()))
    page_id: str
    parent_revision_id: str | None = None
    author: str = "system"
    title: str
    body: str
    summary: str = ""
    change_summary: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)


class MemoryLinkRecord(BaseModel):
    link_id: str = Field(default_factory=lambda: str(uuid4()))
    source_page_id: str
    target_page_id: str
    relation: str = "related_to"
    weight: float = 1.0
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)


class MemoryEmbeddingRecord(BaseModel):
    embedding_id: str = Field(default_factory=lambda: str(uuid4()))
    subject_kind: Literal["page", "revision", "chunk"] = "page"
    subject_id: str
    model: str
    vector_ref: str
    dimensions: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now_iso)


class MemorySearchHit(BaseModel):
    page_id: str
    slug: str
    title: str
    summary: str = ""
    score: float = 0.0
    match_reasons: list[str] = Field(default_factory=list)


class MemoryAccessDecision(BaseModel):
    allowed: bool
    reason: str
    requires_local_redaction: bool = False
    requires_human_review: bool = False
    may_leave_machine: bool = False
    encryption_recommended: bool = False


class MemoryPageView(BaseModel):
    visible: bool
    page_id: str | None = None
    slug: str | None = None
    title: str | None = None
    summary: str | None = None
    disclosure_tier: Literal["none", "public", "sensitive", "enclave", "full"] = "none"
    body_visible: bool = False
    existence_hidden: bool = False
    access_decision: MemoryAccessDecision


class MemoryProjectedHit(BaseModel):
    page_id: str | None = None
    slug: str | None = None
    title: str | None = None
    summary: str | None = None
    disclosure_tier: Literal["none", "public", "sensitive", "enclave", "full"] = "none"
    score: float = 0.0
    match_reasons: list[str] = Field(default_factory=list)
    body_visible: bool = False
    access_decision: MemoryAccessDecision
