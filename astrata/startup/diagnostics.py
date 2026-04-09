"""Startup diagnostics for preflight and post-boot self-reflection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse

from astrata.comms.lanes import OperatorMessageLane
from astrata.config.settings import Settings, load_settings
from astrata.local.backends.llama_cpp import LlamaCppBackend, LlamaCppLaunchConfig
from astrata.local.hardware import probe_thermal_state
from astrata.local.runtime.client import LocalRuntimeClient
from astrata.local.runtime.manager import LocalRuntimeManager
from astrata.local.runtime.processes import ManagedProcessController
from astrata.local.strata_endpoint import StrataEndpointService
from astrata.local.thermal import ThermalController
from astrata.providers.registry import build_default_registry
from astrata.records.models import ArtifactRecord, TaskRecord
from astrata.routing.policy import RouteChooser
from astrata.storage.db import AstrataDatabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def preflight_report_path(settings: Settings) -> Path:
    return settings.paths.data_dir / "startup-preflight.json"


def runtime_report_path(settings: Settings) -> Path:
    return settings.paths.data_dir / "startup-runtime-report.json"


def reflection_state_path(settings: Settings) -> Path:
    return settings.paths.data_dir / "startup-reflection-state.json"


def load_preflight_report(settings: Settings) -> dict[str, Any] | None:
    return _read_json(preflight_report_path(settings))


def load_runtime_report(settings: Settings) -> dict[str, Any] | None:
    return _read_json(runtime_report_path(settings))


def generate_python_preflight_report(
    settings: Settings | None = None,
    *,
    python_executable: str | None = None,
) -> dict[str, Any]:
    settings = settings or load_settings()
    manifest_path = settings.paths.data_dir / "install_manifest.json"
    runtime_python = settings.paths.data_dir / "runtime-venv" / "bin" / "python"
    chosen_python = Path(python_executable).expanduser() if python_executable else runtime_python
    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str, *, category: str = "preflight") -> None:
        checks.append(
            {
                "name": name,
                "category": category,
                "ok": ok,
                "status": "pass" if ok else "fail",
                "detail": detail,
            }
        )

    add_check(
        "runtime_data_dir",
        settings.paths.data_dir.exists(),
        f"Runtime data dir: {settings.paths.data_dir}",
    )
    manifest = _read_json(manifest_path)
    add_check(
        "install_manifest",
        manifest is not None,
        f"Install manifest: {manifest_path}",
    )
    add_check(
        "managed_runtime_python",
        runtime_python.exists(),
        f"Managed runtime python: {runtime_python}",
    )
    chosen_exists = chosen_python.exists() if chosen_python.is_absolute() else True
    add_check(
        "selected_python_exists",
        chosen_exists,
        f"Selected python: {chosen_python}",
    )
    importable = False
    if chosen_exists:
        try:
            result = subprocess.run(
                [str(chosen_python), "-c", "import fastapi, uvicorn, astrata"],
                capture_output=True,
                text=True,
                timeout=20,
                cwd=settings.paths.project_root,
            )
            importable = result.returncode == 0
            detail = "fastapi, uvicorn, astrata import cleanly"
            if result.returncode != 0:
                stderr = (result.stderr or result.stdout or "").strip()
                detail = stderr or "required modules did not import cleanly"
        except Exception as exc:
            detail = f"Import verification failed: {exc}"
    else:
        detail = "selected python does not exist"
    add_check("selected_python_imports", importable, detail)

    issues = [
        {
            "severity": "critical" if check["name"] in {"selected_python_exists", "selected_python_imports"} else "high",
            "kind": check["name"],
            "detail": check["detail"],
        }
        for check in checks
        if not check["ok"]
    ]
    payload = {
        "phase": "pre_inference",
        "generated_at": _now_iso(),
        "ok": not issues,
        "project_root": str(settings.paths.project_root),
        "runtime_data_dir": str(settings.paths.data_dir),
        "selected_python": str(chosen_python),
        "manifest_path": str(manifest_path),
        "checks": checks,
        "issues": issues,
    }
    _write_json(preflight_report_path(settings), payload)
    return payload


@dataclass(frozen=True)
class StartupReflectionResult:
    report: dict[str, Any]
    task_created: bool
    message_sent: bool


def run_startup_reflection(
    settings: Settings | None = None,
    *,
    db: AstrataDatabase | None = None,
) -> StartupReflectionResult:
    settings = settings or load_settings()
    db = db or AstrataDatabase(settings.paths.data_dir / "astrata.db")
    db.initialize()
    report = _build_runtime_report(settings, db)
    _write_json(runtime_report_path(settings), report)
    artifact = ArtifactRecord(
        artifact_id="startup-runtime-report",
        artifact_type="startup_runtime_report",
        title="Startup Runtime Report",
        description="Automatic startup reflection after the Astrata backend came online.",
        content_summary=report["summary"],
        status="good" if report["ok"] else "degraded",
        lifecycle_state="active",
        install_state="present",
        provenance={"source": "startup_reflection", "issues": report["issues"]},
    )
    db.upsert_artifact(artifact)

    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "ok": report["ok"],
                "issues": report["issues"],
                "default_route": report["providers"]["default_route"],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    prior_state = _read_json(reflection_state_path(settings)) or {}
    already_announced = prior_state.get("last_fingerprint") == fingerprint
    task_created = False
    message_sent = False
    lane = OperatorMessageLane(db=db)

    if report["issues"] and not already_announced:
        task = TaskRecord(
            task_id="startup-self-diagnosis",
            title="Review startup diagnostics",
            description=report["summary"],
            priority=9,
            urgency=7,
            provenance={"source": "startup_diagnostic", "report_path": str(runtime_report_path(settings))},
            permissions={},
            risk="low",
            status="pending",
            success_criteria={"startup_issues_reviewed": True},
            completion_policy={"type": "respond_or_execute", "prefer_cheap_lanes": True},
        )
        db.upsert_task(task)
        task_created = True
        lane.send(
            sender="startup",
            recipient="astrata",
            kind="notice",
            intent="startup_diagnostic",
            priority=9,
            urgency=7,
            payload={
                "message": report["summary"],
                "issues": report["issues"],
                "report_path": str(runtime_report_path(settings)),
            },
            related_task_ids=[task.task_id],
        )
        message_sent = True
    elif not report["issues"]:
        for payload in db.list_records("tasks"):
            if payload.get("task_id") != "startup-self-diagnosis":
                continue
            existing = TaskRecord(**payload)
            if existing.status in {"complete", "satisfied", "superseded"}:
                break
            db.upsert_task(existing.model_copy(update={"status": "satisfied", "updated_at": _now_iso()}))
            break

    _write_json(
        reflection_state_path(settings),
        {
            "generated_at": _now_iso(),
            "last_fingerprint": fingerprint,
            "last_ok": report["ok"],
            "last_issue_count": len(report["issues"]),
        },
    )
    return StartupReflectionResult(report=report, task_created=task_created, message_sent=message_sent)


def _build_runtime_report(settings: Settings, db: AstrataDatabase) -> dict[str, Any]:
    registry = build_default_registry()
    chooser = RouteChooser(registry)
    available_providers = registry.list_available_providers()
    inference_sources = registry.list_available_inference_sources()
    default_route = None
    try:
        default_route = chooser.choose(priority=0, urgency=0, risk="moderate").__dict__
    except Exception:
        default_route = None

    process_controller = ManagedProcessController(
        state_path=settings.paths.data_dir / "local_runtime.json",
        log_path=settings.paths.data_dir / "local_runtime.log",
    )
    manager = LocalRuntimeManager(
        backends={"llama_cpp": LlamaCppBackend()},
        process_controller=process_controller,
    )
    manager.discover_models(search_paths=settings.local_runtime.model_search_paths)
    managed_status = manager.managed_status()
    if settings.local_runtime.llama_cpp_base_url:
        manager.select_runtime(
            backend_id="llama_cpp",
            mode="external",
            endpoint=settings.local_runtime.llama_cpp_base_url,
        )
        local_health = manager.health(
            config={
                "host": settings.local_runtime.llama_cpp_host,
                "port": settings.local_runtime.llama_cpp_port,
            }
        )
    else:
        managed_endpoint = managed_status.endpoint if managed_status and managed_status.endpoint else None
        manager.select_runtime(
            backend_id="llama_cpp",
            mode="managed",
            endpoint=managed_endpoint or f"http://{settings.local_runtime.llama_cpp_host}:{settings.local_runtime.llama_cpp_port}/health",
        )
        local_health = manager.health(
            config=_llama_config_from_endpoint(
                endpoint=managed_endpoint,
                binary_path=settings.local_runtime.llama_cpp_binary,
                default_host=settings.local_runtime.llama_cpp_host,
                default_port=settings.local_runtime.llama_cpp_port,
            )
        )
    thermal_state = probe_thermal_state(preference=settings.local_runtime.thermal_preference)
    thermal_controller = ThermalController(state_path=settings.paths.data_dir / "thermal_state.json")
    thermal_decision = thermal_controller.evaluate(thermal_state)
    runtime_client = LocalRuntimeClient()
    native_strata = StrataEndpointService(
        state_path=settings.paths.data_dir / "strata_threads.json",
        runtime_manager=manager,
        runtime_client=runtime_client,
    )
    external_strata_health = None
    if settings.local_runtime.strata_endpoint_base_url:
        external_strata_health = runtime_client.health(
            base_url=settings.local_runtime.strata_endpoint_base_url
        )
    preflight = load_preflight_report(settings)

    issues: list[dict[str, Any]] = []

    def add_issue(severity: str, kind: str, detail: str) -> None:
        issues.append({"severity": severity, "kind": kind, "detail": detail})

    if not preflight:
        add_issue("high", "missing_preflight", "No startup preflight artifact was found.")
    elif not preflight.get("ok", False):
        add_issue("critical", "preflight_failed", "Startup preflight reported blocking issues.")
    if not available_providers:
        add_issue("high", "no_provider_routes", "No inference providers are currently available.")
    if default_route is None:
        add_issue("high", "no_default_route", "No default inference route could be selected.")
    if local_health is not None and not local_health.ok:
        add_issue("medium", "local_runtime_unhealthy", local_health.detail or local_health.status)
    if not thermal_decision.should_start_new_local_work:
        add_issue("low", "thermal_throttle", thermal_decision.reason)

    task_counts: dict[str, int] = {}
    for payload in db.list_records("tasks"):
        status = str(payload.get("status") or "unknown")
        task_counts[status] = task_counts.get(status, 0) + 1

    summary_parts = []
    if issues:
        summary_parts.append(
            f"Startup reflection found {len(issues)} issue(s): "
            + "; ".join(issue["kind"] for issue in issues[:4])
        )
    else:
        summary_parts.append("Startup reflection looks healthy enough to continue normal operation.")
    if default_route:
        summary_parts.append(
            "Default route is "
            + default_route["provider"]
            + (f":{default_route['cli_tool']}" if default_route.get("cli_tool") else "")
            + "."
        )
    report = {
        "phase": "post_boot",
        "generated_at": _now_iso(),
        "ok": not any(issue["severity"] in {"critical", "high"} for issue in issues),
        "summary": " ".join(summary_parts),
        "issues": issues,
        "preflight": preflight,
        "providers": {
            "available": available_providers,
            "inference_sources": inference_sources,
            "default_route": default_route,
        },
        "local_runtime": {
            "thermal": {
                "pressure": thermal_state.thermal_pressure,
                "preference": thermal_state.preference,
                "decision": thermal_decision.action,
                "reason": thermal_decision.reason,
            },
            "health": None if local_health is None else local_health.model_dump(mode="json"),
            "managed_process": None if manager.managed_status() is None else {
                "running": manager.managed_status().running,
                "pid": manager.managed_status().pid,
                "endpoint": manager.managed_status().endpoint,
                "detail": manager.managed_status().detail,
            },
            "native_strata": native_strata.status(),
            "external_strata_health": external_strata_health,
        },
        "queue": {
            "task_counts": task_counts,
        },
    }
    return report


def _llama_config_from_endpoint(
    *,
    endpoint: str | None,
    binary_path: str,
    default_host: str = "127.0.0.1",
    default_port: int = 8080,
) -> LlamaCppLaunchConfig:
    parsed = urlparse(str(endpoint or ""))
    return LlamaCppLaunchConfig(
        binary_path=binary_path,
        host=parsed.hostname or default_host,
        port=parsed.port or default_port,
    )
