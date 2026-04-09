from astrata.audit import AuditDiagnostics, ReviewFinding, open_review


def test_audit_diagnostics_reports_basic_stats_and_validation_issues():
    review = open_review(
        subject_kind="task",
        subject_id="task-1",
        summary="Review summary",
        findings=[
            ReviewFinding(severity="high", summary="Real issue", evidence={"path": "x.py"}),
            ReviewFinding(severity="low", summary="", evidence={}),
        ],
    )
    diagnostics = AuditDiagnostics(review)
    stats = diagnostics.get_basic_stats()
    assert stats["total_findings"] == 2
    assert stats["severity_counts"] == {"high": 1, "low": 1}
    report = diagnostics.generate_report()
    assert "Audit Review Report for task 'task-1'" in report
    assert "Validation Issues:" in report

