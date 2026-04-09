"""Managed llama.cpp runtime control for Astrata's local substrate."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from subprocess import PIPE, Popen
import time
from typing import Callable
from urllib.error import URLError
from urllib.request import urlopen


HealthChecker = Callable[[str], bool]


@dataclass(frozen=True)
class ManagedLlamaCppServerOptions:
    model_path: str
    binary_path: str = "llama-server"
    host: str = "127.0.0.1"
    port: int = 8080
    command_args_prefix: list[str] = field(default_factory=list)
    extra_args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    startup_timeout_ms: int = 60_000
    health_checker: HealthChecker | None = None


class ManagedLlamaCppServer:
    def __init__(self, options: ManagedLlamaCppServerOptions) -> None:
        self.options = options
        self.base_url = f"http://{options.host}:{options.port}"
        self._process: Popen[str] | None = None
        self._stopped = False
        self._healthy = False
        self._last_error: str | None = None
        self._last_exit_code: int | None = None
        self._recent_logs: list[str] = []
        self._health_checker = options.health_checker or _default_health_checker

    def build_command_args(self) -> list[str]:
        return [
            *self.options.command_args_prefix,
            "-m",
            self.options.model_path,
            "--host",
            self.options.host,
            "--port",
            str(self.options.port),
            *self.options.extra_args,
        ]

    def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("Managed llama.cpp server is already running.")
        self._stopped = False
        self._healthy = False
        self._last_error = None
        process = Popen(
            [self.options.binary_path, *self.build_command_args()],
            stdout=PIPE,
            stderr=PIPE,
            stdin=PIPE,
            text=True,
            env=self.options.env,
        )
        self._process = process
        self._wait_for_healthy()
        self._healthy = True

    def stop(self) -> None:
        if self._process is None:
            return
        self._stopped = True
        self._healthy = False
        self._process.terminate()
        try:
            self._process.wait(timeout=2)
        except Exception:
            self._process.kill()
            self._process.wait(timeout=2)
        self._last_exit_code = self._process.returncode
        self._process = None

    def status(self) -> dict[str, object]:
        healthy = self._process is not None and self._health_checker(f"{self.base_url}/health")
        self._healthy = healthy
        return {
            "base_url": self.base_url,
            "running": self._process is not None,
            "healthy": healthy,
            "pid": None if self._process is None else self._process.pid,
            "model_path": self.options.model_path,
            "startup_timeout_ms": self.options.startup_timeout_ms,
            "last_error": self._last_error,
            "last_exit_code": self._last_exit_code,
            "recent_logs": list(self._recent_logs),
        }

    def _wait_for_healthy(self) -> None:
        deadline = time.time() + (self.options.startup_timeout_ms / 1000)
        while time.time() < deadline:
            if self._process is None:
                break
            if self._process.poll() is not None:
                self._last_exit_code = self._process.returncode
                self._last_error = (
                    f"Managed llama.cpp server exited before becoming ready (code={self._process.returncode})."
                )
                self._process = None
                raise RuntimeError(self._last_error)
            if self._health_checker(f"{self.base_url}/health"):
                return
            self._capture_logs()
            time.sleep(0.25)
        self._capture_logs()
        self._last_error = f"Timed out waiting for managed llama.cpp server health at {self.base_url}/health"
        raise RuntimeError(self._last_error)

    def _capture_logs(self) -> None:
        if self._process is None:
            return
        for stream_name, stream in (("stdout", self._process.stdout), ("stderr", self._process.stderr)):
            if stream is None:
                continue
            try:
                line = stream.readline().strip()
            except Exception:
                line = ""
            if line:
                self._recent_logs.append(f"[{stream_name}] {line}")
        if len(self._recent_logs) > 50:
            self._recent_logs = self._recent_logs[-50:]


def _default_health_checker(url: str) -> bool:
    try:
        with urlopen(url, timeout=1.0) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False
