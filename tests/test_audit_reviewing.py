from astrata.audit import review_audit_review, review_consensus_judgment
from astrata.audit.review import ReviewFinding, open_review


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
