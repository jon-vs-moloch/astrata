from astrata.controllers import ExternalAgentBinding, ExternalAgentController
from astrata.records.handoffs import HandoffRecord


def test_external_agent_controller_accepts_prime_cowork_when_binding_allows_it():
    controller = ExternalAgentController(
        binding=ExternalAgentBinding(
            agent_id="codex-prime",
            transport="codex",
            role="prime",
            can_be_prime=True,
            can_receive_subtasks=True,
            online=True,
        )
    )
    handoff = HandoffRecord(
        source_controller="astrata-prime",
        target_controller="external:codex-prime",
        task_id="task-1",
        execution_boundary="external",
        bridge_id="codex-prime",
        delegation_mode="cowork",
        envelope={"require_prime_route": True},
    )

    decision = controller.evaluate_handoff(handoff)

    assert decision.status == "accepted"
    assert decision.followup_actions[0]["type"] == "external_agent_handoff_approved"
    assert decision.followup_actions[1]["execution_boundary"] == "external"


def test_external_agent_controller_blocks_sensitive_work_without_clearance():
    controller = ExternalAgentController(
        binding=ExternalAgentBinding(
            agent_id="external-peer",
            can_be_prime=False,
            accepts_sensitive_payloads=False,
            online=True,
        )
    )
    handoff = HandoffRecord(
        source_controller="astrata-prime",
        target_controller="external:external-peer",
        task_id="task-2",
        execution_boundary="external",
        delegation_mode="direct",
        envelope={"security_level": "enclave"},
    )

    decision = controller.evaluate_handoff(handoff)

    assert decision.status == "blocked"
    assert "disclosure boundary" in decision.reason
    assert decision.followup_actions[0]["type"] == "redact_or_localize"


def test_external_agent_controller_refuses_prime_assignment_when_not_approved():
    controller = ExternalAgentController(
        binding=ExternalAgentBinding(
            agent_id="assistant-peer",
            role="assistant",
            can_be_prime=False,
            online=True,
        )
    )
    handoff = HandoffRecord(
        source_controller="astrata-prime",
        target_controller="external:assistant-peer",
        task_id="task-3",
        execution_boundary="external",
        delegation_mode="supervisory",
        envelope={"require_prime_route": True},
    )

    decision = controller.evaluate_handoff(handoff)

    assert decision.status == "refused"
    assert decision.followup_actions[0]["type"] == "require_internal_prime"
