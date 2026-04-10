from astrata.inference.planner import InferencePlanner
from astrata.inference.contracts import BackendCapabilitySet


def test_inference_planner_exposes_agent_session_profile():
    planner = InferencePlanner()
    profile = planner.endpoint_profile("agent_session")
    assert profile.endpoint_type == "agent_session"
    assert profile.memory_policy == "managed_session_state"
    assert profile.default_strategy == "single_pass"
    assert profile.continuity == "managed"


def test_inference_planner_notes_backend_gaps_for_branch_checkpointed_endpoint():
    planner = InferencePlanner()
    plan = planner.plan_for_endpoint(
        endpoint_type="tool_augmented",
        backend=BackendCapabilitySet(
            backend_id="llama_cpp",
            multi_model_residency=True,
            native_checkpoint_restore=False,
        ),
    )
    assert plan.endpoint.endpoint_type == "tool_augmented"
    assert plan.memory_policy == "branch_checkpointed"
    assert any("emulated" in note.lower() for note in plan.notes)
