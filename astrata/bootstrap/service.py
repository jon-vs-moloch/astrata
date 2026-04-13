"""Shared runtime dependency bootstrap helpers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable


class DependencyBootstrapService:
    """Ensures runtime dependencies exist before subsystems use them."""

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        python_executable: str | None = None,
        auto_install: bool = True,
    ) -> None:
        self._state_path = state_path
        if self._state_path is not None:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._python_executable = python_executable or sys.executable
        self._auto_install = auto_install

    def status(self) -> dict[str, Any]:
        payload = self._load()
        return {
            "python_executable": self._python_executable,
            "auto_install": self._auto_install,
            "resources": dict(payload.get("resources") or {}),
        }

    def is_python_package_available(self, module_name: str) -> bool:
        return bool(importlib.util.find_spec(module_name))

    def ensure_python_package(
        self,
        *,
        module_name: str,
        requirement: str | None = None,
    ) -> bool:
        requirement_text = requirement or module_name
        if self.is_python_package_available(module_name):
            self._record_success(
                key=f"python-package:{module_name}",
                payload={
                    "kind": "python-package",
                    "module_name": module_name,
                    "requirement": requirement_text,
                    "status": "available",
                },
            )
            return False
        return self.ensure_dependency(
            key=f"python-package:{module_name}",
            install=lambda: self._run_subprocess(["-m", "pip", "install", requirement_text]),
            metadata={
                "kind": "python-package",
                "module_name": module_name,
                "requirement": requirement_text,
            },
        )

    def ensure_playwright_browser(self, browser_name: str = "chromium") -> bool:
        return self.ensure_dependency(
            key=f"playwright-browser:{browser_name}",
            install=lambda: self._run_subprocess(["-m", "playwright", "install", browser_name]),
            metadata={
                "kind": "playwright-browser",
                "browser": browser_name,
            },
        )

    def ensure_huggingface_snapshot(
        self,
        *,
        repo_id: str,
        destination_dir: Path,
        allow_patterns: list[str] | None = None,
    ) -> bool:
        self.ensure_python_package(module_name="huggingface_hub", requirement="huggingface-hub>=0.31")

        def _install() -> None:
            from huggingface_hub import snapshot_download

            destination_dir.mkdir(parents=True, exist_ok=True)
            snapshot_download(
                repo_id=repo_id,
                local_dir=str(destination_dir),
                local_dir_use_symlinks=False,
                allow_patterns=allow_patterns,
            )

        return self.ensure_dependency(
            key=f"huggingface-snapshot:{repo_id}",
            install=_install,
            metadata={
                "kind": "huggingface-snapshot",
                "repo_id": repo_id,
                "destination_dir": str(destination_dir),
                "allow_patterns": list(allow_patterns or []),
            },
        )

    def ensure_dependency(
        self,
        *,
        key: str,
        install: Callable[[], Any],
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self._auto_install:
            raise RuntimeError(f"Automatic dependency installation is disabled for {key}.")
        payload = self._load()
        resources = payload.setdefault("resources", {})
        record = dict(resources.get(key) or {})
        attempts = int(record.get("attempts", 0)) + 1
        record.update(metadata or {})
        record["attempts"] = attempts
        try:
            install()
        except Exception as exc:
            record["status"] = "failed"
            record["last_error"] = str(exc)
            resources[key] = record
            self._save(payload)
            raise
        record["status"] = "installed"
        record["last_error"] = ""
        resources[key] = record
        self._save(payload)
        return True

    def _record_success(self, *, key: str, payload: dict[str, Any]) -> None:
        state = self._load()
        resources = state.setdefault("resources", {})
        record = dict(resources.get(key) or {})
        attempts = int(record.get("attempts", 0))
        record.update(payload)
        record["attempts"] = attempts
        record["last_error"] = ""
        resources[key] = record
        self._save(state)

    def _run_subprocess(self, args: list[str]) -> None:
        cmd = [self._python_executable, *args]
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
            return
        except subprocess.CalledProcessError as exc:
            if args[:2] == ["-m", "pip"] and "No module named pip" in ((exc.stderr or "") + (exc.stdout or "")):
                subprocess.run(
                    [self._python_executable, "-m", "ensurepip", "--upgrade"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return
            raise

    def _load(self) -> dict[str, Any]:
        if self._state_path is None or not self._state_path.exists():
            return {"resources": {}}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"resources": {}}

    def _save(self, payload: dict[str, Any]) -> None:
        if self._state_path is None:
            return
        self._state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
