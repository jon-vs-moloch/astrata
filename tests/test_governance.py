from pathlib import Path

from astrata.governance.authority import (
    AuthorityLevel,
    create_admin_authority,
    create_constitutional_authority,
    create_system_authority,
    create_user_authority,
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
