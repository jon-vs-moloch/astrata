from pathlib import Path
from tempfile import TemporaryDirectory

from astrata.config.settings import AstrataPaths, LocalRuntimeSettings, RuntimeLimits, Settings
from astrata.supervisor import AstrataSupervisor, SupervisedService


def _settings(root: Path) -> Settings:
    data_dir = root / ".astrata"
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        paths=AstrataPaths(
            project_root=root,
            data_dir=data_dir,
            docs_dir=root,
            provider_secrets_path=data_dir / "provider_secrets.json",
        ),
        runtime_limits=RuntimeLimits(),
        local_runtime=LocalRuntimeSettings(
            model_search_paths=(),
            model_install_dir=data_dir / "models",
        ),
    )


def test_supervisor_adopts_matching_process_without_starting(monkeypatch):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        supervisor = AstrataSupervisor(settings=_settings(root))
        supervisor.services = [
            SupervisedService(
                service_id="loop0_daemon",
                command=("python", "-m", "astrata.main", "loop0-daemon"),
                log_path=root / ".astrata" / "loop0-daemon.log",
                match_tokens=("astrata.main", "loop0-daemon"),
            )
        ]
        monkeypatch.setattr("astrata.supervisor._pid_alive", lambda pid: pid == 4321)
        monkeypatch.setattr(
            "astrata.supervisor._find_matching_process",
            lambda tokens: (4321, "python -m astrata.main loop0-daemon --steps 1"),
        )
        monkeypatch.setattr(supervisor, "_ensure_local_runtime", lambda: {"status": "already_running"})

        result = supervisor.reconcile()

        assert result["services"]["loop0_daemon"]["action"] == "adopted"
        assert result["services"]["loop0_daemon"]["pid"] == 4321
        assert result["services"]["loop0_daemon"]["adopted"] is True


def test_supervisor_starts_missing_service_and_records_state(monkeypatch):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        supervisor = AstrataSupervisor(settings=_settings(root))
        supervisor.services = [
            SupervisedService(
                service_id="ui_backend",
                command=("python", "-m", "astrata.ui.server", "--port", "8891"),
                log_path=root / ".astrata" / "ui.log",
                match_tokens=("astrata.ui.server", "--port", "8891"),
                health_url="http://127.0.0.1:8891/api/health",
            )
        ]
        monkeypatch.setattr("astrata.supervisor._pid_alive", lambda pid: pid == 1234)
        monkeypatch.setattr("astrata.supervisor._find_matching_process", lambda tokens: (None, None))
        monkeypatch.setattr("astrata.supervisor._http_ok", lambda url, timeout_seconds=1.0: False)
        monkeypatch.setattr(supervisor, "_start_service", lambda service: {"pid": 1234})
        monkeypatch.setattr(supervisor, "_ensure_local_runtime", lambda: {"status": "already_running"})

        result = supervisor.reconcile()

        assert result["services"]["ui_backend"]["action"] == "started"
        assert result["services"]["ui_backend"]["owned"] is True
        assert result["services"]["ui_backend"]["pid"] == 1234


def test_supervisor_restarts_stale_adopted_service(monkeypatch):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        supervisor = AstrataSupervisor(settings=_settings(root))
        supervisor.services = [
            SupervisedService(
                service_id="loop0_daemon",
                command=("python", "-m", "astrata.main", "loop0-daemon"),
                log_path=root / ".astrata" / "loop0-daemon.log",
                match_tokens=("astrata.main", "loop0-daemon"),
            )
        ]
        supervisor.state_path.write_text(
            '{"services":{"loop0_daemon":{"pid":4321,"adopted":true,"command":"python -m astrata.main loop0-daemon"}}}',
            encoding="utf-8",
        )
        monkeypatch.setattr("astrata.supervisor._pid_alive", lambda pid: pid == 9876)
        monkeypatch.setattr("astrata.supervisor._find_matching_process", lambda tokens: (None, None))
        monkeypatch.setattr(supervisor, "_start_service", lambda service: {"pid": 9876})
        monkeypatch.setattr(supervisor, "_ensure_local_runtime", lambda: {"status": "already_running"})

        result = supervisor.reconcile()

        assert result["services"]["loop0_daemon"]["action"] == "started"
        assert result["services"]["loop0_daemon"]["pid"] == 9876
        assert result["services"]["loop0_daemon"]["owned"] is True


def test_supervisor_stop_leaves_adopted_services_running_by_default(monkeypatch):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        supervisor = AstrataSupervisor(settings=_settings(root))
        supervisor.services = [
            SupervisedService(
                service_id="loop0_daemon",
                command=("python", "-m", "astrata.main", "loop0-daemon"),
                log_path=root / ".astrata" / "loop0-daemon.log",
                match_tokens=("astrata.main", "loop0-daemon"),
            )
        ]
        supervisor.state_path.write_text(
            '{"services":{"loop0_daemon":{"pid":4321,"adopted":true,"command":"python -m astrata.main loop0-daemon"}}}',
            encoding="utf-8",
        )
        stopped: list[int] = []
        monkeypatch.setattr("astrata.supervisor._pid_alive", lambda pid: pid == 4321)
        monkeypatch.setattr(supervisor, "_terminate_pid", lambda pid: stopped.append(pid))

        result = supervisor.stop()

        assert result["services"]["loop0_daemon"]["action"] == "left_adopted_running"
        assert stopped == []


def test_supervisor_status_marks_healthy_local_endpoint_adoptable(monkeypatch):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        supervisor = AstrataSupervisor(settings=_settings(root))

        class _Service:
            def _local_runtime_snapshot(self):
                return {"managed_process": {"running": False}}

        monkeypatch.setattr("astrata.supervisor.AstrataUIService", lambda settings: _Service())
        monkeypatch.setattr("astrata.supervisor._http_ok", lambda url, timeout_seconds=1.0: True)

        result = supervisor.status()

        assert result["local_runtime"]["direct_endpoint"]["ok"] is True
        assert result["local_runtime"]["direct_endpoint"]["adoptable"] is True


def test_supervisor_reconcile_runs_runtime_hygiene(monkeypatch):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = _settings(root)
        supervisor = AstrataSupervisor(settings=settings)
        supervisor.services = []
        monkeypatch.setattr(supervisor, "_ensure_local_runtime", lambda: {"status": "already_running"})

        result = supervisor.reconcile()

        assert result["runtime_hygiene"]["status"] == "ok"
