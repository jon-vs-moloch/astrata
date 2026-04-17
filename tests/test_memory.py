from pathlib import Path

from astrata.memory import MemoryEmbeddingRecord, MemoryLinkRecord, MemoryStore


def test_memory_store_creates_pages_revisions_and_searches(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    page, revision = store.create_or_update_page(
        slug="wikipedia",
        title="Wikipedia",
        body="Wikipedia is a collaboratively edited encyclopedia with densely interlinked articles.",
        summary="Collaborative encyclopedia.",
        summary_public="A public encyclopedia.",
        summary_sensitive="A densely-linked knowledge system.",
        author="prime",
        entity_kind="knowledge_system",
        tags=["encyclopedia", "knowledge", "wiki"],
        change_summary="Seed memory page.",
        provenance={"source": "operator"},
    )

    assert page.current_revision_id == revision.revision_id
    assert store.get_page_by_slug("wikipedia") is not None

    revisions = store.list_revisions(page.page_id)
    assert len(revisions) == 1
    assert revisions[0].change_summary == "Seed memory page."

    hits = store.search_pages("encyclopedia")
    assert hits
    assert hits[0].slug == "wikipedia"


def test_memory_store_tracks_revision_history_and_permissions(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    page, first_revision = store.create_or_update_page(
        slug="astrata",
        title="Astrata",
        body="Astrata is a local-first coordination system.",
        summary="Local-first coordination.",
        summary_public="A coordination system.",
        summary_sensitive="A local-first coordination system.",
        read_scopes=["local", "prime"],
        write_scopes=["prime"],
        visibility="restricted",
        status="active",
        change_summary="Initial entry.",
    )
    updated_page, second_revision = store.create_or_update_page(
        slug="astrata",
        title="Astrata",
        body="Astrata is a local-first coordination and memory system.",
        summary="Local-first coordination and memory.",
        summary_public="A coordination and memory system.",
        summary_sensitive="A local-first coordination and memory system.",
        read_scopes=["local", "prime"],
        write_scopes=["prime"],
        visibility="restricted",
        status="active",
        change_summary="Expanded definition.",
    )

    assert updated_page.page_id == page.page_id
    assert second_revision.parent_revision_id == first_revision.revision_id
    assert updated_page.current_revision_id == second_revision.revision_id
    assert updated_page.read_scopes == ["local", "prime"]
    assert updated_page.write_scopes == ["prime"]
    assert updated_page.encryption_status == "planned"

    revisions = store.list_revisions(page.page_id)
    assert [item.change_summary for item in revisions] == ["Initial entry.", "Expanded definition."]


def test_memory_store_tracks_links_and_embedding_metadata(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    astrata, _ = store.create_or_update_page(
        slug="astrata",
        title="Astrata",
        body="Astrata is a system.",
        summary="System page.",
    )
    wikipedia, _ = store.create_or_update_page(
        slug="wikipedia",
        title="Wikipedia",
        body="Wikipedia is a system of articles.",
        summary="Reference page.",
    )

    link = store.upsert_link(
        MemoryLinkRecord(
            source_page_id=astrata.page_id,
            target_page_id=wikipedia.page_id,
            relation="inspired_by",
            provenance={"source": "operator"},
        )
    )
    embedding = store.upsert_embedding(
        MemoryEmbeddingRecord(
            subject_kind="page",
            subject_id=astrata.page_id,
            model="nomic-embed-text",
            vector_ref="memory://embeddings/astrata",
            dimensions=768,
            metadata={"status": "stub"},
        )
    )

    outbound = store.list_links(astrata.page_id, direction="outbound")
    inbound = store.list_links(wikipedia.page_id, direction="inbound")
    embeddings = store.list_embeddings(subject_kind="page", subject_id=astrata.page_id)

    assert outbound[0].link_id == link.link_id
    assert inbound[0].relation == "inspired_by"
    assert embeddings[0].embedding_id == embedding.embedding_id
    assert embeddings[0].metadata["status"] == "stub"


def test_memory_store_denies_remote_access_to_enclave_material_by_default(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    page, _revision = store.create_or_update_page(
        slug="tax-records",
        title="Tax Records",
        body="Sensitive financial material that must remain on-device.",
        summary="Highly sensitive financial records.",
        summary_public="A restricted financial dossier exists.",
        summary_sensitive="Sensitive financial records are present.",
        summary_enclave="Tax records and supporting documents.",
        visibility="enclave",
        confidentiality="enclave",
        read_scopes=["local", "approved_local_runtime"],
        write_scopes=["prime"],
        encryption_status="planned",
        change_summary="Seed enclave page.",
    )

    denied = store.assess_access(
        page_id=page.page_id,
        accessor="cloud",
        action="read",
        destination="remote",
    )
    assert denied.allowed is False
    assert denied.requires_local_redaction is True
    assert denied.requires_human_review is True
    assert denied.may_leave_machine is False
    assert denied.encryption_recommended is True


def test_memory_store_allows_only_local_redaction_path_for_sensitive_remote_prep(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    page, _revision = store.create_or_update_page(
        slug="medical-intake",
        title="Medical Intake",
        body="Personal medical details.",
        summary="Personal medical details.",
        summary_public="A restricted medical intake record exists.",
        summary_sensitive="Personal medical details are stored locally.",
        visibility="restricted",
        confidentiality="sensitive",
        read_scopes=["local", "prime"],
        write_scopes=["prime"],
        encryption_status="planned",
        change_summary="Seed sensitive page.",
    )

    direct = store.assess_access(
        slug="medical-intake",
        accessor="google",
        action="read",
        destination="remote",
    )
    redaction = store.assess_access(
        slug="medical-intake",
        accessor="local",
        action="local_redact_then_verify",
        destination="remote",
    )
    write_allowed = store.assess_access(
        slug="medical-intake",
        accessor="prime",
        action="write",
        destination="local",
    )

    assert direct.allowed is False
    assert direct.requires_local_redaction is True
    assert redaction.allowed is True
    assert redaction.requires_human_review is True
    assert redaction.may_leave_machine is True
    assert write_allowed.allowed is True


def test_memory_store_projects_progressive_disclosure_views(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    page, _revision = store.create_or_update_page(
        slug="board-minutes",
        title="Board Minutes",
        body="Detailed confidential board discussion.",
        summary="Confidential board discussion.",
        summary_public="A restricted governance record exists.",
        summary_sensitive="Confidential board discussion is stored locally.",
        visibility="restricted",
        confidentiality="sensitive",
        read_scopes=["local", "prime"],
        write_scopes=["prime"],
        change_summary="Seed restricted page.",
    )

    remote_view = store.project_view(
        page_id=page.page_id,
        accessor="cloud",
        destination="remote",
    )
    prime_view = store.project_view(
        page_id=page.page_id,
        accessor="prime",
        destination="local",
    )

    assert remote_view.visible is True
    assert remote_view.disclosure_tier == "public"
    assert remote_view.summary == "A restricted governance record exists."
    assert remote_view.body_visible is False
    assert prime_view.visible is True
    assert prime_view.disclosure_tier == "full"
    assert prime_view.body_visible is True


def test_memory_store_can_hide_existence_for_enclave_content(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    page, _revision = store.create_or_update_page(
        slug="root-keys",
        title="Root Keys",
        body="Machine secrets.",
        summary="Machine secrets.",
        summary_public="",
        summary_sensitive="",
        summary_enclave="Root signing keys and recovery material.",
        visibility="enclave",
        confidentiality="enclave",
        read_scopes=["approved_local_runtime", "prime"],
        write_scopes=["prime"],
        change_summary="Seed enclave secret.",
    )

    remote_view = store.project_view(
        page_id=page.page_id,
        accessor="openai",
        destination="remote",
    )
    local_view = store.project_view(
        page_id=page.page_id,
        accessor="approved_local_runtime",
        destination="local",
    )

    assert remote_view.visible is False
    assert remote_view.existence_hidden is True
    assert remote_view.disclosure_tier == "none"
    assert local_view.visible is True
    assert local_view.disclosure_tier == "full"
    assert local_view.summary == "Root signing keys and recovery material."


def test_memory_store_retrieves_projected_views_for_remote_consumers(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    store.create_or_update_page(
        slug="public-wiki",
        title="Public Wiki",
        body="A public encyclopedia page about policy.",
        summary="Public policy page.",
        summary_public="A public policy page.",
        tags=["policy"],
        visibility="shared",
        confidentiality="normal",
        change_summary="Seed public page.",
    )
    store.create_or_update_page(
        slug="board-policy",
        title="Board Policy",
        body="Sensitive policy notes for directors.",
        summary="Sensitive board policy notes.",
        summary_public="A restricted policy record exists.",
        summary_sensitive="Sensitive board policy notes are stored locally.",
        tags=["policy"],
        visibility="restricted",
        confidentiality="sensitive",
        change_summary="Seed sensitive page.",
    )
    store.create_or_update_page(
        slug="enclave-policy",
        title="Enclave Policy",
        body="Enclave-only policy details.",
        summary="Enclave policy notes.",
        summary_public="",
        summary_sensitive="",
        summary_enclave="Enclave-only policy details.",
        tags=["policy"],
        visibility="enclave",
        confidentiality="enclave",
        change_summary="Seed enclave page.",
    )

    hits = store.retrieve_views("policy", accessor="cloud", destination="remote", limit=10)
    by_slug = {hit.slug: hit for hit in hits}

    assert set(by_slug) == {"public-wiki", "board-policy"}
    assert by_slug["public-wiki"].disclosure_tier == "public"
    assert by_slug["public-wiki"].summary == "A public policy page."
    assert by_slug["board-policy"].disclosure_tier == "public"
    assert by_slug["board-policy"].summary == "A restricted policy record exists."
    assert "enclave-policy" not in by_slug


def test_memory_store_exports_only_projected_context_snippets(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory.db")
    store.create_or_update_page(
        slug="deployment-runbook",
        title="Deployment Runbook",
        body="Internal deployment process with rollback notes.",
        summary="Internal deployment process.",
        summary_public="A deployment runbook exists.",
        summary_sensitive="Internal deployment and rollback guidance.",
        tags=["deployment"],
        visibility="restricted",
        confidentiality="sensitive",
        change_summary="Seed deployment runbook.",
    )

    remote_snippets = store.export_context("deployment", accessor="provider", destination="remote", limit=5)
    local_snippets = store.export_context("deployment", accessor="local", destination="local", limit=5)

    assert remote_snippets == ["[public] Deployment Runbook: A deployment runbook exists."]
    assert local_snippets == ["[sensitive] Deployment Runbook: Internal deployment and rollback guidance."]
