from pathlib import Path

from astrata.agents import DurableAgentRecord, DurableAgentRegistry
from astrata.records.models import TaskRecord


def test_durable_agent_registry_bootstraps_core_agents(tmp_path: Path):
    registry = DurableAgentRegistry(state_path=tmp_path / "durable_agents.json")

    agents = registry.ensure_bootstrap_agents()

    agent_ids = {agent.agent_id for agent in agents}
    assert {"prime", "reception", "local", "fallback"} <= agent_ids
    assert registry.get("prime") is not None
    assert registry.get("reception") is not None
    assert registry.get("local") is not None


def test_durable_agent_registry_chooses_reception_for_normal_prime_failover(tmp_path: Path):
    registry = DurableAgentRegistry(state_path=tmp_path / "durable_agents.json")
    registry.ensure_bootstrap_agents()

    fallback = registry.choose_fallback(unavailable_agent_id="prime", security_level="normal")

    assert fallback is not None
    assert fallback.agent_id == "reception"


def test_durable_agent_registry_chooses_local_for_sensitive_prime_failover(tmp_path: Path):
    registry = DurableAgentRegistry(state_path=tmp_path / "durable_agents.json")
    registry.ensure_bootstrap_agents()

    fallback = registry.choose_fallback(unavailable_agent_id="prime", security_level="enclave")

    assert fallback is not None
    assert fallback.agent_id == "local"


def test_durable_agent_registry_upserts_custom_agent(tmp_path: Path):
    registry = DurableAgentRegistry(state_path=tmp_path / "durable_agents.json")
    registry.ensure_bootstrap_agents()

    record = DurableAgentRecord(
        agent_id="research-assistant",
        title="Research Assistant",
        role="assistant",
        responsibilities=["literature review"],
        permissions_profile={"network": True},
        created_by="prime",
    )
    registry.upsert(record)

    stored = registry.get("research-assistant")
    assert stored is not None
    assert stored.created_by == "prime"
    assert stored.permissions_profile["network"] is True


def test_durable_agent_registry_creates_agent_with_governed_surface(tmp_path: Path):
    registry = DurableAgentRegistry(state_path=tmp_path / "durable_agents.json")
    registry.ensure_bootstrap_agents()

    created = registry.create_agent(
        agent_id="browser-steward",
        name="Morrow",
        title="Browser Steward",
        role="assistant",
        created_by="prime",
        responsibilities=["browser navigation"],
        permissions_profile={"network": True},
        inference_binding={"provider": "cli", "cli_tool": "kilocode"},
    )

    assert created.agent_id == "browser-steward"
    assert created.name == "Morrow"
    assert registry.get("browser-steward") is not None


def test_durable_agent_registry_blocks_direct_edit_of_system_agent(tmp_path: Path):
    registry = DurableAgentRegistry(state_path=tmp_path / "durable_agents.json")
    registry.ensure_bootstrap_agents()

    try:
        registry.update_agent("prime", patch={"title": "New Prime"}, updated_by="prime")
    except PermissionError:
        pass
    else:
        raise AssertionError("Expected PermissionError when editing system-managed agent.")


def test_durable_agent_registry_assigns_task_to_durable_agent(tmp_path: Path):
    registry = DurableAgentRegistry(state_path=tmp_path / "durable_agents.json")
    registry.ensure_bootstrap_agents()
    task = TaskRecord(
        task_id="task-1",
        title="Handle intake",
        description="Respond as reception.",
    )

    assigned = registry.assign_task(
        task,
        agent_id="reception",
        assigned_by="prime",
        mode="durable_agent",
    )

    assert assigned.assignee_agent_id == "reception"
    assert assigned.assignment_mode == "durable_agent"
    assert assigned.provenance["assigned_by"] == "prime"


def test_durable_agent_registry_assigns_ephemeral_worker_from_template(tmp_path: Path):
    registry = DurableAgentRegistry(state_path=tmp_path / "durable_agents.json")
    registry.ensure_bootstrap_agents()
    task = TaskRecord(
        task_id="task-2",
        title="Research spike",
        description="Spin up an ephemeral research worker.",
    )

    assigned = registry.assign_task(
        task,
        agent_id="reception",
        assigned_by="prime",
        mode="ephemeral_from_template",
        template_agent_id="reception",
    )

    assert assigned.assignee_agent_id == "reception"
    assert assigned.assignment_mode == "ephemeral_from_template"
    assert assigned.assignment_template_agent_id == "reception"
