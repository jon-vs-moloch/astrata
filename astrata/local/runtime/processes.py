"""Managed local backend process helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from astrata.local.backends.base import BackendLaunchSpec


def find_process_command_map() -> dict[int, str]:
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return {}
    processes: dict[int, str] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        processes[pid] = command.strip()
    return processes


def find_matching_process(tokens: tuple[str, ...]) -> tuple[int | None, str | None]:
    for pid, command in find_process_command_map().items():
        if all(token in command for token in tokens):
            return pid, command
    return None, None


@dataclass(frozen=True)
class ManagedProcessStatus:
    running: bool
    pid: int | None
    endpoint: str | None
    command: list[str]
    log_path: str | None
    started_at: float | None
    detail: str | None = None


class ManagedProcessController:
    def __init__(self, *, state_path: Path, log_path: Path) -> None:
        self.state_path = state_path
        self.log_path = log_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def start(self, launch_spec: BackendLaunchSpec) -> ManagedProcessStatus:
        current = self.status()
        if current.running:
            return current
        with self.log_path.open("ab") as log_handle:
            process = subprocess.Popen(
                launch_spec.command,
                cwd=launch_spec.cwd or None,
                env={**os.environ, **dict(launch_spec.env or {})},
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        state = {
            "pid": process.pid,
            "endpoint": launch_spec.endpoint,
            "command": list(launch_spec.command),
            "log_path": str(self.log_path),
            "started_at": time.time(),
        }
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return self.status()

    def stop(self) -> ManagedProcessStatus:
        state = self._load_state()
        pid = int(state.get("pid") or 0)
        if pid > 0 and self._pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.state_path.unlink(missing_ok=True)
        return ManagedProcessStatus(
            running=False,
            pid=pid or None,
            endpoint=state.get("endpoint"),
            command=list(state.get("command") or []),
            log_path=state.get("log_path"),
            started_at=state.get("started_at"),
            detail="stopped",
        )

    def status(self) -> ManagedProcessStatus:
        state = self._load_state()
        pid = int(state.get("pid") or 0)
        running = pid > 0 and self._pid_alive(pid)
        detail = None if running else ("not_running" if not state else "stale_pid")
        if state and not running:
            self.state_path.unlink(missing_ok=True)
        return ManagedProcessStatus(
            running=running,
            pid=pid or None,
            endpoint=state.get("endpoint"),
            command=list(state.get("command") or []),
            log_path=state.get("log_path"),
            started_at=state.get("started_at"),
            detail=detail,
        )

    def _load_state(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
