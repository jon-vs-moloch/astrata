from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.audit import (
    open_signal,
    review_audit_review,
    review_consensus_judgment,
    select_audit_followup_policy,
    select_signal_followup_policy,
    signals_from_inference_telemetry,
    signals_from_review,
)
from astrata.audit.review import ReviewFinding, open_review
from astrata.config.settings import load_settings
from astrata.loop0.runner import Loop0Runner
from astrata.records.models import AttemptRecord
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

def test_select_signal_followup_policy_targets_open_problem_signal():
    signal = open_signal(
        signal_kind="problem",
        subject_kind="inference_policy",
        subject_id="prime_admission_basis",
        summary="Prime was invoked without a recorded admission basis.",
        severity="high",
    )
    policy = select_signal_followup_policy(signal=signal)
    assert policy["mode"] == "targeted"
    assert policy["followup_specs"]
    assert "Repair observed system problem" in policy["followup_specs"][0]["title"]


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


def test_persist_audit_review_records_negative_route_observation_for_verification_review():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        runner = Loop0Runner(settings=settings, db=db)
        db.upsert_attempt(
            AttemptRecord(
                attempt_id="attempt-1",
                task_id="task-1",
                actor="prime",
                outcome="succeeded",
                resource_usage={
                    "implementation": {
                        "resolved_route": {"provider": "cli", "cli_tool": "kilocode", "model": "kilocode"}
                    }
                },
            )
        )
        review = open_review(
            subject_kind="verification",
            subject_id="verification-1",
            summary="Verification contradicted reality.",
            findings=[ReviewFinding(severity="critical", summary="Verifier passed a broken result.")],
        )
        runner._persist_audit_review(  # noqa: SLF001
            review=review,
            artifact_type="loop0_verification_review",
            title="Loop 0 verification review: task-1",
            description="Second-pass verification audit.",
            provenance={"task_id": "task-1", "attempt_id": "attempt-1"},
        )
        observations = runner.route_observations.list(subject_kind="execution_route", task_class="review")  # noqa: SLF001
        assert observations
        assert observations[-1]["variant_id"] == "cli:kilocode:kilocode"
        assert observations[-1]["passed"] is False
        health = runner.health_store.assess({"provider": "cli", "cli_tool": "kilocode", "model": "kilocode"})  # noqa: SLF001
        assert health["recent_failures"] >= 1


def test_persist_audit_review_records_positive_route_observations_for_consensus_routes():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        runner = Loop0Runner(settings=settings, db=db)
        from astrata.records.models import TaskRecord

        db.upsert_task(
            TaskRecord(
                task_id="task-1",
                title="Consensus-reviewed task",
                description="Bounded review work.",
                provenance={
                    "task_class": "review",
                    "consensus_review": {
                        "status": "approved",
                        "required_reviews": 2,
                        "results": [
                            {"route": {"provider": "cli", "cli_tool": "kilocode", "model": "kilocode"}},
                            {"route": {"provider": "cli", "cli_tool": "gemini-cli", "model": "gemini-2.5-flash"}},
                        ],
                    },
                },
            )
        )
        review = open_review(
            subject_kind="consensus_judgment",
            subject_id="task-1",
            summary="Consensus evidence is internally coherent.",
            findings=[],
        )
        review.status = "resolved"
        runner._persist_audit_review(  # noqa: SLF001
            review=review,
            artifact_type="consensus_review_audit",
            title="Consensus review audit: task-1",
            description="Audit of consensus evidence.",
            provenance={"task_id": "task-1"},
        )
        observations = runner.route_observations.list(subject_kind="execution_route", task_class="review")  # noqa: SLF001
        variant_ids = {item["variant_id"] for item in observations}
        assert "cli:kilocode:kilocode" in variant_ids
        assert "cli:gemini-cli:gemini-2.5-flash" in variant_ids


def test_persist_signals_materializes_followup_tasks_for_general_observation_signals():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        runner = Loop0Runner(settings=settings, db=db)
        artifacts = runner._persist_signals(  # noqa: SLF001
            signals=[
                open_signal(
                    signal_kind="drift",
                    subject_kind="inference_policy",
                    subject_id="prime_admission_basis",
                    summary="Prime was invoked without a recorded admission basis.",
                    severity="moderate",
                )
            ],
            artifact_type="loop0_inference_signal",
            title_prefix="Loop 0 inference signal",
            description="Durable internal observation signal derived from inference telemetry.",
            provenance={"task_id": "task-1", "attempt_id": "attempt-1"},
        )
        assert artifacts
        tasks = db.list_records("tasks")
        assert tasks
        assert any(task.get("provenance", {}).get("source") == "observation_signal" for task in tasks)


def test_inference_telemetry_emits_multiple_reusable_signals():
    signals = signals_from_inference_telemetry(
        {
            "window_hours": 24,
            "unjustified_prime_attempts": 1,
            "avoidable_prime_attempts": 2,
            "unjustified_prime_examples": [{"task_id": "task-1"}],
            "avoidable_prime_examples": [{"task_id": "task-2"}],
        }
    )
    subject_ids = {signal.subject_id for signal in signals}
    assert "prime_admission_basis" in subject_ids
    assert "prime_pressure_reduction" in subject_ids


def test_review_derived_signals_materialize_followup_tasks():
    with TemporaryDirectory() as tmp:
        settings = load_settings(Path("/Users/jon/Projects/Astrata"))
        db = AstrataDatabase(Path(tmp) / "astrata.db")
        db.initialize()
        runner = Loop0Runner(settings=settings, db=db)
        review = open_review(
            subject_kind="verification",
            subject_id="verification-1",
            summary="Verification contradicted reality.",
            findings=[ReviewFinding(severity="critical", summary="Verifier passed a broken result.")],
        )
        artifacts = runner._persist_signals(  # noqa: SLF001
            signals=signals_from_review(review),
            artifact_type="loop0_review_signal",
            title_prefix="Loop 0 review signal",
            description="Durable internal observation signal derived from review findings.",
            provenance={"task_id": "task-1", "attempt_id": "attempt-1"},
        )
        assert artifacts
        tasks = db.list_records("tasks")
        assert tasks
        assert any(task.get("provenance", {}).get("source") == "observation_signal" for task in tasks)
