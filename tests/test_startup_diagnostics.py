from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os

from astrata.config.settings import AstrataPaths, LocalRuntimeSettings, RuntimeLimits, Settings
from astrata.startup.diagnostics import (
    generate_python_preflight_report,
    load_preflight_report,
    load_runtime_report,
    run_startup_reflection,
)
from astrata.storage.db import AstrataDatabase


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


def test_generate_python_preflight_report_writes_artifact():
    with TemporaryDirectory() as tmp:
        settings = _settings(Path(tmp))
        report = generate_python_preflight_report(settings=settings, python_executable="python3")
        assert report["phase"] == "pre_inference"
        assert report["selected_python"]
        stored = load_preflight_report(settings)
        assert stored is not None
        assert stored["phase"] == "pre_inference"


def test_run_startup_reflection_creates_task_for_issues():
    with TemporaryDirectory() as tmp:
        settings = _settings(Path(tmp))
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        generate_python_preflight_report(settings=settings, python_executable="python3")
        result = run_startup_reflection(settings=settings, db=db)
        assert result.report["phase"] == "post_boot"
        stored = load_runtime_report(settings)
        assert stored is not None
        tasks = db.list_records("tasks")
        if result.report["issues"]:
            assert any(task["task_id"] == "startup-self-diagnosis" for task in tasks)
            communications = db.list_records("communications")
            assert any(item["intent"] == "startup_diagnostic" for item in communications)


def test_run_startup_reflection_uses_managed_runtime_endpoint_when_present():
    with TemporaryDirectory() as tmp:
        settings = _settings(Path(tmp))
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        generate_python_preflight_report(settings=settings, python_executable="python3")
        (settings.paths.data_dir / "local_runtime.json").write_text(
            json.dumps(
                {
                    "pid": 12345,
                    "endpoint": "http://127.0.0.1:62734/health",
                    "command": ["llama-server", "--port", "62734"],
                    "log_path": str(settings.paths.data_dir / "local_runtime.log"),
                    "started_at": 1.0,
                }
            ),
            encoding="utf-8",
        )

        result = run_startup_reflection(settings=settings, db=db)

        assert result.report["local_runtime"]["health"]["endpoint"] == "http://127.0.0.1:62734/health"


def test_run_startup_reflection_adopts_running_local_lane_on_probe_denial(monkeypatch):
    with TemporaryDirectory() as tmp:
        settings = _settings(Path(tmp))
        db = AstrataDatabase(settings.paths.data_dir / "astrata.db")
        db.initialize()
        generate_python_preflight_report(settings=settings, python_executable="python3")

        class _Health:
            ok = False
            status = "unreachable"
            endpoint = "http://127.0.0.1:8080/health"
            detail = "<urlopen error [Errno 1] Operation not permitted>"
            metadata = {"backend_id": "llama_cpp"}

            def model_dump(self, mode="json"):
                return {
                    "ok": self.ok,
                    "status": self.status,
                    "endpoint": self.endpoint,
                    "detail": self.detail,
                    "metadata": self.metadata,
                }

            def model_copy(self, update):
                clone = _Health()
                for key, value in update.items():
                    setattr(clone, key, value)
                return clone

        monkeypatch.setattr(
            "astrata.startup.diagnostics.LocalRuntimeManager.health",
            lambda self, config=None, runtime_key=None: _Health(),
        )

        (settings.paths.data_dir / "local_runtime.json").write_text(
            json.dumps(
                    {
                    "pid": os.getpid(),
                    "endpoint": "http://127.0.0.1:8080/health",
                    "command": ["llama-server", "--port", "8080"],
                    "log_path": str(settings.paths.data_dir / "local_runtime.log"),
                    "started_at": 1.0,
                }
            ),
            encoding="utf-8",
        )

        result = run_startup_reflection(settings=settings, db=db)

        assert result.report["local_runtime"]["adopted_existing_endpoint"] is True
        assert result.report["local_runtime"]["health"]["ok"] is True
        assert not any(
            issue["kind"] == "local_runtime_unhealthy" for issue in result.report["issues"]
        )
