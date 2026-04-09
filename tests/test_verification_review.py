from pathlib import Path

from astrata.records.models import VerificationRecord
from astrata.verification.review import review_verification


def test_verification_review_flags_false_positive(tmp_path: Path):
    verification = VerificationRecord(
        target_kind="task",
        target_id="task-1",
        verifier="test",
        result="pass",
        confidence=0.9,
    )
    review = review_verification(
        project_root=tmp_path,
        candidate_key="candidate",
        expected_paths=["astrata/missing.py"],
        implementation={"status": "applied", "written_paths": ["astrata/missing.py"]},
        verification=verification,
    )
    assert review.findings
    assert review.status == "open"
