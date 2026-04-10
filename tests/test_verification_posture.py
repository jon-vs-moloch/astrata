from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.audit.posture import VerificationPostureStore
from astrata.audit.review import ReviewFinding, open_review
from astrata.config.settings import load_settings
from astrata.loop0.runner import Loop0Runner
from astrata.storage.db import AstrataDatabase


def test_verification_posture_store_tightens_after_failures_and_relaxes_after_clean_streak():
    with TemporaryDirectory() as tmp:
        store = VerificationPostureStore(Path(tmp) / "verification_posture.json")
        first = store.record_review(subject_kind="verification", findings_count=1, status="open")
        assert first["level"] == "strict"
        for _ in range(12):
            latest = store.record_review(subject_kind="verification", findings_count=0, status="resolved")
        assert latest["level"] == "relaxed"
        assert latest["sample_rate"] == 4


def test_persist_audit_review_emits_verification_posture_artifact():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        runner = Loop0Runner(settings=settings, db=db)
        review = open_review(
            subject_kind="verification",
            subject_id="verification-1",
            summary="Verifier contradicted reality.",
            findings=[ReviewFinding(severity="critical", summary="Verifier passed a broken result.")],
        )
        runner._persist_audit_review(  # noqa: SLF001
            review=review,
            artifact_type="loop0_verification_review",
            title="Loop 0 verification review: task-1",
            description="Second-pass verification audit.",
            provenance={"task_id": "task-1", "attempt_id": "attempt-1"},
        )
        artifact_types = {artifact["artifact_type"] for artifact in db.list_records("artifacts")}
        assert "verification_posture" in artifact_types
