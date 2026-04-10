"""Hard disclosure rules and progressive disclosure views for memory pages."""

from __future__ import annotations

from astrata.memory.models import MemoryAccessDecision, MemoryPageRecord, MemoryPageView


LOCAL_ACCESSORS = {"local", "prime", "operator", "system", "approved_local_runtime"}
REMOTE_ACCESSORS = {"cloud", "remote", "provider", "codex", "openai", "anthropic", "google"}
REDACTION_ACTIONS = {"local_redaction", "local_redact_then_verify", "enclave_transform"}
FULL_VISIBILITY_ACCESSORS = {"prime", "operator", "system", "approved_local_runtime"}
SENSITIVE_VISIBILITY_ACCESSORS = {"local", "prime", "operator", "system", "approved_local_runtime"}


def assess_memory_access(
    *,
    page: MemoryPageRecord,
    accessor: str,
    action: str = "read",
    destination: str = "local",
) -> MemoryAccessDecision:
    normalized_accessor = str(accessor or "").strip().lower() or "unknown"
    normalized_action = str(action or "").strip().lower() or "read"
    normalized_destination = str(destination or "").strip().lower() or "local"

    is_remote_destination = normalized_destination in REMOTE_ACCESSORS
    is_remote_accessor = normalized_accessor in REMOTE_ACCESSORS
    is_enclave = page.visibility == "enclave" or page.confidentiality == "enclave"
    is_sensitive = page.visibility in {"restricted", "enclave"} or page.confidentiality in {"sensitive", "enclave"}

    if normalized_action == "write":
        if normalized_accessor in {scope.lower() for scope in page.write_scopes}:
            return MemoryAccessDecision(
                allowed=True,
                reason="Accessor is permitted to modify this memory page.",
                encryption_recommended=is_sensitive and page.encryption_status != "encrypted",
            )
        return MemoryAccessDecision(
            allowed=False,
            reason="Accessor does not have write permission for this memory page.",
            encryption_recommended=is_sensitive and page.encryption_status != "encrypted",
        )

    if is_enclave and (is_remote_accessor or is_remote_destination):
        if normalized_action in REDACTION_ACTIONS and normalized_accessor in LOCAL_ACCESSORS:
            return MemoryAccessDecision(
                allowed=True,
                reason="Enclave material may only leave the machine through an explicit local redaction-and-review path.",
                requires_local_redaction=True,
                requires_human_review=True,
                may_leave_machine=False,
                encryption_recommended=page.encryption_status != "encrypted",
            )
        return MemoryAccessDecision(
            allowed=False,
            reason="Enclave material may not be exposed to remote/cloud access without explicit local redaction and deliberate review.",
            requires_local_redaction=True,
            requires_human_review=True,
            may_leave_machine=False,
            encryption_recommended=page.encryption_status != "encrypted",
        )

    if is_sensitive and (is_remote_accessor or is_remote_destination):
        if normalized_action in REDACTION_ACTIONS and normalized_accessor in LOCAL_ACCESSORS:
            return MemoryAccessDecision(
                allowed=True,
                reason="Sensitive material may only be prepared for remote use through a local redaction path.",
                requires_local_redaction=True,
                requires_human_review=True,
                may_leave_machine=True,
                encryption_recommended=page.encryption_status != "encrypted",
            )
        return MemoryAccessDecision(
            allowed=False,
            reason="Sensitive material is local-only unless a deliberate redaction-and-review flow is used.",
            requires_local_redaction=True,
            requires_human_review=True,
            may_leave_machine=False,
            encryption_recommended=page.encryption_status != "encrypted",
        )

    if normalized_accessor not in {scope.lower() for scope in page.read_scopes} and normalized_accessor not in LOCAL_ACCESSORS and not is_remote_accessor:
        return MemoryAccessDecision(
            allowed=False,
            reason="Accessor does not have read permission for this memory page.",
            encryption_recommended=is_sensitive and page.encryption_status != "encrypted",
        )

    return MemoryAccessDecision(
        allowed=True,
        reason="Memory page is readable under current local disclosure rules.",
        may_leave_machine=not is_sensitive and not is_enclave and not is_remote_destination,
        encryption_recommended=is_sensitive and page.encryption_status != "encrypted",
    )


def project_memory_page_view(
    *,
    page: MemoryPageRecord,
    accessor: str,
    action: str = "read",
    destination: str = "local",
) -> MemoryPageView:
    normalized_accessor = str(accessor or "").strip().lower() or "unknown"
    decision = assess_memory_access(
        page=page,
        accessor=normalized_accessor,
        action=action,
        destination=destination,
    )
    is_remote_party = normalized_accessor in REMOTE_ACCESSORS or str(destination or "").strip().lower() in REMOTE_ACCESSORS
    is_enclave = page.visibility == "enclave" or page.confidentiality == "enclave"
    is_sensitive = page.visibility in {"restricted", "enclave"} or page.confidentiality in {"sensitive", "enclave"}

    if is_enclave and is_remote_party:
        return MemoryPageView(
            visible=False,
            disclosure_tier="none",
            existence_hidden=True,
            access_decision=decision,
        )

    if not decision.allowed and is_sensitive and normalized_accessor not in FULL_VISIBILITY_ACCESSORS:
        summary = page.summary_public or None
        return MemoryPageView(
            visible=bool(summary),
            page_id=page.page_id if summary else None,
            slug=page.slug if summary else None,
            title=page.title if summary else None,
            summary=summary,
            disclosure_tier="public" if summary else "none",
            body_visible=False,
            existence_hidden=not bool(summary),
            access_decision=decision,
        )

    if normalized_accessor in FULL_VISIBILITY_ACCESSORS:
        summary = page.summary_enclave or page.summary_sensitive or page.summary or page.summary_public
        return MemoryPageView(
            visible=True,
            page_id=page.page_id,
            slug=page.slug,
            title=page.title,
            summary=summary,
            disclosure_tier="full",
            body_visible=decision.allowed,
            existence_hidden=False,
            access_decision=decision,
        )

    if normalized_accessor in SENSITIVE_VISIBILITY_ACCESSORS and is_sensitive:
        summary = page.summary_sensitive or page.summary_public or page.summary
        return MemoryPageView(
            visible=True,
            page_id=page.page_id,
            slug=page.slug,
            title=page.title,
            summary=summary,
            disclosure_tier="sensitive",
            body_visible=decision.allowed and not is_enclave,
            existence_hidden=False,
            access_decision=decision,
        )

    summary = page.summary_public or page.summary
    return MemoryPageView(
        visible=True,
        page_id=page.page_id,
        slug=page.slug,
        title=page.title,
        summary=summary,
        disclosure_tier="public",
        body_visible=decision.allowed and not is_sensitive and not is_enclave,
        existence_hidden=False,
        access_decision=decision,
    )
