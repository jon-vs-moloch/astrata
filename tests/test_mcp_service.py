from pathlib import Path

from astrata.mcp import MCPBridgeBinding, MCPBridgeService


def test_mcp_bridge_service_registers_and_lists_bindings(tmp_path: Path):
    service = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    inbound = service.register_binding(
        MCPBridgeBinding(
            bridge_id="inbound-1",
            direction="inbound",
            transport="streamable_http",
            agent_id="codex-prime",
            role="prime",
            can_be_prime=True,
        )
    )
    outbound = service.register_binding(
        MCPBridgeBinding(
            bridge_id="outbound-1",
            direction="outbound",
            transport="stdio",
            agent_id="filesystem-worker",
            role="worker",
        )
    )

    assert inbound.bridge_id == "inbound-1"
    assert outbound.bridge_id == "outbound-1"
    assert [binding.bridge_id for binding in service.list_bindings(direction="inbound")] == ["inbound-1"]
    assert [binding.bridge_id for binding in service.list_bindings(direction="outbound")] == ["outbound-1"]


def test_mcp_bridge_service_opens_inbound_handoff(tmp_path: Path):
    service = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    service.register_binding(
        MCPBridgeBinding(
            bridge_id="bridge-a",
            direction="inbound",
            transport="streamable_http",
            agent_id="external-prime",
            role="prime",
            can_be_prime=True,
        )
    )

    handoff = service.open_inbound_handoff(
        bridge_id="bridge-a",
        tool_name="submit_task",
        arguments={"task": "Build the browser bridge"},
        task_id="task-123",
        target_controller="prime",
        delegation_mode="supervisory",
        metadata={"security_level": "normal"},
    )

    assert handoff.source_controller == "external:external-prime"
    assert handoff.target_controller == "prime"
    assert handoff.execution_boundary == "external"
    assert handoff.bridge_id == "bridge-a"
    assert handoff.delegation_mode == "supervisory"
    assert handoff.metadata["tool_name"] == "submit_task"
    assert service.list_events(bridge_id="bridge-a")[0].event_type == "tool_call_received"


def test_mcp_bridge_service_opens_outbound_handoff_and_projects_external_binding(tmp_path: Path):
    service = MCPBridgeService(state_path=tmp_path / "mcp_bridges.json")
    service.register_binding(
        MCPBridgeBinding(
            bridge_id="bridge-b",
            direction="outbound",
            transport="stdio",
            agent_id="helper-agent",
            role="assistant",
            can_receive_subtasks=True,
            allowed_tools=("review_patch", "summarize_results"),
            exposed_resources=("workspace",),
        )
    )

    external = service.external_agent_binding("bridge-b")
    handoff = service.open_outbound_handoff(
        bridge_id="bridge-b",
        task_id="task-456",
        source_controller="astrata-prime",
        tool_name="review_patch",
        arguments={"path": "spec.md"},
        delegation_mode="direct",
    )

    assert external.agent_id == "helper-agent"
    assert "review_patch" in external.capabilities
    assert handoff.source_controller == "astrata-prime"
    assert handoff.target_controller == "external:helper-agent"
    assert handoff.route["provider"] == "mcp"
    assert service.list_events(bridge_id="bridge-b")[0].event_type == "tool_call_requested"
