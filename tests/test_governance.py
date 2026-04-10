from pathlib import Path

from astrata.governance.authority import (
    AuthorityLevel,
    create_admin_authority,
    create_constitutional_authority,
    create_system_authority,
    create_user_authority,
    delegated_task_approval,
)
from astrata.governance.documents import load_governance_bundle


def test_governance_bundle_loads_core_docs():
    bundle = load_governance_bundle(Path("/Users/jon/Projects/Astrata"))
    assert bundle.constitution.exists
    assert "Astrata" in bundle.constitution.content
    assert bundle.planning_docs["build_path"].exists


def test_authority_helpers_expose_basic_levels_and_summary():
    user = create_user_authority()
    system = create_system_authority()
    admin = create_admin_authority()
    constitutional = create_constitutional_authority("runtime")
    assert user.level == AuthorityLevel.USER
    assert system.level == AuthorityLevel.SYSTEM
    assert admin.level == AuthorityLevel.ADMIN
    assert constitutional.summary == "constitution -> section_runtime"
    assert system.validate_level(AuthorityLevel.USER) is True


def test_delegated_task_approval_defaults_to_parent_controlled_review():
    approval = delegated_task_approval(task_payload=None, delegated_by="prime")
    assert approval["required"] is True
    assert approval["approver"] == "parent_task"
    assert approval["delegated_by"] == "prime"
    assert approval["self_approval_allowed"] is False
    assert approval["authority_chain"] == ["prime", "constitution"]


def test_delegated_task_approval_preserves_parent_policy():
    approval = delegated_task_approval(
        task_payload={
            "completion_policy": {
                "approval": {
                    "mode": "explicit",
                    "approver": "principal",
                    "authority_chain": ["principal", "constitution"],
                    "self_approval_allowed": False,
                    "consensus_allowed": True,
                }
            }
        },
        delegated_by="prime",
    )
    assert approval["approver"] == "principal"
    assert approval["consensus_allowed"] is True
    assert approval["authority_chain"] == ["principal", "constitution"]
