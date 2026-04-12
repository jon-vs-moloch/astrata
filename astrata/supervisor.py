"""Lightweight service supervision for Astrata's always-on lane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
from typing import Any

from astrata.config.settings import Settings
from astrata.ui.service import AstrataUIService


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _http_ok(url: str, *, timeout_seconds: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            return int(getattr(response, "status", 200)) < 500
    except Exception:
        return False


@dataclass(frozen=True)
class SupervisedService:
    service_id: str
    command: tuple[str, ...]
    log_path: Path
    match_tokens: tuple[str, ...]
    health_url: str | None = None


class AstrataSupervisor:
    """Owns the small set of processes that make Astrata feel continuously alive."""

    def __init__(
        self,
        *,
        settings: Settings,
        ui_host: str = "127.0.0.1",
        ui_port: int = 8891,
        loop0_steps: int = 1,
        loop0_interval_seconds: int = 120,
        relay_profile_id: str | None = None,
        relay_link_id: str | None = None,
        relay_interval_seconds: float = 30.0,
    ) -> None:
        self.settings = settings
        self.state_path = settings.paths.data_dir / "supervisor.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        python = sys.executable
        self.services: list[SupervisedService] = [
            SupervisedService(
                service_id="ui_backend",
                command=(
                    python,
                    "-m",
                    "astrata.ui.server",
                    "--host",
                    ui_host,
                    "--port",
                    str(ui_port),
                    "--no-open",
                ),
                log_path=settings.paths.data_dir / "ui-backend-supervisor.log",
                match_tokens=("astrata.ui.server", "--port", str(ui_port)),
                health_url=f"http://{ui_host}:{ui_port}/api/health",
            ),
            SupervisedService(
                service_id="loop0_daemon",
                command=(
                    python,
                    "-m",
                    "astrata.main",
                    "loop0-daemon",
                    "--steps",
                    str(loop0_steps),
                    "--interval",
                    str(loop0_interval_seconds),
                ),
                log_path=settings.paths.data_dir / "loop0-daemon.log",
                match_tokens=("astrata.main", "loop0-daemon"),
            ),
        ]
        if relay_profile_id:
            relay_command = [
                python,
                "-m",
                "astrata.main",
                "mcp-relay-watch",
                "--profile-id",
                relay_profile_id,
                "--interval-seconds",
                str(relay_interval_seconds),
            ]
            if relay_link_id:
                relay_command.extend(["--link-id", relay_link_id])
            self.services.append(
                SupervisedService(
                    service_id="mcp_relay_watch",
                    command=tuple(relay_command),
                    log_path=settings.paths.data_dir / "mcp-relay-watch.log",
                    match_tokens=("astrata.main", "mcp-relay-watch", relay_profile_id),
                )
            )

    def status(self) -> dict[str, Any]:
        state = self._load_state()
        services = {service.service_id: self._service_status(service, state) for service in self.services}
        return {
            "status": "ok",
            "checked_at": _now_iso(),
            "state_path": str(self.state_path),
            "services": services,
            "local_runtime": self._local_runtime_status(),
        }

    def reconcile(self) -> dict[str, Any]:
        state = self._load_state()
        services: dict[str, Any] = {}
        for service in self.services:
            before = self._service_status(service, state)
            action = "none"
            if before["owned"]:
                action = "already_running"
            elif before["adopted"]:
                self._record_service(
                    state,
                    service,
                    pid=before["pid"],
                    adopted=True,
                    command=before["command"],
                )
                action = "adopted"
            elif service.health_url and _http_ok(service.health_url):
                self._record_service(
                    state,
                    service,
                    pid=None,
                    adopted=True,
                    command="",
                    detail="healthy_endpoint_without_known_pid",
                )
                action = "adopted_endpoint"
            else:
                status = self._start_service(service)
                self._record_service(
                    state,
                    service,
                    pid=status["pid"],
                    adopted=False,
                    command=" ".join(service.command),
                )
                action = "started"
            services[service.service_id] = {
                **self._service_status(service, state),
                "action": action,
            }
        local_runtime = self._ensure_local_runtime()
        state["updated_at"] = _now_iso()
        self._save_state(state)
        return {
            "status": "ok",
            "reconciled_at": state["updated_at"],
            "state_path": str(self.state_path),
            "services": services,
            "local_runtime": local_runtime,
        }

    def stop(self, *, include_adopted: bool = False, stop_local_runtime: bool = False) -> dict[str, Any]:
        state = self._load_state()
        services: dict[str, Any] = {}
        for service in self.services:
            service_state = dict((state.get("services") or {}).get(service.service_id) or {})
            pid = int(service_state.get("pid") or 0)
            adopted = bool(service_state.get("adopted"))
            action = "not_running"
            if pid and _pid_alive(pid):
                if adopted and not include_adopted:
                    action = "left_adopted_running"
                else:
                    self._terminate_pid(pid)
                    action = "stopped"
            service_state["last_action"] = action
            service_state["stopped_at"] = _now_iso() if action == "stopped" else service_state.get("stopped_at")
            state.setdefault("services", {})[service.service_id] = service_state
            services[service.service_id] = {
                **self._service_status(service, state),
                "action": action,
            }
        local_runtime = {"status": "not_requested"}
        if stop_local_runtime:
            try:
                local_runtime = AstrataUIService(settings=self.settings).stop_local_runtime()
            except Exception as exc:
                local_runtime = {"status": "failed", "error": str(exc)}
        state["updated_at"] = _now_iso()
        self._save_state(state)
        return {
            "status": "ok",
            "stopped_at": state["updated_at"],
            "services": services,
            "local_runtime": local_runtime,
        }

    def _start_service(self, service: SupervisedService) -> dict[str, Any]:
        service.log_path.parent.mkdir(parents=True, exist_ok=True)
        with service.log_path.open("ab") as log_handle:
            process = subprocess.Popen(
                list(service.command),
                cwd=str(self.settings.paths.project_root),
                env=os.environ.copy(),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        if service.health_url:
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if _http_ok(service.health_url, timeout_seconds=0.5):
                    break
                time.sleep(0.25)
        return {"pid": process.pid}

    def _service_status(self, service: SupervisedService, state: dict[str, Any]) -> dict[str, Any]:
        from astrata.local.runtime.processes import find_matching_process

        service_state = dict((state.get("services") or {}).get(service.service_id) or {})
        pid = int(service_state.get("pid") or 0) or None
        running = _pid_alive(pid)
        adopted_pid, adopted_command = (None, None) if running else find_matching_process(service.match_tokens)
        health_ok = _http_ok(service.health_url) if service.health_url else None
        if not running and adopted_pid is not None:
            pid = adopted_pid
        return {
            "service_id": service.service_id,
            "running": running or adopted_pid is not None or bool(health_ok),
            "owned": running and not bool(service_state.get("adopted")),
            "adopted": bool(service_state.get("adopted")) or adopted_pid is not None or (bool(health_ok) and not running),
            "pid": pid,
            "health_ok": health_ok,
            "command": service_state.get("command") or adopted_command or " ".join(service.command),
            "log_path": str(service.log_path),
            "detail": service_state.get("detail"),
            "started_at": service_state.get("started_at"),
            "updated_at": service_state.get("updated_at"),
        }

    def _record_service(
        self,
        state: dict[str, Any],
        service: SupervisedService,
        *,
        pid: int | None,
        adopted: bool,
        command: str,
        detail: str | None = None,
    ) -> None:
        state.setdefault("services", {})[service.service_id] = {
            "pid": pid,
            "adopted": adopted,
            "command": command or " ".join(service.command),
            "log_path": str(service.log_path),
            "detail": detail,
            "started_at": _now_iso(),
            "updated_at": _now_iso(),
        }

    def _local_runtime_status(self) -> dict[str, Any]:
        try:
            service = AstrataUIService(settings=self.settings)
            snapshot = service._local_runtime_snapshot()  # noqa: SLF001
            endpoint = (
                f"http://{self.settings.local_runtime.llama_cpp_host}:"
                f"{self.settings.local_runtime.llama_cpp_port}/health"
            )
            endpoint_ok = _http_ok(endpoint)
            snapshot["direct_endpoint"] = {
                "endpoint": endpoint,
                "ok": endpoint_ok,
                "adoptable": endpoint_ok and not bool((snapshot.get("managed_process") or {}).get("running")),
            }
            return snapshot
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    def _ensure_local_runtime(self) -> dict[str, Any]:
        try:
            return AstrataUIService(settings=self.settings).ensure_local_runtime()
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"services": {}}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {"services": {}}
        except Exception:
            return {"services": {}}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    def _terminate_pid(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not _pid_alive(pid):
                return
            time.sleep(0.1)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
