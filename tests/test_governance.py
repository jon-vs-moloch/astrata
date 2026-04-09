from astrata.governance.documents import load_governance_bundle


def test_governance_bundle_loads_core_docs():
    bundle = load_governance_bundle(__import__("pathlib").Path("/Users/jon/Projects/Astrata"))
    assert bundle.constitution.exists
    assert "Astrata" in bundle.constitution.content
    assert bundle.planning_docs["build_path"].exists

