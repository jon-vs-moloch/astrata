from pathlib import Path

from astrata.config.settings import load_settings
from astrata.mcp.projections import ConnectorProjectionService
from astrata.memory import MemoryStore
from astrata.records.models import TaskRecord
from astrata.storage.db import AstrataDatabase


def test_connector_projection_service_projects_task_and_memory_views(tmp_path: Path):
    settings = load_settings(tmp_path)
    db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
    db.initialize()
    db.upsert_task(
        TaskRecord(
            task_id="task-1",
            title="Test task",
            description="Sensitive-ish detail",
            status="working",
        )
    )
    memory = MemoryStore(settings.paths.data_dir / "memory.db")
    memory.create_or_update_page(
        slug="test-page",
        title="Test Page",
        body="Body text",
        summary="Internal summary",
        summary_public="Public summary",
        visibility="shared",
        confidentiality="normal",
    )
    projections = ConnectorProjectionService(settings=settings, db=db, memory_store=memory)

    capabilities = projections.list_capabilities(advertisement={"allowed_tools": ["search"], "control_posture": "peer"})
    status = projections.get_task_status(task_id="task-1")
    search = projections.search(query="test", limit=5)
    fetched = projections.fetch(identifier="test-page")

    assert capabilities["capabilities"] == ["search"]
    assert capabilities["system"]["name"] == "Astrata"
    assert "local-first" in capabilities["system"]["summary"]
    assert capabilities["access_policy"]["public_access"]["download"] is True
    assert capabilities["hosted_bridge_eligibility"]["status"] == "invite_required"
    assert status["found"] is True
    assert status["summary_public"] == "Test task is currently working."
    assert search["task_hits"][0]["id"] == "task-1"
    assert search["memory_hits"][0]["slug"] == "test-page"
    assert fetched["found"] is True
    assert fetched["summary"] == "Public summary"
