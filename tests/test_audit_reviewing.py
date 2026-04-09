from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.audit import review_audit_review, review_consensus_judgment, select_audit_followup_policy
from astrata.audit.review import ReviewFinding, open_review
from astrata.config.settings import load_settings
from astrata.loop0.runner import Loop0Runner
from astrata.storage.db import AstrataDatabase


def test_review_consensus_judgment_flags_false_approval():
    review = review_consensus_judgment(
        task_id="task-1",
        consensus={
            "required_reviews": 2,
            "status": "approved",
            "worker_ids": ["worker.kilocode", "worker.gemini-cli.gemini-2-5-flash"],
            "results": [
                {
                    "worker_task_id": "child-1",
                    "status": "applied",
                    "principal_response": "Looks good.",
                }
            ],
        },
    )
    assert review.status == "open"
    assert any("without enough successful worker reviews" in finding.summary for finding in review.findings)


def test_review_audit_review_flags_resolved_review_with_findings():
    review = open_review(
        subject_kind="verification",
        subject_id="verification-1",
        summary="This should not be marked resolved.",
        findings=[ReviewFinding(severity="high", summary="Verifier contradicted observed reality.")],
    )
    review.status = "resolved"
    meta_review = review_audit_review(review=review)
    assert meta_review.status == "open"
    assert any("marked resolved" in finding.summary for finding in meta_review.findings)


def test_select_audit_followup_policy_targets_findings():
    review = open_review(
        subject_kind="verification",
        subject_id="verification-1",
        summary="Verifier contradicted reality.",
        findings=[ReviewFinding(severity="critical", summary="Verifier passed a broken result.")],
    )
    policy = select_audit_followup_policy(review=review)
    assert policy["mode"] == "targeted"
    assert policy["followup_specs"]
    assert "Repair verification path" in policy["followup_specs"][0]["title"]


def test_select_audit_followup_policy_can_sample_clean_review():
    review = open_review(
        subject_kind="verification",
        subject_id="verification-clean",
        summary="Verifier looks healthy.",
        findings=[],
    )
    review.status = "resolved"
    policy = select_audit_followup_policy(review=review, sample_rate=1)
    assert policy["mode"] == "sampled"
    assert policy["followup_specs"]
    assert "Spot-check verification" in policy["followup_specs"][0]["title"]


def test_persist_audit_review_materializes_targeted_followup_task():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        runner = Loop0Runner(settings=settings, db=db)
        review = open_review(
            subject_kind="consensus_judgment",
            subject_id="task-1",
            summary="Consensus was approved without enough evidence.",
            findings=[ReviewFinding(severity="high", summary="Consensus was approved without enough successful worker reviews.")],
        )
        runner._persist_audit_review(  # noqa: SLF001
            review=review,
            artifact_type="consensus_review_audit",
            title="Consensus review audit: task-1",
            description="Audit of consensus evidence.",
            provenance={"task_id": "task-1"},
        )
        tasks = db.list_records("tasks")
        assert tasks
        assert any(task.get("provenance", {}).get("source") == "audit_followup" for task in tasks)
