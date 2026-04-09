from pathlib import Path

from astrata.verification.basic import verify_expected_paths, verify_gap_candidate


def test_verify_expected_paths_fails_when_targets_are_missing():
    result = verify_expected_paths(
        Path("/Users/jon/Projects/Astrata"),
        ["astrata/does_not_exist/module.py", "astrata/does_not_exist/__init__.py"],
    )
    assert result.result == "fail"
    assert result.evidence["missing"]


def test_verify_gap_candidate_passes_for_real_missing_slice():
    result = verify_gap_candidate(
        Path("/Users/jon/Projects/Astrata"),
        ["astrata/does_not_exist/module.py", "astrata/does_not_exist/__init__.py"],
    )
    assert result.result == "pass"
    assert "missing" in result.evidence
