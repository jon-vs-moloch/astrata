from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.comms.intake import MessageIntake, normalize_derived_task_proposal, process_inbound_messages
from astrata.records.communications import CommunicationRecord
from astrata.storage.db import AstrataDatabase


def test_message_intake_builds_request_spec_and_task():
    intake = MessageIntake(project_root=Path("/Users/jon/Projects/Astrata"))
    message = CommunicationRecord(
        channel="operator",
        kind="request",
        sender="principal",
        recipient="astrata",
        intent="principal_message",
        payload={"message": "Please strengthen the execution runner so it can run bounded commands."},
    )
    request_spec = intake.build_request_spec(message)
    assert request_spec.source_communication_id == message.communication_id
    assert request_spec.summary
    assert request_spec.delta_kind == "spec_vs_reality"
    proposals = intake.propose_tasks(request_spec)
    assert proposals
    assert proposals[0].route_preferences["preferred_cli_tools"][0] == "kilocode"
    task = intake.materialize_task(proposals[0])
    assert task.title
    assert task.provenance["source_communication_id"] == message.communication_id
    assert task.provenance["delta_kind"] == "spec_vs_reality"
    assert task.completion_policy["route_preferences"]["preferred_cli_tools"][0] == "kilocode"


def test_message_intake_classifies_spec_request_and_proposes_multiple_tasks():
    intake = MessageIntake(project_root=Path("/Users/jon/Projects/Astrata"))
    message = CommunicationRecord(
        channel="operator",
        kind="request",
        sender="principal",
        recipient="astrata",
        intent="principal_message",
        payload={"message": "Review the spec and strengthen the operator inbox intake path."},
    )
    request_spec = intake.build_request_spec(message)
    assert request_spec.request_kind == "spec_hardening"
    assert request_spec.delta_kind == "input_vs_spec"
    assert "governance" in request_spec.domains
    proposals = intake.propose_tasks(request_spec)
    assert len(proposals) >= 2


def test_process_inbound_messages_acknowledges_and_materializes_tasks():
    with TemporaryDirectory() as tmp:
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        db.upsert_communication(
            CommunicationRecord(
                channel="operator",
                kind="request",
                sender="principal",
                recipient="astrata",
                intent="principal_message",
                payload={"message": "Build the inbox bridge and review the spec."},
            )
        )
        results = process_inbound_messages(
            db=db,
            project_root=Path("/Users/jon/Projects/Astrata"),
            recipient="astrata",
            limit=3,
        )
        assert results
        assert results[0]["tasks"]
        assert results[0]["request_spec_artifact"]["artifact_type"] == "request_spec"
        assert results[0]["task_proposal_artifact"]["artifact_type"] == "task_proposal_bundle"
        communications = db.list_records("communications")
        assert communications[0]["status"] == "acknowledged"


def test_normalize_derived_task_proposal_classifies_spec_work():
    proposal = normalize_derived_task_proposal(
        title="Rewrite the spec section",
        description="Review the architecture spec and rewrite it for clarity.",
        parent_provenance={"source": "message_task_followup"},
        suggested_completion_type=None,
    )
    assert proposal.completion_policy["type"] == "review_or_rewrite_spec"
    assert proposal.route_preferences["preferred_cli_tools"][0] == "kilocode"
    assert proposal.provenance["derived_request_kind"] == "spec_hardening"
    assert proposal.provenance["delta_kind"] == "input_vs_spec"


def test_normalize_derived_task_proposal_preserves_dependency_hints():
    proposal = normalize_derived_task_proposal(
        title="Persist runtime posture",
        description="Write the inspected posture into persisted settings.",
        parent_provenance={"source": "message_task_followup"},
        suggested_completion_type="respond_or_execute",
        task_id_hint="persist",
        depends_on=["inspect"],
        parallelizable=False,
    )
    assert proposal.task_id_hint == "persist"
    assert proposal.depends_on == ["inspect"]
    assert proposal.parallelizable is False
