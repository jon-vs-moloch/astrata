"""LM Studio CLI bridge for bounded local-model execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import time


@dataclass(frozen=True)
class LmStudioGeneration:
    model_key: str
    prompt: str
    content: str
    duration_seconds: float


class LmStudioCli:
    def __init__(self, *, executable: str = "/Users/jon/.lmstudio/bin/lms", timeout_seconds: int = 300) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        return bool(shutil.which(self.executable) or self.executable)

    def generate(
        self,
        *,
        model_key: str,
        prompt: str,
        system_prompt: str | None = None,
        ttl_seconds: int = 300,
    ) -> LmStudioGeneration:
        self._ensure_model_ready(model_key)
        args = [self.executable, "chat", model_key, "-p", prompt, "-y", "--ttl", str(ttl_seconds)]
        if system_prompt:
            args.extend(["-s", system_prompt])
        started = time.monotonic()
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        duration = max(0.0, time.monotonic() - started)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
            raise RuntimeError(f"LM Studio generation failed for {model_key}: {detail}")
        return LmStudioGeneration(
            model_key=model_key,
            prompt=prompt,
            content=(proc.stdout or "").strip(),
            duration_seconds=duration,
        )

    def _ensure_model_ready(self, model_key: str) -> None:
        path = Path(model_key).expanduser()
        if not path.exists():
            return
        load_proc = subprocess.run(
            [self.executable, "load", str(path)],
            capture_output=True,
            text=True,
            timeout=min(self.timeout_seconds, 120),
            check=False,
        )
        if load_proc.returncode != 0:
            detail = (load_proc.stderr or load_proc.stdout or f"exit code {load_proc.returncode}").strip()
            raise RuntimeError(f"LM Studio failed to load {model_key}: {detail}")
