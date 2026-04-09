from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.governance.policy import GovernanceDriftMonitor, governance_surface_fingerprint


def test_governance_drift_monitor_initializes_from_current_state():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "spec.md").write_text("# Spec\n")
        (root / "astrata/governance").mkdir(parents=True, exist_ok=True)
        (root / "astrata/governance/constitution.py").write_text('"""Constitution loading helpers."""\n')
        monitor = GovernanceDriftMonitor(root / ".astrata/governance_drift_state.json")
        result = monitor.scan(root)
        assert result["status"] == "initialized"
        assert result["drifted_paths"] == []
        assert result["approved"] == governance_surface_fingerprint(root)


def test_governance_drift_monitor_reports_unapproved_change_once():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "spec.md").write_text("# Spec\n")
        (root / "astrata/governance").mkdir(parents=True, exist_ok=True)
        target = root / "astrata/governance/constitution.py"
        target.write_text('"""Constitution loading helpers."""\n')
        monitor = GovernanceDriftMonitor(root / ".astrata/governance_drift_state.json")
        monitor.scan(root)

        target.write_text('"""Constitution loading and parsing helpers."""\n')
        result = monitor.scan(root)
        assert result["status"] == "drifted"
        assert result["drifted_paths"] == ["astrata/governance/constitution.py"]
        assert result["newly_reported_paths"] == ["astrata/governance/constitution.py"]

        repeat = monitor.scan(root)
        assert repeat["status"] == "drifted"
        assert repeat["newly_reported_paths"] == []


def test_governance_drift_monitor_accepts_approved_state():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "spec.md").write_text("# Spec\n")
        (root / "astrata/governance").mkdir(parents=True, exist_ok=True)
        target = root / "astrata/governance/constitution.py"
        target.write_text('"""Constitution loading helpers."""\n')
        monitor = GovernanceDriftMonitor(root / ".astrata/governance_drift_state.json")
        monitor.scan(root)

        target.write_text('"""Principal-authorized constitution update."""\n')
        monitor.approve_current(root)
        result = monitor.scan(root)
        assert result["status"] == "clean"
        assert result["drifted_paths"] == []
