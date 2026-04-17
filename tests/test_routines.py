from pathlib import Path

from astrata.routines import RoutineService


def test_routine_service_registers_default_registry_refreshes(tmp_path: Path):
    service = RoutineService(state_path=tmp_path / "routines.json")

    routines = service.ensure_default_routines()

    ids = {routine.routine_id for routine in routines}
    assert "refresh-kilocode-model-registry" in ids
    assert "sync-google-ai-studio-model-registry" in ids
    assert all(routine.procedure_id == "refresh-inference-registries" for routine in routines)


def test_routine_service_runs_registered_command(tmp_path: Path, monkeypatch):
    service = RoutineService(state_path=tmp_path / "routines.json")
    service.ensure_default_routines()

    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr("astrata.routines.service.subprocess.run", lambda *args, **kwargs: _Proc())

    result = service.run("refresh-kilocode-model-registry")

    assert result["status"] == "succeeded"
    assert result["routine"]["last_run_at"] is not None

