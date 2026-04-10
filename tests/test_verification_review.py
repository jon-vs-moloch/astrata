from pathlib import Path

from astrata.audit import signals_from_review
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


def test_verification_review_flags_unjustified_prime_usage(tmp_path: Path):
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
        expected_paths=[],
        implementation={"status": "applied", "written_paths": []},
        verification=verification,
        attempt={
            "task_id": "task-1",
            "resource_usage": {
                "implementation": {
                    "resolved_route": {"provider": "codex", "model": "gpt-5.4"},
                }
            },
        },
        task_payload={
            "task_id": "task-1",
            "title": "Review task",
            "risk": "low",
            "provenance": {"task_class": "review"},
            "completion_policy": {"type": "review_or_audit"},
        },
    )
    assert review.status == "open"
    assert any("without a recorded admission basis" in finding.summary for finding in review.findings)


def test_verification_review_produces_reusable_signals_for_findings(tmp_path: Path):
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
    signals = signals_from_review(review)
    assert signals
    assert signals[0].subject_kind == "verification"
